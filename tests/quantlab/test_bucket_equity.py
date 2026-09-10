from __future__ import annotations

import threading
import time

import pandas as pd

from quantlab.services.bucket_equity import (
    assign_daily_quintiles,
    attach_segment_curves,
    bucket_predictions,
    bucket_worker_count,
    memory_safe_process_workers,
)
from quantlab.services.ranking_metrics import capture_predictions
from quantlab.services.backtest_job import _performance_metrics


def _panel() -> pd.DataFrame:
    rows = []
    dates = ["20200102", "20200103", "20200106", "20200107"]
    # Five names: A smallest cap / lowest turn, E largest / highest turn.
    names = ["AAA.SZ", "BBB.SZ", "CCC.SZ", "DDD.SZ", "EEE.SZ"]
    for date in dates:
        for index, name in enumerate(names):
            close = 10.0 + index
            rows.append(
                {
                    "date": date,
                    "instrument": name,
                    "open": close,
                    "close": close,
                    "up_limit": close + 2,
                    "down_limit": close - 2,
                    "suspended": False,
                    "float_market_cap": float((index + 1) * 1_000_000_000),
                    "turn": float((index + 1) * 0.5),
                    "score": float(index + 1),
                }
            )
    return pd.DataFrame(rows)


def test_assign_daily_quintiles_puts_smallest_cap_in_q1() -> None:
    frame = _panel()
    buckets = assign_daily_quintiles(frame, "float_market_cap")
    day = frame["date"] == "20200102"
    labeled = frame.loc[day].assign(bucket=buckets.loc[day].to_numpy())
    by_name = dict(zip(labeled["instrument"], labeled["bucket"], strict=True))
    assert by_name["AAA.SZ"] == 1
    assert by_name["EEE.SZ"] == 5


def test_bucket_predictions_keeps_only_that_quintile() -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    buckets = assign_daily_quintiles(frame, "float_market_cap")
    q1 = bucket_predictions(predictions, frame, buckets, 1)
    assert set(q1["instrument"]) == {"AAA.SZ"}
    q5 = bucket_predictions(predictions, frame, buckets, 5)
    assert set(q5["instrument"]) == {"EEE.SZ"}


def test_attach_segment_curves_runs_top_n_inside_each_cap_and_turn_bucket() -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    config = {
        "test": {"date_from": "20200102", "date_to": "20200107"},
        "rebalance_every": 1,
        "holding_days": 1,
        "top_n": 1,
        "lot_size": 100,
        "initial_capital": 100_000,
        "weighting": "equal",
        "buy_price": "open",
        "sell_price": "close",
    }
    payload = attach_segment_curves({}, frame, predictions, config)
    cap = payload["segment_curves"]["by_float_market_cap"]
    turn = payload["segment_curves"]["by_turn"]
    assert cap["status"] == "available"
    assert turn["status"] == "available"
    assert [item["label"] for item in cap["buckets"]] == ["Q1 小盘", "Q2", "Q3", "Q4", "Q5 大盘"]
    assert [item["label"] for item in turn["buckets"]] == ["Q1 低换手", "Q2", "Q3", "Q4", "Q5 高换手"]
    q1_names = set(cap["buckets"][0]["instruments"])
    q5_names = set(cap["buckets"][4]["instruments"])
    assert q1_names == {"AAA.SZ"}
    assert q5_names == {"EEE.SZ"}
    assert cap["buckets"][0]["equity_curve"]
    assert cap["buckets"][4]["equity_curve"]


def test_performance_metrics_reads_captured_predictions_for_segment_curves() -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    capture_predictions(frame, predictions)
    config = {
        "test": {"date_from": "20200102", "date_to": "20200107"},
        "rebalance_every": 1,
        "holding_days": 1,
        "top_n": 1,
        "lot_size": 100,
        "initial_capital": 100_000,
    }
    equity = [
        {"date": "20200102", "equity": 100_000.0},
        {"date": "20200107", "equity": 100_000.0},
    ]
    metrics = _performance_metrics([], config, frame, equity_curve=equity)
    assert metrics["segment_curves"]["by_float_market_cap"]["status"] == "available"
    assert metrics["segment_curves"]["by_turn"]["buckets"][0]["label"] == "Q1 低换手"


def _config() -> dict:
    return {
        "test": {"date_from": "20200102", "date_to": "20200107"},
        "rebalance_every": 1,
        "holding_days": 1,
        "top_n": 1,
        "lot_size": 100,
        "initial_capital": 100_000,
        "weighting": "equal",
        "buy_price": "open",
        "sell_price": "close",
    }


def test_bucket_worker_count_uses_eighty_percent_of_cpus(monkeypatch) -> None:
    monkeypatch.delenv("QUANTLAB_BUCKET_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.bucket_equity.cpu_count", lambda: 10)
    assert bucket_worker_count(90) == 8
    assert bucket_worker_count(3) == 3
    assert bucket_worker_count(1) == 1


def test_bucket_worker_count_env_overrides_cpu_percent(monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "4")
    monkeypatch.setattr("quantlab.services.bucket_equity.cpu_count", lambda: 10)
    assert bucket_worker_count(90) == 4


def test_bucket_worker_count_uses_settings_when_env_unset(monkeypatch) -> None:
    from quantlab.services.settings import apply_compute

    monkeypatch.delenv("QUANTLAB_BUCKET_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.bucket_equity.cpu_count", lambda: 10)
    apply_compute({"bucket_workers": 4})
    assert bucket_worker_count(90) == 4
    apply_compute({"bucket_workers": 0})
    assert bucket_worker_count(90) == 8


def test_memory_safe_process_workers_avoids_copying_frame_beyond_ram() -> None:
    four_gb = 4 * 1024**3
    ram_16 = 16 * 1024**3
    assert memory_safe_process_workers(four_gb, 4, ram=ram_16) == 1
    assert memory_safe_process_workers(four_gb, 10, ram=ram_16) == 1
    assert memory_safe_process_workers(512 * 1024**2, 4, ram=ram_16) >= 2
    assert memory_safe_process_workers(four_gb, 1, ram=ram_16) == 1


def test_tight_ram_does_not_spawn_bucket_processes(monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "4")
    monkeypatch.delenv("QUANTLAB_BUCKET_POOL", raising=False)
    monkeypatch.setattr("quantlab.services.bucket_equity.total_ram_bytes", lambda: 16 * 1024**3)
    monkeypatch.setattr("quantlab.services.bucket_equity.frame_nbytes", lambda frame: 4 * 1024**3)

    def boom(*_args, **_kwargs):
        raise AssertionError("process pool should not start when RAM cannot hold copies")

    monkeypatch.setattr("quantlab.services.bucket_equity.ProcessPoolExecutor", boom)
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    payload = attach_segment_curves({}, frame, predictions, _config())
    assert payload["segment_curves"]["by_float_market_cap"]["status"] == "available"


def test_segment_curves_spawn_pool_matches_serial(monkeypatch) -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "1")
    monkeypatch.setenv("QUANTLAB_BUCKET_POOL", "thread")
    serial = attach_segment_curves({}, frame.copy(), predictions.copy(), _config())
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "2")
    monkeypatch.delenv("QUANTLAB_BUCKET_POOL", raising=False)
    spawned = attach_segment_curves({}, frame.copy(), predictions.copy(), _config())
    assert serial["segment_curves"] == spawned["segment_curves"]


def test_segment_curves_parallel_matches_serial(monkeypatch) -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "1")
    serial = attach_segment_curves({}, frame.copy(), predictions.copy(), _config())
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "4")
    parallel = attach_segment_curves({}, frame.copy(), predictions.copy(), _config())
    assert serial["segment_curves"] == parallel["segment_curves"]


def test_segment_bucket_jobs_run_in_parallel(monkeypatch) -> None:
    import quantlab.services.bucket_equity as module

    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "4")
    monkeypatch.setenv("QUANTLAB_BUCKET_POOL", "thread")
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    lock = threading.Lock()
    active = 0
    max_active = 0
    original = module.engine_portfolio

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

    monkeypatch.setattr(module, "engine_portfolio", wrapped)
    payload = attach_segment_curves({}, frame, predictions, _config())
    assert payload["segment_curves"]["by_float_market_cap"]["status"] == "available"
    assert max_active >= 2


def test_empty_benchmark_open_dates_leave_bucket_equity_flat() -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    config = {
        **_config(),
        "open_when_benchmark_gt_ma200": True,
        "benchmark_open_dates": [],
    }
    payload = attach_segment_curves({}, frame, predictions, config)
    buckets = payload["segment_curves"]["by_float_market_cap"]["buckets"]
    assert buckets
    assert all(item["trade_count"] == 0 for item in buckets)
    assert all(item["total_return"] in {None, 0.0} for item in buckets)


def test_performance_metrics_fills_missing_benchmark_open_dates(monkeypatch) -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    capture_predictions(frame, predictions)
    monkeypatch.setattr(
        "quantlab.services.backtest_job.benchmark_trend_open_dates",
        lambda *_args, **_kwargs: {"20200102", "20200103", "20200106"},
    )
    config = {**_config(), "open_when_benchmark_gt_ma200": True}
    equity = [
        {"date": "20200102", "equity": 100_000.0},
        {"date": "20200107", "equity": 100_000.0},
    ]
    metrics = _performance_metrics([], config, frame, equity_curve=equity)
    buckets = metrics["segment_curves"]["by_float_market_cap"]["buckets"]
    assert any(item["trade_count"] > 0 for item in buckets)
