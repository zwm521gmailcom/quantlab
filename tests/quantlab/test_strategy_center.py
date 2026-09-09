from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.strategy_center import StrategyCenterService


RUN_ID = "20260902-120000-0001"


def _setup(tmp_path: Path) -> tuple[Settings, Database, StrategyCenterService]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    data_path = data_root / "canonical.parquet"
    pq.write_table(pa.table({"trade_date": ["20240102"], "ts_code": ["000001.SZ"], "hfq_close": [10.0]}), data_path)
    settings = Settings(project_root=tmp_path, data_root=data_root, calibration_root=tmp_path / "calibration", runtime_root=tmp_path / "runtime")
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds_canonical_market', '标准行情宽表', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, status, quality_status) VALUES ('ds_canonical_market', 'current', ?, 1, ?, 'published', 'passed')",
            (str(data_path), json.dumps(["trade_date", "ts_code", "hfq_close"])),
        )
        connection.execute("INSERT INTO factors(entity_id, name, category, status) VALUES ('factor_momentum_5', '五日动量', '技术', 'published')")
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, status, quality_status) VALUES ('factor_momentum_5', 'v1', 'ds_canonical_market', 'current', 'hfq_close / hfq_close.shift(5) - 1', '[\"hfq_close\"]', 'published', 'passed')"
        )
    return settings, database, StrategyCenterService(settings, database, FactorRepository(settings, database))


def _model_config() -> dict[str, object]:
    return {
        "dataset_id": "ds_canonical_market",
        "dataset_version_id": "current",
        "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1", "direction": "positive"}],
        "label": {"formula": "hfq_close.shift(-1) / hfq_open - 1", "price_fields": ["hfq_open", "hfq_close"]},
        "train_window": {"start": "2023-01-01", "end": "2023-12-31"},
        "validation_window": {"start": "2024-01-01", "end": "2024-03-31"},
        "test_window": {"start": "2024-04-01", "end": "2024-06-30"},
        "train_filter": {"st_status": 0, "suspended": False},
        "validation_filter": {"st_status": 0, "suspended": False},
        "test_filter": {"st_status": 0, "suspended": False},
        "preprocessing": {"missing": "drop", "winsorize": "1%/99%"},
        "hyperparameters": {"number_of_trees": 5, "max_bins": 511},
        "random_seed": 7,
        "code_hash": "sha256:test-code",
    }


def _strategy_config() -> dict[str, object]:
    return {
        "stock_scope": "中国A股（SH/SZ）",
        "rebalance_every": 2,
        "top_n": 10,
        "weighting": "equal",
        "signal_time": "close",
        "buy_price": "open",
        "sell_price": "close",
        "buy_fee": {"rate": 0.0003, "minimum": 5},
        "sell_fee": {"rate": 0.0005, "minimum": 5},
        "slippage": 0,
        "benchmark": "000300.SH",
        "train_filter": {"st_status": 0, "suspended": False},
        "test_filter": {"st_status": 0, "suspended": False},
    }


def test_training_run_rejects_queued_to_completed_and_failed_to_running(tmp_path: Path) -> None:
    _, _, service = _setup(tmp_path)
    model = service.create_model("model_lgbm", "LightGBM排序")
    version = service.create_model_version(model["entity_id"], _model_config())
    run = service.create_training_run(model["entity_id"], version["version_id"], _model_config())
    run_id = run["run_id"]
    with pytest.raises(ValueError):
        service.transition_training_run(run_id, "completed")
    service.transition_training_run(run_id, "running")
    service.transition_training_run(run_id, "failed")
    with pytest.raises(ValueError):
        service.transition_training_run(run_id, "running")


def test_model_training_run_locks_config_and_artifact_without_running_backtest(tmp_path: Path) -> None:
    settings, database, service = _setup(tmp_path)
    model = service.create_model("model_lgbm", "LightGBM排序")
    version = service.create_model_version(model["entity_id"], _model_config())
    run = service.create_training_run(model["entity_id"], version["version_id"], _model_config())
    assert run["status"] == "queued"
    run_id = run["run_id"]
    assert service.transition_training_run(run_id, "running")["status"] == "running"
    artifact_path = settings.runtime_root / "results" / "models" / "model.bin"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"model")
    artifact = service.attach_training_artifact(run_id, artifact_path, "模型文件")
    assert artifact["display_name"] == "模型文件"
    completed = service.transition_training_run(run_id, "completed")
    assert completed["status"] == "completed"
    published = service.publish_model_version(model["entity_id"], version["version_id"])
    assert published["status"] == "published"
    assert published["dataset_version_id"] == "current"
    assert published["factor_versions"][0]["version_id"] == "v1"
    assert service.list_training_runs()[0]["config"]["code_hash"] == "sha256:test-code"
    with pytest.raises(ValueError, match="immutable"):
        service.update_model_version(model["entity_id"], version["version_id"], {"hyperparameters": {"number_of_trees": 99}})


def test_strategy_publish_copy_diff_and_backtest_draft_preserve_exact_references(tmp_path: Path) -> None:
    settings, database, service = _setup(tmp_path)
    model = service.create_model("model_lgbm", "LightGBM排序")
    version = service.create_model_version(model["entity_id"], _model_config())
    run = service.create_training_run(model["entity_id"], version["version_id"], _model_config())
    service.transition_training_run(run["run_id"], "running")
    service.transition_training_run(run["run_id"], "completed")
    service.publish_model_version(model["entity_id"], version["version_id"])
    strategy = service.create_strategy("strategy_momentum", "Momentum策略")
    draft = service.create_strategy_version(strategy["entity_id"], {**_strategy_config(), "model_entity_id": model["entity_id"], "model_version_id": version["version_id"], "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1", "direction": "positive"}]})
    assert service.publish_strategy_version(strategy["entity_id"], draft["version_id"])["status"] == "published"
    copied = service.copy_strategy_version(strategy["entity_id"], draft["version_id"])
    assert copied["status"] == "draft"
    assert copied["factor_versions"] == draft["factor_versions"]
    assert copied["model_version_id"] == version["version_id"]
    assert service.diff_strategy_versions(strategy["entity_id"], draft["version_id"], copied["version_id"])["changed"] == []
    backtest_draft = service.to_backtest_draft(strategy["entity_id"], draft["version_id"])
    assert backtest_draft["strategy_version_id"] == f"{strategy['entity_id']}:{draft['version_id']}"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0


def test_strategy_center_api_exposes_models_strategies_and_pages(tmp_path: Path) -> None:
    settings, database, service = _setup(tmp_path)
    client = TestClient(create_app(settings, database))
    assert client.get("/models").status_code == 200
    redirected = client.get("/strategies", follow_redirects=False)
    assert redirected.status_code in {301, 302, 303, 307, 308}
    assert "/models" in redirected.headers.get("location", "")
    created = client.post("/api/models", json={"entity_id": "model_lgbm", "name": "LightGBM排序"})
    assert created.status_code == 201, created.text
    version = client.post("/api/models/model_lgbm/versions", json=_model_config())
    assert version.status_code == 201
    assert client.get("/api/models").json()["items"][0]["name"] == "LightGBM排序"
    assert client.get("/api/models/model_lgbm/versions/v1").status_code == 200


def test_strategy_center_api_registers_training_state_and_strategy_draft_without_backtest(tmp_path: Path) -> None:
    settings, database, service = _setup(tmp_path)
    client = TestClient(create_app(settings, database))
    assert client.post("/api/models", json={"entity_id": "model_lgbm", "name": "LightGBM排序"}).status_code == 201
    assert client.post("/api/models/model_lgbm/versions", json=_model_config()).status_code == 201
    run = client.post("/api/models/model_lgbm/versions/v1/training-runs", json={"config": _model_config()})
    assert run.status_code == 201
    run_id = run.json()["run_id"]
    assert client.post(f"/api/models/runs/{run_id}/status", json={"status": "running"}).json()["status"] == "running"
    assert client.post(f"/api/models/runs/{run_id}/status", json={"status": "completed"}).json()["status"] == "completed"
    assert client.post("/api/models/model_lgbm/versions/v1/publish").status_code == 200
    assert client.post("/api/strategies", json={"entity_id": "strategy_momentum", "name": "Momentum策略"}).status_code == 201
    strategy_config = {**_strategy_config(), "model_entity_id": "model_lgbm", "model_version_id": "v1", "dataset_id": "ds_canonical_market", "dataset_version_id": "current", "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1"}]}
    created = client.post("/api/strategies/strategy_momentum/versions", json=strategy_config)
    assert created.status_code == 201, created.text
    assert client.post("/api/strategies/strategy_momentum/versions/v1/copy").status_code == 201
    draft = client.post("/api/strategies/strategy_momentum/versions/v1/backtest-draft")
    assert draft.status_code == 200
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0
