# 回测运算资源自动配额 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **规格：** `docs/specs/2026-09-10-backtest-resource-auto-tune-design.md`
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 每条回测记下进程树峰值内存与实际工人数，并在下次同类回测里用这份先验收紧折/分层并行；全局仍然一次只跑一条回测。

**Architecture:** 新增 `quantlab/services/compute_budget.py`，负责采样 RSS、运行签名、查最近样本、把「请求工人数」压到内存天花板。`backtest_job.execute` / `rule_backtest` 在写 `metrics_json` 前挂上 `resources`。`fold_worker_count` 与分层进程在现有请求值之后再套天花板。设置页和运行记录只展示，不提供「同时回测条数」旋钮。

**Tech Stack:** 现有 FastAPI / SQLite `metrics_json`、`.venv` Python 3.12、pytest；RSS 用 Unix `ps`，不引入 psutil。

## Global Constraints

- **全局闸门不变：** `reserve_execution` 仍一次一条回测。本计划不加计划内并行。
- **绩效字段不变：** `resources` 只活在 `metrics_json.resources`。`result_archive` 的收益/回撤投影、计划页 `json_extract($.return)` 不得把资源字段当成绩效。
- **请求值仍是上限的输入，不是最终值：** `QUANTLAB_FOLD_WORKERS` / 设置页显式数字也不能超过内存天花板。
- **无先验时接近现状：** `frame_nbytes=0` 且无 `peak_rss` 时，折并行仍是 CPU×0.8；分层仍走现有 `memory_safe_process_workers`。
- **禁止顺手重构：** 不改撮合、训练、切折算法、不改状态机、不改路径相对化。
- **Python：** `.venv/bin/python` / `.venv/bin/pytest`。
- **工作区：** 不主动 `git commit`。

## 文件地图

| 文件 | 职责 |
|---|---|
| Create: `quantlab/services/compute_budget.py` | RSS 采样、签名、查先验、封顶工人数 |
| Create: `tests/quantlab/test_compute_budget.py` | 预算与采样单测 |
| Modify: `quantlab/services/settings.py` | `compute_hint` 带上建议与「同时 1 条」 |
| Modify: `quantlab/services/backtest_job.py` | execute 采样；折并行走封顶 |
| Modify: `quantlab/services/rule_backtest.py` | 成功/失败同样写入 `resources` |
| Modify: `quantlab/services/bucket_equity.py` | 分层进程用先验成本封顶 |
| Modify: `quantlab/services/result_archive.py` | 详情暴露 `resources`，不进 `_METRICS` |
| Modify: `quantlab/web/pages/backtest_run_record.html` | 运算资源块 |
| Modify: `quantlab/web/assets/backtest/run-record.js` | 渲染峰值内存与工人数 |
| Modify: `quantlab/web/pages/settings.html` / `quantlab/web/assets/settings.js` | hint 文案 |
| Modify: `tests/quantlab/test_rolling_fold_parallel.py` | 折封顶 |
| Modify: `tests/quantlab/test_bucket_equity.py` | 先验压分层 |
| Modify: `tests/quantlab/test_result_archive.py` | 详情带 resources、列表绩效不含该键 |
| Modify: `tests/quantlab/test_settings_page.py` | hint 字段 |
| Modify: `tests/quantlab/test_web_shell.py` | 运行记录页有资源块 |
| Modify: `README.md` / `README.en.md` | 说明自动配额与同时 1 条 |

---

## Chunk 1: 预算模块

### Task 1: 运行签名、先验查找、工人封顶

**Files:**
- Create: `quantlab/services/compute_budget.py`
- Create: `tests/quantlab/test_compute_budget.py`
- Modify: `quantlab/services/settings.py`（`memory_safe_process_workers` 可被 budget 调用，不要复制公式）

- [ ] **Step 1: 写失败测试**

```python
# tests/quantlab/test_compute_budget.py
from quantlab.services.compute_budget import (
    cap_workers,
    run_signature,
    last_resource_sample,
)


def test_run_signature_stable_for_same_window() -> None:
    config = {
        "dataset_id": "ds_canonical_market",
        "dataset_version_id": "current",
        "kind": "lgbm_ranker",
        "train": {"date_from": "2019-01-02", "date_to": "2023-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2025-12-31"},
        "walk_forward": {"mode": "rolling"},
    }
    assert run_signature(config) == run_signature(dict(config))
    other = {**config, "test": {**config["test"], "date_to": "2026-08-31"}}
    assert run_signature(config) != run_signature(other)


def test_cap_workers_without_prior_matches_cpu_request() -> None:
    assert cap_workers(requested=8, task_count=90, ram_bytes=16 * 1024**3, unit_bytes=0, kind="fold") == 8
    assert cap_workers(requested=8, task_count=3, ram_bytes=16 * 1024**3, unit_bytes=0, kind="fold") == 3


def test_cap_workers_fold_collapses_when_prior_peak_is_half_of_ram() -> None:
    ram = 16 * 1024**3
    assert cap_workers(
        requested=8,
        task_count=90,
        ram_bytes=ram,
        unit_bytes=0,
        kind="fold",
        prior_peak_rss_bytes=int(ram * 0.50),
    ) == 1


def test_cap_workers_bucket_uses_prior_unit_cost(monkeypatch) -> None:
    ram = 16 * 1024**3
    # 先验峰值 8GB、当时 2 个分层进程 → 单份约 4GB → 16GB 的 40% 预算装不下 2 份拷贝
    assert cap_workers(
        requested=4,
        task_count=10,
        ram_bytes=ram,
        unit_bytes=512 * 1024**2,
        kind="bucket",
        prior_peak_rss_bytes=8 * 1024**3,
        prior_workers=2,
    ) == 1
```

再补一条用临时 SQLite 的 `last_resource_sample`：插入两条 `completed` 运行，`metrics_json` 带不同 `resources.signature`，断言只返回匹配且更新的那条。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_compute_budget.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

`quantlab/services/compute_budget.py` 要点：

```python
def run_signature(config: dict[str, Any]) -> str:
    train = config.get("train") if isinstance(config.get("train"), dict) else {}
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    walk = config.get("walk_forward") if isinstance(config.get("walk_forward"), dict) else {}
    payload = {
        "dataset_id": str(config.get("dataset_id") or ""),
        "dataset_version_id": str(config.get("dataset_version_id") or ""),
        "kind": str(config.get("kind") or ""),
        "train_from": str(train.get("date_from") or ""),
        "test_from": str(test.get("date_from") or ""),
        "test_to": str(test.get("date_to") or ""),
        "walk": str(walk.get("mode") or config.get("walk_forward_mode") or ""),
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
        return requested
    cost = max(int(unit_bytes or 0), 0)
    if prior_peak_rss_bytes and prior_workers:
        cost = max(cost, int(prior_peak_rss_bytes) // max(1, int(prior_workers)))
    from quantlab.services.settings import memory_safe_process_workers
    return memory_safe_process_workers(cost, requested, ram=ram_bytes)
```

`last_resource_sample(database, signature)`：查询

```sql
SELECT metrics_json FROM backtest_runs
WHERE status='completed'
ORDER BY rowid DESC
LIMIT 50
```

解析 `resources.signature == signature` 的第一条。缺表/坏 JSON 返回 `None`。

RSS 采样（同文件）：

```python
def process_tree_rss_bytes(pid: int) -> int:
    """Sum RSS of pid and descendants via `ps -o rss=` (kilobytes). Missing ps → 0."""

class ResourceSampler:
    def start(self) -> ResourceSampler: ...
    def stop(self) -> dict[str, Any]:
        # peak_rss_bytes, elapsed_ms
```

子进程：复用 `backtest_control._signal_tree` 的 `pgrep -P` 思路，不要 import 控制模块造成环。本地写一个 `_child_pids`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/quantlab/test_compute_budget.py tests/quantlab/test_bucket_equity.py::test_memory_safe_process_workers_avoids_copying_frame_beyond_ram -q`
Expected: PASS

---

## Chunk 2: 写入 resources

### Task 2: 回测结束把样本写入 metrics_json

**Files:**
- Modify: `quantlab/services/backtest_job.py`（`execute` 约 L1412、成功写 metrics 约 L772–817；失败路径约 L822–838）
- Modify: `quantlab/services/rule_backtest.py`（`_write_success` 约 L324–358；失败 UPDATE 对称补上）
- Modify: `quantlab/services/result_archive.py`（`_project(..., include_detail=True)`）
- Modify: `tests/quantlab/test_result_archive.py`
- Modify: `tests/quantlab/test_backtest_workbench.py` 或新增小测：execute 结束 metrics 含 `resources`

- [ ] **Step 1: 写失败测试**

在 `tests/quantlab/test_result_archive.py`：

```python
def test_archive_detail_exposes_resources_without_using_them_as_return(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    run_id = "20260902-120000-0001"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET metrics_json=? WHERE run_id=?",
            (
                json.dumps({
                    "return": 0.25,
                    "resources": {
                        "peak_rss_bytes": 3 * 1024**3,
                        "fold_workers": 2,
                        "bucket_workers": 1,
                        "signature": "abc",
                    },
                }),
                run_id,
            ),
        )
    detail = ResultArchiveService(settings, database).get(run_id)
    assert detail["metrics"]["return"]["value"] == 0.25
    assert detail["resources"]["fold_workers"] == 2
    listing = ResultArchiveService(settings, database).list()
    assert "resources" not in listing["items"][0]
```

再在 `tests/quantlab/test_compute_budget.py` 或 workbench 测：monkeypatch `process_tree_rss_bytes` 返回固定值，跑一条最小 execute（现有 `_setup` fixture），断言 `metrics_json` 有 `resources.peak_rss_bytes` 和 `resources.signature`。优先复用 `test_backtest_workbench.py` 里已能 `execute` 的最小配置，不要新造全市场夹具。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_result_archive.py::test_archive_detail_exposes_resources_without_using_them_as_return -q`
Expected: FAIL（`resources` 键不存在）

- [ ] **Step 3: 最小实现**

1. `execute` 开头 `sampler = ResourceSampler().start()`，`finally: sample = sampler.stop()`。
2. 成功构建 `metrics` 后：

```python
metrics["resources"] = attach_resource_sample(
    sample,
    config=config,
    frame_nbytes=frame_nbytes(frame) if frame is not None else 0,
    fold_workers=...,
    bucket_workers=...,
    bucket_pool=resolve_bucket_pool(),
)
```

`fold_workers` / `bucket_workers` 用本次实际值（可 contextvar，或 sampler 上 `note(fold_workers=n)`，避免改 `_rolling_predictions` 签名过多）。

3. **失败路径**也 UPDATE `metrics_json` 为 `{"resources": ...}`（不要冲掉已有绩效；失败时本来常无绩效）。若当前失败只写 `error_message`，改为同时写入 `resources`，便于 OOM 后仍有峰值。
4. `result_archive._project`：`include_detail` 时 `item["resources"] = metrics.get("resources") or {}`。`_METRICS` 元组不增加 resources 键。列表项不要带 `resources`。

规则回测 `_write_success` 同样合并 `resources`。可在 `execute_rule_signal` 外包同一 sampler。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/quantlab/test_result_archive.py tests/quantlab/test_compute_budget.py tests/quantlab/test_backtest_workbench.py -q --tb=short`
Expected: PASS

---

## Chunk 3: 用先验封顶并行

### Task 3: 折线程与分层进程走 cap_workers

**Files:**
- Modify: `quantlab/services/backtest_job.py` `fold_worker_count`（约 L1436）与 `_rolling_predictions` 里 `workers = fold_worker_count(...)`（约 L1516）
- Modify: `quantlab/services/bucket_equity.py` `_run_bucket_jobs`（约 L190–199）
- Modify: `tests/quantlab/test_rolling_fold_parallel.py`
- Modify: `tests/quantlab/test_bucket_equity.py`

- [ ] **Step 1: 写失败测试**

`test_rolling_fold_parallel.py`：

```python
def test_fold_worker_count_collapses_when_budget_says_process_is_fat(monkeypatch) -> None:
    monkeypatch.delenv("QUANTLAB_FOLD_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    monkeypatch.setattr(
        "quantlab.services.backtest_job.total_ram_bytes",
        lambda: 16 * 1024**3,
    )
    # 需要 fold_worker_count 能接收 prior；或通过 compute_budget 注入
    from quantlab.services.compute_budget import cap_workers
    assert cap_workers(
        requested=8, task_count=90, ram_bytes=16 * 1024**3,
        unit_bytes=0, kind="fold", prior_peak_rss_bytes=9 * 1024**3,
    ) == 1
```

`test_bucket_equity.py`：在 `test_tight_ram_does_not_spawn_bucket_processes` 旁增加：`unit` 很小但 `prior_peak_rss_bytes=8GB, prior_workers=2` 时即使 `QUANTLAB_BUCKET_WORKERS=4` 也不启动 `ProcessPoolExecutor`。

现有 `test_fold_worker_count_uses_eighty_percent_of_cpus` **必须继续 PASS**（无先验）。

- [ ] **Step 2: 跑测试确认新测失败、旧测仍过**

Run: `.venv/bin/pytest tests/quantlab/test_rolling_fold_parallel.py tests/quantlab/test_bucket_equity.py -q`
Expected: 新测 FAIL 或旧测仍绿、新测红

- [ ] **Step 3: 接线**

`fold_worker_count(fold_count, *, prior_peak_rss_bytes=0)`：

```python
requested = resolve_worker_count("QUANTLAB_FOLD_WORKERS", "fold_workers", fold_count, cpu_count)
return cap_workers(
    requested=requested,
    task_count=fold_count,
    ram_bytes=total_ram_bytes(),
    unit_bytes=0,
    kind="fold",
    prior_peak_rss_bytes=prior_peak_rss_bytes,
)
```

`_rolling_predictions` 在算 `workers` 前：`prior = last_resource_sample(self.database, run_signature(config))`。

`_run_bucket_jobs` 在现有 `memory_safe_process_workers` 处改为 `cap_workers(..., kind="bucket", unit_bytes=frame_nbytes(frame), prior_*)`。不要套两次公式。

`_run_bucket_jobs` 没有 database/config 签名时：给函数增加可选 `prior: dict | None = None`，由 `attach_segment_curves` 传入（它拿得到 config；database 可通过 config 旁的 job 或 contextvar）。**最小侵入：** `contextvars.ContextVar` 名如 `_ACTIVE_RESOURCE_PRIOR`，由 `execute` 在 sampler 旁 set。禁止把 Database 传到 bucket 模块每个叶子。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/quantlab/test_rolling_fold_parallel.py tests/quantlab/test_bucket_equity.py tests/quantlab/test_compute_budget.py -q`
Expected: PASS

---

## Chunk 4: 页面与说明

### Task 4: 设置 hint 与运行记录展示

**Files:**
- Modify: `quantlab/services/settings.py` `compute_hint`（约 L48–65）
- Modify: `quantlab/web/assets/settings.js` 约 L25
- Modify: `quantlab/web/pages/settings.html` 运算资源说明（可选一句「同时只跑一条」）
- Modify: `quantlab/web/pages/backtest_run_record.html`「模块耗时」下增加 `#resource-summary`
- Modify: `quantlab/web/assets/backtest/run-record.js` `renderStepTiming` 旁渲染资源
- Modify: `tests/quantlab/test_settings_page.py`
- Modify: `tests/quantlab/test_web_shell.py`
- Modify: `README.md`、`README.en.md` 回测并行环境变量段

- [ ] **Step 1: 写失败测试**

`test_settings_page.py`：

```python
assert body["compute_hint"]["max_concurrent_backtests"] == 1
assert "safe_fold_workers" in body["compute_hint"]
```

`test_web_shell.py`：运行记录 HTML 含 `id="resource-summary"` 与「峰值内存」。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/quantlab/test_settings_page.py tests/quantlab/test_web_shell.py -q -k "settings_are_masked or run_record or resource"`
Expected: FAIL

- [ ] **Step 3: 实现展示**

`compute_hint` 增加：

```python
"max_concurrent_backtests": 1,
```

设置页文案（中文）：`本机 {cpu} 核、约 {ram} GB。折并行建议不超过 {safe_fold}；分层净值建议 {safe_bucket}。同时回测固定 1 条，不会根据历史消耗自动加路。`

运行记录：把 `d.resources` 格式化为「峰值内存 x.x GB · 折并行 n · 分层 n」。缺字段显示 —。峰值用 GiB，保留 1 位小数。

README 并行变量段补一句：设置页/环境变量是请求值；内存不够时运行时会再降；**不要**用它们理解成同时多条回测。

- [ ] **Step 4: 跑测试并做页面冒烟**

Run: `.venv/bin/pytest tests/quantlab/test_settings_page.py tests/quantlab/test_web_shell.py tests/quantlab/test_result_archive.py tests/quantlab/test_compute_budget.py tests/quantlab/test_rolling_fold_parallel.py tests/quantlab/test_bucket_equity.py -q`

浏览器（本机服务已在 `127.0.0.1:8765`）：打开 `/settings` 看到「同时回测固定 1 条」；打开一条已有运行记录，旧数据资源块为 —，不出现绝对路径。

Expected: 单测 PASS；设置页文案可见。

---

## 验收

- 16GB、无历史：分层仍因 `memory_safe_process_workers` 多为 1；折并行仍约 CPU×0.8。
- 有一条同类完成运行且峰值 > 45% 内存：下次折并行变为 1。
- 有先验分层单份成本很大：即使设置页填 4 也不起进程池。
- 档案列表排序/收益列不受 `resources` 干扰。
- 计划开始第二条时仍报「已有回测在运行」。

## 明确不做（防止执行时膨胀）

- 计划多 worker 并行跑 items
- 根据成功率自动 `max_concurrent_backtests += 1`
- Windows RSS、cgroup、GPU
- 新表/新 migration（只用 `metrics_json`）
