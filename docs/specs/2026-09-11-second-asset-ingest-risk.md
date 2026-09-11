# 第二资产入库前风险检测与修正方案

日期：2026-09-11
状态：入库前闸门（先修冲突，再登记新资产）
第一资产：沪深 A 股 `data/canonical.parquet`（`ds_canonical_market` / `current`）

## 结论

**不要把第二资产写入现有 `canonical.parquet`，也不要覆盖 `ds_canonical_market`。**  
当前仓库按「单表、单市场、单主键」建成。第二资产必须是**新的 Dataset + 新文件 + 新 entity_id**。在修好 P0 之前，不要跑会改库的 `init-db` 或把整目录 SQLite 当入库手段。

## 第一资产现状

| 项 | 值 |
|---|---|
| 文件 | `data/canonical.parquet`（约 1028 万行、32 列） |
| 主键 | `trade_date` + `ts_code` |
| 交易所 | 仅 `SSE` / `SZSE`，代码后缀 `.SH` / `.SZ` |
| 契约 | `docs/specs/2026-09-03-canonical-contract.md` |
| 已登记 | `dataset_source_tables`、`ds_canonical_market` |
| 派生 | `data/derived/canonical_pack_factors.parquet`、`composite_pack_factors.parquet` |

因子、K 线、训练、回测默认都绑在 `ds_canonical_market`。K 线代码正则是 `^\d{6}\.(SZ|SH)$`。默认基准 `000300.SH`。交易日历下载写死 `exchange=SSE`。

## 禁止（入库时踩了会坏第一资产）

1. 向 `canonical.parquet` 追加或合并第二资产行。
2. 复用 `ds_canonical_market`、`version_id=current`、或因子 ID `factor_<字段>`。
3. 用整目录拷贝 + `init-db` 当「入库」（已发布版本的 `path` 不可改，Mac 已炸过）。
4. 用 git 或 LAN 同步覆盖 `quantlab_runtime/db/`、`config/`。
5. 假定第二资产也有 ST、涨跌停、停牌、`.SH/.SZ`、A 股交易日历。

## 冲突点

### P0 已发布版本不可改 path（已发生）

`dataset_versions_immutable_published` 禁止改已发布行的 `path` / 行数 / 字段 / 哈希 / `metadata_json`。  
`register_configured()` 在 `init-db` 和「重新扫描」里会对已有行执行：

```sql
UPDATE dataset_versions SET quality_status = ?, path = ?
```

整目录换盘符或换机器后，`store_path()` 变成相对路径（如 `data/canonical.parquet`），和库里旧绝对路径不同，触发：

`sqlite3.IntegrityError: published dataset version metadata is immutable`

Mac 启动脚本先跑 `init-db`，所以整夹拷到 `/Volumes/T2/quantlab` 会直接退出，服务起不来。

**修正：** 已发布行只更新 `quality_status`。路径解析把「另一台机器的绝对路径」按 `data/`、`quantlab_runtime/` 后缀重定位到本机项目根。新登记一律存相对路径。

### P0 单表主键与契约

契约规定每日每股票一行。第二资产若写入同一文件：

- `ts_code` 可能撞号（指数 `000001.SH` 与股票体系混用）。
- 日历、停牌、涨跌停、ST、复权因子语义不同，第一资产的 `eligible` / 过滤会错杀或漏杀。
- `apply_suspend_d()` 原地替换 `canonical.parquet`，第二资产行会被 A 股停牌表改写。

**修正：** 新文件，例如 `data/canonical_<asset>.parquet`，新 `entity_id`（如 `ds_canonical_<asset>`），新 `version_id`。第一资产文件只读。

### P1 全局因子 ID

`factor_momentum_5` 等 entity_id 全局唯一，且 `sync_factor_versions()` 把 v1 写死到 `ds_canonical_market`。第二资产若再用同名字段会：

- 插不进 `factor_versions`（已有 v1 直接 skip）；
- 或误绑到 A 股宽表。

**修正：** 第二资产因子用 `factor_<asset>_<field>`，禁止 `sync_factor_versions()` 为新表自动登记 A 股那 7 个 ID。

### P1 沪深过滤把非 A 股滤成空集

| 位置 | 行为 |
|---|---|
| `backtest_workbench` 预估 | `instrument.str.endswith((".SH", ".SZ"))` |
| `backtest_job.apply_section_stock_scope` | 沪市/深市/中国A股按后缀切 |
| `kline.py` | 代码必须 `\d{6}.SZ\|SH` |
| `index_membership` | 主板/中小/创业板号段；成分只认 `000300.SH` / `000905.SH` |
| 开仓过滤默认 | `open < up_limit AND open > down_limit`；缺涨跌停则整段剔除 |

第二资产没有这些列或后缀时，回测预估为 0、K 线拒符号、开仓过滤器清空样本。

**修正：** 市场范围做成数据集属性（`market_scope`），不要从代码后缀猜。第二资产的 K 线/回测走自己的代码规则和过滤，缺涨跌停则关闭对应过滤器，而不是清空。

### P1 训练与回测默认数据集

`model_training` 在数据集未写明时退回 `ds_canonical_market`。工作台/规则页 JS 同样默认该 ID。新资产若只加文件、不改入口，训练仍读 A 股。

**修正：** 策略/回测/手工因子必须显式选数据集；去掉「空则 A 股」的隐式回退，或仅在 `dataset_id` 属于 A 股契约时才回退。

### P1 日历与基准

`tushare_download.refresh_trade_cal` 只拉 SSE。基准默认 `000300.SH`。第二资产用自己的交易日或基准时，再平衡、收益、权益曲线会错位。

**修正：** 每个 DatasetVersion 登记 `calendar_id` 与 `default_benchmark`。回测禁止跨数据集借用 A 股日历。

### P2 局域网同步文件名

8766 行情同步按 `data/` 相对路径覆盖同名文件。若第二资产也叫 `canonical.parquet`，同步会覆盖第一资产。

**修正：** 文件名不得与第一资产冲突。同步白名单按相对路径精确匹配，不整目录镜像删除。

### P2 机器库不可当资产载体

设计已规定不同步 `quantlab_runtime/db/` 与 `config/`（机器码、运算设置、Token）。整夹拷贝等于把机器 A 的已发布元数据搬到机器 B，正是 P0 的触发条件。

**修正：** 第二资产只作为 `data/` 下新文件入库。目标机用本机 SQLite **INSERT 新 DatasetVersion**，不搬运旧库。程序更新继续 `git pull`。

### P2 派生包与计算预算

`canonical_pack_factors` / `composite_pack_factors` 按 A 股公式和市值/股息字段物化。第二资产若缺 `float_market_cap`、`dividend_yield_ratio` 等，整包会失败或写出空列。一条回测已按整表内存来估预算，第二张宽表不能默认再开一条并行。

**修正：** 派生包按 `dataset_id` 分目录，例如 `data/derived/<asset>/`。资源闸门仍是同时 1 条回测。

## 回测与宽表：一次回测只走一张表

把现有 `canonical.parquet` 按资产拆开或挪到子目录，**会把已有回测弄断**，原因：

1. **已发布路径不可改。** `ds_canonical_market` / `current` 已把 `path` 冻在当前文件。挪文件后库里的路径还指向旧位置，历史 `backtest_runs` 全部读不到。
2. **作业其实已经按数据集取文件。** `BacktestJobService._path_and_row` 用本次运行的 `dataset_id` + `dataset_version_id` 去 `dataset_versions.path` 读 parquet。并不是扫 `data/` 下所有宽表。
3. **工作台把数据集锁死在因子上。** 下拉只放「所选因子绑定的那张表」，默认 `ds_canonical_market::current`。现有已发布因子全绑 A 股，所以界面上看起来「回测都走现在的宽表」。
4. **公式包旁路按宽表父目录拼路径。** `default_sidecar_path` = `{宽表目录}/derived/canonical_pack_factors.parquet`。若第二资产也放在 `data/` 根下，会和 A 股抢同一份 sidecar。

因此：**第一资产文件原地不动。** 分开文件只表示「第二资产另起一张表」，不是把现在这张表切开。一次回测仍然只绑一张宽表；要跑第二资产，选第二套因子/数据集，作业就会改读新 path。不要把两张表拼进同一次回测。

```text
# 作业取数（已有）
backtest_runs.dataset_id + dataset_version_id
  → dataset_versions.path
  → 只读这一份 parquet（外加该表自己的 derived sidecar）
```

## 修正后的入库形状

```text
data/
  canonical.parquet                    # 第一资产，原地不动；已有回测继续走这里
  canonical_<asset>.parquet            # 第二资产，新文件，禁止与上一行同名
  derived/
    canonical_pack_factors.parquet     # 仅服务第一资产（现有 sidecar 也不动）
    composite_pack_factors.parquet
    <asset>/                           # 第二资产自己的 sidecar，禁止写回上一层同名文件
  raw_<asset>/                         # 可选；不要写入 raw/daily 等 A 股日更目录
```

`datasets.json` 增一条，**不改** `ds_canonical_market` 的 path：

```text
entity_id: ds_canonical_<asset>
path: canonical_<asset>.parquet
version_id: current
metadata.market_scope / calendar_id / default_benchmark: 随该资产
```

因子：`factor_<asset>_<field>`，`dataset_id=ds_canonical_<asset>`。  
工作台：选这些因子后，数据集下拉锁到第二张表，那次回测读新文件；未选因子时仍默认 A 股，旧回测不受影响。

## 入库前检查清单

在写入任何新 parquet 或改 `datasets.json` 之前：

1. 修好 `init-db`：已发布行不改 `path`；启动脚本在库已存在时不要把 `init-db` 当必经且失败即退出。
2. `resolve_user_path` 能消化拷盘后的绝对路径（否则本机读第一资产也会失败）。
3. 新文件路径相对 `data/`，与现有文件零重名。
4. 抽样主键：`(trade_date, ts_code)` 在新文件内唯一；与第一资产代码集交叉只作报告，不合并。
5. Schema 对照契约：缺的 A 股列（ST、涨跌停、停牌）记为「本市场不适用」，不要填 0 假装合规。
6. 确认 LAN 同步不会把新文件覆盖到对端的 `canonical.parquet`。
7. 确认没有任何任务仍持有第一资产写锁（`apply_suspend_d`、物化因子包）。
8. 目标机用本机库 INSERT；禁止把源机 `quantlab.sqlite3` 覆盖过来。

## 建议实施顺序

1. **闸门修复（必须先做）**：`catalog._register_entry` 不更新已发布 `path`；`Settings.resolve_user_path` 重定位外机绝对路径。没有这项，第二台机器无法稳定启动，更谈不上入库。
2. **登记通道**：只新增 Dataset，不改 `ds_canonical_market` 的 path；工作台靠因子锁定切表，而不是拆现有宽表。
3. **市场策略**：`market_scope`、代码规则、过滤器、日历、基准随数据集走；A 股回测的沪深/涨跌停默认保持不变。
4. **因子命名空间**：第二资产因子独立 entity_id，不走 A 股 `sync_factor_versions`。没有第二套已发布因子时，回测入口不会切到新表。
5. **最后才拷第二资产文件** 并 INSERT 新 DatasetVersion。禁止 rename/移动 `canonical.parquet`。

## 明确不做

- 不把第二资产行混进现有 1028 万行宽表
- 不把现有 `canonical.parquet` 拆目录、改名或改已发布 path
- 不把两张宽表拼进同一次回测
- 不改已发布 A 股 DatasetVersion 的哈希、字段、行数
- 不把 `features.parquet` 或因子派生包冒充第二行情资产
- 不在本阶段做跨资产组合或跨日历回测
- 不开放公网 bind、不同步 Token/机器码
