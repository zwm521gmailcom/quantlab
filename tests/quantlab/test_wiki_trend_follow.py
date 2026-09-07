"""Tests for Wiki multi-indicator trend strategy signal generation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from quantlab.services.index_membership import latest_members, members_on
from quantlab.services.rule_indicators import enrich_indicators
from quantlab.strategies.registry import RULE_STRATEGIES
from quantlab.strategies.wiki_trend_follow import DEFAULTS, generate_signals


def _dates(n: int, end: str = "20240131") -> list[datetime]:
    end_dt = datetime.strptime(end, "%Y%m%d")
    return [end_dt - timedelta(days=n - 1 - i) for i in range(n)]


def _qualified_uptrend_prices(
    n: int,
    base: float = 10.0,
    daily_gain: float = 1.005,
    pullback: float = 0.99,
    every: int = 5,
) -> list[float]:
    """Uptrend with periodic pullbacks so RSI stays within the strategy band."""
    prices = [base]
    for i in range(1, n):
        if i % every == 0:
            prices.append(prices[-1] * pullback)
        else:
            prices.append(prices[-1] * daily_gain)
    return prices


def _downtrend_prices(n: int, base: float = 20.0, daily_return: float = 0.99) -> list[float]:
    prices = [base]
    for _ in range(1, n):
        prices.append(prices[-1] * daily_return)
    return prices


def _downtrend_then_bounce_prices(
    n: int,
    base: float = 10.0,
    down_return: float = 0.997,
    bounce_return: float = 1.01,
    bounce_days: int = 6,
) -> list[float]:
    """Long grind down, short bounce: EMA20 stays below EMA60 while MACD/RSI/Boll pass."""
    prices = [base]
    for i in range(1, n):
        remaining = n - i
        factor = bounce_return if remaining <= bounce_days else down_return
        prices.append(prices[-1] * factor)
    return prices


def _close_just_below_boll_mid(prices: list[float], frac: float = 0.9995) -> list[float]:
    """Nudge the last close under SMA20 via the prior-19 mean, without a crash print."""
    adjusted = list(prices)
    mean19 = sum(adjusted[-20:-1]) / 19
    adjusted[-1] = mean19 * frac
    return adjusted


def _build_frame(
    specs: dict[str, dict],
    n_days: int = 80,
    end_date: str = "20240131",
) -> pd.DataFrame:
    """Build a multi-instrument daily frame from per-stock overrides."""
    rows: list[dict] = []
    dates = _dates(n_days, end_date)
    list_date = (dates[0] - timedelta(days=400)).strftime("%Y%m%d")
    for instrument, spec in specs.items():
        prices = spec.get("prices")
        if prices is None:
            prices = _qualified_uptrend_prices(n_days, base=spec.get("base", 10.0))
        if len(prices) != n_days:
            raise ValueError(f"{instrument}: expected {n_days} prices, got {len(prices)}")
        amount = spec.get("amount", 80_000_000.0)
        st_status = spec.get("st_status", 0)
        is_suspended = spec.get("is_suspended", False)
        for dt, close in zip(dates, prices):
            rows.append(
                {
                    "date": dt,
                    "instrument": instrument,
                    "hfq_close": close,
                    "amount": amount,
                    "st_status": st_status,
                    "is_suspended": is_suspended,
                    "list_date": list_date,
                }
            )
    return pd.DataFrame(rows)


def _membership(*members: tuple[str, set[str]], trade_date: str = "20240101") -> pd.DataFrame:
    rows = []
    for index_code, codes in members:
        for code in codes:
            rows.append(
                {
                    "index_code": index_code,
                    "con_code": code,
                    "trade_date": trade_date,
                    "weight": 1.0,
                }
            )
    return pd.DataFrame(rows)


def _four_condition_flags(row: pd.Series) -> dict[str, bool]:
    return {
        "ema": bool(row["ema_20"] > row["ema_60"]),
        "macd": bool(row["macd_dif"] > row["macd_dea"] and row["macd_hist"] > 0),
        "rsi": bool(DEFAULTS["rsi_low"] <= row["rsi"] <= DEFAULTS["rsi_high"]),
        "boll": bool(row["hfq_close"] > row["boll_mid"]),
    }


def _last_row(frame: pd.DataFrame, instrument: str, trade_date: pd.Timestamp) -> pd.Series:
    enriched = enrich_indicators(frame)
    match = enriched.loc[(enriched["date"] == trade_date) & (enriched["instrument"] == instrument)]
    assert len(match) == 1, f"expected one last bar for {instrument}"
    return match.iloc[0]


def _hs300_up_fraction(frame: pd.DataFrame, members: set[str], trade_date: pd.Timestamp, window: int) -> float:
    enriched = enrich_indicators(frame)
    day = enriched.loc[(enriched["date"] == trade_date) & (enriched["instrument"].isin(members))]
    prior = day[f"hfq_close_{window}"]
    valid = day["hfq_close"].notna() & prior.notna()
    return float((day.loc[valid, "hfq_close"] > prior.loc[valid]).mean())


def test_bearish_breadth_still_emits_when_name_passes():
    """Market-wide 20-day breadth can be weak; a name that passes four conditions still emits."""
    n = 80
    end = "20240131"
    qualified = "600000.SH"
    hs300 = {qualified, "600001.SH", "600002.SH", "600003.SH"}
    csi500 = {"000001.SZ"}

    frame = _build_frame(
        {
            qualified: {"prices": _qualified_uptrend_prices(n)},
            "600001.SH": {"prices": _downtrend_prices(n, base=20.0, daily_return=0.99)},
            "600002.SH": {"prices": _downtrend_prices(n, base=15.0, daily_return=0.992)},
            "600003.SH": {"prices": _downtrend_prices(n, base=12.0, daily_return=0.988)},
            "000001.SZ": {"prices": _downtrend_prices(n, base=18.0, daily_return=0.99)},
        },
        n_days=n,
        end_date=end,
    )
    signal_day = pd.Timestamp(end)
    assert _hs300_up_fraction(frame, hs300, signal_day, 20) == pytest.approx(0.25)
    assert _four_condition_flags(_last_row(frame, qualified, signal_day)) == {
        "ema": True,
        "macd": True,
        "rsi": True,
        "boll": True,
    }

    params = {**DEFAULTS, "top_n": 10, "up_pct_20": 0.99, "up_pct_60": 0.99}
    signals = generate_signals(
        frame,
        _membership(("000300.SH", hs300), ("000905.SH", csi500)),
        params,
    )
    day = signals[signals["date"] == signal_day]
    assert qualified in set(day["instrument"])
    assert len(day) >= 1


def test_asof_universe_does_not_use_later_snapshot():
    n = 80
    end = "20240131"
    stale = "600099.SH"
    current = "600000.SH"
    csi = "000001.SZ"
    frame = _build_frame(
        {
            stale: {"prices": _qualified_uptrend_prices(n, base=9.0)},
            current: {"prices": _qualified_uptrend_prices(n, base=10.0)},
            csi: {"prices": _qualified_uptrend_prices(n, base=11.0)},
        },
        n_days=n,
        end_date=end,
    )
    membership = pd.concat(
        [
            _membership(("000300.SH", {stale}), trade_date="20200101"),
            _membership(("000905.SH", {csi}), trade_date="20200101"),
            _membership(("000300.SH", {current}), trade_date="20240131"),
            _membership(("000905.SH", {csi}), trade_date="20240131"),
        ],
        ignore_index=True,
    )
    assert members_on(membership, "000300.SH", "20231231") == {stale}
    assert latest_members(membership, "000300.SH") == {current}

    signals = generate_signals(frame, membership, {**DEFAULTS, "top_n": 10})
    last = set(signals.loc[signals["date"] == pd.Timestamp(end), "instrument"])
    assert stale not in last
    assert current in last
    assert csi in last
    earlier = signals.loc[signals["date"] < pd.Timestamp(end), "instrument"]
    assert stale in set(earlier)


@pytest.mark.parametrize(
    "fail_instrument,fail_kind,fail_prices_factory",
    [
        ("600010.SH", "ema", lambda n: _downtrend_then_bounce_prices(n)),
        (
            "600011.SH",
            "macd",
            lambda n: _qualified_uptrend_prices(n, daily_gain=1.006, pullback=0.985, every=6),
        ),
        (
            "600012.SH",
            "rsi",
            lambda n: _qualified_uptrend_prices(n, daily_gain=1.004, pullback=0.995, every=7),
        ),
        (
            "600013.SH",
            "boll",
            lambda n: _close_just_below_boll_mid(
                _qualified_uptrend_prices(n, daily_gain=1.003, pullback=0.98, every=8)
            ),
        ),
    ],
)
def test_requires_all_four_conditions(fail_instrument, fail_kind, fail_prices_factory):
    """Each case: three conditions pass, exactly one fails; failer is dropped, qualifier remains."""
    n = 80
    end = "20240131"
    good_a = "600000.SH"
    good_b = "600001.SH"
    good_prices = _qualified_uptrend_prices(n)
    fail_prices = fail_prices_factory(n)

    frame = _build_frame(
        {
            good_a: {"prices": good_prices},
            good_b: {"prices": good_prices},
            fail_instrument: {"prices": fail_prices},
        },
        n_days=n,
        end_date=end,
    )
    signal_day = pd.Timestamp(end)

    good_flags = _four_condition_flags(_last_row(frame, good_a, signal_day))
    assert all(good_flags.values())
    fail_flags = _four_condition_flags(_last_row(frame, fail_instrument, signal_day))
    assert fail_flags[fail_kind] is False
    assert all(value for key, value in fail_flags.items() if key != fail_kind)

    membership = _membership(
        ("000300.SH", {good_a, good_b, fail_instrument}),
    )
    params = {**DEFAULTS, "top_n": 10}
    signals = generate_signals(frame, membership, params)
    picked = set(signals.loc[signals["date"] == signal_day, "instrument"])

    assert fail_instrument not in picked
    assert good_a in picked
    assert len(picked) >= 1


def test_rank_picks_top_n_equal_ten_percent():
    """Filtered names ranked 40/35/25; keep the exact top 2 with weight 0.10 each."""
    n = 80
    end = "20240131"
    hs300 = {"600000.SH", "600001.SH", "600002.SH"}
    csi500 = {"000001.SZ", "000002.SZ", "000003.SZ"}
    specs = {
        "600000.SH": {"prices": _qualified_uptrend_prices(n, base=10.0, daily_gain=1.005)},
        "600001.SH": {"prices": _qualified_uptrend_prices(n, base=11.0, daily_gain=1.004)},
        "600002.SH": {"prices": _qualified_uptrend_prices(n, base=12.0, daily_gain=1.003)},
        "000001.SZ": {"prices": _qualified_uptrend_prices(n, base=13.0, daily_gain=1.005)},
        "000002.SZ": {"prices": _qualified_uptrend_prices(n, base=14.0, daily_gain=1.004)},
        "000003.SZ": {"prices": _qualified_uptrend_prices(n, base=15.0, daily_gain=1.003)},
    }

    frame = _build_frame(specs, n_days=n, end_date=end)
    membership = _membership(
        ("000300.SH", hs300),
        ("000905.SH", csi500),
    )
    params = {**DEFAULTS, "top_n": 2, "target_weight": 0.10}

    signal_day = pd.Timestamp(end)
    last = enrich_indicators(frame)
    last = last.loc[last["date"] == signal_day].copy()
    last = last.loc[last.apply(lambda row: all(_four_condition_flags(row).values()), axis=1)]
    last["expected_score"] = (
        last["macd_hist"].rank(pct=True) * 0.40
        + last["mom_20"].rank(pct=True) * 0.35
        + last["rsi"].rank(pct=True) * 0.25
    )
    expected = list(last.sort_values("expected_score", ascending=False).head(2)["instrument"])
    assert expected == ["000001.SZ", "600000.SH"]

    signals = generate_signals(frame, membership, params)
    day_rows = signals[signals["date"] == signal_day].reset_index(drop=True)

    assert list(day_rows["instrument"]) == expected
    assert (day_rows["weight"] == 0.10).all()
    assert day_rows["weight"].sum() == pytest.approx(0.20)
    assert day_rows["rank_score"].is_monotonic_decreasing


def test_registry_exports_wiki_trend_follow():
    entry = RULE_STRATEGIES["wiki_trend_follow"]
    assert entry["module"] == "quantlab.strategies.wiki_trend_follow"
    assert entry["title"]
    assert entry["defaults"] == DEFAULTS
    assert entry["source_hash"].startswith("sha256:")


def test_members_on_integration():
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000905.SH"],
            "con_code": ["AAA.SZ", "BBB.SH"],
            "trade_date": ["20240101", "20240101"],
            "weight": [1.0, 1.0],
        }
    )
    assert latest_members(weights, "000300.SH") == {"AAA.SZ"}
    assert latest_members(weights, "000905.SH") == {"BBB.SH"}


def test_generate_signals_accepts_ts_code_trade_date_columns():
    n = 80
    end = "20240131"
    frame = _build_frame(
        {
            "600000.SH": {"prices": _qualified_uptrend_prices(n)},
            "600001.SH": {"prices": _qualified_uptrend_prices(n, base=11.0)},
        },
        n_days=n,
        end_date=end,
    )
    renamed = frame.rename(columns={"instrument": "ts_code", "date": "trade_date"})
    membership = _membership(("000300.SH", {"600000.SH", "600001.SH"}))
    signals = generate_signals(renamed, membership, {**DEFAULTS, "top_n": 10})
    picked = set(signals.loc[signals["date"] == pd.Timestamp(end), "instrument"])
    assert picked == {"600000.SH", "600001.SH"}


def test_missing_suspend_column_rejects_the_name():
    from quantlab.strategies.wiki_trend_follow import _passes_stock_filters

    row = pd.Series(
        {
            "st_status": 0,
            "list_date": "20200101",
            "instrument": "000001.SZ",
            "amount": 80_000_000,
        }
    )
    assert _passes_stock_filters(row, pd.Timestamp("20240131"), DEFAULTS) is False


def test_missing_list_date_rejects_the_name():
    from quantlab.strategies.wiki_trend_follow import _passes_stock_filters

    row = pd.Series(
        {
            "st_status": 0,
            "is_suspended": 0,
            "instrument": "000001.SZ",
            "amount": 80_000_000,
        }
    )
    assert _passes_stock_filters(row, pd.Timestamp("20240131"), DEFAULTS) is False


def test_delist_date_on_trade_date_rejects_the_name():
    from quantlab.strategies.wiki_trend_follow import _passes_stock_filters

    row = pd.Series(
        {
            "st_status": 0,
            "is_suspended": 0,
            "list_date": "20200101",
            "delist_date": "20240131",
            "instrument": "000001.SZ",
            "amount": 80_000_000,
        }
    )
    assert _passes_stock_filters(row, pd.Timestamp("20240131"), DEFAULTS) is False
