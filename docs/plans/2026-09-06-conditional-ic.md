# 因子明细分层 IC Implementation Plan

> **For agentic workers:** REQUIRED: 用 TDD 按任务实现。本工作区禁止主动 git commit。改 HTML/CSS/JS 后 bump `factors.html` 的 query string。不要改已存因子 Parquet、不要回写旧 `factor_calculation_runs`、不要改全体 RankIC 口径。

**Goal:** 因子数据明细在现有 IC 图下增加流通市值 / 换手五档的 conditional RankIC，柱状图 + 每日曲线，数字写入该次计算任务的 `summary_json`。

**Architecture:** 读行情时多带 `float_market_cap`、`turn`；`_crop` 保留这两列；`cross_section_diagnostics` 在按日循环里算档内 Spearman；结果进 `conditional_ic`。明细页从任务对象读取并绘图。旧任务缺键则提示重跑。

**Tech Stack:** pandas `qcut` + rank corr、`factor_calculation_runs.summary_json`、Lightweight Charts、pytest、Playwright。

**Spec:** `docs/specs/2026-09-06-conditional-ic-design.md`

## 文件

- Modify: `quantlab/services/factor_calculation.py` — 读列、裁切、诊断、`_summary`、`_project`
- Modify: `quantlab/web/assets/app.js` — 摘要两行 + 分层图
- Modify: `quantlab/web/assets/app.css` — 可正可负的五档柱
- Modify: `quantlab/web/pages/factors.html` — bump `app.css` / `app.js`
- Test: `tests/quantlab/test_factor_calculation.py`
- Test: `tests/quantlab/test_web_shell.py`
- Test: `tests/quantlab/browser/test_factor_detail_browser.py`

---

## Chunk 1: 诊断与落库

### Task 1: 无特征列时分层状态为 missing_field，全体 IC 不变

**Files:**

- Modify: `quantlab/services/factor_calculation.py`
- Test: `tests/quantlab/test_factor_calculation.py`

- [ ] **Step 1: 扩展现有诊断测试**

在 `test_cross_section_diagnostics_match_rank_ic_and_skip_thin_days` 末尾增加：

```python
    cond = result["conditional_ic"]
    assert cond["by_float_market_cap"]["status"] == "missing_field"
    assert cond["by_float_market_cap"]["buckets"] == []
    assert cond["by_turn"]["status"] == "missing_field"
    assert cond["by_turn"]["buckets"] == []
```

`test_cross_section_diagnostics_match_legacy_daily_loop` 在对比 legacy 之后同样断言 `missing_field`。不要把 `conditional_ic` 塞进 legacy 对照字典。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/quantlab/test_factor_calculation.py::test_cross_section_diagnostics_match_rank_ic_and_skip_thin_days -q`

Expected: FAIL，`conditional_ic` KeyError。

- [ ] **Step 3: 最小实现**

`quantlab/services/factor_calculation.py`：

```python
CONDITIONAL_FIELDS = ("float_market_cap", "turn")
CAP_BUCKET_LABELS = {1: "Q1 小盘", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 大盘"}
TURN_BUCKET_LABELS = {1: "Q1 低换手", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 高换手"}
```

`cross_section_diagnostics` 开头不要只切四列；保留上述字段。循环里全体 IC 之后调用档内 IC。无列时返回 `status=missing_field, buckets=[]`。

档内 IC 辅助逻辑（写在同一文件，供诊断函数调用）：

```python
def _rank_ic(factor: pd.Series, target: pd.Series) -> float | None:
    ic = factor.rank(method="average").corr(target.rank(method="average"))
    return float(ic) if pd.notna(ic) else None


def _empty_conditional_dimension(field: str, *, status: str, labels: dict[int, str]) -> dict[str, Any]:
    if status == "missing_field":
        return {"field": field, "status": status, "buckets": []}
    return {
        "field": field,
        "status": status,
        "buckets": [
            {
                "bucket": index,
                "label": labels[index],
                "ic_mean": None,
                "ic_positive_ratio": None,
                "effective_days": 0,
                "daily_ic": [],
            }
            for index in range(1, 6)
        ],
    }
```

- [ ] **Step 4: 再跑 Step 2 的测试**

Expected: PASS。顺带跑 `test_cross_section_diagnostics_match_legacy_daily_loop`。

### Task 2: 档内 RankIC 与手算一致

**Files:**

- Modify: `quantlab/services/factor_calculation.py`
- Test: `tests/quantlab/test_factor_calculation.py`

- [ ] **Step 1: 写失败测试**

15 只股票、两日。流通市值按 1–15 分成 5 档、每档 3 只。换手用另一套排列，避免与市值完全共线。档内因子与收益同序，IC 应为 1.0。全体 IC 用一个「无特征列的对照 frame」先算一遍，加上两列后再算，全体 `daily_ic`/`ic_mean` 必须相同。

```python
def test_conditional_ic_by_cap_and_turn_matches_within_bucket_spearman() -> None:
    rows = []
    for date, sign in (("20240102", 1.0), ("20240104", 1.0)):
        for index in range(15):
            cap_bucket = index // 3  # 0..4
            turn_bucket = index % 5
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": f"{index:06d}.SZ",
                    "_factor": float(index % 3) * sign,
                    "_target": float(index % 3) * sign,
                    "float_market_cap": float(10 + cap_bucket * 100 + index),
                    "turn": float(0.1 + turn_bucket + index * 0.001),
                }
            )
    frame = pd.DataFrame(rows)
    baseline = cross_section_diagnostics(frame.drop(columns=["float_market_cap", "turn"]))
    result = cross_section_diagnostics(frame)
    assert result["daily_ic"] == baseline["daily_ic"]
    assert result["ic_mean"] == baseline["ic_mean"]
    cap = result["conditional_ic"]["by_float_market_cap"]
    assert cap["status"] == "available"
    assert len(cap["buckets"]) == 5
    for bucket in cap["buckets"]:
        assert bucket["effective_days"] == 2
        assert bucket["ic_mean"] == pytest.approx(1.0)
        assert bucket["daily_ic"][0]["count"] == 3
    turn = result["conditional_ic"]["by_turn"]
    assert turn["status"] == "available"
    for bucket in turn["buckets"]:
        assert bucket["effective_days"] == 2
        assert bucket["ic_mean"] == pytest.approx(1.0)
```

若构造导致某维档内因子为常数，RankIC 为 null：调整 `_factor/_target` 使每档至少有 3 个不同秩。原则：每档 3 只的因子值取 `{0,1,2}`，收益同序。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/quantlab/test_factor_calculation.py::test_conditional_ic_by_cap_and_turn_matches_within_bucket_spearman -q`

Expected: FAIL（档内 IC 尚未计算或 status 仍是 missing_field）。

- [ ] **Step 3: 在按日循环中实现档内 qcut + RankIC**

对每个特征列：当日该列非空子集 `qcut`；每档 `len>=3` 才记 `{date, ic, count}`。循环结束后按档汇总 `ic_mean` / `ic_positive_ratio` / `effective_days`，截断 `daily_ic[-MAX_SERIES_POINTS:]`。有列但无任何有效点 → `insufficient_data`。

薄日（全体 < 3）整日跳过，分层也不做。

- [ ] **Step 4: 跑 Task 1+2 相关测试**

Expected: PASS。

### Task 3: 读行情保留两列，summary 写入并投影

**Files:**

- Modify: `quantlab/services/factor_calculation.py`（`_read` 的 extra、`_crop`、`_summary`、`_project`）
- Test: `tests/quantlab/test_factor_calculation.py`

- [ ] **Step 1: 写失败测试**

扩展 `_formula_env` 或新 fixture：parquet 含 `hfq_open/hfq_close/close/float_market_cap/turn`。`run` 完成后：

```python
    assert result["conditional_ic"]["by_float_market_cap"]["status"] in {"available", "insufficient_data"}
    assert result["summary"]["conditional_ic"]["by_turn"]["field"] == "turn"
```

现有 `_env`（只有 momentum/future_return）跑完后应为 `missing_field`，且 `test_rerun_same_factor_keeps_equal_metrics_and_new_row` 增加：

```python
    assert second["summary"]["conditional_ic"] == first["summary"]["conditional_ic"]
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL，`conditional_ic` 不在投影结果里，或裁切丢掉了列导致 `missing_field`。

- [ ] **Step 3: 接线**

- `_builtin_frame` / `_formula_frame` 的 `extra_columns` 追加 `float_market_cap`、`turn`。
- `_crop` 保留这两列（若在 frame 中）。
- `_summary` 返回值加入截断后的 `conditional_ic`。
- `_project`：`"conditional_ic": summary.get("conditional_ic") or {}`。

3 只股票的公式测试环境可能是 `insufficient_data`（每档不够 3 只），这是正确行为；断言 status 不是 KeyError 即可。Task 2 的 15 股面板负责 available。

- [ ] **Step 4: 跑**

`python -m pytest tests/quantlab/test_factor_calculation.py -q`

Expected: PASS。

---

## Chunk 2: 明细页

### Task 4: 摘要两行 + 分层图

**Files:**

- Modify: `quantlab/web/assets/app.js`
- Modify: `quantlab/web/assets/app.css`
- Modify: `quantlab/web/pages/factors.html`（`app.css?v=`、`app.js?v=` 改为 `20260906a`）
- Test: `tests/quantlab/test_web_shell.py`

- [ ] **Step 1: 写失败测试**

`test_factor_data_page_declares_ic_annotations_and_calculation_history` 增加：

```python
    assert "分层 IC · 流通市值" in app
    assert "小盘 IC" in app
    assert "此任务未含分层 IC，重新运行分析后显示。" in app
    assert "factor-ic-bucket-bars" in css
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/quantlab/test_web_shell.py::test_factor_data_page_declares_ic_annotations_and_calculation_history -q`

- [ ] **Step 3: 实现 UI**

- `FACTOR_METRIC_EXPLANATIONS` 增加「小盘 IC」「大盘 IC」。
- 有效性指标 ICIR 后插入两行，值来自 `completed.conditional_ic || completed.summary?.conditional_ic`。
- `renderFactorDailyIc`：Top-Bottom 之后、直方图之前，对市值/换手各渲染一块。无键 → 一条 state 提示。`missing_field` / `insufficient_data` 用 spec 文案。`available`：五档柱（零轴居中，正蓝负红）+ 五条 `addLineSeries`。
- CSS：`.factor-ic-bucket-bars` 高度约 150px，中线为 0；柱从中线向上或向下。

曲线配色：`#9ecae1`、`#6baed6`、`#3182bd`、`#08519c`、`#d94801`。

- [ ] **Step 4: 再跑 web_shell 测试**

Expected: PASS。

---

## Chunk 3: 浏览器

### Task 5: Playwright 拦截计算历史并核对图表

**Files:**

- Test: `tests/quantlab/browser/test_factor_detail_browser.py`

- [ ] **Step 1: 写失败测试**

用 `page.route` 拦截 `**/api/factor-calculations?*`，返回一条 `status=completed` 且带 `conditional_ic.by_float_market_cap` 五档（含 `daily_ic` 两点）的任务。打开 `/factors/momentum_5`。

断言：

```python
    expect(page.locator("#factor-summary")).to_contain_text("小盘 IC")
    expect(page.locator("#factor-charts")).to_contain_text("分层 IC · 流通市值")
    expect(page.locator("#factor-charts")).to_contain_text("Q1 小盘")
```

不要在浏览器里点「运行分析」（会扫真实 parquet）。

- [ ] **Step 2: 跑确认失败**

`python -m pytest tests/quantlab/browser/test_factor_detail_browser.py -q`

- [ ] **Step 3: 若失败是因为 route 没罩住或标题文案不一致，改测试或 UI 文案直到与 spec 一字不差**

- [ ] **Step 4: 跑相关测试**

```
python -m pytest tests/quantlab/test_factor_calculation.py tests/quantlab/test_web_shell.py tests/quantlab/browser/test_factor_detail_browser.py -q
```

Expected: PASS。

---

## 完成标准

- 旧 IC 数字不变（legacy 对照仍绿）。
- 新任务 summary 含 `conditional_ic`。
- 明细页能看到市值/换手分层图；旧任务看到重跑提示。
- 不 commit。
