# 规则策略仓库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 数据中心能下载沪深300/中证500月度成分；回测中心能跑 Wiki 多指标趋势跟踪（信号表 + 10% 目标仓位 + 止盈止损），结果进入现有运行记录。

**Architecture:** 新逻辑全部放进新模块。`portfolio.py` / `backtest_job.py` / `backtest_workbench.py` 目前是 `_recovered_pyc` 桩，只在 `exec` 之后加分发，避免先反编译整文件。规则策略 `kind=rule_signal` 走 `generate_signals` → `run_target_weight_portfolio`，不训练、不用槽位账户。

**Tech Stack:** FastAPI、pandas、PyArrow、Tushare `index_weight`、pytest、Playwright。

**规格:** `docs/specs/2026-09-06-rule-strategy-warehouse-design.md`

## Global Constraints

- 权威 Wiki：[C54hzJxRqd](https://bigquant.com/wiki/doc/C54hzJxRqd)；`up_pct_60` 阈值是 **0.35**，不是 Cowork 调试里的 0.45。
- 成分：Tushare `index_weight` 月度 as-of，禁止未来月份。
- 缺成分股 → 回测 `failed`，不准改成全 A。
- 指标用 `hfq_close`；成交额语义 5000 万元人民币。
- `kind=rule_signal` 不用沪深300 MA200 开仓开关。
- 因子排序 / 槽位账户测试必须保持绿。
- 不引入用户 `exec` 任意代码；第一期只有内置 `wiki_trend_follow`。
- 工作区禁止主动 `git commit`。

## 文件地图

| 文件 | 职责 |
|---|---|
| Create: `quantlab/services/index_membership.py` | 读月度权重、as-of 成分、板块/上市天数/成交额 |
| Create: `quantlab/services/rule_indicators.py` | EMA / MACD / RSI / 布林中轨 / 20 日动量 |
| Create: `quantlab/strategies/wiki_trend_follow.py` | Wiki 择时 + 四条件 + 打分 → 信号表 |
| Create: `quantlab/strategies/registry.py` | 策略 id → 模块、默认参数、代码哈希 |
| Create: `quantlab/services/rule_portfolio.py` | 目标仓位 10%、空仓清仓、止盈止损、最长 45 日 |
| Create: `quantlab/services/rule_backtest.py` | 规则策略执行：读数、信号、撮合、指标 |
| Modify: `quantlab/services/tushare_download.py` | `download_index_weight` |
| Modify: `quantlab/services/catalog.py` | 登记 `index_weight` 接口文档 96 |
| Modify: `quantlab/api/app.py` | 下载 API、规则策略目录 API |
| Modify: `quantlab/services/backtest_workbench.py` | `kind=rule_signal` 校验（桩文件尾部分发） |
| Modify: `quantlab/services/backtest_job.py` | 规则策略分发 |
| Modify: `quantlab/web/pages/data.html` + `assets/app.js` | 下载成分股 |
| Modify: `quantlab/web/pages/backtest_workbench_formal.html` | 规则策略表单 |
| Modify: `quantlab/web/pages/backtest_run_record.html` | 多头天数 / 空仓天数 |
| Test: `tests/quantlab/test_index_membership.py` | 新建 |
| Test: `tests/quantlab/test_wiki_trend_follow.py` | 新建 |
| Test: `tests/quantlab/test_rule_portfolio.py` | 新建 |
| Test: `tests/quantlab/test_rule_backtest.py` | 新建 |
| Test: `tests/quantlab/test_web_shell.py` | 页面文案 |
| Test: `tests/quantlab/browser/test_rule_strategy_browser.py` | 浏览器 |

---

### Task 1: 下载并展示 `index_weight`

**Files:**
- Modify: `quantlab/services/tushare_download.py`
- Modify: `quantlab/services/catalog.py`
- Modify: `quantlab/api/app.py`
- Modify: `quantlab/web/assets/app.js`
- Test: `tests/quantlab/test_tushare_download.py`（若无则新建）

**Interfaces:**
- Produces: `TushareDownloadService.download_index_weight(index_code: str, start_date: str, end_date: str) -> dict[str, Any]`
- 落盘：`raw/index_weight/index_weight_{000300_SH|000905_SH}.parquet`
- 字段：`index_code, con_code, trade_date, weight`

- [ ] **Step 1: 写失败测试**

```python
def test_catalog_lists_index_weight_doc_96():
    from quantlab.services.catalog import CatalogService
    assert CatalogService._RAW_INTERFACE_DOCS["index_weight"] == 96
    assert CatalogService._RAW_INTERFACE_NAMES["index_weight"] == "指数成分和权重"


def test_download_index_weight_writes_monthly_rows(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService
    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(svc, "_call", lambda api, params: [{
        "index_code": params["index_code"],
        "con_code": "000001.SZ",
        "trade_date": "20180903",
        "weight": 0.86,
    }])
    out = svc.download_index_weight("000300.SH", "20180901", "20180930")
    assert out["rows"] == 1
    assert "index_weight_000300_SH.parquet" in out["target"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_tushare_download.py -q --tb=short`

Expected: `index_weight` KeyError 或属性不存在。

- [ ] **Step 3: 实现**

按月循环调用 Tushare（每月 1 日–月末）。指数代码默认 `000300.SH`、`000905.SH`；若接口要 `399300.SZ`，在服务里映射并在日志写清。合并去重键：`index_code, con_code, trade_date`。

API：`POST /api/raw/download/index_weight`，body `{index_code, start_date, end_date}`。数据中心 raw 文件弹窗加「下载成分权重」按钮（需已配置 Tushare token）。

- [ ] **Step 4: 跑测试至通过**

Run: `.venv/bin/python -m pytest tests/quantlab/test_tushare_download.py tests/quantlab/test_web_shell.py -q --tb=short`

Expected: PASS。不在这一任务打真实 Tushare（单测 mock `_call`）。

---

### Task 2: 月度成分 as-of 与股票过滤

**Files:**
- Create: `quantlab/services/index_membership.py`
- Test: `tests/quantlab/test_index_membership.py`

**Interfaces:**
- Produces:
  - `load_index_weight(raw_root: Path) -> pd.DataFrame`
  - `members_on(weights: pd.DataFrame, index_code: str, trade_date: str) -> set[str]`
  - `amount_yuan(series: pd.Series) -> pd.Series`（千元则 ×1000）
  - `is_main_sme_chinext(ts_code: str) -> bool`

- [ ] **Step 1: 写失败测试**

```python
def test_members_on_uses_last_month_not_future():
    weights = pd.DataFrame({
        "index_code": ["000300.SH", "000300.SH"],
        "con_code": ["AAA.SZ", "BBB.SZ"],
        "trade_date": ["20180903", "20181008"],
        "weight": [1.0, 1.0],
    })
    assert members_on(weights, "000300.SH", "20180920") == {"AAA.SZ"}
    assert members_on(weights, "000300.SH", "20181008") == {"BBB.SZ"}


def test_members_on_missing_index_is_empty_not_all_a():
    assert members_on(pd.DataFrame(columns=["index_code","con_code","trade_date","weight"]), "000300.SH", "20180903") == set()


def test_board_excludes_star_and_bj():
    assert is_main_sme_chinext("600000.SH")
    assert is_main_sme_chinext("300001.SZ")
    assert not is_main_sme_chinext("688001.SH")
    assert not is_main_sme_chinext("830001.BJ")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_index_membership.py -q --tb=short`

Expected: import 失败。

- [ ] **Step 3: 实现 as-of**

`trade_date` 归一成 `YYYYMMDD`。对指定指数取 `weight_date <= trade_date` 的最大 `weight_date`，返回该日全部 `con_code`。没有更早快照则空集。

成交额：若中位数 < 1e7 且文档口径是千元，视为千元 ×1000；把判定结果放进返回的 `unit` 字段，调用方写日志。

- [ ] **Step 4: 跑测试至通过**

Expected: PASS。

---

### Task 3: Wiki 信号表

**Files:**
- Create: `quantlab/services/rule_indicators.py`
- Create: `quantlab/strategies/wiki_trend_follow.py`
- Create: `quantlab/strategies/registry.py`
- Test: `tests/quantlab/test_wiki_trend_follow.py`

**Interfaces:**
- Produces: `generate_signals(frame, membership, params) -> pd.DataFrame` 列：`date, instrument, weight, rank_score`
- `weight` 固定 `params["target_weight"]`（默认 0.10），每日最多 `top_n` 行
- 非多头日：该日 **零行**

- [ ] **Step 1: 写失败测试**

用 3 只股票、已知收盘价手算 EMA/RSI 不必全表；至少：

```python
def test_bearish_day_emits_no_rows():
    # HS300 20 日上涨占比 < 0.50 → 即使个股四条件满足也无信号
    ...


def test_rank_picks_top_n_equal_ten_percent():
    # 过过滤的股票按 40/35/25 打分，只留 2 只，weight 均为 0.10
    ...


def test_requires_all_four_conditions():
    # 缺 close>boll_mid 的股票不得入选
    ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_wiki_trend_follow.py -q --tb=short`

- [ ] **Step 3: 实现指标与策略**

- EMA：`ewm(span=n, adjust=False)`，按 `instrument` groupby。
- MACD 柱：`2 * (dif - dea)`。
- RSI(14)：Wilder（`ewm(alpha=1/14, adjust=False)` 于 gain/loss）。
- 布林中轨：`rolling(20).mean()`。
- 择时只在 **沪深300** `members_on` 集合上算 `up_pct_20` / `up_pct_60`。
- 选股池 = 300 ∪ 500。
- `registry.py`：`{"wiki_trend_follow": {module, title, defaults, source_hash}}`。

默认参数：

```python
DEFAULTS = {
    "up_pct_20": 0.50,
    "up_pct_60": 0.35,
    "rsi_low": 40.0,
    "rsi_high": 72.0,
    "top_n": 10,
    "target_weight": 0.10,
    "min_list_days": 365,
    "min_amount_yuan": 50_000_000,
}
```

- [ ] **Step 4: 跑测试至通过**

Expected: PASS。

---

### Task 4: 目标仓位账户 + 止盈止损

**Files:**
- Create: `quantlab/services/rule_portfolio.py`
- Test: `tests/quantlab/test_rule_portfolio.py`

**Interfaces:**
- Produces: `run_target_weight_portfolio(frame, signals, config) -> tuple[list[dict], list[dict]]`（trades, equity）
- 不修改现有 `run_portfolio` 槽位语义。

- [ ] **Step 1: 写失败测试**

```python
def test_each_name_is_ten_percent_leftover_is_cash():
    # 2 只信号 → 各 10% 权益，80% 现金


def test_non_bullish_rebalance_sells_at_close():
    # 调仓日 signals 为空 → 当日收盘全卖


def test_stop_loss_beats_take_profit_same_bar():
    # 同一日 high 到 +25%、low 到 -10% → 按止损成交


def test_max_hold_45_sells_at_close_no_refill_until_rebalance():
    ...


def test_name_staying_in_top_n_is_not_churned():
    # 下期仍在名单 → 无卖出、无新买入，持有天数累加
```

买入价默认下一交易日 `open`（config `buy_price`）；卖出价调仓/到期用 `close`，止盈止损用触发当日的 high/low 对应价格（止损用当时的 `hfq_low` 触及价近似为入场价×0.90，止盈为入场价×1.25）。手续费/涨跌停沿用现有 `eval_open_filters` 能复用的部分；复制必要的成交量手数逻辑，不要调用槽位 `run_portfolio`。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_rule_portfolio.py -q --tb=short`

- [ ] **Step 3: 实现**

调仓日历：测试区间交易日按 `rebalance_every`（默认 5）取点，与 `TradingDaysRebalance` 一样从区间首个交易日起数。

- [ ] **Step 4: 跑测试至通过**

Expected: PASS。接着跑：

`.venv/bin/python -m pytest tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: 槽位测试仍 PASS。

---

### Task 5: 回测分发与缺数据失败

**Files:**
- Create: `quantlab/services/rule_backtest.py`
- Modify: `quantlab/services/backtest_workbench.py`（桩尾部分发）
- Modify: `quantlab/services/backtest_job.py`（桩尾部分发）
- Modify: `quantlab/api/app.py`
- Test: `tests/quantlab/test_rule_backtest.py`

**Interfaces:**
- `validate_rule_config(config) -> dict`
- `execute_rule_signal(job_service, run_id) -> dict`
- `kind=rule_signal` 不要求 model / strategy / factor_versions；要求 `dataset_id` + `rule_strategy_id`

- [ ] **Step 1: 写失败测试**

```python
def test_validate_rule_signal_does_not_need_model():
    out = svc.validate(rule_config())
    assert out["kind"] == "rule_signal"
    assert out["rule_strategy_id"] == "wiki_trend_follow"
    assert out["code_hash"].startswith("sha256:")


def test_execute_fails_when_index_weight_missing(tmp_path):
    # 不创建 raw/index_weight
    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.json()["status"] == "failed"
    assert "成分" in executed.json()["error"]


def test_execute_completes_with_zero_trades_when_never_bullish(tmp_path):
    ...
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 桩文件分发（示例）**

`quantlab/services/backtest_job.py` 在现有 `exec(_code)` 之后：

```python
_original_execute = BacktestJobService.execute

def execute(self, run_id: str):
    row = self.get(run_id)
    config = (row or {}).get("config") or {}
    if config.get("kind") == "rule_signal":
        from quantlab.services.rule_backtest import execute_rule_signal
        return execute_rule_signal(self, run_id)
    return _original_execute(self, run_id)

BacktestJobService.execute = execute
```

`validate` 同样：`kind==rule_signal` 走 `validate_rule_config`，否则原逻辑。

`execute_rule_signal`：读 canonical + index_weight → 信号 → `run_target_weight_portfolio` → 写 trades/equity/`metrics.json`（含 `bullish_days`、`signal_rows`、`flat_days`、`membership_asof: monthly`）。失败写中文 `error`。

- [ ] **Step 4: 跑测试至通过**

Run: `.venv/bin/python -m pytest tests/quantlab/test_rule_backtest.py tests/quantlab/test_backtest_workbench.py -q --tb=short`

Expected: PASS。

---

### Task 6: 回测页、运行记录、浏览器

**Files:**
- Modify: `quantlab/web/pages/backtest_workbench_formal.html`
- Modify: `quantlab/web/pages/backtest_workbench.html`（若仍被重定向使用，保持文案一致）
- Modify: `quantlab/web/pages/backtest_run_record.html`
- Modify: `quantlab/web/assets/app.js`
- Modify: `tests/quantlab/test_web_shell.py`
- Create: `tests/quantlab/browser/test_rule_strategy_browser.py`

- [ ] **Step 1: 写失败测试**

```python
def test_backtest_new_exposes_rule_strategy_controls():
    html = _page("backtest_workbench_formal.html")
    assert "规则策略" in html
    assert "wiki_trend_follow" in html or "多指标趋势跟踪" in html
    assert "up_pct_60" in html
```

浏览器：打开 `/backtests/new`，切到规则策略，训练超参隐藏，趋势参数可见；提交校验返回 200 或明确缺成分错误，不 500。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 页面**

- 信号来源增加选项「规则策略」。
- 选中后：策略下拉（仅 Wiki 一篇）、参数（up_pct_20/60、RSI 上下限、TopN、调仓、止损、止盈、最长持有）。
- `configValue()` 写 `kind: "rule_signal"`、`rule_strategy_id`、`account_mode: "target_weight_exits"`，**不要**写 `open_when_benchmark_gt_ma200: true`。
- 手续费默认卖 0.0013（仅该 kind 的出厂值；用户改了以用户为准）。
- 运行记录增加：市场多头天数、信号条数、完全空仓天数、成分 as-of=月度。

- [ ] **Step 4: 跑测试至通过**

```
.venv/bin/python -m pytest tests/quantlab/test_web_shell.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_backtest_workbench.py -q --tb=short
.venv/bin/python -m pytest tests/quantlab/browser/test_rule_strategy_browser.py -q --tb=short
```

Expected: PASS。

---

## Spec coverage

| 规格项 | 任务 |
|---|---|
| 下载 index_weight | 1 |
| 月度 as-of、禁止未来函数 | 2 |
| 成交额 5000 万、板块过滤 | 2 |
| Wiki 择时 50%/35% 与四条件 | 3 |
| 10% 仓位、空仓清仓、SL/TP/45 日 | 4 |
| 缺成分失败、零信号完成 | 5 |
| 回测页 + 运行记录 | 6 |
| 不改槽位账户 | 4 回归 `test_backtest_workbench.py` |
| 无策略 IDE | 全计划无 `exec` 用户代码 |

## 实现时注意

- `portfolio.py` 等桩文件不要整文件重写，除非测试证明分发不够。
- 真实全市场回测不是本计划门禁；门禁用合成表。用户要跑 2018 至今时，先在数据中心下载成分，再在回测页提交。
