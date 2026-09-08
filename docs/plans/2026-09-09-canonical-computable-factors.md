# 宽表可算因子包 Implementation Plan

**Goal:** 22 条宽表公式按现有因子格式计算、验证、发布入库。窗口为数据版本最后一年。

**Architecture:** `canonical_factor_pack.py` 固定公式表。每条：草稿 → `FactorCalculationService.run`（最后一年）→ 覆盖率/有效天数门槛 → `quality_status=passed` → publish。IC 正负不拦。已发布跳过。前端逐条 POST。

不要改 canonical.parquet、不要改已入库 7 个因子、不要改训练/成交、不要写入 `FEATURE_FIELDS`。本工作区禁止主动 git commit。

**Tech Stack:** FastAPI、SQLite FactorVersion、现有手工表达式 AST、pytest、Playwright。

**Spec:** `docs/specs/2026-09-09-canonical-computable-factors-design.md`

## 文件

- Create: `quantlab/services/canonical_factor_pack.py` — 22 条公式表、清单、安装草稿
- Modify: `quantlab/services/factor_manual.py` — 抽出 `finite_factor_values`
- Modify: `quantlab/services/factor_calculation.py` — `_formula_frame` 使用该函数
- Modify: `quantlab/api/app.py` — GET/POST 公式包 API
- Modify: `quantlab/web/pages/factor_manual.html` — 生成草稿按钮与清单
- Modify: `quantlab/web/assets/app.js` — 安装按钮逻辑
- Test: `tests/quantlab/test_canonical_factor_pack.py`
- Test: `tests/quantlab/test_factor_calculation.py`（无穷值）
- Test: `tests/quantlab/test_web_shell.py`
- Test: `tests/quantlab/browser/test_factor_manual_browser.py`

---

## Chunk 1: 公式表与安装草稿

### Task 1: 公式表覆盖 22 条且不含已入库 7 个

**Files:**

- Create: `quantlab/services/canonical_factor_pack.py`
- Test: `tests/quantlab/test_canonical_factor_pack.py`

- [ ] **Step 1: 写失败测试**

```python
from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK, pack_entity_id

def test_pack_has_twenty_two_stable_formulas():
    fields = [item.field for item in CANONICAL_FACTOR_PACK]
    assert fields == [
        "momentum_1", "momentum_10", "momentum_20", "momentum_60",
        "close_bias_20", "close_bias_60", "close_zscore_20", "close_zscore_60",
        "close_ts_rank_20", "close_ts_rank_60",
        "amount_zscore_20", "vol_mean_20", "amount_cs_rank", "turn_cs_rank",
        "pe_ttm_cs_rank", "float_mv_cs_rank", "total_mv_cs_rank", "div_yield_cs_rank",
        "intraday_range", "overnight_ret", "close_location", "trade_vwap",
    ]
    assert "momentum_5" not in fields
    assert "volatility_5" not in fields
    assert pack_entity_id("momentum_20") == "factor_momentum_20"
    by_field = {item.field: item for item in CANONICAL_FACTOR_PACK}
    assert by_field["momentum_20"].formula == "hfq_close.pct_change(20)"
    assert by_field["intraday_range"].formula == "(hfq_high - hfq_low) / hfq_close"
    assert by_field["pe_ttm_cs_rank"].direction == "negative"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/quantlab/test_canonical_factor_pack.py::test_pack_has_twenty_two_stable_formulas -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 最小实现**

`canonical_factor_pack.py` 用 `@dataclass(frozen=True)`：`field, name, formula, direction, category`。`direction` 只能是 `positive` 或 `negative`。`pack_entity_id(field) -> f"factor_{field}"`。按 spec 填 22 条。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/quantlab/test_canonical_factor_pack.py::test_pack_has_twenty_two_stable_formulas -q`

Expected: PASS

### Task 2: 公式都能被现有解析器解析

**Files:**

- Test: `tests/quantlab/test_canonical_factor_pack.py`

- [ ] **Step 1: 写失败测试**

```python
from quantlab.services.factor_manual import parse_expression
from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK

CANONICAL_FIELDS = {
    "hfq_open", "hfq_high", "hfq_low", "hfq_close", "amount", "vol",
    "turn", "pe_ttm", "float_market_cap", "total_market_cap", "dividend_yield_ratio",
}

def test_pack_formulas_parse_against_canonical_fields():
    for item in CANONICAL_FACTOR_PACK:
        parsed = parse_expression(item.formula, CANONICAL_FIELDS)
        assert parsed.input_fields
        assert set(parsed.input_fields) <= CANONICAL_FIELDS
```

- [ ] **Step 2: 跑测试**

若公式写错会 FAIL。修公式，不要改解析器语法。

### Task 3: 安装草稿不读 parquet，已存在则跳过

**Files:**

- Modify: `quantlab/services/canonical_factor_pack.py`
- Test: `tests/quantlab/test_canonical_factor_pack.py`

仿 `test_factor_manual.py` 的 `_setup`：登记 `ds_canonical_market/current`，`fields_json` 含公式所需列。路径指向一个**空或不存在也行的占位 parquet 也可以**，但安装函数不得打开该文件。

- [ ] **Step 1: 写失败测试**

```python
def test_install_pack_creates_drafts_without_reading_parquet(tmp_path, monkeypatch):
    # 建 settings/database/dataset 后：
    def boom(*_args, **_kwargs):
        raise AssertionError("pack install must not read parquet")
    monkeypatch.setattr("pandas.read_parquet", boom)
    monkeypatch.setattr("pyarrow.parquet.ParquetFile", boom)
    service = CanonicalFactorPackService(settings, database, FactorRepository(settings, database))
    first = service.install_drafts("ds_canonical_market", "current")
    assert first["created_count"] == 22
    assert first["skipped_count"] == 0
    row = factors.get("factor_momentum_20", "v1")
    assert row["status"] == "draft"
    assert row["formula"] == "hfq_close.pct_change(20)"
    assert row["origin"] == "import"
    second = service.install_drafts("ds_canonical_market", "current")
    assert second["created_count"] == 0
    assert second["skipped_count"] == 22
```

未发布数据集应 `ValueError`。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 `install_drafts`**

对每条：若 `factors.get(entity_id, "v1")` 已有 → skipped。否则 `parse_expression(formula, metadata_fields)`，`import_definition`：

- `origin`: `"import"`
- `author`: `"canonical_factor_pack"`
- `source`: `"canonical_expression"`
- `missing_policy`: `"drop"`
- `pit_policy`: `"as-of trade_date"`
- `quality_status`: `"needs_review"`
- `code_hash`: `sha256` of formula
- `version_id`: `"v1"`

从 `ManualFactorService._dataset` 只取 row + fields，不要 `_read`。

单条 `ExpressionError` 进 `failed`，其余继续。

- [ ] **Step 4: 跑测试确认通过**

### Task 4: HTTP API

**Files:**

- Modify: `quantlab/api/app.py`
- Test: `tests/quantlab/test_canonical_factor_pack.py`

- [ ] **Step 1: 写 API 测试**

用 `create_app` + 临时库（复制现有 factor draft client 写法）：

- `GET /api/factor-packs/canonical` 返回 `items` 长度 22，每项含 `field, name, formula, entity_id, present`
- `POST /api/factor-packs/canonical/drafts` 默认数据集，`created_count == 22`
- 再 POST 一次 `skipped_count == 22`

- [ ] **Step 2: 接线**

```python
@app.get("/api/factor-packs/canonical")
def factor_pack_canonical():
    return pack_service.catalog()

@app.post("/api/factor-packs/canonical/drafts")
async def factor_pack_install(request: Request):
    body = await _json_body(request)
    return pack_service.install_drafts(
        str(body.get("dataset_id") or "ds_canonical_market"),
        str(body.get("dataset_version_id") or "current"),
    )
```

`create_app` 里构造 `CanonicalFactorPackService`。

---

## Chunk 2: 公式求值把无穷变成缺失

### Task 5: `_formula_frame` 与预览同一套有限值

两列公式会除零。预览已经 mask inf；计算任务还没有。

**Files:**

- Modify: `quantlab/services/factor_manual.py`
- Modify: `quantlab/services/factor_calculation.py`
- Test: `tests/quantlab/test_factor_manual.py` 或 `tests/quantlab/test_factor_calculation.py`

- [ ] **Step 1: 写失败测试**

构造一天 `hfq_high == hfq_low == hfq_close` 的面板，公式 `(hfq_close - hfq_low) / (hfq_high - hfq_low)`，经 `_formula_frame` 后该行 `factor_value` 为 NA，不能是 inf。

若现有测试不便构造完整 binding，可先测抽出的：

```python
def test_finite_factor_values_turn_inf_into_missing():
    series = pd.Series([1.0, float("inf"), float("-inf"), 0.0])
    out = finite_factor_values(series)
    assert list(out.isna()) == [False, True, True, False]
```

再加一条 `_formula_frame` 集成测试（复制 `test_factor_calculation` 里手工公式因子的夹具）。

- [ ] **Step 2: 实现**

```python
def finite_factor_values(values: pd.Series) -> pd.Series:
    series = pd.Series(values, dtype="float64")
    return series.mask(~series.replace([float("inf"), float("-inf")], pd.NA).notna())
```

`preview` 和 `_formula_frame` 赋值 `factor_value` 前都走它。

---

## Chunk 3: 手动建因子页

### Task 6: 页面有生成按钮和 22 条清单

**Files:**

- Modify: `quantlab/web/pages/factor_manual.html`
- Modify: `quantlab/web/assets/app.js`
- Test: `tests/quantlab/test_web_shell.py`
- Test: `tests/quantlab/browser/test_factor_manual_browser.py`

- [ ] **Step 1: 扩展 shell / 浏览器测试**

`test_web_shell.py` 的 `test_factor_workflow_pages_share_back_path_and_step_state` 增加：

- `id="canonical-factor-pack"`
- 按钮文案「生成宽表公式包草稿」
- `id="canonical-factor-pack-list"`

浏览器测试在现有 `test_manual_factor_page_exposes_safe_authoring_controls` 增加：按钮可见；点开后清单出现「20 日动量」或 `momentum_20`。不要在浏览器里对真实 1028 万行点安装（本机内存不够）。安装行为用 API 单测覆盖。

可选：浏览器 mock 掉 POST，或只断言 GET 清单渲染，点击安装用 `page.route` 拦截 POST 返回假数据。

- [ ] **Step 2: 页面**

在表单前或后加一节：

```html
<section id="canonical-factor-pack" class="card">
  <h3>宽表公式包</h3>
  <p class="form-note">从标准行情宽表生成 22 条草稿，不自动发布，不算整表 IC。已有同代码的因子会跳过。</p>
  <button id="canonical-factor-pack-install" type="button">生成宽表公式包草稿</button>
  <div id="canonical-factor-pack-result" class="state hidden"></div>
  <div id="canonical-factor-pack-list"></div>
</section>
```

Bump `app.css` / `app.js` query string。

- [ ] **Step 3: JS**

进入 `/factors/new` 时 GET 清单并渲染名称+公式。点按钮 POST `/api/factor-packs/canonical/drafts`，把 `created_count` / `skipped_count` / `failed` 写到结果区。失败显示错误，不要重试循环。

---

## 验收命令

```bash
.venv/bin/python -m pytest tests/quantlab/test_canonical_factor_pack.py tests/quantlab/test_factor_manual.py tests/quantlab/test_web_shell.py tests/quantlab/browser/test_factor_manual_browser.py -q
```

浏览器有环境再跑。不要对生产 `data/canonical.parquet` 跑 22 条全历史计算作为本计划验收。

## 明确不做

- 不改自动挖掘窗口、不算子、不把公式包塞进挖掘任务。
- 不一次跑 22 条因子计算分析。
- 不把草稿勾进回测组合。
- 不实现收益波动新算子、上市天数。
