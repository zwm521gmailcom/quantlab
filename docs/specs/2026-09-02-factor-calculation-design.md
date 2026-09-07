# 因子 IC 计算与历史留档设计

日期：2026-09-02
页面：`/data/factors`、因子版本详情

## 存储

新增 `factor_calculation_runs` 表，记录：

- `calculation_id`（自增流水号，页面显示为 `#1`、`#2`）
- 因子实体/版本、数据集/数据版本
- 计算周期 `date_from/date_to`
- 状态、开始/完成时间、错误
- `params_json` 与 `summary_json`
- 行数、覆盖率、缺失行、IC 均值、IC 正值比例、IC 标准差、换手率、有效交易日

同一因子可多次运行；每次计算独立留档，目录只取最新一次，详情展示全部历史。

## 计算口径

- IC：每日按截面把因子值与 `future_return` 做秩相关（Spearman），得到每日 IC；
- IC 均值/IC 正值比例/IC 标准差由全部有效交易日汇总；
- 换手率按每日因子 Top 20% 组合与前一日成分差异计算；
- 覆盖率 = 计算周期内（因子非空且标签非空行数 ÷ 该周期原始行数）；
- 目录表的“注册覆盖率”仍表示整张 `features.parquet` footer 口径。

## API

- `POST /api/factor-calculations`：按 factor/version/周期发起计算；
- `GET /api/factor-calculations`：因子全部历史；
- `GET /api/factor-calculations/latest/{factor_id}`：最新一条；
- `GET /api/factor-calculations/{calculation_id}`：单条详情。

## 因子文件位置

因子目录与版本详情展示“因子文件位置”，值为注册路径别名（如
`data/features.parquet`），不暴露任意绝对路径。

## 首条真实结果

`momentum_5`，周期 2024-01-01 — 2024-12-31，流水号 `#1`：

- 行数 1,222,473，覆盖率 99.13%，缺失 10,729；
- IC 均值 -0.0123，IC 正值比例 46.7%，IC 标准差 0.2218；
- 换手率均值 0.323，有效交易日 240。
