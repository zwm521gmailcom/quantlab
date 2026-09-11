from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.machine_identity import load_machine_identity
from quantlab.services.result_archive import ResultArchiveService
from quantlab.services.result_sync import (
    backfill_missing_machine_ids,
    import_result_manifests,
    sync_result_catalog,
    write_run_manifest,
)
from quantlab.services.run_identity import RunIdentity


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )


def test_run_identity_embeds_machine_serial_prefix(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    identity = load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    runs = RunIdentity(database, settings.runtime_root)
    monkeypatch.setattr(runs, "now", lambda: __import__("datetime").datetime(2026, 9, 10, 12, 0, 0))
    run_id = runs.next_id()
    prefix = int(identity["serial_prefix"])
    serial = int(run_id.split("-")[-1])
    assert serial // 100 == prefix


def test_import_result_manifest_inserts_missing_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-3701"
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    config = {"name": "同步回测", "machine_id": "aabbccdd", "dataset_id": "ds", "dataset_version_id": "v1"}
    (folder / "metrics.json").write_text(json.dumps({"return": 0.1, "resources": {"machine_id": "aabbccdd"}}), encoding="utf-8")
    (folder / "run.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "run_id": run_id,
                "machine_id": "aabbccdd",
                "status": "completed",
                "created_at": "2026-09-10T12:00:00+00:00",
                "finished_at": "2026-09-10T12:01:00+00:00",
                "config": config,
                "dataset_id": None,
                "dataset_version_id": None,
                "strategy_entity_id": None,
                "strategy_version_id": None,
                "error_message": None,
                "steps": [],
                "artifacts": [{"role": "metrics", "name": "metrics.json", "display_name": "回测指标"}],
            }
        ),
        encoding="utf-8",
    )
    imported = import_result_manifests(settings, database)
    assert run_id in imported
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["status"] == "completed"
    assert detail["machine_id"] == "aabbccdd"
    assert detail["metrics"]["return"]["value"] == 0.1
    assert import_result_manifests(settings, database) == []


def test_write_run_manifest_round_trip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1001"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
            (run_id, "2026-09-10T12:00:00+00:00", "2026-09-10T12:01:00+00:00"),
        )
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status, config_json, metrics_json) VALUES (?, 'queued', ?, ?)",
            (run_id, json.dumps({"name": "本机回测", "machine_id": "local01"}), json.dumps({"return": 0.2})),
        )
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='completed' WHERE run_id=?", (run_id,))
    path = write_run_manifest(settings, database, run_id)
    assert path is not None and path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["machine_id"] == "local01"
    assert payload["status"] == "completed"


def _insert_completed_run(
    database: Database,
    run_id: str,
    *,
    config: dict,
    metrics: dict,
    submission_token: str | None = None,
) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
            (run_id, "2026-09-10T12:00:00+00:00", "2026-09-10T12:01:00+00:00"),
        )
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status, config_json, metrics_json, submission_token) "
            "VALUES (?, 'queued', ?, ?, ?)",
            (run_id, json.dumps(config, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False), submission_token),
        )
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='completed' WHERE run_id=?", (run_id,))


def test_backfill_stamps_local_machine_id_on_legacy_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    identity = load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1002"
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text(json.dumps({"return": 0.2}), encoding="utf-8")
    _insert_completed_run(database, run_id, config={"name": "旧回测"}, metrics={"return": 0.2})
    assert backfill_missing_machine_ids(settings, database) == 1
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["machine_id"] == identity["machine_id"]
    manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert manifest["machine_id"] == identity["machine_id"]
    assert manifest["config"]["machine_id"] == identity["machine_id"]
    disk_metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    assert disk_metrics["return"] == 0.2
    assert disk_metrics["resources"]["machine_id"] == identity["machine_id"]
    assert backfill_missing_machine_ids(settings, database) == 0


def test_backfill_does_not_overwrite_existing_machine_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1003"
    _insert_completed_run(
        database,
        run_id,
        config={"name": "他机回测", "machine_id": "aabbccdd"},
        metrics={"return": 0.1, "resources": {"machine_id": "aabbccdd"}},
    )
    assert backfill_missing_machine_ids(settings, database) == 0
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["machine_id"] == "aabbccdd"


def test_backfill_copies_machine_id_from_run_json_into_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1004"
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"schema": 1, "run_id": run_id, "machine_id": "deadbeef", "status": "completed", "config": {"name": "拷来的"}}),
        encoding="utf-8",
    )
    _insert_completed_run(database, run_id, config={"name": "拷来的"}, metrics={"return": 0.3}, submission_token=f"sync:{run_id}")
    assert backfill_missing_machine_ids(settings, database) == 1
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["machine_id"] == "deadbeef"
    local = load_machine_identity(settings.runtime_root)["machine_id"]
    assert detail["machine_id"] != local


def test_backfill_skips_imported_run_without_any_machine_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1005"
    _insert_completed_run(
        database,
        run_id,
        config={"name": "无来源"},
        metrics={"return": 0.4},
        submission_token=f"sync:{run_id}",
    )
    assert backfill_missing_machine_ids(settings, database) == 0
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["machine_id"] == ""


def test_sync_result_catalog_backfills_legacy_machine_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    identity = load_machine_identity(settings.runtime_root)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-1006"
    _insert_completed_run(database, run_id, config={"name": "启动补齐"}, metrics={})
    sync_result_catalog(settings, database)
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail is not None
    assert detail["machine_id"] == identity["machine_id"]


def _write_completed_manifest(settings: Settings, run_id: str, *, machine_id: str = "aabbccdd") -> Path:
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    config = {"name": "同步回测", "machine_id": machine_id}
    (folder / "metrics.json").write_text(json.dumps({"return": 0.1, "resources": {"machine_id": machine_id}}), encoding="utf-8")
    (folder / "run.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "run_id": run_id,
                "machine_id": machine_id,
                "status": "completed",
                "created_at": "2026-09-10T12:00:00+00:00",
                "finished_at": "2026-09-10T12:01:00+00:00",
                "config": config,
                "steps": [],
                "artifacts": [{"role": "metrics", "name": "metrics.json", "display_name": "回测指标"}],
            }
        ),
        encoding="utf-8",
    )
    return folder


def test_sync_result_catalog_attaches_imported_runs_to_pending_plan_items(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-8808"
    _write_completed_manifest(settings, run_id, machine_id="d23135c5")
    payload = json.loads((settings.runtime_root / "results" / run_id / "run.json").read_text(encoding="utf-8"))
    payload["config"]["name"] = "2026 · 价格60加低估值 · Top4"
    (settings.runtime_root / "results" / run_id / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    stamp = "2026-09-10T12:00:00+00:00"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO backtest_plans(plan_id, name, status, closed, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("plan-test", "96笔扫描", "stopped", 0, stamp, stamp),
        )
        connection.execute(
            "INSERT INTO backtest_plan_items("
            "item_id, plan_id, sort_order, selected, name, config_json, status, "
            "run_id, error_message, started_at, finished_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "item-pending",
                "plan-test",
                1,
                1,
                "2026 · 价格60加低估值 · Top4",
                json.dumps({"name": "2026 · 价格60加低估值 · Top4", "top_n": 4}),
                "pending",
                None,
                None,
                None,
                None,
                stamp,
                stamp,
            ),
        )
    imported = sync_result_catalog(settings, database)
    assert run_id in imported
    with database.connect() as connection:
        item = connection.execute(
            "SELECT status, run_id FROM backtest_plan_items WHERE item_id='item-pending'"
        ).fetchone()
        plan = connection.execute("SELECT status FROM backtest_plans WHERE plan_id='plan-test'").fetchone()
    assert item["status"] == "completed"
    assert item["run_id"] == run_id
    assert plan["status"] == "completed"
    assert sync_result_catalog(settings, database) == []


def test_delete_writes_tombstone_and_blocks_reimport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-2001"
    _insert_completed_run(database, run_id, config={"name": "待删"}, metrics={"return": 0.1})
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    ResultArchiveService(settings, database).delete(run_id)
    marker = settings.runtime_root / "results" / "_deleted" / f"{run_id}.json"
    assert marker.is_file()
    assert not folder.exists()
    _write_completed_manifest(settings, run_id)
    assert import_result_manifests(settings, database) == []
    assert ResultArchiveService(settings, database).get(run_id) is None
    assert marker.is_file()


def test_sync_applies_peer_tombstone_and_removes_local_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-2002"
    folder = _write_completed_manifest(settings, run_id)
    _insert_completed_run(database, run_id, config={"name": "对端已删"}, metrics={"return": 0.2})
    marker = settings.runtime_root / "results" / "_deleted" / f"{run_id}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"schema": 1, "run_id": run_id, "machine_id": "deadbeef", "deleted_at": "2026-09-10T12:02:00+00:00"}),
        encoding="utf-8",
    )
    sync_result_catalog(settings, database)
    assert ResultArchiveService(settings, database).get(run_id) is None
    assert not folder.exists()
    assert marker.is_file()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()[0] == 0


def test_plan_snapshot_is_imported_into_empty_database(tmp_path: Path) -> None:
    from quantlab.services.result_sync import PLANS_DIR_NAME, sync_result_catalog

    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    run_id = "20260910-120000-9909"
    _write_completed_manifest(settings, run_id, machine_id="d23135c5")
    plan_id = "plan-syncedplan01"
    folder = settings.runtime_root / "results" / PLANS_DIR_NAME
    folder.mkdir(parents=True)
    (folder / f"{plan_id}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "plan_id": plan_id,
                "name": "对端计划",
                "status": "completed",
                "closed": False,
                "created_at": "2026-09-10T12:00:00+00:00",
                "updated_at": "2026-09-10T12:01:00+00:00",
                "items": [
                    {
                        "item_id": "item-synceditem1",
                        "sort_order": 1,
                        "selected": True,
                        "name": "同步回测",
                        "config": {"name": "同步回测", "top_n": 4},
                        "status": "completed",
                        "run_id": run_id,
                        "error_message": None,
                        "started_at": "2026-09-10T12:00:00+00:00",
                        "finished_at": "2026-09-10T12:01:00+00:00",
                        "created_at": "2026-09-10T12:00:00+00:00",
                        "updated_at": "2026-09-10T12:01:00+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sync_result_catalog(settings, database)
    with database.connect() as connection:
        plan = connection.execute("SELECT name, status FROM backtest_plans WHERE plan_id=?", (plan_id,)).fetchone()
        item = connection.execute(
            "SELECT name, status, run_id FROM backtest_plan_items WHERE plan_id=?", (plan_id,)
        ).fetchone()
    assert plan is not None
    assert plan["name"] == "对端计划"
    assert plan["status"] == "completed"
    assert item["name"] == "同步回测"
    assert item["status"] == "completed"
    assert item["run_id"] == run_id


def test_creating_a_plan_writes_snapshot_file(tmp_path: Path) -> None:
    from quantlab.api.app import create_app
    from quantlab.services.result_sync import PLANS_DIR_NAME
    from tests.quantlab.test_backtest_workbench import config, setup_env

    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post(
        "/api/backtest-plans",
        json={"name": "本机计划", "items": [{"name": "一笔", "config": config("a")}]},
    )
    assert created.status_code == 201
    plan_id = created.json()["plan_id"]
    path = settings.runtime_root / "results" / PLANS_DIR_NAME / f"{plan_id}.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "本机计划"
    assert payload["items"][0]["name"] == "一笔"


def test_list_plans_imports_snapshot_written_after_startup(tmp_path: Path) -> None:
    from quantlab.api.app import create_app
    from quantlab.services.result_sync import PLANS_DIR_NAME
    from tests.quantlab.test_backtest_workbench import setup_env

    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    assert client.get("/api/backtest-plans").json()["items"] == []
    plan_id = "plan-lateimport01"
    folder = settings.runtime_root / "results" / PLANS_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{plan_id}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "plan_id": plan_id,
                "name": "同步后出现",
                "status": "completed",
                "closed": False,
                "created_at": "2026-09-11T04:00:00+00:00",
                "updated_at": "2026-09-11T04:00:00+00:00",
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    names = [plan["name"] for plan in client.get("/api/backtest-plans").json()["items"]]
    assert "同步后出现" in names
