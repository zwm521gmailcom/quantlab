from __future__ import annotations

import pandas as pd

from quantlab.services.ranking_metrics import monthly_ranking_metrics


def test_monthly_ranking_metrics_groups_days_and_scores_perfect_ranking() -> None:
    frame = pd.DataFrame(
        {
            "date": ["20190201", "20190201", "20190201", "20190301", "20190301", "20190301"],
            "instrument": ["a", "b", "c", "a", "b", "c"],
            "_raw": [0.3, 0.2, 0.1, 0.1, 0.2, 0.3],
        }
    )
    predictions = pd.DataFrame(
        {
            "date": ["20190201", "20190201", "20190201", "20190301", "20190301", "20190301"],
            "instrument": ["a", "b", "c", "a", "b", "c"],
            "score": [3, 2, 1, 1, 2, 3],
        }
    )
    out = monthly_ranking_metrics(frame, predictions, k=2)
    assert out["rank_ic"] == 1.0
    assert out["ndcg_at_10"] == 1.0
    assert [row["month"] for row in out["monthly_ranking"]] == ["201902", "201903"]
    assert all(row["rank_ic"] == 1.0 for row in out["monthly_ranking"])


def test_monthly_ranking_metrics_returns_empty_without_scores() -> None:
    frame = pd.DataFrame({"date": ["20190201"], "instrument": ["a"], "_raw": [0.1]})
    predictions = pd.DataFrame({"date": ["20190201"], "instrument": ["a"]})
    out = monthly_ranking_metrics(frame, predictions)
    assert out == {"rank_ic": None, "ndcg_at_10": None, "monthly_ranking": []}
