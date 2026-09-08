"""Force-stop a running backtest without taking down the web server."""

from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.backtest_job import BacktestJobService

STOPPED_MESSAGE = "已强行停止"

_lock = threading.Lock()
_workers: dict[str, Any] = {}
_stop_requested: set[str] = set()
_gate_lock = threading.Lock()
_gate_owner: int | str | None = None
_gate_depth = 0
_gate_label: str | None = None


def settings_payload(settings: Settings) -> dict[str, Any]:
    return {
        "project_root": str(settings.project_root),
        "data_root": str(settings.data_root),
        "calibration_root": str(settings.calibration_root),
        "raw_root": str(settings.raw_root),
        "runtime_root": str(settings.runtime_root),
        "host": settings.host,
        "port": settings.port,
    }


def execute_backtest_worker(payload: dict[str, Any], run_id: str) -> None:
    settings = Settings(**payload)
    database = Database(settings.database_path)
    BacktestJobService(settings, database).execute(run_id)


def reset_execution_gate() -> None:
    global _gate_owner, _gate_depth, _gate_label
    with _gate_lock:
        _gate_owner = None
        _gate_depth = 0
        _gate_label = None


def execution_busy_label() -> str | None:
    with _gate_lock:
        return _gate_label


def reserve_execution(label: str) -> None:
    global _gate_owner, _gate_depth, _gate_label
    with _gate_lock:
        if _gate_owner is not None:
            raise ValueError(f"已有回测在运行（{_gate_label}），请等当前任务结束或先停止。")
        _gate_owner = "pending"
        _gate_depth = 1
        _gate_label = label


def bind_execution_thread() -> None:
    global _gate_owner
    with _gate_lock:
        _gate_owner = threading.get_ident()


def acquire_execution(label: str) -> None:
    global _gate_owner, _gate_depth, _gate_label
    ident = threading.get_ident()
    with _gate_lock:
        if _gate_owner == "pending" or (_gate_owner is not None and _gate_owner != ident):
            raise ValueError(f"已有回测在运行（{_gate_label}），请等当前任务结束或先停止。")
        _gate_owner = ident
        _gate_depth += 1
        if _gate_label is None:
            _gate_label = label


def release_execution() -> None:
    global _gate_owner, _gate_depth, _gate_label
    ident = threading.get_ident()
    with _gate_lock:
        if _gate_owner != ident:
            return
        _gate_depth = max(0, _gate_depth - 1)
        if _gate_depth == 0:
            _gate_owner = None
            _gate_label = None


def release_reservation() -> None:
    reset_execution_gate()


def register_worker(run_id: str, proc: Any) -> None:
    with _lock:
        _workers[run_id] = proc


def _signal_tree(pid: int, sig: int) -> None:
    try:
        children = subprocess.check_output(["pgrep", "-P", str(pid)], text=True).split()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        children = []
    for child in children:
        try:
            child_pid = int(child)
        except ValueError:
            continue
        _signal_tree(child_pid, sig)
        try:
            os.kill(child_pid, sig)
        except ProcessLookupError:
            continue


def _terminate(proc: Any) -> None:
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    alive = True
    try:
        alive = bool(proc.is_alive())
    except Exception:
        alive = True
    if pid and alive:
        _signal_tree(pid, signal.SIGTERM)
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.join(timeout=4)
    except Exception:
        pass
    still = False
    try:
        still = bool(proc.is_alive())
    except Exception:
        still = False
    if still:
        if pid:
            _signal_tree(pid, signal.SIGKILL)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.join(timeout=2)
        except Exception:
            pass


def mark_stopped(database: Database, run_id: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET status='running' WHERE run_id=? AND status='queued'",
            (run_id,),
        )
        connection.execute(
            "UPDATE backtest_runs SET status='failed', error_message=? WHERE run_id=? AND status='running'",
            (STOPPED_MESSAGE, run_id),
        )
        connection.execute(
            "UPDATE backtest_steps SET status='failed', finished_at=COALESCE(finished_at, ?), error_message=? "
            "WHERE run_id=? AND status='running'",
            (timestamp, STOPPED_MESSAGE, run_id),
        )
        connection.execute(
            "UPDATE backtest_steps SET status='skipped', finished_at=COALESCE(finished_at, ?) "
            "WHERE run_id=? AND status='pending'",
            (timestamp, run_id),
        )
        connection.execute(
            "UPDATE run_registry SET finished_at=COALESCE(finished_at, ?) WHERE run_id=?",
            (timestamp, run_id),
        )


def stop_run(job: BacktestJobService, run_id: str) -> dict[str, Any]:
    current = job.get(run_id)
    if current is None:
        raise ValueError("backtest run not found")
    if current.get("status") == "completed":
        raise ValueError("这条回测已经完成，不能停止。")
    with _lock:
        _stop_requested.add(run_id)
        proc = _workers.get(run_id)
    _terminate(proc)
    with _lock:
        _workers.pop(run_id, None)
    mark_stopped(job.database, run_id)
    return job.get(run_id) or current


def run_isolated(settings: Settings, job: BacktestJobService, run_id: str) -> dict[str, Any] | None:
    current = job.get(run_id)
    if current is None:
        return None
    acquire_execution(f"run:{run_id}")
    try:
        with _lock:
            _stop_requested.discard(run_id)
        inline = str(os.environ.get("QUANTLAB_BACKTEST_INLINE") or "").strip().lower()
        if inline in {"1", "true", "yes"}:
            return job.execute(run_id)
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=execute_backtest_worker,
            args=(settings_payload(settings), run_id),
            name=f"quantlab-bt-{run_id}",
        )
        with _lock:
            if run_id in _stop_requested:
                mark_stopped(job.database, run_id)
                return job.get(run_id)
            _workers[run_id] = proc
        proc.start()
        try:
            with _lock:
                if run_id in _stop_requested:
                    _terminate(proc)
            proc.join()
        finally:
            with _lock:
                _workers.pop(run_id, None)
        result = job.get(run_id)
        if proc.exitcode not in (0,) and result and result.get("status") == "running":
            mark_stopped(job.database, run_id)
            result = job.get(run_id)
        return result
    finally:
        release_execution()
