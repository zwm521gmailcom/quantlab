# 第二资产入库前风险检测与修正方案

日期：2026-09-11
状态：入库前闸门（先修冲突，再登记新资产）
第一资产：沪深 A 股 `data/canonical.parquet`（`ds_canonical_market` / `current`）
第二资产：数字货币（日频宽表，基本上只有价格、市值、总份数）

## 结论

**不要把数字货币写入现有 `canonical.parquet`，也不要覆盖 `ds_canonical_market`。**  
现有回测、因子、工作台都绑在 A 股那一张 32 列宽表上。数字货币另起文件和 Dataset。一次回测只读一张表：A 股回测继续走旧表；要跑币，选数字货币的因子/数据集。

数字货币**不要补** ST、涨跌停、停牌、复权、股息、PE。缺的列就缺，回测侧关掉对应默认过滤器，而不是填 0 假装是 A 股。

## 第一资产现状

| 项 | 值 |
|---|---|
| 文件 | `data/canonical.parquet`（约 1028 万行、32 列） |
| 主键 | `trade_date` + `ts_code` |
| 交易所 | 仅 `SSE` / `SZSE`，代码后缀 `.SH` / `.SZ` |
| 契约 | `docs/specs/2026-09-03-canonical-contract.md` |
| 已登记 | `dataset_source_tables`、`ds_canonical_market` |

因子、K 线、训练、回测默认都绑在 `ds_canonical_market`。K 线代码正则是 `^\d{6}\.(SZ|SH)$`。默认基准 `000300.SH`。交易日历下载写死 `exchange=SSE`。提交回测时 `stock_scope` 必须是 `中国A股（SH/SZ）`。

## 数字货币宽表契约（最小集）

只落真实有的列。主键仍是每日每标的一行，但代码不是 A 股 `ts_code`。

| 字段 | 含义 | 说明 |
|---|---|---|
| `ts_code` | 标的代码 | 如 `BTC-USDT`，禁止写成 `000001.SZ` 这种 A 股号 |
| `trade_date` | UTC 日（YYYYMMDD） | 按 UTC 自然日切 bar，不用 SSE 日历 |
| `open` / `high` / `low` / `close` | 价格 | 无复权；回测买卖价就用这四列 |
| `vol` | 成交量 | 有则落；没有可缺 |
| `amount` | 成交额 | 有则落；没有可缺 |
| `total_market_cap` | 市值 | 与 A 股「元」对齐，便于截面排名 |
| `circulating_supply` | 流通份数 | 用户说的「总股数」对应项 |
| `total_supply` | 总份数 | 有则落；没有可缺 |

**不要落、也不要填默认值：** `adj_factor`、`hfq_*`、`up_limit`、`down_limit`、`st_status`、`is_suspended`、`eligible`、`pe_ttm`、`dv_ttm`、`dividend_yield_ratio`、`list_date`、`delist_date`。

口径：

- `total_market_cap = close × circulating_supply`（若源数据已给市值，以源为准并记下）
- 价格列不乘复权因子；需要「后复权语义」的公式在数字货币上改读 `close`
- 日历：有 bar 的日期即交易日，不用 `trade_cal`

## 禁止（入库时踩了会坏第一资产或把币回测跑空）

1. 向 `canonical.parquet` 追加数字货币行。
2. 复用 `ds_canonical_market`、`version_id=current`，或因子 ID `factor_momentum_5`。
3. 用整目录拷贝 + `init-db` 当入库（已发布 `path` 不可改，Mac 已炸过）。
4. 用 git 或 LAN 同步覆盖 `quantlab_runtime/db/`、`config/`。
5. 给数字货币补 ST/涨跌停/停牌/复权，或继续用 `000300.SH` + SSE 日历。
6. 把现有 `canonical.parquet` 拆目录、改名。

## 冲突点

### P0 已发布版本不可改 path（已发生）

`dataset_versions_immutable_published` 禁止改已发布行的 `path` / 行数 / 字段 / 哈希 / `metadata_json`。  
`register_configured()` 会对已有行 `UPDATE ... path`。拷盘后路径字符串一变即：

`sqlite3.IntegrityError: published dataset version metadata is immutable`

**修正：** 已发布行只更新 `quality_status`。外机绝对路径按 `data/`、`quantlab_runtime/` 重定位。新登记存相对路径。

### P0 单表主键与 A 股契约

数字货币代码与 A 股 `ts_code` 不是同一套。混进一张表后：`apply_suspend_d()` 会按 A 股停牌表改写整文件；涨跌停/ST 过滤会把币当异常股删光。

**修正：** `data/canonical_crypto.parquet` + `ds_canonical_crypto`。A 股文件只读。

### P0 回测提交写死 A 股范围

`BacktestWorkbenchService._validate_core`：`stock_scope` 必须是 `中国A股（SH/SZ）`。  
预估样本用 `instrument.str.endswith((".SH", ".SZ"))`。  
`drop_unverified_halt_rows` 在 `skip_limit_close=True` 且没有 `up_limit`/`down_limit` 时**直接清空**。  
预览还要求表里有 `st_status`、`is_suspended`。  
工作台默认开仓公式是 `open < up_limit AND open > down_limit`。

现有 A 股回测必须保持这些默认。数字货币若走同一套默认，预览失败或样本为 0。

**修正（仅当 `dataset_id=ds_canonical_crypto`）：**

| 项 | A 股（不变） | 数字货币 |
|---|---|---|
| `stock_scope` | `中国A股（SH/SZ）` | `数字货币` |
| 代码过滤 | `.SH` / `.SZ` | 不过滤后缀 |
| 开仓涨跌停公式 | 默认开启 | 不预填、不校验涨跌停 |
| `skip_limit_close` | 默认 true | false，且缺列时跳过而不是清空 |
| ST / 停牌 | 默认过滤 | 不要求这些列 |
| 预览必填列 | 日期、代码、ST、停牌 | 只要日期、代码 |
| 买卖价 | 可用未复权 open/close | 只用 `open`/`close` |
| 基准 | `000300.SH` | 单独登记，例如 `BTC-USDT`，禁止默默用沪深300 |
| K 线代码 | `^\d{6}\.(SZ\|SH)$` | 数字货币自己的代码规则 |

### P1 全局因子 ID 与公式包

`sync_factor_versions()` 把 v1 写死到 `ds_canonical_market`。A 股公式包用 `hfq_close`、股息、市值排名。数字货币没有后复权和股息；市值可以用，价格动量应作用在 `close` 上。

**修正：** 数字货币因子 `factor_crypto_<field>`，`dataset_id=ds_canonical_crypto`。  
允许的第一批：价格动量/位置（读 `close`）、市值截面排名、份数相关。  
A 股 `canonical_pack_factors.parquet` 不动；币的 sidecar 放 `data/derived/crypto/`。

### P1 日历与 24h

A 股用 SSE 交易日和 `rebalance_days` 交易日计数。数字货币按 UTC 自然日几乎天天有 bar。混用 A 股日历会错位再平衡。

**修正：** `ds_canonical_crypto` 自带 `calendar_id=utc_daily`。回测禁止跨数据集借用 A 股日历。

### P2 局域网同步与拷库

同名 `canonical.parquet` 会被 8766 覆盖。SQLite 不能当资产载体搬机器。

**修正：** 文件名 `canonical_crypto.parquet`。目标机本机库 INSERT。程序仍 `git pull`。

## 回测与宽表：一次回测只走一张表

作业已经按运行里的 `dataset_id` + `dataset_version_id` 读 `dataset_versions.path`。工作台把数据集锁在所选因子上，默认 A 股。

所以：**不要拆 `canonical.parquet`。** 数字货币另起一张表。选 `factor_crypto_*` 后，那次回测改读新文件；未选时仍默认 A 股，旧回测不受影响。不要把 A 股和币拼进同一次回测。

## 修正后的入库形状

```text
data/
  canonical.parquet                     # A 股，原地不动
  canonical_crypto.parquet              # 数字货币，仅最小字段
  derived/
    canonical_pack_factors.parquet        # 仅 A 股
    composite_pack_factors.parquet
    crypto/                             # 仅数字货币 sidecar
```

`datasets.json` 增一条，**不改** `ds_canonical_market`：

```text
entity_id: ds_canonical_crypto
name: 数字货币行情宽表
path: canonical_crypto.parquet
metadata.market_scope: crypto
metadata.calendar_id: utc_daily
metadata.default_benchmark: BTC-USDT   # 入库时按实际基准改
```

## 入库前检查清单

1. `init-db` 已发布行不改 `path`；路径能重定位。
2. 回测在 `ds_canonical_crypto` 上允许 `stock_scope=数字货币`，且缺涨跌停/ST 时不把样本清空（A 股路径回归保持原样）。
3. 新文件相对 `data/`，不与 `canonical.parquet` 同名。
4. `(trade_date, ts_code)` 在币表内唯一；与 A 股代码集交叉只报告，不合并。
5. 表里没有 A 股专用列，也不填 0。
6. LAN 同步不会覆盖对端 `canonical.parquet`。
7. 目标机本机库 INSERT，禁止覆盖 `quantlab.sqlite3`。

## 建议实施顺序

1. 闸门：已发布行不改 path（本分支已做）。
2. 回测/预览/K 线按 `market_scope=crypto` 关掉 A 股过滤器；`stock_scope` 放开 `数字货币`。没有这项，宽表入库了也跑不成回测。
3. 登记 `ds_canonical_crypto` 与最小 schema。
4. 数字货币因子命名空间 + `derived/crypto/` sidecar。
5. 最后才落 `canonical_crypto.parquet`。

## 明确不做

- 不把数字货币行混进现有 A 股宽表
- 不把现有 `canonical.parquet` 拆目录、改名或改已发布 path
- 不把 A 股和数字货币拼进同一次回测
- 不给币表编造涨跌停、ST、停牌、复权、股息、PE
- 不把数字货币默默换成 `000300.SH` 基准或 SSE 日历
- 不在本阶段做股+币混合组合
- 不开放公网 bind、不同步 Token/机器码
