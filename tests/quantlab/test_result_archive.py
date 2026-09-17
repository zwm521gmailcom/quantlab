from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.result_archive import ResultArchiveService, step_label


def test_step_label_uses_factor_rank_copy():
    assert step_label("model_training") == "模型训练"
    assert step_label("prediction") == "预测打分"
    assert step_label("model_training", kind="factor_rank") == "准备数据"
    assert step_label("prediction", kind="factor_rank") == "因子排序"


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


def test_archive_detail_exposes_resources_without_using_them_as_return(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "return": 0.25,
                        "resources": {
                            "peak_rss_bytes": 3 * 1024**3,
                            "fold_workers": 2,
                            "bucket_workers": 1,
                            "signature": "abc",
                        },
                    }
                ),
                run_id,
            ),
        )
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail["metrics"]["return"]["value"] == 0.25
    assert detail["resources"]["fold_workers"] == 2
    listing = ResultArchiveService(settings, database).list()
    assert all("resources" not in item for item in listing["items"])


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
    assert result["results_root"] == "runtime/results"
    assert not Path(result["results_root"]).is_absolute()


def test_archive_filters_status_strategy_name_date_and_sorts_without_detail_scan(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    service = ResultArchiveService(settings, database)
    result = service.list(status="completed", strategy="strategy", query="Alpha", sort="return", order="desc")
    assert result["total"] == 1
    assert result["items"][0]["name"] == "Alpha动量"
    assert result["items"][0]["detail_url"] == "/backtests/runs/20260902-120000-0001"


def test_archive_header_sorts_metrics_and_puts_missing_last(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "return": 0.05,
                        "annual_return": 0.04,
                        "sharpe": 2.1,
                        "max_drawdown": -0.4,
                        "win_rate": 0.4,
                    }
                ),
                "20260902-120001-0002",
            ),
        )
    service = ResultArchiveService(settings, database)
    by_return = service.list(sort="return", order="desc")
    assert [item["run_id"] for item in by_return["items"]] == [
        "20260902-120000-0001",
        "20260902-120001-0002",
        "20260902-120002-0003",
    ]
    by_sharpe = service.list(sort="sharpe", order="asc")
    assert [item["run_id"] for item in by_sharpe["items"]] == [
        "20260902-120000-0001",
        "20260902-120001-0002",
        "20260902-120002-0003",
    ]
    by_drawdown = service.list(sort="max_drawdown", order="asc")
    assert [item["run_id"] for item in by_drawdown["items"][:2]] == [
        "20260902-120001-0002",
        "20260902-120000-0001",
    ]
    by_created = service.list(sort="created_at", order="asc")
    assert [item["run_id"] for item in by_created["items"]] == [
        "20260902-120000-0001",
        "20260902-120001-0002",
        "20260902-120002-0003",
    ]
    with pytest.raises(ValueError, match="invalid sort"):
        service.list(sort="unknown", order="desc")


def test_archive_prefers_model_center_name(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO models(entity_id, name, status) VALUES ('model_factor_rank', '单因子排名', 'published')"
        )
        connection.execute(
            "UPDATE strategies SET name=? WHERE entity_id=?",
            ("单因子排名 · 默认策略", "strategy"),
        )
        connection.execute(
            "UPDATE backtest_runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Alpha动量",
                        "kind": "factor_rank",
                        "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"},
                        "strategy_entity_id": "strategy",
                        "model": {"entity_id": "model_factor_rank", "version_id": "v1"},
                    }
                ),
                "20260902-120000-0001",
            ),
        )
    item = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert item is not None
    assert item["strategy"]["name"] == "单因子排名"
    assert item["config"]["model"]["name"] == "单因子排名"
    with database.transaction() as connection:
        connection.execute("UPDATE models SET name=? WHERE entity_id=?", ("自定义名称", "model_factor_rank"))
    renamed = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert renamed is not None
    assert renamed["strategy"]["name"] == "自定义名称"


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
    listing = ResultArchiveService(settings, database).list(page=1, page_size=10, query="momentum_5")
    assert listing["items"][0]["factors"] == ["momentum_5"]


def test_archive_list_skips_catalog_sync_and_benchmark_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, database = setup_archive(tmp_path)
    monkeypatch.setattr(
        "quantlab.services.result_archive.fill_benchmark_metrics",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("list must not rebuild benchmark metrics")),
    )
    folder = settings.runtime_root / "results" / "20260902-129999-0009"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps(
            {
                "run_id": "20260902-129999-0009",
                "status": "completed",
                "created_at": "2026-09-02T12:09:00+00:00",
                "config": {"name": "磁盘未入库"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "return": 0.25,
                        "equity_curve": [{"date": f"2020{index:04d}", "equity": 1.0} for index in range(2000)],
                    }
                ),
                "20260902-120000-0001",
            ),
        )
    result = ResultArchiveService(settings, database).list(page=1, page_size=10)
    assert [item["run_id"] for item in result["items"]] == [
        "20260902-120002-0003",
        "20260902-120001-0002",
        "20260902-120000-0001",
    ]
    assert "20260902-129999-0009" not in [item["run_id"] for item in result["items"]]
    completed = next(item for item in result["items"] if item["run_id"] == "20260902-120000-0001")
    assert completed["metrics"]["return"]["value"] == 0.25
    assert "equity_curve" not in completed
    assert "metrics_raw" not in completed


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


def test_copy_config_keeps_benchmark_open_gate_and_roll_periods(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Alpha动量",
                        "kind": "factor_rank",
                        "open_when_benchmark_gt_ma200": True,
                        "train_period_months": 1,
                        "test_period_months": 1,
                        "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"},
                        "strategy_entity_id": "strategy",
                        "factor_versions": [],
                    }
                ),
                run_id,
            ),
        )
    copied = TestClient(create_app(settings, database)).post(f"/api/backtests/runs/{run_id}/copy-config")
    assert copied.status_code == 201
    payload = copied.json()["config"]
    assert payload["open_when_benchmark_gt_ma200"] is True
    assert payload["train_period_months"] == 1
    assert payload["test_period_months"] == 1
    draft = TestClient(create_app(settings, database)).get(f"/api/backtests/drafts/{copied.json()['draft_id']}")
    assert draft.status_code == 200
    assert draft.json()["complete"] is True
    assert draft.json()["config"]["open_when_benchmark_gt_ma200"] is True


def test_copy_config_does_not_repeat_pretrade_stock_in_train_or_test(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    shared = [
        "st_status == 0",
        "is_suspended == 0",
        "close > low",
        "close != up_limit AND close != down_limit",
    ]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Alpha动量",
                        "kind": "factor_rank",
                        "open_when_benchmark_gt_ma200": True,
                        "pretrade_filters": {"stock": shared, "benchmark": []},
                        "train": {
                            "date_from": "2019-01-02",
                            "date_to": "2019-12-31",
                            "filter": {"expressions": [*shared, "hfq_close > sma200"]},
                        },
                        "test": {
                            "date_from": "2020-01-01",
                            "date_to": "2020-12-31",
                            "filter": {"expressions": [*shared, "st_status==0 & close>low"]},
                        },
                        "strategy_entity_id": "strategy",
                        "factor_versions": [],
                    }
                ),
                run_id,
            ),
        )
    copied = TestClient(create_app(settings, database)).post(f"/api/backtests/runs/{run_id}/copy-config")
    assert copied.status_code == 201
    payload = copied.json()["config"]
    assert payload["pretrade_filters"]["stock"] == shared
    assert payload["train"]["filter"]["expressions"] == ["hfq_close > sma200"]
    assert payload["test"]["filter"]["expressions"] == ["st_status==0 & close>low"]
    draft = TestClient(create_app(settings, database)).get(f"/api/backtests/drafts/{copied.json()['draft_id']}")
    assert draft.json()["config"]["train"]["filter"]["expressions"] == ["hfq_close > sma200"]
    assert draft.json()["config"]["test"]["filter"]["expressions"] == ["st_status==0 & close>low"]


def test_archive_page_is_available(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    response = TestClient(create_app(settings, database)).get("/backtests/runs")
    assert response.status_code == 200
    html = response.text
    js = Path("quantlab/web/assets/backtest/archive.js").read_text(encoding="utf-8")
    source = html + js
    assert "结果档案" in html
    assert "重新回测" in source
    assert "删除" in source
    assert "<label>排序" not in html
    assert 'id="sort"' not in html
    assert "archive-sortable" in js
    assert "function setSort(key)" in js


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
    marker = settings.runtime_root / "results" / "_deleted" / f"{run_id}.json"
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["machine_id"]
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
    assert (settings.runtime_root / "results" / "_deleted" / "20260902-120000-0001.json").is_file()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 2
