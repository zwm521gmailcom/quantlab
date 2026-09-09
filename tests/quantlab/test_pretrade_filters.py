from __future__ import annotations

import pandas as pd
import pytest

from quantlab.services.backtest_job import apply_expression_filters, apply_pretrade_filters, benchmark_pass_dates
from quantlab.services.trade_filters import parse_expr


def test_apply_pretrade_filters_drops_stock_days_and_benchmark_dates() -> None:
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103"],
            "instrument": ["AAA.SZ", "BBB.SZ", "AAA.SZ", "BBB.SZ"],
            "close": [10.0, 5.0, 10.0, 10.0],
            "low": [9.0, 4.0, 9.0, 9.0],
            "st_status": [0, 0, 0, 0],
            "is_suspended": [0, 0, 0, 0],
            "up_limit": [11.0, 11.0, 11.0, 11.0],
            "down_limit": [1.0, 1.0, 1.0, 1.0],
        }
    )
    index = pd.DataFrame(
        {
            "trade_date": ["20200102", "20200103"],
            "close": [100.0, 80.0],
            "hfq_close": [100.0, 80.0],
            "sma_200": [90.0, 90.0],
        }
    )
    kept = apply_pretrade_filters(
        frame,
        {
            "pretrade_filters": {
                "stock": ["close > 8"],
                "benchmark": ["close > sma200"],
            }
        },
        index_frame=index,
    )
    assert list(zip(kept["date"], kept["instrument"], strict=True)) == [("20200102", "AAA.SZ")]


def test_parse_expr_accepts_bq_ampersand_and() -> None:
    with_and = parse_expr("st_status==0 AND close>low")
    with_amp = parse_expr("st_status==0 & close>low")
    with_andand = parse_expr("st_status==0 && close>low")
    assert with_amp == with_and
    assert with_andand == with_and
    assert with_amp[0] == "and"


def test_apply_expression_filters_accepts_bq_ampersand() -> None:
    frame = pd.DataFrame(
        {
            "st_status": [0, 1, 0],
            "close": [10.0, 10.0, 5.0],
            "low": [9.0, 9.0, 6.0],
        }
    )
    kept = apply_expression_filters(frame, ["st_status==0 & close>low"])
    assert list(kept.index) == [0]
    kept_or = apply_expression_filters(frame, ["st_status==1 | close>low"])
    assert list(kept_or.index) == [0, 1]


def test_benchmark_pass_dates_uses_index_close_and_sma() -> None:
    index = pd.DataFrame(
        {
            "trade_date": ["20200102", "20200103"],
            "close": [100.0, 80.0],
            "sma_200": [90.0, 90.0],
        }
    )
    assert benchmark_pass_dates(index, ["close > sma200"]) == {"20200102"}


def test_attach_sma_is_rolling_mean_not_expanding_cumsum() -> None:
    from quantlab.services.backtest_job import attach_sma

    n = 210
    close = [float(i) for i in range(n)]
    frame = pd.DataFrame(
        {
            "instrument": ["X"] * n,
            "date": [f"{20170101 + i}" for i in range(n)],
            "hfq_close": close,
        }
    )
    out = attach_sma(frame)
    assert out["sma_200"].iloc[:199].isna().all()
    assert out["sma_200"].iloc[199] == pytest.approx(sum(range(200)) / 200)
    assert out["sma_200"].iloc[-1] == pytest.approx(sum(range(n - 200, n)) / 200)


def test_benchmark_pass_dates_builds_sma_from_trade_date() -> None:
    n = 201
    dates = pd.bdate_range("2018-01-02", periods=n).strftime("%Y%m%d").tolist()
    index = pd.DataFrame({"trade_date": dates, "close": [10.0] * 200 + [20.0]})
    assert benchmark_pass_dates(index, ["close > sma200"]) == {dates[-1]}


def test_stock_ma_on_short_slice_needs_sma_from_full_history() -> None:
    from quantlab.services.backtest_job import _attach_config_sma

    n = 210
    frame = pd.DataFrame(
        {
            "instrument": ["X"] * n,
            "date": [f"{20170101 + i}" for i in range(n)],
            "hfq_close": [10.0] * 200 + [20.0] * 10,
        }
    )
    assert apply_expression_filters(frame.iloc[-10:].copy(), ["hfq_close > sma200"]).empty
    prepared = _attach_config_sma(frame, {"pretrade_filters": {"stock": ["hfq_close > sma200"]}})
    kept = apply_expression_filters(prepared.iloc[-10:].copy(), ["hfq_close > sma200"])
    assert len(kept) == 10
    assert "sma_200" in kept.columns
