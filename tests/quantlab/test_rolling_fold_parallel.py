from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from quantlab.services.backtest_job import BacktestJobService, fold_worker_count
from quantlab.services.model_training import booster_thread_count


def test_fold_worker_count_uses_eighty_percent_of_cpus(monkeypatch) -> None:
    monkeypatch.delenv("QUANTLAB_FOLD_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    assert fold_worker_count(90) == 8
    assert fold_worker_count(3) == 3
    assert fold_worker_count(1) == 1


def test_fold_worker_count_collapses_when_budget_says_process_is_fat(monkeypatch) -> None:
    monkeypatch.delenv("QUANTLAB_FOLD_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    monkeypatch.setattr("quantlab.services.backtest_job.total_ram_bytes", lambda: 16 * 1024**3)
    from quantlab.services.compute_budget import cap_workers

    assert cap_workers(
        requested=8,
        task_count=90,
        ram_bytes=16 * 1024**3,
        unit_bytes=0,
        kind="fold",
        prior_peak_rss_bytes=9 * 1024**3,
    ) == 1
    assert fold_worker_count(90, prior_peak_rss_bytes=9 * 1024**3) == 1
    assert fold_worker_count(90) == 8


def test_fold_worker_count_env_overrides_cpu_percent(monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "4")
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    assert fold_worker_count(90) == 4


def test_fold_worker_count_uses_settings_when_env_unset(monkeypatch) -> None:
    from quantlab.services.settings import apply_compute

    monkeypatch.delenv("QUANTLAB_FOLD_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    apply_compute({"fold_workers": 4})
    assert fold_worker_count(90) == 4
    apply_compute({"fold_workers": 0})
    assert fold_worker_count(90) == 8


def test_booster_thread_count_splits_cpus_across_fold_workers() -> None:
    assert booster_thread_count(1, lambda: 10) is None
    assert booster_thread_count(8, lambda: 10) == 1
    assert booster_thread_count(4, lambda: 10) == 2
    assert booster_thread_count(10, lambda: 10) == 1


def _weekdays(start: str, end: str) -> list[str]:
    begin = datetime.strptime(start, "%Y%m%d")
    stop = datetime.strptime(end, "%Y%m%d")
    days = []
    current = begin
    while current <= stop:
        if current.weekday() < 5:
            days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _panel(start: str = "20190101", end: str = "20190430") -> pd.DataFrame:
    dates = _weekdays(start, end)
    instruments = [f"00000{index}.SZ" for index in range(8)]
    rows = []
    for instrument in instruments:
        seed = sum(ord(ch) for ch in instrument)
        for offset, date in enumerate(dates):
            close = 10.0 + (seed % 5) * 0.1 + offset * 0.01
            rows.append(
                {
                    "date": date,
                    "instrument": instrument,
                    "hfq_open": close,
                    "hfq_close": close + 0.02,
                    "open": close,
                    "close": close,
                    "momentum_5": 0.1 + (seed % 7) * 0.01 + offset * 0.001,
                    "st_status": 0,
                    "suspended": False,
                    "up_limit": close + 1,
                    "down_limit": close - 1,
                }
            )
    return pd.DataFrame(rows)


def _config() -> dict:
    return {
        "walk_forward": "rolling",
        "train_period_months": 1,
        "test_period_months": 1,
        "train": {
            "date_from": "20190101",
            "filter": {"skip_limit_close": False},
        },
        "test": {"date_to": "20190430"},
    }


def _split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = frame.loc[frame["date"] <= "20190131"].copy()
    test = frame.loc[frame["date"] >= "20190201"].copy()
    return train, test


def _run(job: BacktestJobService, frame: pd.DataFrame):
    train, test = _split(frame)
    return job._rolling_predictions(
        frame=frame,
        train=train,
        test=test,
        config=_config(),
        kind="ridge_linear",
        params={"alpha": 1.0, "random_seed": 123},
        feature_fields=["momentum_5"],
        holding_days=2,
        filter_notes=[],
    )


def test_rolling_predictions_run_independent_folds_in_parallel(monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "4")
    frame = _panel()
    job = BacktestJobService.__new__(BacktestJobService)
    lock = threading.Lock()
    active = 0
    max_active = 0
    original = __import__("quantlab.services.backtest_job", fromlist=["fit_estimator"]).fit_estimator
    import quantlab.services.backtest_job as job_mod

    def wrapped(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        try:
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(job_mod, "fit_estimator", wrapped)
    predictions, _labeled, fold_rows = _run(job, frame)
    assert len(fold_rows) >= 2
    assert not predictions.empty
    assert max_active >= 2


def test_rolling_predictions_parallel_scores_match_serial(monkeypatch) -> None:
    frame = _panel()
    job = BacktestJobService.__new__(BacktestJobService)
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "1")
    serial_pred, _, serial_folds = _run(job, frame)
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "4")
    parallel_pred, _, parallel_folds = _run(job, frame)
    left = serial_pred.sort_values(["date", "instrument"]).reset_index(drop=True)
    right = parallel_pred.sort_values(["date", "instrument"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)
    assert [row["month"] for row in serial_folds] == [row["month"] for row in parallel_folds]
    assert [row["train_rows"] for row in serial_folds] == [row["train_rows"] for row in parallel_folds]

