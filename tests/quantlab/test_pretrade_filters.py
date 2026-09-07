from __future__ import annotations

import pandas as pd

from quantlab.services.backtest_job import apply_pretrade_filters, benchmark_pass_dates


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


def test_benchmark_pass_dates_uses_index_close_and_sma() -> None:
    index = pd.DataFrame(
        {
            "trade_date": ["20200102", "20200103"],
            "close": [100.0, 80.0],
            "sma_200": [90.0, 90.0],
        }
    )
    assert benchmark_pass_dates(index, ["close > sma200"]) == {"20200102"}
