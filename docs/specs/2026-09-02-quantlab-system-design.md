# QuantLab 整体系统设计

## 1. 目标

QuantLab 是运行在本机浏览器中的量化研究与回测管理平台。它统一管理本地 K 线、因子、策略、模型训练、回测运行和结果档案，并保证每个结果都能追溯到数据版本、因子版本、策略版本、模型参数和交易参数。

系统首先服务仓库下 `data/` 中已经存在的完整沪深 A 股数据与研究产物，不重新下载、不移动、不覆盖这些数据。

## 2. 方案选择

### 方案 A：静态 HTML + JSON

优点是开发快，适合原型。缺点是无法安全读取大型 Parquet、运行因子计算、训练模型或管理长时间回测任务。

### 方案 B：本地模块化单体（采用）

使用 FastAPI 提供本地 API 和静态页面，SQLite 保存元数据，PyArrow/Polars 读取外部 Parquet，训练与回测通过后台任务执行。该方案适合单机研究，部署简单，同时可以清楚分离页面、业务服务和数据仓库。

### 方案 C：前后端分离 SPA + 多服务任务系统

扩展能力最强，但需要 Node 构建链、独立队列和更多部署组件。当前单机研究阶段成本高于收益，暂不采用。

## 3. 核心原则

1. 原始数据和标准数据只读，任何研究计算写入新版本或独立运行目录。
2. 页面不复制完整数据仓库，通过注册表和 API 按路径读取；容量由目录清单实时计算。
3. 所有业务对象使用稳定 ID，页面跳转必须携带实体 ID 或版本 ID。
4. 因子、策略和运行记录均版本化；历史版本不可覆盖。
5. 训练集、验证集和测试集严格分离，测试过滤不能引用未来收益或标签。
6. 正式后复权字段沿用真实数据字段：`hfq_open`、`hfq_high`、`hfq_low`、`hfq_close`。
7. `DateGroupedRanker` 明确标记为本地排序代理模型，不宣称精确复刻 BigQuant 私有 StockRanker。
8. 失败任务也保存配置、错误、日志和已生成文件，便于复核。

## 4. 系统边界

### 4.1 权威数据仓库

以下目录继续作为权威数据仓库：

- `data/`
- `data/calibration`

正式注册的数据包括：

- `source_tables/*.parquet`：来源整理表；
- `canonical.parquet`：标准行情宽表；
- `features.parquet`：含七个已具备因子的研究宽表；
- `derived/hfq_market_st_v1`：带 ST 状态的版本化后复权行情；
- 经人工确认的单因子诊断和正式回测产物。

`tushare_migration_calibration` 中的大量目录默认属于实验产物，只有进入 QuantLab 注册表后才成为可用因子、模型或回测记录。

第一批正式后复权快照锁定为：

```text
dataset_id: ds_hfq_market_st_v1
version_id: 20260830T173152Z-50e42e72
manifest_sha256: 47e9c6968ee856136fea128aa49a76d0c6976afa63d99066744ab0b717f02d2a
row_count: 8204633
date_range: 20161010—20241231
```

注册时同时保存 `CURRENT` 当时的内容、manifest 哈希、源文件指纹和生成时间。以后 `CURRENT` 改变不会修改历史 DatasetVersion。`features.parquet` 单独注册为研究宽表，并记录它所引用的标准行情版本；它包含因子、标签和派生字段，只允许按注册字段用途读取，不能把标签列当作预测输入。

### 4.2 QuantLab 应用目录

```text
quantlab/
├── api/                  # HTTP 路由
├── domain/               # 数据集、因子、策略、运行实体
├── repositories/         # SQLite 和文件注册表
├── services/             # 目录、诊断、训练、回测、归档服务
├── jobs/                 # 后台任务和状态管理
├── web/
│   ├── assets/           # 共享 CSS、JS、图标
│   ├── pages/            # 正式页面
│   └── data/             # 随代码发布的静态小型资源
├── config/               # 数据根目录和默认参数
└── cli.py                # 启动与初始化入口

quantlab_runtime/
├── db/
│   └── quantlab.sqlite3  # 元数据
├── jobs/                 # 任务日志和临时状态
├── factors/              # 新计算的因子版本
├── strategies/           # 策略快照
└── results/              # 每次回测独立目录
```

`quantlab/web/data` 只保存随代码发布的静态小型资源；运行时目录、版本摘要和诊断摘要由 SQLite/API 提供，不在源码目录生成。任何位置都不保存完整行情和完整因子宽表副本。

## 5. 统一实体

### 5.1 Dataset 与 DatasetVersion

记录数据集名称、类型、存储路径、格式、主键、字段、日期范围、股票数量、行数、哈希、状态和版本。数据版本一旦发布不可修改。

### 5.2 Factor 与 FactorVersion

记录因子身份和不可变版本。版本包含公式、输入字段、数据版本、复权口径、窗口、频率、方向、缺失值规则、异常值规则、点时规则、诊断结果和状态。

### 5.3 Strategy 与 StrategyVersion

记录因子版本组合、模型定义、训练/测试过滤、标签、信号、仓位和交易规则。回测必须引用一个冻结的策略版本，或在开始运行时把工作台草稿冻结为临时策略版本。

### 5.4 Model、ModelVersion 与 ModelTrainingRun

`Model` 记录模型家族，例如 LightGBM LambdaRank、随机森林或 ExtraTrees。`ModelVersion` 记录已训练模型的特征版本、训练数据版本、训练区间、预处理器、超参数、随机种子、模型文件和指标。`ModelTrainingRun` 记录一次训练任务、日志、失败原因和产物。

策略可以引用一个已发布 ModelVersion，也可以保存训练规范并在每个 Walk-Forward 窗口产生新的 ModelVersion。本地 `DateGroupedRanker` 必须显示 `proxy` 标签。

### 5.5 ResearchRun

记录因子诊断、自动挖掘和模型比较等研究任务。保存输入快照、参数、状态、日志、候选结果和产物。

### 5.6 BacktestRun

记录回测名称、时间序列 ID、策略版本、数据版本、完整参数、状态、开始/结束时间、绩效摘要和结果目录。

### 5.7 Artifact

使用独立 `artifact_id` 统一登记每个任务输出文件，包含中文名称、原始文件名、角色、类型、路径、大小、内容哈希、生成时间和所属运行 ID。

### 5.8 ID 与唯一约束

- 数据集、因子、模型和策略使用稳定代码 ID，例如 `ds_hfq_market_st_v1`、`factor_momentum_5`；
- 版本使用不可变版本 ID，例如数据 manifest 版本、语义版本或内容哈希；
- 用户可见运行 ID 使用 `YYYYMMDD-HHMMSS-NNNN`；同一秒通过 SQLite 事务递增流水号；
- `(entity_id, version_id)` 和运行 ID 有唯一约束；Artifact 使用 `(run_id, artifact_role, content_hash)` 组合唯一，同一内容可以关联不同运行；
- 相同参数再次运行仍生成新的 BacktestRun，不覆盖历史记录；
- Walk-Forward 回测使用 `backtest_model_versions` 关联表记录每个窗口使用的 ModelVersion 与窗口序号，不把模型版本压缩成单值；
- 同一草稿发布时使用数据库事务和乐观锁，避免并发生成重复版本。

## 6. 状态与质量标记

DatasetVersion、FactorVersion、ModelVersion 和 StrategyVersion 使用：

```text
draft → validated → published → deprecated
```

ResearchRun、ModelTrainingRun 和 BacktestRun 使用：

```text
queued → running → completed
                 ↘ failed
```

数据质量单独使用 `quality_status = passed | warning | failed | needs_review`，不能混入生命周期状态。文件不存在、哈希变化或 Schema 变化会触发 `needs_review`；重新扫描且人工确认后才能恢复 `passed`。页面只能显示服务返回的状态，不自行构造不同名称。

## 7. 标准数据流

```text
TuShare 来源表
  → canonical 标准行情
  → hfq_market_st_v1 后复权版本
  → 因子计算或 features 宽表列
  → FactorVersion 注册与诊断
  → StrategyVersion 选择因子和模型
  → 训练集过滤与标签分箱
  → 本地模型训练与测试集预测
  → Top K 仓位分配
  → T+1 撮合、成本和现金管理
  → BacktestRun 绩效与产物
  → 结果档案和运行记录
```

每一层只引用上游版本，不回写上游数据。

### 7.1 时间、窗口和执行语义

- 当前 `momentum_5` 使用 `window_mode=instrument_observation`：按单只股票存在的有效行情记录执行 `shift(5)`，不是按自然日，也不是对停牌日补价格后再移位；
- 新建因子必须保存 `window_mode`，可选 `instrument_observation` 或 `market_trade_day`，两者属于不同 FactorVersion；
- 标签默认使用 `hfq_close[t+2] / hfq_open[t+1] - 1`，标签时点只用于训练，不进入测试输入；
- 默认信号在 t 日收盘后生成，t+1 日以 `hfq_open` 买入，至少持有一个完整交易日，于 t+2 日以 `hfq_close` 卖出；
- 若执行日停牌、缺少价格或触及禁止成交条件，订单按策略保存的 `unfilled_policy` 取消或顺延，不能用前值假装成交；
- 净值估值可以按配置使用最后可得收盘价，但估值价格与成交价格必须分开记录；
- 调仓间隔、执行延迟和持仓周期是三个独立参数，不能用一个“2 日调仓”隐含另外两个参数。

## 8. 因子规则

### 8.1 已有七因子

- `pe_ttm`
- `total_market_cap`
- `float_market_cap`
- `dividend_yield_ratio`
- `turn`
- `momentum_5`
- `volatility_5`

第一阶段从 `features.parquet` 注册这些因子。`momentum_5` 的正式公式统一为：

```text
hfq_close[t] / hfq_close[t - 5 observations] - 1
```

### 8.2 手动建立

用户选择已注册数据版本和字段，填写受限表达式与处理规则。系统先校验字段、窗口、点时和数据覆盖，再生成草稿版本；诊断通过后才能发布。

表达式不得直接使用 Python `eval`，必须通过白名单解析器执行。

### 8.3 自动挖掘

自动挖掘是 ResearchRun：固定数据快照、训练/验证/测试区间、字段白名单、算子白名单、表达式深度、覆盖率、相关性和复杂度。候选结果必须人工确认后才能转为 FactorVersion，不能自动进入正式回测。

所有截尾、标准化、分箱边界和缺失值统计只能在训练集拟合，再冻结并应用到验证集和测试集。自动挖掘不得用测试集选择候选公式。

## 9. 策略与回测规则

策略版本完整保存：

- 股票范围与数据版本；
- 因子版本及顺序；
- 训练、验证、测试区间；
- 训练集和测试集过滤规则；
- 标签公式和分箱规则；
- 模型类型及全部参数；
- 信号字段、排序方向、Top K 与权重；
- 调仓间隔、执行延迟、持仓周期；
- 买卖价格字段、手续费、最低费用、印花税、滑点、每手股数；
- 基准指数。

Walk-Forward 策略还需保存每个窗口的训练、验证、测试区间，以及“每窗口重新训练”或“复用已发布模型”的选择。模型文件必须作为 Artifact 关联到对应 ModelVersion。

回测开始前执行运行门禁：数据覆盖、主键唯一、版本存在、因子可用、无未来数据、撮合字段齐全、训练/测试不重叠。门禁失败不创建运行任务。

第一阶段只允许一个重型训练或回测任务运行。失败任务不自动重试；“重新运行”复制冻结配置并生成新运行 ID，原任务保持不变。

## 10. 页面信息架构与职责

### 10.1 研究总览 `/`

展示数据健康、正式因子数量、策略数量、任务状态、最近运行和常用入口。只汇总，不承担编辑。

### 10.2 数据中心 `/data`

展示所有注册数据集、当前版本、覆盖范围、质量和存储位置。进入 K 线管理、因子数据管理和质量报告。

### 10.3 K 线数据管理 `/data/kline`

展示原始与后复权字段和版本；支持按股票/日期查看 K 线、复权事件、缺失和质量报告。第一阶段只读。当前正式表持久化 `raw_*` 与 `hfq_*`；不提供前复权（qfq）。

### 10.4 因子数据管理 `/data/factors`

展示因子列的物理存储、覆盖范围、版本映射和诊断产物，区分正式数据与实验产物。

### 10.5 因子研究主页 `/research/factors`

管理手动建立、自动挖掘、研究任务和因子库入口。

### 10.5a 研究运行详情 `/research/runs/{run_id}`

展示因子诊断或自动挖掘的配置快照、数据/因子版本、状态、候选、日志和 Artifact。

### 10.6 因子库 `/factors`

查询因子及最新正式版本；不在这里维护临时因子组合。因子组合属于策略或回测工作台。

### 10.7 因子详情 `/factors/{factor_id}/versions/{version_id}`

展示不可变定义、数据版本、诊断、历史版本和产物。可以复制为草稿或带版本 ID 进入策略/回测工作台。

### 10.8 手动建立因子 `/factors/new`

编辑草稿、运行校验和诊断、发布新版本。

### 10.9 自动挖掘 `/research/factor-mining`

配置搜索空间、运行研究任务、查看候选和人工入库。

### 10.10 策略中心 `/strategies`

管理策略及版本，连接因子、模型、过滤、仓位和交易规则。策略页面不直接执行回测。

### 10.10a 模型中心 `/models`

管理 Model、ModelVersion 和 ModelTrainingRun，展示模型家族、训练规范、已发布版本、适用因子和 proxy 标记。

### 10.10b 模型版本与训练运行 `/models/{model_id}/versions/{version_id}`、`/models/runs/{run_id}`

展示不可变模型版本、训练数据/因子版本、窗口、预处理、超参数、指标、日志和模型 Artifact。

### 10.11 回测工作台 `/backtests/new`

加载策略版本或复制历史配置，允许形成新草稿；运行前显示完整参数、预估样本和门禁结果。点击运行后冻结配置并生成新运行 ID。

### 10.12 结果档案 `/backtests/runs`

按名称、ID、策略、状态和时间筛选所有运行。点击名称进入运行记录；复制配置进入工作台但不自动运行。

### 10.13 运行记录 `/backtests/runs/{run_id}`

展示状态、配置、绩效、净值、回撤、持仓、交易、日志、中文文件清单和可追溯检查。

运行记录不能只依赖文件路径。SQLite 中保存结构化绩效摘要；净值、回撤、持仓和交易使用有固定 Schema 的 Parquet Artifact：

```text
daily_equity: trade_date, equity, cash, market_value, daily_return, drawdown, benchmark_equity
positions: trade_date, ts_code, quantity, weight, market_value, score
trades: trade_date, ts_code, side, quantity, price, gross_amount, fee, tax, slippage, order_status
metrics: total_return, annual_return, volatility, sharpe, max_drawdown, win_rate, turnover, benchmark_return
```

### 10.14 设置 `/settings`

管理数据根目录、结果根目录、Python 环境、并发数和默认回测参数。修改设置不得移动既有数据。

路径必须位于配置的允许根目录中。页面不能接受任意系统路径；路径变化后先扫描并显示影响，不自动迁移文件。

## 11. 页面共享能力

- 统一应用壳、导航、面包屑；
- 实体与版本选择器；
- 状态标签；
- 数据质量面板；
- 参数摘要；
- 中文产物清单；
- 加载、空数据和错误状态；
- 保存、运行、发布确认；
- URL 参数和浏览器返回状态保持。

## 12. 错误处理

1. 文件不存在或哈希变化：数据版本标记 `needs_review`，禁止新运行。
2. Schema 不匹配：展示缺失字段，不静默改名。
3. 因子表达式错误：保存草稿和错误位置，不发布版本。
4. 训练或回测失败：保存冻结配置、异常、日志和部分产物。
5. 页面 API 失败：显示实体 ID、错误信息和重试入口，不显示伪造示例结果。
6. 任务进程异常退出：数据库将任务标记为 `failed`，保留 PID、退出码、最后日志位置和已登记 Artifact。

## 13. 测试与验收

### 单元测试

- 注册表解析、路径边界和哈希；
- 状态转换；
- 因子表达式白名单；
- 策略配置校验；
- 回测运行 ID 和产物登记。

### 数据契约测试

- `(trade_date, ts_code)` 主键唯一；
- 实际字段使用 `hfq_*`；
- 数据版本和因子版本存在；
- 训练、验证、测试无越界；
- 点时规则和标签对齐；
- 原始文件修改时间与哈希不变。

### 浏览器验收

- 每个导航入口可达；
- 页面通过 URL 正确传递实体和版本 ID；
- 刷新后仍显示同一实体；
- 复制配置不自动运行；
- 失败状态和日志可查看；
- 运行完成后结果档案和运行记录一致。

## 14. 实施依赖顺序

1. 应用基础、统一实体、SQLite、数据注册表和共享页面壳；
2. 数据中心、K 线管理、因子数据管理；
3. 因子研究主页、因子库、因子详情；
4. 手动建立因子；
5. 自动挖掘因子；
6. 策略中心；
7. 回测工作台和本地执行适配；
8. 结果档案和运行记录；
9. 设置、总览和端到端验收。

后续任务必须按该顺序执行。每个任务由独立子代理实现，主代理先做规格符合性审查，再做代码质量和浏览器验收；未通过不得开始下一任务。
