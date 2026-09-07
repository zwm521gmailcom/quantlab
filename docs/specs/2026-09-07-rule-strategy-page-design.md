# 规则回测独立页面

日期：2026-09-07  
状态：已确认  
取代：`docs/specs/2026-09-06-rule-strategy-warehouse-design.md` 中「回测中心把规则策略塞进信号来源下拉」这一段。后端口径（信号表、成分、账户）不变。

## 目标

模型回测和规则回测分成两个入口。用户不再在「模型与信号」里切换「规则策略」。

## 非目标

- 不做策略 IDE、不贴任意 Python。
- 不做策略仓库列表页（这一期只有一张规则回测表单 + 一个内置模板）。
- 不改结果档案的存储：仍是 `backtest_runs`。
- 不改 Wiki 信号与账户口径。

## 导航

- 侧栏在「回测中心」下增加 **规则回测**，路由 `/backtests/rules`。
- `/backtests/new`（回测中心）只服务模型 / 因子排序：去掉「信号来源」和规则参数面板。
- 研究总览可加「规则回测」快捷入口，非必须。

## 规则回测页 `/backtests/rules`

只保留与规则运行有关的字段：

- 回测名称
- 研究数据版本（默认 `ds_canonical_market` / current）
- **一个**回测区间（不要训练区间、不要因子组合、不要树/线性超参、不要「信号来源」）
- 策略下拉：现仅 `wiki_trend_follow`（文案：Wiki 多指标趋势跟踪）
- 只读规则说明
- 可改：`up_pct_20`、`up_pct_60`、`rsi_low`、`rsi_high`、Top N、调仓间隔、止损、止盈、最长持有
- 买卖费率：出厂买 0.03%、卖 0.13%、印花税 0、最低 5 元
- 保存草稿、开始回测

提交 `kind=rule_signal`、`account_mode=target_weight_exits`、`open_when_benchmark_gt_ma200: false`。

## 跳转与失败

- 缺沪深300或中证500 `index_weight` → 运行 `failed`，中文提示先去数据中心下载。
- 运行记录、结果档案沿用；配置快照必须能看出是规则策略。
- 从运行记录复制规则配置 → `/backtests/rules?draft_id=…`，不得打开 `/backtests/new`。

## 实现边界

- 复用现有 `validate_rule_config` / `execute_rule_signal`。
- 新页面（或从 formal 页拆出），不要继续在模型表单上用 `data-kinds` 开关伪装。
- 浏览器测试改为打开 `/backtests/rules`，不再在 `/backtests/new` 上选「规则策略」。
