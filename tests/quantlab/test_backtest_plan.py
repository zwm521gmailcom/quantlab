from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.services.backtest_plan import BacktestPlanService

from tests.quantlab.test_backtest_workbench import config, setup_env


def _patch_slots(monkeypatch: pytest.MonkeyPatch, slots: int) -> None:
    def _slots(ram=None, cpu=None, value: int = slots) -> int:
        return value

    monkeypatch.setattr("quantlab.services.settings.max_concurrent_backtests", _slots)
    from quantlab.services.backtest_admission import set_slot_override_for_test

    set_slot_override_for_test(slots)


def _wait_plan(client: TestClient, plan_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/backtest-plans/{plan_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "stopped"}:
            return body
        time.sleep(0.05)
    raise AssertionError(body)


def test_create_plan_stores_items_without_running(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post(
        "/api/backtest-plans",
        json={
            "name": "可算因子第一批",
            "items": [
                {"name": "核心组合 · 一次训练", "config": config("a")},
                {"name": "核心组合 · 定长回看", "config": {**config("b"), "walk_forward": "rolling"}},
            ],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "可算因子第一批"
    assert body["status"] == "draft"
    assert len(body["items"]) == 2
    assert all(item["status"] == "pending" and item["selected"] is True for item in body["items"])
    assert body["items"][0]["metrics"]["return"] == {"value": None, "display": "—"}
    assert body["items"][0]["metrics"]["max_drawdown"]["display"] == "—"
    assert body["items"][0]["summary"]["model_name"] == "模型"
    assert body["items"][0]["summary"]["kind"] == "factor_rank"
    assert body["items"][0]["config"]["model"]["name"] == "模型"
    with database.transaction() as connection:
        connection.execute("UPDATE models SET name=? WHERE entity_id=?", ("单因子排名", "m"))
    renamed = client.get(f"/api/backtest-plans/{body['plan_id']}").json()
    assert renamed["items"][0]["summary"]["model_name"] == "单因子排名"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0


def test_start_runs_selected_items_serially(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_slots(monkeypatch, 1)
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan_id = client.post(
        "/api/backtest-plans",
        json={
            "name": "串行计划",
            "items": [
                {"name": "第一笔", "config": config("one")},
                {"name": "第二笔", "config": config("two")},
            ],
        },
    ).json()["plan_id"]
    started = client.post(f"/api/backtest-plans/{plan_id}/start", json={})
    assert started.status_code == 202
    assert started.json()["status"] == "running"
    finished = _wait_plan(client, plan_id)
    assert finished["status"] == "completed"
    statuses = [item["status"] for item in finished["items"]]
    assert statuses == ["completed", "completed"]
    run_ids = [item["run_id"] for item in finished["items"]]
    assert all(run_ids)
    assert run_ids[0] != run_ids[1]
    first = client.get(f"/api/backtests/{run_ids[0]}").json()
    second = client.get(f"/api/backtests/{run_ids[1]}").json()
    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert finished["items"][0]["finished_at"]
    assert finished["items"][1]["started_at"]
    assert finished["items"][0]["finished_at"] <= finished["items"][1]["started_at"]
    run_id = run_ids[0]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
            (json.dumps({"return": 0.1234, "max_drawdown": -0.0567}), run_id),
        )
    metrics = client.get(f"/api/backtest-plans/{plan_id}").json()["items"][0]["metrics"]
    assert metrics["return"]["value"] == pytest.approx(0.1234)
    assert metrics["return"]["display"] == "12.34%"
    assert metrics["max_drawdown"]["value"] == pytest.approx(-0.0567)
    assert metrics["max_drawdown"]["display"] == "-5.67%"


def test_start_runs_two_items_in_parallel_when_machine_allows_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_slots(monkeypatch, 2)
    from quantlab.services import backtest_control

    barrier = threading.Barrier(2, timeout=8)
    original = backtest_control.run_isolated

    def together(settings, job, run_id):
        from quantlab.services import backtest_admission as gate

        gate.mark_holder_settled_for_test()
        barrier.wait()
        return original(settings, job, run_id)

    monkeypatch.setattr("quantlab.services.backtest_plan.run_isolated", together)
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan_id = client.post(
        "/api/backtest-plans",
        json={
            "name": "并行计划",
            "items": [
                {"name": "第一笔", "config": config("one")},
                {"name": "第二笔", "config": config("two")},
            ],
        },
    ).json()["plan_id"]
    started = client.post(f"/api/backtest-plans/{plan_id}/start", json={})
    assert started.status_code == 202
    finished = _wait_plan(client, plan_id)
    assert finished["status"] == "completed"
    assert [item["status"] for item in finished["items"]] == ["completed", "completed"]
    started_at = [item["started_at"] for item in finished["items"]]
    finished_at = [item["finished_at"] for item in finished["items"]]
    assert all(started_at) and all(finished_at)
    assert min(finished_at) >= max(started_at)
    assert started_at[0] <= started_at[1]


def test_plan_workers_can_hold_two_slots_and_still_block_workbench(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_slots(monkeypatch, 2)
    from quantlab.services.backtest_control import (
        acquire_execution,
        mark_plan_worker,
        release_execution,
        release_reservation,
        reserve_execution,
    )

    reserve_execution("plan:slots")
    barrier = threading.Barrier(3, timeout=5)
    hold = threading.Event()
    errors: list[BaseException] = []

    def worker(label: str) -> None:
        mark_plan_worker()
        try:
            acquire_execution(label)
            barrier.wait()
            hold.wait(timeout=5)
        except BaseException as error:
            errors.append(error)
        finally:
            release_execution()

    threads = [threading.Thread(target=worker, args=(f"run:{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    with pytest.raises(ValueError, match="已有回测在运行"):
        acquire_execution("run:workbench")
    overflow: list[str] = []

    def extra_plan_worker() -> None:
        mark_plan_worker()
        try:
            acquire_execution("run:extra")
            overflow.append("acquired")
            release_execution()
        except ValueError as error:
            overflow.append(str(error))

    extra = threading.Thread(target=extra_plan_worker)
    extra.start()
    extra.join(timeout=5)
    assert extra.is_alive() is False
    assert overflow and "已有回测在运行" in overflow[0]
    hold.set()
    for thread in threads:
        thread.join(timeout=5)
        assert thread.is_alive() is False
    assert errors == []
    release_reservation()


def test_plan_detail_includes_admission_snapshot(tmp_path: Path) -> None:
    from quantlab.services import backtest_admission as gate

    gate.reset_admission()
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post(
        "/api/backtest-plans",
        json={"name": "放行快照", "items": [{"name": "一笔", "config": config("snap")}]},
    )
    assert created.status_code == 201
    body = client.get(f"/api/backtest-plans/{created.json()['plan_id']}").json()
    admission = body["admission"]
    assert admission["loading"] == 0
    assert admission["running"] == 0
    assert admission["limit"] >= 1
    assert admission["load_limit"] >= 1
    assert admission["can_start"] is True
    gate.reset_admission()


def test_start_skips_unselected_and_completed_items(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan = client.post(
        "/api/backtest-plans",
        json={
            "name": "部分勾选",
            "items": [
                {"name": "跑", "config": config("run")},
                {"name": "先不跑", "config": config("skip")},
            ],
        },
    ).json()
    skipped_id = plan["items"][1]["item_id"]
    patched = client.patch(
        f"/api/backtest-plans/{plan['plan_id']}/items",
        json={"selected": {skipped_id: False}},
    )
    assert patched.status_code == 200
    assert patched.json()["items"][1]["selected"] is False
    client.post(f"/api/backtest-plans/{plan['plan_id']}/start", json={})
    finished = _wait_plan(client, plan["plan_id"])
    assert finished["items"][0]["status"] == "completed"
    assert finished["items"][1]["status"] == "pending"
    assert finished["items"][1]["run_id"] is None
    again = client.post(f"/api/backtest-plans/{plan['plan_id']}/start", json={})
    assert again.status_code == 400
    assert again.json()["error_code"] == "BACKTEST_PLAN_EMPTY"
    unchanged = client.get(f"/api/backtest-plans/{plan['plan_id']}").json()
    assert unchanged["items"][0]["run_id"] == finished["items"][0]["run_id"]
    assert unchanged["items"][1]["status"] == "pending"


def test_second_start_is_rejected_while_plan_is_running(tmp_path: Path, monkeypatch) -> None:
    from quantlab.services import backtest_control

    hold = time.time() + 3
    original = backtest_control.run_isolated

    def slow_isolated(settings, job, run_id):
        while time.time() < hold:
            time.sleep(0.05)
        return original(settings, job, run_id)

    monkeypatch.setattr("quantlab.services.backtest_plan.run_isolated", slow_isolated)
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan_id = client.post(
        "/api/backtest-plans",
        json={"name": "占用", "items": [{"name": "慢", "config": config("hold")}]},
    ).json()["plan_id"]
    first = client.post(f"/api/backtest-plans/{plan_id}/start", json={})
    assert first.status_code == 202
    busy = client.post(f"/api/backtest-plans/{plan_id}/start", json={})
    assert busy.status_code == 409
    assert busy.json()["error_code"] == "BACKTEST_PLAN_BUSY"
    _wait_plan(client, plan_id, timeout=15)


def test_list_plans_keeps_creation_order_after_later_updates(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    first = client.post("/api/backtest-plans", json={"name": "先建"}).json()
    second = client.post("/api/backtest-plans", json={"name": "后建"}).json()
    added = client.post(
        f"/api/backtest-plans/{first['plan_id']}/items",
        json={"name": "后补的任务", "config": config("later")},
    )
    assert added.status_code == 201
    names = [plan["name"] for plan in client.get("/api/backtest-plans").json()["items"]]
    assert names[:2] == ["后建", "先建"]


def test_list_plans_returns_summaries_without_items_or_catalog_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post(
        "/api/backtest-plans",
        json={"name": "摘要计划", "items": [{"name": "一笔", "config": config("a")}]},
    ).json()
    real_get = BacktestPlanService.get

    def boom_get(self, plan_id: str):
        raise AssertionError("list_plans must not load full plans")

    monkeypatch.setattr(BacktestPlanService, "get", boom_get)
    listed = client.get("/api/backtest-plans").json()["items"]
    assert len(listed) == 1
    row = listed[0]
    assert row["plan_id"] == created["plan_id"]
    assert row["name"] == "摘要计划"
    assert row["status"] == "draft"
    assert row["closed"] is False
    assert row["item_count"] == 1
    assert "items" not in row
    assert "config" not in row
    opened = client.get("/api/backtest-plans", params={"open": "1"}).json()["items"]
    assert [item["plan_id"] for item in opened] == [created["plan_id"]]
    assert "items" not in opened[0]
    monkeypatch.setattr(BacktestPlanService, "get", real_get)
    detail = client.get(f"/api/backtest-plans/{created['plan_id']}").json()
    assert len(detail["items"]) == 1
    assert detail["items"][0]["name"] == "一笔"
    assert isinstance(detail["items"][0]["config"], dict)


def test_start_reruns_single_failed_item(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan = client.post(
        "/api/backtest-plans",
        json={
            "name": "失败单笔重算",
            "items": [
                {"name": "要重算", "config": config("retry")},
                {"name": "先不动", "config": config("keep")},
            ],
        },
    ).json()
    failed_id = plan["items"][0]["item_id"]
    keep_id = plan["items"][1]["item_id"]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_plan_items SET status=?, error_message=? WHERE item_id=?",
            ("failed", "已强行停止", failed_id),
        )
        connection.execute(
            "UPDATE backtest_plans SET status=? WHERE plan_id=?",
            ("completed", plan["plan_id"]),
        )
    started = client.post(
        f"/api/backtest-plans/{plan['plan_id']}/start",
        json={"item_ids": [failed_id]},
    )
    assert started.status_code == 202
    finished = _wait_plan(client, plan["plan_id"])
    by_id = {item["item_id"]: item for item in finished["items"]}
    assert by_id[failed_id]["status"] == "completed"
    assert by_id[failed_id]["run_id"]
    assert by_id[failed_id]["error_message"] in {None, ""}
    assert by_id[keep_id]["status"] == "pending"
    assert by_id[keep_id]["run_id"] is None


def test_start_reruns_single_completed_item(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan = client.post(
        "/api/backtest-plans",
        json={
            "name": "完成单笔重算",
            "items": [
                {"name": "要重算", "config": config("retry")},
                {"name": "先不动", "config": config("keep")},
            ],
        },
    ).json()
    retry_id = plan["items"][0]["item_id"]
    keep_id = plan["items"][1]["item_id"]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_plan_items SET status=? WHERE item_id=?",
            ("completed", retry_id),
        )
        connection.execute(
            "UPDATE backtest_plans SET status=? WHERE plan_id=?",
            ("completed", plan["plan_id"]),
        )
    started = client.post(
        f"/api/backtest-plans/{plan['plan_id']}/start",
        json={"item_ids": [retry_id]},
    )
    assert started.status_code == 202
    finished = _wait_plan(client, plan["plan_id"])
    by_id = {item["item_id"]: item for item in finished["items"]}
    assert by_id[retry_id]["status"] == "completed"
    assert by_id[retry_id]["run_id"]
    assert by_id[keep_id]["status"] == "pending"
    assert by_id[keep_id]["run_id"] is None


def test_create_rejects_blank_plan_name(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post("/api/backtest-plans", json={"name": "  "})
    assert created.status_code == 400
    assert created.json()["error_code"] == "BACKTEST_PLAN_INVALID"


def test_close_hides_plan_from_open_list(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    created = client.post("/api/backtest-plans", json={"name": "可算因子第一批"}).json()
    plan_id = created["plan_id"]
    assert created["closed"] is False
    with database.connect() as connection:
        info = connection.execute("PRAGMA table_info(backtest_plans)").fetchall()
        names = {row[1] for row in info}
        pk = {row[1] for row in info if row[5]}
        assert "plan_id" in names
        assert pk == {"plan_id"}
    closed = client.post(f"/api/backtest-plans/{plan_id}/close", json={})
    assert closed.status_code == 200
    body = closed.json()
    assert body["closed"] is True
    assert body["status"] != "running"
    listed = client.get("/api/backtest-plans").json()["items"]
    assert any(item["plan_id"] == plan_id for item in listed)
    opened = client.get("/api/backtest-plans", params={"open": "1"}).json()["items"]
    assert opened == []


def test_closed_plan_rejects_new_items_and_start(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan_id = client.post("/api/backtest-plans", json={"name": "要完结"}).json()["plan_id"]
    client.post(f"/api/backtest-plans/{plan_id}/close")
    added = client.post(
        f"/api/backtest-plans/{plan_id}/items",
        json={"name": "不能加", "config": config("closed")},
    )
    assert added.status_code == 400
    assert added.json()["error_code"] == "BACKTEST_PLAN_CLOSED"
    started = client.post(f"/api/backtest-plans/{plan_id}/start", json={})
    assert started.status_code == 400
    assert started.json()["error_code"] == "BACKTEST_PLAN_CLOSED"


def _insert_run(database, settings, run_id: str, name: str) -> Path:
    from quantlab.repositories.artifacts import ArtifactRepository
    from quantlab.services.backtest_summary import refresh_backtest_summary

    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    metrics = folder / "metrics.json"
    trades = folder / "trades.json"
    metrics.write_text('{"return":0.2}', encoding="utf-8")
    trades.write_text("[]", encoding="utf-8")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
            (run_id, "2026-10-03T10:00:00.000000+00:00", "2026-10-03T10:01:00.000000+00:00"),
        )
        connection.execute(
            "INSERT INTO backtest_runs(run_id,status,config_json,metrics_json) VALUES (?, 'queued', ?, ?)",
            (
                run_id,
                json.dumps({"name": name, "test": {"date_from": "2025-01-02", "date_to": "2026-08-31"}}),
                '{"return":0.2}',
            ),
        )
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='completed' WHERE run_id=?", (run_id,))
        connection.execute(
            "INSERT INTO backtest_steps(run_id, ordinal, step_name, status) VALUES (?, 1, 'metrics', 'completed')",
            (run_id,),
        )
        refresh_backtest_summary(connection, run_id)
    ArtifactRepository(settings, database).register(
        run_id=run_id, path=metrics, display_name="回测指标", artifact_role="metrics"
    )
    return folder


def test_empty_plan_can_be_deleted(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    empty_id = client.post("/api/backtest-plans", json={"name": "空计划"}).json()["plan_id"]
    client.post(f"/api/backtest-plans/{empty_id}/close")
    deleted = client.delete(f"/api/backtest-plans/{empty_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "plan_id": empty_id, "deleted_run_ids": []}
    missing = client.get(f"/api/backtest-plans/{empty_id}")
    assert missing.status_code == 404
    listed = client.get("/api/backtest-plans").json()["items"]
    assert all(item["plan_id"] != empty_id for item in listed)
    unknown = client.delete("/api/backtest-plans/plan-missing")
    assert unknown.status_code == 404


def test_deleting_plan_removes_its_runs_artifacts_and_leaves_other_runs(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    filled = client.post(
        "/api/backtest-plans",
        json={
            "name": "有任务",
            "items": [
                {"name": "独有", "config": config("keep")},
                {"name": "共用", "config": config("shared")},
            ],
        },
    ).json()
    kept = client.post(
        "/api/backtest-plans",
        json={"name": "别的计划", "items": [{"name": "留着", "config": config("other")}]},
    ).json()
    run_id = "20261003-184700-0001"
    kept_id = "20261003-184700-0002"
    shared_id = "20261003-184700-0003"
    folder = _insert_run(database, settings, run_id, "要删的回测")
    kept_folder = _insert_run(database, settings, kept_id, "别的回测")
    shared_folder = _insert_run(database, settings, shared_id, "两边都引用")
    snapshot = settings.runtime_root / "results" / "_plans" / f"{filled['plan_id']}.json"
    assert snapshot.is_file()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_plan_items SET run_id=?, status='completed' WHERE item_id=?",
            (run_id, filled["items"][0]["item_id"]),
        )
        connection.execute(
            "UPDATE backtest_plan_items SET run_id=?, status='completed' WHERE item_id=?",
            (shared_id, filled["items"][1]["item_id"]),
        )
        connection.execute(
            "UPDATE backtest_plan_items SET run_id=?, status='completed' WHERE item_id=?",
            (shared_id, kept["items"][0]["item_id"]),
        )
        connection.execute(
            "UPDATE backtest_plans SET status='running' WHERE plan_id=?",
            (filled["plan_id"],),
        )
    running = client.delete(f"/api/backtest-plans/{filled['plan_id']}")
    assert running.status_code == 400
    assert "正在运行" in running.json()["message"]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_plans SET status='completed' WHERE plan_id=?",
            (filled["plan_id"],),
        )
    deleted = client.delete(f"/api/backtest-plans/{filled['plan_id']}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["plan_id"] == filled["plan_id"]
    assert body["deleted_run_ids"] == [run_id]
    assert client.get(f"/api/backtest-plans/{filled['plan_id']}").status_code == 404
    assert not folder.exists()
    assert not snapshot.exists()
    marker = settings.runtime_root / "results" / "_deleted" / f"{run_id}.json"
    assert marker.is_file()
    assert kept_folder.is_dir() and shared_folder.is_dir()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_summaries WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_steps WHERE run_id=?", (run_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_plan_items WHERE plan_id=?", (filled["plan_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (kept_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (shared_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM backtest_summaries WHERE run_id=?", (shared_id,)).fetchone()[0] == 1
    assert client.get(f"/api/backtest-plans/{kept['plan_id']}").status_code == 200
    removed_kept = client.delete(f"/api/backtest-plans/{kept['plan_id']}")
    assert removed_kept.status_code == 200
    assert removed_kept.json()["deleted_run_ids"] == [shared_id]
    assert not shared_folder.exists()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id=?", (shared_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_summaries WHERE run_id=?", (shared_id,)).fetchone()[0] == 0


def test_selected_plan_items_can_be_deleted_in_bulk(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    plan = client.post(
        "/api/backtest-plans",
        json={
            "name": "可删任务",
            "items": [
                {"name": "留着", "config": config("keep")},
                {"name": "删掉甲", "config": config("drop-a")},
                {"name": "删掉乙", "config": config("drop-b")},
            ],
        },
    ).json()
    keep_id = plan["items"][0]["item_id"]
    drop_ids = [plan["items"][1]["item_id"], plan["items"][2]["item_id"]]
    empty = client.post(f"/api/backtest-plans/{plan['plan_id']}/items/delete", json={"item_ids": []})
    assert empty.status_code == 400
    assert empty.json()["error_code"] == "BACKTEST_PLAN_NOTHING_SELECTED"
    client.patch(
        f"/api/backtest-plans/{plan['plan_id']}/items",
        json={"selected": {keep_id: False, drop_ids[0]: True, drop_ids[1]: True}},
    )
    by_selection = client.post(f"/api/backtest-plans/{plan['plan_id']}/items/delete", json={})
    assert by_selection.status_code == 200
    remaining = [item["item_id"] for item in by_selection.json()["items"]]
    assert remaining == [keep_id]
    all_ids = [keep_id]
    cleared = client.post(
        f"/api/backtest-plans/{plan['plan_id']}/items/delete",
        json={"item_ids": all_ids},
    )
    assert cleared.status_code == 200
    assert cleared.json()["items"] == []
    closed_id = client.post("/api/backtest-plans", json={"name": "完结后不能删任务"}).json()["plan_id"]
    client.post(f"/api/backtest-plans/{closed_id}/close")
    blocked = client.post(f"/api/backtest-plans/{closed_id}/items/delete", json={"item_ids": ["item-x"]})
    assert blocked.status_code == 400
    assert blocked.json()["error_code"] == "BACKTEST_PLAN_CLOSED"


def test_plan_page_and_workbench_expose_select_all_start(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    page = client.get("/backtests/plan")
    assert page.status_code == 200
    assert "no-store" in (page.headers.get("cache-control") or "")
    html = page.text
    plan_js = Path("quantlab/web/assets/backtest/plan.js").read_text(encoding="utf-8")
    source = html + plan_js
    assert "回测计划" in html
    assert "同时读几笔行情" in html
    assert "正在跑 ${running} 笔" in plan_js
    assert "全选" in html
    assert "开始" in html
    assert "停止计划" in html
    assert 'id="new-plan"' in html
    assert "新建计划" in html
    assert 'id="plan-dialog"' in html
    assert 'id="plan-name"' in html
    assert 'id="close-plan"' in html
    assert "完结" in html
    assert 'id="delete-plan"' in html
    assert "删除" in html
    assert 'id="delete-items"' in html
    assert "删除任务" in html
    assert "/api/backtest-plans/" in plan_js and "/items/delete" in plan_js
    assert "async function loadPlan(" in plan_js
    assert "item_count" in plan_js
    assert '$("plan-select").addEventListener("change", async () => {' in plan_js
    assert "收益率" in source
    assert "最大回撤" in source
    assert "plan-sortable" in source
    assert "summary?.model_name" in plan_js
    assert 'dataset.sort = column.key' in plan_js or "dataset.sort" in plan_js
    assert "重算" in plan_js
    assert '{key: "retry", label: "重算"' in plan_js
    assert "plan-col-retry" in plan_js
    assert '["failed", "skipped", "completed"].includes(item.status)' in plan_js
    workbench = client.get("/backtests/new").text
    workbench_js = Path("quantlab/web/assets/backtest/workbench.js").read_text(encoding="utf-8")
    workbench_source = workbench + workbench_js
    assert "加入计划" in workbench
    assert "/backtests/plan" in workbench
    identity = workbench.split("运行身份", 1)[1].split("因子组合", 1)[0]
    assert 'id="identity-plan"' in identity
    assert 'label for="identity-plan">回测计划（选填）' in identity
    assert ">不选择<" in identity
    assert "没有未完结计划（选填）" in workbench_source
    assert "/api/backtest-plans?open=1" in workbench_js
