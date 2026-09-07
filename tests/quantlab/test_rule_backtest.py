"""Rule-signal backtest dispatch: validate without model, missing membership, empty signals."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.services.backtest_workbench import BacktestWorkbenchService
from quantlab.strategies.registry import RULE_STRATEGIES

from test_backtest_workbench import setup_env


def _enrich_canonical(path: Path) -> None:
    table = pq.read_table(path)
    n = table.num_rows
    extras = {
        "list_date": pa.array(["20100101"] * n),
        "amount": pa.array([80_000.0] * n),
        "hfq_high": table["hfq_close"],
        "hfq_low": table["hfq_open"],
        "high": table["close"],
        "low": table["open"],
    }
    for name, column in extras.items():
        if name not in table.column_names:
            table = table.append_column(name, column)
    pq.write_table(table, path)


def setup_rule_env(tmp_path: Path):
    settings, database = setup_env(tmp_path)
    _enrich_canonical(settings.data_root / "features.parquet")
    return settings, database


def rule_config(token: str = "rule-tok", **overrides) -> dict:
    payload = {
        "submission_token": token,
        "name": "规则策略回测",
        "kind": "rule_signal",
        "dataset_id": "ds",
        "dataset_version_id": "v1",
        "test": {
            "date_from": "2020-01-02",
            "date_to": "2020-01-04",
        },
    }
    payload.update(overrides)
    return payload


def _write_index_weight(raw_root: Path, *, trade_date: str = "20191231") -> None:
    weight_dir = raw_root / "index_weight"
    weight_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "600000.SH"],
                "trade_date": [trade_date, trade_date],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH"],
                "con_code": ["000001.SZ"],
                "trade_date": [trade_date],
                "weight": [0.4],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )


def _error_text(payload: dict) -> str:
    return str(payload.get("error_message") or payload.get("error") or "")


def test_validate_rule_signal_does_not_need_model(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    svc = BacktestWorkbenchService(settings, database)
    raw = rule_config()
    assert "model" not in raw
    assert "strategy_entity_id" not in raw
    assert "factor_versions" not in raw

    out = svc.validate(raw)

    assert out["kind"] == "rule_signal"
    assert out["rule_strategy_id"] == "wiki_trend_follow"
    assert out["code_hash"].startswith("sha256:")
    assert out["code_hash"] == RULE_STRATEGIES["wiki_trend_follow"]["source_hash"]
    assert out["account_mode"] == "target_weight_exits"
    assert out["rebalance_every"] == 5
    assert out["stop_loss"] == pytest.approx(0.10)
    assert out["take_profit"] == pytest.approx(0.25)
    assert out["max_hold_days"] == 45
    assert out["sell_fee_rate"] == pytest.approx(0.0005)
    assert out["stamp_tax_rate"] == pytest.approx(0.001)
    assert "model" not in out or not out.get("model")
    assert not out.get("factor_versions")
    assert not out.get("strategy_entity_id")


def _write_hs300_weight_only(raw_root: Path, *, trade_date: str = "20191231") -> None:
    weight_dir = raw_root / "index_weight"
    weight_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "600000.SH"],
                "trade_date": [trade_date, trade_date],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )


def test_execute_fails_when_only_hs300_weight_present(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    _write_hs300_weight_only(settings.raw_root)
    client = TestClient(create_app(settings, database))

    created = client.post("/api/backtests", json=rule_config("only-hs300-weight"))
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "failed"
    assert "成分" in _error_text(body)

    stored = client.get(f"/api/backtests/{run_id}")
    assert stored.status_code == 200
    assert stored.json()["status"] == "failed"
    assert "成分" in _error_text(stored.json())


def test_execute_fails_when_index_weight_missing(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    assert not (settings.raw_root / "index_weight").exists()
    client = TestClient(create_app(settings, database))

    created = client.post("/api/backtests", json=rule_config("missing-weight"))
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "failed"
    assert "成分" in _error_text(body)

    stored = client.get(f"/api/backtests/{run_id}")
    assert stored.status_code == 200
    assert stored.json()["status"] == "failed"
    assert "成分" in _error_text(stored.json())


def test_execute_completes_with_zero_trades_when_no_signals(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    _write_index_weight(settings.raw_root)
    client = TestClient(create_app(settings, database))

    created = client.post("/api/backtests", json=rule_config("never-signals"))
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "completed"
    assert body["trades"] == []
    metrics = body["metrics"]
    assert metrics["signal_rows"] == 0
    assert "bullish_days" not in metrics
    assert metrics["membership_asof"] == "monthly"
    curve = metrics["equity_curve"]
    assert curve
    assert metrics["flat_days"] == len(curve)
    for row in curve:
        assert row["invested"] == pytest.approx(0.0)
        assert row["equity"] == pytest.approx(row["cash"])
        assert row["cash"] > 0
    assert not _error_text(body)


def test_execute_fails_when_latest_snapshot_empty(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    _write_index_weight(settings.raw_root)
    weight_dir = settings.raw_root / "index_weight"
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH"],
                "con_code": [""],
                "trade_date": ["20240131"],
                "weight": [0.4],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    client = TestClient(create_app(settings, database))
    created = client.post("/api/backtests", json=rule_config("empty-latest"))
    assert created.status_code == 201, created.text
    executed = client.post(f"/api/backtests/{created.json()['run_id']}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "failed"
    assert "成分" in _error_text(body)


def test_execute_fails_when_window_starts_before_membership(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    _write_index_weight(settings.raw_root, trade_date="20240131")
    client = TestClient(create_app(settings, database))
    created = client.post("/api/backtests", json=rule_config("before-weight"))
    assert created.status_code == 201, created.text
    executed = client.post(f"/api/backtests/{created.json()['run_id']}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "failed"
    assert "成分" in _error_text(body)


def test_execute_accepts_ts_code_trade_date_market(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    path = settings.data_root / "features.parquet"
    table = pq.read_table(path)
    renamed = [
        {"date": "trade_date", "instrument": "ts_code"}.get(name, name)
        for name in table.column_names
    ]
    pq.write_table(table.rename_columns(renamed), path)
    _write_index_weight(settings.raw_root)
    client = TestClient(create_app(settings, database))

    created = client.post("/api/backtests", json=rule_config("ts-code-market"))
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "completed", _error_text(body)
    assert "instrument" not in _error_text(body).lower()
    assert not _error_text(body)


def test_rules_page_is_available(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    response = TestClient(create_app(settings, database)).get("/backtests/rules")
    assert response.status_code == 200
    assert "规则回测" in response.text
    assert "Wiki 多指标趋势跟踪" in response.text
    assert "下一交易日开盘" in response.text
    assert "当时" in response.text
    assert 'id="signal-source"' not in response.text


def test_save_draft_accepts_rule_signal_without_model(tmp_path):
    settings, database = setup_rule_env(tmp_path)
    svc = BacktestWorkbenchService(settings, database)
    raw = rule_config("draft-rule")
    raw.update({
        "strategy_entity_id": None,
        "strategy_version_id": None,
        "factor_versions": [],
        "model": {},
        "rule_strategy_id": "wiki_trend_follow",
    })
    saved = svc.save_draft(raw)
    assert saved["draft_id"]
    assert saved["config"]["kind"] == "rule_signal"
    assert saved["config"]["strategy_entity_id"] is None
    assert saved["config"]["factor_versions"] == []

