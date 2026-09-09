# 五模块字节码还原为可读源码 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 把 `trade_filters` / `portfolio` / `model_training` / `backtest_workbench` / `backtest_job` 从 `SourcelessFileLoader` + `.pyc` + 同文件补丁，变成单一可读 `.py`；删除 `_recovered_pyc` 与 hatch `force-include`；现有 `tests/quantlab` 全绿；IDE 能跳进回测/训练实现。

**Architecture:** 行为冻结在 **当前 HEAD 运行时**（`.pyc` exec 之后再跑补丁），不是冻结在裸字节码。按依赖从大到小还原：`trade_filters` → `portfolio` → `model_training` → `backtest_workbench` → `backtest_job`。每模块：反编译初稿 → 按合并表手工并入补丁 → 与 oracle 加载器对照 → 替换生产 `.py`。`.pyc` 留到五模块全部替换后再删，保证中途可回退。

**Tech Stack:** Python 3.12（`.venv`）、现有 pytest、`SourcelessFileLoader` 仅作迁移期 oracle、反编译器 `pycdc`（首选）或 `pylingual`（备选）。不把反编译器加入项目依赖。

## Global Constraints

- 行为等价对象是 **pyc + 补丁**，不是「更早的 git 源码」，也不是「去掉补丁的裸 pyc」。
- 禁止借还原重构引擎：不拆文件、不改公开函数名、不改规则策略/计划队列/宽表因子旁路的设计。
- 最新设计必须以补丁为准：日索引、`&&`/`&` 规范化、截面分箱标签、once embargo、规则策略分发、pretrade/SMA、并行 fold、pack 因子 `_load_frame`、`_now` 毫秒、`_step` 的 `COALESCE`。
- 仓库 `requires-python >= 3.10`，还原后的 `.py` 按现有代码风格写（已有 `from __future__ import annotations` 的模块保持）。
- 工作目录 `.tmp_pyc_recovery/`（已被 `.gitignore` 的 `.tmp_*` 覆盖），不进 git。
- 每换一个生产 `.py` 后立刻跑该模块对照测试；五模块完成后再跑全量 `pytest tests/quantlab`。
- 本工作区禁止主动 `git commit`。

**基线：** `main` @ `b2ef3f1`（工作区干净，已与 `origin/main` 同步）。还原前不要混入其它功能改动。

---

## 方案选择（已锁定 A）

| 方案 | 做法 | 结论 |
|---|---|---|
| **A. 反编译 pyc + 合并当前补丁（采用）** | 3.12 `.pyc` 出初稿，补丁按「替换 / 包装 / 新增」并入 | 唯一能保住 HEAD 行为的路径 |
| B. 从 vnpy 旧 git 源码演进 | `54345f30e` 的 `backtest_job.py` 约 13KB，pyc 记录源码 45KB；`trade_filters`/`portfolio`/`model_training` 从未进过 git | 缺大半引擎，否决 |
| C. 补丁拆到旁路模块、继续 exec pyc | 仍双真相、仍锁 3.12 | 不满足 P0，否决 |

---

## 平稳过渡原则

1. **Oracle 先固化：** Task 0 把五个「加载器+补丁」复制到 `.tmp_pyc_recovery/oracle/`，`.pyc` 路径改成绝对路径。对照测试始终 import 这份 oracle，直到该模块生产文件已替换且对照通过。
2. **一次只换一个 `.py`：** 替换后该模块不再 `exec` 字节码，其它模块仍可 exec。依赖方向允许这样做。
3. **`.pyc` 最后删：** 目录、hatch、`.gitignore`、README 在 Task 7 一次性去掉。中途失败可把单个 `.py` 从 git checkout 回来。
4. **包装函数保留 `_impl` 名字：** 测试会 `monkeypatch` `model_training._fit_lgb_impl`。合并后仍保留这些内部名，避免改测试语义。
5. **唯一必须改的测试：** `test_rolling_predictions_workers_one_matches_original` 依赖 `_original_rolling_predictions`（裸 pyc）。合并后该符号消失；用 oracle 对照一次后改测试，不再从生产模块 import `_original_*`。

---

## 最新程序设计逻辑冻结表（补丁赢）

这些是 HEAD 已经在跑、还原后必须原样保留的行为。反编译稿若与此冲突，**丢反编译、留补丁**。

### trade_filters

- `parse_expr`：先把 `&&`/`||`/`&`/`|` 换成 `AND`/`OR`，再交给原 parser。
- `eval_open_filters`：按表达式字符串缓存 AST（`_parsed_open_filters`），用补丁后的 `parse_expr`。

### portfolio

- `_day_rows` / `build_day_index` / `_RowView` / `_cached_day_index` / `_DAY_INDEX_ATTR`：日索引，禁止回到 pyc 的 `loc`+`iterrows`。
- `bucket_equity` 依赖 `_DAY_INDEX_ATTR` 与 `_cached_day_index`。
- `run_portfolio` 留 pyc 槽位引擎；它调用的 `_day_rows` 必须是补丁版。
- `eval_open_filters` 从 `trade_filters` 再导出，确保用到带缓存的实现。

### model_training

- `attach_label`：pyc 算 `_raw` 后，按日截面 `_bin_future_return(..., LIGHTGBM_LABEL_BINS)`。
- `holdout_split` / `validation_date_cutoff` / `restrict_fit_sample`。
- `fit_lgb` / `fit_xgb`：`random_seed` 临时写入 `LIGHTGBM_RANKER_SEED`；并行 fold 时 cap `num_threads`/`nthread`。
- `fit_estimator`：`random_forest` 且 seed ≠ 123 时走补丁里的 `RandomForestRegressor`。
- 保留 `_attach_label_impl` / `_fit_lgb_impl` / `_fit_xgb_impl` / `_fit_estimator_impl`。

### backtest_workbench

- `validate`：`kind=rule_signal` 走 `validate_rule_config`，不走 pyc validate。
- 训练/验证/测试窗口不重叠；验证窗并入 `hyperparameters`。
- 股票 pretrade 表达式合并进 train/test/validation filter。
- 默认 `random_seed=123`；`test_usage_count` 统计已完成同测试窗次数。
- `submit`：已有 `submission_token` 直接交给原 submit；否则写入 `test_usage_count`。
- `preview`：**整段以补丁为准**（规范列名、表达式字段、SMA、阶段剩余数量）。丢掉 pyc 的 preview。

### backtest_job

- `attach_label`：once 路径套 `apply_split_embargo`。
- `execute`：先设基准 pretrade 允许日期；`kind=rule_signal` → `execute_rule_signal`；`walk_forward_mode==once` 时 `once_label_embargo` 再调核心 execute。
- `_filter`：先跑 pyc 过滤，再 `apply_section_stock_scope`、`drop_unverified_halt_rows`、表达式、`allowed_dates`。
- `_load_frame`：pyc 读盘 → `attach_pack_factor_columns` → `_attach_config_sma`。这是宽表可算因子旁路，禁止丢。
- `_rolling_predictions`：**整段以补丁为准**（`fold_worker_count` + `booster_thread_limit` + 线程池）。丢掉 pyc 版。
- `run_portfolio` 包一层 `capture_predictions`，结束清 `_quantlab_day_index`。
- `_performance_metrics`：pyc 指标 + `attach_segment_curves` + `attach_ranking_metrics` + sortino/calmar/年报。
- `attach_sma`：**补丁版**（按标的 rolling mean，不是 expanding）。
- `_now`：`timespec="milliseconds"`（pyc 是 seconds）。
- `_step`：`started_at=COALESCE(started_at, ?)`。

---

## 合并规则（每个符号四选一）

- **KEEP_PYC**：只有字节码有，补丁没碰。反编译进生产文件。
- **KEEP_PATCH**：补丁整段替换。用当前 `.py` 补丁源码，反编译对应函数丢掉。
- **WRAP**：补丁调用原实现。反编译函数改名为 `_xxx_impl` / `_xxx_core`，补丁包装留在模块顶层或类上。
- **KEEP_PATCH_NEW**：只存在于补丁的新函数，原样搬入。

### trade_filters

| 符号 | 规则 |
|---|---|
| `tokenize`, `_Parser`, `_eval_tree`, `_value`, `_resolve_name`, `expression_fields`, `needs_sma200`, `split_open_filters`, `normalize_trade_filters`, `open_expressions`, 常量 `FIELD_ALIASES`/`FILL_FIELDS`/`DEFAULT_OPEN_LIMIT_EXPR`/`DERIVED_FIELDS` | KEEP_PYC |
| `parse_expr` | WRAP：pyc 体 → `_parse_expr_impl`；补丁 `parse_expr` 先 `_normalize_bool_ops` |
| `eval_open_filters`, `_normalize_bool_ops`, `_parsed_open_filters` | KEEP_PATCH / KEEP_PATCH_NEW |
| `SourcelessFileLoader`, `_pyc`, `_code` | 删除 |

### portfolio

| 符号 | 规则 |
|---|---|
| `_finite_price`, `_at_limit`, `_norm_date`, `_mark`, `_offset_date`, `_held_count`, `_fill_orders`, `_fit_buy`, `_close_lots`, `run_portfolio` | KEEP_PYC |
| `_day_rows`, `_DayIndex`, `_RowView`, `_DayRows`, `_cell`, `build_day_index`, `index_day_rows`, `_cached_day_index`, `_DAY_INDEX_ATTR` | KEEP_PATCH |
| `eval_open_filters` 再导出 | KEEP_PATCH（从 `trade_filters` import） |

### model_training

| 符号 | 规则 |
|---|---|
| 全部常量、`normalize_*`、`enrich_feature_columns`、`_bin_future_return`、`predict_*`、`ModelTrainingService` 及其方法 | KEEP_PYC |
| pyc `attach_label` / `fit_lgb` / `fit_xgb` / `fit_estimator` | WRAP 为 `_attach_label_impl` 等 |
| 补丁 `attach_label`, `fit_lgb`, `fit_xgb`, `fit_estimator`, `holdout_split`, `validation_date_cutoff`, `restrict_fit_sample`, `booster_thread_*`, `_call_with_capped_train` | KEEP_PATCH / KEEP_PATCH_NEW |

### backtest_workbench

| 符号 | 规则 |
|---|---|
| `_now`, `_json`, `_as_bool`, `_ymd`, `_format_like`, `__init__`, `_bind_published_model_and_strategy`, `save_draft`, `get_draft`, `get` | KEEP_PYC |
| pyc `validate` / `submit` | WRAP 为 `_validate_core` / `_submit_core`（实例方法） |
| 补丁 `validate` / `submit` / `preview` 及所有 `_compact_date` 等辅助函数 | KEEP_PATCH |
| pyc `preview` | 丢弃 |

合并后类上赋值：

```python
BacktestWorkbenchService.validate = validate
BacktestWorkbenchService.submit = submit
BacktestWorkbenchService.preview = preview
```

保持与现在相同的绑定方式，避免漏绑。

### backtest_job

| 符号 | 规则 |
|---|---|
| `period_train_folds`, `monthly_train_folds`, `walk_forward_mode`, 日历函数, 基准/`fill_benchmark_metrics`, pyc `_performance_metrics` 体, `factor_rank_*`, `STEPS`, `BacktestJobService.submit/_path_and_row/_prepare_steps/_factor_field/_snapshot_rows/_requeue_failed/get/_step_status` | KEEP_PYC |
| pyc `_filter` | WRAP → `_filter_core` |
| pyc `execute` | WRAP → `_execute_core` |
| pyc `_load_frame` | WRAP → `_load_frame_core` |
| pyc `_rolling_predictions`, pyc `attach_sma`, pyc `_now`, pyc `_step`, pyc `attach_label` | 丢弃（KEEP_PATCH） |
| 补丁 `once_label_*`, `apply_*`, `eval_expression_mask`, `attach_sma`, `_filter`, `execute`, `_rolling_predictions`, `_now`, `_step`, `_load_frame_with_pack_factors`, 额外指标等 | KEEP_PATCH / KEEP_PATCH_NEW |
| `_original_rolling_predictions` 等生产导出 | 删除；测试按 Task 6 改 |

`execute` 合并后形态（逻辑与当前补丁相同，只是核心从 pyc 变成 `_execute_core`）：

```python
def execute(self, run_id: str):
    row = self.get(run_id)
    config = (row or {}).get("config") or {}
    token = _pretrade_allowed_dates.set(_benchmark_allowed_dates(self, config))
    try:
        if config.get("kind") == "rule_signal":
            from quantlab.services.rule_backtest import execute_rule_signal
            return execute_rule_signal(self, run_id)
        if walk_forward_mode(config) != "once":
            return _execute_core(self, run_id)
        test = config.get("test") or {}
        predict_from = test.get("date_from")
        if not predict_from:
            return _execute_core(self, run_id)
        hyper = config.get("hyperparameters") if isinstance(config.get("hyperparameters"), dict) else {}
        validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
        validation_from = hyper.get("validation_date_from") or validation.get("date_from")
        with once_label_embargo(predict_from, config.get("holding_days"), validation_from=validation_from):
            return _execute_core(self, run_id)
    finally:
        _pretrade_allowed_dates.reset(token)

BacktestJobService.execute = execute
```

`_load_frame` 合并后：

```python
def _load_frame_with_pack_factors(self, path, config):
    from quantlab.services.canonical_pack_factors import attach_pack_factor_columns, default_sidecar_path
    frame = _load_frame_core(self, path, config)
    refs = (config or {}).get("factor_versions") or []
    frame = attach_pack_factor_columns(frame, refs, default_sidecar_path(path))
    return _attach_config_sma(frame, config)

BacktestJobService._load_frame = _load_frame_with_pack_factors
```

---

## 文件地图

| 文件 | 职责 |
|---|---|
| Modify: `quantlab/services/trade_filters.py` | 完整源码，去掉 loader |
| Modify: `quantlab/services/portfolio.py` | 完整源码，去掉 loader |
| Modify: `quantlab/services/model_training.py` | 完整源码，去掉 loader |
| Modify: `quantlab/services/backtest_workbench.py` | 完整源码，去掉 loader |
| Modify: `quantlab/services/backtest_job.py` | 完整源码，去掉 loader |
| Delete: `quantlab/services/_recovered_pyc/*.pyc` 及目录 | 迁移完成后 |
| Modify: `pyproject.toml` | 删除 `force-include` 整段 |
| Modify: `.gitignore` | 删除 `_recovered_pyc` 例外 |
| Modify: `README.md` | 删除「以 `.pyc` 运行」一句 |
| Modify: `tests/quantlab/test_rolling_fold_parallel.py` | 去掉 `_original_rolling_predictions` |
| Scratch: `.tmp_pyc_recovery/` | oracle 加载器、反编译初稿、对照脚本；不进 git |

下游只消费公开 API，还原时不要改这些文件的调用方式：`quantlab/api/app.py`、`backtest_plan.py`、`backtest_control.py`、`rule_backtest.py`、`rule_portfolio.py`、`result_archive.py`、`bucket_equity.py`、`canonical_pack_factors.py`。

---

## Chunk 1: Oracle 与反编译工具

### Task 1: 固化 oracle 加载器

**Files:**

- Create: `.tmp_pyc_recovery/oracle/{trade_filters,portfolio,model_training,backtest_workbench,backtest_job}.py`（gitignored）
- Create: `.tmp_pyc_recovery/snapshot_oracle.py`

**Produces:** 可独立 import 的当前行为副本；`.pyc` 用绝对路径，不依赖 oracle 文件位置。

- [ ] **Step 1: 建目录并复制五个生产 `.py`**

Run:

```bash
mkdir -p .tmp_pyc_recovery/oracle .tmp_pyc_recovery/decomp .tmp_pyc_recovery/merged
cp quantlab/services/trade_filters.py .tmp_pyc_recovery/oracle/trade_filters.py
cp quantlab/services/portfolio.py .tmp_pyc_recovery/oracle/portfolio.py
cp quantlab/services/model_training.py .tmp_pyc_recovery/oracle/model_training.py
cp quantlab/services/backtest_workbench.py .tmp_pyc_recovery/oracle/backtest_workbench.py
cp quantlab/services/backtest_job.py .tmp_pyc_recovery/oracle/backtest_job.py
```

- [ ] **Step 2: 把 oracle 里的 `_pyc` 改成绝对路径**

五个文件都将：

```python
_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "<name>.pyc"
```

改成（路径按仓库根展开）：

```python
_pyc = Path("/Volumes/T2/quantlab/quantlab/services/_recovered_pyc/<name>.pyc")
```

`__name__` 保持加载器传入的模块名即可。

- [ ] **Step 3: 写对照加载辅助**

`.tmp_pyc_recovery/snapshot_oracle.py`：

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path("/Volumes/T2/quantlab")
ORACLE = ROOT / ".tmp_pyc_recovery" / "oracle"


def load_oracle(name: str):
    path = ORACLE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"oracle_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"candidate_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 4: 确认 oracle 能加载且不含生产模块污染**

Run:

```bash
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, ".")
from pathlib import Path
import importlib.util
p = Path(".tmp_pyc_recovery/oracle/trade_filters.py")
spec = importlib.util.spec_from_file_location("oracle_trade_filters", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert callable(m.parse_expr)
assert m.parse_expr("st_status==0 & close>low") == m.parse_expr("st_status==0 AND close>low")
print("oracle trade_filters ok", m.parse_expr)
PY
```

Expected: 打印 `oracle trade_filters ok`，无 `bad magic number`。

### Task 2: 准备 3.12 反编译器并出五份初稿

**Files:**

- Create: `.tmp_pyc_recovery/decomp/*.py`

- [ ] **Step 1: 检测/安装 pycdc（不写入项目依赖）**

Run:

```bash
command -v pycdc || echo "NEED_PYCDC"
```

若没有：用临时目录编译 [zrax/pycdc](https://github.com/zrax/pycdc)，或 `pip install pylingual` 到一次性 venv。不要改 `pyproject.toml`。

- [ ] **Step 2: 反编译五份 pyc**

```bash
for name in trade_filters portfolio model_training backtest_workbench backtest_job; do
  pycdc quantlab/services/_recovered_pyc/${name}.pyc > .tmp_pyc_recovery/decomp/${name}.py
done
wc -l .tmp_pyc_recovery/decomp/*.py
```

Expected: 五个文件非空。`trade_filters` 应出现 `class _Parser`；`portfolio` 应出现 `def run_portfolio`；`backtest_job` 应出现 `class BacktestJobService`。若某文件几乎是 `Unsupported` / 空，对该文件改用 pylingual，再不行则用 `.venv/bin/python -m dis` 对照 `co_consts` 手工补。

- [ ] **Step 3: 记录反编译缺陷，不要在这一步改生产文件**

把明显语法错误留在 `decomp/`。生产合并在后续 Task 手工做。

---

## Chunk 2: trade_filters → portfolio

### Task 3: 还原 `trade_filters.py`

**Files:**

- Modify: `quantlab/services/trade_filters.py`（整文件替换为源码）
- Test: `tests/quantlab/test_pretrade_filters.py`
- Test: `tests/quantlab/test_portfolio_day_index.py`

**Interfaces:**

- Consumes: 无（最底层）
- Produces: `parse_expr`, `eval_open_filters`, `_eval_tree`, `needs_sma200`, `FIELD_ALIASES`, `open_expressions`, `split_open_filters`, `normalize_trade_filters`, `expression_fields`

- [ ] **Step 1: 合并草稿到 `.tmp_pyc_recovery/merged/trade_filters.py`**

结构顺序：

1. 模块 docstring 改成一句正常描述，例如「开仓过滤表达式解析与求值。」
2. `from __future__ import annotations` + `import re` + 原 pyc 的 typing
3. KEEP_PYC 常量与 parser
4. `_parse_expr_impl` = 反编译的 `parse_expr` 函数体
5. 当前补丁的 `_normalize_bool_ops` / `parse_expr` / `_parsed_open_filters` / `eval_open_filters`

不要留下 `SourcelessFileLoader`。

- [ ] **Step 2: 对照 oracle 的 AST**

```bash
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, ".")
import importlib.util
from pathlib import Path

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

oracle = load("oracle_tf", Path(".tmp_pyc_recovery/oracle/trade_filters.py"))
cand = load("cand_tf", Path(".tmp_pyc_recovery/merged/trade_filters.py"))
exprs = [
    "st_status==0 AND close>low",
    "st_status==0 & close>low",
    "st_status==0 && close>low",
    "open < up_limit AND open > down_limit",
    "NOT suspended",
    "hfq_close > sma200",
]
for e in exprs:
    assert oracle.parse_expr(e) == cand.parse_expr(e), e
row = {"st_status": 0, "close": 2, "low": 1, "open": 10, "up_limit": 11, "down_limit": 9}
assert oracle.eval_open_filters(["st_status==0 & close>low"], row) is True
assert cand.eval_open_filters(["st_status==0 & close>low"], row) is True
print("trade_filters candidate matches oracle")
PY
```

Expected: `trade_filters candidate matches oracle`

- [ ] **Step 3: 覆盖生产文件并跑测试**

```bash
cp .tmp_pyc_recovery/merged/trade_filters.py quantlab/services/trade_filters.py
.venv/bin/python -m pytest tests/quantlab/test_pretrade_filters.py tests/quantlab/test_portfolio_day_index.py -q --tb=short
```

Expected: PASS。`inspect.getsource(tokenize)` 不再 `lineno is out of bounds`。

### Task 4: 还原 `portfolio.py`

**Files:**

- Modify: `quantlab/services/portfolio.py`
- Test: `tests/quantlab/test_portfolio_day_index.py`
- Test: `tests/quantlab/test_bucket_equity.py`

**Consumes:** Task 3 的 `eval_open_filters` / `open_expressions` / `split_open_filters`

- [ ] **Step 1: 合并草稿**

`.tmp_pyc_recovery/merged/portfolio.py`：

1. KEEP_PYC 辅助函数 + `run_portfolio`（含内部 `record_unfilled` / `sell_instrument` / `apply_pending`）
2. 用当前文件里的 `_DayIndex` 整段替换反编译的 `_day_rows`
3. `from quantlab.services.trade_filters import eval_open_filters`
4. 不要恢复 pyc 的 `loc`+`iterrows` `_day_rows`

- [ ] **Step 2: 对照 oracle 小样本 `run_portfolio`**

用 `tests/quantlab/test_portfolio_day_index.py` 里 `test_run_portfolio_keeps_slot_engine_fills_on_small_fixture` 的同一 fixture：oracle 与 candidate 的 `trades`/`curve` 必须相等（可用 `pandas.testing.assert_frame_equal` 或 `==` 比 list[dict]）。

- [ ] **Step 3: 覆盖生产文件并跑测试**

```bash
cp .tmp_pyc_recovery/merged/portfolio.py quantlab/services/portfolio.py
.venv/bin/python -m pytest tests/quantlab/test_portfolio_day_index.py tests/quantlab/test_bucket_equity.py tests/quantlab/test_pretrade_filters.py -q --tb=short
```

Expected: PASS。`run_portfolio` 可 `inspect.getsource`。

---

## Chunk 3: model_training → workbench

### Task 5: 还原 `model_training.py`

**Files:**

- Modify: `quantlab/services/model_training.py`
- Test: `tests/quantlab/test_model_training.py`
- Test: `tests/quantlab/test_rolling_fold_parallel.py`

- [ ] **Step 1: 合并草稿**

保留补丁包装名，测试依赖它们：

```python
_attach_label_impl = ...  # 反编译 attach_label 体
_fit_lgb_impl = ...
_fit_xgb_impl = ...
_fit_estimator_impl = ...
```

然后贴上当前补丁的 `attach_label` / `holdout_split` / `fit_lgb` / `fit_xgb` / `fit_estimator` / `booster_thread_*`。`ModelTrainingService` 全部 KEEP_PYC。

- [ ] **Step 2: 对照标签分箱**

对 `tests/quantlab/test_model_training.py` 的 `_panel()`，oracle 与 candidate 的 `_raw`、`_target` 必须一致。

- [ ] **Step 3: 覆盖并测试**

```bash
cp .tmp_pyc_recovery/merged/model_training.py quantlab/services/model_training.py
.venv/bin/python -m pytest tests/quantlab/test_model_training.py tests/quantlab/test_once_label_embargo.py tests/quantlab/test_rolling_fold_parallel.py -q --tb=short
```

Expected: PASS。此时 `test_rolling_predictions_workers_one_matches_original` 仍可通过，因为它还走 `backtest_job` 的 pyc `_original_rolling_predictions`。

### Task 6: 还原 `backtest_workbench.py`

**Files:**

- Modify: `quantlab/services/backtest_workbench.py`
- Test: `tests/quantlab/test_backtest_workbench.py`
- Test: `tests/quantlab/test_rule_backtest.py`

- [ ] **Step 1: 合并草稿**

- pyc `validate` → 函数 `_validate_core(self, raw)`，再 `BacktestWorkbenchService._validate_core = _validate_core`
- 补丁 `validate` 里把 `_original_validate(self, config)` 改成 `self._validate_core(config)`
- `submit` 同样：`_submit_core`
- `preview` 只用当前补丁，不合并 pyc preview
- `save_draft` / `get_draft` / `get` KEEP_PYC

- [ ] **Step 2: 对照 validate 冻结配置**

用 `test_config_normalizes_and_freezes_and_rejects_invalid_scope` 同输入：oracle 与 candidate 的 `validate` 输出 dict 相等（忽略无关键顺序则 `==` 即可，两边都 `json.dumps(..., sort_keys=True)`）。另测 `kind=rule_signal` 走规则校验、非法重叠验证窗抛 `ValueError`。

- [ ] **Step 3: 覆盖并测试**

```bash
cp .tmp_pyc_recovery/merged/backtest_workbench.py quantlab/services/backtest_workbench.py
.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_web_shell.py -q --tb=short
```

Expected: PASS。

---

## Chunk 4: backtest_job 与拆除字节码

### Task 7: 还原 `backtest_job.py`

**Files:**

- Modify: `quantlab/services/backtest_job.py`（当前 674 行加载器+补丁 → 单一源码，预计 1200–2000 行，允许保持单文件，与现状一致）
- Modify: `tests/quantlab/test_rolling_fold_parallel.py`
- Test: `tests/quantlab/test_pretrade_filters.py`
- Test: `tests/quantlab/test_once_label_embargo.py`
- Test: `tests/quantlab/test_pack_factor_materialize.py`
- Test: `tests/quantlab/test_backtest_workbench.py`
- Test: `tests/quantlab/test_performance_metrics.py`
- Test: `tests/quantlab/test_run_record.py`

- [ ] **Step 1: 合并草稿**

按上文 backtest_job 合并表。特别检查：

- `_load_frame_core` 之后必须调用 `attach_pack_factor_columns`
- `execute` 必须先 `rule_signal` 再 `once` embargo
- `attach_sma` 用补丁（rolling），不要反编译稿
- `_now` 毫秒；`_step` 带 `COALESCE`
- 删除模块级 `_original_execute` / `_original_filter` / `_original_load_frame` / `_original_rolling_predictions` 导出
- 去掉重复的 `_attach_label = attach_label` / `_once_embargo = None` 双赋值，只留一份

- [ ] **Step 2: 在删除 `_original_rolling_predictions` 前用 oracle 对照 fold**

对 `test_rolling_fold_parallel.py` 的 `_panel()` / `_config()`：candidate `_rolling_predictions`（workers=1）与 **oracle 模块当前补丁版**（不是裸 pyc）分数一致。不要对照裸 pyc——那会把最新并行/过滤逻辑判错。

- [ ] **Step 3: 改对照测试，去掉生产模块对 `_original_*` 的依赖**

把 `test_rolling_predictions_workers_one_matches_original` 改成「workers=1 与默认并行路径分数一致」，复用文件里已有的 `_panel`/`_split`/`_config`：

```python
def test_rolling_predictions_workers_one_matches_original(monkeypatch) -> None:
    frame = _panel()
    job = BacktestJobService.__new__(BacktestJobService)
    train, test = _split(frame)
    kwargs = dict(
        frame=frame,
        train=train,
        test=test,
        config=_config(),
        kind="ridge_linear",
        params={"alpha": 1.0, "random_seed": 123},
        feature_fields=["momentum_5"],
        holding_days=2,
        filter_notes=[],
    )
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "1")
    serial_pred, _, serial_folds = job._rolling_predictions(**kwargs)
    monkeypatch.setenv("QUANTLAB_FOLD_WORKERS", "4")
    parallel_pred, _, parallel_folds = job._rolling_predictions(**kwargs)
    left = serial_pred.sort_values(["date", "instrument"]).reset_index(drop=True)
    right = parallel_pred.sort_values(["date", "instrument"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)
    assert [row["month"] for row in serial_folds] == [row["month"] for row in parallel_folds]
    assert [row["train_rows"] for row in serial_folds] == [row["train_rows"] for row in parallel_folds]
```

若与 `test_rolling_predictions_parallel_scores_match_serial` 完全重复，则删除本测试，只保留一条并行对照。不要同时留两个同义测试。

- [ ] **Step 4: 覆盖生产文件并跑回测相关测试**

```bash
cp .tmp_pyc_recovery/merged/backtest_job.py quantlab/services/backtest_job.py
.venv/bin/python -m pytest \
  tests/quantlab/test_pretrade_filters.py \
  tests/quantlab/test_once_label_embargo.py \
  tests/quantlab/test_rolling_fold_parallel.py \
  tests/quantlab/test_backtest_workbench.py \
  tests/quantlab/test_rule_backtest.py \
  tests/quantlab/test_performance_metrics.py \
  tests/quantlab/test_bucket_equity.py \
  tests/quantlab/test_run_record.py \
  tests/quantlab/test_pack_factor_materialize.py \
  tests/quantlab/test_portfolio_day_index.py \
  tests/quantlab/test_model_training.py \
  tests/quantlab/test_settings_page.py \
  -q --tb=short
```

Expected: PASS。`inspect.getsource(period_train_folds)` 指向本仓库 `quantlab/services/backtest_job.py`，不再指向 vnpy worktree。

### Task 8: 删除字节码基础设施并全量验收

**Files:**

- Delete: `quantlab/services/_recovered_pyc/backtest_job.pyc`
- Delete: `quantlab/services/_recovered_pyc/backtest_workbench.pyc`
- Delete: `quantlab/services/_recovered_pyc/model_training.pyc`
- Delete: `quantlab/services/_recovered_pyc/portfolio.pyc`
- Delete: `quantlab/services/_recovered_pyc/trade_filters.pyc`
- Delete: `quantlab/services/_recovered_pyc/`（空目录）
- Modify: `pyproject.toml` — 删除 `[tool.hatch.build.targets.wheel.force-include]` 整节
- Modify: `.gitignore` — 删除：

```
# Recovered module bytecode while source is missing
*.pyc
!quantlab/services/_recovered_pyc/
!quantlab/services/_recovered_pyc/*.pyc
```

改为只保留常规 `*.pyc` 忽略（已有 `__pycache__/` 即可；若仍想忽略散落 pyc，留一行 `*.pyc`，**不要**再 un-ignore）。
- Modify: `README.md` — 删除「部分训练/回测模块目前以恢复出的 `.pyc` 运行…」整句。

- [ ] **Step 1: 删除字节码与打包例外**

```bash
rm -rf quantlab/services/_recovered_pyc
```

`pyproject.toml` 删掉整个 `[tool.hatch.build.targets.wheel.force-include]` 节。`.gitignore` 删掉 `_recovered_pyc` 的三行例外，保留 `__pycache__/` 与 `*.pyc`。`README.md` 删掉加载器说明那一句。

- [ ] **Step 2: 仓库内检索必须为零**

```bash
rg -n "_recovered_pyc|SourcelessFileLoader" quantlab tests pyproject.toml README.md .gitignore
```

Expected: 无匹配。`docs/plans/` 里允许保留历史叙述，不要搜进去当失败。

- [ ] **Step 3: 对五个还原文件跑 ruff（只修语法/未使用导入，不改行为）**

```bash
.venv/bin/ruff check quantlab/services/trade_filters.py quantlab/services/portfolio.py quantlab/services/model_training.py quantlab/services/backtest_workbench.py quantlab/services/backtest_job.py
```

Expected: 无新增 E/F 错误。反编译残留的未使用 import、重复赋值必须删掉。不要为了「更干净」改算法。

- [ ] **Step 4: 全量单测（不含浏览器）**

```bash
.venv/bin/python -m pytest tests/quantlab -q --tb=short --ignore=tests/quantlab/browser
```

Expected: 全部 PASS。浏览器测试不在本 P0 必跑范围；若环境已有 Playwright，可加跑 `tests/quantlab/browser` 作回归，失败则只修还原引入的断裂。

- [ ] **Step 5: IDE/inspect 抽查**

```bash
.venv/bin/python - <<'PY'
import inspect
from quantlab.services.trade_filters import tokenize, parse_expr
from quantlab.services.portfolio import run_portfolio
from quantlab.services.model_training import ModelTrainingService, fit_lgb
from quantlab.services.backtest_workbench import BacktestWorkbenchService
from quantlab.services.backtest_job import period_train_folds, BacktestJobService

checks = [
    tokenize, parse_expr, run_portfolio, fit_lgb,
    ModelTrainingService.create_design,
    BacktestWorkbenchService.save_draft,
    BacktestWorkbenchService.validate,
    period_train_folds,
    BacktestJobService.execute,
    BacktestJobService._load_frame,
]
for fn in checks:
    src = inspect.getsourcefile(fn)
    assert src and src.endswith(".py") and "_recovered_pyc" not in src, (fn, src)
    assert "/vnpy/.worktrees/" not in src, (fn, src)
    inspect.getsourcelines(fn)
print("inspect ok")
PY
```

Expected: `inspect ok`

- [ ] **Step 6: 清 scratch（可选）**

```bash
rm -rf .tmp_pyc_recovery
```

不删也可以，已被 gitignore。

---

## 验收标准

- `quantlab/`、`tests/`、`pyproject.toml`、`README.md`、`.gitignore` 不再出现 `_recovered_pyc` / `SourcelessFileLoader`。
- 五个模块是完整 `.py`，无 `exec` 字节码。
- `.venv/bin/python -m pytest tests/quantlab --ignore=tests/quantlab/browser` 通过。
- `inspect.getsource` 对回测 DAG、槽位 `run_portfolio`、训练 `create_design` 成功，且文件在本仓库。
- 下列最新逻辑仍在：规则策略分发、once embargo、日索引、pack 因子旁路、pretrade/SMA rolling、并行 fold、默认 seed 123、`_now` 毫秒。

## 回退（完整回到本方案实施前）

冻结点（实施前已钉在远程）：

- Commit: `b2ef3f1d88263958768353ac2c61cafe6d0cac20`
- Tag: `restore-before-pyc-source`
- Branch: `restore/before-pyc-source`

这是当前 `main` / `origin/main` 的完整快照（含五个 `.pyc` 加载器与全部最新补丁）。还原后程序与现在一致。

### 实施中途（尚未 commit）

单个模块失败：

```bash
git checkout -- quantlab/services/<module>.py
```

oracle 与 `quantlab/services/_recovered_pyc/` 仍在。不要提前删 `.pyc`。

全部丢弃未提交改动（不删 gitignore 的 `.tmp_pyc_recovery`）：

```bash
git checkout -- quantlab tests pyproject.toml README.md .gitignore
```

### 已经本地 commit、尚未 push

```bash
git switch main
git reset --hard restore-before-pyc-source
```

工作区会回到 `b2ef3f1`。未跟踪的 `.tmp_pyc_recovery/` 和本计划文件会留下，可手动 `rm -rf .tmp_pyc_recovery`。

### 已经 push 到 origin/main

不要默认 force-push。优先用新 commit 回到冻结点：

```bash
git switch main
git fetch origin
git restore --source=restore-before-pyc-source --worktree --staged -- .
git status
# 确认五个 .pyc 加载器已回来后，再由用户明确要求时才 commit
```

若用户明确要求远程也回到这一版（会改写 `main` 历史）：

```bash
git switch main
git reset --hard restore-before-pyc-source
git push --force-with-lease origin main
```

未获用户当面允许，禁止 `--force` / `--force-with-lease`。

### 验证已回到冻结点

```bash
git rev-parse HEAD
# 期望：b2ef3f1d88263958768353ac2c61cafe6d0cac20

test -f quantlab/services/_recovered_pyc/backtest_job.pyc
rg -n "SourcelessFileLoader" quantlab/services/backtest_job.py quantlab/services/trade_filters.py
```

## 明确不做

- 不升级/降级 Python、不改 hatch 其它配置。
- 不把 `backtest_job.py` 拆成多文件（可另开 P1）。
- 不重写槽位撮合语义、不「优化」`run_portfolio`。
- 不改 `canonical_pack_factors.py` / `backtest_plan.py` / 规则策略模块，除非还原后 import 破裂（不应发生）。
- 不主动 commit / push。
