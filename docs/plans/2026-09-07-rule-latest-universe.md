# 最新成分股票池 + 开盘调仓 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (tasks are tightly coupled). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wiki 规则策略用最新一期沪深300∪中证500冻结股票池、去掉市场择时，调仓日改为当日开盘先卖后买。

**Architecture:** `latest_members` 从 `index_weight` 取每个指数最大 `trade_date`；`generate_signals` 全程用该并集、不再看 up_pct；`run_target_weight_portfolio` 调仓日开盘换仓后再做止盈止损；页面去掉 up_pct，运行记录 `membership_asof=latest`。

**Tech Stack:** pandas、pytest、FastAPI TestClient、Playwright。

## Global Constraints

- 规格：`docs/specs/2026-09-07-rule-latest-universe-design.md`
- 禁止主动 `git commit`
- 不还原历史成分、不把 Cowork 收益率当门槛、不改槽位账户
- TDD：先失败测试再改生产代码
- 旧草稿 `up_pct_*` 后端忽略

---

### Task 1: latest_members

**Files:**
- Modify: `quantlab/services/index_membership.py`
- Modify: `quantlab/services/rule_backtest.py` (`_membership_error`)
- Test: `tests/quantlab/test_index_membership.py`
- Test: `tests/quantlab/test_rule_backtest.py`（空最新一期失败，可放 Task 4）

**Interfaces:**
- Produces: `latest_members(weights: pd.DataFrame, index_code: str) -> set[str]`
- 每个 `index_code` 取规范化后最大 `trade_date` 的 `con_code`；空表或无该指数返回 `set()`

- [ ] **Step 1: 失败测试**

```python
def test_latest_members_ignores_older_months():
    weights = pd.DataFrame({
        "index_code": ["000300.SH", "000300.SH", "000905.SH"],
        "con_code": ["OLD.SZ", "NEW.SZ", "FIVE.SZ"],
        "trade_date": ["20180903", "20240131", "20231231"],
        "weight": [1.0, 1.0, 1.0],
    })
    assert latest_members(weights, "000300.SH") == {"NEW.SZ"}
    assert latest_members(weights, "000905.SH") == {"FIVE.SZ"}
    assert members_on(weights, "000300.SH", "20180920") == {"OLD.SZ"}
```

- [ ] **Step 2:** `pytest tests/quantlab/test_index_membership.py::test_latest_members_ignores_older_months -v` 期望 FAIL（未定义）
- [ ] **Step 3:** 实现 `latest_members`（日期规范化与 `members_on` 相同）
- [ ] **Step 4:** 测试 PASS。不 commit。

---

### Task 2: 信号去掉择时、冻结股票池

**Files:**
- Modify: `quantlab/strategies/wiki_trend_follow.py`
- Modify: `tests/quantlab/test_wiki_trend_follow.py`

**Interfaces:**
- Consumes: `latest_members`
- `DEFAULTS` 不再含 `up_pct_20` / `up_pct_60`
- `generate_signals`：HS300/CSI500 各取 latest 一次，并集用于每一天；旧 params 里的 up_pct 忽略
- 缺 HS300 或 CSI500 最新成分：零行（失败由 execute 层负责）

- [ ] **Step 1:** 把 `test_bearish_day_emits_no_rows` 改成「多数下跌仍出信号」；新增冻结池测试：旧月份成分不进池。

```python
def test_bearish_breadth_still_emits_when_name_passes():
    # 同一套四条件合格票 + 三只下跌 HS300；signal_day 必须仍有 qualified

def test_frozen_universe_uses_latest_snapshot_not_asof():
    membership = concat(
        _membership(("000300.SH", {"600099.SH"}), trade_date="20200101"),
        _membership(("000300.SH", {"600000.SH"}), trade_date="20240131"),
        _membership(("000905.SH", {"000001.SZ"}), trade_date="20240131"),
    )
    # 600099 有合格价格也不该入选；600000 应入选
```

- [ ] **Step 2:** 先跑这两测，确认旧实现让 bearish 测仍按「无行」失败（改测后旧代码会让新断言 FAIL 或旧测名字要对上）
- [ ] **Step 3:** `generate_signals` 用 `latest_members`，删除 up_pct 过滤与 DEFAULTS 两项
- [ ] **Step 4:** `pytest tests/quantlab/test_wiki_trend_follow.py -v` PASS。不 commit。

---

### Task 3: 调仓日开盘买卖

**Files:**
- Modify: `quantlab/services/rule_portfolio.py`
- Modify: `tests/quantlab/test_rule_portfolio.py`

**Interfaces:**
- 调仓日：先按开盘卖出不在目标的持仓，再按开盘买入新目标；留存不换手
- 然后对仍持有仓位做止盈止损（high/low）；最长持有仍收盘卖
- 去掉「次日开盘买」的 pending 队列

现有测试必须改断言：

- `test_each_name_is_ten_percent_leftover_is_cash`：买在 `dates[0]`
- `test_non_bullish_rebalance_sells_at_close` → 改名为 `test_empty_rebalance_sells_at_open`：卖出价为该日 **open**
- `test_stop_loss_beats_take_profit_same_bar`：买入日变为 `dates[0]`；若与调仓同日，先买再止损。把买入日 open 设为 10、当日 high/low 打到止损，或把止损放到次日且次日不是调仓
- `test_max_hold_45_sells_at_close_no_refill_until_rebalance`：买入日 `dates[0]`，满 45 日为 `dates[44]`（hold_days = day_i - buy_i + 1）
- `test_name_staying_in_top_n_is_not_churned`：AAA 买在 `dates[0]`；BBB 在 `dates[5]` 按 **open** 卖

- [ ] **Step 1:** 改/写失败测试（空目标开盘清仓、调入当日开盘买）
- [ ] **Step 2:** 跑 `pytest tests/quantlab/test_rule_portfolio.py -v` 期望 FAIL
- [ ] **Step 3:** 重写日循环：无 pending；调仓日开盘换仓 → 止盈止损 → 到期收盘
- [ ] **Step 4:** 测试 PASS。不 commit。

---

### Task 4: 回测指标、页面、浏览器

**Files:**
- Modify: `quantlab/services/rule_backtest.py`（`membership_asof="latest"`；删除 `_bullish_days` 写入；`_membership_error` 检查 latest 非空）
- Modify: `quantlab/web/pages/backtest_rules.html`
- Modify: `quantlab/web/pages/backtest_run_record.html`
- Modify: `tests/quantlab/test_rule_backtest.py`
- Modify: `tests/quantlab/test_web_shell.py`
- Modify: `tests/quantlab/browser/test_rule_strategy_browser.py`

- [ ] **Step 1:** 失败测试
  - `test_execute_completes_with_zero_trades_when_never_bullish` → 零信号完成：`membership_asof == "latest"`，**没有** `bullish_days`
  - `test_execute_fails_when_latest_snapshot_empty`：文件在但最新一期无成分
  - web_shell：rules 页无 `up-pct`，说明含「最新」；run record 文案「最新一期（冻结）」；旧 `bullish_days` 仍可显示
  - browser：`#up-pct-60` count 0；`configValue().params` 无 up_pct

- [ ] **Step 2:** 跑对应测试 FAIL
- [ ] **Step 3:** 实现上述文件。`configValue` 的 `buy_price` 仍 `"open"`；调仓卖开盘由账户实现，不必把全局 `sell_price` 改成 open（到期仍收盘）。`applyConfig` 不再写 up_pct。
- [ ] **Step 4:**
```
pytest tests/quantlab/test_index_membership.py tests/quantlab/test_wiki_trend_follow.py tests/quantlab/test_rule_portfolio.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_web_shell.py tests/quantlab/test_result_archive.py -q
PLAYWRIGHT_BROWSERS_PATH=$HOME/Library/Caches/ms-playwright pytest tests/quantlab/browser/test_rule_strategy_browser.py -q
```
期望 PASS。不 commit。

## Spec coverage

| 规格 | 任务 |
|---|---|
| 最新一期冻结并集 | 1–2 |
| 缺成分失败 | 4 |
| 无择时 / 忽略 up_pct | 2、4 |
| 四条件 TopN | 2（现有测保留） |
| 开盘调仓、空仓开盘清、止损优先、45 日收盘 | 3 |
| 页面去 up_pct、membership latest | 4 |
| 零信号 completed | 4 |
