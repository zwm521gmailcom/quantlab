# QuantLab 端到端集成验收报告

日期：2026-09-02

## 验收范围

本次验收覆盖本地单体服务的页面路由、API 契约、元数据持久化和回测提交边界。测试使用临时目录、临时 SQLite 与小型 Parquet fixture，不读取或写入权威数据目录。

## 证据

- `tests/quantlab/test_e2e_integration.py`：覆盖因子库/因子详情、手动/自动研究、策略中心、回测中心、结果档案、运行设置页面及对应 API。
- 回测提交验证配置冻结、数据/因子/模型/策略版本绑定、SH/SZ 范围、Top N、调仓间隔、HFQ 开收盘、手续费下限、训练/测试区间和过滤规则。
- 复制结果配置只创建回测草稿，不增加 `backtest_runs`，不触发执行。
- `ResultArchiveService`/运行记录使用数据库指标、DAG、日志和 Artifact 注册表；不扫描 `trades` 明细。
- `snapshot-data-baseline` 与只读 `verify-data-baseline` 比较文件大小、mtime、JSON 内容哈希及 Parquet footer/schema 信息。
- 最新验证：`197 passed in 22.88s`；Ruff 与 `git diff --check` 通过。

## 页面与链路结论

数据注册 → 因子定义/研究 → 策略与模型版本 → 回测配置冻结/提交 → 运行记录 → 结果档案 → 设置页的关键入口已连接。失败运行保留失败原因、部分指标、失败/跳过 DAG 步骤和已生成 Artifact。BQ `StockRanker` 仍按设计标记为本地实用近似，不宣称 100% 等价。

## 边界与未覆盖项

浏览器真实 Uvicorn 启停、无控制台错误和完整真实数据五年回测不在本次单元/API fixture 验收中；应在发布前用本机浏览器做一次 smoke check。正式行情、因子宽表和历史数据仍由外部权威目录提供，QuantLab 只登记并只读引用。
