# QuantLab 因子数据管理实施计划

**目标：** 建立 `/data/factors`，检查因子矩阵覆盖率、分布、缺失和版本来源，作为研究与回测的唯一因子数据入口。

**依赖：** `00-foundation`、`02-data-center`、`03-kline-data`。

## 数据与接口

- 正式基础矩阵来自 `features.parquet`，首批包含 `pe_ttm/total_market_cap/float_market_cap/dividend_yield_ratio/turn/momentum_5/volatility_5`。
- 因子定义与因子值分离；所有值必须关联 `factor_version_id` 与 `dataset_version_id`。
- `GET /api/factor-data/query|summary|quality` 支持因子、股票、日期、版本与分页。
- 建立显式物理映射表：七个预测字段分别映射到首批 `FactorVersion` 和 `features.parquet` 的同名列，统一关联正式 DatasetVersion；`label/future_return` 及任何未来字段标记为 target-only，禁止出现在特征选择器。
- `momentum_5 = hfq_close[t] / hfq_close[t-5 instrument observations] - 1`；`volatility_5` 按正式 manifest 口径；其余五个因子保存来源字段、方向、频率、缺失规则与点时规则。

## 实施任务

- [ ] 写因子矩阵查询和列白名单测试，禁止用户输入成为任意列/SQL，明确测试 target-only 字段不能作为预测输入。
- [ ] 实现覆盖率、缺失率、分位数、极值、每日截面数量和重复键诊断。
- [ ] 页面把因子公式、版本、方向、数据源、覆盖率、缺失行、状态合并进目录列；不保留右侧“当前选择/详情”双卡片。
- [ ] 单击因子进入独立详情页；多选仅用于批量质量检查，不在本页维护回测组合。
- [ ] 提供样本导出 Artifact，限制行数并记录筛选条件。

## 验证与审批

- [ ] 与 Parquet metadata 和抽样 pandas 计算对比行数、空值、分位数。
- [ ] 验证因子版本和数据版本完整锁定、字段命名一致、无伪造覆盖率。
- [ ] 浏览器验证筛选、分页、详情跳转和空/错误状态。
