# QuantLab Implementation Plan

> **For agentic workers:** REQUIRED: Use `subagent-driven-development` (if subagents available) or `executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 规划 QuantLab 的数据管理、因子研究、策略管理、回测执行和结果追溯全链路实现。

**Architecture:** 采用 FastAPI + SQLite 的本地模块化单体；通过 PyArrow/Polars 外部只读访问现有大型 Parquet，运行时摘要由 SQLite/API 提供。实体、版本、任务状态和 Artifact 统一管理，页面通过稳定 ID/API 串联。

**Tech Stack:** FastAPI, SQLite (`sqlite3`), Pydantic, PyArrow/Polars, pytest, Playwright, 共享 HTML/CSS/JS 页面壳。

---

## 当前状态

总体状态：`implementation_in_progress`

00–15 计划文件已建立；只有通过 Luna 规格复核和主代理审批后，状态才改为 `approved`。实现完成前均不标记为 `completed`。

## 计划文件清单

| 顺序 | 文件 | 状态 | 说明 |
|---:|---|---|---|
| 00 | `00-foundation.md` | 已完成 | 基础服务、实体、状态机、注册表、任务管理、共享壳；56 项测试通过 |
| 01 | `01-overview.md` | 已完成 | 真实研究总览、质量告警和三类运行跳转；65 项测试通过 |
| 02 | `02-data-center.md` | 已完成 | 真实数据目录、版本/覆盖范围、质量状态和扫描审计；77 项测试通过 |
| 03 | `03-kline-data.md` | 已完成 | raw/qfq/hfq 查询、K线浏览、质量与受限导出；88 项测试通过 |
| 04 | `04-factor-data.md` | 已完成 | 七因子物理映射、目标隔离、受限查询、质量诊断与导出；102 项测试通过 |
| 05 | `05-factor-research.md` | 已完成 | 手动/自动入口、版本锁定研究记录、草稿复制与运行详情；117 项测试通过 |
| 06 | `06-factor-library.md` | 已完成 | 因子库页面、目录 API、质量门和生命周期契约；155 项全量测试通过 |
| 07 | `07-factor-detail.md` | 已完成 | 不可变因子版本详情、真实诊断、Artifact、版本复制与 revision 安全的回测草稿；144 项全量测试通过 |
| 08 | `08-factor-manual.md` | 已审批 | 手动建立因子 |
| 09 | `09-factor-auto-mine.md` | 已审批 | 自动挖掘因子 |
| 10 | `10-strategy-center.md` | 已完成 | 模型、训练运行、策略与策略版本；172 项全量测试通过 |
| 11 | `11-backtest-workbench.md` | 已审批 | 回测配置、门禁与运行 |
| 12 | `12-result-archive.md` | 已审批 | 回测结果清单与复制配置 |
| 13 | `13-run-record.md` | 已审批 | 回测运行详情、指标、Artifact |
| 14 | `14-settings.md` | 已审批 | 路径、环境和默认参数 |
| 15 | `15-e2e-integration.md` | 已审批 | 全链路集成验收 |

## 预定依赖图

```text
00 foundation
  ├─ 01 overview
  ├─ 02 data center
  │   ├─ 03 kline data
  │   └─ 04 factor data
  ├─ 05 factor research
  │   ├─ 06 factor library
  │   ├─ 07 factor detail
  │   ├─ 08 manual factor
  │   └─ 09 automatic factor mining
  ├─ 10 strategy center
  ├─ 11 backtest workbench
  ├─ 12 result archive
  ├─ 13 run record
  ├─ 14 settings
  └─ 15 end-to-end integration
```

## 统一设计约束

- 权威数据目录保持原位，只读使用：`/Volumes/T2/vnpy/tushare_migration_data` 与 `/Volumes/T2/vnpy/tushare_migration_calibration`。
- 标准成交和因子字段使用 `hfq_open`、`hfq_high`、`hfq_low`、`hfq_close`；`momentum_5` 使用 `hfq_close[t] / hfq_close[t-5 observations] - 1`。
- 大型 Parquet 不复制到网站；运行时目录、版本摘要和诊断摘要由 SQLite/API 动态提供，源码目录不生成运行数据。
- 因子、策略、模型、数据集和回测均使用不可变版本或运行快照；不覆盖历史记录。
- 自动挖掘只能产生候选和 ResearchRun，不能自动发布因子；回测复制配置只能载入草稿，不能自动运行。
- 所有任务保存配置、状态、错误、日志和已生成 Artifact；失败任务不静默重试。

## 代理职责与审批顺序

每个任务由一个独立代理实现；主代理负责在进入下一任务前审核：

1. 规格：接口、字段、实体关系与本计划一致；
2. 测试：TDD 红/绿步骤完成，单元、数据契约和浏览器验收通过；
3. 代码质量：边界清晰、无未授权路径访问、无 Python `eval`、日志可读；
4. 数据不变：原始/标准 Parquet 及既有 HTML 原型哈希和修改时间未变化；
5. 浏览器：页面可达、实体 ID 可传递、刷新可恢复、失败可查看、复制不自动运行。

未通过任一项，不得开始下一个任务。`00-foundation` 是第一个实现任务；其余任务按 01–15 顺序执行。实现阶段可由主代理调度 Luna 子代理，但子代理不得绕过主代理审批门。

## 实施前审批要求

每个计划需明确依赖、URL/API、输入输出实体、交互、TDD、浏览器验收、失败处理和数据边界。实现代理开始任务前，主代理还会把该计划细化为具体文件和 RED/GREEN 命令；未细化或未审批的计划不得实施。
