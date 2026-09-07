# QuantLab 策略中心实施计划

**目标：** 建立 `/models` 与 `/strategies`，先管理模型定义、训练运行和模型版本，再版本化管理因子组合、过滤、训练/测试规则、仓位与撮合配置。

**依赖：** `06-factor-library`、`09-factor-auto-mine`。

## 策略版本内容

- 股票范围、训练/测试窗口、标签、训练过滤、测试过滤。
- 有序 `factor_version_id` 列表与方向；模型版本或模型训练配置。
- Top N、权重方式、调仓频率、信号时点、买卖时点、价格字段、手续费、滑点、基准。
- 所有引用均锁定不可变版本；策略修改创建新版本。

## 实施任务

- [x] 写 Model/ModelVersion/ModelTrainingRun 注册、训练状态、Artifact 和版本发布测试。
- [x] 实现 `/models`、`/models/{model_id}/versions/{version_id}`、`/models/runs/{run_id}` 页面及 API；模型训练锁定 DatasetVersion、FactorVersion[]、标签、过滤、预处理、超参数、随机种子和代码哈希。
- [x] 训练运行按独立 run 记录配置；未创建回测撮合逻辑，留待任务 11 接入。
- [x] 写 Strategy/StrategyVersion 创建、复制、校验、发布和差异测试。
- [x] 实现策略清单、版本详情、配置差异和复制接口。
- [x] 实现策略详情编辑入口，按因子、模型、过滤、仓位、执行和基准保存完整配置。
- [x] “送入回测”复制完整配置到回测草稿，不自动运行。
- [x] 清单显示名称/ID、版本、状态、因子数和模型引用。

## 验证与审批

- [x] 验证不存在/已弃用依赖不能发布，版本内容哈希稳定。
- [x] 浏览器确认策略复制和送入回测保留所有配置且不运行。

## 任务 10 验收记录

- 提交：`quantlab/services/strategy_center.py`、策略中心 API、模型/策略页面、数据库迁移与专项测试。
- 约束：模型训练配置锁定 DatasetVersion、精确 FactorVersion、标签、三段时间区间、过滤、预处理、超参数、随机种子和代码哈希。
- 状态：训练运行遵守 queued → running → completed/failed；模型版本发布前必须存在 completed 训练运行；已发布版本由数据库触发器和服务层共同保护。
- 回测边界：策略版本送入回测只写可追溯草稿，不创建或启动 `backtest_runs`。
- 验证：任务 10 专项 4 passed；`tests/quantlab` 172 passed；浏览器测试 24 passed；Ruff 与 `git diff --check` 通过。
