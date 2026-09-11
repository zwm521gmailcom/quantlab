from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app

from tests.quantlab.test_backtest_workbench import config, setup_env


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


def test_start_runs_selected_items_serially(tmp_path: Path) -> None:
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


def test_empty_plan_can_be_deleted_and_plan_with_items_cannot(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    empty_id = client.post("/api/backtest-plans", json={"name": "空计划"}).json()["plan_id"]
    filled = client.post(
        "/api/backtest-plans",
        json={"name": "有任务", "items": [{"name": "一笔", "config": config("keep")}]},
    ).json()
    blocked = client.delete(f"/api/backtest-plans/{filled['plan_id']}")
    assert blocked.status_code == 400
    assert blocked.json()["error_code"] == "BACKTEST_PLAN_NOT_EMPTY"
    assert client.get(f"/api/backtest-plans/{filled['plan_id']}").status_code == 200
    client.post(f"/api/backtest-plans/{empty_id}/close")
    deleted = client.delete(f"/api/backtest-plans/{empty_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "plan_id": empty_id}
    missing = client.get(f"/api/backtest-plans/{empty_id}")
    assert missing.status_code == 404
    listed = client.get("/api/backtest-plans").json()["items"]
    assert all(item["plan_id"] != empty_id for item in listed)
    unknown = client.delete("/api/backtest-plans/plan-missing")
    assert unknown.status_code == 404


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
    html = page.text
    plan_js = Path("quantlab/web/assets/backtest/plan.js").read_text(encoding="utf-8")
    source = html + plan_js
    assert "回测计划" in html
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
    assert "收益率" in source
    assert "最大回撤" in source
    assert "plan-sortable" in source
    assert "summary?.model_name" in plan_js
    assert 'dataset.sort = column.key' in plan_js or "dataset.sort" in plan_js
    assert "重算" in plan_js
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
