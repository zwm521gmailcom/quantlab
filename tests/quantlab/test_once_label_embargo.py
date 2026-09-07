from __future__ import annotations

import pandas as pd
import pytest

from quantlab.services.backtest_job import (
    apply_label_cutoff,
    attach_label,
    once_label_cutoff,
    once_label_embargo,
)


def test_once_label_cutoff_is_holding_plus_one_sessions_before_test() -> None:
    calendar = ["20240102", "20240103", "20240104", "20240105", "20240108"]
    assert once_label_cutoff(calendar, "20240108", holding_days=1) == "20240104"
    assert once_label_cutoff(calendar, "20240108", holding_days=2) == "20240103"


def test_apply_label_cutoff_drops_dates_after_cutoff() -> None:
    frame = pd.DataFrame(
        {
            "date": ["20240103", "20240104", "20240105"],
            "instrument": ["AAA.SZ", "AAA.SZ", "AAA.SZ"],
            "_raw": [0.1, 0.2, 0.3],
        }
    )
    kept = apply_label_cutoff(frame, "20240104")
    assert list(kept["date"]) == ["20240103", "20240104"]


def _price_panel(dates: list[str]) -> pd.DataFrame:
    rows = []
    for instrument, close0 in (("AAA.SZ", 10.0), ("BBB.SZ", 11.0)):
        for index, date in enumerate(dates):
            close = close0 + index
            rows.append(
                {
                    "instrument": instrument,
                    "date": date,
                    "hfq_open": close,
                    "hfq_close": close,
                    "high": close + 0.1,
                    "low": close - 0.1,
                }
            )
    return pd.DataFrame(rows)


def test_once_attach_label_does_not_keep_labels_that_use_test_session() -> None:
    frame = _price_panel(["20240102", "20240103", "20240104", "20240105", "20240108"])
    with once_label_embargo("20240108", holding_days=1):
        labeled = attach_label(frame, holding_days=1)
    dates = labeled["date"].astype(str).str.replace("-", "", regex=False)
    assert dates.max() == "20240104"
    assert "20240105" not in set(dates)
    assert "20240108" not in set(dates)


def test_once_attach_label_drops_train_rows_that_use_validation_but_keeps_val() -> None:
    frame = _price_panel(
        ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109", "20240110"]
    )
    with once_label_embargo("20240110", holding_days=1, validation_from="20240108"):
        labeled = attach_label(frame, holding_days=1)
    dates = set(labeled["date"].astype(str).str.replace("-", "", regex=False))
    assert "20240104" in dates
    assert "20240105" not in dates
    assert "20240108" in dates
    assert "20240109" not in dates
    assert "20240110" not in dates
