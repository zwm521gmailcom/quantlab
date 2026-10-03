from __future__ import annotations

import threading
import time
from pathlib import Path

from quantlab.services.backtest_admission import (
    RESERVE_BYTES,
    SPIKE_BYTES,
    allow_start,
    load_ceiling,
    load_finished,
    note_load_settled,
    settle_after_load,
    steady_ceiling,
)

GIB = 1024**3


def test_memory_ceiling_ignores_the_settings_slot(monkeypatch) -> None:
    from quantlab.services.backtest_admission import memory_ceiling

    monkeypatch.setenv("QUANTLAB_MAX_CONCURRENT_BACKTESTS", "2")
    assert memory_ceiling(124 * GIB) == (124 - 16) // 3
    assert memory_ceiling(16 * GIB) == 1


def test_steady_ceiling_uses_the_smaller_of_slot_cap_and_memory() -> None:
    assert steady_ceiling(124 * GIB, slot_ceiling=10) == 10
    assert steady_ceiling(124 * GIB, slot_ceiling=16) == 16
    assert steady_ceiling(16 * GIB, slot_ceiling=16) == 1


def test_first_job_starts_even_when_memory_is_tight() -> None:
    assert allow_start(running=0, loading=0, mem_available=2 * GIB, ceiling=10) is True


def test_load_ceiling_follows_installed_memory() -> None:
    assert load_ceiling(16 * GIB) == 1
    assert load_ceiling(124 * GIB) == (124 - 16) // 12


def test_second_job_waits_for_the_load_token_and_for_memory() -> None:
    assert allow_start(running=1, loading=1, mem_available=80 * GIB, ceiling=40) is True
    assert allow_start(running=1, loading=1, mem_available=RESERVE_BYTES + 2 * SPIKE_BYTES - 1, ceiling=40) is False
    assert allow_start(running=1, loading=0, mem_available=RESERVE_BYTES + SPIKE_BYTES - 1, ceiling=10) is False
    assert allow_start(running=1, loading=0, mem_available=RESERVE_BYTES + SPIKE_BYTES, ceiling=10) is True
    assert allow_start(running=10, loading=0, mem_available=80 * GIB, ceiling=10) is False


def test_load_token_returns_only_after_the_marker_or_a_long_timeout() -> None:
    assert load_finished(
        rss_bytes=3 * GIB,
        below_count=2,
        marked=False,
        since_mark_s=0,
        since_start_s=5,
        mem_available=80 * GIB,
    ) is False
    assert load_finished(
        rss_bytes=3 * GIB,
        below_count=2,
        marked=True,
        since_mark_s=0,
        since_start_s=5,
        mem_available=80 * GIB,
    ) is True
    assert load_finished(
        rss_bytes=11 * GIB,
        below_count=0,
        marked=True,
        since_mark_s=19,
        since_start_s=30,
        mem_available=80 * GIB,
    ) is False
    assert load_finished(
        rss_bytes=11 * GIB,
        below_count=0,
        marked=True,
        since_mark_s=20,
        since_start_s=30,
        mem_available=80 * GIB,
    ) is True
    assert load_finished(
        rss_bytes=11 * GIB,
        below_count=0,
        marked=True,
        since_mark_s=20,
        since_start_s=30,
        mem_available=20 * GIB,
    ) is False
    assert load_finished(
        rss_bytes=11 * GIB,
        below_count=0,
        marked=False,
        since_mark_s=0,
        since_start_s=90,
        mem_available=80 * GIB,
    ) is True


def test_second_enter_blocks_until_the_marker_and_rss_settles(tmp_path: Path) -> None:
    from quantlab.services import backtest_admission as gate

    gate.reset_admission()
    gate.set_poll_interval(0.05)
    rss = {"1": 11 * 1024**3}
    gate.set_probes(
        mem_available=lambda: 30 * 1024**3,
        rss_of=lambda pid: rss.get(str(pid), 0),
        monotonic=time.monotonic,
        ceiling=lambda: 10,
    )
    try:
        assert gate.enter(None) is True
        gate.attach(1, "run-a", tmp_path)
        started: list[str] = []

        def second() -> None:
            assert gate.enter(None) is True
            started.append("b")
            gate.leave()

        thread = threading.Thread(target=second)
        thread.start()
        time.sleep(0.2)
        rss["1"] = 3 * 1024**3
        time.sleep(0.3)
        assert started == []
        gate.note_load_settled(tmp_path, "run-a")
        thread.join(2)
        assert started == ["b"]
        gate.leave()
    finally:
        gate.reset_admission()


def test_stop_unblocks_enter() -> None:
    from quantlab.services import backtest_admission as gate

    gate.reset_admission()
    gate.set_poll_interval(0.05)
    gate.set_probes(
        mem_available=lambda: 30 * 1024**3,
        rss_of=lambda pid: 11 * 1024**3,
        monotonic=time.monotonic,
        ceiling=lambda: 10,
    )
    try:
        assert gate.enter(None) is True
        gate.attach(1, "run-a", Path("/tmp"))
        stop = threading.Event()
        result: list[bool] = []

        def waiter() -> None:
            result.append(gate.enter(stop))

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.2)
        stop.set()
        thread.join(2)
        assert result == [False]
        gate.leave()
    finally:
        gate.reset_admission()


def test_settle_after_load_writes_marker(tmp_path: Path) -> None:
    note_load_settled(tmp_path, "run-1")
    assert (tmp_path / "admission" / "run-1.settled").is_file()
    settle_after_load(tmp_path, "run-2")
    assert (tmp_path / "admission" / "run-2.settled").is_file()
