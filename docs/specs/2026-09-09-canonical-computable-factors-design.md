# 宽表可算因子包设计

日期：2026-09-09
状态：已确认（未实现）
数据：`data/canonical.parquet`（32 列，约 1028 万行，2016-10-10 — 2026-08-31）

## 已确认决策

- 第一批 18 个单列公式全部生成草稿（价格位置三类先入库做诊断，发布时再筛）。规模截面含流通市值与总市值排名。
- 第二批 4 个两列公式同期生成，不推迟。合计 22 条。
- 落地方式：固定公式包一条龙——按现有因子格式计算、验证、发布入库。
- 计算窗口：数据版本 `date_max` 所在自然年年初至 `date_max`（当前约 2026-01-01 — 2026-08-31）。
- 不自动进回测组合，不改标签，不改 canonical，不改训练/成交语义。
- 不走自动挖掘随机搜索。

## 目标

在不改宽表、不引入新数据源的前提下，把 22 个公式按现有因子库格式算完、验证后发布入库。因子数据页能直接看到 IC，回测组合可选这些已发布版本。

## 约束

- 点时：只使用该股票当日及此前有效观测；停牌不补价。
- 窗口按该股票有效交易日计数，不是自然日。
- 标签仍是 `t+1 open → t+2 close`。
- 手工表达式：字段、数字、`+ - * /`、以及作用在**列名**上的单算子。不能链式 `a.pct_change(1).rolling_std(20)`。
- 已入库 7 个因子不重复生成。
- 一条龙只读最后一年（加 warmup），不并行开进程池，不把 22 条同时装进内存。
- 库表 `direction` 只允许 `positive` / `negative`。研究假设为「不定」的因子入库时记 `positive`，以计算任务 IC 为准。

## 不做

| 列 / 能力 | 原因 |
|---|---|
| `ts_code` / `trade_date` / `exchange` | 主键，不是信号 |
| `open/high/low/close` | 价格类只用 `hfq_*` |
| `adj_factor` | 复权系数 |
| 涨跌停价 | 成交规则 |
| `is_suspended` / `eligible` / `st_status` | 过滤条件 |
| `list_date` 上市天数 | 没有日期差算子 |
| `total_mv` / `circ_mv` / `turnover_rate` / `dv_ttm` | 已有换算后的入库字段 |
| 20/60 日收益波动 | 需要链式 `pct_change` 再 `rolling_std` |
| 自动挖掘随机搜索 | `seed` + `max_candidates` 抽不齐本清单；相关剪枝会丢掉价格位置三类 |

行业、ROE、分析师、资金流向宽表没有，不编造。

## 已入库（跳过）

`pe_ttm`、`total_market_cap`、`float_market_cap`、`dividend_yield_ratio`、`turn`、`momentum_5`、`volatility_5`。

实体 ID 为 `factor_<字段名>`。本包新因子同样使用该规则。

## 第一批：单列公式（18）

建议方向只是研究假设，入库后以诊断 IC 为准。

`pct_change(n)` 与已入库 `momentum_5` 同口径：`hfq_close[t] / hfq_close[t-n] - 1`。

### 动量 / 反转

| 字段名 | 公式 | 名称 | 假设方向 |
|---|---|---|---|
| `momentum_1` | `hfq_close.pct_change(1)` | 1 日涨跌幅 | 反向 |
| `momentum_10` | `hfq_close.pct_change(10)` | 10 日动量 | 正向 |
| `momentum_20` | `hfq_close.pct_change(20)` | 20 日动量 | 正向 |
| `momentum_60` | `hfq_close.pct_change(60)` | 60 日动量 | 正向 |

不重复 5 日。

### 价格位置

三类高度相关。先全算做诊断，发布时每窗口可只留一类。

| 字段名 | 公式 | 名称 | 假设方向 |
|---|---|---|---|
| `close_bias_20` | `hfq_close.rolling_bias(20)` | 20 日均线偏离 | 正向 |
| `close_bias_60` | `hfq_close.rolling_bias(60)` | 60 日均线偏离 | 正向 |
| `close_zscore_20` | `hfq_close.ts_zscore(20)` | 20 日价格标准化 | 正向 |
| `close_zscore_60` | `hfq_close.ts_zscore(60)` | 60 日价格标准化 | 正向 |
| `close_ts_rank_20` | `hfq_close.ts_rank(20)` | 20 日价格分位 | 正向 |
| `close_ts_rank_60` | `hfq_close.ts_rank(60)` | 60 日价格分位 | 正向 |

不做 `hfq_close.rolling_std`：那是价格水平波动，与 `volatility_5`（收益波动）不是一回事。

### 流动性

| 字段名 | 公式 | 名称 | 入库 direction |
|---|---|---|---|
| `amount_zscore_20` | `amount.ts_zscore(20)` | 20 日成交额标准化 | positive（假设不定） |
| `vol_mean_20` | `vol.rolling_mean(20)` | 20 日均量 | positive（假设不定） |
| `amount_cs_rank` | `amount.cs_rank(0)` | 成交额截面排名 | positive（假设不定） |
| `turn_cs_rank` | `turn.cs_rank(0)` | 换手率截面排名 | 正向 |

### 估值 / 规模截面

| 字段名 | 公式 | 名称 | 假设方向 |
|---|---|---|---|
| `pe_ttm_cs_rank` | `pe_ttm.cs_rank(0)` | 市盈率截面排名 | 反向 |
| `float_mv_cs_rank` | `float_market_cap.cs_rank(0)` | 流通市值截面排名 | 反向 |
| `total_mv_cs_rank` | `total_market_cap.cs_rank(0)` | 总市值截面排名 | 反向 |
| `div_yield_cs_rank` | `dividend_yield_ratio.cs_rank(0)` | 股息率截面排名 | 正向 |

`pe_ttm` 约 19% 空、股息率约 32% 空：空值剔除，不向前填充。覆盖率低于价格类视为预期。

## 第二批：两列公式（4）

现有解析器支持，自动挖掘拼不出来。

| 字段名 | 公式 | 名称 | 入库 direction |
|---|---|---|---|
| `intraday_range` | `(hfq_high - hfq_low) / hfq_close` | 日振幅 | positive（假设不定） |
| `overnight_ret` | `hfq_open / hfq_close.shift(1) - 1` | 隔夜收益 | 正向 |
| `close_location` | `(hfq_close - hfq_low) / (hfq_high - hfq_low)` | 收盘位置 | positive（假设不定） |
| `trade_vwap` | `amount / vol` | 成交均价 | positive（假设不定） |

除零、无穷转为缺失。`trade_vwap` 只作相对比较。

## 明确推迟

- 20/60 日收益波动、上市天数、VWAP 相对收盘、量价背离。
- 1/2/5 短窗再扫一遍（与已有 5 日因子高度相关）。

## 架构

固定公式表在代码里。一条龙对**每一条**顺序执行（不并行、不复制整表）：

1. **设计**：稳定 ID `factor_<字段名>`，公式、方向、分类写入 `FactorVersion`（先草稿）。
2. **计算**：现有 `FactorCalculationService.run`，窗口为最后一年；60 日窗口用年前数据做 warmup，与 `momentum_5` 同一套点时/标签。
3. **验证**：计算任务成功；有效交易日 ≥ 5；覆盖率 ≥ 门槛（默认 0.95；`pe_ttm_cs_rank` / `div_yield_cs_rank` 为 0.60）。**不因 IC 正负拒绝**。
4. **入库**：验证通过则 `quality_status=passed` 并 publish；结果写入 `factor_calculation_runs`。失败留草稿并记录原因，继续下一条。
5. 已发布同 ID：跳过，不覆盖。

不要把这 22 条写进 `FEATURE_FIELDS`。新因子走 `_formula_frame`。市场默认沪深（SH/SZ）。

页面一次点「计算验证并入库」，前端按字段逐条 POST，便于进度和释放内存。

## API 与页面

- `GET /api/factor-packs/canonical`：22 条定义 + 库内状态。
- `POST /api/factor-packs/canonical/ingest`：`field` 指定一条；省略则顺序跑完全包。默认数据集 `ds_canonical_market` / `current`。
- 页面：`/factors/new/manual`。不改回测页，不自动勾进回测组合。

## 错误处理

- 单条失败不影响其余。
- 数据集未发布：400。当前宽表若为 `needs_review`，公式包在计算验证通过后仍可发布。
- 公式求值把 `inf` / `-inf` 变成缺失。

## 验收

- 公式只读 canonical，不写回 parquet。
- 通过验证的因子 `status=published`，因子数据页有该年计算任务和 IC。
- 与 `momentum_5` 重叠的 5 日涨跌不在包内。
- 不足窗口为空；不停牌补价。
- 不自动进回测组合。
