"""Serial backtest plan queue: save configs first, run selected items one by one."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.backtest_control import (
    bind_execution_thread,
    release_reservation,
    reserve_execution,
    run_isolated,
    stop_run,
)
from quantlab.services.backtest_job import BacktestJobService
from quantlab.services.backtest_workbench import BacktestWorkbenchService


_runtime_lock = threading.Lock()
_runtime: dict[str, dict[str, Any]] = {}


def reset_plan_runtime() -> None:
    with _runtime_lock:
        for state in _runtime.values():
            event = state.get("stop")
            if event is not None:
                event.set()
        _runtime.clear()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _summary(config: dict[str, Any]) -> dict[str, Any]:
    factors: list[str] = []
    for item in config.get("factor_versions") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("factor_id") or "").strip()
        if field:
            factors.append(field.removeprefix("factor_"))
    train = config.get("train") if isinstance(config.get("train"), dict) else {}
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    return {
        "factors": factors,
        "train": f"{train.get('date_from') or ''} — {train.get('date_to') or ''}".strip(" —"),
        "test": f"{test.get('date_from') or ''} — {test.get('date_to') or ''}".strip(" —"),
        "kind": config.get("kind"),
        "walk_forward": config.get("walk_forward") or "once",
        "top_n": config.get("top_n"),
        "model": model,
    }


def _item_view(row: Any) -> dict[str, Any]:
    config = json.loads(row["config_json"] or "{}")
    return {
        "item_id": row["item_id"],
        "plan_id": row["plan_id"],
        "sort_order": int(row["sort_order"]),
        "selected": bool(row["selected"]),
        "name": row["name"],
        "status": row["status"],
        "run_id": row["run_id"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "summary": _summary(config),
        "config": config,
    }


class BacktestPlanService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        workbench: BacktestWorkbenchService,
        job: BacktestJobService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.workbench = workbench
        self.job = job

    def list_plans(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backtest_plans ORDER BY updated_at DESC, plan_id DESC"
            ).fetchall()
        return {"items": [self.get(row["plan_id"]) for row in rows]}

    def get(self, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise ValueError("backtest plan not found")
            items = connection.execute(
                "SELECT * FROM backtest_plan_items WHERE plan_id=? ORDER BY sort_order, item_id",
                (plan_id,),
            ).fetchall()
        return {
            "plan_id": row["plan_id"],
            "name": row["name"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "items": [_item_view(item) for item in items],
        }

    def create(self, raw: dict[str, Any]) -> dict[str, Any]:
        name = str(raw.get("name") or "").strip() or "未命名回测计划"
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        plan_id = _new_id("plan")
        stamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO backtest_plans(plan_id, name, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (plan_id, name, "draft", stamp, stamp),
            )
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("plan item must be an object")
            self.add_item(plan_id, item)
        return self.get(plan_id)

    def add_item(self, plan_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        plan = self.get(plan_id)
        if plan["status"] == "running":
            raise ValueError("回测计划正在运行，不能改任务清单。")
        config_raw = raw.get("config")
        if not isinstance(config_raw, dict):
            raise ValueError("config is required")
        config = self.workbench.validate(config_raw)
        name = str(raw.get("name") or config.get("name") or "未命名回测").strip()
        stamp = _now()
        item_id = _new_id("item")
        with self.database.transaction() as connection:
            order_row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM backtest_plan_items WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            sort_order = int(order_row["max_order"] if order_row else 0) + 1
            connection.execute(
                "INSERT INTO backtest_plan_items("
                "item_id, plan_id, sort_order, selected, name, config_json, status, "
                "run_id, error_message, started_at, finished_at, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    plan_id,
                    sort_order,
                    1,
                    name,
                    _json(config),
                    "pending",
                    None,
                    None,
                    None,
                    None,
                    stamp,
                    stamp,
                ),
            )
            connection.execute(
                "UPDATE backtest_plans SET status='draft', updated_at=? WHERE plan_id=?",
                (stamp, plan_id),
            )
        return self.get(plan_id)

    def set_selected(self, plan_id: str, selected: dict[str, Any]) -> dict[str, Any]:
        self.get(plan_id)
        if not isinstance(selected, dict) or not selected:
            raise ValueError("selected is required")
        stamp = _now()
        with self.database.transaction() as connection:
            for item_id, flag in selected.items():
                connection.execute(
                    "UPDATE backtest_plan_items SET selected=?, updated_at=? WHERE plan_id=? AND item_id=?",
                    (1 if flag else 0, stamp, plan_id, str(item_id)),
                )
            connection.execute(
                "UPDATE backtest_plans SET updated_at=? WHERE plan_id=?",
                (stamp, plan_id),
            )
        return self.get(plan_id)

    def delete_item(self, plan_id: str, item_id: str) -> dict[str, Any]:
        plan = self.get(plan_id)
        if plan["status"] == "running":
            raise ValueError("回测计划正在运行，不能删任务。")
        stamp = _now()
        with self.database.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM backtest_plan_items WHERE plan_id=? AND item_id=?",
                (plan_id, item_id),
            ).rowcount
            if not deleted:
                raise ValueError("backtest plan item not found")
            connection.execute(
                "UPDATE backtest_plans SET updated_at=? WHERE plan_id=?",
                (stamp, plan_id),
            )
        return self.get(plan_id)

    def start(self, plan_id: str, item_ids: list[str] | None = None) -> dict[str, Any]:
        plan = self.get(plan_id)
        if plan["status"] == "running":
            raise RuntimeError("busy")
        wanted = {str(item) for item in (item_ids or [])}
        if item_ids:
            stamp = _now()
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE backtest_plan_items SET selected=0, updated_at=? WHERE plan_id=?",
                    (stamp, plan_id),
                )
                for item_id in wanted:
                    connection.execute(
                        "UPDATE backtest_plan_items SET selected=1, updated_at=? WHERE plan_id=? AND item_id=?",
                        (stamp, plan_id, item_id),
                    )
            plan = self.get(plan_id)
        eligible = [
            item
            for item in plan["items"]
            if item["selected"] and item["status"] in {"pending", "failed", "skipped"}
        ]
        if not eligible:
            raise ValueError("empty")
        stamp = _now()
        with self.database.transaction() as connection:
            changed = connection.execute(
                "UPDATE backtest_plans SET status='running', updated_at=? "
                "WHERE plan_id=? AND status != 'running'",
                (stamp, plan_id),
            ).rowcount
            if not changed:
                raise RuntimeError("busy")
            for item in eligible:
                connection.execute(
                    "UPDATE backtest_plan_items SET status='queued', error_message=NULL, updated_at=? "
                    "WHERE item_id=?",
                    (stamp, item["item_id"]),
                )
        try:
            reserve_execution(f"plan:{plan_id}")
        except ValueError:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE backtest_plans SET status='draft', updated_at=? WHERE plan_id=?",
                    (_now(), plan_id),
                )
                connection.execute(
                    "UPDATE backtest_plan_items SET status='pending', updated_at=? "
                    "WHERE plan_id=? AND status='queued'",
                    (_now(), plan_id),
                )
            raise
        stop = threading.Event()
        thread = threading.Thread(
            target=self._run_loop,
            args=(plan_id, [item["item_id"] for item in eligible], stop),
            name=f"quantlab-plan-{plan_id}",
            daemon=True,
        )
        with _runtime_lock:
            _runtime[plan_id] = {"stop": stop, "thread": thread, "current_run_id": None}
        thread.start()
        return self.get(plan_id)

    def stop(self, plan_id: str) -> dict[str, Any]:
        self.get(plan_id)
        with _runtime_lock:
            state = _runtime.get(plan_id)
            run_id = state.get("current_run_id") if state else None
            if state and state.get("stop"):
                state["stop"].set()
        if run_id:
            try:
                stop_run(self.job, run_id)
            except ValueError:
                pass
        stamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE backtest_plan_items SET status='pending', updated_at=? "
                "WHERE plan_id=? AND status='queued'",
                (stamp, plan_id),
            )
            connection.execute(
                "UPDATE backtest_plans SET status='stopped', updated_at=? WHERE plan_id=? AND status='running'",
                (stamp, plan_id),
            )
        return self.get(plan_id)

    def _run_loop(self, plan_id: str, item_ids: list[str], stop: threading.Event) -> None:
        try:
            bind_execution_thread()
            for item_id in item_ids:
                if stop.is_set():
                    break
                self._run_item(plan_id, item_id, stop)
        finally:
            stamp = _now()
            with self.database.transaction() as connection:
                current = connection.execute(
                    "SELECT status FROM backtest_plans WHERE plan_id=?", (plan_id,)
                ).fetchone()
                connection.execute(
                    "UPDATE backtest_plan_items SET status='pending', updated_at=? "
                    "WHERE plan_id=? AND status='queued'",
                    (stamp, plan_id),
                )
                if current and current["status"] == "running":
                    done = "stopped" if stop.is_set() else "completed"
                    connection.execute(
                        "UPDATE backtest_plans SET status=?, updated_at=? WHERE plan_id=?",
                        (done, stamp, plan_id),
                    )
            with _runtime_lock:
                _runtime.pop(plan_id, None)
            release_reservation()

    def _run_item(self, plan_id: str, item_id: str, stop: threading.Event) -> None:
        if stop.is_set():
            return
        stamp = _now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_plan_items WHERE item_id=?", (item_id,)
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE backtest_plan_items SET status='running', started_at=?, updated_at=? WHERE item_id=?",
                (stamp, stamp, item_id),
            )
        config = json.loads(row["config_json"] or "{}")
        payload = dict(config)
        payload["submission_token"] = uuid.uuid4().hex
        payload["name"] = row["name"]
        try:
            submitted = self.workbench.submit(payload)
            run_id = str(submitted["run_id"])
        except Exception as error:
            self._finish_item(item_id, "failed", error_message=str(error), finished_at=_now())
            return
        with _runtime_lock:
            state = _runtime.get(plan_id)
            if state is not None:
                state["current_run_id"] = run_id
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE backtest_plan_items SET run_id=?, updated_at=? WHERE item_id=?",
                (run_id, _now(), item_id),
            )
        if stop.is_set():
            try:
                stop_run(self.job, run_id)
            except ValueError:
                pass
            self._finish_item(
                item_id, "failed", run_id=run_id, error_message="已强行停止", finished_at=_now()
            )
            return
        try:
            result = run_isolated(self.settings, self.job, run_id) or {}
        except Exception as error:
            self._finish_item(
                item_id, "failed", run_id=run_id, error_message=str(error), finished_at=_now()
            )
            return
        status = str(result.get("status") or "failed")
        item_status = "completed" if status == "completed" else "failed"
        message = None if item_status == "completed" else str(result.get("error_message") or status)
        self._finish_item(
            item_id,
            item_status,
            run_id=run_id,
            error_message=message,
            finished_at=_now(),
        )

    def _finish_item(
        self,
        item_id: str,
        status: str,
        *,
        run_id: str | None = None,
        error_message: str | None = None,
        finished_at: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE backtest_plan_items SET status=?, run_id=COALESCE(?, run_id), "
                "error_message=?, finished_at=?, updated_at=? WHERE item_id=?",
                (status, run_id, error_message, finished_at, finished_at, item_id),
            )
        with _runtime_lock:
            for state in _runtime.values():
                if state.get("current_run_id") == run_id:
                    state["current_run_id"] = None
