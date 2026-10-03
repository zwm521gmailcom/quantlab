"""In-process gate: concurrent reads and the steady cap both follow free memory."""

from __future__ import annotations

import ctypes
import gc
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SPIKE_BYTES = 12 * 1024**3
RESERVE_BYTES = 16 * 1024**3
STEADY_BYTES = 3 * 1024**3
SETTLE_RSS_BYTES = 4 * 1024**3
SETTLE_POLLS = 2
MARK_TIMEOUT_S = 20
START_TIMEOUT_S = 90
POLL_S = 0.5

_MemFn = Callable[[], int]
_RssFn = Callable[[int], int]
_ClockFn = Callable[[], float]
_CeilingFn = Callable[[], int]


def steady_ceiling(ram_bytes: int, slot_ceiling: int | None = None) -> int:
    if int(ram_bytes) <= RESERVE_BYTES:
        return 1
    by_mem = max(1, (int(ram_bytes) - RESERVE_BYTES) // STEADY_BYTES)
    if slot_ceiling is None:
        return by_mem
    return max(1, min(int(slot_ceiling), by_mem))


def load_ceiling(mem_bytes: int) -> int:
    """How many market-data reads fit in this much RAM after the reserve."""
    if int(mem_bytes) <= RESERVE_BYTES:
        return 1
    return max(1, (int(mem_bytes) - RESERVE_BYTES) // SPIKE_BYTES)


def allow_start(*, running: int, loading: int, mem_available: int, ceiling: int) -> bool:
    if running <= 0 and loading <= 0:
        return True
    if running >= ceiling or loading >= ceiling:
        return False
    # Budget a full spike for every read already admitted. Those reads may not
    # have reached the peak yet, so current MemAvailable does not show it.
    return int(mem_available) >= RESERVE_BYTES + SPIKE_BYTES * (int(loading) + 1)


def load_finished(
    *,
    rss_bytes: int,
    below_count: int,
    marked: bool,
    since_mark_s: float,
    since_start_s: float,
    mem_available: int,
) -> bool:
    if marked and int(rss_bytes) <= SETTLE_RSS_BYTES and int(below_count) >= SETTLE_POLLS:
        return True
    enough = int(mem_available) >= RESERVE_BYTES + SPIKE_BYTES
    if marked and float(since_mark_s) >= MARK_TIMEOUT_S and enough:
        return True
    if (not marked) and float(since_start_s) >= START_TIMEOUT_S and enough:
        return True
    return False


_slot_override: int | None = None


def set_slot_override_for_test(limit: int | None) -> None:
    global _slot_override
    _slot_override = None if limit is None else int(limit)


def memory_ceiling(ram_bytes: int | None = None) -> int:
    """Steady cap from installed RAM. Settings and QUANTLAB_MAX_CONCURRENT_BACKTESTS are not consulted."""
    if ram_bytes is None:
        from quantlab.services.settings import total_ram_bytes

        ram_bytes = int(total_ram_bytes() or 0)
    if _slot_override is None:
        return steady_ceiling(int(ram_bytes))
    return steady_ceiling(int(ram_bytes), int(_slot_override))


def _default_ceiling() -> int:
    return memory_ceiling()


def _read_mem_available() -> int:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024
                except ValueError:
                    return 0
    return 0


def _read_rss(pid: int) -> int:
    try:
        text = Path(f"/proc/{int(pid)}/status").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024
                except ValueError:
                    return 0
    return 0


def _pid_status_exists(pid: int) -> bool:
    try:
        return Path(f"/proc/{int(pid)}/status").is_file()
    except OSError:
        return False


@dataclass
class _Holder:
    pid: int | None = None
    run_id: str | None = None
    runtime_root: Path | None = None
    loading: bool = True
    started: float = 0.0
    marked_at: float | None = None
    below_count: int = 0


@dataclass
class _Probes:
    mem_available: _MemFn
    rss_of: _RssFn
    monotonic: _ClockFn
    ceiling: _CeilingFn


def _fresh_probes() -> _Probes:
    return _Probes(
        mem_available=_read_mem_available,
        rss_of=_read_rss,
        monotonic=time.monotonic,
        ceiling=_default_ceiling,
    )


_lock = threading.Lock()
_condition = threading.Condition(_lock)
_holders: dict[int, _Holder] = {}
_probes = _fresh_probes()
_poll_s = POLL_S
_stop = threading.Event()
_generation = 0
_supervisor: threading.Thread | None = None


def _mem_available() -> int:
    with _lock:
        fn = _probes.mem_available
    try:
        return int(fn())
    except Exception:
        return 0


def _clock() -> float:
    with _lock:
        fn = _probes.monotonic
    return float(fn())


def _ceiling() -> int:
    with _lock:
        fn = _probes.ceiling
    return int(fn())


def _marker_path(runtime_root: Path, run_id: str) -> Path:
    return Path(runtime_root) / "admission" / f"{run_id}.settled"


def _supervise(generation: int) -> None:
    while True:
        with _lock:
            if _stop.is_set() or generation != _generation:
                return
            poll = _poll_s
        try:
            _tick(generation)
        except Exception:
            pass
        with _lock:
            if _stop.is_set() or generation != _generation:
                return
            _condition.wait(poll)


def _tick(generation: int) -> None:
    mem = _mem_available()
    now = _clock()
    with _lock:
        if generation != _generation:
            return
        rss_fn = _probes.rss_of
        snapshot = [
            (
                ident,
                holder.pid,
                holder.run_id,
                holder.runtime_root,
                holder.marked_at,
                holder.loading,
            )
            for ident, holder in _holders.items()
        ]
    observations: list[tuple[int, float | None, int | None]] = []
    for ident, pid, run_id, runtime_root, marked_at, loading in snapshot:
        if not loading:
            continue
        seen = marked_at
        if seen is None and run_id and runtime_root is not None:
            try:
                if _marker_path(runtime_root, run_id).is_file():
                    seen = now
            except OSError:
                seen = marked_at
        marked = seen is not None
        rss: int | None
        if pid is None:
            rss = None
        else:
            try:
                raw = int(rss_fn(int(pid)))
            except Exception:
                raw = 0
            missing = raw == 0 and not _pid_status_exists(int(pid))
            if missing and not marked:
                rss = None
            else:
                rss = 0 if missing else raw
        observations.append((ident, seen, rss))
    with _lock:
        if generation != _generation:
            return
        for ident, seen, rss in observations:
            holder = _holders.get(ident)
            if holder is None or not holder.loading:
                continue
            if seen is not None and holder.marked_at is None:
                holder.marked_at = seen
            marked = holder.marked_at is not None
            if rss is None:
                rss_bytes = SETTLE_RSS_BYTES + 1
                below = holder.below_count
            else:
                if marked and rss <= SETTLE_RSS_BYTES:
                    holder.below_count += 1
                else:
                    holder.below_count = 0
                rss_bytes = rss
                below = holder.below_count
            since_mark = (now - holder.marked_at) if holder.marked_at is not None else 0.0
            since_start = now - holder.started
            if load_finished(
                rss_bytes=rss_bytes,
                below_count=below,
                marked=marked,
                since_mark_s=since_mark,
                since_start_s=since_start,
                mem_available=mem,
            ):
                holder.loading = False
        _condition.notify_all()


def _ensure_supervisor() -> None:
    global _supervisor
    with _lock:
        if _supervisor is not None and _supervisor.is_alive():
            return
        generation = _generation
        _stop.clear()
        thread = threading.Thread(
            target=_supervise,
            args=(generation,),
            name="quantlab-admission",
            daemon=True,
        )
        _supervisor = thread
        thread.start()


def enter(stop: threading.Event | None) -> bool:
    _ensure_supervisor()
    ident = threading.get_ident()
    while True:
        if stop is not None and stop.is_set():
            return False
        mem = _mem_available()
        with _lock:
            if _stop.is_set() and stop is not None and stop.is_set():
                return False
            if stop is not None and stop.is_set():
                return False
            if ident in _holders:
                return True
            running = len(_holders)
            loading = sum(1 for holder in _holders.values() if holder.loading)
            ceiling = int(_probes.ceiling())
            if allow_start(running=running, loading=loading, mem_available=mem, ceiling=ceiling):
                _holders[ident] = _Holder(loading=True, started=float(_probes.monotonic()))
                return True
            _condition.wait(_poll_s)


def leave() -> None:
    with _lock:
        _holders.pop(threading.get_ident(), None)
        _condition.notify_all()


def holds() -> bool:
    with _lock:
        return threading.get_ident() in _holders


def attach(pid: int, run_id: str, runtime_root: Path) -> None:
    with _lock:
        holder = _holders.get(threading.get_ident())
        if holder is None:
            return
        holder.pid = int(pid)
        holder.run_id = str(run_id)
        holder.runtime_root = Path(runtime_root)


def note_load_settled(runtime_root: Path, run_id: str) -> None:
    path = _marker_path(runtime_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def settle_after_load(runtime_root: Path, run_id: str) -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    note_load_settled(runtime_root, run_id)


def mark_holder_settled_for_test() -> None:
    with _lock:
        holder = _holders.get(threading.get_ident())
        if holder is None:
            return
        holder.loading = False
        _condition.notify_all()


def snapshot() -> dict[str, int | bool]:
    mem = _mem_available()
    with _lock:
        running = len(_holders)
        loading = sum(1 for holder in _holders.values() if holder.loading)
        limit = int(_probes.ceiling())
        can_start = allow_start(
            running=running,
            loading=loading,
            mem_available=mem,
            ceiling=limit,
        )
        reads = load_ceiling(mem)
    return {
        "running": running,
        "loading": loading,
        "limit": limit,
        "load_limit": reads,
        "can_start": can_start,
    }


def set_probes(
    *,
    mem_available: _MemFn,
    rss_of: _RssFn,
    monotonic: _ClockFn,
    ceiling: _CeilingFn,
) -> None:
    global _probes
    with _lock:
        _probes = _Probes(
            mem_available=mem_available,
            rss_of=rss_of,
            monotonic=monotonic,
            ceiling=ceiling,
        )
        _condition.notify_all()


def set_poll_interval(seconds: float) -> None:
    global _poll_s
    with _lock:
        _poll_s = float(seconds)
        _condition.notify_all()


def reset_admission() -> None:
    global _probes, _poll_s, _generation, _supervisor, _slot_override
    with _lock:
        _stop.set()
        _generation += 1
        thread = _supervisor
        _supervisor = None
        _condition.notify_all()
    if thread is not None and thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=2)
    with _lock:
        _holders.clear()
        _slot_override = None
        _probes = _fresh_probes()
        _poll_s = POLL_S
        _stop.clear()
        _condition.notify_all()
