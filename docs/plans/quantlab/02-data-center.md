# QuantLab 数据中心实施计划

**目标：** 建立 `/data` 数据资产总目录，统一展示数据集、版本、质量、路径别名和更新时间，并进入 K 线或因子数据明细。

**依赖：** `00-foundation`。

## 数据与接口

- `GET /api/datasets` 支持类别、状态、质量状态、日期范围和关键字筛选。
- `GET /api/datasets/{dataset_id}/versions` 展示不可变版本；CURRENT 仅作为当前指针。
- 页面只显示路径别名和受控相对路径，不暴露任意文件读取能力。
- 首批目录必须覆盖 source tables、canonical、features 和正式 HFQ+ST 数据集。
- 配置固定登记 `/Volumes/T2/vnpy/tushare_migration_data` 与 `/Volumes/T2/vnpy/tushare_migration_calibration`；正式版本 `ds_hfq_market_st_v1@20260830T173152Z-50e42e72` 锁定 8,204,633 行、2016-10-10 至 2024-12-31、`47e9c6968ee856136fea128aa49a76d0c6976afa63d99066744ab0b717f02d2a` manifest 哈希和实际 Schema。

## 实施任务

- [ ] 写数据目录仓储与筛选失败测试。
- [ ] 实现分页、排序、版本状态、Schema 摘要和质量告警 API。
- [ ] 写页面失败测试后实现目录表、筛选、版本抽屉和进入明细按钮。
- [ ] 增加“重新扫描元数据”，只更新草稿扫描结果，不修改 Parquet；已发布版本的大小、mtime、manifest 或 Schema 变化时标记 `needs_review` 并禁止新研究/训练/回测引用，人工确认后创建新 DatasetVersion，绝不改写历史版本。
- [ ] 区分权威数据、派生数据和校准实验，实验不得自动发布。

## 验证与审批

- [ ] 测试筛选、版本锁定、CURRENT 变化不改历史版本、路径越界拒绝。
- [ ] 浏览器核对行数、日期、字段来自真实 metadata。
- [ ] 对权威数据目录做实施前后大小、mtime、manifest 哈希对比。
