from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.services.catalog import snapshot_authoritative_data
from quantlab.cli import main

from test_strategy_center import _model_config, _setup, _strategy_config


def test_quantlab_core_pages_and_api_contracts_are_connected(tmp_path: Path) -> None:
    settings, database, strategy_service = _setup(tmp_path)
    client = TestClient(create_app(settings, database))

    for page, marker in (
        ("/factors", "因子数据"),
        ("/factors/factor_momentum_5/versions/v1", "因子数据"),
        ("/research/factors/manual", "手动建立因子"),
        ("/research/factors/auto", "自动挖掘"),
        ("/research/factor-jobs", "因子计算任务"),
        ("/models", "模型中心"),
        ("/backtests/new", "回测中心"),
        ("/backtests/runs", "结果档案"),
        ("/settings", "设置"),
    ):
        response = client.get(page)
        assert response.status_code == 200, (page, response.text)
        assert marker in response.text

    gone = client.get("/strategies", follow_redirects=False)
    assert gone.status_code in {301, 302, 303, 307, 308}
    assert "/models" in gone.headers.get("location", "")

    assert client.get("/api/factors").status_code == 200
    assert client.get("/api/research-runs").status_code == 200
    assert client.get("/api/strategies").status_code == 200
    assert client.get("/api/backtests/runs").status_code == 200
    assert client.get("/api/settings").json()["secrets"]["tushare_token"] is False

    model = strategy_service.create_model("model_lgbm", "LightGBM排序")
    version = strategy_service.create_model_version(model["entity_id"], _model_config())
    training = strategy_service.create_training_run(model["entity_id"], version["version_id"], _model_config())
    strategy_service.transition_training_run(training["run_id"], "running")
    strategy_service.transition_training_run(training["run_id"], "completed")
    strategy_service.publish_model_version(model["entity_id"], version["version_id"])
    strategy = strategy_service.create_strategy("strategy_momentum", "Momentum策略")
    strategy_version = strategy_service.create_strategy_version(
        strategy["entity_id"],
        {**_strategy_config(), "dataset_id": "ds_canonical_market", "dataset_version_id": "current",
         "model_entity_id": model["entity_id"], "model_version_id": version["version_id"],
         "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1"}]},
    )
    strategy_service.publish_strategy_version(strategy["entity_id"], strategy_version["version_id"])
    config = {
        "name": "端到端动量验收",
        "dataset_id": "ds_canonical_market", "dataset_version_id": "current",
        "strategy_entity_id": strategy["entity_id"], "strategy_version_id": strategy_version["version_id"],
        "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1", "field": "momentum_5"}],
        "model": {"entity_id": model["entity_id"], "version_id": version["version_id"]},
        "stock_scope": "中国A股（SH/SZ）", "top_n": 10, "weighting": "equal", "rebalance_every": 2,
        "buy_price": "open", "sell_price": "close", "buy_fee_rate": 0.0003,
        "sell_fee_rate": 0.0005, "buy_fee_minimum": 5, "sell_fee_minimum": 5,
        "stamp_tax_rate": 0.001, "slippage": 0, "lot_size": 100, "initial_capital": 1_000_000,
        "unfilled_policy": "keep_cash",
        "train": {"date_from": "2023-01-01", "date_to": "2023-12-31", "filter": {"st_status": 0, "suspended": False}},
        "test": {"date_from": "2024-01-01", "date_to": "2024-03-31", "filter": {"st_status": 0, "suspended": False}},
        "submission_token": "e2e-token",
    }
    created = client.post("/api/backtests", json=config)
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    assert client.get(f"/api/backtests/runs/{run_id}").status_code == 200
    copied = client.post(f"/api/backtests/runs/{run_id}/copy-config")
    assert copied.status_code == 201
    assert copied.json()["redirect_url"].startswith("/backtests/new?draft_id=")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 1


def test_authoritative_baseline_is_created_outside_data_root(tmp_path: Path) -> None:
    settings, database, _ = _setup(tmp_path)
    baseline = snapshot_authoritative_data(settings, settings.runtime_root / "baselines" / "authoritative-data.json")
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert baseline.is_relative_to(settings.runtime_root)
    assert payload["roots"]["data"] == str(settings.data_root)
    assert not (settings.data_root / "baselines").exists()
    assert database.db_path.is_relative_to(settings.runtime_root)


def test_data_baseline_can_be_verified_without_writing_authority(tmp_path: Path) -> None:
    settings, _, _ = _setup(tmp_path)
    baseline = settings.runtime_root / "baselines" / "authoritative-data.json"
    snapshot_authoritative_data(settings, baseline)
    assert main([
        "verify-data-baseline", "--project-root", str(settings.project_root),
        "--data-root", str(settings.data_root), "--calibration-root", str(settings.calibration_root),
        "--runtime-root", str(settings.runtime_root),
    ]) == 0
