# 最简离线 RL（按日选专家）Implementation Plan

> **For agentic workers:** REQUIRED: Use `subagent-driven-development`（有子代理时）或 `executing-plans` 按任务 TDD 实施。步骤用 `- [ ]`。  
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。  
> **回测：** 写完配置不自动开跑；实验只在用户点「运行实验」后执行。

**Goal:** 在回测中心下新增「离线策略学习」页，用已有自然年回测轨迹做离散 Fitted Q（每天选一个专家或持币），样本外与「上年冠军重放」比夏普/超额。

**Architecture:** 纯离线。从 `results/<run_id>/` 的 `run.json` / `equity_curve.json` / `trades.json` 建专家网格与 `(s,a,r,s')`；用小线性/浅层模型拟合 Q；走步评估年只读更早年数据选动作表。不改回测中心训练，不用 LightGBM 排序，不学个股权重。

**Tech Stack:** FastAPI、pandas/numpy、scikit-learn（`Ridge`/`SGDRegressor` 拟合 Q）、现有结果档案与页面壳、pytest。

**Origin:** `docs/specs/2026-09-14-offline-rl-from-backtests-design.md`

## Global Constraints

- 与回测中心「未来收益 → 排序树」无关；本页主模型不得用 LightGBM Ranker。
- 长窗回测（测试区间跨自然年）不得进入专家池或训练轨迹。
- 交易年 Y 的动作表与训练数据只用测试年 &lt; Y 的自然年回测。
- 奖励 = 当日组合收益 − 沪深300 当日收益；评估主判定 = 各评分年简单平均夏普更高且平均超额不更差。
- 写完参数不自动开始任何回测/实验。
- 产物 `config.kind = "offline_rl"`（或等价字段），避免再被选进专家池。

---

## 文件地图

| 文件 | 职责 |
|---|---|
| Create: `quantlab/services/offline_rl/__init__.py` | 包导出 |
| Create: `quantlab/services/offline_rl/fingerprint.py` | 配置指纹 |
| Create: `quantlab/services/offline_rl/experts.py` | 自然年过滤、网格、选 K |
| Create: `quantlab/services/offline_rl/holdings.py` | 从 trades 还原每日持仓集合 |
| Create: `quantlab/services/offline_rl/trajectories.py` | 日收益、超额奖励、四元组 |
| Create: `quantlab/services/offline_rl/state_features.py` | 状态向量 |
| Create: `quantlab/services/offline_rl/fitted_q.py` | Fitted Q-iteration |
| Create: `quantlab/services/offline_rl/evaluate.py` | 走步、重放、判定 |
| Create: `quantlab/services/offline_rl/experiment.py` | 编排、写结果目录、进度 |
| Create: `quantlab/api/routes/offline_rl.py` | API |
| Modify: `quantlab/api/routes/__init__.py` | 挂路由 |
| Modify: `quantlab/api/routes/pages.py` | `/backtests/offline-rl` |
| Create: `quantlab/web/pages/offline_rl.html` | 页面 |
| Create: `quantlab/web/assets/backtest/offline-rl.js` | 前端 |
| Modify: `quantlab/web/assets/nav.js` | 侧栏入口 |
| Modify: `tests/quantlab/test_web_shell.py` | 导航与页面合同 |
| Create: `tests/quantlab/test_offline_rl_fingerprint.py` | |
| Create: `tests/quantlab/test_offline_rl_experts.py` | |
| Create: `tests/quantlab/test_offline_rl_holdings.py` | |
| Create: `tests/quantlab/test_offline_rl_trajectories.py` | |
| Create: `tests/quantlab/test_offline_rl_fitted_q.py` | |
| Create: `tests/quantlab/test_offline_rl_evaluate.py` | |
| Create: `tests/quantlab/test_offline_rl_api.py` | |

---

### Task 1: 配置指纹

**Files:**
- Create: `quantlab/services/offline_rl/fingerprint.py`
- Test: `tests/quantlab/test_offline_rl_fingerprint.py`

**Produces:**
- `config_fingerprint(config: dict) -> str` — 稳定哈希；同内容同指纹
- `is_offline_rl_result(config: dict) -> bool` — `kind == "offline_rl"` 为真

- [ ] **Step 1: Write the failing test**

```python
from quantlab.services.offline_rl.fingerprint import config_fingerprint, is_offline_rl_result

def test_fingerprint_ignores_name_and_dates_but_keeps_rules():
    a = {
        "name": "2019 · 袖套A",
        "factor_versions": [{"field": "sleeve_p60_c075", "factor_id": "f1", "version_id": "v1"}],
        "top_n": 4,
        "weighting": "equal",
        "holding_days": 2,
        "rebalance_every": 1,
        "open_when_benchmark_gt_ma200": True,
        "test": {"filter": {"expressions": ["st_status == 0"]}, "date_from": "2019-01-02", "date_to": "2019-12-31"},
        "train": {"filter": {"expressions": ["st_status == 0"]}},
        "model": {"kind": "factor_rank"},
    }
    b = dict(a)
    b["name"] = "2020 · 袖套A"
    b["test"] = dict(a["test"], date_from="2020-01-02", date_to="2020-12-31")
    assert config_fingerprint(a) == config_fingerprint(b)
    c = dict(a)
    c["top_n"] = 5
    assert config_fingerprint(a) != config_fingerprint(c)
    assert is_offline_rl_result({"kind": "offline_rl"}) is True
    assert is_offline_rl_result({"kind": "factor_rank"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_fingerprint.py -q`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement fingerprint**

指纹字段（排序后 JSON + sha256 前 16 hex）：`factor fields`（只取 `field` 或 `factor_id` 排序列表）、`top_n`、`weighting`、`holding_days`、`rebalance_every`、`open_when_benchmark_gt_ma200`、`test.filter` / `train.filter`（规范化表达式列表）、`model.kind`、`buy_price`、`sell_price`、盘前/开仓相关表达式若存在。  
**排除：** `name`、`machine_id`、`content_hash`、`test.date_*`、`train.date_*`、`hyperparameters`（单因子排名无关）、`kind==offline_rl` 的结果本身。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_fingerprint.py -q`  
Expected: PASS

---

### Task 2: 自然年专家网格

**Files:**
- Create: `quantlab/services/offline_rl/experts.py`
- Test: `tests/quantlab/test_offline_rl_experts.py`

**Consumes:** `config_fingerprint`, `is_offline_rl_result`  
**Produces:**
- `natural_year_window(date_from, date_to) -> int | None` — 同一自然年内返回年份，否则 `None`
- `build_expert_grid(runs: list[dict]) -> dict` — 见下
- `select_experts_before_year(grid, year: int, *, k: int = 8, metric: str = "sharpe") -> list[str]` — 返回 fingerprint 列表

每个 `runs` 元素至少含：`run_id`, `config`, `metrics`（含 `sharpe` / `excess_return`）, 可选跳过 `status != completed`。

- [ ] **Step 1: Write the failing test**

```python
from quantlab.services.offline_rl.experts import (
    natural_year_window,
    build_expert_grid,
    select_experts_before_year,
)

def test_natural_year_and_long_window_excluded():
    assert natural_year_window("2019-01-02", "2019-12-31") == 2019
    assert natural_year_window("2020-01-02", "2026-08-31") is None

def test_grid_and_k_selection_uses_only_prior_years():
    runs = [
        {
            "run_id": "a2019",
            "config": {
                "name": "A",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_x"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2019-01-02", "date_to": "2019-12-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 1.0, "excess_return": 0.1},
        },
        {
            "run_id": "a2020",
            "config": {
                "name": "A",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_x"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2020-01-02", "date_to": "2020-12-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 2.0, "excess_return": 0.2},
        },
        {
            "run_id": "long",
            "config": {
                "name": "long",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_y"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2020-01-02", "date_to": "2026-08-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 9.0, "excess_return": 9.0},
        },
        {
            "run_id": "rl",
            "config": {"kind": "offline_rl", "test": {"date_from": "2019-01-02", "date_to": "2019-12-31"}},
            "metrics": {"sharpe": 8.0},
        },
    ]
    grid = build_expert_grid(runs)
    assert "long" not in {cell["run_id"] for cells in grid["by_year"].values() for cell in cells}
    fps = select_experts_before_year(grid, 2021, k=1)
    # 2020 夏普更高的同指纹应排前；选 K 用 year<2021 的年均或末年夏普
    assert len(fps) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_experts.py -q`  
Expected: FAIL

- [ ] **Step 3: Implement experts**

`build_expert_grid` 返回大致结构：

```python
{
  "excluded_long_windows": [{"run_id": "...", "date_from": "...", "date_to": "..."}],
  "by_year": {
    2019: [{"fingerprint": "...", "run_id": "...", "sharpe": 1.0, "excess_return": 0.1, "name": "..."}],
    ...
  },
  "fingerprints": {"<fp>": {"label": "...", "sample_config": {...}}},
}
```

`select_experts_before_year`：对每个 fingerprint，取所有 `year < Y` 的格子，按 `metric`（默认夏普）对多年取**简单平均**，再取 top K。无格子的 fingerprint 丢弃。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_experts.py -q`  
Expected: PASS

---

### Task 3: 从 trades 还原每日持仓

**Files:**
- Create: `quantlab/services/offline_rl/holdings.py`
- Test: `tests/quantlab/test_offline_rl_holdings.py`

**Produces:**
- `daily_holdings_from_trades(trades: list[dict], dates: list[str]) -> dict[str, set[str]]`  
  日期键为 `YYYYMMDD`。`filled` 且 `buy_date <= d < sell_date`（或 `sell_date` 缺失则持有到末）计入持仓。`unfilled` 忽略。

- [ ] **Step 1: Write the failing test**

```python
from quantlab.services.offline_rl.holdings import daily_holdings_from_trades

def test_holdings_span_buy_to_sell_exclusive():
    trades = [
        {
            "instrument": "600000.SH",
            "buy_date": "20180103",
            "sell_date": "20180105",
            "status": "filled",
        },
        {
            "instrument": "000001.SZ",
            "buy_date": "20180104",
            "sell_date": "20180105",
            "status": "filled",
        },
    ]
    dates = ["20180102", "20180103", "20180104", "20180105"]
    held = daily_holdings_from_trades(trades, dates)
    assert held["20180102"] == set()
    assert held["20180103"] == {"600000.SH"}
    assert held["20180104"] == {"600000.SH", "000001.SZ"}
    assert held["20180105"] == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_holdings.py -q`  
Expected: FAIL

- [ ] **Step 3: Implement holdings**

日期一律 `str.replace("-","")[:8]`。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/quantlab/test_offline_rl_holdings.py -q`  
Expected: PASS

---

### Task 4: 日收益、奖励与轨迹四元组

**Files:**
- Create: `quantlab/services/offline_rl/trajectories.py`
- Test: `tests/quantlab/test_offline_rl_trajectories.py`

**Produces:**
- `daily_returns_from_equity(curve: list[dict]) -> dict[str, float]` — `equity[t]/equity[t-1]-1`
- `align_excess_rewards(port_rets, bench_rets) -> dict[str, float]` — 交集日期上相减
- `transitions_for_expert(*, fingerprint, action_id, equity_curve, trades, bench_rets) -> list[Transition]`  
  `Transition` 为 dataclass：`date`, `action_id`, `reward`, `holdings: frozenset[str]`, `port_ret`

动作 `0` 预留给现金（持仓空、组合收益 0，超额 = 0 − 基准）。专家动作为 `1..K`。

- [ ] **Step 1: Write the failing test**

```python
from quantlab.services.offline_rl.trajectories import daily_returns_from_equity, align_excess_rewards

def test_daily_excess_reward():
    curve = [
        {"date": "20180102", "equity": 100.0},
        {"date": "20180103", "equity": 101.0},
        {"date": "20180104", "equity": 102.01},
    ]
    port = daily_returns_from_equity(curve)
    assert abs(port["20180103"] - 0.01) < 1e-12
    bench = {"20180103": 0.002, "20180104": 0.0}
    exc = align_excess_rewards(port, bench)
    assert abs(exc["20180103"] - 0.008) < 1e-12
```

- [ ] **Step 2–4:** 实现并通过测试。基准日收益由调用方注入（评估模块从 raw/基准序列算）；本任务不读磁盘。

---

### Task 5: 状态特征

**Files:**
- Create: `quantlab/services/offline_rl/state_features.py`
- Test: `tests/quantlab/test_offline_rl_state_features.py`

**Produces:**
- `StateBuilder(window: int = 20)`  
  - `observe(date, *, bench_rets, expert_excess: dict[int, dict[str, float]], current_action: int, cash_ratio: float) -> list[float]`  
  固定长度向量：基准近窗均值/波动、每个动作 `1..K` 近窗超额均值（缺失填 0）、`current_action` one-hot（含现金）、`cash_ratio`。  
  - `dim(k: int) -> int`

- [ ] **Step 1: Write failing test** — 同一输入两次向量相等；`window=2` 时长度稳定；缺专家历史时对应维为 0。  
- [ ] **Step 2–4:** 实现并通过。

---

### Task 6: Fitted Q（离散）

**Files:**
- Create: `quantlab/services/offline_rl/fitted_q.py`
- Test: `tests/quantlab/test_offline_rl_fitted_q.py`

**Produces:**
- `FittedQ(*, n_actions: int, gamma: float = 0.99, iterations: int = 10)`  
  - `fit(transitions: list[dict])` — 每项含 `state: list[float]`, `action: int`, `reward: float`, `next_state: list[float]`, `done: bool`  
  - `predict_q(state) -> list[float]` 长度 `n_actions`  
  - `greedy(state, *, available: set[int] | None = None) -> int`

实现：每轮用 `sklearn.linear_model.Ridge` **按动作分别**拟合 `Q(s,a)`（共 `n_actions` 个回归器），目标 `r + gamma * max_{a'} Q(s',a')`（`done` 则无 bootstrap）。第一期不用神经网络、不用排序树。

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from quantlab.services.offline_rl.fitted_q import FittedQ

def test_greedy_prefers_higher_reward_action():
    # 两动作：状态常数；动作1总给 +1，动作2总给 -1
    transitions = []
    for _ in range(40):
        s = [1.0, 0.0]
        transitions.append({"state": s, "action": 1, "reward": 1.0, "next_state": s, "done": False})
        transitions.append({"state": s, "action": 2, "reward": -1.0, "next_state": s, "done": False})
    model = FittedQ(n_actions=3, gamma=0.5, iterations=5)
    model.fit(transitions)
    assert model.greedy(s, available={1, 2}) == 1
```

- [ ] **Step 2–4:** 实现并通过。`available` 用于评估日某专家无持仓曲线时屏蔽该动作。

---

### Task 7: 走步评估与重放对照

**Files:**
- Create: `quantlab/services/offline_rl/evaluate.py`
- Test: `tests/quantlab/test_offline_rl_evaluate.py`

**Produces:**
- `walk_forward_years(grid) -> list[int]` — 可评分年（至少存在 `year-1` 的专家格子），默认从 2020 起若数据够  
- `replay_champion_curve(grid, year, metric="sharpe") -> dict` — 上年冠军在 `year` 的 equity 日收益与夏普/超额  
- `evaluate_policy_year(...)` — 用已 fit 的 Q，在 `year` 每个交易日贪心选动作，拼接所选专家当日组合收益（无该专家该年 `equity_curve` 的日收益），再算夏普/超额  
- `verdict(year_rows: list[dict]) -> dict` — `passed: bool`, `mean_sharpe_policy`, `mean_sharpe_replay`, `mean_excess_*`, `champion_copy_ratio`（策略所选==冠军日占比，可选）

- [ ] **Step 1: Write failing tests**

1. 人造两年网格：2019 冠军 fpA，2020 有 fpA 曲线收益恒正；重放 2020 夏普可算。  
2. 若策略每天选冠军，`champion_copy_ratio == 1`。  
3. `verdict`：策略年均夏普更高且超额不更差 → `passed True`。

- [ ] **Step 2–4:** 实现并通过。夏普口径对齐 `backtest_job._performance_metrics`：日收益均值/标准差 × √252；无交易日跳过。

**Benchmark 序列：** `load_benchmark_daily_returns(raw_root, code="000300.SH", dates=...)` 放在 `evaluate.py` 或小函数；从现有 raw/基准读取方式跟随 `fill_benchmark_metrics` 所用数据源（先读代码再复用，禁止另造基准文件格式）。

---

### Task 8: 实验编排（读档案、点跑、写产物）

**Files:**
- Create: `quantlab/services/offline_rl/experiment.py`
- Test: `tests/quantlab/test_offline_rl_evaluate.py`（扩展）或 `tests/quantlab/test_offline_rl_experiment.py`

**Produces:**
- `OfflineRlExperimentService(settings, database)`  
  - `preview(k=8, window=20) -> dict` — 网格摘要、排除长窗、可评分年，**不训练**  
  - `run(k=8, window=20, gamma=0.99, iterations=10) -> dict` — 全走步；若已有回测/计划在跑则 `raise RuntimeError("busy")`（复用计划页忙碌探测：查 `backtest_plans.status=='running'` 或现有 job 锁，与 `backtest_plan` 一致）  
  - 进度：内存快照 `{status, percent, message}`，仿 `lan_sync` 的 progress 模式（简单版即可）

写产物（可选但推荐）：`runtime_root/results/<run_id>/`  
- `run.json`：`kind=offline_rl`，含 K、window、各年表、verdict  
- `metrics.json`：汇总夏普等  
- **不要**自动 `execute` 普通回测

登记进 `backtest_runs`：若成本低则插入一行 `completed`；若插入路径复杂，第一期可只写目录 + API 返回 JSON，页面直接展示（计划允许「仅 API 返回」，档案登记列为同 Task 内若半小时内可完成则做）。

- [ ] **Step 1–4:** 用 tmp_path 造两个自然年假 `results/`，`preview` 看到排除长窗；`run` 返回 `verdict` 字段。

---

### Task 9: API

**Files:**
- Create: `quantlab/api/routes/offline_rl.py`
- Modify: `quantlab/api/routes/__init__.py`
- Test: `tests/quantlab/test_offline_rl_api.py`

**Endpoints:**
- `GET /api/offline-rl/preview?k=8&window=20`
- `POST /api/offline-rl/run` body `{k, window, gamma?, iterations?}` — **不**在 GET/保存时触发  
- `GET /api/offline-rl/progress`

忙碌 → HTTP 409，`error_code=OFFLINE_RL_BUSY`。

- [ ] **Step 1: Write API tests with TestClient + tmp settings**  
- [ ] **Step 2–4:** 实现并挂到 `register_routers`；在 `create_app` 可挂 `app.state.offline_rl_service`（或路由内懒创建）。

---

### Task 10: 页面与导航

**Files:**
- Create: `quantlab/web/pages/offline_rl.html`
- Create: `quantlab/web/assets/backtest/offline-rl.js`
- Modify: `quantlab/web/assets/nav.js` — 回测中心 children 增加 `{ href: "/backtests/offline-rl", label: "离线策略学习", ... }`  
- Modify: `quantlab/api/routes/pages.py` — `@router.get("/backtests/offline-rl")`  
- Modify: `tests/quantlab/test_web_shell.py` — 断言导航含「离线策略学习」、页面含运行按钮与结果表 id

**页面结构（与计划页同壳）：**
1. 说明：离散 Q、奖励=日超额、不自动开跑  
2. 参数：`K`、状态窗口；奖励只读说明  
3. 「刷新预览」→ preview；「运行实验」→ POST run（需二次确认文案可简单）  
4. 排除长窗列表；动作表（指纹短标签）  
5. 成绩表：年 | 重放夏普 | 重放超额 | Q 夏普 | Q 超额 | 与冠军相同日占比  
6. 总判定横幅：通过 / 未通过

- [ ] **Step 1: 合同测试先失败**  
- [ ] **Step 2–4:** 实现页面；样式复用 `plan-page` / `archive-table`，不新造设计体系。

---

### Task 11: 规格状态与自检

**Files:**
- Modify: `docs/specs/2026-09-14-offline-rl-from-backtests-design.md` — `状态：已确认`  
- 保持 `docs/specs/2026-09-14-backtest-distill-design.md` 为已撤回

- [ ] **Step 1:** 跑聚焦测试

```bash
.venv/bin/pytest tests/quantlab/test_offline_rl_*.py tests/quantlab/test_web_shell.py -q
```

Expected: PASS

- [ ] **Step 2:** 手工核对（不自动开跑）  
打开 `/backtests/offline-rl` → 刷新预览应列出自然年与排除长窗 → **不要**点运行除非用户要求。

---

## 规格覆盖自检

| 规格要点 | 任务 |
|---|---|
| 离散选专家 + 现金动作 0 | 4–6 |
| 长窗排除、走步、Y 前选 K | 2, 7 |
| 奖励日超额 | 4, 7 |
| Fitted Q，非 LightGBM | 6 |
| 重放冠军对照与判定 | 7–8 |
| 独立页面、点跑才学 | 9–10 |
| 不做连续权重 / IQL / 改工作台 | 全局约束 |
| 冠军复制占比提示 | 7, 10 |

## 执行时注意

- 还原持仓与 equity 日收益不一致时：奖励以 **equity_curve** 为准；`available` 以该专家该年是否有该日收益为准。  
- 行为轨迹几乎整天同一专家：训练四元组里 `action` 用该 run 的指纹映射到当年动作表 id；若某年指纹未入选 K，该 run 不进该折训练。  
- 第一期状态**不**读个股因子矩阵，避免做成回测中心翻版。
