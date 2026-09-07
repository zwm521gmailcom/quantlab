# QuantLab Shared Foundation Implementation Plan

> **For agentic workers:** REQUIRED: Use `subagent-driven-development` (if subagents available) or `executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 QuantLab 本地 Web 服务、统一实体与状态、SQLite 元数据、只读数据注册表、Artifact 登记、共享页面壳和自动化测试基础。

**Architecture:** 新建独立的 `quantlab` Python 包，不修改 vn.py 核心。FastAPI 只提供本地 API 和静态页面；SQLite 保存元数据；外部 Parquet 通过注册路径只读访问；运行时目录与摘要由 SQLite/API 提供，不写入源码目录。

**Tech Stack:** Python 3.12、FastAPI、Uvicorn、Pydantic、SQLite、PyArrow、Polars、pytest、Ruff、原生 HTML/CSS/JavaScript。

---

## 依赖与边界

- 依赖总设计：`docs/specs/2026-09-02-quantlab-system-design.md`。
- 权威数据目录只读：`tushare_migration_data`、`tushare_migration_calibration`。
- 本任务不实现因子计算、自动挖掘、模型训练或回测。
- 不修改 `.codex/brainstorm/20260901` 中现有原型。
- 第一阶段强制只监听 `127.0.0.1`，不提供非本地监听开关。
- 受控可写根目录统一为 `quantlab_runtime/{db,jobs,factors,strategies,results,baselines,logs}`；权威数据根仅只读。

## Chunk 1: 包结构与配置

### Task 1: 建立可测试的配置对象

**Files:**
- Modify: `pyproject.toml`
- Create: `quantlab/__init__.py`
- Create: `quantlab/config.py`
- Create: `quantlab/cli.py`
- Test: `tests/quantlab/test_config.py`

- [ ] **Step 1: 写失败测试**

  测试默认项目根目录、数据根目录、校准根目录、运行根目录和允许路径集合；测试外部路径会被拒绝。

- [ ] **Step 2: 运行失败测试**

  Run: `.venv/bin/pytest tests/quantlab/test_config.py -q`

  Expected: FAIL，原因是 `quantlab.config` 尚不存在。

- [ ] **Step 3: 添加依赖、打包配置与最小实现**

  在 `pyproject.toml` 增加 `quantlab` 可选依赖：FastAPI、Uvicorn、Pydantic、Playwright、pytest-playwright，并把 Hatch wheel packages 改为 `['vnpy', 'quantlab']`、sdist 纳入 `quantlab*`。`Settings` 必须使用显式绝对路径，并通过 `Path.resolve()`（包括符号链接解析）验证读取路径属于两个只读数据根，写入路径属于 `quantlab_runtime` 的受控子目录。

- [ ] **Step 4: 验证通过**

  Run: `.venv/bin/pytest tests/quantlab/test_config.py -q`

  Expected: PASS。

- [ ] **Step 5: 增加 CLI 骨架**

  支持 `python -m quantlab.cli serve --host 127.0.0.1 --port 8765` 和 `init-db`；传入其他 host 必须拒绝。

## Chunk 2: 领域实体与 SQLite

### Task 2: 建立实体、状态和数据库 Schema

**Files:**
- Create: `quantlab/domain/__init__.py`
- Create: `quantlab/domain/entities.py`
- Create: `quantlab/domain/status.py`
- Create: `quantlab/repositories/__init__.py`
- Create: `quantlab/repositories/database.py`
- Create: `quantlab/repositories/schema.sql`
- Test: `tests/quantlab/test_entities.py`
- Test: `tests/quantlab/test_database.py`

- [ ] **Step 1: 写实体失败测试**

  覆盖 Dataset、DatasetVersion、Factor、FactorVersion、Model、ModelVersion、ModelTrainingRun、Strategy、StrategyVersion、ResearchRun、BacktestRun、Artifact。验证缺少 ID、非法状态和可变版本会失败。

- [ ] **Step 2: 验证测试正确失败**

  Run: `.venv/bin/pytest tests/quantlab/test_entities.py -q`

- [ ] **Step 3: 实现状态枚举、转换矩阵和不可变实体**

  定义生命周期状态 `draft/validated/published/deprecated`、运行状态 `queued/running/completed/failed`、质量状态 `passed/warning/failed/needs_review`。显式实现允许转换矩阵；至少拒绝 `published -> draft`、`deprecated -> published`、`completed -> running`、`failed -> running`，重跑必须创建新运行。

- [ ] **Step 4: 写数据库失败测试**

  验证表存在、外键开启、`(entity_id, version_id)` 唯一、运行 ID 唯一、事务冲突会回滚。明确验证 `FactorVersion -> DatasetVersion`、`ModelVersion -> FactorVersion[]/DatasetVersion`、`StrategyVersion -> FactorVersion[]/ModelVersion`、`ModelTrainingRun -> ModelVersion/DatasetVersion`、`BacktestRun -> StrategyVersion/DatasetVersion` 的引用完整性与删除限制；Walk-Forward 使用 `backtest_model_versions(backtest_run_id, fold_index, model_version_id)` 关联每个窗口的模型版本。

- [ ] **Step 5: 实现 SQLite 初始化与事务封装**

  使用标准库 `sqlite3`，连接时执行 `PRAGMA foreign_keys=ON` 和 WAL；所有发布操作必须在事务中完成。

- [ ] **Step 6: 验证通过**

  Run: `.venv/bin/pytest tests/quantlab/test_entities.py tests/quantlab/test_database.py -q`

## Chunk 3: 数据注册表与只读校验

### Task 3: 注册现有权威数据集

**Files:**
- Create: `quantlab/repositories/datasets.py`
- Create: `quantlab/services/catalog.py`
- Create: `quantlab/config/datasets.json`
- Test: `tests/quantlab/test_dataset_catalog.py`
- Test: `tests/quantlab/test_data_immutability.py`

- [ ] **Step 1: 写注册表失败测试**

  验证首批注册项：source tables、`canonical.parquet`、`features.parquet`、`ds_hfq_market_st_v1@20260830T173152Z-50e42e72`。验证真实字段是 `hfq_open/hfq_high/hfq_low/hfq_close`。

- [ ] **Step 2: 记录数据基线**

  新建 `quantlab_runtime/baselines/authoritative-data.json`，按相对路径记录文件大小、纳秒 mtime、manifest/CURRENT 内容哈希和关键 Parquet footer/schema 哈希。基线由 `python -m quantlab.cli snapshot-data-baseline` 在任何数据注册写入前生成；文件已存在时默认拒绝覆盖。不得把大型 Parquet 复制到网站源码目录。

- [ ] **Step 3: 运行失败测试**

  Run: `.venv/bin/pytest tests/quantlab/test_dataset_catalog.py tests/quantlab/test_data_immutability.py -q`

- [ ] **Step 4: 实现目录加载与元数据扫描**

  只读取 Parquet metadata、manifest 和 CURRENT；保存 DatasetVersion 的路径、行数、Schema、日期范围、内容哈希、源指纹和质量状态。历史版本不得跟随 CURRENT 改写。

- [ ] **Step 5: 生成轻量浏览器目录响应**

  目录由 SQLite/API 动态返回，只包含 ID、版本、显示名称、行数、日期范围、状态和 API URL，不生成运行时源码文件，也不包含数据明细。

- [ ] **Step 6: 验证通过且数据未变化**

  Run: `.venv/bin/pytest tests/quantlab/test_dataset_catalog.py tests/quantlab/test_data_immutability.py -q`

## Chunk 4: Artifact 与运行身份

### Task 4: 建立 Artifact 登记和时间序列运行 ID

**Files:**
- Create: `quantlab/repositories/artifacts.py`
- Create: `quantlab/services/run_identity.py`
- Test: `tests/quantlab/test_artifacts.py`
- Test: `tests/quantlab/test_run_identity.py`

- [ ] **Step 1: 写失败测试**

  验证运行 ID 格式 `YYYYMMDD-HHMMSS-NNNN`，同一秒并发生成不重复；Artifact 使用独立且唯一的 `artifact_id`，并包含中文名称、原文件名、角色、路径、大小、哈希、时间和所属运行。

- [ ] **Step 2: 验证失败**

  Run: `.venv/bin/pytest tests/quantlab/test_artifacts.py tests/quantlab/test_run_identity.py -q`

- [ ] **Step 3: 最小实现**

  使用 SQLite 事务分配流水号。数据库固定在 `quantlab_runtime/db/quantlab.sqlite3`。Artifact 路径必须属于统一白名单中的运行根目录或只读数据根目录，文件缺失时返回明确错误。Artifact 内容哈希不做全局唯一；采用 `(run_id, artifact_role, content_hash)` 唯一，允许相同内容关联不同运行。

- [ ] **Step 4: 验证通过**

  Run: `.venv/bin/pytest tests/quantlab/test_artifacts.py tests/quantlab/test_run_identity.py -q`

## Chunk 5: API 与共享页面壳

### Task 5: 建立本地 API

**Files:**
- Create: `quantlab/api/__init__.py`
- Create: `quantlab/api/app.py`
- Create: `quantlab/api/routes/__init__.py`
- Create: `quantlab/api/routes/health.py`
- Create: `quantlab/api/routes/datasets.py`
- Create: `quantlab/api/routes/artifacts.py`
- Test: `tests/quantlab/test_api_health.py`
- Test: `tests/quantlab/test_api_datasets.py`

- [ ] **Step 1: 写 API 失败测试**

  覆盖：`GET /api/health`、`GET /api/datasets`、`GET /api/datasets/{id}`、`GET /api/datasets/{id}/versions/{version}`、`GET /api/artifacts/{id}`；不存在返回 404，Schema 变化返回质量警告。

- [ ] **Step 2: 验证失败**

  Run: `.venv/bin/pytest tests/quantlab/test_api_health.py tests/quantlab/test_api_datasets.py -q`

- [ ] **Step 3: 实现 API 与错误结构**

  所有错误返回 `error_code/message/entity_id/details`。API 不返回任意绝对路径下载能力；Artifact 下载必须经过白名单检查。

- [ ] **Step 4: 验证通过**

  Run: `.venv/bin/pytest tests/quantlab/test_api_health.py tests/quantlab/test_api_datasets.py -q`

### Task 6: 建立共享浏览器壳

**Files:**
- Create: `quantlab/web/assets/app.css`
- Create: `quantlab/web/assets/app.js`
- Create: `quantlab/web/pages/index.html`
- Test: `tests/quantlab/test_web_shell.py`

- [ ] **Step 1: 写静态结构失败测试**

  验证统一导航含研究总览、数据中心、因子研究、模型中心、策略中心、回测中心、结果档案和设置；研究运行与模型训练详情可从对应中心进入；验证加载、空数据、错误状态容器存在。

- [ ] **Step 2: 验证失败**

  Run: `.venv/bin/pytest tests/quantlab/test_web_shell.py -q`

- [ ] **Step 3: 实现共享 CSS/JS 与首页占位壳**

  页面只显示 API 返回的数据；API 失败时不能回退到伪造示例结果。导航使用相对 URL，不硬编码端口。

- [ ] **Step 4: 验证通过**

  Run: `.venv/bin/pytest tests/quantlab/test_web_shell.py -q`

## Chunk 6: 基础门禁与浏览器验收

### Task 7: 完成基础验证

**Files:**
- Create: `tests/quantlab/test_foundation_smoke.py`
- Create: `tests/quantlab/browser/test_foundation.py`
- Create: `tests/quantlab/browser/conftest.py`
- Create: `docs/quantlab/start-local.md`

- [ ] **Step 1: 安装 QuantLab 与浏览器测试依赖**

  Run: `.venv/bin/pip install -e '.[alpha,quantlab,dev]' && .venv/bin/python -m playwright install chromium`

- [ ] **Step 2: 运行完整基础测试**

  Run: `.venv/bin/pytest tests/quantlab -q`

  Expected: 0 failures。

- [ ] **Step 3: 运行 Ruff**

  Run: `.venv/bin/ruff check quantlab tests/quantlab`

  Expected: 0 errors。

- [ ] **Step 4: 启动本地服务并检查**

  Run: `.venv/bin/python -m quantlab.cli serve --host 127.0.0.1 --port 8765`

  Expected: `/api/health`、首页和数据目录可访问。

- [ ] **Step 5: 浏览器验收**

  `tests/quantlab/browser/conftest.py` 使用子进程启动 Uvicorn 到随机本地端口，轮询 `/api/health` 就绪，测试结束发送 terminate 并等待退出。Run: `.venv/bin/pytest tests/quantlab/browser/test_foundation.py -q`。验证导航、API 数据、错误状态、刷新保持和无控制台错误。

- [ ] **Step 6: 数据不变检查**

  对比实施前保存的大小、修改时间和 manifest 哈希。Expected: 权威数据无变化。

## 主代理审批门

- [ ] **规格符合性：** 实体、API、状态、字段和目录边界与总设计一致。
- [ ] **测试：** 观察过 RED，全部 pytest 与 Ruff 通过。
- [ ] **代码质量：** 无任意路径访问、无 `eval`、事务和错误结构清晰。
- [ ] **数据不变：** 两个权威数据目录未被移动、覆盖或复制进网站。
- [ ] **浏览器：** 页面可达、API 数据真实、刷新可恢复、错误可查看。

只有五项全部通过，README 中 `00-foundation` 才能从 `pending` 改为 `completed`，并开始 `01-overview`。
