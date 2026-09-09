# 拆分 app.py 按域路由 Implementation Plan（P1 第二版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **取代：** 桌面稿 `/Users/weiminzhu/Desktop/2026-09-09-split-app-routes.md`（v1）。以本文件为准。
>
> **Commit policy:** 用户规则禁止主动 commit。各任务不要执行 `git commit`，除非用户当场明确要求。

**Goal:** 把 `quantlab/api/app.py`（1670 行、156 条 `@app.(get|post|put|patch|delete)` 装饰器 + 已拆的 `GET /api/overview`）拆成按 URL 域划分的 `APIRouter` 模块，使 `create_app` 只负责依赖装配、`app.state`、异常处理、`include_router` 与 `/assets` mount；**URL、HTTP 方法、状态码、JSON 契约、页面路径全部冻结**。

**Architecture:** 沿用已落地的 `overview` 模式。当前除 overview 外，**全部业务路由都是 `create_app` 内部闭包**，捕获 `catalog` / `model_training` 等局部变量。迁出不是「剪切装饰器」那么简单：必须先把闭包用到的对象全部挂上 `app.state`，再把 handler 改成 `request.app.state.*`。共享错误助手迁到 `quantlab/api/errors.py`（**保持原函数名**）。按「小域 → 中域 → 大域」一次迁一簇。

**Tech Stack:** FastAPI `APIRouter`、现有 `TestClient`、`.venv` Python 3.12。可选 Playwright（非每任务必跑）。

## Global Constraints

- **行为冻结：** 不得改路径字符串、query/body 字段名、成功/错误 `error_code` 形状、装饰器 `status_code`（含 `201`/`202`/`204`）、`Response` 头。
- **依赖访问：** 继续用 `request.app.state.*`；本计划不引入 Depends / 新 DI。
- **禁止顺手重构：** 不改 services/repositories 逻辑；不合并/拆分业务 API；不改 HTML/JS（测试若按文件路径搜 `app.py` 字符串，只改测试的搜路径，不改前端）。
- **顺序冻结，禁止「优化」：** 每个 router **原样保持该域在 `app.py` 里的相对注册顺序**。不要按字母排，也不要「把静态路径提前」——v1 对 models 的重排是错误的（见下方审查）。Starlette **先注册先匹配**。
- **测试入口不变：** 测试仍 `from quantlab.api.app import create_app`。
- **Python：** 一律 `.venv/bin/python` / `.venv/bin/pytest`（pyc magic 是 3.12；系统 `python3` 可能是 3.14）。
- **工作区：** 不主动 `git commit`。
- **`include_router` 不加 `prefix=`：** 装饰器已写绝对路径（与 `overview.py` 一致）。加 `prefix="/api"` 会变成 `/api/api/...`。

**基线（2026-09-09，`main` @ 源码还原后）：**

| 项 | 值 |
|---|---|
| `app.py` | 1670 行 |
| `@app.(get\|post\|…)` | 156 |
| 另已拆 | `routes/overview.py` → `GET /api/overview` |
| `create_app` | L152 |
| 已挂 `app.state` | overview / kline / factor_* / strategy_center / backtest_* / result_archive / research_run_repository / settings_service / tushare_download |
| **未挂、但闭包在用** | `catalog`、`artifacts`、`model_training`、`resolved_settings`、`resolved_database` |
| 嵌套错误助手 | `_strategy_error`（约 L950）、`_plan_error`（约 L1126） |

---

## v1 审查结论（本版已吸收）

v1 锁定方案 A、一次一域、契约冻结、pages 用 `parents[2]`、`backtest-drafts` 归 `backtests.py`——这些保留。

必须修正：

1. **真正的工作是闭包 → `app.state`。** 除 overview 外没有模块级 handler。漏挂 state 会在迁出后 `NameError`。
2. **缺 5 个 state：** `dataset_catalog`、`artifact_repository`、`model_training_service`、`settings`（`Settings` 实例）、`database`。`run_isolated(resolved_settings, …)` 与 `apply_suspend_d` 都要用后两个。
3. **不要改名 `_error_payload` / `_raise_*` / `_json_body`。** 测试不 import 私有名；改名只增加替换面。v1 骨架与现码一致，原样剪切即可。把 `_strategy_error`、`_plan_error` 一并放进 `errors.py`。
4. **models 不要重排。** 现码是 `kinds/design/catalog` → `{entity_id}/versions/...` → `runs...`。不存在 `GET /api/models/{entity_id}` 这种与 `/runs` 同形的叶子路径（`/runs` 3 段，`{id}/versions/{v}` 5 段），「把 runs 提前」不是修复而是无关重排。**照现序剪切。**
5. **真正会同形抢匹配的路径（保持现序即可）：**
   - `/api/datasets/raw`（及 quality-* / rescan / scan-audits）**先于** `/api/datasets/{entity_id}`
   - `/api/factor-calculations/latest/{factor_id}` **先于** `/api/factor-calculations/{calculation_id}`
   - `/api/backtests/validate`、`preview`、`drafts`、`runs`、`runs.csv`、`runs/{run_id}` **先于** `/api/backtests/{run_id}`
   - 页面：`/factors/new`、`/factors/new/manual` **先于** `/factors/{factor_id}`（`test_factor_manual.py` 打 `GET /factors/new`）
6. **漏迁点：** `POST /api/factor-mining/runs/{run_id}/candidates/{dedupe_key}/draft` 写在 strategies **之后**（约 L1111），必须进 `factor_mining.py`，不要留在 app 或误放 strategies。
7. **两套草稿 API 不要混语义：**
   - `/api/backtest-drafts`（POST/GET）→ `factor_detail_service`（因子详情生成的回测草稿）
   - `/api/backtests/drafts` → `backtest_workbench`  
   文件都归 `backtests.py`（按 URL 域），但 handler 仍走各自 service。
8. **有测试在读 `app.py` 源码字符串**，迁路由后会红（允许改测试搜路径，这不是改 `create_app` import）：
   - `tests/quantlab/test_canonical_market.py`：`'/api/raw/apply/suspend_d' in app`
   - `tests/quantlab/test_tushare_download.py`：`'/api/raw/download/suspend_d' in app`
9. **清单脚本用 `.venv/bin/python`。** v1 的 `python3` 在本机可能不是 3.12。Task 0 除 regex 外再加 **runtime `app.routes` 顺序快照**（sorted 清单只能做集合 diff，不能验顺序）。

---

## 方案选择（仍锁定 A）

| 方案 | 做法 | 结论 |
|---|---|---|
| **A. 按 URL 域拆 APIRouter + app.state（采用）** | 先补全 state，再机械迁 handler | 与 overview 一致 |
| B. Depends 注入 | 改动面大 | 否决 |
| C. 一次搬完全部 | 难回滚 | 否决 |

---

## 目标文件结构

```text
quantlab/api/
├── app.py                 # 装配 + exception handlers + mount + include_router
├── errors.py              # 现有 _error_payload / _raise_* / _json_body / _strategy_error / _plan_error
├── static.py              # NoStoreStaticFiles
└── routes/
    ├── __init__.py        # register_routers(app)
    ├── overview.py        # 已存在，不改行为
    ├── health.py
    ├── settings.py
    ├── datasets.py        # /api/datasets* /api/raw* /api/artifacts*
    ├── kline.py           # 含 _kline_params
    ├── factor_data.py     # /api/factor-data* /api/factor-calculations* /api/factor-packs*
    ├── factors.py         # 前半 + 后半全部 /api/factors* /api/factor-drafts*
    ├── factor_mining.py   # /api/factor-mining* /api/factor-jobs*（含 strategies 后那条 draft）
    ├── models.py
    ├── strategies.py
    ├── backtests.py       # backtest-drafts + backtest-plans + backtests*
    ├── research_runs.py
    └── pages.py
```

---

## `app.state` 名称表（全计划锁定）

迁出前（Task 1）就必须赋值。handler 用右列。

| `create_app` 局部变量 | `app.state` 名 |
|---|---|
| `overview` | `overview_service`（已有） |
| `kline` | `kline_service`（已有） |
| `factor_data` | `factor_data_service`（已有） |
| `factors` | `factor_repository`（已有） |
| `factor_detail_service` | `factor_detail_service`（已有） |
| `factor_manual_service` | `factor_manual_service`（已有） |
| `factor_mining_service` | `factor_mining_service`（已有） |
| `factor_calculation` | `factor_calculation_service`（已有） |
| `factor_pack_service` | `factor_pack_service`（已有） |
| `strategy_center` | `strategy_center_service`（已有） |
| `backtest_workbench` | `backtest_workbench_service`（已有） |
| `backtest_job` | `backtest_job_service`（已有） |
| `backtest_plan` | `backtest_plan_service`（已有） |
| `result_archive` | `result_archive_service`（已有） |
| `research_runs` | `research_run_repository`（已有） |
| `settings_service` | `settings_service`（已有） |
| `tushare_download` | `tushare_download_service`（已有） |
| `catalog` | **`dataset_catalog`（新增）** |
| `artifacts` | **`artifact_repository`（新增）** |
| `model_training` | **`model_training_service`（新增）** |
| `resolved_settings` | **`settings`（新增，类型 `Settings`）** |
| `resolved_database` | **`database`（新增）** |

模块级可继续 import 的纯函数（不是闭包）：`apply_suspend_d`、`run_isolated`、`stop_run`、`DEFAULT_KLINE_VERSION`、`FACTOR_MAX_ROWS`。

---

## 闭包改写模式（所有迁出 handler 共用）

现码（嵌在 `create_app` 里）：

```python
@app.get("/api/models/kinds")
def model_kinds() -> dict[str, object]:
    return model_training.list_kinds()
```

迁出后：

```python
@router.get("/api/models/kinds")
def model_kinds(request: Request) -> dict[str, object]:
    return request.app.state.model_training_service.list_kinds()
```

规则：

- `@app.` → `@router.`，**path / methods / status_code / 参数默认值原样**。
- 原先无 `Request` 的补 `request: Request`（放在其它参数前或按 FastAPI 习惯；不要删 `Query`/`Body`/`Response`）。
- 局部服务名换成上表。已有 `Request` 的（如 settings POST）只改服务访问，不要改 body 解析方式：有的用 `_json_body`，有的用 `await request.json()`，**保持原样**。
- 同一 path 只注册一次：从 `app.py` **剪走**，不要复制后留一份。

---

## 域 → 回归测试映射

命令均在仓库根：

| 域文件 | 最低回归 |
|---|---|
| health / settings | `.venv/bin/pytest tests/quantlab/test_api_health.py tests/quantlab/test_settings_page.py tests/quantlab/test_api_errors.py -q` |
| datasets / raw / artifacts | `.venv/bin/pytest tests/quantlab/test_api_datasets.py tests/quantlab/test_tushare_download.py tests/quantlab/test_canonical_market.py tests/quantlab/test_artifacts.py -q` |
| kline | `.venv/bin/pytest tests/quantlab/test_api_kline.py tests/quantlab/test_kline_page.py tests/quantlab/test_kline_contract.py -q` |
| factor_data / packs / calculations | `.venv/bin/pytest tests/quantlab/test_canonical_factor_pack.py tests/quantlab/test_factor_calculation.py tests/quantlab/test_pack_factor_materialize.py -q` |
| factors / drafts | `.venv/bin/pytest tests/quantlab/test_factor_library.py tests/quantlab/test_factor_detail.py tests/quantlab/test_factor_manual.py tests/quantlab/test_factor_library_page.py -q` |
| factor_mining / jobs | `.venv/bin/pytest tests/quantlab/test_factor_mining_api.py tests/quantlab/test_factor_auto_mine.py -q` |
| models / strategies | `.venv/bin/pytest tests/quantlab/test_strategy_center.py tests/quantlab/test_model_training.py -q` |
| backtests / plans | `.venv/bin/pytest tests/quantlab/test_backtest_workbench.py tests/quantlab/test_backtest_plan.py tests/quantlab/test_run_record.py tests/quantlab/test_result_archive.py tests/quantlab/test_rule_backtest.py -q` |
| research_runs | `.venv/bin/pytest tests/quantlab/test_research_runs.py -q` |
| pages | `.venv/bin/pytest tests/quantlab/test_web_shell.py tests/quantlab/test_overview_page.py tests/quantlab/test_kline_page.py tests/quantlab/test_factor_manual.py tests/quantlab/test_factor_detail.py tests/quantlab/test_canonical_factor_pack.py tests/quantlab/test_strategy_center.py tests/quantlab/test_backtest_workbench.py tests/quantlab/test_backtest_plan.py tests/quantlab/test_run_record.py -q` |
| 收尾全量 | `.venv/bin/pytest tests/quantlab -q --ignore=tests/quantlab/browser` |

可选：`.venv/bin/pytest tests/quantlab/browser -q`。

---

## Chunk 1: 快照与基础设施

### Task 0: 路由清单快照（防漏迁）

**Files:**

- Create: `.tmp_route_split/routes_before.txt`（集合，sorted）
- Create: `.tmp_route_split/routes_before_ordered.txt`（runtime 注册顺序）
- 不改生产代码

**Interfaces:**

- Produces: 迁移前 `METHOD path` 集合 + 顺序清单

- [ ] **Step 1: 生成集合清单（regex，与 v1 兼容）**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import re
rows = []
for src in Path("quantlab/api").rglob("*.py"):
    text = src.read_text()
    for m in re.finditer(r'@(?:app|router)\.(get|post|put|patch|delete)\("([^"]+)"', text):
        rows.append(f"{m.group(1).upper()} {m.group(2)}")
Path(".tmp_route_split").mkdir(exist_ok=True)
Path(".tmp_route_split/routes_before.txt").write_text("\n".join(sorted(rows)) + "\n")
print(len(rows))
PY
```

Expected: 打印 **157**（156 + overview）。

- [ ] **Step 2: 生成 runtime 顺序快照**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from quantlab.api.app import create_app

app = create_app()
rows = []
for route in app.routes:
    path = getattr(route, "path", None)
    methods = sorted(getattr(route, "methods", None) or [])
    if path is None or not methods:
        continue
    if path.rstrip("/") == "/assets" or path.startswith("/assets"):
        continue
    for method in methods:
        if method in {"HEAD", "OPTIONS"}:
            continue
        rows.append(f"{method} {path}")
Path(".tmp_route_split/routes_before_ordered.txt").write_text("\n".join(rows) + "\n")
print(len(rows))
PY
```

Expected: 非空；其中能看到 `GET /api/datasets/raw` 出现在 `GET /api/datasets/{entity_id}` 之前，`GET /factors/new` 在 `GET /factors/{factor_id}` 之前。

- [ ] **Step 3: 人工记下同形冲突行号（迁移时整块移动）**

在 `app.py` 确认（不要改）：

1. `GET /api/datasets/raw` 在 `GET /api/datasets/{entity_id}` 之前
2. `GET /api/factor-calculations/latest/{factor_id}` 在 `GET /api/factor-calculations/{calculation_id}` 之前
3. `POST /api/backtests/validate` … `GET /api/backtests/runs.csv` … 在 `GET /api/backtests/{run_id}` 之前
4. `GET /factors/new` 在 `GET /factors/{factor_id}` 之前

---

### Task 1: `errors.py`、`static.py`、补全 `app.state`

**Files:**

- Create: `quantlab/api/errors.py`
- Create: `quantlab/api/static.py`
- Modify: `quantlab/api/app.py` — import 替换；补 5 个 state；删除本地重复定义
- Test: `tests/quantlab/test_api_errors.py`、`tests/quantlab/test_api_health.py`

**Interfaces:**

- Produces（**保持现名**，不要去下划线）:
  - `_error_payload(...)`
  - `_raise_kline_error` / `_raise_factor_error` / `_raise_factor_library_error` / `_raise_research_error`
  - `_json_body`
  - `_strategy_error(error: ValueError, status_code: int = 400) -> None`
  - `_plan_error(error: Exception, plan_id: str | None = None) -> None`
  - `class NoStoreStaticFiles`
- `_kline_params` 仍留 `app.py`，到 Task 3 随 kline 走。

- [ ] **Step 1: 新建 `quantlab/api/errors.py`**

从 `app.py` **原样复制** `_error_payload`、`_raise_kline_error`、`_raise_factor_error`、`_raise_factor_library_error`、`_json_body`、`_raise_research_error`。再把 `create_app` 内部的 `_strategy_error`、`_plan_error` 提升为模块函数（正文逐字段与现码一致，含 `BACKTEST_PLAN_BUSY` 的 409）。

`app.py` 里原嵌套函数删掉后，模型/计划 handler 改为调用模块级同名函数（本 Task 就可以改调用，或留到迁域时再改；推荐本 Task 改完，避免嵌套函数随闭包消失）。

- [ ] **Step 2: 新建 `quantlab/api/static.py`**

把 `NoStoreStaticFiles` 原样迁入（含对 `app.js` / `app.css` / `nav.js` 的 `no-store`）。`app.py` 顶部那个「class 插在 import 中间」的怪序一并清掉。

- [ ] **Step 3: 补全 `app.state`（handler 仍可继续用闭包）**

在现有赋值旁增加：

```python
    app.state.dataset_catalog = catalog
    app.state.artifact_repository = artifacts
    app.state.model_training_service = model_training
    app.state.settings = resolved_settings
    app.state.database = resolved_database
```

- [ ] **Step 4: `app.py` 改为 `from quantlab.api.errors import ...` 与 `from quantlab.api.static import NoStoreStaticFiles`**

异常 handler 继续留在 `create_app`，只是调用导入的 `_error_payload`。

- [ ] **Step 5: 回归**

```bash
.venv/bin/pytest tests/quantlab/test_api_errors.py tests/quantlab/test_api_health.py -q
```

Expected: PASS。

---

## Chunk 2: 按域剪切

每域完成后：`rg -n '@app\.(get|post|put|patch|delete)\("/api/<前缀>' quantlab/api/app.py` 对该前缀应为 0。

`create_app` 里 `include_router` 放在 `app.include_router(overview_router)` **之后**、仍存在的 `@app.` 路由 **之前或之后都可以**（跨 router 无同形冲突时）。推荐追加在 overview 之后，按下面总序逐渐加。

**最终 `register_routers` 顺序（Task 7 收口，中途可逐个 include）：**

1. `overview_router`（已有）
2. `health_router`
3. `settings_router`
4. `datasets_router`
5. `kline_router`
6. `factor_data_router`
7. `factors_router`
8. `factor_mining_router`
9. `models_router`
10. `strategies_router`
11. `backtests_router`
12. `research_runs_router`
13. `pages_router`

### Task 2: `health` + `settings`

**Files:**

- Create: `quantlab/api/routes/health.py`
- Create: `quantlab/api/routes/settings.py`
- Modify: `quantlab/api/app.py`
- Test: 映射表 health / settings

**Interfaces:**

- Consumes: `request.app.state.settings_service`
- Produces: `health.router`、`settings.router`

- [ ] **Step 1: `health.py`**

现实现就是两字段，不要删 `service`：

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "quantlab"}
```

- [ ] **Step 2: 剪切全部 `/api/settings*` 到 `settings.py`**

包含：`raw-root` GET/POST、`tushare-token` GET/POST、`GET/PUT /api/settings`、`reset`、`test-connection`、`scan`。闭包 `settings_service` → `request.app.state.settings_service`。`PUT /api/settings` 继续 `await request.json()`，不要改成 `_json_body`。

- [ ] **Step 3: include 并删除旧 handler**

```python
from quantlab.api.routes.health import router as health_router
from quantlab.api.routes.settings import router as settings_router

app.include_router(health_router)
app.include_router(settings_router)
```

- [ ] **Step 4: 回归**（映射表 health / settings）

Expected: PASS。

---

### Task 3: `datasets` + `kline`

**Files:**

- Create: `quantlab/api/routes/datasets.py`
- Create: `quantlab/api/routes/kline.py`
- Modify: `quantlab/api/app.py`
- Modify: `tests/quantlab/test_canonical_market.py` — 字符串断言改为搜 `quantlab/api/`（见 Step 4）
- Modify: `tests/quantlab/test_tushare_download.py` — 同上
- Test: 映射表 datasets / kline

**Interfaces:**

- Consumes: `dataset_catalog`、`tushare_download_service`、`artifact_repository`、`database`、`settings`、`kline_service`
- `_kline_params` 放到 `kline.py`（模块私有）

- [ ] **Step 1: 剪切 datasets/raw/artifacts，块内顺序冻结**

`datasets.py` 内顺序必须与现 `app.py` 一致：

1. `GET /api/datasets`
2. `POST /api/raw/download/index_basic`
3. `POST /api/raw/download/index_weight`
4. `POST /api/raw/download/suspend_d`
5. `POST /api/raw/apply/suspend_d`（`database.connect` + `settings.data_root` / `settings.raw_root` + `apply_suspend_d`）
6. `GET /api/raw/{interface_name}/files`
7. `GET /api/datasets/raw`
8. `GET /api/datasets/quality-alerts`
9. `GET /api/datasets/quality-summary`
10. `POST /api/datasets/rescan`
11. `GET /api/datasets/scan-audits`
12. `GET /api/datasets/{entity_id}`
13. `GET /api/datasets/{entity_id}/versions`
14. `GET /api/datasets/{entity_id}/versions/{version_id}`
15. `GET /api/artifacts/{artifact_id}/download`
16. `GET /api/artifacts/{artifact_id}`

- [ ] **Step 2: 剪切 kline 四条 + `_kline_params`**

`/api/kline/query`、`summary`、`quality`、`export.csv`。继续 `Query(DEFAULT_KLINE_VERSION)` 与 `_raise_kline_error`。

- [ ] **Step 3: include 并删除 `app.py` 对应代码**

- [ ] **Step 4: 改两处源码字符串测试**

`test_canonical_market.py` / `test_tushare_download.py` 不要再 `Path("quantlab/api/app.py").read_text()` 断言路径。改为例如：

```python
api_src = "".join(p.read_text(encoding="utf-8") for p in Path("quantlab/api").rglob("*.py"))
assert "/api/raw/apply/suspend_d" in api_src
```

（tushare 测试同理换成 `"/api/raw/download/suspend_d"`。）

- [ ] **Step 5: 回归**（映射表 datasets + kline）

Expected: PASS。若 `GET /api/datasets/raw` 变成 404/`entity not found`，是 `{entity_id}` 抢匹配，把 raw 段挪回 `{entity_id}` 之前。

---

### Task 4: `factor_data` + `factors` + `factor_mining`

**Files:**

- Create: `quantlab/api/routes/factor_data.py`
- Create: `quantlab/api/routes/factors.py`
- Create: `quantlab/api/routes/factor_mining.py`
- Modify: `quantlab/api/app.py`
- Test: 映射表三行

**切割（按路径前缀，含后半段）：**

| 文件 | 前缀 |
|---|---|
| `factor_data.py` | `/api/factor-data`、`/api/factor-calculations`、`/api/factor-packs` |
| `factors.py` | `/api/factors`、`/api/factor-drafts` |
| `factor_mining.py` | `/api/factor-mining`、`/api/factor-jobs` |

**`factors.py` 必须合并两段：** 约 L703–845 与约 L1397–1491。合并时 **先粘前段、再粘后段**，不要按 v1 建议重排成「先 create/import 再详情」。GET `{entity_id}/{version_id}` 与 POST `/import` 不同形，重排无益。

**`factor_data.py` 顺序冻结：** `GET /api/factor-calculations` 列表 → `GET .../latest/{factor_id}` → `GET .../{calculation_id}`。

**`factor_mining.py` 必须包含** 现位于 strategies 之后的：

- `POST /api/factor-mining/runs/{run_id}/candidates/{dedupe_key}/draft`

不要把它放进 `strategies.py`。

- [ ] **Step 1: 迁 `factor_data.py` 并回归**（映射表 factor_data 行）

- [ ] **Step 2: 迁 `factors.py`（两段一起）并回归**（factors 行）

- [ ] **Step 3: 迁 `factor_mining.py`（含 candidate draft）并回归**（mining 行）

Expected: PASS。404 先查 `latest` vs `{calculation_id}` 顺序。

---

### Task 5: `models` + `strategies`

**Files:**

- Create: `quantlab/api/routes/models.py`
- Create: `quantlab/api/routes/strategies.py`
- Modify: `quantlab/api/app.py`
- Test: 映射表 models / strategies

**Interfaces:**

- Consumes: `strategy_center_service`、`model_training_service`
- `_strategy_error` 已在 `errors.py`

- [ ] **Step 1: 迁 models，顺序与现码一致（不要把 runs 提前）**

1. `GET/POST /api/models`
2. `GET /api/models/kinds`
3. `POST /api/models/design`
4. `POST /api/models/catalog`
5. `GET /api/models/{entity_id}/versions/{version_id}`
6. `POST /api/models/{entity_id}/versions`
7. `POST /api/models/{entity_id}/versions/{version_id}/publish`
8. `GET /api/models/runs`
9. `GET /api/models/runs/{run_id}`
10. `POST /api/models/{entity_id}/versions/{version_id}/training-runs`
11. `POST /api/models/runs/{run_id}/status`
12. `POST /api/models/runs/{run_id}/artifacts`

`kinds` / `design` / `catalog` 走 `model_training_service`；列表/版本/runs 走 `strategy_center_service`（与现码一致）。

- [ ] **Step 2: 迁 strategies（含 `.../backtest-draft`）**

到 `POST /api/strategies/{entity_id}/versions/{version_id}/backtest-draft` 为止。**不要**带走后面的 factor-mining candidate draft。

- [ ] **Step 3: 回归**

Expected: PASS。

---

### Task 6: `backtests` + `research_runs`

**Files:**

- Create: `quantlab/api/routes/backtests.py`
- Create: `quantlab/api/routes/research_runs.py`
- Modify: `quantlab/api/app.py`
- Test: 映射表 backtests / research_runs

**Interfaces:**

- Consumes: `factor_detail_service`（`/api/backtest-drafts*`）、`backtest_plan_service`、`backtest_workbench_service`、`backtest_job_service`、`result_archive_service`、`settings`、`research_run_repository`
- `_plan_error` 已在 `errors.py`
- `run_isolated` / `stop_run` 模块级 import

- [ ] **Step 1: 迁 backtest 相关路径，顺序冻结**

推荐按 **现码出现顺序** 拼进一个 router（中间被挖走的 mining/models 不要留下空洞注释）：

1. `POST /api/backtest-drafts`（`factor_detail_service`，可改 `response.status_code = 201`）
2. `GET /api/backtest-drafts/{draft_id}`（`factor_detail_service`，404 走 `_raise_factor_library_error`）
3. 全部 `/api/backtest-plans...`（`_plan_error`）
4. `POST /api/backtests/validate`、`preview`
5. `POST /api/backtests`（201）
6. `POST /api/backtests/drafts`、`GET /api/backtests/drafts/{draft_id}`（workbench）
7. `GET /api/backtests/runs`
8. `GET /api/backtests/runs.csv`
9. `GET /api/backtests/runs/{run_id}`、`POST .../copy-config`、`DELETE ...`（204）
10. **最后：** `GET /api/backtests/{run_id}`、`POST .../execute`、`POST .../stop`、`GET .../status`

`execute`：`run_isolated(request.app.state.settings, request.app.state.backtest_job_service, run_id)`。

- [ ] **Step 2: 迁 `research_runs.py`**

`GET/POST /api/research-runs`、`GET /{run_id}`、`POST /{run_id}/copy-config`、`POST /{run_id}/status`。create 继续 `await request.json()`，不要改成 `_json_body`。

- [ ] **Step 3: 回归**

Expected: PASS。若 `GET /api/backtests/runs` 或 `runs.csv` 落到 `BACKTEST_NOT_FOUND`，是 `{run_id}` 抢了 `runs` / `runs.csv`。

---

## Chunk 3: 页面与验收

### Task 7: `pages` + 瘦身 `app.py` + 全量验收

**Files:**

- Create: `quantlab/api/routes/pages.py`
- Modify: `quantlab/api/app.py` — 目标 ≤350 行（理想 <300）
- Modify: `quantlab/api/routes/__init__.py` — `register_routers(app: FastAPI) -> None`
- Test: 全量 + 清单 diff

**Interfaces:**

- Produces: `register_routers(app)` 按上文 13 项顺序 `include_router`

- [ ] **Step 1: 迁全部页面路由到 `pages.py`，顺序与现码一致**

现序（不可把 `{factor_id}` 提前）：

1. `GET /`
2. `GET /data`
3. `GET /kline`
4. `GET /data/factors`、`GET /data/factors/{factor_id}`（307）
5. `GET /factors`（`factors.html` + no-store 头，原样）
6. `GET /models`
7. `GET /models/{model_id}/versions/{version_id}`
8. `GET /models/runs/{run_id}`
9. `GET /strategies`（307 → `/models`）
10. `GET /backtests/new`、`/plan`、`/rules`、`/runs`、`/runs/{run_id}`
11. `GET /strategies/{strategy_id}/versions/{version_id}`
12. `GET /factors/new`、`GET /factors/new/manual`
13. `GET /factors/{factor_id}`（同样 no-store 头）
14. `GET /factors/{factor_id}/versions/{version_id}` 与 `GET /factors/{factor_id}/{version_id}`（叠装饰器，307）
15. `GET /research/factors`、`/manual`、`/auto`（叠装饰器，307 分支原样）
16. `GET /research/factor-mining`、`/research/factor-jobs`、`/research/runs/{run_id}`
17. `GET /settings`

页面目录：

```python
_PAGES = Path(__file__).resolve().parents[2] / "web/pages"
```

`app.py` 的 `parents[1]` 不要照抄。

- [ ] **Step 2: 瘦身 `app.py`**

只保留：imports、`create_app` 里 Settings/DB/services/`app.state`、三个 exception handler、`mount("/assets", NoStoreStaticFiles(...))`、`register_routers(app)`、`return app`。

```bash
wc -l quantlab/api/app.py
rg -n '@app\.(get|post|put|patch|delete)\(' quantlab/api/app.py
```

Expected: 行数 ≤350；`@app.(get|post|…)` **无匹配**（exception handler 不是这种装饰器）。

- [ ] **Step 3: 生成迁移后清单并 diff**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import re
rows = []
for path in sorted(Path("quantlab/api").rglob("*.py")):
    text = path.read_text()
    for m in re.finditer(r'@(?:app|router)\.(get|post|put|patch|delete)\("([^"]+)"', text):
        rows.append(f"{m.group(1).upper()} {m.group(2)}")
Path(".tmp_route_split/routes_after.txt").write_text("\n".join(sorted(rows)) + "\n")
before = Path(".tmp_route_split/routes_before.txt").read_text().splitlines()
after = Path(".tmp_route_split/routes_after.txt").read_text().splitlines()
print("missing", sorted(set(before) - set(after)))
print("extra", sorted(set(after) - set(before)))
print("count", len(before), len(after))
PY
```

Expected: `missing []`、`extra []`、count 两边均为 157。

再用 runtime 顺序快照对一下同形冲突四条（datasets/raw、calculations/latest、backtests/runs、/factors/new）仍在对应 `{param}` 之前。

- [ ] **Step 4: grep 闭包残留**

```bash
rg -n "\b(catalog|artifacts|model_training|resolved_settings|resolved_database|settings_service|kline|factor_data|strategy_center|backtest_workbench|backtest_job|backtest_plan|result_archive|research_runs|tushare_download|factor_mining_service|factor_detail_service|factor_pack_service|pages)\b" quantlab/api/routes
```

Expected: routes 里只应通过 `request.app.state.*` 或模块级 import（`apply_suspend_d` 等）访问；不应再出现 `create_app` 局部变量名当函数体依赖（`pages` 本地 `Path` 变量除外）。

- [ ] **Step 5: 全量单测**

```bash
.venv/bin/pytest tests/quantlab -q --ignore=tests/quantlab/browser
```

Expected: 全部 PASS。

- [ ] **Step 6（可选）: browser 冒烟**

```bash
.venv/bin/pytest tests/quantlab/browser -q
```

失败只修路由注册/页面路径，不改页面业务。

---

## 平稳过渡原则

1. 一次一域（Task 内写明的一簇）；同一 path 只能注册一次。
2. Task 1 先补 state，后面只改访问方式。
3. 最终统一 `app.state`，禁止半套闭包半套 Request。
4. 404 / 错 error_code 优先怀疑路由顺序，不要先改 service。
5. 可加 `tags=`；不加 `response_model`；不改 operation 语义。
6. 与 P2/P3 解耦：不碰 `app.js` 逻辑、不碰 `domain/status` 写路径。

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| `{param}` 抢静态段 | Task 0 顺序快照 + 上表四条；404 先查顺序 |
| 闭包变量未挂 state | Task 1 名称表；Task 7 grep |
| `Path.parents[N]` 错导致页面 500 | `test_factor_manual` / `test_web_shell` 相关 GET |
| 漏迁后半 `/api/factors*` 或 mining draft | Task 4 写死；Task 7 清单 diff |
| 测试搜 `app.py` 字符串 | Task 3 改两处测试 |
| `include_router(prefix="/api")` | 禁止 |

回滚：还原 `quantlab/api/`（或把该域 handler 贴回 `create_app`）。冻结点仍是源码还原后的 `main`。

---

## 工作量粗估

| Task | 粗估 |
|---|---|
| 0 清单 | <30 分钟 |
| 1 errors/static/state | 1 小时 |
| 2 health/settings | 0.5–1 小时 |
| 3 datasets/kline + 两则测试 | 1–2 小时 |
| 4 factors 三件套 | 2–3 小时 |
| 5 models/strategies | 1–2 小时 |
| 6 backtests/research | 2–3 小时 |
| 7 pages + 验收 | 1–2 小时 |
| **合计** | **约 1–2 人日** |

---

## Self-Review

1. **Spec coverage：** 拆路由、补 state、契约冻结、清单 diff、字符串测试、pages 顺序均有任务。
2. **Placeholder scan：** 无 TBD；回归命令写死 `.venv/bin/pytest`。
3. **一致性：** state 名全表锁定；`backtest-drafts` vs `backtests/drafts` 分 service；models **不重排**。

---

## 非目标（本计划不做）

- 不改 JSON schema / 不加 Pydantic `response_model`
- 不引入 API 版本前缀
- 不拆 `services/*`
- 不治理 P2 前端巨石、P3 状态机
- 不把 `error_payload` 去下划线、不引入 Depends
