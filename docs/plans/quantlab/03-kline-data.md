# QuantLab K 线数据管理实施计划

**目标：** 建立 `/data/kline`，管理和检查原始/后复权 K 线，不复制全量数据，不把展示字段误写成真实字段。

**依赖：** `00-foundation`、`02-data-center`。

## 口径

- 正式持久化字段统一为 `raw_open/raw_high/raw_low/raw_close` 与 `hfq_open/hfq_high/hfq_low/hfq_close`。
- 复权因子字段为 `adj_factor`；数据按 `trade_date, ts_code` 唯一。
- 默认读取正式版本 `ds_hfq_market_st_v1@20260830T173152Z-50e42e72`，版本必须显式可切换。
- 前复权不是现有持久化字段，仅供浏览：对每只股票按所选不可变版本的最后一个有效因子归一化，`qfq_price = raw_price * adj_factor / terminal_adj_factor`；不得用于历史模型输入。后复权以正式 `hfq_*` 字段为准，不用未经验证的乘法覆盖它。

## 实施任务

- [x] 写 `KlineQueryService` 测试：股票、日期、版本、分页、字段选择、唯一键和越界限制。
- [x] 实现基于 PyArrow dataset 的列裁剪/分区过滤，禁止整表载入内存。
- [x] 提供 `/api/kline/query`、`/api/kline/summary`、`/api/kline/quality`。
- [x] 页面实现标的搜索、日期区间、原始/前复权/后复权切换、OHLCV 表、K 线图和质量摘要。
- [x] 图表读取 API 下采样结果；表格提供受限 CSV 即时下载，不创建 Artifact。
- [x] 显示可用的 ST、涨跌停和无价格状态；不以前向填充伪造交易价格。

## 验证与审批

- [x] 对 `000001.SZ`、`000002.SZ`、`600000.SH` 验证正式 `hfq_*` 与其生成 manifest；按上述终点归一化公式抽样复算只读 `qfq_*` 展示值。
- [x] 验证字段名和日期边界；质量接口核对正式 manifest、必需字段和唯一键。
- [x] 浏览器检查图表与表格同源；查询只取受限列/批次，图表使用 API 下采样结果，不整表载入内存。
