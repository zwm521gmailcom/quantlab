# 回测加载错峰与内存放行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 计划里的回测继续排队，但同一时刻只允许一笔处在读行情的峰值里；峰值过去后按当时剩余内存决定能不能再放下一笔，稳定阶段的笔数不超过现有硬上限。

**Architecture:** 父进程里加一个进程内的放行门 `backtest_admission`。工作线程在 `spawn` 之前取得「稳态名额 + 加载令牌」。子进程把行情读完并尝试把内存还给系统后，只写一个标记文件；父进程的监督线程读这个标记和该子进程的 `VmRSS`，再决定交还加载令牌。稳态名额要等进程结束才还。`MemAvailable` 不够下一次峰值时，即使加载令牌空了也不放下一笔。单笔工作台执行和计划执行走同一扇门。

**Tech Stack:** 现有 FastAPI / SQLite、`multiprocessing.spawn`、`/proc/meminfo` 的 `MemAvailable`、`/proc/<pid>/status` 的 `VmRSS`。不引入 psutil。不改撮合、训练和因子公式。

## Global Constraints

- 加载令牌同时最多 1 个。
- 没有任何回测在跑时，允许启动第 1 笔，避免 16GB 机器永远开不了工。
- 已经有回测在跑时，下一笔要同时满足：加载令牌空闲、在跑笔数小于稳态上限、`MemAvailable >= 16GiB + 12GiB`。
- 稳态上限 = `min(16, max_concurrent_backtests(), 按总内存估算的笔数)`。`max_concurrent_backtests()` 仍是环境变量与硬件公式的硬顶，本计划不改那个公式。
- 环境变量 `QUANTLAB_MAX_CONCURRENT_BACKTESTS` 只做硬顶。它不再表示「一起启动这么多笔」。本机当前进程里这个值是 10，所以不重启的话代码不会生效；重启后若仍是 10，稳定阶段最多 10 笔，但不会 10 笔同时读行情。要把稳定阶段的硬顶放到代码允许的 16，重启时设为 16 或删掉该变量。
- 加载令牌的交还必须先看到标记文件。刚启动时 RSS 本来就低，不能把这段当成峰值已过。标记写出之后，`VmRSS <= 4GiB` 连续两拍就交还。标记写出后超过 20 秒、RSS 仍高，只有 `MemAvailable` 仍够下一次峰值才交还。一直没有标记时，满 90 秒并且内存仍够才交还，用来兜住没写上标记的进程。进程退出时稳态名额和加载令牌一起交还。
- `enter` 使用的上限就是 `steady_ceiling`，不要用没套内存项的 `max_concurrent_backtests()`。
- `acquire_execution` 留在 `run_isolated` 里，并且发生在 `enter` 成功之后。等门的线程不算稳态名额。
- 不按每进程 136 个线程去除核数。那些线程在内存回落后仍然挂着，但不是 136 个忙核。第一版不因此把并发打成 1。
- 内存紧时只停止放行，不杀已经在跑的进程。
- 子进程不能调用父进程函数。它只写标记文件。
- 计划任务在拿到放行门之前保持 `queued`。拿到门之后才写成 `running`。
- 停止必须唤醒正在等门的线程。等门失败时不要留下已经提交、却没有执行的 `backtest_runs` 行。
- `acquire_execution` 使用的名额和计划线程池宽度都等于稳态上限，避免第 N 笔被「已有回测在运行」打成失败。
- 父进程放行不使用子进程里的 `ResourceSampler`。那个采样把进程树 RSS 加总，并且要等回测结束才落盘。
- 不改折训练、分层净值的工人封顶。`docs/plans/2026-09-10-backtest-resource-auto-tune.md` 里「全局一次只跑一条」已被计划内并行取代；本计划只取代「按硬顶同时启动」。
- 执行本计划后的服务重启，必须先确认没有 `status=running` 的回测计划。当前 `plan-081cf60874b4` 正在跑，写代码和跑测试可以做，重启服务不行。
- 工作区不主动 `git commit`。测试用 `/home/zwm521/桌面/quantlab/.venv/bin/pytest`。

## 文件地图

| 文件 | 职责 |
|---|---|
| Create: `quantlab/services/backtest_admission.py` | 纯判断、放行门、监督线程、标记文件 |
| Create: `tests/quantlab/test_backtest_admission.py` | 判断表和门的单测 |
| Modify: `quantlab/services/backtest_control.py` | `run_isolated` 在 `spawn` 前进入门，并登记子进程 pid |
| Modify: `quantlab/services/backtest_plan.py` | 线程池宽度改为稳态上限；任务先排队，拿到门再标运行 |
| Modify: `quantlab/services/backtest_job.py` | `_load_frame` 之后归还内存并写标记 |
| Modify: `quantlab/web/assets/settings.js` | 说明加载错峰，不再说成一起启动 N 条 |
| Modify: `quantlab/web/pages/backtest_plan.html` | 同一句说明 |
| Modify: `quantlab/web/assets/backtest/plan.js` | 状态行写出正在跑、读数据、上限 |
| Modify: `quantlab/services/backtest_plan.py` 的 `get` | 附上 `admission` 快照 |
| Modify: `tests/quantlab/test_backtest_plan.py` | 两笔重叠必须发生在第一笔交还加载令牌之后 |
| Modify: `tests/quantlab/test_settings_page.py` | 跟上新文案 |

## 常量

放在 `backtest_admission.py` 模块顶部，测试从这里导入，禁止在别的文件再写一套数字。

- `SPIKE_BYTES = 12 * 1024**3`：再放一笔读行情时按 12GB 预算。实测峰值约 11GB。
- `RESERVE_BYTES = 16 * 1024**3`：留给系统和页面缓存。
- `STEADY_BYTES = 3 * 1024**3`：读完后的常驻按 3GB 估算。实测约 2.6GB。只用于算硬顶里的内存项，放行本身看 `MemAvailable`。
- `SETTLE_RSS_BYTES = 4 * 1024**3`：低于此值视为这一笔的读数峰值已经过去。
- `SETTLE_POLLS = 2`
- `MARK_TIMEOUT_S = 20`
- `START_TIMEOUT_S = 90`
- `POLL_S = 0.5`

---

### Task 1: 放行判断是纯函数

**Files:**
- Create: `quantlab/services/backtest_admission.py`
- Create: `tests/quantlab/test_backtest_admission.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `steady_ceiling(ram_bytes: int, slot_ceiling: int) -> int`
  - `allow_start(*, running: int, loading: int, mem_available: int, ceiling: int) -> bool`
  - `load_finished(*, rss_bytes: int, below_count: int, marked: bool, since_mark_s: float, since_start_s: float, mem_available: int) -> bool`

- [ ] **Step 1: 写失败测试**

```python
from quantlab.services.backtest_admission import (
    RESERVE_BYTES,
    SPIKE_BYTES,
    allow_start,
    load_finished,
    steady_ceiling,
)

GIB = 1024**3


def test_steady_ceiling_uses_the_smaller_of_slot_cap_and_memory() -> None:
    assert steady_ceiling(124 * GIB, slot_ceiling=10) == 10
    assert steady_ceiling(124 * GIB, slot_ceiling=16) == 16
    assert steady_ceiling(16 * GIB, slot_ceiling=16) == 1


def test_first_job_starts_even_when_memory_is_tight() -> None:
    assert allow_start(running=0, loading=0, mem_available=2 * GIB, ceiling=10) is True


def test_second_job_waits_for_the_load_token_and_for_memory() -> None:
    assert allow_start(running=1, loading=1, mem_available=80 * GIB, ceiling=10) is False
    assert allow_start(running=1, loading=0, mem_available=RESERVE_BYTES + SPIKE_BYTES - 1, ceiling=10) is False
    assert allow_start(running=1, loading=0, mem_available=RESERVE_BYTES + SPIKE_BYTES, ceiling=10) is True
    assert allow_start(running=10, loading=0, mem_available=80 * GIB, ceiling=10) is False


def test_load_token_returns_only_after_the_marker_or_a_long_timeout() -> None:
    assert load_finished(rss_bytes=3 * GIB, below_count=2, marked=False, since_mark_s=0, since_start_s=5, mem_available=80 * GIB) is False
    assert load_finished(rss_bytes=3 * GIB, below_count=2, marked=True, since_mark_s=0, since_start_s=5, mem_available=80 * GIB) is True
    assert load_finished(rss_bytes=11 * GIB, below_count=0, marked=True, since_mark_s=19, since_start_s=30, mem_available=80 * GIB) is False
    assert load_finished(rss_bytes=11 * GIB, below_count=0, marked=True, since_mark_s=20, since_start_s=30, mem_available=80 * GIB) is True
    assert load_finished(rss_bytes=11 * GIB, below_count=0, marked=True, since_mark_s=20, since_start_s=30, mem_available=20 * GIB) is False
    assert load_finished(rss_bytes=11 * GIB, below_count=0, marked=False, since_mark_s=0, since_start_s=90, mem_available=80 * GIB) is True
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py -v`

Expected: FAIL，`backtest_admission` 尚不存在。

- [ ] **Step 3: 实现这三个纯函数**

`steady_ceiling`：`ram_bytes <= RESERVE_BYTES` 时返回 1。否则 `by_mem = max(1, (ram_bytes - RESERVE_BYTES) // STEADY_BYTES)`，返回 `max(1, min(16, int(slot_ceiling), by_mem))`。

`allow_start`：`running <= 0 and loading <= 0` 时返回 True。`loading >= 1` 或 `running >= ceiling` 时返回 False。否则返回 `mem_available >= RESERVE_BYTES + SPIKE_BYTES`。

`load_finished`：`marked` 为真，并且 `rss_bytes <= SETTLE_RSS_BYTES`、`below_count >= SETTLE_POLLS` 时返回 True。没有标记时，低 RSS 一律返回 False，避免进程刚启动、行情还没读就被放行。`marked` 为真、`since_mark_s >= MARK_TIMEOUT_S`、并且 `mem_available >= RESERVE_BYTES + SPIKE_BYTES` 时返回 True。没有标记但 `since_start_s >= START_TIMEOUT_S` 且内存仍够下一次峰值时返回 True。其余返回 False。

- [ ] **Step 4: 再跑，确认通过**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py -v`

Expected: PASS

---

### Task 2: 进程内的门和监督线程

**Files:**
- Modify: `quantlab/services/backtest_admission.py`
- Modify: `tests/quantlab/test_backtest_admission.py`

**Interfaces:**
- Consumes: Task 1 的 `allow_start`、`load_finished`、`steady_ceiling`
- Produces:
  - `enter(stop: threading.Event | None) -> bool`
  - `leave() -> None`
  - `holds() -> bool`
  - `attach(pid: int, run_id: str, runtime_root: Path) -> None`
  - `note_load_settled(runtime_root: Path, run_id: str) -> None`
  - `snapshot() -> dict[str, int | bool]`
  - `reset_admission() -> None`
  - `set_probes(*, mem_available, rss_of, monotonic, ceiling) -> None`

门的状态在一把锁里：

- `holders: dict[int, Holder]`，键是线程 id。`Holder` 含 `pid`、`run_id`、`loading`、`started`、`marked_at`、`below_count`。
- 监督线程是 daemon。`enter` 第一次被调用时拉起。它每 `POLL_S` 做三件事：读 `mem_available()`；对每个已有 pid 的 holder 读 `rss_of(pid)`，按 `load_finished` 把 `loading` 设为 False；`notify_all`。
- 读 `/proc` 放在锁外。改 `holders` 时回到锁内。pid 读不到时不要当成 RSS 0。没有标记就继续占着加载令牌，等 `leave()`；已经有标记才按 RSS 0 走低内存交还。稳态名额始终等 `leave()`。
- `enter` 在锁内循环。`stop` 已置位则返回 False。`allow_start(running=当前 holder 数, loading=loading 为 True 的个数, mem_available, ceiling)` 为真时，把自己放进 `holders` 且 `loading=True`，返回 True。否则 `condition.wait(POLL_S)`。
- `leave` 删掉当前线程的 holder。线程还没有 `enter` 成功时调用 `leave` 什么都不做。
- `holds` 看当前线程是否在 `holders` 里。
- `attach` 给当前线程的 holder 填 `pid`、`run_id`、`runtime_root`。重复 attach 覆盖。监督线程只在这个目录里找 `admission/{run_id}.settled`。
- `note_load_settled` 把 `runtime_root/admission/{run_id}.settled` 写成空文件。目录不存在就创建。这是子进程唯一要调用的函数，里面不准碰 `holders`。
- 监督线程看到标记文件后填写该 holder 的 `marked_at`。文件还不存在就当 `marked=False`。`below_count` 只在 `marked` 为真且 RSS 低于阈值时累加，RSS 一旦回到阈值以上就清零。
- `snapshot` 返回 `running`、`loading`、`limit`、`can_start`。`limit` 和 `enter` 的上限都用 `steady_ceiling(total_ram_bytes(), max_concurrent_backtests())`。`can_start` 用当前状态调用 `allow_start`。
- `reset_admission` 停掉监督线程、清空 holder、清掉探针，并把轮询间隔设回 0.5 秒。每个测试的 `finally` 都调用它。
- 探针默认：`mem_available` 读 `/proc/meminfo` 的 `MemAvailable`（kB 乘 1024）；`rss_of` 读 `/proc/<pid>/status` 的 `VmRSS`；读不到返回 0。`ceiling` 默认就是上面的 `steady_ceiling`。`monotonic` 默认 `time.monotonic`。`set_poll_interval(seconds)` 只给测试把监督间隔调短。

- [ ] **Step 1: 写失败测试**

```python
import threading
import time
from pathlib import Path

from quantlab.services import backtest_admission as gate


def test_second_enter_blocks_until_the_marker_and_rss_settles(tmp_path: Path) -> None:
    gate.reset_admission()
    gate.set_poll_interval(0.05)
    rss = {"1": 11 * 1024**3}
    gate.set_probes(
        mem_available=lambda: 80 * 1024**3,
        rss_of=lambda pid: rss.get(str(pid), 0),
        monotonic=time.monotonic,
        ceiling=lambda: 10,
    )
    try:
        assert gate.enter(None) is True
        gate.attach(1, "run-a", tmp_path)
        started = []

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
    gate.reset_admission()
    gate.set_poll_interval(0.05)
    gate.set_probes(
        mem_available=lambda: 80 * 1024**3,
        rss_of=lambda pid: 11 * 1024**3,
        monotonic=time.monotonic,
        ceiling=lambda: 10,
    )
    try:
        assert gate.enter(None) is True
        gate.attach(1, "run-a", Path("/tmp"))
        stop = threading.Event()
        result = []

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
```

`test_second_enter_blocks_until_the_marker_and_rss_settles` 先把 RSS 降到 3GB 但不写标记，第二笔必须仍然停着。写出标记之后，监督线程连续两拍看到低 RSS，才放第二笔进来。

- [ ] **Step 2: 跑新增测试，确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py -v`

Expected: FAIL，`enter` 尚不存在。

- [ ] **Step 3: 实现门**

按上面的状态和接口实现。`test_second_enter` 不写标记文件，走 RSS 连续两拍这条路径。两拍的时间用注入的 `monotonic` 相减；测试里把 `now["t"]` 设到 2.0 后，监督线程下一拍要看到 `below_count` 增加到 2。实现时不要用墙钟判断「连续两拍」，用「RSS 低于阈值的连续监督次数」。

- [ ] **Step 4: 再跑，确认通过**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py -v`

Expected: PASS

---

### Task 3: 子进程读完行情后写标记

**Files:**
- Modify: `quantlab/services/backtest_job.py`（`_execute_core` 里 `frame = self._load_frame(path, config)` 之后，约 777 行）
- Modify: `tests/quantlab/test_backtest_admission.py`

**Interfaces:**
- Consumes: `note_load_settled(runtime_root, run_id)`
- Produces: 标记文件 `runtime_root/admission/{run_id}.settled`

- [ ] **Step 1: 写失败测试**

用临时目录调用 `note_load_settled(tmp_path, "run-1")`，断言 `tmp_path/admission/run-1.settled` 存在。再把同一函数接到一段最小包装：`settle_after_load(runtime_root, run_id)`，它先 `gc.collect()`，再尝试 `ctypes.CDLL("libc.so.6").malloc_trim(0)`，失败就忽略，然后写标记。测试只断言标记文件出现，不断言 RSS 真的下降。

- [ ] **Step 2: 跑测试，确认 `settle_after_load` 不存在**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py::test_settle_after_load_writes_marker -v`

Expected: FAIL

- [ ] **Step 3: 在 `_execute_core` 读帧之后调用**

`qlib` 与普通一次训练都走 `_execute_core` 的 `self._load_frame`。就在这一行之后调用 `settle_after_load(self.settings.runtime_root, run_id)`，然后再 `note_workers(frame_nbytes=...)`。滚动折和因子排名如果全程占着大帧，标记仍然要写；RSS 不回落时，交不交加载令牌由父进程的 20 秒超时加 `MemAvailable` 决定。

`rule_signal` 不走 `_execute_core`。在 `quantlab/services/rule_backtest.py` 的 `frame = _load_parquet_window(...)` 之后同样调用 `settle_after_load`。不写标记的话，规则回测会把加载令牌占到 90 秒。

读行情本身若超过 90 秒，并且当时空闲内存仍不少于 28GB，兜底会允许下一笔开始读。这轮实测的读数峰值远短于 90 秒。以后若读数变长，只调大 `START_TIMEOUT_S`，不要去掉内存检查。

- [ ] **Step 4: 跑 admission 测试和一条现有回测单测**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py tests/quantlab/test_backtest_plan.py::test_start_runs_selected_items_serially -v`

Expected: PASS。串行计划仍是第一笔结束后第二笔才开始。

---

### Task 4: 计划和单笔执行都在 spawn 前进入这扇门

**Files:**
- Modify: `quantlab/services/backtest_control.py` 的 `run_isolated`
- Modify: `quantlab/services/backtest_plan.py` 的 `_run_loop`、`_run_item`
- Modify: `quantlab/services/backtest_control.py` 的 `acquire_execution`，名额改读 `steady_ceiling(total_ram_bytes(), max_concurrent_backtests())`
- Modify: `tests/quantlab/test_backtest_plan.py`

**Interfaces:**
- Consumes: `enter`、`leave`、`holds`、`attach`、`steady_ceiling`
- Produces: 计划线程池宽度等于稳态上限；工作台 `POST /api/backtests/{run_id}/execute` 不改路由，因为 `run_isolated` 自己会 `enter`

`_run_item` 的顺序改成：

1. `stop` 已置位就返回，任务保持 `queued`。
2. `admission.enter(stop)` 返回 False 就返回，任务保持 `queued`。此时还没有 `workbench.submit`，所以没有孤儿 `backtest_runs`。
3. `enter` 成功后才把任务写成 `running` 并写 `started_at`。
4. `submit` 得到 `run_id`。
5. 调用 `run_isolated`。`run_isolated` 看见 `admission.holds()` 为真，就不再 `enter`，也不在自己的 `finally` 里 `leave`。
6. `_run_item` 的 `finally` 调用 `admission.leave()`。

`run_isolated` 在真正 `proc.start()` 之后立刻 `admission.attach(proc.pid, run_id, settings.runtime_root)`。`acquire_execution` 仍留在 `run_isolated` 里，并且放在 `enter` 之后：计划路径是 `_run_item` 先 `enter`，再进 `run_isolated` 才占执行名额。停在 `enter` 里的线程还不占名额，也不会把工作台那条提前打成「已有回测在运行」之外的失败。工作台那条没有计划任务的路径里，`holds()` 为假，由 `run_isolated` 自己 `enter` / `leave`。`enter` 失败时按现有停止语义把这条 run 标成停止，返回 `job.get(run_id)`。计划路径不会在 `run_isolated` 里 `enter` 失败，因为已经持有门。

`_run_loop` 的 `ThreadPoolExecutor(max_workers=workers)` 里，`workers` 改为 `steady_ceiling(total_ram_bytes(), max_concurrent_backtests())`。不要在循环开始时把全部 future 都放进「已运行」。线程池可以一次性 `submit` 全部 item id，池宽本身就是稳态上限；还没排到线程的任务留在池内队列，数据库状态保持 `queued`。

`acquire_execution` 的 `len(_depths) >= max_concurrent_backtests()` 改成 `>= steady_ceiling(...)`。计划工人和这个判断用同一个数字。

- [ ] **Step 1: 改并行测试的预期**

`test_start_runs_two_items_in_parallel_when_machine_allows_two` 现在在 `run_isolated` 里用 `Barrier(2)`，两笔会同时进入。加载门会让第二笔停在 `enter`，屏障超时。把替身改成：

```python
def together(settings, job, run_id):
    from quantlab.services import backtest_admission as gate
    gate.mark_holder_settled_for_test()
    barrier.wait()
    return original(settings, job, run_id)
```

`mark_holder_settled_for_test` 只给测试用：把当前线程 holder 的 `loading` 设为 False 并 `notify_all`。生产路径不调用它。生产路径靠监督线程看见标记文件和 RSS。只写标记文件不会让这个替身放行，因为测试里没有真的子进程 RSS。这个替身证明：第一笔交还加载令牌之后，第二笔才能进入 `run_isolated`，然后两笔可以同时停在屏障里。断言仍是两笔都完成，且两笔的运行区间重叠。另外断言第一笔 `started_at <= 第二笔 started_at`。

串行测试 `finished_at[0] <= started_at[1]` 保持不变。`slots=1` 时稳态上限是 1，第二笔本来就要等第一笔整个结束。

- [ ] **Step 2: 跑这两条测试，确认新替身还对不上实现**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_plan.py::test_start_runs_selected_items_serially tests/quantlab/test_backtest_plan.py::test_start_runs_two_items_in_parallel_when_machine_allows_two tests/quantlab/test_backtest_plan.py::test_plan_workers_can_hold_two_slots_and_still_block_workbench -v`

Expected: 并行那条先失败或超时。实现 Step 3 之后变为 PASS。名额测试仍是 PASS：稳态上限 2 时，第三个 `acquire_execution` 继续被拒绝。

- [ ] **Step 3: 按上面的顺序改 `_run_item`、`run_isolated`、`acquire_execution`、池宽**

计划开始时现有代码已经把选中任务写成 `queued`。不要在 `enter` 之前写成 `running`。

- [ ] **Step 4: 再跑 Task 4 的三条测试和 admission 测试**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py tests/quantlab/test_backtest_plan.py -v`

Expected: PASS

---

### Task 5: 界面写出当前放行的笔数

**Files:**
- Modify: `quantlab/services/backtest_plan.py` 的 `get`
- Modify: `quantlab/web/assets/backtest/plan.js`（约 229 行的 `plan-state`）
- Modify: `quantlab/web/pages/backtest_plan.html` 第 30 行
- Modify: `quantlab/web/assets/settings.js` 第 31 行
- Modify: `tests/quantlab/test_settings_page.py` 约 248 行

**Interfaces:**
- Consumes: `snapshot()`
- Produces: 计划详情 JSON 的 `admission`：`running`、`loading`、`limit`、`can_start`

`get` 在返回体上加 `admission`。没有人调用过 `enter` 时，`running=0`、`loading=0`、`limit` 仍按当前机器计算、`can_start=True`。

`plan.js` 在现有「正在跑 N 笔」后面加上：有 `plan.admission` 时写 `，读数据 ${loading} 笔，上限 ${limit} 笔`。没有这个字段时保持旧句子，避免旧响应把页面打空。

`backtest_plan.html` 的说明改为：`先把要跑的回测写进清单。全选后点开始。同一时刻只有一笔在读行情；读完并且内存还够，再放下一笔。16GB 内存仍然一条一条跑。`

`settings.js` 的 hint 保留「同时回测」四个字，后面改成：`同时回测的硬顶是 ${x.compute_hint.max_concurrent_backtests} 条。真正启动时同一时刻只让一笔读行情，读完再按剩余内存放行。没有手动档。` 测试 `assert "同时回测" in settings.js` 因此仍能过。再加一条断言，文件里含有 `只让一笔读行情`。

- [ ] **Step 1: 写一条 API 测试**

在 `test_backtest_plan.py` 加 `test_plan_detail_includes_admission_snapshot`。创建草稿计划后 GET，断言 `admission.loading == 0`、`admission.running == 0`、`admission.limit >= 1`、`admission.can_start is True`。

- [ ] **Step 2: 跑这条测试，确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_plan.py::test_plan_detail_includes_admission_snapshot -v`

Expected: FAIL，响应里没有 `admission`。

- [ ] **Step 3: 接上 snapshot 和三处文案**

- [ ] **Step 4: 跑计划测试和设置页测试**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_plan.py tests/quantlab/test_settings_page.py -v`

Expected: PASS

---

### Task 6: 回归与重启边界

**Files:**
- 无新文件

- [ ] **Step 1: 跑相关测试**

Run: `.venv/bin/pytest tests/quantlab/test_backtest_admission.py tests/quantlab/test_backtest_plan.py tests/quantlab/test_settings_page.py tests/quantlab/test_compute_budget.py -v`

Expected: PASS。

`test_max_concurrent_backtests_follows_installed_ram_and_cpu` 若因为 `hardware_concurrent_cap` 在 `gb >= 120` 时返回 12、而断言写的是 6 已经失败，不要在本计划里改公式或改那个断言。把失败原样记到任务说明里，停下来问。这条失败不是本计划引入的。

- [ ] **Step 2: 不要重启正在跑的服务**

代码换进进程才生效。确认 `backtest_plans.status='running'` 的计划已经结束之后，才能重启 `quantlab serve`。当前这一条是 `plan-081cf60874b4`。重启时若希望稳定阶段硬顶高于现在的 10，把 `QUANTLAB_MAX_CONCURRENT_BACKTESTS` 设为 16 或删掉。不设的话，本机硬件公式的硬顶会生效，加载门仍然是 1。

---

## 审核记录

三遍审核都改在这份计划正文里，不另起一份计划。

### 第一遍：对照设想

核对排队、加载错峰、峰值后放行、按内存决定笔数、界面通知。

发现并已写入正文：

- 只错开几秒仍会把峰值叠上。加载令牌必须等 RSS 回落或超时加内存检查，不能用固定睡眠。
- 稳定阶段 2.6GB 不能拿来把 124GB 填满后再无上限地开。硬顶仍是 `min(16, 环境变量或硬件公式)`。
- 136 个线程不能当忙核去除，否则并发变成 1。第一版不按线程数缩。
- 界面要显示正在跑、读数据和上限，不是只改调度不告诉人。

### 第二遍：对照现有代码路径

核对 `_run_loop`、`_run_item`、`run_isolated`、`acquire_execution`、工作台 `POST /execute`、计划停止、现有测试。

发现并已写入正文：

- 放行若只写在计划循环里，工作台单笔执行仍能在计划读行情时再开一笔。门放在 `run_isolated` 前面，计划路径先 `enter` 再调用它。
- 现有代码在 `enter` 之前就把任务写成 `running`，排队会看起来已经在跑。状态改到 `enter` 成功之后。
- `enter` 之前若先 `submit`，停止等待时会留下孤儿 run。计划路径在 `enter` 成功之后才 `submit`。
- 线程池宽度、`acquire_execution` 和稳态上限若是三个数字，多出来的那笔会被打成失败。三处用 `steady_ceiling`。
- `test_start_runs_two_items_in_parallel_when_machine_allows_two` 的屏障在加载门之后进不去。测试改成第一笔先交还加载令牌。
- `ResourceSampler` 不能当父进程的放行信号。

### 第三遍：对照失败模式

核对内存不回落、小内存、停止卡住、标记丢失、监督线程和测试互相污染、重启打掉正在跑的计划。

发现并已写入正文：

- 第一笔若也要求 `MemAvailable >= 28GB`，16GB 机器一笔都跑不了。`running == 0` 时允许启动。
- 标记丢失或 RSS 不回落时，加载令牌不能永远占着。20 秒和 90 秒两条超时都还要再看剩余内存，内存不够就继续占着。
- 子进程崩溃由 `leave()` 交还稳态名额。监督线程看到 pid 消失时，只有标记已经写过才把加载令牌交还；进程刚启动就消失则等 `leave()`。不能把「pid 暂时读不到」当成 RSS 0，否则启动瞬间会放行下一笔。
- 低 RSS 在标记出现之前一律不算峰值已过。刚 `spawn` 出来的进程 RSS 很低，紧接着才会涨到约 11GB。纯函数测试里，没有标记的 3GB 必须返回 False。
- 规则回测不写标记会把加载令牌占到 90 秒。标记写在 `rule_backtest.py` 读完窗口之后。
- `enter` 的上限也用 `steady_ceiling`，避免和线程池、`acquire_execution` 又分成两个数字。
- `stop` 必须让 `enter` 返回 False。测试覆盖这条，并用 `finally` 调用 `reset_admission()`。
- 本计划的测试可以在当前机器上跑。重启服务不在测试步骤里，并且明确避开正在跑的 `plan-081cf60874b4`。
