# QuantLab 手动建立因子实施计划

**目标：** 建立 `/factors/new`，通过受控表达式、真实字段和预览诊断创建因子草稿并发布版本。

**依赖：** `06-factor-library`、`07-factor-detail`。

## 安全与语义

- 不使用 Python `eval/exec`；表达式解析为白名单 AST。
- 语法仅允许数字常量、登记字段、圆括号、`+ - * /`、比较、布尔组合和登记函数；每个函数定义参数数量/类型、输出类型、最小窗口与最大窗口，rolling/shift 必须按股票分组且只允许非负历史窗口。
- 时间窗口必须声明 `per_instrument_observation` 或 `market_calendar`；默认沿用现有 `shift(5)` 观察步长。
- 估值缺失和技术因子缺失策略分别配置并版本化。
- 基本面字段必须按公告日做 as-of join，公告前不可见；除零、无穷和溢出统一转为缺失，不允许不同执行路径选择不同语义。

## 实施任务

- [x] 先写表达式解析器安全、类型、窗口和越界测试。
- [x] 实现定义表单、字段浏览器、公式校验和小样本预览 API。
- [x] 实现草稿保存、运行诊断、发布三个独立动作；发布要求诊断质量门通过。
- [x] 生成 FactorVersion、代码哈希、输入血缘和研究运行。

## 验证与审批

- [x] 恶意表达式、未来引用和未知字段必须拒绝；除零/无穷/溢出统一转换为缺失。
- [x] 预览按登记 DatasetVersion 读取并按股票分组计算窗口；不写入外部权威数据目录。
- [x] 浏览器验证手动建立页面、公式预览入口和错误提示；草稿保存、诊断、发布为独立动作。

## 实现与验收记录

- `quantlab/services/factor_manual.py` 使用 Python AST 白名单，只允许登记字段、数字、算术/比较/布尔运算以及 `shift`、`rolling_mean` 非负窗口；不使用 `eval`、`exec`、任意 Python 或 SQL。
- `POST /api/factor-drafts/preview` 执行真实 Parquet 小样本预览，`POST /api/factor-drafts` 创建手动 FactorVersion 草稿，`PATCH /api/factor-drafts/{entity_id}/{version_id}` 重新校验后保存，诊断和发布分开执行。
- 发布仅接受 `quality_status=passed`；修改草稿会重置为 `needs_review`，已发布版本仍由既有 FactorRepository 不可变触发器保护。
- 验收：任务 08 专项测试 8 个通过，浏览器页面测试 1 个通过；随后全量 `tests/quantlab` 155 个通过，Ruff 和 `git diff --check` 通过。
