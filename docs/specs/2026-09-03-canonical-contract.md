# Canonical 标准行情宽表字段契约（v2026）

日期：2026-09-03
数据覆盖：2016-10-10 — 2026-08-31
目标：新版 canonical 作为 K线、因子、回测的唯一行情来源，不再单独保存 K线快照。

## 原则
- 原始行情只读，来自 raw 按日文件。
- 正式复权口径只用后复权（hfq）。
- 不落盘前复权（qfq）字段；页面口径仅原始 raw 与后复权 hfq，不提供 qfq 展示。
- 每个字段为每日每股票一行，主键 trade_date + ts_code。

## 字段表
| 字段 | 中文 |
|---|---|
| ts_code | 股票代码 |
| trade_date | 交易日期 |
| open | 开盘价（原始） |
| high | 最高价（原始） |
| low | 最低价（原始） |
| close | 收盘价（原始） |
| vol | 成交量 |
| amount | 成交额 |
| pe_ttm | TTM 市盈率 |
| total_mv | 总市值（万元原始） |
| circ_mv | 流通市值（万元原始） |
| dv_ttm | TTM 股息率（百分比原始） |
| turnover_rate | 换手率（百分比原始） |
| total_market_cap | 总市值（元） |
| float_market_cap | 流通市值（元） |
| dividend_yield_ratio | 股息率（小数） |
| turn | 换手率因子 |
| adj_factor | 复权因子 |
| hfq_open | 后复权开盘 |
| hfq_high | 后复权最高 |
| hfq_low | 后复权最低 |
| hfq_close | 后复权收盘 |
| up_limit | 涨停价（原始） |
| down_limit | 跌停价（原始） |
| hfq_up_limit | 后复权涨停 |
| hfq_down_limit | 后复权跌停 |
| st_status | ST 状态（0 正常/1 ST/2 *ST） |
| is_suspended | 停牌标记 |
| eligible | 可入池 |
| exchange | 交易所 |
| list_date | 上市日期 |
| delist_date | 退市日期 |

## 计算口径
- hfq_price = raw_price × adj_factor
- hfq_up_limit = up_limit × adj_factor
- hfq_down_limit = down_limit × adj_factor
- total_market_cap = total_mv × 10000
- float_market_cap = circ_mv × 10000
- dividend_yield_ratio = dv_ttm ÷ 100
- ST 稀疏表未出现的股票按 st_status=0 填充
- 前复权不在本宽表中持久化，页面也不提供 qfq 口径

## 验证
- 新旧重叠区间与旧 canonical hfq_close 完全一致
- 2025-2026 行数与 trade_cal 交易日数一致
- 不删除旧数据直到新版本回归通过
