from __future__ import annotations

import time
from pathlib import Path

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


def test_plan_page_and_workbench_expose_select_all_start(tmp_path: Path) -> None:
    settings, database = setup_env(tmp_path)
    client = TestClient(create_app(settings, database))
    page = client.get("/backtests/plan")
    assert page.status_code == 200
    html = page.text
    assert "回测计划" in html
    assert "全选" in html
    assert "开始" in html
    assert "停止计划" in html
    workbench = client.get("/backtests/new").text
    assert "加入计划" in workbench
    assert "/backtests/plan" in workbench
