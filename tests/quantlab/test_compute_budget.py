from __future__ import annotations

import json
from pathlib import Path

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.compute_budget import (
    cap_workers,
    last_resource_sample,
    run_signature,
)


def test_run_signature_stable_for_same_window() -> None:
    config = {
        "dataset_id": "ds_canonical_market",
        "dataset_version_id": "current",
        "kind": "lgbm_ranker",
        "train": {"date_from": "2019-01-02", "date_to": "2023-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2025-12-31"},
        "walk_forward": {"mode": "rolling"},
    }
    assert run_signature(config) == run_signature(dict(config))
    other = {**config, "test": {**config["test"], "date_to": "2026-08-31"}}
    assert run_signature(config) != run_signature(other)
    as_string = {**config, "walk_forward": "rolling"}
    assert run_signature(as_string) != run_signature({**as_string, "walk_forward": "once"})


def test_cap_workers_without_prior_matches_cpu_request() -> None:
    assert cap_workers(requested=8, task_count=90, ram_bytes=16 * 1024**3, unit_bytes=0, kind="fold") == 1
    assert cap_workers(requested=8, task_count=3, ram_bytes=16 * 1024**3, unit_bytes=0, kind="fold") == 1


def test_cap_workers_fold_shares_budget_across_concurrent_slots(monkeypatch) -> None:
    ram = 128 * 1024**3
    monkeypatch.setattr("quantlab.services.settings.max_concurrent_backtests", lambda ram=None, cpu=None: 4)
    assert cap_workers(requested=4, task_count=90, ram_bytes=ram, unit_bytes=0, kind="fold") == 1
    monkeypatch.setattr("quantlab.services.settings.max_concurrent_backtests", lambda ram=None, cpu=None: 1)
    assert cap_workers(requested=4, task_count=90, ram_bytes=ram, unit_bytes=0, kind="fold") == 4


def test_cap_workers_fold_collapses_when_prior_peak_is_half_of_ram() -> None:
    ram = 16 * 1024**3
    assert cap_workers(
        requested=8,
        task_count=90,
        ram_bytes=ram,
        unit_bytes=0,
        kind="fold",
        prior_peak_rss_bytes=int(ram * 0.50),
    ) == 1


def test_cap_workers_bucket_uses_prior_unit_cost() -> None:
    ram = 16 * 1024**3
    assert cap_workers(
        requested=4,
        task_count=10,
        ram_bytes=ram,
        unit_bytes=512 * 1024**2,
        kind="bucket",
        prior_peak_rss_bytes=8 * 1024**3,
        prior_workers=2,
    ) == 1


def _insert_completed_run(database: Database, run_id: str, signature: str, peak: int) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
            (run_id, "2026-09-02T12:00:00+00:00", "2026-09-02T12:01:00+00:00"),
        )
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status, config_json, metrics_json) VALUES (?, 'queued', '{}', ?)",
            (
                run_id,
                json.dumps({"resources": {"signature": signature, "peak_rss_bytes": peak, "bucket_workers": 2}}),
            ),
        )
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='completed' WHERE run_id=?", (run_id,))


def test_last_resource_sample_returns_newest_matching_signature(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    target = run_signature(
        {
            "dataset_id": "ds",
            "dataset_version_id": "v1",
            "kind": "lgbm_ranker",
            "train": {"date_from": "2019-01-02"},
            "test": {"date_from": "2024-01-02", "date_to": "2025-12-31"},
            "walk_forward": {"mode": "rolling"},
        }
    )
    other = run_signature(
        {
            "dataset_id": "ds",
            "dataset_version_id": "v1",
            "kind": "lgbm_ranker",
            "train": {"date_from": "2019-01-02"},
            "test": {"date_from": "2024-01-02", "date_to": "2026-08-31"},
            "walk_forward": {"mode": "rolling"},
        }
    )
    _insert_completed_run(database, "20260902-120000-0001", target, 111)
    _insert_completed_run(database, "20260902-120001-0002", other, 999)
    _insert_completed_run(database, "20260902-120002-0003", target, 222)
    sample = last_resource_sample(database, target)
    assert sample is not None
    assert sample["signature"] == target
    assert sample["peak_rss_bytes"] == 222
    assert last_resource_sample(database, "missing-signature") is None
