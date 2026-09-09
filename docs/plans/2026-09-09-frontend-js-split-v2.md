# 收敛前端 JS Implementation Plan（P2 第二版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **取代：** 桌面稿 `/Users/weiminzhu/Desktop/2026-09-09-frontend-js-split.md`（v1）。以本文件为准。
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。
>
> **落地顺序：** 建议在 P3 之后。本计划 **必须** 跑 Playwright（`tests/quantlab/browser`），不再标可选。

**Goal:** 把回测域大段 inline script 与 `quantlab/web/assets/app.js`（3976 行）拆成按域的静态 JS 模块；HTML 只保留结构；行为与页面契约冻结；用现有 Playwright 与 `test_web_shell.py` 做回归护栏。

**Architecture:** 无 bundler（仓库无 package.json）。继续用多文件 `<script src>` 顺序加载，**禁止 `type="module"`**。先扩 no-store（否则新文件会被浏览器缓存），再抽 shared，再外提 6 个回测/设置页 inline，最后按域切 `app.js`。

**Tech Stack:** 原生脚本（与现有 `app.js` / `nav.js` 一致）；Playwright browser；FastAPI `quantlab/api/static.py`；`.venv` Python 3.12。

## Global Constraints

- **行为冻结：** 不改 API URL、页面路由、DOM id/class（除非同步改测试）。**不改** CSS `?v=` query（测试钉死例如 `app.css?v=20260909plan13`）。禁止「统一 bump」或删 `?v=`。
- **不加构建链：** 禁止引入 npm / Vite / webpack / esbuild。
- **一次一页或一域：** 禁止大爆炸拆分。
- **脚本顺序冻住为：** `[vendor?] → [table-pager?] → shared/* → 域文件 → nav.js`。与现状「域脚本/inline 在 nav **之前**」一致。**不要把 nav 改到最前。**
- **与 P3 解耦：** 不改 Python status 写路径、不改 services 算法。
- **Python：** `.venv/bin/python` / `.venv/bin/pytest`。
- **工作区：** 不主动 `git commit`。
- **未挂路由页不碰：** `/backtests/new` 用 `backtest_workbench_formal.html`（`quantlab/api/routes/pages.py`）。`backtest_workbench.html`（minified）与 `backtest_workbench_runtime.html` **不在正式路由里，P2 不迁不删。**

**基线（2026-09-09，`main` @ P0+P1）：**

| 项 | 值 |
|---|---|
| `app.js` | 3976 行 |
| 六页 inline | workbench_formal 1250 + plan 525 + rules 247 + run_record 546 + archive 236 + settings 97 = **2901** |
| 已有 | `nav.js` 210、`table-pager.js` 85、`app.css`、`vendor/lightweight-charts...` |
| NoStore 白名单 | 仅 `app.js` / `app.css` / `nav.js`（`table-pager.js` 已漏） |
| 六页是否加载 app.js | **否**（仅 nav ± pager/charts） |
| 典型 app.js 页顺序 | `[vendor?] → [pager?] → app.js → nav.js` |

---

## v1 审查结论（本版已吸收）

v1 锁定方案 A、无 bundler、先 inline 后 app.js、workbench 允许单文件仍大——这些保留。

必须修正：

1. **`test_web_shell.py` 在 HTML 里搜 JS 函数。** 外提后会红。允许改测试搜路径为 `quantlab/web/assets` rglob（排除 `vendor/`），**不改**被搜的标识符字符串、不改 DOM 断言。典型：
   - `QuantLabPager.mount`、`function setSort(key)`、`table.className = "archive-grid plan-grid"`（`backtest_plan.html`）
   - `function clampTestPeriodToLookback`、`function rememberDraft`、`function bindWorkbenchPanelDrag`（`backtest_workbench_formal.html`）
   - `walkSnap === "lookback"`、`window.location.assign(y.redirect_url)`（`backtest_run_record.html`）
2. **缓存 query 冻住。** 例如 `assert "app.css?v=20260909plan13" in html`。新 script src 可沿用该页现有 `?v=`，不要全局改。
3. **NoStore 必须先于任何新 JS 文件（Task 0）。** 覆盖所有**自有** `.js`（含 `table-pager.js` 与未来 `shared/` / `backtest/`）和 `app.css`。**排除 `vendor/`**（lightweight-charts 体积大）。
4. **不要 nav-first。** v1 写 `nav → shared → 域` 与现状相反。v2 链见上。`nav.js` 的 `window.qlTheme` IIFE 在 parse 时执行；域脚本的 `DOMContentLoaded` 时 theme 已存在（现码如此）。
5. **禁止 `type="module"`。** 会改变加载/作用域，打断 `function` 全局与 `QuantLabPager`。
6. **Task 0 必须补 `/assets/*` 的 Cache-Control 断言。** `test_web_shell.py` 目前不测静态头。
7. **`loadFactorLibrary*` 可删**（`app.js` ~L1824–2168 一带）：`loadFactorLibraryPage` 无调用；HTML 无 `#factor-library-*`；browser 测试断言旧 DOM count==0。删前全仓库 `rg`。
8. workbench 外提后单文件允许 ~1200 行；**P2.1 再拆，本计划不二次切 workbench。**

测试搜路径助手（各 Task 改 `test_web_shell.py` 时复用，不要每个测试复制不同逻辑）：

```python
from pathlib import Path

def _first_party_js() -> str:
    root = Path("quantlab/web/assets")
    chunks = []
    for path in sorted(root.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "".join(chunks)
```

DOM id / 可见文案 / `?v=` **继续对 HTML `read_text()`**。只有「函数名、JS 语句」从 `in html` 改为 `in _first_party_js()`（或 `html + _first_party_js()` 若暂时两处都可能有）。

---

## 方案选择（仍锁定 A）

| 方案 | 做法 | 结论 |
|---|---|---|
| **A. 静态多 script + shared（采用）** | 无构建链 | 与 nav.js 一致 |
| B. Vite/ESM bundler | 引入 npm | 否决 |
| C. 只拆 app.js 不动 inline | 优先级反了（回测页根本不加载 app.js） | 否决 |

---

## 目标结构

```text
quantlab/web/assets/
├── shared/
│   ├── dom.js      # window.ql$ / window.qlQ
│   ├── fetch.js    # window.apiFetch
│   └── theme.js    # 从 app.js 顶迁出的 qlChartColors / qlCss（可薄封装 qlTheme）
├── backtest/
│   ├── workbench.js
│   ├── plan.js
│   ├── rules.js
│   ├── run-record.js
│   └── archive.js
├── data/
│   ├── overview.js
│   ├── datasets.js
│   └── kline.js
├── factors/
│   ├── catalog.js
│   ├── detail.js
│   ├── manual.js
│   ├── mining.js
│   └── jobs.js
├── models/
│   ├── catalog.js
│   ├── version.js
│   └── run.js
├── research/
│   └── runs.js
├── settings.js
├── bootstrap.js     # 原 app.js 的 pathname 分派
├── nav.js           # 不改行为
├── table-pager.js   # 不改行为
├── app.css
└── vendor/          # 不改；NoStore 排除
```

加载顺序（每页按需省略没有的段）：

```text
[vendor/lightweight-charts?] → [table-pager.js?] → shared/dom.js → shared/fetch.js → shared/theme.js → <域文件> → [bootstrap.js] → nav.js
```

`app.js` 在 Task 4 结束时删除，或压到 ≤50 行的兼容转发给 bootstrap（优先删除，并改所有仍引用它的 HTML）。

---

## 回归映射

| 目标 | 最低命令 |
|---|---|
| static no-store | `.venv/bin/pytest tests/quantlab/test_web_shell.py -q` |
| settings | `.venv/bin/pytest tests/quantlab/test_settings_page.py tests/quantlab/test_web_shell.py -q` |
| archive | `.venv/bin/pytest tests/quantlab/browser/test_result_archive_browser.py tests/quantlab/test_web_shell.py -q` |
| rules | `.venv/bin/pytest tests/quantlab/browser/test_rule_strategy_browser.py tests/quantlab/test_web_shell.py -q` |
| workbench | `.venv/bin/pytest tests/quantlab/browser/test_backtest_draft_browser.py tests/quantlab/browser/test_pretrade_filters_browser.py tests/quantlab/test_web_shell.py -q` |
| data/kline/overview | `.venv/bin/pytest tests/quantlab/browser/test_data.py tests/quantlab/browser/test_kline.py tests/quantlab/browser/test_overview.py -q` |
| factors | `.venv/bin/pytest tests/quantlab/browser/test_factors.py tests/quantlab/browser/test_factor_detail_browser.py tests/quantlab/browser/test_factor_manual_browser.py tests/quantlab/browser/test_factor_mining_browser.py tests/quantlab/browser/test_factor_jobs_browser.py tests/quantlab/browser/test_factor_library_browser.py tests/quantlab/browser/test_factor_library_browser_page.py -q` |
| 收尾 | `.venv/bin/pytest tests/quantlab/browser -q` 以及 `test_web_shell.py` |

plan / run-record：现无专用 browser 文件。用 `test_web_shell.py` + `test_run_record.py`（页面 no-store 头）+ `test_backtest_plan.py`（若打页面）。**不要**为了本计划新写大型 Playwright，除非现测不够证明 200 与关键节点；若补 smoke，只断言 200 + 关键 `id`。

---

## Chunk 1: 缓存 + shared

### Task 0: no-store 策略

**Files:**

- Modify: `quantlab/api/static.py`
- Modify: `tests/quantlab/test_web_shell.py`（新增 cache 断言）

- [ ] **Step 1: 记录基线**

```bash
wc -l quantlab/web/assets/app.js quantlab/web/assets/nav.js quantlab/web/assets/table-pager.js \
  quantlab/web/pages/backtest_workbench_formal.html quantlab/web/pages/backtest_plan.html \
  quantlab/web/pages/backtest_rules.html quantlab/web/pages/backtest_run_record.html \
  quantlab/web/pages/result_archive.html quantlab/web/pages/settings.html
```

Expected: app.js 3976；六页行数与基线表同量级。

- [ ] **Step 2: 扩展 no-store**

`get_response` 里 `path` 相对 `web/assets`（vendor 文件为 `vendor/...`）：

```python
asset = path.replace("\\", "/")
is_vendor = asset == "vendor" or asset.startswith("vendor/")
name = asset.rsplit("/", 1)[-1]
if (not is_vendor) and (name.endswith(".js") or name == "app.css"):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    if "etag" in response.headers:
        del response.headers["etag"]
```

保留 `is_not_modified` 恒 `False`。

- [ ] **Step 3: 补测试**

```python
from fastapi.testclient import TestClient
from quantlab.api.app import create_app

def test_first_party_assets_are_no_store_vendor_is_not() -> None:
    client = TestClient(create_app())
    for url in ("/assets/nav.js", "/assets/app.js", "/assets/table-pager.js", "/assets/app.css"):
        header = client.get(url).headers.get("cache-control", "").lower()
        assert "no-store" in header, url
    vendor = client.get("/assets/vendor/lightweight-charts.standalone.production.js")
    assert vendor.status_code == 200
    assert "no-store" not in vendor.headers.get("cache-control", "").lower()
```

- [ ] **Step 4: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_web_shell.py -q
```

Expected: PASS。

---

### Task 1: shared 公共层

**Files:**

- Create: `quantlab/web/assets/shared/dom.js`
- Create: `quantlab/web/assets/shared/fetch.js`
- Create: `quantlab/web/assets/shared/theme.js`
- Modify: `quantlab/web/pages/settings.html`（先只加 script src，inline 暂可仍用本地 `$`；或下一步 Task 2 一起换。本 Task **试点接入**：settings 在 shared 之后仍能跑）

**Interfaces（IIFE 挂 window，经典 script）：**

```javascript
// shared/dom.js
(function (w) {
  w.ql$ = function (id) { return document.getElementById(id); };
  w.qlQ = function (sel, root) { return (root || document).querySelector(sel); };
})(window);
```

```javascript
// shared/fetch.js
(function (w) {
  w.apiFetch = async function (url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      const error = new Error(body.message || body.error_code || response.statusText);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  };
})(window);
```

`theme.js`：把 `app.js` L1–14 的 `qlChartColors` / `qlCss` **原样**挂到 `window`（内部仍走 `window.qlTheme`，nav 尚未加载时走 fallback——与现 app.js 顶函数一致）。**本 Task 不要从 app.js 删除**，避免未改加载顺序的页面缺符号。Task 4 迁 data/factors 时再删 app.js 副本。

- [ ] **Step 1–3:** 创建三个文件，无 `type=module`。
- [ ] **Step 4:** `settings.html` 在现有 inline **之前**插入：

```html
<script src="/assets/shared/dom.js"></script>
<script src="/assets/shared/fetch.js"></script>
```

保留原 `nav.js?v=20260907a` 在最后。不要改 css `?v=`。

- [ ] **Step 5:**

```bash
.venv/bin/pytest tests/quantlab/test_settings_page.py tests/quantlab/test_web_shell.py -q
```

Expected: PASS。新 js 自动吃到 Task 0 no-store。

---

### Task 2: 外提 `settings.js`

**Files:**

- Create: `quantlab/web/assets/settings.js`
- Modify: `quantlab/web/pages/settings.html`
- Modify: `tests/quantlab/test_web_shell.py`（若有 JS 字符串在 settings HTML 上；当前设置页测偏 DOM）

- [ ] **Step 1:** 把 settings.html 两块 `<script>` 业务（约 L95–191）剪到 `settings.js`。改用 `ql$` / `apiFetch` 处仅当能保持行为；若现码 `fetch` + 手工 `r.ok` 分支不同，**允许原样保留 fetch**，不要为了换 `apiFetch` 改变错误展示。
- [ ] **Step 2:** HTML 引用：`shared/dom.js` → `shared/fetch.js` → `settings.js` → `nav.js?v=20260907a`。inline 业务行 **&lt;20**（最好 0）。
- [ ] **Step 3:** 回归 `test_settings_page.py` + `test_web_shell.py`。

---

## Chunk 2: 回测域 inline

### Task 3: 外提回测域

**Files:** Create `quantlab/web/assets/backtest/{archive,rules,run-record,plan,workbench}.js`；改对应 HTML。

**顺序（锁定）：** archive → rules → run-record → plan → workbench。

每页步骤相同：

1. 剪切 inline 到域文件（逻辑原样，含该页自己的 `$` / `q` 辅助函数；可改用 `ql$` 但不要改 DOM id）。
2. script 链：已有 vendor/pager 保持相对位置 → shared（若该页用了 ql$/apiFetch，否则可暂不引 shared）→ 域文件 → **现有** `nav.js?v=...`。
3. 把 `test_web_shell.py` 里对该 HTML 的 **JS 语句断言** 改为 `_first_party_js()`；DOM/`?v=`/`table-pager.js` 字符串仍对 HTML。
4. 跑映射测试。
5. 该页 inline 业务 ≈ 0。

- [ ] **archive**（`result_archive.html` → `backtest/archive.js`）+ `test_result_archive_browser.py` + `test_web_shell.py`
- [ ] **rules**（`backtest_rules.html` → `backtest/rules.js`）+ `test_rule_strategy_browser.py`
- [ ] **run-record**（`backtest_run_record.html` → `backtest/run-record.js`）+ `test_run_record.py` + `test_web_shell.py`（`walkSnap`、`redirect_url`）
- [ ] **plan**（`backtest_plan.html` → `backtest/plan.js`）+ `test_web_shell.py`（`QuantLabPager.mount`、`setSort`、`plan-grid`）+ `test_backtest_plan.py`。**保留** `app.css?v=20260909plan13`。
- [ ] **workbench**（`backtest_workbench_formal.html` → `backtest/workbench.js`）+ draft/pretrade browser + `test_web_shell.py`（`clampTestPeriodToLookback`、`rememberDraft`、panel drag 等）
- [ ] **六页 inline 清零：** `rg -n "<script>" quantlab/web/pages/backtest_*.html quantlab/web/pages/result_archive.html quantlab/web/pages/settings.html` 只应剩 `script src=`，无大块内联（settings 已在 Task 2 清）。

workbench.js 允许 ~1200 行。

---

## Chunk 3: 拆 app.js

### Task 4: 拆分 `app.js`

**顺序（锁定）：** data（overview / datasets / kline）→ factors → 删死代码 → models + research → bootstrap。

pathname 分派现位于 `app.js` L3929–3975。迁走后只留 `bootstrap.js`：

- `/` → overview
- `/data` → datasets
- `/kline` → kline
- `/factors`、`/data/factors` → catalog
- `/factors/new` → manual
- `/research/factor-mining` → mining
- `/research/factor-jobs` → jobs
- `/factors/{id}` → detail
- `/models` → models catalog
- `/models/{id}/versions/{v}` → version
- `/models/runs/{id}` → run
- `/research/runs/{id}` → research/runs.js

**不要**给 `/settings`、`/backtests/*` 挂 bootstrap（那些页不加载 app.js）。

- [ ] **迁 data 域**到 `data/{overview,datasets,kline}.js`。改 `index.html` / `data.html` / `kline.html` 的 script：pager/vendor 原样 → shared/theme（图表页需要）→ 域文件 → **去掉 app.js 或暂时仍加载剩余 app.js**。在过渡期允许仍加载缩减后的 app.js，直到本 Task 末删除。
- [ ] 跑 overview / data / kline browser。
- [ ] **迁 factors**（catalog / detail / manual / mining / jobs）。`test_web_shell.py` 里 `Path(".../app.js").read_text()` 对因子函数的断言改为 `_first_party_js()`。
- [ ] **`rg` 后删除死代码** `factorLibrary*` / `loadFactorLibrary*` / `loadFactorLibraryPage` / `loadFactorVersionDetailPage`（约 L1824–2168）。

```bash
rg -n "loadFactorLibrary|factor-library-table|renderFactorLibrary" quantlab tests
```

Expected: 删除后无生产引用；browser 负例仍 PASS。

- [ ] **迁 models + research**。`test_web_shell.py` 的 `function loadModelVersionPage` 等改为 `_first_party_js()`；HTML 仍断言 `<script src="/assets/app.js` 的，改为 bootstrap 或对应域文件 src（**不要**断言错误文件名）。
- [ ] **bootstrap 接管 pathname**；删除 `app.js` 或 ≤50 行。所有仍引用 `/assets/app.js` 的 HTML 改为 bootstrap + 所需域文件。
- [ ] 确认无页面同时加载「旧 app.js 全量」与新域文件导致双绑定。

```bash
rg -n 'src="/assets/app.js' quantlab/web/pages
```

Expected: 0（或仅转发给 bootstrap 的兼容页，且 app.js ≤50 行）。

- [ ] `.venv/bin/pytest tests/quantlab/test_web_shell.py tests/quantlab/browser -q` 对本 Task 涉及的子集；全部 browser 放到 Task 5。

---

### Task 5: 收尾

- [ ] 确认无巨型 `app.js`（删除或 ≤50）、六页无大块 inline。
- [ ] `wc -l` 域文件；workbench 允许大。
- [ ] 全量 browser：

```bash
.venv/bin/pytest tests/quantlab/browser -q
```

Expected: PASS。失败先查 `X is not defined` 与 script 顺序，再查 no-store / 缓存。

- [ ] 再跑：

```bash
.venv/bin/pytest tests/quantlab/test_web_shell.py tests/quantlab/test_settings_page.py -q
```

---

## 平稳过渡

1. Task 0 不过不要加新 JS 文件（可先写测试）。
2. 先外提可跑副本，再视需要换 `ql$` / `apiFetch`；换助手时不要改错误文案。
3. 全局符号用 `ql$` / `apiFetch` 前缀，避免与页内 `$` 混用而不清。
4. 空白页先查 script 顺序与 404 路径（`/assets/shared/...`）。
5. Playwright 失败先看控制台 `X is not defined`。
6. `table-pager` 双轨：archive/plan 直用 `QuantLabPager`；data/kline 经 `bindTablePager`。抽 shared 时勿弄断。

## 风险

| 风险 | 缓解 |
|---|---|
| 脚本顺序 | 固定「域 → nav」；browser |
| 新文件被缓存 | Task 0 自有 js no-store；排除 vendor |
| `test_web_shell` 搜 HTML 里的函数 | rglob `_first_party_js()` |
| `?v=` 被「优化」 | 禁止改现有 query |
| workbench 仍大 | 允许；P2.1 |
| 误迁未挂路由的 workbench.html | 明确不碰 |
| 删 `loadFactorLibrary*` 误伤 catalog | 先 rg；活跃路径是 `loadFactors` / `renderFactorCatalog` |

## 工作量

约 **4–7 人日**（Task 3–4 为主）。

## 非目标

不上 React/bundler；不改视觉与后端 API；不做 P3；不二次拆 workbench.js；不删除未挂路由的遗留 HTML。
