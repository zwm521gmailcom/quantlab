# 规则回测独立页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit policy:** 用户禁止主动 commit。各任务不要执行 `git commit`。

**Goal:** 侧栏在回测中心下增加「规则回测」页 `/backtests/rules`；原 `/backtests/new` 去掉规则策略下拉，只做模型/因子。

**Architecture:** 新页面独立表单，只提交 `kind=rule_signal`。复用 `validate_rule_config` / `execute_rule_signal`。复制规则运行时 `redirect_url` 指向 `/backtests/rules`。

**Tech Stack:** FastAPI FileResponse、现有 workbench CSS、pytest、Playwright。

## Global Constraints

- 不做策略 IDE / 不贴任意 Python。
- 第一期只有 `wiki_trend_follow`。
- 一个回测区间；无训练区间、无因子组合、无树超参、无「信号来源」。
- 费率出厂买 0.0003、卖 0.0013、印花税 0、最低 5。
- `open_when_benchmark_gt_ma200: false`。
- 工作区禁止主动 `git commit`。

## 文件地图

| 文件 | 职责 |
|---|---|
| Create: `quantlab/web/pages/backtest_rules.html` | 规则回测表单 |
| Modify: `quantlab/api/app.py` | `GET /backtests/rules` |
| Modify: `quantlab/web/assets/nav.js` | 回测中心子项「规则回测」 |
| Modify: `quantlab/web/pages/backtest_workbench_formal.html` | 去掉规则策略入口 |
| Modify: `quantlab/services/result_archive.py` | 规则复制跳 `/backtests/rules` |
| Modify: tests + browser tests | 锁新路由、旧页不再出现规则下拉 |

---

## Chunk 1: 入口与表单

### Task 1: 路由、导航、规则页

**Files:**
- Create: `quantlab/web/pages/backtest_rules.html`
- Modify: `quantlab/api/app.py`
- Modify: `quantlab/web/assets/nav.js`
- Modify: `tests/quantlab/test_web_shell.py`
- Modify: `tests/quantlab/test_backtest_workbench.py`（若有 `/backtests/new` 200 测试，加 `/backtests/rules`）
- Modify: `tests/quantlab/browser/test_rule_strategy_browser.py`
- Modify: `quantlab/web/pages/backtest_workbench_formal.html`
- Modify: `quantlab/services/result_archive.py`
- Modify: `tests/quantlab/test_result_archive.py`

**Interfaces:**
- Produces: `GET /backtests/rules` → `backtest_rules.html`
- Produces: `copy_config` 当 `kind=="rule_signal"` 或存在 `rule_strategy_id` 时 `redirect_url` 以 `/backtests/rules?draft_id=` 开头
- `configValue()` 始终 `kind: "rule_signal"`，`test` 来自单一回测区间

- [x] **Step 1: 写失败测试**

`test_web_shell.py`：导航含 `/backtests/rules` 与「规则回测」；`backtest_rules.html` 含 Wiki 模板字段、**不含** `id="signal-source"`；`backtest_workbench_formal.html` **不含** `value="rule_signal"`。

浏览器：打开 `/backtests/rules`，可见 `up_pct_60` 与「Wiki 多指标趋势跟踪」；`configValue().kind === "rule_signal"`。不再在 `/backtests/new` 上选规则策略。

`copy_config`：规则配置复制后 redirect 到 `/backtests/rules`。

- [x] **Step 2: 跑测试确认失败**

```
.venv/bin/python -m pytest tests/quantlab/test_web_shell.py::test_nav_exposes_rule_backtest_page tests/quantlab/test_web_shell.py::test_backtest_new_does_not_embed_rule_strategy -q --tb=short
```

Expected: FAIL（新断言尚未满足）。

- [x] **Step 3: 实现**

1. `nav.js`：回测中心增加 child `{ href: "/backtests/rules", label: "规则回测" }`；父级 `active` 含 `/backtests/new` 与 `/backtests/rules`。
2. `app.py`：`GET /backtests/rules` → `backtest_rules.html`。
3. 新页面：名称、数据版本默认 `ds_canonical_market`/`current`、回测区间 from/to、策略下拉、参数、Top N、调仓 5、费率、保存/开始回测。提交走现有 `/api/backtests` + `/execute`。
4. 从 formal 页删除信号来源与 `data-kinds="rule_signal"` 面板及相关 JS 分支。
5. `copy_config` 按 kind 分流 redirect。

- [x] **Step 4: 跑测试至通过**

```
.venv/bin/python -m pytest tests/quantlab/test_web_shell.py tests/quantlab/test_rule_backtest.py tests/quantlab/test_result_archive.py tests/quantlab/test_backtest_workbench.py -q --tb=short
.venv/bin/python -m pytest tests/quantlab/browser/test_rule_strategy_browser.py -q --tb=short
```

Expected: PASS。

- [x] **Step 5: 不要 commit**

## Spec coverage

| 规格项 | 任务 |
|---|---|
| `/backtests/rules` 独立页 | 1 |
| `/backtests/new` 去掉规则 | 1 |
| 单一回测区间、无因子/训练 | 1 |
| 复制跳规则页 | 1 |
| 复用 rule_signal 后端 | 已有，本计划不改引擎 |
