# 状态机守住写路径 Implementation Plan（P3 第二版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **取代：** 桌面稿 `/Users/weiminzhu/Desktop/2026-09-09-status-transitions.md`（v1）。以本文件为准。
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。
>
> **落地顺序：** 建议先于 P2。本计划只靠 `.venv/bin/pytest`，不跑 Playwright。

**Goal:** 让 `quantlab/domain/status.py` 的转移矩阵成为改生命周期/运行状态的唯一 Python 入口；消除 research / training / backtest_runs / factors-strategy-model lifecycle 的 service 层绕过；与 `schema.sql` 触发器语义对齐（含 backtest `failed→running`）；用负例测试锁住非法转移。

**Architecture:** DB trigger 已是全库防线。P3 补齐 Python 层：非法状态在进 SQL 前失败，并统一 `finished_at` 等副作用。先扩展 domain（独立 `BacktestRunStatus`），再按爆炸半径收敛：research bypass → training runs → backtest runs → factors/strategy/model lifecycle。plans / calc runs / datasets **不进核心**。

**Tech Stack:** `quantlab/domain/status.py`、repositories/services、`.venv` Python 3.12、pytest。

## Global Constraints

- **行为冻结：** 不改对外 API 路径与成功响应形状。非法转移继续变成 `ValueError` → 现有 HTTP 400（如 `_strategy_error`），不要变成 SQLite 500。
- **不削弱 DB trigger；** Python 与 trigger 语义对齐（见对齐表）。禁止为了「统一 RunStatus」去改 schema、禁止把 backtest 重跑改成 `failed→queued`。
- **publish 可保留两步 UPDATE，** 但每步必须先 `transition(LifecycleStatus, ...)` 再用返回值写 SQL。quality gate 不动。
- **INSERT 初始** `queued` / `draft` / 种子 `published` 可保留字面量；**转移**必须走 `transition`。
- **禁止顺手重构：** 不改撮合/训练/因子计算算法；不改 QualityStatus 机；不删 trigger。
- **与 P2 解耦：** 不改前端 JS / HTML。
- **Python：** 一律 `.venv/bin/python` / `.venv/bin/pytest`。
- **工作区：** 不主动 `git commit`。

**基线（2026-09-09，`main` @ P0+P1）：**

| 项 | 值 |
|---|---|
| `domain.transition` 生产调用 | 几乎仅 `ResearchRunRepository.transition`（`research_runs.py` ~L359） |
| `RunStatus` | 禁止 `failed→running`（`test_entities.py` L67–79 已锁） |
| `backtest_runs_state_machine` | **允许** `failed→running`（`schema.sql` + `test_database.py::test_failed_backtest_can_restart_to_running`） |
| `factor_data.py` bypass | L742–785：INSERT queued 后直写 running/completed/failed |
| `transition_training_run` | `strategy_center.py` L121–131：读行后直 UPDATE，无 domain |
| backtest 重跑 | `backtest_job.py` L598–611、`rule_backtest.py` L105–116：`queued\|failed → running` |
| Lifecycle publish | `factors.py` `_publish_in_transaction`、`strategy_center.py` publish、`model_training.py` deprecate：裸 SQL |
| plan 状态 | 不在 domain；无 trigger |

---

## v1 审查结论（本版已吸收）

v1 锁定方案 A、先 domain 后按域收敛、不改 QualityStatus——这些保留。

必须修正：

1. **必须独立 `BacktestRunStatus`（含 `failed→running`）。** 否决「先 failed→queued 再统一 RunStatus」：那要改 trigger + `_requeue_failed` + job，超出 P3，并打断现有重跑。`transition()` 按 **enum 类型** 分发矩阵，`BacktestRunStatus` **不要** 继承 `RunStatus`（否则 `isinstance` 会走错表）。
2. **`factor_data.py` 行号仍是 742–785**（不是过时行号）。改为 `ResearchRunRepository.transition` / `fail`。`finished_at` 与正规路径对齐视为**允许副作用**，不是 API JSON 改形。成功路径用 `transition(..., "completed")`，不要误用 `complete()`（后者会写 `summary_json`，现码 completed 时不写 summary）。
3. **`POST /api/models/runs/{run_id}/status` 已把 `ValueError` 映射到 `_strategy_error`**（`quantlab/api/routes/models.py`）。先读当前 status 再 `transition(RunStatus, ...)`，非法边继续 400。
4. **Task 6 的 `rg` 只扫 `quantlab/`，排除 `tests/`。** `test_overview_service`、`test_result_archive`、`test_database` 等夹具会继续直写 SQL。
5. **核心收口后允许残留的直写（禁止当成漏网去「清零」）：**
   - `backtest_plans` / `backtest_plan_items`
   - `backtest_steps`
   - `factor_calculation_runs`
   - `dataset_scan_audits`
   - `quality_status` 字段
   - INSERT 初始 `queued` / `draft` / 种子 `published`
6. **5c datasets 默认延期（锁定）。** 5a plans 建议跟进、**不进 P3 核心**。5b calc runs 只文档化「INSERT 即 running」，不发明 `queued`（schema 无此值）。
7. **测试夹具里的 `UPDATE ... SET status` 不要改成 domain。** 那是在造夹具状态，不是生产写路径。

---

## 方案选择（仍锁定 A）

| 方案 | 做法 | 结论 |
|---|---|---|
| **A. 扩展 domain + 仓库封装 + 逐域收敛（采用）** | 独立 BacktestRunStatus；生产转移走 `transition` | 与 schema 一致 |
| B. 只靠 DB trigger | Python 仍可绕过到进 SQL 才爆 | 否决 |
| C. 一次改完所有 `SET status` | 含 plans/steps/calc/datasets | 否决 |
| D. 统一 RunStatus 并改 backtest 为 failed→queued | 改 trigger + 重跑语义 | 否决 |

---

## Domain 对齐表

| 枚举 | 允许边 | 备注 |
|---|---|---|
| `LifecycleStatus` | draft→validated→published→deprecated | 保持；单向 |
| `RunStatus` | queued→running→completed\|failed | research + training；终态不可再转；**禁止 failed→running** |
| `BacktestRunStatus`（新建） | queued→running→completed\|failed **且 failed→running** | 与 `backtest_runs_state_machine` 一致 |
| PlanStatus | 不进核心 | 见 Task 5a |
| `QualityStatus` | 不走 `transition()` | 保持独立 |

---

## 目标文件结构

```text
quantlab/domain/status.py              # + BacktestRunStatus；transition 按类型分发
quantlab/repositories/run_lifecycle.py # 新建：读当前 → transition → UPDATE 指定白名单表
quantlab/repositories/research_runs.py # 已走 domain；可继续自用或改调 helper
quantlab/services/factor_data.py       # 去掉 L746/778/782 直写
quantlab/services/strategy_center.py   # training + publish
quantlab/services/backtest_job.py      # backtest_runs 转移
quantlab/services/rule_backtest.py
quantlab/services/backtest_control.py  # mark_stopped 的 runs 转移（steps 仍直写）
quantlab/repositories/factors.py       # publish/deprecate
quantlab/services/model_training.py    # deprecate 路径
```

`run_lifecycle.py` 职责：生产路径里 **UPDATE 运行表 status** 的唯一封装。表名白名单：`research_runs`、`model_training_runs`、`backtest_runs`。lifecycle 实体表（factors / models / strategies 及 versions）仍在原 repository 里调 `transition()` 后再 UPDATE（它们不是 `run_id` 主键）。

---

## 闭包/改写模式

### 运行状态

现码：

```python
connection.execute(
    "UPDATE backtest_runs SET status='running' WHERE run_id=? AND status IN ('queued', 'failed')",
    (run_id,),
)
```

改为：先读当前 status，再：

```python
from quantlab.domain.status import BacktestRunStatus, transition
from quantlab.repositories.run_lifecycle import apply_run_status

apply_run_status(
    database,
    table="backtest_runs",
    run_id=run_id,
    target="running",
    status_enum=BacktestRunStatus,
)
```

`apply_run_status` 必须：

1. `table` 不在白名单则 raise。
2. SELECT 当前 status；找不到行 → `ValueError("... not found")`（文案与现码接近）。
3. `transition(status_enum(current), status_enum(target))`。
4. `UPDATE {table} SET status=? WHERE run_id=?`。
5. 调用方若还需写 `error_message` / `metrics_json` / `finished_at`，**仍可在同一事务或随后执行**；不要把业务列塞进 domain。

同一 UPDATE 里同时改 status 与 metrics 时：先 `apply_run_status`（或在同一 `transaction()` 内先校验 `transition` 再一条 UPDATE 带 metrics）。不要先写 metrics 再绕过校验。推荐在同一 `with database.transaction()` 里：读 status → `transition` → 一条 `UPDATE ... SET status=?, metrics_json=?`。

### Lifecycle

现码：

```python
connection.execute("UPDATE factor_versions SET status='validated' ...")
connection.execute("UPDATE factor_versions SET status='published' ...")
```

改为：

```python
from quantlab.domain.status import LifecycleStatus, transition

next_status = transition(LifecycleStatus(row["status"]), LifecycleStatus.VALIDATED).value
connection.execute("UPDATE factor_versions SET status=? ...", (next_status, ...))
next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
connection.execute("UPDATE factor_versions SET status=? ...", (next_status, ...))
```

已是 `validated` 则不要再走 draft→validated（现码 L657–661 已分支）。保持该分支，只在实际发生的边上调用 `transition`。

---

## 域 → 回归测试映射

| Task | 最低命令 |
|---|---|
| 0 domain | `.venv/bin/pytest tests/quantlab/test_entities.py tests/quantlab/test_database.py -q` |
| 1 research bypass | `.venv/bin/pytest tests/quantlab/test_research_runs.py tests/quantlab/test_factor_calculation.py tests/quantlab/test_canonical_factor_pack.py -q` |
| 2 training | `.venv/bin/pytest tests/quantlab/test_strategy_center.py tests/quantlab/test_e2e_integration.py -q` |
| 3 backtest | `.venv/bin/pytest tests/quantlab/test_backtest_workbench.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_run_record.py tests/quantlab/test_database.py -q` |
| 4 lifecycle | `.venv/bin/pytest tests/quantlab/test_factor_library.py tests/quantlab/test_factor_manual.py tests/quantlab/test_strategy_center.py tests/quantlab/test_model_training.py -q` |
| 6 收尾 | `.venv/bin/pytest tests/quantlab -q --ignore=tests/quantlab/browser` |

---

## Chunk 1: domain + 封装

### Task 0: 扩展 domain + 负测

**Files:**

- Modify: `quantlab/domain/status.py`
- Modify: `tests/quantlab/test_entities.py`
- Create: `quantlab/repositories/run_lifecycle.py`
- Test: `tests/quantlab/test_entities.py`（可加 `tests/quantlab/test_run_lifecycle.py` 若 helper 需要独立测）

- [ ] **Step 1: 先写失败测试**

在 `test_illegal_lifecycle_and_run_transitions_are_rejected` **旁新增**（不要删现有 `RunStatus.FAILED → RUNNING` 禁止断言）：

```python
from quantlab.domain.status import BacktestRunStatus, RunStatus, transition

def test_backtest_run_allows_failed_to_running() -> None:
    assert transition(BacktestRunStatus.FAILED, BacktestRunStatus.RUNNING) == BacktestRunStatus.RUNNING
    assert transition(BacktestRunStatus.QUEUED, BacktestRunStatus.RUNNING) == BacktestRunStatus.RUNNING
    with pytest.raises(ValueError, match="not allowed"):
        transition(BacktestRunStatus.COMPLETED, BacktestRunStatus.RUNNING)
    with pytest.raises(ValueError, match="not allowed"):
        transition(RunStatus.FAILED, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="types do not match"):
        transition(RunStatus.FAILED, BacktestRunStatus.RUNNING)
```

- [ ] **Step 2: 跑测确认 RED**

```bash
.venv/bin/pytest tests/quantlab/test_entities.py::test_backtest_run_allows_failed_to_running -q
```

Expected: FAIL（`BacktestRunStatus` 未定义或 `FAILED→RUNNING` 仍不允许）。

- [ ] **Step 3: 实现枚举与分发**

`status.py` 增加（值字符串与 `RunStatus` 相同，类型不同）：

```python
class BacktestRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

_BACKTEST_RUN_TRANSITIONS = {
    BacktestRunStatus.QUEUED: {BacktestRunStatus.RUNNING},
    BacktestRunStatus.RUNNING: {BacktestRunStatus.COMPLETED, BacktestRunStatus.FAILED},
    BacktestRunStatus.COMPLETED: set(),
    BacktestRunStatus.FAILED: {BacktestRunStatus.RUNNING},
}

_TRANSITIONS = {
    LifecycleStatus: _LIFECYCLE_TRANSITIONS,
    RunStatus: _RUN_TRANSITIONS,
    BacktestRunStatus: _BACKTEST_RUN_TRANSITIONS,
}

StatusType = TypeVar("StatusType", LifecycleStatus, RunStatus, BacktestRunStatus)

def transition(current: StatusType, target: StatusType) -> StatusType:
    if type(current) is not type(target):
        raise ValueError("status transition types do not match")
    allowed = _TRANSITIONS[type(current)]
    if target not in allowed[current]:
        raise ValueError(f"status transition {current.value} -> {target.value} is not allowed")
    return target
```

QualityStatus **不要** 加入 `_TRANSITIONS`。

- [ ] **Step 4: 实现 `apply_run_status`**

```python
# quantlab/repositories/run_lifecycle.py
_ALLOWED_TABLES = {
    "research_runs": ...,  # 调用方传入 status_enum
    "model_training_runs": ...,
    "backtest_runs": ...,
}
```

实现要点：白名单表名；SELECT status；`transition`；UPDATE。找不到行 raise `ValueError`。不要在此写 `finished_at`（各调用方原样保留自己的 finished_at SQL）。

- [ ] **Step 5: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_entities.py tests/quantlab/test_database.py -q
```

Expected: PASS。`test_failed_backtest_can_restart_to_running` 仍是 **SQL trigger** 测试，不要改成只测 domain。

---

## Chunk 2: 运行态收敛

### Task 1: 收敛 `factor_data` research bypass

**Files:**

- Modify: `quantlab/services/factor_data.py`（`create_sample_export`，约 L708–798）
- Test: 在 `tests/quantlab/test_research_runs.py` 或现有 factor 测试中补负例（若尚无覆盖该 export 的 status 边）

**Interfaces:**

- Consumes: `ResearchRunRepository(self.settings, self.database)`（现 `FactorDataService` 已有 `settings`/`database`，不必改 `create_app`）
- INSERT `queued` **保留字面量**
- 删除 L746、L778、L782 的 `UPDATE ... SET status=`

- [ ] **Step 1: `rg` 确认直写**

```bash
rg -n "UPDATE research_runs SET status" quantlab --glob '*.py'
```

Expected: `factor_data.py` 与 `research_runs.py`（后者是合法封装）。

- [ ] **Step 2: 改为 repository**

模式：

```python
from quantlab.repositories.research_runs import ResearchRunRepository

runs = ResearchRunRepository(self.settings, self.database)
# INSERT queued + run_registry 保持现码
runs.transition(run_id, "running")
try:
    ...
    runs.transition(run_id, "completed")
except Exception as error:
    runs.fail(run_id, str(error))
    raise
```

`fail()` 会写 `error_message` 并 `transition` 到 failed，且补 `finished_at`。这是允许的副作用。

不要用 `complete(run_id, summary)`，除非现码本来就写 summary（现码不写）。

- [ ] **Step 3: 负测**

补一条：queued→completed 必须拒绝（可走 repository 或走会进 `create_sample_export` 的 API）。`test_research_runs.py` 已有 queued→completed 拒绝；确认仍 PASS。若 export 路径缺测，加最小测试：export 成功后 `research_runs.status==completed` 且 `run_registry.finished_at` 非空。

- [ ] **Step 4: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_research_runs.py tests/quantlab/test_factor_calculation.py tests/quantlab/test_canonical_factor_pack.py -q
```

Expected: PASS。

---

### Task 2: training runs

**Files:**

- Modify: `quantlab/services/strategy_center.py`（`transition_training_run` L121–131）
- Test: `tests/quantlab/test_strategy_center.py`

- [ ] **Step 1: 先写负测（若还没有）**

```python
def test_training_run_rejects_queued_to_completed_and_failed_to_running(tmp_path: Path) -> None:
    # 用现有 _setup / create_training_run
    with pytest.raises(ValueError):
        service.transition_training_run(run_id, "completed")  # 仍是 queued
    service.transition_training_run(run_id, "running")
    service.transition_training_run(run_id, "failed")
    with pytest.raises(ValueError):
        service.transition_training_run(run_id, "running")
```

- [ ] **Step 2: RED 后实现**

在 UPDATE 前：

```python
from quantlab.domain.status import RunStatus, transition

current = row["status"]
next_status = transition(RunStatus(current), RunStatus(status)).value
connection.execute("UPDATE model_training_runs SET status=? WHERE run_id=?", (next_status, run_id))
```

保留：`status not in {running, completed, failed}` 的现有校验；`finished_at` 现码。也可用 `apply_run_status(..., table="model_training_runs", status_enum=RunStatus)` 再单独写 `finished_at`。

- [ ] **Step 3: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_strategy_center.py tests/quantlab/test_e2e_integration.py -q
```

Expected: PASS。e2e 里 `queued → running → completed` 合法两步仍成功。

---

### Task 3: backtest runs

**Files:**

- Modify: `quantlab/services/backtest_job.py`（约 L598–611、L809–836）
- Modify: `quantlab/services/rule_backtest.py`（约 L105–116、L350–377）
- Modify: `quantlab/services/backtest_control.py`（`mark_stopped` L162–185 的 **runs** 两步；**steps 直写保留**）
- Test: `tests/quantlab/test_backtest_workbench.py`、`test_rule_backtest.py`、`test_database.py`

- [ ] **Step 1: `rg` 三文件**

```bash
rg -n "UPDATE backtest_runs SET status" quantlab/services/backtest_job.py quantlab/services/rule_backtest.py quantlab/services/backtest_control.py
```

- [ ] **Step 2: 负测**

- queued→completed 拒绝（Python，在 job 封装上或 domain）。
- failed→running **允许**（不要用 `RunStatus`，用 `BacktestRunStatus`）。
- 现有 `test_failed_backtest_can_restart_to_running` 保持。

- [ ] **Step 3: 替换 `backtest_runs` 转移**

`queued|failed → running`：读当前，`transition(BacktestRunStatus(current), BacktestRunStatus.RUNNING)`，再 UPDATE。`AND status IN ('queued', 'failed')` 可保留作乐观条件，但 **不能** 代替 domain。

completed / failed：同样走 `BacktestRunStatus`。`mark_stopped` 仍是 queued→running 再 running→failed（现码两步）；每步都要 `transition`。若已是 running，第一步 `queued→running` 会失败——保持现码语义：第一条 UPDATE 带 `AND status='queued'` 0 行是合法的，**不要**对 0 行调用 `transition`。实现时：若当前是 queued，才 apply queued→running；然后若当前是 running，再 apply running→failed。

`backtest_steps` 的 SET status **不要**迁入 domain。

- [ ] **Step 4: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_backtest_workbench.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_run_record.py tests/quantlab/test_database.py -q
```

Expected: PASS。失败任务重跑仍成功。

---

## Chunk 3: lifecycle + 收口

### Task 4: Lifecycle publish / deprecate

**Files:**

- Modify: `quantlab/repositories/factors.py`（`_publish_in_transaction` ~L634–676、`_deprecate_in_transaction` ~L707–718）
- Modify: `quantlab/services/strategy_center.py`（`publish_model_version` L136–149、`publish_strategy_version` L190–201）
- Modify: `quantlab/services/model_training.py`（`_deprecate_other_versions` / `_deprecate_entity` ~L850–863）
- Test: `tests/quantlab/test_factor_library.py`、`test_factor_manual.py`、`test_strategy_center.py`

- [ ] **Step 1: 负测**

- `published → draft` 拒绝。
- 单步 `draft → published` 拒绝（必须经过 validated）。publish 函数内部仍是两步合法边，对外一次 `publish()` 仍成功。
- 对 domain 直接测：`transition(LifecycleStatus.DRAFT, LifecycleStatus.PUBLISHED)` raise。

- [ ] **Step 2: 每步 `transition` 后再 UPDATE**

quality_status=passed 检查保持在 `transition` **之前**（现码顺序）。parent factor/model/strategy 的 draft→validated→published 同样逐步 `transition`。

`_deprecate_other_versions` 可能一次 UPDATE 多行 `published→deprecated`：对**每一行**（或对将要更新的集合）用 `transition(LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED)` 一次即可（边相同），不要改成别的状态。

- [ ] **Step 3: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_factor_library.py tests/quantlab/test_factor_manual.py tests/quantlab/test_strategy_center.py tests/quantlab/test_model_training.py -q
```

Expected: PASS。

---

### Task 5: 可选扩展（记录决策，核心不含 5c）

**P3 核心以 Task 0–4 + Task 6 为准。本 Task 只写进本文件的「已决定」清单，不要在本 PR 改 datasets INSERT published。**

- **5a plans/items（建议跟进 PR，不进本次核心）：** `backtest_plans`：`draft | running | completed | stopped` + `closed` 布尔；items：`pending | queued | running | completed | failed | skipped`。无 DB trigger。另开计划再做 PlanStatus。
- **5b `factor_calculation_runs`：** schema 仅 `running | completed | failed`。现码 INSERT `'running'`。文档化「INSERT 即 running」，**不要**补 queued（无列值）。
- **5c datasets/catalog（锁定延期）：** `catalog.py` INSERT 直接 `published`。改这个要产品决策。

#### 已决定

| 项 | 决策 |
|---|---|
| **5a plans/items** | **不进 P3 核心。** 如需收敛，另开跟进 PR。`backtest_plans` / `backtest_plan_items` 继续直写 SQL；本轮不在 domain 引入 `PlanStatus`。 |
| **5b `factor_calculation_runs`** | **保持现码 INSERT `'running'`。** 语义文档化：**INSERT 即 running**（schema 无 `queued` 列值，禁止发明 `queued`）。 |
| **5c datasets/catalog** | **延期。** `catalog.py` INSERT 直接 `published` 不变；是否改为 draft→published 需后续产品决策，**本 PR 不改**。 |

- [x] **Step 1: 在本 Task 的 PR 描述或代码注释中不必新开文件。** 实施者在 Task 6 报告里写明：5a 未做、5b 保持 INSERT running、5c 延期。
- [x] **Step 2: 不要改 `catalog.py` 的 INSERT published。**

---

### Task 6: 全量验收

- [x] **Step 1: 限定范围 rg**

```bash
rg -n "SET status\s*=" quantlab --glob '*.py'
```

Expected：hits 只落在：

- `run_lifecycle.py` / `research_runs.py`（封装）
- lifecycle 封装后的 factors / strategy_center / model_training（UPDATE 使用 `transition` 返回值）
- **允许残留：** `backtest_plan.py`、`backtest_job.py`/`rule_backtest.py`/`backtest_control.py` 的 **steps**、`factor_calculation.py`、`catalog.py`（scan audits + INSERT published）、INSERT 初始 status、`quality_status`

不允许残留：`factor_data.py` 对 `research_runs` 的直写；`transition_training_run` 无 `transition` 的直写；`backtest_runs` 无 `BacktestRunStatus` 的直写。

不要 `rg` `tests/`。

- [x] **Step 2: domain 引用面**

```bash
rg -n "from quantlab.domain.status import|domain.status" quantlab --glob '*.py'
```

Expected: `status.py` 被 research / training / backtest_runs / factors / strategy publish / model deprecate 引用。

- [x] **Step 3: 全量单测**

```bash
.venv/bin/pytest tests/quantlab -q --ignore=tests/quantlab/browser
```

Expected: 全部 PASS。

---

## 平稳过渡

1. 先测后改；每 Task 可独立停住（不主动 commit）。
2. INSERT 初始 queued/draft 可保留字面量。
3. Python 与 DB 不一致时 **先改 domain 对齐 schema**，不改 trigger 去迁就 Python。
4. 不重构业务算法。
5. `mark_stopped` 对 0 行 UPDATE 不要强行 `transition`。

## 风险

| 风险 | 缓解 |
|---|---|
| 漏网直写 | Task 6 rg（排除 tests 与允许残留表） |
| 误禁 backtest 重试 | 独立 `BacktestRunStatus`；保留 `test_failed_backtest_can_restart_to_running` |
| publish 两步中断 | 仍在同一 `database.transaction()` |
| 把测试夹具当生产漏网 | rg 只扫 `quantlab/` |
| datasets 强改 | 5c 延期 |
| `complete()` 误用 | factor_data 成功路径只用 `transition(..., "completed")` |

## 工作量

核心约 **3–5 人日**（不含 5a/5c）。

## 非目标

不改 QualityStatus 机；不删 DB trigger；不做 P2 前端；不把 plans/steps/calc/datasets 强行纳入本次核心。
