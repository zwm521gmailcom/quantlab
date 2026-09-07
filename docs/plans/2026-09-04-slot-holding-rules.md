# 槽位账户与持仓规则 Implementation Plan

> **For agentic workers:** 本工作区禁止主动 commit。用 TDD 实现。不要回退未复权撮合、涨跌停和训练失败即停。

**Goal:** 回测改成槽位账户：每天（或按调仓间隔）用信号补空位；持仓天数是硬性平仓；训练标签与持仓天数对齐。

**Architecture:** `rebalance_every` 只控制哪天看信号补仓。`holding_days` 从买入日再数 N 个交易日收盘卖。满 `top_n` 不买，不满按分数排序补仓并跳过已持有。`attach_label` 用 `hfq_close.shift(-(1+holding_days)) / hfq_open.shift(-1) - 1`。回测页拆成仓位 / 持仓规则 / 交易规则。

**Tech Stack:** 现有 `portfolio.py`、`model_training.py`、`backtest_workbench.py`、正式回测页、pytest、Playwright。

**默认值：** 调仓间隔 `1`（每天出信号），持仓天数 `2`（T 收盘信号 → T+1 开盘买 → T+2 只持仓 → T+3 收盘卖）。

**本计划不做：** 因子库 IC 标签仍是 `t+1 open -> t+2 close`；止盈止损；成交量限制。

---

## Task 1: 槽位 + 持仓天数账户

**Files:**
- Modify: `quantlab/services/portfolio.py`
- Modify: `tests/quantlab/test_backtest_workbench.py`

- [x] 写失败测试：持仓 2 天到期卖、满槽不买、掉出 Top N 不提前卖、到期后按新信号补仓
- [x] 改 `run_portfolio`：不再按目标权重整组换仓
- [x] 跑测试至通过

## Task 2: 训练标签跟随持仓天数

**Files:**
- Modify: `quantlab/services/model_training.py`
- Modify: `quantlab/services/backtest_job.py`
- Modify: `tests/quantlab/test_model_training.py`

- [x] 写失败测试：`holding_days=2` 时标签为 T+1 开盘 / T+3 收盘
- [x] `attach_label(frame, holding_days=2)`；回测训练传入 config
- [x] 跑测试至通过

## Task 3: 配置校验与页面分组

**Files:**
- Modify: `quantlab/services/backtest_workbench.py`
- Modify: `quantlab/web/pages/backtest_workbench_formal.html`
- Modify: `quantlab/web/pages/backtest_workbench.html`
- Modify: `quantlab/web/pages/backtest_run_record.html`
- Modify: `tests/quantlab/test_web_shell.py`
- Modify: `tests/quantlab/browser/test_backtest_filters_browser.py`

- [x] `holding_days` 缺省 2，必须 ≥ 1
- [x] 页面拆仓位 / 持仓规则 / 交易规则；默认间隔 1、持仓 2
- [x] 运行记录快照增加持仓天数
