from __future__ import annotations

import pandas as pd
import pytest

from quantlab.services import model_training as mt
from quantlab.services.model_training import attach_label, holdout_split, validation_date_cutoff


def _panel() -> pd.DataFrame:
    rows = []
    dates = ["20240102", "20240103", "20240104", "20240105"]
    # holding_days=1 label at t uses t+1 open and t+2 close.
    # A: t0 label 0.01, t1 label 1.0
    # B: t0 label 0.02, t1 label -0.50
    specs = {
        "AAA.SZ": {
            "hfq_open": [10.0, 10.0, 10.0, 10.0],
            "hfq_close": [10.0, 10.0, 10.1, 20.0],
        },
        "BBB.SZ": {
            "hfq_open": [10.0, 10.0, 10.0, 10.0],
            "hfq_close": [10.0, 10.0, 10.2, 5.0],
        },
    }
    for instrument, prices in specs.items():
        for index, date in enumerate(dates):
            open_ = prices["hfq_open"][index]
            close = prices["hfq_close"][index]
            rows.append(
                {
                    "instrument": instrument,
                    "date": date,
                    "hfq_open": open_,
                    "hfq_close": close,
                    "high": close + 0.1,
                    "low": open_ - 0.1,
                }
            )
    return pd.DataFrame(rows)


def test_label_bins_are_cross_sectional_per_date() -> None:
    labeled = attach_label(_panel(), holding_days=1)
    first = labeled.loc[labeled["date"] == "20240102"].set_index("instrument")
    assert first.loc["AAA.SZ", "_raw"] == pytest.approx(0.01)
    assert first.loc["BBB.SZ", "_raw"] == pytest.approx(0.02)
    bins = sorted(first["_target"].tolist())
    assert bins == [0.0, 1.0]


def test_validation_cutoff_holds_out_last_fifth_of_dates() -> None:
    dates = pd.Series([f"202401{index:02d}" for index in range(1, 11)])
    cutoff = validation_date_cutoff(dates, fraction=0.8)
    assert cutoff == "20240108"
    assert (dates <= cutoff).sum() == 8
    assert (dates > cutoff).sum() == 2


def test_holdout_split_uses_explicit_validation_window() -> None:
    dates = ["20240102", "20240103", "20240104", "20240108", "20240109"]
    train_days, valid_days = holdout_split(
        dates,
        {
            "train_fit_date_to": "20240103",
            "validation_date_from": "20240104",
            "validation_date_to": "20240108",
        },
    )
    assert train_days == ["20240102", "20240103"]
    assert valid_days == ["20240104", "20240108"]


def _fit_frame():
    features = pd.DataFrame({"momentum_5": [1.0, 2.0, 3.0, 4.0]})
    target = pd.Series([0.0, 1.0, 2.0, 3.0])
    dates = pd.Series(["20240102", "20240103", "20240104", "20240108"])
    params = {
        "train_fit_date_to": "20240103",
        "validation_date_from": "20240104",
        "validation_date_to": "20240108",
        "alpha": 1.0,
        "number_of_trees": 5,
        "max_bins": 256,
        "max_depth": 6,
        "min_child_samples": 1,
        "learning_rate": 0.1,
        "label_gain": "linear_0_19",
        "metric": "ndcg",
        "ndcg_eval_at": 10,
    }
    return features, target, dates, params


def test_fit_estimator_fits_all_rows_including_validation(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_fit(kind, features, target, params, dates=None):
        seen["kind"] = kind
        seen["n"] = len(features)
        seen["dates"] = [str(day) for day in dates]
        return "ridge"

    monkeypatch.setattr(mt, "_fit_estimator_impl", fake_fit)
    features, target, dates, params = _fit_frame()
    result = mt.fit_estimator("ridge_linear", features, target, params, dates)
    assert result == "ridge"
    assert seen["kind"] == "ridge_linear"
    assert seen["n"] == 4
    assert seen["dates"] == ["20240102", "20240103", "20240104", "20240108"]


def test_fit_xgb_fits_all_rows_including_validation(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_fit(features, target, params, dates=None):
        seen["n"] = len(features)
        seen["dates"] = [str(day) for day in dates]
        return "xgb"

    monkeypatch.setattr(mt, "_fit_xgb_impl", fake_fit)
    features, target, dates, params = _fit_frame()
    result = mt.fit_xgb(features, target, params, dates)
    assert result == "xgb"
    assert seen["n"] == 4
    assert seen["dates"] == ["20240102", "20240103", "20240104", "20240108"]


def test_fit_lgb_uses_random_seed_from_params(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_impl(features, target, params, dates=None):
        captured["seed"] = mt.LIGHTGBM_RANKER_SEED
        return object()

    monkeypatch.setattr(mt, "_fit_lgb_impl", fake_impl)
    features, target, dates, params = _fit_frame()
    params = {**params, "random_seed": 99, "num_leaves": 8, "number_of_trees": 5}
    mt.fit_lgb(features, target, params, dates)
    assert captured["seed"] == 99


def test_fit_lgb_caps_num_threads_when_parallel_folds(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_train(spec, *args, **kwargs):
        captured["spec"] = spec
        return object()

    monkeypatch.setattr("lightgbm.train", fake_train)
    features, target, dates, params = _fit_frame()
    params = {**params, "random_seed": 7, "num_leaves": 8, "number_of_trees": 5}
    with mt.booster_thread_limit(8, cpu_fn=lambda: 10):
        mt.fit_lgb(features, target, params, dates)
    assert captured["spec"]["num_threads"] == 1


def test_fit_lgb_does_not_call_early_stopping(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_train(spec, *args, **kwargs):
        captured["kwargs"] = kwargs
        captured["spec"] = spec
        return object()

    monkeypatch.setattr("lightgbm.train", fake_train)
    features, target, dates, params = _fit_frame()
    params = {**params, "random_seed": 7, "num_leaves": 8, "number_of_trees": 5}
    mt.fit_lgb(features, target, params, dates)
    kwargs = captured.get("kwargs") or {}
    assert "valid_sets" not in kwargs
    callbacks = kwargs.get("callbacks") or []
    assert not any("early" in type(item).__name__.lower() for item in callbacks)
