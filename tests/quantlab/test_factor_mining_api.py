from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database


def _client(tmp_path: Path) -> TestClient:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "features.parquet"
    rows = []
    for date, close, future_return in zip(
        ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"],
        [10, 11, 12, 13, 14, 15],
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        strict=True,
    ):
        rows.append((date, "000001.SZ", close, future_return))
        rows.append((date, "000002.SZ", close + 1, future_return + 0.01))
    pq.write_table(
        pa.table({
            "date": [row[0] for row in rows],
            "instrument": [row[1] for row in rows],
            "close": [row[2] for row in rows],
            "future_return": [row[3] for row in rows],
            "eligible": [1] * len(rows),
        }),
        path,
    )
    settings = Settings(project_root=tmp_path, data_root=data_root, calibration_root=tmp_path / "calibration", runtime_root=tmp_path / "runtime")
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds_features', '特征', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_features', 'v1', ?, ?, ?, '20240102', '20240109', 'published', 'passed')",
            (str(path), len(rows), json.dumps(["date", "instrument", "close", "future_return"])),
        )
    return TestClient(create_app(settings, database))


def _config() -> dict[str, object]:
    return {
        "dataset_id": "ds_features",
        "dataset_version_id": "v1",
        "date_from": "20240102",
        "train_end": "20240103",
        "validation_end": "20240105",
        "date_to": "20240109",
        "seed": 7,
        "max_candidates": 4,
        "max_depth": 4,
        "correlation_threshold": 0.95,
        "min_validation_coverage": 0.0,
        "min_validation_rank_ic": -1.0,
    }


def test_factor_mining_api_runs_and_returns_auditable_candidates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/factor-mining/runs", json={"config": _config()})
    assert response.status_code == 201
    payload = response.json()
    assert payload["research_run"]["research_type"] == "automatic"
    assert payload["research_run"]["status"] == "completed"
    assert payload["selection_period"] == "validation"
    assert payload["candidates"]
    assert payload["rejections"]
    assert payload["research_run"]["config"]["search"]["windows"] == [1, 2, 5]
    run_id = payload["research_run"]["run_id"]
    detail = client.get(f"/api/factor-mining/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["lineage"]["dataset_version_id"] == "v1"
    assert detail.json()["candidates"][0]["kept_in_task"] is True
    assert detail.json()["candidates"][0]["enabled"] is False
    assert client.get("/api/factors").json()["total"] == 0


def test_factor_mining_does_not_enter_library_until_task_enable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = client.post("/api/factor-mining/runs", json={"config": _config()}).json()
    run_id = payload["research_run"]["run_id"]
    leftover = client.post("/api/factors/import", json={
        "entity_id": "factor_auto_leftover_1",
        "name": "自动候选 9",
        "category": "自动挖掘",
        "version_id": "v1",
        "dataset_id": "ds_features",
        "dataset_version_id": "v1",
        "formula": "close",
        "input_fields": ["close"],
        "source": "automatic_mining",
        "direction": "positive",
        "frequency": "daily",
        "missing_policy": "drop",
        "pit_policy": "as-of observation date",
        "pit_lineage": {"rule": "as-of observation date", "snapshot": "dataset:ds_features:v1", "window_mode": "per_instrument_observation"},
        "upstream_factor_versions": [],
        "origin": "automatic",
        "author": "QuantLab",
        "quality_status": "needs_review",
    })
    assert leftover.status_code == 201
    assert client.get("/api/factors").json()["total"] == 0
    jobs = client.get("/api/factor-jobs")
    assert jobs.status_code == 200
    assert jobs.json()["total"] == 1
    assert jobs.json()["items"][0]["run_id"] == run_id
    detail = client.get(f"/api/factor-jobs/{run_id}")
    assert detail.status_code == 200
    items = detail.json()["items"]
    assert items
    kept = [item for item in items if item["kept_in_task"] and not item["enabled"]]
    assert kept
    selected = [kept[0]["candidate_id"]]
    enabled = client.post(f"/api/factor-jobs/{run_id}/enable", json={"candidate_ids": selected})
    assert enabled.status_code == 200
    payload = enabled.json()
    assert payload["enabled_count"] == 1
    calculation = payload["items"][0]["calculation"]
    assert calculation["status"] == "completed"
    library = client.get("/api/factors").json()
    assert library["total"] == 1
    assert library["items"][0]["quality"]["enabled_from_task"] is True
    assert library["items"][0]["origin"] == "automatic"
    assert library["items"][0]["entity_id"] != "factor_auto_leftover_1"
    factor_id = payload["items"][0]["enabled_entity_id"].removeprefix("factor_")
    latest = client.get(f"/api/factor-calculations/latest/{factor_id}")
    assert latest.status_code == 200
    assert latest.json()["status"] == "completed"
    assert "ic_mean" in latest.json()
    catalog = client.get("/api/factor-data/catalog").json()
    entry = next(item for item in catalog if item["factor_id"] == factor_id)
    assert entry["latest_calculation"]["status"] == "completed"
    draft = client.post(f"/api/factor-mining/runs/{run_id}/candidates/{kept[0]['dedupe_key']}/draft")
    assert draft.status_code == 400
    assert client.get("/api/research-runs", params={"research_type": "automatic"}).json()["total"] == 1
    assert client.get("/api/backtest-drafts").status_code in {404, 405}


def test_factor_mining_api_rejects_malformed_config_without_creating_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/factor-mining/runs", json={"config": {"dataset_id": "outside"}})
    assert response.status_code == 400
    assert response.json()["error_code"] == "FACTOR_MINING_INVALID"
    assert client.get("/api/research-runs", params={"research_type": "automatic"}).json()["total"] == 0


def test_factor_mining_job_cancel_persists_cancelled_failure_and_checkpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/factor-mining/jobs", json={"config": _config()})
    assert response.status_code == 201
    run = response.json()["research_run"]
    run_id = run["run_id"]

    checkpoint = client.post(
        f"/api/factor-mining/jobs/{run_id}/checkpoint",
        json={
            "processed_candidates": ["already-processed"],
            "processed_dedupe_keys": ["dedupe-1"],
            "logs": ["candidate already-processed evaluated"],
        },
    )
    assert checkpoint.status_code == 200
    assert checkpoint.json()["checkpoint"]["processed_dedupe_keys"] == ["dedupe-1"]

    cancelled = client.post(f"/api/factor-mining/jobs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["research_run"]["status"] == "failed"
    assert cancelled.json()["research_run"]["error_message"] == "cancelled"
    assert cancelled.json()["research_run"]["summary"]["checkpoint"]["cancel_reason"] == "cancelled"


def test_factor_mining_job_resume_creates_child_run_with_checkpoint_and_parent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    original = client.post("/api/factor-mining/jobs", json={"config": _config()}).json()["research_run"]
    run_id = original["run_id"]
    client.post(
        f"/api/factor-mining/jobs/{run_id}/checkpoint",
        json={"processed_candidates": ["c1"], "processed_dedupe_keys": ["d1"], "logs": ["kept c1"]},
    )
    client.post(f"/api/factor-mining/jobs/{run_id}/cancel")

    resumed = client.post(f"/api/factor-mining/jobs/{run_id}/resume")
    assert resumed.status_code == 201
    child = resumed.json()["research_run"]
    assert child["run_id"] != run_id
    assert child["parent_run_id"] == run_id
    assert child["config"]["job_checkpoint"]["processed_dedupe_keys"] == ["d1"]
    assert child["summary"]["checkpoint"]["processed_dedupe_keys"] == ["d1"]
    assert child["summary"]["checkpoint"]["resumed_from_run_id"] == run_id
