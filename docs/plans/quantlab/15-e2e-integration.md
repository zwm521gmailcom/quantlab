# QuantLab 全链路集成与验收计划

**目标：** 验证“数据登记 → 因子定义/挖掘 → 策略版本 → 模型训练 → 回测 → 结果档案”的完整本地链路可复现、可追溯且不修改现有数据。

**依赖：** `00` 至 `14` 全部通过主代理审批。

## 端到端场景

- 场景 A：登记正式 HFQ+ST 数据，浏览一只股票 K 线并核对字段。
- 场景 B：用手动页面重建 `momentum_5`，诊断后发布新版本。
- 场景 C：自动挖掘运行生成候选，选择一个转为草稿但不自动发布。
- 场景 D：建立单因子 LightGBM LambdaRank 近似模型策略，训练 1 年、测试 1 年。
- 场景 E：提交 2020 回测，查看结果、Artifact、日志，复制配置但不运行。
- 场景 F：失败任务显示明确原因，修正后新建运行 ID，历史失败记录保留。
- 场景 G：K 线页切换原始/qfq/hfq；因子数据页排除 target-only；因子库进入详情；研究运行进入 `/research/runs/{id}`；模型中心查看模型版本和训练详情；策略中心锁定模型版本；设置仅影响新草稿。

## 实施任务

- [x] 建立 fixture 与独立测试数据库，注入小型只读 Parquet 副本到临时目录，禁止测试写入权威数据目录。
- [x] 使用 `.venv/bin/pytest tests/quantlab -q`、`.venv/bin/ruff check quantlab tests/quantlab`；API 集成测试覆盖本地路由契约。
- [x] 对关键结果做复算：配置冻结、版本绑定、训练/测试过滤参数、交易成本和未成交策略均在回测配置中保留。
- [x] 保存运行环境、配置、数据/因子/模型/策略版本与代码哈希。
- [x] 使用 `quantlab_runtime/baselines/authoritative-data.json` 和 `python -m quantlab.cli verify-data-baseline` 执行实施前后大小、mtime、manifest/footer/schema 哈希对比。
- [x] 生成 `docs/quantlab/acceptance-report.md`，逐页面记录证据、未解决差异和适用边界。

## 最终审批门

- [x] 所有已纳入本地 API 的页面返回正式页面；集成测试覆盖关键页面与 API。
- [x] 任一结果可从运行 ID 追到配置、版本、日志、明细和 Artifact。
- [x] 模型明确标记为 BQ StockRanker 的实用近似，不宣称 100% 等价。
- [x] 完整 K 线和因子数据仍位于原目录且内容不变。
- [x] 验收正式数据精确绑定、训练/测试过滤、买卖价格、全部成本与未成交策略。
- [x] 验收结果清单状态并入记录、ID/名称上下同列、保存总目录置顶、输出文件中文名称，以及复制配置不运行。
- [x] 主代理逐项签字后，系统才标记为可用。
