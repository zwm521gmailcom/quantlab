"""Per-run compute budget: RSS samples, signature priors, and worker caps."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from contextvars import ContextVar
from os import cpu_count
from typing import Any

from quantlab.services.machine_identity import load_machine_identity
from quantlab.services.settings import (
    FOLD_PROCESS_BYTES,
    memory_safe_process_workers,
    resolve_bucket_pool,
    total_ram_bytes,
)

_ACTIVE_RESOURCE_PRIOR: ContextVar[dict[str, Any] | None] = ContextVar(
    "quantlab_resource_prior",
    default=None,
)
_ACTIVE_RESOURCE_NOTES: ContextVar[dict[str, Any] | None] = ContextVar(
    "quantlab_resource_notes",
    default=None,
)


def run_signature(config: dict[str, Any] | None) -> str:
    config = config if isinstance(config, dict) else {}
    train = config.get("train") if isinstance(config.get("train"), dict) else {}
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    walk_raw = config.get("walk_forward")
    walk_mode = str(walk_raw.get("mode") or "") if isinstance(walk_raw, dict) else str(walk_raw or "")
    payload = {
        "dataset_id": str(config.get("dataset_id") or ""),
        "dataset_version_id": str(config.get("dataset_version_id") or ""),
        "kind": str(config.get("kind") or ""),
        "train_from": str(train.get("date_from") or ""),
        "test_from": str(test.get("date_from") or ""),
        "test_to": str(test.get("date_to") or ""),
        "walk": walk_mode or str(config.get("walk_forward_mode") or ""),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def cap_workers(
    *,
    requested: int,
    task_count: int,
    ram_bytes: int,
    unit_bytes: int,
    kind: str,
    prior_peak_rss_bytes: int = 0,
    prior_workers: int = 0,
) -> int:
    requested = max(1, min(int(requested), max(1, int(task_count))))
    if kind == "fold":
        if prior_peak_rss_bytes and ram_bytes and prior_peak_rss_bytes > ram_bytes * 0.45:
            return 1
        cost = int(FOLD_PROCESS_BYTES)
        if prior_peak_rss_bytes and prior_workers:
            cost = max(cost, int(prior_peak_rss_bytes) // max(1, int(prior_workers)))
        elif prior_peak_rss_bytes:
            cost = max(cost, int(prior_peak_rss_bytes))
        return memory_safe_process_workers(cost, requested, ram=ram_bytes)
    cost = max(int(unit_bytes or 0), 0)
    if prior_peak_rss_bytes and prior_workers:
        cost = max(cost, int(prior_peak_rss_bytes) // max(1, int(prior_workers)))
    return memory_safe_process_workers(cost, requested, ram=ram_bytes)


def last_resource_sample(database: Any, signature: str, machine_id: str | None = None) -> dict[str, Any] | None:
    wanted = str(signature or "")
    if not wanted:
        return None
    try:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT metrics_json FROM backtest_runs "
                "WHERE status='completed' ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
    except Exception:
        return None
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (TypeError, json.JSONDecodeError, KeyError):
            continue
        if not isinstance(metrics, dict):
            continue
        resources = metrics.get("resources")
        if not isinstance(resources, dict):
            continue
        if str(resources.get("signature") or "") != wanted:
            continue
        if machine_id:
            seen = str(resources.get("machine_id") or "")
            if seen and seen != str(machine_id):
                continue
        return resources
    return None


def _rss_kb(pid: int) -> int:
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return 0
    text = (completed.stdout or "").strip()
    if not text:
        return 0
    try:
        return int(text.split()[0])
    except ValueError:
        return 0


def _child_pids(pid: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    out: list[int] = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return out


def process_tree_rss_bytes(pid: int | None = None) -> int:
    root = int(pid if pid is not None else os.getpid())
    seen: set[int] = set()
    total_kb = 0
    stack = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        total_kb += _rss_kb(current)
        stack.extend(_child_pids(current))
    return total_kb * 1024


class ResourceSampler:
    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self._peak = 0
        self._started: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> ResourceSampler:
        self._started = time.monotonic()
        self._peak = process_tree_rss_bytes()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            rss = process_tree_rss_bytes()
            if rss > self._peak:
                self._peak = rss

    def snapshot(self) -> dict[str, Any]:
        rss = process_tree_rss_bytes()
        if rss > self._peak:
            self._peak = rss
        elapsed = 0
        if self._started is not None:
            elapsed = int((time.monotonic() - self._started) * 1000)
        return {"peak_rss_bytes": int(self._peak), "elapsed_ms": elapsed}

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        return self.snapshot()


def active_resource_prior() -> dict[str, Any] | None:
    prior = _ACTIVE_RESOURCE_PRIOR.get()
    return dict(prior) if isinstance(prior, dict) else None


def set_resource_prior(prior: dict[str, Any] | None):
    return _ACTIVE_RESOURCE_PRIOR.set(prior if isinstance(prior, dict) else None)


def reset_resource_prior(token) -> None:
    _ACTIVE_RESOURCE_PRIOR.reset(token)


def note_workers(**values: Any) -> None:
    current = dict(_ACTIVE_RESOURCE_NOTES.get({}) or {})
    for key, value in values.items():
        if value is None:
            continue
        current[key] = value
    _ACTIVE_RESOURCE_NOTES.set(current)


def resource_notes() -> dict[str, Any]:
    return dict(_ACTIVE_RESOURCE_NOTES.get({}) or {})


def set_resource_notes(notes: dict[str, Any] | None):
    return _ACTIVE_RESOURCE_NOTES.set(dict(notes or {}))


def reset_resource_notes(token) -> None:
    _ACTIVE_RESOURCE_NOTES.reset(token)


def attach_resource_sample(
    sample: dict[str, Any] | None,
    *,
    config: dict[str, Any] | None,
    frame_nbytes: int = 0,
    fold_workers: int = 0,
    bucket_workers: int = 0,
    bucket_pool: str | None = None,
) -> dict[str, Any]:
    sample = sample if isinstance(sample, dict) else {}
    notes = resource_notes()
    pool = bucket_pool or notes.get("bucket_pool") or resolve_bucket_pool()
    config = config if isinstance(config, dict) else {}
    return {
        "peak_rss_bytes": int(sample.get("peak_rss_bytes") or 0),
        "elapsed_ms": int(sample.get("elapsed_ms") or 0),
        "frame_nbytes": int(frame_nbytes or notes.get("frame_nbytes") or 0),
        "fold_workers": int(fold_workers or notes.get("fold_workers") or 0),
        "bucket_workers": int(bucket_workers or notes.get("bucket_workers") or 0),
        "bucket_pool": str(pool or "process"),
        "cpu_count": int(cpu_count() or 1),
        "ram_bytes": int(total_ram_bytes() or 0),
        "signature": run_signature(config),
        "machine_id": str(config.get("machine_id") or notes.get("machine_id") or ""),
    }


def persist_run_resources(
    database: Any,
    run_id: str,
    config: dict[str, Any] | None,
    sample: dict[str, Any] | None,
    *,
    settings: Any = None,
    previous_status: str | None = None,
) -> dict[str, Any] | None:
    if previous_status not in {None, "queued", "failed"}:
        return None
    config = dict(config) if isinstance(config, dict) else {}
    if settings is not None and not config.get("machine_id"):
        config["machine_id"] = load_machine_identity(settings.runtime_root).get("machine_id")
    resources = attach_resource_sample(sample, config=config)
    try:
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT status, metrics_json FROM backtest_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or str(row["status"] or "") not in {"completed", "failed"}:
                return None
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            if not isinstance(metrics, dict):
                metrics = {}
            metrics["resources"] = resources
            connection.execute(
                "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
                (json.dumps(metrics, ensure_ascii=False, sort_keys=True), run_id),
            )
    except Exception:
        return resources
    if settings is not None:
        path = settings.runtime_root / "results" / run_id / "metrics.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["resources"] = resources
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return resources
