# 单因子回测加速 Implementation Plan

> **For agentic workers:** REQUIRED: Use `subagent-driven-development` (if subagents available) or `executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `factor_rank` 不再为训练读整段样本，切池和年线只打在股票池上，研究批量跳过 10 遍分层回测；成交与时点成分语义不变。

**Architecture:** 向量化 `filter_index_universe_asof`（as-of 映射 + 成分表 merge）。`_load_frame` 改为：行情切片 → PIT 切池 → 挂旁路因子 → 个股 SMA。`research_frame_date_span` 在 `factor_rank` 下只用回测窗。`segment_curves` 配置控制是否跑 `attach_segment_curves`。步骤内部名不变，仅 `factor_rank` 改显示文案。

**Tech Stack:** Python、pandas、pytest、现有回测工作台 JS。

**Spec:** `docs/specs/2026-09-17-factor-rank-backtest-speed-design.md`

## Global Constraints

- 不改买卖规则、涨跌停、手续费、时点成分 as-of。
- 截面合成因子继续用已物化旁路，不在池内重算排名。
- 个股年线预热仍是 400 个自然日。
- 写计划或改代码后不自动开始回测。
- 未经用户明确要求不要 git commit / push。
- 中证2000 仍缺权重时继续用国证2000 `399303.SZ`，名称不要写成中证2000。

---

## File map

| 文件 | 职责 |
|---|---|
| `quantlab/services/index_membership.py` | 向量化 as-of 切池 |
| `quantlab/services/backtest_job.py` | 加载日期跨度、切池后再挂因子/年线、分层开关 |
| `quantlab/services/backtest_workbench.py` | 校验并冻结 `segment_curves` |
| `quantlab/services/result_archive.py` | `factor_rank` 步骤中文名 |
| `quantlab/web/assets/backtest/workbench.js` | 运行状态文案 |
| `quantlab/web/assets/backtest/run-record.js` | 运行记录步骤名、分层空态 |
| `quantlab/web/pages/backtest_workbench_formal.html` | 脚本缓存戳 |
| `tests/quantlab/test_index_membership.py` | 切池语义 |
| `tests/quantlab/test_factor_rank_frame.py` | 日期跨度、SMA 切池后不变、加载顺序 |
| `tests/quantlab/test_bucket_equity.py` | 关闭分层 |
| `tests/quantlab/test_backtest_workbench.py` | 配置规范化 |
| `tests/quantlab/test_result_archive.py` | 单因子步骤中文名 |

---

## Chunk 1: 向量化时点切池

### Task 1: as-of 切池改为映射 + merge

**Files:**
- Modify: `quantlab/services/index_membership.py`（`filter_index_universe_asof`，约 164–200 行）
- Test: `tests/quantlab/test_index_membership.py`

**Interfaces:**
- Consumes: `_member_snapshots`, `_normalize_trade_date`, `parse_universe_index_codes`
- Produces: `filter_index_universe_asof(frame, weights, index_codes=None) -> pd.DataFrame` 语义与现在相同

- [x] **Step 1: Write the failing tests**

在 `tests/quantlab/test_index_membership.py` 追加（现有 `test_filter_index_universe_asof_drops_future_constituents` 保留）：

```python
def test_filter_index_universe_asof_unions_two_indices():
    frame = pd.DataFrame(
        {
            "date": ["20180920", "20180920", "20180920"],
            "instrument": ["AAA.SZ", "BBB.SZ", "CCC.SZ"],
            "close": [1.0, 1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000905.SH"],
            "con_code": ["AAA.SZ", "BBB.SZ"],
            "trade_date": ["20180903", "20180903"],
            "weight": [1.0, 1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH", "000905.SH"))
    assert set(kept["instrument"]) == {"AAA.SZ", "BBB.SZ"}


def test_filter_index_universe_asof_drops_days_before_first_snapshot():
    frame = pd.DataFrame(
        {
            "date": ["20180801", "20180920"],
            "instrument": ["AAA.SZ", "AAA.SZ"],
            "close": [1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["AAA.SZ"],
            "trade_date": ["20180903"],
            "weight": [1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH",))
    assert list(zip(kept["date"], kept["instrument"], strict=True)) == [("20180920", "AAA.SZ")]


def test_filter_index_universe_asof_accepts_dashed_dates():
    frame = pd.DataFrame(
        {
            "date": ["2018-09-20", "2018-09-20"],
            "instrument": ["AAA.SZ", "BBB.SZ"],
            "close": [1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["AAA.SZ"],
            "trade_date": ["20180903"],
            "weight": [1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH",))
    assert list(kept["instrument"]) == ["AAA.SZ"]
```

- [ ] **Step 2: Run tests to verify new ones fail or old loop still passes**

Run: `.venv/bin/pytest tests/quantlab/test_index_membership.py -q`

Expected: 现有用例 PASS；若新用例在改前已因旧实现对齐也可能 PASS。改实现后三例必须 PASS，且函数体内不得再对每个 `day` 做 `day_keys.eq(day) & frame[code_col].astype(str).isin(...)`。

- [ ] **Step 3: Replace the per-day mask loop**

把 `filter_index_universe_asof` 中从 `day_keys = ...` 到 `return frame.loc[mask]` 换成：

```python
    union: dict[str, set[str]] = {}
    for index_code in codes:
        ordered, snaps = _member_snapshots(weights, index_code)
        for day, members in zip(ordered, snaps, strict=False):
            if not day:
                continue
            bucket = union.get(day)
            if bucket is None:
                union[day] = set(members)
            else:
                bucket.update(members)
    snap_dates = sorted(union)
    if not snap_dates:
        return frame.iloc[0:0].copy()

    day_keys = frame[date_col].map(_normalize_trade_date)
    code_keys = frame[code_col].astype(str).str.strip()
    unique_days = [day for day in dict.fromkeys(day_keys.tolist()) if day]
    day_to_snap: dict[str, str] = {}
    for day in unique_days:
        index = bisect.bisect_right(snap_dates, day) - 1
        if index >= 0:
            day_to_snap[day] = snap_dates[index]
    asof = day_keys.map(day_to_snap)
    members = pd.DataFrame(
        ((day, name) for day, names in union.items() for name in names),
        columns=["_snap", "_code"],
    )
    if members.empty:
        return frame.iloc[0:0].copy()
    members = members.drop_duplicates()
    keyed = pd.DataFrame(
        {"_row": frame.index, "_snap": asof.to_numpy(), "_code": code_keys.to_numpy()}
    )
    matched = keyed.merge(members, on=["_snap", "_code"], how="inner")
    return frame.loc[matched["_row"].to_numpy()].copy()
```

保留函数前半：空表 / 空权重 / 权重里没有请求指数时原样返回；`date`/`instrument` 与 `trade_date`/`ts_code` 列名探测不变。

- [ ] **Step 4: Re-run membership tests**

Run: `.venv/bin/pytest tests/quantlab/test_index_membership.py -q`

Expected: PASS

- [ ] **Step 5: Commit only if the user asks**

```bash
git add quantlab/services/index_membership.py tests/quantlab/test_index_membership.py
git commit -m "$(cat <<'EOF'
Speed up PIT universe filter with as-of merge instead of per-day full-frame isin.

EOF
)"
```

未要求提交则跳过。

---

## Chunk 2: 先切池再挂因子和年线

### Task 2: 调整 `_load_frame` 顺序，并锁住 SMA 不因切池改变

**Files:**
- Modify: `quantlab/services/backtest_job.py`（`_load_frame_with_pack_factors`，约 1711–1720 行；`attach_sma` 约 1236–1258 行保持算法）
- Create: `tests/quantlab/test_factor_rank_frame.py`

**Interfaces:**
- Consumes: `apply_pit_index_universe`, `attach_pack_factor_columns`, `_attach_config_sma`
- Produces: `_load_frame` 对切池后的表挂因子和 SMA

- [x] **Step 1: Write the failing tests**

创建 `tests/quantlab/test_factor_rank_frame.py`：

```python
from __future__ import annotations

import pandas as pd

from quantlab.services.backtest_job import attach_sma, prepare_research_frame
from quantlab.services.index_membership import filter_index_universe_asof


def test_stock_sma_matches_before_and_after_universe_filter():
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200103", "20200106"] * 2,
            "instrument": ["KEEP.SZ"] * 3 + ["DROP.SZ"] * 3,
            "hfq_close": [10.0, 11.0, 12.0, 20.0, 21.0, 22.0],
        }
    )
    full = attach_sma(frame.copy(), window=2)
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"] * 2,
            "con_code": ["KEEP.SZ", "KEEP.SZ"],
            "trade_date": ["20191231", "20191231"],
            "weight": [1.0, 1.0],
        }
    )
    pit = filter_index_universe_asof(frame.copy(), weights, ("000300.SH",))
    after = attach_sma(pit, window=2)
    keep_full = full.loc[full["instrument"] == "KEEP.SZ", "sma_2"].reset_index(drop=True)
    keep_after = after.loc[after["instrument"] == "KEEP.SZ", "sma_2"].reset_index(drop=True)
    pd.testing.assert_series_equal(keep_full, keep_after, check_names=False)


def test_prepare_research_frame_filters_before_attaching_sma(tmp_path, monkeypatch):
    calls: list[str] = []
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102"],
            "instrument": ["KEEP.SZ", "DROP.SZ"],
            "hfq_close": [10.0, 20.0],
            "close": [10.0, 20.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["KEEP.SZ"],
            "trade_date": ["20191231"],
            "weight": [1.0],
        }
    )
    raw = tmp_path / "raw"
    (raw / "index_weight").mkdir(parents=True)
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(
        pa.Table.from_pylist(weights.to_dict("records")),
        raw / "index_weight" / "index_weight_000300_SH.parquet",
    )

    import quantlab.services.backtest_job as job

    real_pit = job.apply_pit_index_universe if hasattr(job, "apply_pit_index_universe") else None

    def track_attach(frame_in, config):
        calls.append(("sma", int(len(frame_in))))
        return frame_in

    monkeypatch.setattr(job, "_attach_config_sma", track_attach)
    config = {
        "universe_index_codes": ["000300.SH"],
        "factor_versions": [],
        "train": {"filter": {"expressions": ["hfq_close > sma200"]}},
        "test": {"filter": {"expressions": ["hfq_close > sma200"]}},
    }
    out = prepare_research_frame(frame, config, raw, sidecar_path=tmp_path / "missing.parquet")
    assert list(out["instrument"]) == ["KEEP.SZ"]
    assert calls and calls[0][0] == "sma"
    assert calls[0][1] == 1
```

若 `prepare_research_frame` 尚不存在，Step 2 应变为 FAIL：`ImportError`.

- [ ] **Step 2: Run the new tests**

Run: `.venv/bin/pytest tests/quantlab/test_factor_rank_frame.py -q`

Expected: FAIL（没有 `prepare_research_frame`）

- [ ] **Step 3: Extract `prepare_research_frame` and point `_load_frame` at it**

在 `quantlab/services/backtest_job.py` 增加：

```python
def prepare_research_frame(frame, config, raw_root, sidecar_path):
    from quantlab.services.canonical_pack_factors import attach_pack_factor_columns
    from quantlab.services.index_membership import apply_pit_index_universe, parse_universe_index_codes

    codes = parse_universe_index_codes((config or {}).get("universe_index_codes"))
    narrowed = apply_pit_index_universe(frame, raw_root, codes)
    refs = (config or {}).get("factor_versions") or []
    with_factors = attach_pack_factor_columns(narrowed, refs, sidecar_path)
    return _attach_config_sma(with_factors, config)
```

`_load_frame_with_pack_factors` 改为：

```python
def _load_frame_with_pack_factors(self, path, config):
    from quantlab.services.canonical_pack_factors import default_sidecar_path

    frame = _load_frame_core(self, path, config)
    return prepare_research_frame(frame, config, self.settings.raw_root, default_sidecar_path(path))
```

`factor_versions` 为空时 `attach_pack_factor_columns` 必须直接返回（已有 `if not needed: return frame`）。`sidecar` 不存在且不需要字段时不得报错。

- [ ] **Step 4: Re-run**

Run: `.venv/bin/pytest tests/quantlab/test_factor_rank_frame.py tests/quantlab/test_index_membership.py -q`

Expected: PASS

- [ ] **Step 5: Commit only if the user asks**

---

## Chunk 3: 单因子不读训练年

### Task 3: `research_frame_date_span`

**Files:**
- Modify: `quantlab/services/backtest_job.py`（`_load_frame_core` 约 672–685 行）
- Modify: `tests/quantlab/test_factor_rank_frame.py`

**Interfaces:**
- Consumes: `resolve_kind` from `quantlab.services.model_training`
- Produces: `research_frame_date_span(config) -> tuple[str, str]`（YYYYMMDD, YYYYMMDD），不含预热

- [x] **Step 1: Write the failing tests**

追加到 `tests/quantlab/test_factor_rank_frame.py`：

```python
from quantlab.services.backtest_job import research_frame_date_span


def test_factor_rank_date_span_uses_test_window_only():
    config = {
        "kind": "factor_rank",
        "train": {"date_from": "2017-01-03", "date_to": "2017-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2026-08-31"},
    }
    assert research_frame_date_span(config) == ("20240102", "20260831")


def test_tree_model_date_span_covers_train_and_test():
    config = {
        "kind": "lightgbm_tree",
        "train": {"date_from": "2017-01-03", "date_to": "2017-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2026-08-31"},
    }
    assert research_frame_date_span(config) == ("20170103", "20260831")
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/pytest tests/quantlab/test_factor_rank_frame.py::test_factor_rank_date_span_uses_test_window_only tests/quantlab/test_factor_rank_frame.py::test_tree_model_date_span_covers_train_and_test -q`

Expected: FAIL

- [ ] **Step 3: Implement span + wire `_load_frame_core`**

```python
def research_frame_date_span(config):
    train, test = config["train"], config["test"]

    def compact(section, key):
        return str(section[key]).replace("-", "")[:8]

    test_from = compact(test, "date_from")
    test_to = compact(test, "date_to")
    nested = config.get("model") if isinstance(config.get("model"), dict) else {}
    kind = resolve_kind(config.get("kind") or nested.get("kind"), config.get("hyperparameters"))
    if kind == "factor_rank":
        return test_from, test_to
    train_from = compact(train, "date_from")
    train_to = compact(train, "date_to")
    return min(train_from, test_from), max(train_to, test_to)
```

`_load_frame_core` 把原来的 `start = min(...)` / `end = max(...)` 换成：

```python
    start, end = research_frame_date_span(config)
```

`load_warmup_start(start, config)` 仍紧跟其后。`factor_rank` 时训练过滤若落在加载范围外，得到空 `train`、`train_rows=0`，允许，不要为此重新拉 2017。

- [ ] **Step 4: Re-run**

Run: `.venv/bin/pytest tests/quantlab/test_factor_rank_frame.py -q`

Expected: PASS

- [ ] **Step 5: Commit only if the user asks**

---

## Chunk 4: 研究批量关掉分层 10 遍

### Task 4: `segment_curves` 配置

**Files:**
- Modify: `quantlab/services/backtest_job.py`（`_performance_metrics` 约 1124–1133 行）
- Modify: `quantlab/services/backtest_workbench.py`（`validate`/`normalize` 里布尔字段一段，约 255–260 行）
- Modify: `tests/quantlab/test_bucket_equity.py`
- Modify: `tests/quantlab/test_backtest_workbench.py`（若已有 validate 快照，补一项）

**Interfaces:**
- Consumes: `_as_bool`（job 与 workbench 各有一份）
- Produces: `wants_segment_curves(config) -> bool`；缺省 `True`；`segment_curves is False` 时写入 `status: "skipped"`

- [x] **Step 1: Write the failing tests**

在 `tests/quantlab/test_bucket_equity.py` 追加：

```python
def test_performance_metrics_skips_segment_curves_when_disabled() -> None:
    frame = _panel()
    predictions = frame[["date", "instrument", "score"]].copy()
    capture_predictions(frame, predictions)
    config = {
        "test": {"date_from": "20200102", "date_to": "20200107"},
        "segment_curves": False,
        "rebalance_every": 1,
        "holding_days": 1,
        "top_n": 1,
        "lot_size": 100,
        "initial_capital": 100_000,
    }
    equity = [
        {"date": "20200102", "equity": 100_000.0},
        {"date": "20200107", "equity": 100_000.0},
    ]
    metrics = _performance_metrics([], config, frame, equity_curve=equity)
    assert metrics["segment_curves"]["by_float_market_cap"]["status"] == "skipped"
    assert metrics["segment_curves"]["by_turn"]["status"] == "skipped"
    assert metrics["segment_curves"]["by_float_market_cap"]["buckets"] == []
    assert "return" in metrics or "max_drawdown" in metrics
```

保留 `test_performance_metrics_reads_captured_predictions_for_segment_curves`：缺省仍要 `available`。

- [ ] **Step 2: Run the skip test**

Run: `.venv/bin/pytest tests/quantlab/test_bucket_equity.py::test_performance_metrics_skips_segment_curves_when_disabled -q`

Expected: FAIL

- [ ] **Step 3: Implement flag**

在 `backtest_job.py`：

```python
def wants_segment_curves(config) -> bool:
    payload = config if isinstance(config, dict) else {}
    if "segment_curves" not in payload:
        return True
    return _as_bool(payload.get("segment_curves"), True)
```

`_performance_metrics`：

```python
def _performance_metrics(trades, config, frame, equity_curve=None, raw_root=None):
    metrics = _compute_performance_metrics(
        trades, config, frame, equity_curve=equity_curve, raw_root=raw_root
    )
    captured_frame, predictions = peek_captured()
    source = captured_frame if captured_frame is not None else frame
    if wants_segment_curves(config):
        portfolio_config = _segment_portfolio_config(
            config, source if source is not None else frame, raw_root
        )
        metrics = attach_segment_curves(metrics, source, predictions, portfolio_config)
    else:
        metrics["segment_curves"] = {
            "by_float_market_cap": {"field": "float_market_cap", "status": "skipped", "buckets": []},
            "by_turn": {"field": "turn", "status": "skipped", "buckets": []},
        }
    metrics = attach_ranking_metrics(metrics, frame, config)
    return _attach_extra_performance_metrics(metrics, equity_curve)
```

在 `backtest_workbench.py` 规范化段（与其它 `_as_bool` 字段一起）：

```python
        if "segment_curves" in c:
            c["segment_curves"] = _as_bool(c.get("segment_curves"), True)
```

缺省不要写入 `false`，以免改变现有工作台单笔。

研究大批量创建脚本 / 人工 POST 计划时，每条 config 加 `"segment_curves": false`。本轮不改已停的 `plan-4b0c6da8072a`，等用户说开始再写新计划。

- [ ] **Step 4: Re-run bucket + workbench tests**

Run: `.venv/bin/pytest tests/quantlab/test_bucket_equity.py tests/quantlab/test_backtest_workbench.py -q`

Expected: PASS

- [ ] **Step 5: Commit only if the user asks**

---

## Chunk 5: 单因子步骤文案

### Task 5: `factor_rank` 显示「准备数据 / 因子排序」

**Files:**
- Modify: `quantlab/services/result_archive.py`（`_STEP_LABELS`、`_annotate_step_timing`）
- Modify: `quantlab/web/assets/backtest/workbench.js`（`RUN_STEP_LABELS`、`applyRunStatus`）
- Modify: `quantlab/web/assets/backtest/run-record.js`（`STEP_LABELS`、`renderSegmentDimension` 空文案）
- Modify: `quantlab/web/pages/backtest_workbench_formal.html`（`workbench.js?v=`）
- Modify: `quantlab/web/pages/backtest_run_record.html`（`run-record.js?v=`）
- Test: `tests/quantlab/test_result_archive.py`

**Interfaces:**
- Produces: 步骤 key 仍是 `model_training` / `prediction` / `execution`；仅 label 随 `kind==factor_rank` 变化

- [ ] **Step 1: Shared label helper in result_archive**

```python
_STEP_LABELS = {
    "snapshot_validation": "快照校验",
    "model_training": "模型训练",
    "prediction": "预测打分",
    "positions": "生成仓位",
    "execution": "撮合成交",
    "metrics": "指标汇总",
}
_FACTOR_RANK_STEP_LABELS = {
    **_STEP_LABELS,
    "model_training": "准备数据",
    "prediction": "因子排序",
}


def step_label(step_name: str, *, kind: str | None = None) -> str:
    names = _FACTOR_RANK_STEP_LABELS if kind == "factor_rank" else _STEP_LABELS
    key = str(step_name or "")
    return names.get(key, key)
```

`_annotate_step_timing` 增加 `kind` 参数，用 `step_label(...)`。调用处从该 run 的 `config_json.kind` 传入。

- [ ] **Step 2: workbench.js**

```javascript
function stepLabels(kind) {
  const base = {
    snapshot_validation: "快照校验",
    model_training: "模型训练",
    prediction: "预测打分",
    positions: "生成仓位",
    execution: "撮合成交",
    metrics: "指标汇总",
  };
  if (kind === "factor_rank") {
    return { ...base, model_training: "准备数据", prediction: "因子排序" };
  }
  return base;
}
```

`applyRunStatus` 用 `payload.config?.kind || selectedKind()`。`model_training` 完成日志：`factor_rank` 写「数据准备完成，进入回测」，其它种类仍写「训练完成，进入回测」。

页面上 `#run-steps` 的静态文字在 `syncKindFields` 时改一遍，避免未开始时仍显示「模型训练」。

- [ ] **Step 3: run-record 分层空态**

`status === "skipped"` 时：

「这次按配置跳过了市值/换手分层回测。」

其它非 `available` 保持原句。

缓存戳改为 `?v=20260917speed`（`workbench.js`、`run-record.js`、引用它们的 html）。

- [ ] **Step 4: Tests for labels**

在 `tests/quantlab/test_result_archive.py` 追加：

```python
from quantlab.services.result_archive import step_label


def test_step_label_uses_factor_rank_copy():
    assert step_label("model_training") == "模型训练"
    assert step_label("prediction") == "预测打分"
    assert step_label("model_training", kind="factor_rank") == "准备数据"
    assert step_label("prediction", kind="factor_rank") == "因子排序"
```

缓存戳两处都改成 `?v=20260917speed`：
- `quantlab/web/pages/backtest_workbench_formal.html`
- `quantlab/web/pages/backtest_run_record.html`

Run: `.venv/bin/pytest tests/quantlab/test_result_archive.py::test_step_label_uses_factor_rank_copy -q`

Expected: PASS

- [ ] **Step 5: Commit only if the user asks**

---

## Chunk 6: 部署与验证（不跑回测）

### Task 6: 测试全集 + 188 同步说明

**Files:** 无新代码。188 若要生效需拷贝上述 py/js/html 并重启 `8765`。

- [ ] **Step 1: Local pytest**

Run:

```bash
.venv/bin/pytest tests/quantlab/test_index_membership.py tests/quantlab/test_factor_rank_frame.py tests/quantlab/test_bucket_equity.py tests/quantlab/test_backtest_workbench.py -q
```

Expected: PASS

- [ ] **Step 2: 188 只在用户要求时拷贝并重启 serve**

拷贝清单：

- `quantlab/services/index_membership.py`
- `quantlab/services/backtest_job.py`
- `quantlab/services/backtest_workbench.py`
- `quantlab/services/result_archive.py`
- `quantlab/web/assets/backtest/workbench.js`
- `quantlab/web/assets/backtest/run-record.js`
- 对应 html

重启方式：按 PID 杀 `quantlab.cli serve`，不要 `pkill -f`（会误杀 SSH）。**不要** start `plan-4b0c6da8072a` 或任何新计划。

- [ ] **Step 3: 验收对照**

| 检查 | 期望 |
|---|---|
| 时点成分 | 调仓日后才纳入新成员 |
| 单因子加载窗 | 确认窗不再从 2017 读到 2026 |
| 分层 | `segment_curves: false` 主曲线仍在，无 10 遍额外组合 |
| 树模型 | 加载窗仍含训练段 |
| 回测 | 本轮不自动开始 |

---

## Out of scope（明确留下）

- Rank IC / NDCG 改按月或关掉
- 买卖引擎、400 天预热
- 工作台「计算分层净值」开关 UI
- 中证2000 `932000.CSI` 权重下载
- 自动开跑国证2000 大批量
