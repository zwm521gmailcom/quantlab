from __future__ import annotations

import sqlite3

from quantlab.repositories.database import Database
from quantlab.services.overview import OverviewService


def _database(tmp_path):
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    return database


def test_empty_database_returns_zero_counts_and_empty_collections(tmp_path) -> None:
    overview = OverviewService(_database(tmp_path)).get_overview()

    assert overview == {
        "summary": {"dataset_count": 0, "published_factor_count": 0, "model_count": 0, "backtest_count": 0, "strategy_count": 0},
        "run_status_counts": {},
        "recent_runs": [],
        "quality_alerts": [],
    }


def test_overview_projects_mixed_runs_with_type_names_statuses_and_detail_urls(tmp_path) -> None:
    database = _database(tmp_path)
    with database.transaction() as connection:
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('f1', '动量', 'published')")
        connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES ('s1', '多因子', 'published')")
        connection.execute("INSERT INTO models(entity_id, name, status) VALUES ('m1', '排序模型', 'published')")
        connection.execute("INSERT INTO model_versions(entity_id, version_id, status) VALUES ('m1', 'v1', 'published')")
        connection.execute("INSERT INTO strategy_versions(entity_id, version_id, status) VALUES ('s1', 'v1', 'published')")
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('d1', '行情', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status, quality_status) "
            "VALUES ('d1', 'v1', '/tmp/d1', 'published', 'warning')"
        )
        rows = [
            ("20260902-120000-0001", "research", "completed", "2026-09-02T12:00:00Z", "2026-09-02T12:01:00Z"),
            ("20260902-120001-0002", "model_training", "running", "2026-09-02T12:00:01Z", None),
            ("20260902-120002-0003", "backtest", "failed", "2026-09-02T12:00:02Z", "2026-09-02T12:03:00Z"),
        ]
        for run_id, run_type, _status, created_at, finished_at in rows:
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, ?, ?, ?)",
                (run_id, run_type, created_at, finished_at),
            )
        connection.execute("INSERT INTO research_runs(run_id, status) VALUES ('20260902-120000-0001', 'queued')")
        connection.execute("UPDATE research_runs SET status='running' WHERE run_id='20260902-120000-0001'")
        connection.execute("UPDATE research_runs SET status='completed' WHERE run_id='20260902-120000-0001'")
        connection.execute(
            "INSERT INTO model_training_runs(run_id, status, model_entity_id, model_version_id) "
            "VALUES ('20260902-120001-0002', 'queued', 'm1', 'v1')"
        )
        connection.execute("UPDATE model_training_runs SET status='running' WHERE run_id='20260902-120001-0002'")
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status, strategy_entity_id, strategy_version_id, config_json) "
            "VALUES ('20260902-120002-0003', 'queued', 's1', 'v1', ?)",
            ('{"name":"衔接测试回测"}',),
        )
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id='20260902-120002-0003'")
        connection.execute("UPDATE backtest_runs SET status='failed' WHERE run_id='20260902-120002-0003'")

    result = OverviewService(database).get_overview()

    assert result["summary"] == {"dataset_count": 1, "published_factor_count": 1, "model_count": 1, "backtest_count": 1, "strategy_count": 1}
    assert result["run_status_counts"] == {"completed": 1, "running": 1, "failed": 1}
    assert [run["run_type"] for run in result["recent_runs"]] == ["backtest", "model_training", "research"]
    assert result["recent_runs"] == [
        {
            "run_type": "backtest",
            "run_id": "20260902-120002-0003",
            "name": "衔接测试回测",
            "status": "failed",
            "created_at": "2026-09-02T12:00:02Z",
            "finished_at": "2026-09-02T12:03:00Z",
            "detail_url": "/backtests/runs/20260902-120002-0003",
        },
        {
            "run_type": "model_training",
            "run_id": "20260902-120001-0002",
            "name": "排序模型",
            "status": "running",
            "created_at": "2026-09-02T12:00:01Z",
            "finished_at": None,
            "detail_url": "/models/runs/20260902-120001-0002",
        },
        {
            "run_type": "research",
            "run_id": "20260902-120000-0001",
            "name": "研究运行",
            "status": "completed",
            "created_at": "2026-09-02T12:00:00Z",
            "finished_at": "2026-09-02T12:01:00Z",
            "detail_url": "/research/runs/20260902-120000-0001",
        },
    ]
    assert result["quality_alerts"] == [
        {"entity_type": "dataset_version", "entity_id": "d1", "version_id": "v1", "quality_status": "warning", "message": "质量状态为 warning"}
    ]


def test_overview_does_not_hide_sqlite_failures(tmp_path) -> None:
    database = _database(tmp_path)
    with database.connect() as connection:
        connection.execute("DROP TABLE run_registry")

    try:
        OverviewService(database).get_overview()
    except sqlite3.Error as error:
        assert "run_registry" in str(error)
    else:
        raise AssertionError("expected the database failure to remain visible")


def test_overview_returns_only_the_ten_most_recent_runs(tmp_path) -> None:
    database = _database(tmp_path)
    with database.transaction() as connection:
        for index in range(11):
            run_id = f"20260902-1200{index:02d}-{index + 1:04d}"
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type, created_at) VALUES (?, 'research', ?)",
                (run_id, f"2026-09-02T12:{index:02d}:00Z"),
            )
            connection.execute("INSERT INTO research_runs(run_id, status) VALUES (?, 'queued')", (run_id,))
            connection.execute("UPDATE research_runs SET status='running' WHERE run_id=?", (run_id,))
            connection.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (run_id,))

    runs = OverviewService(database).get_overview()["recent_runs"]

    assert len(runs) == 10
    assert [run["run_id"] for run in runs] == [
        f"20260902-1200{index:02d}-{index + 1:04d}" for index in range(10, 0, -1)
    ]
