from __future__ import annotations

import pandas as pd

from quantlab.services.backtest_job import _performance_metrics


def test_performance_metrics_adds_sortino_calmar_streak_and_annual_summary() -> None:
    equity = [
        {"date": "20200102", "equity": 1_000_000.0},
        {"date": "20200103", "equity": 1_010_000.0},
        {"date": "20200106", "equity": 990_000.0},
        {"date": "20210104", "equity": 1_100_000.0},
    ]
    metrics = _performance_metrics(
        [],
        {"initial_capital": 1_000_000.0},
        pd.DataFrame(),
        equity_curve=equity,
    )
    assert metrics["sortino"] is not None
    assert metrics["calmar"] is not None
    assert metrics["max_loss_streak"] >= 1
    years = {str(row["year"]) for row in metrics["annual_summary"]}
    assert "2020" in years
    assert "2021" in years
