from __future__ import annotations

import pandas as pd
import pytest

from quantlab.services.backtest_job import factor_rank_predictions


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["20200102"] * 3,
            "instrument": ["AAA.SZ", "BBB.SZ", "CCC.SZ"],
            "turn": [1.0, 2.0, 3.0],
            "dividend_yield_ratio": [0.03, 0.02, 0.01],
            "float_market_cap": [30.0, 20.0, 10.0],
        }
    )


def test_single_factor_still_uses_raw_value() -> None:
    out = factor_rank_predictions(_frame(), "turn", direction="positive")
    assert list(out["instrument"]) == ["AAA.SZ", "BBB.SZ", "CCC.SZ"]
    assert list(out["score"]) == [1.0, 2.0, 3.0]


def test_single_factor_negative_flips_raw_value() -> None:
    out = factor_rank_predictions(_frame(), "turn", direction="negative")
    assert list(out["score"]) == [-1.0, -2.0, -3.0]


def test_multi_factor_equal_cs_rank_unifies_direction() -> None:
    refs = [
        {"field": "turn", "direction": "negative"},
        {"field": "dividend_yield_ratio", "direction": "positive"},
        {"field": "float_market_cap", "direction": "negative"},
    ]
    out = factor_rank_predictions(
        _frame(),
        "turn",
        direction="positive",
        factor_versions=refs,
    )
    by_name = {row.instrument: row.score for row in out.itertuples()}
    # pct rank on 3 names: 1/3, 2/3, 1.0
    # turn reverse: AAA 1-1/3=2/3, BBB 1-2/3=1/3, CCC 0
    # div forward: AAA 1.0, BBB 2/3, CCC 1/3
    # mcap reverse: AAA 0, BBB 1/3, CCC 2/3
    # equal mean: AAA (2/3+1+0)/3=5/9, BBB (1/3+2/3+1/3)/3=4/9, CCC (0+1/3+2/3)/3=1/3
    assert by_name["AAA.SZ"] == pytest.approx(5 / 9)
    assert by_name["BBB.SZ"] == pytest.approx(4 / 9)
    assert by_name["CCC.SZ"] == pytest.approx(1 / 3)
    assert list(out.sort_values("score", ascending=False)["instrument"]) == ["AAA.SZ", "BBB.SZ", "CCC.SZ"]


def test_multi_factor_drops_row_if_any_leg_missing() -> None:
    frame = _frame()
    frame.loc[frame["instrument"] == "BBB.SZ", "turn"] = float("nan")
    refs = [
        {"field": "turn", "direction": "negative"},
        {"field": "dividend_yield_ratio", "direction": "positive"},
    ]
    out = factor_rank_predictions(frame, "turn", factor_versions=refs)
    assert set(out["instrument"]) == {"AAA.SZ", "CCC.SZ"}
