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
from quantlab.domain.status import BacktestRunStatus, transition
from quantlab.repositories.database import Database
from quantlab.services.backtest_job import BacktestJobService

STOPPED_MESSAGE = "已强行停止"

_lock = threading.Lock()
_workers: dict[str, Any] = {}
_stop_requested: set[str] = set()
_gate_lock = threading.Lock()
_gate_label: str | None = None
_plan_reserved: str | None = None
_depths: dict[int, int] = {}
_plan_worker = threading.local()


def settings_payload(settings: Settings) -> dict[str, Any]:
    return {
        "project_root": settings.display_path(settings.project_root),
        "data_root": settings.display_path(settings.data_root),
        "calibration_root": settings.display_path(settings.calibration_root),
        "raw_root": settings.display_path(settings.raw_root),
        "runtime_root": settings.display_path(settings.runtime_root),
        "host": settings.host,
        "port": settings.port,
    }


def execute_backtest_worker(payload: dict[str, Any], run_id: str) -> None:
    settings = Settings(
        project_root=".",
        data_root=payload["data_root"],
        calibration_root=payload["calibration_root"],
        raw_root=payload["raw_root"],
        runtime_root=payload["runtime_root"],
        host=payload.get("host", "127.0.0.1"),
        port=int(payload.get("port") or 8765),
    )
    database = Database(settings.database_path)
    BacktestJobService(settings, database).execute(run_id)


def _spawn_backtest_worker(project_root: str, payload: dict[str, Any], run_id: str) -> None:
    os.chdir(project_root)
    execute_backtest_worker(payload, run_id)


def reset_execution_gate() -> None:
    global _gate_label, _plan_reserved
    with _gate_lock:
        _gate_label = None
        _plan_reserved = None
        _depths.clear()


def execution_busy_label() -> str | None:
    with _gate_lock:
        if _plan_reserved or _depths:
            return _gate_label
        return None


def mark_plan_worker() -> None:
    _plan_worker.active = True


def reserve_execution(label: str) -> None:
    global _gate_label, _plan_reserved
    with _gate_lock:
        if _plan_reserved is not None or _depths:
            raise ValueError(f"已有回测在运行（{_gate_label}），请等当前任务结束或先停止。")
        _plan_reserved = label
        _gate_label = label


def bind_execution_thread() -> None:
    return


def acquire_execution(label: str) -> None:
    global _gate_label
    ident = threading.get_ident()
    is_plan = bool(getattr(_plan_worker, "active", False))
    from quantlab.services.settings import max_concurrent_backtests

    with _gate_lock:
        if ident in _depths:
            _depths[ident] += 1
            return
        if _plan_reserved and not is_plan:
            raise ValueError(f"已有回测在运行（{_gate_label}），请等当前任务结束或先停止。")
        if len(_depths) >= max_concurrent_backtests():
            raise ValueError(f"已有回测在运行（{_gate_label}），请等当前任务结束或先停止。")
        _depths[ident] = 1
        if _gate_label is None:
            _gate_label = label


def release_execution() -> None:
    global _gate_label
    ident = threading.get_ident()
    with _gate_lock:
        depth = _depths.get(ident)
        if not depth:
            return
        if depth > 1:
            _depths[ident] = depth - 1
            return
        _depths.pop(ident, None)
        if not _depths and _plan_reserved is None:
            _gate_label = None


def release_reservation() -> None:
    global _gate_label, _plan_reserved
    with _gate_lock:
        _plan_reserved = None
        if not _depths:
            _gate_label = None


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
        row = connection.execute(
            "SELECT status FROM backtest_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is not None:
            current = BacktestRunStatus(row["status"])
            if current == BacktestRunStatus.QUEUED:
                next_status = transition(current, BacktestRunStatus.RUNNING).value
                connection.execute(
                    "UPDATE backtest_runs SET status=? WHERE run_id=? AND status='queued'",
                    (next_status, run_id),
                )
                current = BacktestRunStatus.RUNNING
            if current == BacktestRunStatus.RUNNING:
                next_status = transition(current, BacktestRunStatus.FAILED).value
                connection.execute(
                    "UPDATE backtest_runs SET status=?, error_message=? WHERE run_id=? AND status='running'",
                    (next_status, STOPPED_MESSAGE, run_id),
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
            target=_spawn_backtest_worker,
            args=(os.fspath(settings.project_root), settings_payload(settings), run_id),
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
