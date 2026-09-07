# BQ 对齐：过滤、排序树、账户撮合、指标 Implementation Plan

> **For agentic workers:** REQUIRED: Use `subagent-driven-development` (if subagents available) or `executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 回测按页面超参训练；训/测过滤对齐 BQ 的 `st_status==0 & close>low` 并加入涨跌停；训练失败即停；树模型改排序目标；撮合改成目标权重账户；运行记录补资金曲线、超额、换手、资金占用。

**Architecture:** 超参与过滤走现有 `config` 冻结路径（页面 → `BacktestWorkbenchService.validate` → `backtest_runs.config_json` → `BacktestJobService.execute`）。排序训练仍在 `model_training.py`。账户撮合从 `backtest_job._execute_events` 抽到 `quantlab/services/portfolio.py`，按交易日盯市。指标写进 `metrics.json`，运行记录页用已有 `lightweight-charts` 画曲线。

**Tech Stack:** FastAPI 静态页、LightGBM `LGBMRanker`、pandas、canonical.parquet（raw `open/high/low/close/up_limit/down_limit`）、pytest、Playwright。

**本计划不做：** 因子公式与 BQ 特征集对齐；成交量限制；印花税改成与 BQ 默认一致（页面已有 0.001，保持）。标签 1%/99% 截尾 + 20 档只作为排序标签的必要预处理，不单独做成页面开关。

---

## 已锁定口径

### 页面超参必须原样进训练

路径已经是：回测页 `backtestHyperparams()` → `config.hyperparameters` → `normalize_hyperparams` → `fit_lgb`。

**现存静默截断：** `normalize_hyperparams` 把 `max_bins` 卡在 255；模型中心 / 回测页 `max="255"`。页面填 511 会变成 255。`min_child_samples` 没有上限，填 1000 可以进训练，但出厂 HTML 仍是 20。

**本计划：** 取消 255 上限（允许 16–511）；页面 `max` 改为 511；出厂默认改成 BQ 常用值（`max_bins=511`、`min_child_samples=1000`、`num_leaves=30`）。已保存在库里的种类默认仍以目录/草稿为准，不被出厂值覆盖。

### 训/测过滤（页面展示 + 写入 config.filter）

宽表已有 raw：`close`、`low`、`high`、`up_limit`、`down_limit`。

默认同时打开（训、测相同）：

| 开关 | 条件 | 含义 |
|---|---|---|
| `st_status == 0` | 已有 | 非 ST |
| `suspended is False` | 已有 | 非停牌 |
| `close_gt_low` | `close > low` | 与 BQ `close>low` 相同；一字板（high=low=close）会被去掉 |
| `skip_limit_close` | `close < up_limit` 且 `close > down_limit` | 收盘未触及涨跌停价 |

缺列时：该条过滤跳过并在训练日志/失败原因里写明，不静默当 True。

页面「训练集过滤 / 测试集过滤」改为只读说明，文案：`st_status=0，非停牌，收盘>最低价，收盘未涨跌停`。`configValue()` 把上述四个开关写入 `train.filter` / `test.filter`。

### 训练失败即停

`kind != factor_rank` 时：样本不足、缺标签、LightGBM/sklearn 异常、拟合抛错 → `status=failed`，中文原因，不写成交、不退化成单因子排序。`factor_rank` 仍不训练。

### 树模型 = 排序（模型中心 + 回测）

`lightgbm_tree` 改为 `LGBMRanker`（lambdarank），按 `date` 分组。标签：后复权 `hfq_close[t+2]/hfq_open[t+1]-1`，1%/99% 截尾，20 档整数相关度；T+1 `high==low` 的样本标签为空（不进训练）。线性 / 随机森林 / Huber 仍回归。模型中心文案改为「回测时按截面排序训练」。

### 目标权重账户

信号日 S（测试集交易日，间隔 `rebalance_every`）用当日分数取 Top N，目标权重等权（`weighting=score` 时按分数归一）。下一交易日 E 开盘买入/加仓；离开组合的股票在 S 收盘卖出。仓位按**调仓时权益**，不是 `初始本金/N`。涨跌停规则仍作用于该笔开盘买 / 收盘卖。每日收盘盯市，生成权益曲线。

### 指标与图

在现有累计/年化/夏普/回撤/胜率/基准之外增加：超额收益、日均换手、日均资金占用。运行记录增加权益 vs 基准曲线（`/assets/vendor/lightweight-charts.standalone.production.js`）。

---

## 文件地图

| 文件 | 职责 |
|---|---|
| `quantlab/services/model_training.py` | 放开 `max_bins`；`attach_label` 截尾/分箱/一字板；`fit_lgb` 改 Ranker |
| `quantlab/services/backtest_workbench.py` | 校验 filter 开关、超参范围 |
| `quantlab/services/backtest_job.py` | 过滤、训练失败即停、调用账户引擎、写出曲线指标 |
| Create: `quantlab/services/portfolio.py` | 目标权重账户 + 日度权益 |
| `quantlab/services/result_archive.py` | 新指标中文展示 |
| `quantlab/web/pages/models.html` | 出厂 max/默认值、树模型说明 |
| `quantlab/web/pages/backtest_workbench_formal.html` | 过滤文案、`max_bins`、filter 写入 config |
| `quantlab/web/assets/app.js` | `max_bins` 读取不再按 255 理解；种类说明 |
| `quantlab/web/pages/backtest_run_record.html` | 曲线图 + 新指标 |
| `quantlab/web/assets/app.css` | 曲线容器高度 |
| `tests/quantlab/test_model_training.py` | 超参透传、Ranker |
| `tests/quantlab/test_backtest_workbench.py` | 过滤、失败即停、账户、指标 |
| `tests/quantlab/browser/test_model_kinds_browser.py` 或新建 `test_backtest_filters_browser.py` | 页面过滤文案、511 可填入 |

---

## Chunk 1: 页面超参原样进训练

### Task 1: `max_bins=511` 与 `min_child_samples=1000` 不被截断

**Files:**
- Modify: `quantlab/services/model_training.py`（`DEFAULT_HYPERPARAMS`、`normalize_hyperparams`）
- Modify: `quantlab/web/pages/models.html`、`quantlab/web/pages/backtest_workbench_formal.html`
- Test: `tests/quantlab/test_model_training.py`

- [ ] **Step 1: 写失败测试**

```python
def test_normalize_hyperparams_keeps_page_bins_and_leaf_samples():
    out = normalize_hyperparams(
        {"number_of_trees": 5, "max_bins": 511, "num_leaves": 30,
         "min_child_samples": 1000, "learning_rate": 0.1},
        kind="lightgbm_tree",
    )
    assert out["max_bins"] == 511
    assert out["min_child_samples"] == 1000
    assert out["num_leaves"] == 30
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_model_training.py::test_normalize_hyperparams_keeps_page_bins_and_leaf_samples -q --tb=short`

Expected: `assert 255 == 511`（或当前截断后的值）

- [ ] **Step 3: 实现**

`normalize_hyperparams` 中 lightgbm 分支改为：

```python
bins = max(16, min(511, int(src.get("max_bins") or defaults["max_bins"])))
leaves = max(2, int(src.get("num_leaves") or defaults["num_leaves"]))
samples = max(1, int(src.get("min_child_samples") or defaults["min_child_samples"]))
```

`DEFAULT_HYPERPARAMS`：`max_bins: 511`，`num_leaves: 30`，`min_child_samples: 1000`。

页面：`#model-bins` / `#bt-bins` 的 `max="511"` `value="511"`；`#model-min-samples` / `#bt-min-samples` `value="1000"`；`#model-leaves` / `#bt-leaves` `value="30"`。`backtestHyperparams()` 里 `max_bins` 的 fallback 从 255 改为 511，`min_child_samples` fallback 改为 1000。

- [ ] **Step 4: 再跑测试**

Run: `.venv/bin/python -m pytest tests/quantlab/test_model_training.py -q --tb=short`

Expected: PASS。另跑 `tests/quantlab/test_backtest_workbench.py::test_validate_*` 确认校验仍接受这些超参。

---

## Chunk 2: 训/测过滤 close>low 与涨跌停

### Task 2: 过滤函数 + validate 默认打开

**Files:**
- Modify: `quantlab/services/backtest_job.py` `_filter`
- Modify: `quantlab/services/backtest_workbench.py` `validate`（给 train/test.filter 补默认）
- Modify: `tests/quantlab/test_backtest_workbench.py` 的 `setup_env` parquet 增加 `high`/`low`
- Test: 同文件新测试

- [ ] **Step 1: 写失败测试**

在 `setup_env` 的表里加 `high`/`low`（已有 `up_limit`/`down_limit`）。造一行 `close==low`、一行 `close>=up_limit`、一行正常。

```python
def test_filter_drops_close_at_low_and_limit(tmp_path):
    s, db = setup_env(tmp_path)
    job = BacktestJobService(s, db)
    frame = pd.DataFrame({
        "date": ["20200102"] * 3,
        "instrument": ["aaa.SZ", "bbb.SZ", "ccc.SZ"],
        "close": [10.0, 10.0, 11.0],
        "low": [10.0, 9.0, 10.0],
        "high": [10.0, 10.0, 11.0],
        "up_limit": [11.0, 10.0, 12.0],
        "down_limit": [9.0, 9.0, 9.0],
        "st_status": [0, 0, 0],
        "suspended": [False, False, False],
    })
    out = job._filter(frame, {
        "date_from": "20200102", "date_to": "20200102",
        "filter": {"st_status": 0, "suspended": False, "close_gt_low": True, "skip_limit_close": True},
    })
    assert list(out["instrument"]) == ["ccc.SZ"]
```

再测 `validate`：未传这两个开关时默认 True。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py::test_filter_drops_close_at_low_and_limit -q --tb=short`

Expected: FAIL（当前 `_filter` 不读这两个键，三行都在）

- [ ] **Step 3: 实现**

`_filter` 在现有 ST/停牌之后：

```python
filt = section.get("filter", {})
if filt.get("close_gt_low", True) and {"close", "low"} <= set(result.columns):
    result = result.loc[pd.to_numeric(result["close"], errors="coerce") > pd.to_numeric(result["low"], errors="coerce")]
if filt.get("skip_limit_close", True) and {"close", "up_limit", "down_limit"} <= set(result.columns):
    close = pd.to_numeric(result["close"], errors="coerce")
    up = pd.to_numeric(result["up_limit"], errors="coerce")
    down = pd.to_numeric(result["down_limit"], errors="coerce")
    result = result.loc[(close < up) & (close > down)]
```

`validate`：对 `train`/`test` 的 `filter` 缺省写入 `close_gt_low=True`、`skip_limit_close=True`。

页面：`#train-filter` / `#test-filter` 的 value 改为 `st_status=0，非停牌，收盘>最低价，收盘未涨跌停`；`parseRange` 增加这两个开关。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: PASS。若旧执行测试因过滤掉样本而 0 笔成交，给 fixture 的正常行保证 `close>low` 且未涨跌停。

---

## Chunk 3: 训练失败即停

### Task 3: 非 factor_rank 不得退化成单因子

**Files:**
- Modify: `quantlab/services/backtest_job.py` `execute` 训练段
- Modify: `quantlab/services/result_archive.py` `_ERROR_ZH`（如需映射）
- Test: `tests/quantlab/test_backtest_workbench.py`

- [ ] **Step 1: 写失败测试**

用 `kind="lightgbm_tree"` 且训练窗只有 1 天（样本 < `MIN_TRAIN_ROWS`）：

```python
def test_training_failure_stops_run_without_factor_fallback(tmp_path):
    s, db = setup_env(tmp_path)
    run = BacktestWorkbenchService(s, db).submit({**config(), "kind": "lightgbm_tree"})
    result = BacktestJobService(s, db).execute(run["run_id"])
    assert result["status"] == "failed"
    assert "训练" in (result.get("error_message") or "")
    assert result.get("trades") in ([], None) or result["status"] == "failed"
```

（若当前 fixture 样本够 30 行，改为故意抽掉 `_target` 或把 `MIN_TRAIN_ROWS` 用超小窗；断言失败原因中文、steps 在 `model_training` 失败、后续 skipped。）

- [ ] **Step 2: 跑测试确认失败**

Expected: 当前实现 `status==completed` 且用因子当 score。

- [ ] **Step 3: 实现**

删除 `except (ValueError, KeyError, OSError): booster = None` 对训练路径的吞掉。

```python
if kind == "factor_rank":
    booster = None
else:
    labeled = attach_label(train)
    usable = labeled.dropna(subset=feature_fields + ["_target"])
    if len(usable) > MAX_TRAIN_ROWS:
        usable = downsample(usable)
    if len(usable) < MIN_TRAIN_ROWS:
        raise ValueError(f"训练样本不足（{len(usable)} 行，至少 {MIN_TRAIN_ROWS} 行）。")
    booster = fit_estimator(...)
    if booster is None:
        raise ValueError("模型没有训练出来。")
```

预测步：非 `factor_rank` 必须用 `booster`，禁止 `else: predictions = test[field]`。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: 新测试 PASS；`factor_rank` 的旧路径若有测试仍绿。

---

## Chunk 4: 树模型改排序（模型中心 + 回测）

### Task 4: 标签预处理 + LGBMRanker

**Files:**
- Modify: `quantlab/services/model_training.py` `attach_label`、`fit_lgb`、`MODEL_KINDS["lightgbm_tree"]`
- Modify: `quantlab/web/pages/models.html` 树模型 `form-note`
- Test: `tests/quantlab/test_model_training.py`

- [ ] **Step 1: 写失败测试**

```python
def test_attach_label_winsorizes_bins_and_nulls_one_word_next_open(tmp_path):
    # 构造多日多股 hfq_open/hfq_close/high/low
    # T+1 high==low 的行 _target 为空
    # 其余 _target 为 0–19 的整数档

def test_fit_lgb_uses_ranker_grouped_by_date():
    # monkeypatch 或检查返回模型类型名包含 Ranker
```

- [ ] **Step 2: 跑测试确认失败**

Expected: 当前 `_target` 是连续收益；模型是 `LGBMRegressor`。

- [ ] **Step 3: 实现**

`attach_label`：

1. 计算 `_raw = hfq_close.shift(-2) / hfq_open.shift(-1) - 1`
2. T+1 `high==low`（用 lead high/low）→ `_raw` 置空
3. 用非空样本的 1%/99% 分位 clip
4. `pd.qcut(..., 20, labels=False, duplicates="drop")` 得到 `_target` 整数

`fit_lgb`：

```python
model = lgb.LGBMRanker(
    n_estimators=int(params["number_of_trees"]),
    max_bin=int(params["max_bins"]),
    num_leaves=int(params["num_leaves"]),
    min_child_samples=int(params["min_child_samples"]),
    learning_rate=float(params["learning_rate"]),
    objective="lambdarank",
    verbosity=-1,
    random_state=7,
)
order = features.assign(_date=dates).sort_values("_date")
group = order.groupby("_date", sort=True).size().to_list()
model.fit(order_features, order_target, group=group)
```

`execute` 调用 `fit_estimator` 时必须把对齐后的 `date` 传进 `fit_lgb`（可让 `fit_estimator` 接收可选 `groups` Series）。`MODEL_KINDS["lightgbm_tree"]["method"] = "lightgbm_ranker"`；`summary` / 页面说明改为排序。

线性模型继续用 clip 前的连续 `_raw` 作回归标签（或共用 clip 后连续值、不用 20 档）。**推荐：** 只对 `lightgbm_tree` 用分箱整数；回归类用 clip 后的连续 `_raw`。`attach_label` 同时产出 `_raw` 与 `_target`，`fit_estimator` 按 kind 选列。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/quantlab/test_model_training.py tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: PASS。LightGBM 未安装的环境保持原来的 RuntimeError 中文，且回测 failed 而不是 fallback。

---

## Chunk 5: 目标权重账户

### Task 5: 抽出 `portfolio.py`，替换独立两日交易

**Files:**
- Create: `quantlab/services/portfolio.py`
- Modify: `quantlab/services/backtest_job.py`（positions/execution 步）
- Test: `tests/quantlab/test_backtest_workbench.py`（改旧成交断言 + 新账户测试）

- [ ] **Step 1: 写失败测试**

用至少 4 个测试交易日、两只股票、分数一高一低：

- 连续两期 Top1 都是 A：A 只在第一期买入，第二期**不**因「重新开仓」再卖再买（持仓数量可因权益变化微调）。
- 第二期 Top1 换成 B：A 在第一期信号日收盘卖出，B 在次日开盘买入。
- 成交金额按当时权益 / N，而不是全程 `initial_capital / N`。
- 输出 `equity_curve` 每个测试日一行，含 `equity`/`cash`/`invested`。

旧测试 `test_execute_runs_ordered_dag_and_keeps_artifacts` 的 `buy_date`/`sell_date` 按新语义改写，或改成断言账户快照。

- [ ] **Step 2: 跑测试确认失败**

Expected: 当前每期都按 `initial_capital/top_n` 独立开仓。

- [ ] **Step 3: 实现要点**

```python
# portfolio.py
def run_portfolio(frame, predictions, config) -> tuple[list[dict], list[dict]]:
    """返回 (trades, equity_curve)。"""
```

循环测试集交易日：

1. 收盘盯市：持仓 × 当日 `close`（未复权）。
2. 若当日是信号日 S：按过滤后预测取 Top N，得到 `target_weights`；对不在目标里的持仓，若 `skip_close_down_limit` 且收盘跌停则记 unfilled，否则按 `close` 卖出（费率+印花税）。
3. 若当日是某信号的下一交易日 E：用开盘价买入/补到目标权重；开盘涨跌停按现有 `skip_open_limit`。
4. 整手 100 股；现金不足则少买。

`weighting=="equal"`：权重 `1/N`。`weighting=="score"`：正分数归一，否则退回等权。

`backtest_job.execute` 的 positions 步调用 `run_portfolio`，trades 仍写成 `results/<run_id>/trades.json`，曲线写成 `equity_curve.json` 并登记 artifact。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: PASS。

---

## Chunk 6: 指标与运行记录图

### Task 6: 超额、换手、资金占用 + 曲线

**Files:**
- Modify: `quantlab/services/backtest_job.py` `_performance_metrics`（改为吃 `equity_curve`）
- Modify: `quantlab/services/result_archive.py` `_METRICS`
- Modify: `quantlab/web/pages/backtest_run_record.html`、`quantlab/web/assets/app.css`
- Test: `tests/quantlab/test_backtest_workbench.py`、`tests/quantlab/test_result_archive.py`

- [ ] **Step 1: 写失败测试**

```python
def test_metrics_include_excess_turnover_and_capital_usage(tmp_path):
    ...
    m = result["metrics"]
    assert "excess_return" in m
    assert "turnover" in m
    assert "capital_usage" in m
    assert result["metrics"]["equity_curve"][0]["equity"] > 0
```

页面测试（可选 Playwright）：运行记录有 `id="equity-chart"`。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

- `excess_return` = 策略累计收益 − 基准累计收益（基准仍用测试窗 `000300.SH` 的 `hfq_close`，曲线也按日对齐）。
- `turnover` = 各调仓日 `|成交额| / 当日权益` 的平均。
- `capital_usage` = 各日 `invested / equity` 的平均。
- 夏普/回撤改为**日度权益**而不是按卖出日拼 pnl。
- 归档 `_METRICS` 增加三项；`backtest_run_record.html` 的 `metricLabels` 同步。
- 引入 `lightweight-charts`：策略权益、基准复权到同一起点。无数据时显示「未生成曲线」。

- [ ] **Step 4: 跑测试 + 浏览器**

Run: `.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py tests/quantlab/test_result_archive.py -q --tb=short`

改 Python 后重启 `python -m quantlab.cli serve --host 127.0.0.1 --port 8765`。改静态资源 bump `?v=`。浏览器：回测中心过滤文案可见；开始回测失败时横幅为中文训练原因；成功后运行记录有曲线和新指标。

---

## 验收清单

1. 回测页填 `max_bins=511`、`最小样本=1000`，训练用的就是这两个数，不是 255/20。
2. 训/测样本不含 `close<=low`、不含收盘涨跌停；页面过滤行能读到这两句。
3. LightGBM 样本不足或报错 → 运行 `failed`，原因可读，没有因子排序假成交。
4. 树模型是 Ranker；模型中心说明是排序。
5. 连续两期同一只 TopN 不会无意义地卖再买；权益会复利。
6. 运行记录有超额、换手、资金占用和权益曲线。

## 建议实施顺序

Chunk 1 → 2 → 3 → 4 → 5 → 6。5 依赖 2 的过滤；6 依赖 5 的日度权益。不要先改 UI 图再改账户，否则曲线没有真实数据。
