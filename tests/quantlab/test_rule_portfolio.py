"""Target-weight portfolio: 10% names, cash leftover, SL/TP, max hold 45."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from quantlab.services.rule_portfolio import run_target_weight_portfolio


def _ymd(value) -> str:
    text = str(value).replace("-", "").replace(" ", "").replace("T", "")
    return text[:8]


def _trading_dates(n: int, start: str = "20240102") -> list[str]:
    """n consecutive calendar days treated as the trading calendar."""
    start_dt = datetime.strptime(start, "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _series(spec: dict, key: str, n: int, default: float) -> list[float]:
    value = spec.get(key, spec.get("price", default))
    if isinstance(value, (list, tuple)):
        if len(value) != n:
            raise ValueError(f"{key}: expected {n} values, got {len(value)}")
        return [float(item) for item in value]
    return [float(value)] * n


def _frame(dates: list[str], specs: dict[str, dict]) -> pd.DataFrame:
    rows: list[dict] = []
    n = len(dates)
    for instrument, spec in specs.items():
        opens = _series(spec, "open", n, 10.0)
        closes = _series(spec, "close", n, 10.0)
        highs = _series(spec, "high", n, 10.0)
        lows = _series(spec, "low", n, 10.0)
        for date, open_, close, high, low in zip(dates, opens, closes, highs, lows):
            rows.append(
                {
                    "date": date,
                    "instrument": instrument,
                    "open": open_,
                    "close": close,
                    "high": high,
                    "low": low,
                    "up_limit": 1000.0,
                    "down_limit": 0.01,
                    "is_suspended": 0,
                }
            )
    return pd.DataFrame(rows)


def _signals(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "instrument", "weight"])


def _config(**overrides) -> dict:
    cfg = {
        "rebalance_every": 5,
        "target_weight": 0.10,
        "stop_loss": 0.10,
        "take_profit": 0.25,
        "max_hold_days": 45,
        "initial_capital": 1_000_000,
        "buy_price": "open",
        "sell_price": "close",
        "buy_fee_rate": 0.0003,
        "sell_fee_rate": 0.0013,
        "buy_fee_minimum": 5.0,
        "sell_fee_minimum": 5.0,
        "lot_size": 100,
        "slippage": 0.0,
    }
    cfg.update(overrides)
    return cfg


def _equity_on(curve: list[dict], date: str) -> dict:
    for row in curve:
        if _ymd(row["date"]) == date:
            return row
    raise AssertionError(f"missing equity row for {date}")


def _legs(trades: list[dict], *, side: str | None = None, instrument: str | None = None) -> list[dict]:
    out = trades
    if instrument is not None:
        out = [row for row in out if row["instrument"] == instrument]
    if side is not None:
        out = [row for row in out if row.get("side") == side]
    return out


def _notional(trade: dict) -> float:
    if "amount" in trade:
        return float(trade["amount"])
    return float(trade["price"]) * float(trade["quantity"])


def test_each_name_is_ten_percent_leftover_is_cash():
    dates = _trading_dates(3)
    frame = _frame(
        dates,
        {
            "AAA.SZ": {"price": 10.0},
            "BBB.SZ": {"price": 10.0},
        },
    )
    signals = _signals(
        [
            (dates[0], "AAA.SZ", 0.10),
            (dates[0], "BBB.SZ", 0.10),
        ]
    )

    trades, equity = run_target_weight_portfolio(frame, signals, _config())

    buys = _legs(trades, side="buy")
    assert {row["instrument"] for row in buys} == {"AAA.SZ", "BBB.SZ"}
    for row in buys:
        assert row["date"] == dates[1]
        assert _ymd(row.get("signal_date")) == dates[0]
        assert _notional(row) == pytest.approx(100_000.0, rel=0.02)

    before = _equity_on(equity, dates[0])
    assert before["invested"] == pytest.approx(0.0)
    snap = _equity_on(equity, dates[1])
    assert snap["invested"] / snap["equity"] == pytest.approx(0.20, abs=0.015)
    assert snap["cash"] / snap["equity"] == pytest.approx(0.80, abs=0.015)
    assert snap["cash"] > 0.75 * snap["equity"]


def test_empty_rebalance_sells_at_open():
    dates = _trading_dates(8)
    opens = [10.0] * 8
    opens[6] = 10.5
    closes = [10.0] * 8
    closes[6] = 11.0
    highs = [10.2] * 8
    highs[6] = 11.0
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": opens,
                "close": closes,
                "high": highs,
                "low": 9.5,
            }
        },
    )
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config(rebalance_every=5))

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    assert buys[0]["date"] == dates[1]
    sells = _legs(trades, side="sell", instrument="AAA.SZ")
    assert len(sells) == 1
    assert sells[0]["date"] == dates[6]
    assert sells[0]["price"] == pytest.approx(10.5)
    assert sells[0]["price"] != pytest.approx(11.0)
    assert sells[0].get("reason") in {"rebalance", "empty", "flat"}


def test_stop_loss_does_not_exit_on_entry_bar():
    dates = _trading_dates(4)
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": [10.0, 10.0, 10.0, 10.0],
                "close": [10.0, 10.5, 10.5, 10.5],
                "high": [10.0, 12.5, 12.5, 10.5],
                "low": [10.0, 9.0, 9.0, 10.0],
            }
        },
    )
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config())

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    sells = _legs(trades, side="sell", instrument="AAA.SZ")
    assert buys[0]["date"] == dates[1]
    assert not any(_ymd(row["date"]) == dates[1] for row in sells)
    assert len(sells) == 1
    assert sells[0]["date"] == dates[2]
    assert sells[0]["price"] == pytest.approx(9.0)
    assert sells[0]["price"] != pytest.approx(12.5)
    assert sells[0].get("reason") == "stop_loss"


def test_gap_through_stop_fills_at_open_not_stop_price():
    dates = _trading_dates(4)
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": [10.0, 10.0, 8.5, 8.5],
                "close": [10.0, 10.0, 8.4, 8.4],
                "high": [10.0, 10.2, 8.6, 8.6],
                "low": [10.0, 9.8, 8.0, 8.0],
            }
        },
    )
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config())

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    sells = _legs(trades, side="sell", instrument="AAA.SZ")
    assert buys[0]["date"] == dates[1]
    assert sells[0]["date"] == dates[2]
    assert sells[0].get("reason") == "stop_loss"
    assert sells[0]["price"] == pytest.approx(8.5)
    assert sells[0]["price"] != pytest.approx(9.0)


def test_max_hold_45_sells_at_close_no_refill_until_rebalance():
    n = 60
    rebalance_every = 7
    dates = _trading_dates(n)
    closes = [10.0] * n
    closes[45] = 10.4
    highs = [10.2] * n
    highs[45] = 10.4
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": 10.0,
                "close": closes,
                "high": highs,
                "low": 9.5,
            }
        },
    )
    rebalance_dates = dates[::rebalance_every]
    signals = _signals([(date, "AAA.SZ", 0.10) for date in rebalance_dates])

    trades, _equity = run_target_weight_portfolio(
        frame,
        signals,
        _config(rebalance_every=rebalance_every),
    )

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    sells = _legs(trades, side="sell", instrument="AAA.SZ")
    buy_dates = [_ymd(row["date"]) for row in buys]
    sell_dates = [_ymd(row["date"]) for row in sells]

    assert dates[1] in buy_dates
    assert dates[0] not in buy_dates
    max_hold_sells = [row for row in sells if _ymd(row["date"]) == dates[45]]
    assert len(max_hold_sells) == 1
    assert max_hold_sells[0]["price"] == pytest.approx(10.4)
    assert max_hold_sells[0].get("reason") == "max_hold"

    for gap_date in dates[46:50]:
        assert gap_date not in buy_dates

    assert dates[50] in buy_dates
    assert dates[45] not in buy_dates


def test_name_staying_in_top_n_is_not_churned():
    dates = _trading_dates(12)
    quiet = {"open": 10.0, "close": 10.0, "high": 10.2, "low": 9.8}
    frame = _frame(dates, {"AAA.SZ": dict(quiet), "BBB.SZ": dict(quiet)})
    signals = _signals(
        [
            (dates[0], "AAA.SZ", 0.10),
            (dates[0], "BBB.SZ", 0.10),
            (dates[5], "AAA.SZ", 0.10),
        ]
    )

    trades, _equity = run_target_weight_portfolio(frame, signals, _config(rebalance_every=5))

    aaa_buys = _legs(trades, side="buy", instrument="AAA.SZ")
    aaa_sells = _legs(trades, side="sell", instrument="AAA.SZ")
    bbb_sells = _legs(trades, side="sell", instrument="BBB.SZ")

    assert len(aaa_buys) == 1
    assert aaa_buys[0]["date"] == dates[1]
    assert not any(_ymd(row["date"]) == dates[6] for row in aaa_sells)
    assert not any(_ymd(row["date"]) == dates[7] for row in aaa_buys)
    assert len(bbb_sells) == 1
    assert bbb_sells[0]["date"] == dates[6]
    assert bbb_sells[0]["price"] == pytest.approx(10.0)


def test_buys_at_next_open_not_signal_day_open():
    dates = _trading_dates(4)
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": [9.0, 11.0, 11.0, 11.0],
                "close": [10.5, 10.5, 10.5, 10.5],
                "high": [11.0, 11.2, 11.2, 11.2],
                "low": [8.8, 10.8, 10.8, 10.8],
            }
        },
    )
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config())

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    assert len(buys) == 1
    assert buys[0]["date"] == dates[1]
    assert buys[0]["price"] == pytest.approx(11.0)
    assert buys[0]["price"] != pytest.approx(9.0)
    assert _ymd(buys[0].get("signal_date")) == dates[0]


def test_signal_on_last_bar_does_not_trade():
    dates = _trading_dates(1)
    frame = _frame(dates, {"AAA.SZ": {"open": 10.0, "close": 10.0, "high": 10.2, "low": 9.8}})
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, equity = run_target_weight_portfolio(frame, signals, _config())

    assert _legs(trades, side="buy") == []
    snap = _equity_on(equity, dates[0])
    assert snap["invested"] == pytest.approx(0.0)
    assert snap["cash"] == pytest.approx(1_000_000.0)


def test_default_slippage_is_not_zero():
    dates = _trading_dates(4)
    frame = _frame(
        dates,
        {
            "AAA.SZ": {
                "open": [9.0, 11.0, 11.0, 11.0],
                "close": [10.5, 10.5, 10.5, 10.5],
                "high": [11.0, 11.2, 11.2, 11.2],
                "low": [8.8, 10.8, 10.8, 10.8],
            }
        },
    )
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])
    config = {key: value for key, value in _config().items() if key != "slippage"}

    trades, _equity = run_target_weight_portfolio(frame, signals, config)

    buys = _legs(trades, side="buy", instrument="AAA.SZ")
    assert len(buys) == 1
    assert buys[0]["price"] == pytest.approx(11.0 * 1.0005)
    assert buys[0]["price"] != pytest.approx(11.0)


def test_missing_limit_columns_do_not_buy():
    dates = _trading_dates(4)
    frame = _frame(dates, {"AAA.SZ": {"open": 10.0, "close": 10.0, "high": 10.2, "low": 9.8}})
    frame = frame.drop(columns=[name for name in ("up_limit", "down_limit") if name in frame.columns])
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config())

    assert _legs(trades, side="buy") == []


def test_missing_suspend_column_blocks_buys():
    dates = _trading_dates(4)
    frame = _frame(dates, {"AAA.SZ": {"open": 10.0, "close": 10.0, "high": 10.2, "low": 9.8}})
    frame = frame.drop(columns=[name for name in ("is_suspended", "suspended") if name in frame.columns])
    assert "is_suspended" not in frame.columns
    assert "suspended" not in frame.columns
    signals = _signals([(dates[0], "AAA.SZ", 0.10)])

    trades, _equity = run_target_weight_portfolio(frame, signals, _config())

    assert _legs(trades, side="buy") == []
