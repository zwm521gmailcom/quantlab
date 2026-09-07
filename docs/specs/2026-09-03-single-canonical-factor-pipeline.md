# Single Canonical Factor Pipeline 设计

日期：2026-09-03
状态：已确认，开始执行

## 目标
- canonical.parquet 是唯一持久化行情宽表。
- 不再维护全局 features/multifactor.parquet。
- 因子研究/IC/单因子宽表按需从 canonical 生成。
- 多因子宽表未来若固化，作为独立可选产物。

## 数据层
- canonical.parquet：标准行情宽表（32 字段，无 qfq）。
- 原始 raw：只读下载层。
- 当前 features.parquet 先保留为 legacy，直到因子服务全部切换完成后再归档。

## 因子服务改造
1. FactorDataService：
   - 目录仍来自 factor_versions 定义；
   - 物理数据从 canonical 读取；
   - momentum_5 / volatility_5 在查询/汇总时按版本公式计算；
   - 估值因子直接从 canonical 对应字段映射。
2. FactorCalculationService：
   - 读取 canonical；
   - 按因子公式生成因子列；
   - 按标签定义（t+1 open → t+2 close）生成 future_return；
   - IC/分组/分布从生成结果计算；
   - 计算结果写入 factor_calculation_runs 与 results 文件夹。
3. Kline：已切换 canonical。

## 结果文件规则
data/results/factor_calculations/<calculation_id>/summary.json

## 验收
- momentum_5 IC 与旧 features 结果一致；
- 因子目录/明细不依赖 features.parquet；
- canonical 文件不被研究进程改写；
- features.parquet 可删除后系统仍正常工作。
