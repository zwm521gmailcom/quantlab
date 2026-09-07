# QuantLab 研究总览实施计划

**目标：** 建立 `/` 研究总览，聚合真实数据状态、最近研究、最近回测和待处理质量问题，不展示静态伪数据。

**依赖：** `00-foundation`。

## 数据与接口

- `GET /api/overview` 返回数据集数量、已发布因子数、策略数、最近运行、运行状态统计和质量告警。
- 所有卡片必须由 SQLite 与数据注册表查询生成；无记录时显示空状态。
- 最近运行使用统一投影 `run_type/run_id/name/status/created_at/finished_at`。`research` 跳转 `/research/runs/{id}`，`model_training` 跳转 `/models/runs/{id}`，`backtest` 跳转 `/backtests/runs/{id}`。

## 实施任务

- [ ] 先写 `tests/quantlab/test_overview_service.py`，覆盖空库、三类运行混合状态、最近运行排序、正确详情 URL 和质量告警。
- [ ] 实现 `quantlab/services/overview.py` 与 `quantlab/api/routes/overview.py`。
- [ ] 先写 `tests/quantlab/test_overview_page.py`，再实现 `quantlab/web/pages/overview.html` 与页面 JS。
- [ ] 复用共享导航、加载/空/错误组件，不复制页面壳。
- [ ] 将原型中的指标和清单替换为 API 数据；禁止硬编码收益率或状态。

## 验证与审批

- [ ] `pytest tests/quantlab/test_overview_service.py tests/quantlab/test_overview_page.py -q`
- [ ] Ruff 通过；浏览器验证空状态、真实记录跳转、API 失败状态和控制台无错误。
- [ ] 主代理核对总览数字可由底层查询逐项复算后，方可进入下一任务。
