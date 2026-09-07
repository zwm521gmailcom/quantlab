from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.result_archive import ResultArchiveService


def setup_archive(tmp_path: Path) -> tuple[Settings, Database]:
    data = tmp_path / "data"
    data.mkdir()
    settings = Settings(
        project_root=tmp_path,
        data_root=data,
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO strategies(entity_id,name,status) VALUES ('strategy','Alpha策略','published')")
        connection.execute("INSERT INTO strategy_versions(entity_id,version_id,status,quality_status) VALUES ('strategy','v1','published','passed')")
        rows = [
            ("20260902-120000-0001", "Alpha动量", "completed", '{"return":0.25,"annual_return":0.22,"sharpe":1.4,"max_drawdown":-0.1,"win_rate":0.61,"benchmark_return":0.12}'),
            ("20260902-120001-0002", "Beta价值", "failed", "{}"),
            ("20260902-120002-0003", "Alpha动量", "running", "{}"),
        ]
        for run_id, name, status, metrics in rows:
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
                (run_id, f"2026-09-02T12:00:0{run_id[-4]}.000000+00:00", None if status == "running" else "2026-09-02T12:01:00+00:00"),
            )
            connection.execute(
                "INSERT INTO backtest_runs(run_id,status,strategy_entity_id,strategy_version_id,config_json,metrics_json,error_message) VALUES (?, 'queued', 'strategy', 'v1', ?, ?, ?)",
                (run_id, json.dumps({"name": name, "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"}, "strategy_entity_id": "strategy"}), metrics, "失败原因" if status == "failed" else None),
            )
            if status != "queued":
                connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
                if status != "running":
                    connection.execute("UPDATE backtest_runs SET status=? WHERE run_id=?", (status, run_id))
    artifact_path = settings.runtime_root / "results" / rows[0][0] / "metrics.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text('{"return":0.25}', encoding="utf-8")
    ArtifactRepository(settings, database).register(
        run_id=rows[0][0], path=artifact_path, display_name="回测指标", artifact_role="metrics"
    )
    return settings, database


def test_archive_lists_projected_metrics_and_marks_missing_as_un_generated(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    result = ResultArchiveService(settings, database).list(page=1, page_size=2)
    assert result["total"] == 3
    assert [item["run_id"] for item in result["items"]] == ["20260902-120002-0003", "20260902-120001-0002"]
    assert result["items"][0]["metrics"]["return"]["display"] == "未生成"
    assert result["items"][1]["status"] == "failed"
    assert result["items"][1]["retryable"] is True
    assert result["items"][1]["metrics"]["sharpe"]["display"] == "未生成"
    assert "excess_return" in result["items"][1]["metrics"]


def test_archive_filters_status_strategy_name_date_and_sorts_without_detail_scan(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    service = ResultArchiveService(settings, database)
    result = service.list(status="completed", strategy="strategy", query="Alpha", sort="return", order="desc")
    assert result["total"] == 1
    assert result["items"][0]["name"] == "Alpha动量"
    assert result["items"][0]["detail_url"] == "/backtests/runs/20260902-120000-0001"


def test_archive_hides_default_strategy_suffix(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    with database.transaction() as connection:
        connection.execute("UPDATE strategies SET name=? WHERE entity_id=?", ("树模型（LightGBM） · 默认策略", "strategy"))
    item = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert item is not None
    assert item["strategy"]["name"] == "树模型（LightGBM）"
    listed = ResultArchiveService(settings, database).list(page=1, page_size=10)
    assert listed["items"][-1]["strategy"]["name"] == "树模型（LightGBM）"


def test_archive_uses_config_factor_fields_when_strategy_has_none(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Alpha动量",
                        "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"},
                        "strategy_entity_id": "strategy",
                        "factor_versions": [{"factor_id": "factor_momentum_5", "field": "momentum_5", "version_id": "v1"}],
                    }
                ),
                "20260902-120000-0001",
            ),
        )
    record = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert record is not None
    assert record["factors"] == ["momentum_5"]


def test_archive_api_page_detail_copy_and_csv_are_explicit(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    client = TestClient(create_app(settings, database))
    listing = client.get("/api/backtests/runs?page=1&page_size=10&status=completed")
    assert listing.status_code == 200
    detail = client.get("/api/backtests/runs/20260902-120000-0001")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["config"]["name"] == "Alpha动量"
    assert detail.json()["artifacts"][0]["display_name"] == "回测指标"
    copied = client.post("/api/backtests/runs/20260902-120000-0001/copy-config")
    assert copied.status_code == 201
    assert copied.json()["redirect_url"].startswith("/backtests/new?draft_id=")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 3
    exported = client.get("/api/backtests/runs.csv?status=completed")
    assert exported.status_code == 200
    assert "回测名称" in exported.text
    assert "夏普" in exported.text
    assert "Rank IC" in exported.text.splitlines()[0]
    assert "NDCG@10" in exported.text.splitlines()[0]
    assert "模型" in exported.text.splitlines()[0]
    assert "策略" not in exported.text.splitlines()[0]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM system_audit_logs WHERE action='backtest_result_export'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1


def test_copy_rule_config_redirects_to_rules_page(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Wiki趋势",
                        "kind": "rule_signal",
                        "rule_strategy_id": "wiki_trend_follow",
                        "test": {"date_from": "2018-01-01", "date_to": "2026-09-07"},
                        "strategy_entity_id": None,
                        "strategy_version_id": None,
                        "factor_versions": [],
                    }
                ),
                run_id,
            ),
        )
    copied = TestClient(create_app(settings, database)).post(f"/api/backtests/runs/{run_id}/copy-config")
    assert copied.status_code == 201
    assert copied.json()["redirect_url"].startswith("/backtests/rules?draft_id=")
    assert copied.json()["config"]["kind"] == "rule_signal"
    assert "/backtests/new?" not in copied.json()["redirect_url"]


def test_archive_page_is_available(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    response = TestClient(create_app(settings, database)).get("/backtests/runs")
    assert response.status_code == 200
    assert "结果档案" in response.text
    assert "重新回测" in response.text
    assert "删除" in response.text


def test_archive_delete_removes_run_rows_artifacts_and_result_files(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    kept_id = "20260902-120001-0002"
    result_dir = settings.runtime_root / "results" / run_id
    kept_dir = settings.runtime_root / "results" / kept_id
    kept_dir.mkdir(parents=True)
    (kept_dir / "keep.json").write_text("{}", encoding="utf-8")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO backtest_steps(run_id, ordinal, step_name, status) VALUES (?, 1, 'metrics', 'completed')",
            (run_id,),
        )
    service = ResultArchiveService(settings, database)

    deleted = service.delete(run_id)

    assert deleted["run_id"] == run_id
    assert service.get(run_id) is None
    assert service.get(kept_id) is not None
    assert not result_dir.exists()
    assert (kept_dir / "keep.json").is_file()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM run_registry WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_steps WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM system_audit_logs WHERE action='backtest_result_delete'").fetchone()[0] == 1


def test_archive_delete_missing_run_raises(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    with pytest.raises(ValueError, match="找不到这条回测"):
        ResultArchiveService(settings, database).delete("19990101-000000-0001")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 3


def test_archive_api_delete_is_explicit(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    client = TestClient(create_app(settings, database))
    listing = client.get("/api/backtests/runs?page=1&page_size=10")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["delete_url"].endswith("/api/backtests/runs/20260902-120002-0003")

    missing = client.delete("/api/backtests/runs/19990101-000000-0001")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "BACKTEST_NOT_FOUND"

    deleted = client.delete("/api/backtests/runs/20260902-120000-0001")
    assert deleted.status_code == 204
    assert client.get("/api/backtests/runs/20260902-120000-0001").status_code == 404
    remaining = client.get("/api/backtests/runs?page=1&page_size=10")
    assert remaining.status_code == 200
    assert remaining.json()["total"] == 2
    assert (settings.runtime_root / "results" / "20260902-120000-0001").exists() is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 2
