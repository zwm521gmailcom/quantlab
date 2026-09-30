"""Day-indexed market lookup must match loc+iterrows without rescanning."""

from __future__ import annotations

import pandas as pd

from quantlab.services import portfolio


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103", "20200106"],
            "instrument": ["000001.SZ", "600000.SH", "000001.SZ", "600000.SH", "000001.SZ"],
            "open": [10.0, 20.0, 11.0, 21.0, 12.0],
            "close": [10.5, 20.5, 11.5, 21.5, 12.5],
        }
    )


def _naive_day_rows(frame: pd.DataFrame, date: str) -> dict[str, pd.Series]:
    subset = frame.loc[frame["date"].astype(str) == date]
    return {str(row["instrument"]): row for _, row in subset.iterrows()}


def test_index_day_rows_stores_plain_dicts_not_series() -> None:
    indexed = portfolio.index_day_rows(_frame())
    row = indexed["20200102"]["000001.SZ"]
    assert isinstance(row, dict)
    assert not isinstance(row, pd.Series)
    assert row["open"] == 10.0


def test_day_rows_returns_series_compatible_with_portfolio_to_dict() -> None:
    got = portfolio._day_rows(_frame(), "20200102")
    row = got["000001.SZ"]
    assert row.to_dict()["open"] == 10.0
    assert row["open"] == 10.0
    indexed = portfolio.index_day_rows(_frame())
    assert set(indexed) == {"20200102", "20200103", "20200106"}
    assert set(indexed["20200102"]) == {"000001.SZ", "600000.SH"}
    assert indexed["20200102"]["000001.SZ"]["open"] == 10.0
    assert indexed["20200103"]["600000.SH"]["close"] == 21.5


def test_day_rows_matches_loc_iterrows_mapping() -> None:
    frame = _frame()
    for date in ("20200102", "20200103", "20200106", "19990101"):
        got = portfolio._day_rows(frame, date)
        expected = _naive_day_rows(frame, date)
        assert set(got) == set(expected)
        for instrument, row in expected.items():
            assert got[instrument]["open"] == row["open"]
            assert got[instrument]["close"] == row["close"]


def test_day_rows_reuses_the_same_index_on_repeated_lookups() -> None:
    frame = _frame()
    first = portfolio._day_rows(frame, "20200102")
    cached = frame.attrs[portfolio._DAY_INDEX_ATTR]
    second = portfolio._day_rows(frame, "20200102")
    other = portfolio._day_rows(frame, "20200106")
    assert frame.attrs[portfolio._DAY_INDEX_ATTR] is cached
    assert set(first) == set(second) == {"000001.SZ", "600000.SH"}
    assert set(other) == {"000001.SZ"}


def test_day_rows_does_not_wrap_the_cross_section_as_series(monkeypatch) -> None:
    created = {"count": 0}
    real_series = pd.Series

    def wrapped(*args, **kwargs):
        created["count"] += 1
        return real_series(*args, **kwargs)

    monkeypatch.setattr(portfolio.pd, "Series", wrapped)
    frame = pd.DataFrame(
        {
            "date": ["20200102"] * 40,
            "instrument": [f"{index:06d}.SZ" for index in range(40)],
            "open": [10.0] * 40,
            "close": [10.5] * 40,
            "suspended": [False] * 40,
        }
    )
    rows = portfolio._day_rows(frame, "20200102")
    row = rows.get("000000.SZ")
    assert created["count"] == 0
    assert row is not None
    assert not isinstance(row, real_series)
    assert row.to_dict()["open"] == 10.0
    assert row["close"] == 10.5
    assert row.get("suspended", True) is False
    assert portfolio._finite_price(row, "open") == 10.0
    assert "000001.SZ" in rows
    assert rows.get("missing") is None


def test_eval_open_filters_parses_each_expression_once(monkeypatch) -> None:
    from quantlab.services import trade_filters

    calls = {"count": 0}
    real_parse = trade_filters.parse_expr

    def wrapped(text):
        calls["count"] += 1
        return real_parse(text)

    monkeypatch.setattr(trade_filters, "parse_expr", wrapped)
    trade_filters._parsed_open_filters.clear()
    row = {"open": 10.0, "up_limit": 11.0, "down_limit": 9.0}
    exprs = ["open < up_limit AND open > down_limit"]
    for _ in range(25):
        assert portfolio.eval_open_filters(exprs, row) is True
    assert calls["count"] == 1


def test_run_portfolio_keeps_slot_engine_fills_on_small_fixture() -> None:
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103", "20200106", "20200106", "20200107", "20200107"],
            "instrument": ["AAA.SZ", "BBB.SZ"] * 4,
            "open": [10, 20, 10.2, 20.2, 10.4, 20.4, 10.1, 20.1],
            "close": [10.1, 20.1, 10.3, 20.3, 10.5, 20.5, 10.0, 20.0],
            "up_limit": [12, 24] * 4,
            "down_limit": [8, 16] * 4,
            "suspended": [False] * 8,
        }
    )
    predictions = pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103", "20200106", "20200106"],
            "instrument": ["AAA.SZ", "BBB.SZ"] * 3,
            "score": [2.0, 1.0, 2.0, 1.0, 0.5, 3.0],
        }
    )
    trades, curve = portfolio.run_portfolio(
        frame,
        predictions,
        {
            "test": {"date_from": "20200102", "date_to": "20200107"},
            "rebalance_every": 2,
            "holding_days": 2,
            "top_n": 1,
            "lot_size": 100,
            "initial_capital": 100000,
            "trade_filters": {"open": ["open < up_limit AND open > down_limit"]},
            "buy_fee_rate": 0.0003,
            "sell_fee_rate": 0.0003,
            "stamp_tax_rate": 0.001,
            "slippage": 0,
        },
    )
    assert len(trades) == 1
    trade = trades[0]
    assert trade["instrument"] == "AAA.SZ"
    assert trade["buy_date"] == "20200103"
    assert trade["sell_date"] == "20200107"
    assert trade["quantity"] == 9800
    assert trade["buy_price"] == 10.2
    assert trade["sell_price"] == 10.0
    assert [row["date"] for row in curve] == ["20200102", "20200103", "20200106", "20200107"]


def test_target_weight_limit_down_open_sells_on_the_next_day() -> None:
    dates = ["20200102", "20200103", "20200106", "20200107"]
    rows = []
    for day in dates:
        limit_down = day == "20200106"
        rows.append(
            {
                "date": day,
                "instrument": "AAA.SZ",
                "open": 9.0 if limit_down else 10.0,
                "close": 10.0,
                "up_limit": 11.0,
                "down_limit": 9.0 if limit_down else 8.0,
                "suspended": False,
            }
        )
        rows.append(
            {
                "date": day,
                "instrument": "BBB.SZ",
                "open": 20.0,
                "close": 20.0,
                "up_limit": 22.0,
                "down_limit": 18.0,
                "suspended": False,
            }
        )
    predictions = pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103", "20200106", "20200106"],
            "instrument": ["AAA.SZ", "BBB.SZ", "AAA.SZ", "BBB.SZ", "AAA.SZ", "BBB.SZ"],
            "score": [2.0, 1.0, 0.0, 2.0, 0.0, 2.0],
        }
    )
    config = {
        "test": {"date_from": "20200102", "date_to": "20200107"},
        "rebalance_mode": "target_weight",
        "rebalance_every": 1,
        "holding_days": 1,
        "top_n": 1,
        "lot_size": 100,
        "initial_capital": 100000,
        "buy_price": "open",
        "sell_price": "close",
        "skip_close_down_limit": True,
        "trade_filters": {"open": ["open < up_limit AND open > down_limit"]},
        "buy_fee_rate": 0,
        "sell_fee_rate": 0,
        "buy_fee_minimum": 0,
        "sell_fee_minimum": 0,
        "stamp_tax_rate": 0,
        "slippage": 0,
    }
    trades, _curve = portfolio.run_portfolio(pd.DataFrame(rows), predictions, config)
    sold = [trade for trade in trades if trade.get("instrument") == "AAA.SZ" and trade.get("status") == "filled"]
    assert len(sold) == 1
    assert sold[0]["buy_date"] == "20200103"
    assert sold[0]["sell_date"] == "20200107"
