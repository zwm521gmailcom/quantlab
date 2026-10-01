---
title: 循序改进 Qlib 挖因子质量
date: 2026-10-01
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# 循序改进 Qlib 挖因子质量

## Goal Capsule

改进挖因子的搜索目标，使每一轮按验证段持仓质量计数，并在此之后依次对齐标签、去掉规模暴露、加算子、再做多因子。测试段继续只用于挖完后的筛选。

权威顺序：本计划的范围边界高于实现时的便利改动。已有计划文件里的轮次和已发布的测试成绩保持原样。筛选规则的比较方式保持原样。

停机条件：

- 不启动挖因子，不启动回测，不转换面板。
- 不把测试段年化、回撤或信息比率写进模型提示词。
- 不改写已有 `quantlab_runtime/qlib/plans/qlib-*.json` 的轮次。
- 没有历史行业分类时，不做行业中性。
- 标签周期的默认值保持 1 日，直到单独的重算报告出来并且用户确认后再改。

执行时一次只做一个实现单元。后一个单元依赖前一个单元的测试通过。50 轮对照挖因子由用户明确说开始后再跑，不属于任何一个实现单元的完成条件。

## Product Contract

### Summary

当前循环把「公式能算完且名字不重复」当成挖到一条。凑满 `max_loops` 后，`screen_factor_picks` 才用测试段年化和信息比率留因子。本计划把计数改成验证段持仓过线，并按公式类限制窗口微调。标签、规模中性、新算子和多因子按顺序跟在后面，每次只改一个环节。

### Problem Frame

`qlib_factor_loop.py` 的 `execute` 在公式求值成功后调用 `run_factor_strategy`。该函数用单因子 LightGBM 拟合下一期收益，只在 `valid_end` 之后出分数，再按 TopkDropout 计算测试段持仓。验证段 Spearman IC 会记下来，也写进提示词，但不决定这一轮算不算数。`patience` 只存在计划字段里，循环停机只看 `_mined_factor_count` 是否达到 `max_loops`。

提示词取最近 48 条成功公式和最近 24 条验证 IC。模型因此沿最近的公式改窗口。`formula_family` 把 `Ref`、`Mean`、`Std` 的整数窗口收成 `W`，但这只发生在挖完后的筛选里。最近一次挖满的 `qlib-d7450dadaa19` 是 1000 条成功公式，筛选后该计划留下 14 类。

持仓每天保留打分最高的 50 只、换掉最弱的 5 只。标签却是下一天收益：`close.groupby(instrument).shift(-1) / close - 1`。单因子在进模型前已经按日标准化，树模型几乎不改变横截面排序。`stock_basic` 只有代码、交易所和上市退市日，没有行业。`circ_mv` 已经在面板里，可以做规模残差。

### Requirements

- R1. 一轮计入挖到的因子，必须同时满足：公式求值成功、验证段持仓的年化高于该段等权基准、验证段信息比率大于 0。
- R2. 验证段持仓只用训练段拟合，不使用验证段标签做早停。测试段持仓仍只用 `valid_end` 之后的分数，早停仍看验证段。
- R3. 同一公式类里，后一条只有验证段信息比率高于该类已计数成员时才计数。原文相同或名字相同仍直接拒绝。
- R4. 本机模型提示词只列出已计数公式里验证段信息比率最高的 48 条，以及其中最近的成功记录。失败公式和测试段成绩不出现。
- R5. 已有计划文件的轮次、测试成绩和筛选结果保持不变。新计划用自己的标签周期计算，不和旧的 1 日成绩混成一张无标注的表。
- R6. 标签周期成为计划字段。默认仍是 1 日。重算报告比较 5 日和 10 日，用户确认前不改默认值。
- R7. 规模中性是当日横截面对 `log(circ_mv)` 的残差，发生在 IC 和 LightGBM 之前。行业中性不在本计划内。
- R8. 新算子只在 R3 的按类拒绝生效之后加入。首批是 `Corr`、`Rank`、`Delta`。
- R9. 多因子只用筛选后、验证段低相关的单因子，调用已有的 `lightgbm_multi_scores`。它不占用单因子的 `max_loops` 计数。

### Actors

- A1. 研究员在 Qlib 页面开始或继续一个挖因子计划，并在记录页查看每一轮为何计数。
- A2. 本机模型 `gpt-oss-120b` 只看见提示词里的成功公式和验证段信息，交回一个 JSON 公式。

### Key Flows

- F1. 模型交公式。原文或名字重复则在同一次尝试内退回。公式求值后先做验证段持仓。未过线则记入错误轮次且不计数，不算测试段。过线后，若验证段信息比率不高于同一公式类已计数成员，则退回且不计数。高于时才计数，并另算测试段持仓供筛选。
- F2. 实现单元完成后，研究员自行用新计划做最多 50 轮对照。本计划的实现不发起这次运行。
- F3. 重算读取当前筛选结果里的公式，用 5 日和 10 日标签各算一遍验证段持仓，写入运行库报告。不改原计划文件。
- F4. 用户确认标签周期后，新计划使用该周期。旧计划仍按原 1 日标签展示。
- F5. 筛选后的低相关因子进入一次多因子持仓。结果是单独一条组合记录，不是新的单因子轮次。

### Acceptance Examples

- AE1. 验证段信息比率等于 0 的公式留下错误说明，`_mined_factor_count` 不增加。
- AE2. `Mean(close, 10)` 已计数后，`Mean(close, 20)` 的验证段信息比率不更高时不计数。验证段信息比率更高时计数。
- AE3. 提示词含有验证段信息比率最高的公式，不含 `Ref(close, -1)` 这类失败公式，不含测试段信息比率字段。
- AE4. 对已完成计划做标签重算后，该计划 JSON 的轮次条数和最后一条公式不变。
- AE5. 在构造数据上，因子等于 `circ_mv` 时，可回归日期的规模残差接近 0。残差计算不读取行业列。

### Success Criteria

实现完成的标准是计数、提示词和重算函数符合下面的验收例子。公式类是否变多，要等用户另行开始的对照运行，不作为实现完成的条件。测试段筛选规则不变。

### Scope Boundaries

做：

- 验证段持仓成为计数条件。
- 按公式类拒绝窗口微调。
- 提示词改为验证段最好的已计数公式。
- 标签周期可配置，并提供不改历史文件的重算。
- 用 `circ_mv` 做规模残差。
- 增加 `Corr`、`Rank`、`Delta`。
- 一条多因子组合持仓。

不做：

- 不改 `screen_factor_picks` 的比较：测试年化必须高于该行基准，信息比率必须大于 0，同一类只留信息比率最高的一条。
- 不改 TopkDropout 的 50、5、95% 仓位、买入 5 个基点、卖出 15 个基点。
- 不把 Microsoft Qlib 的 Alpha158 或工作流嵌进来。
- 不下载行业分类，不做行业中性。
- 不在实现过程中启动挖因子或回测。
- 不把 1000 这个上限改成别的数。质量来自计数条件，不来自把上限加大。

### Dependencies

面板字段和公式求值依赖 `quantlab/services/qlib_export.py` 的 `EXTRA_FIELDS` 与 `quantlab/services/qlib_factor_loop.py` 的 `evaluate_formula`。持仓依赖 `quantlab/services/qlib_strategy.py` 的 `lightgbm_scores`、`lightgbm_multi_scores` 和 `backtest_topk`。筛选依赖 `screen_factor_picks` 与 `formula_family`。

### Outstanding Questions

- Q1. 下一轮新计划的默认标签用 5 日还是 10 日。延期，不挡住 U1 和 U2。U3 写出两种周期的验证段信息比率后，由用户确认，U4 才改新计划的默认值。

### Sources

- `quantlab/services/qlib_factor_loop.py`：`execute`、`_prompt_for_model`、`_mined_factor_count`、`formula_family`、`screen_factor_picks`、`mean_rank_ic`。
- `quantlab/services/qlib_strategy.py`：`lightgbm_scores`、`backtest_topk`、`run_factor_strategy`、`TOPK`、`N_DROP`。
- 2026-10-01 筛选快照：过线 223 条，留下 94 类。`qlib-d7450dadaa19` 完成 1000/1000，留下 14 类。全表测试段信息比率最高的一条是更早计划里的 `pc_amp_vol_corr_100`，1.946。该计划自己最好的是 `pe_pb_refhl_std20`，测试段信息比率 0.992。这两个数都是测试段筛选结果，不是验证段。

## Planning Contract

### Key Technical Decisions

- KTD1. 计数看验证段持仓，不看验证 IC 的符号。LightGBM 可以用负相关，IC 大于 0 会误杀能被模型反过来用的公式。验证段持仓使用与筛选相同的两条线：年化高于该段等权基准，信息比率大于 0。Governs R1。
- KTD2. 验证段持仓的拟合窗口是 `date <= train_end`，固定 80 轮，不用验证段早停。测试段路径保持现在的早停。两套分数分开存。Governs R2。
- KTD3. 公式类沿用 `formula_family`。比较时去掉空白。新算子 `Corr`、`Delta` 的窗口也收成 `W`；`Rank` 没有窗口。`Corr` 的窗口是 2 到 120，因为 1 日相关没有定义。`Ref`、`Mean`、`Std`、`Delta` 仍是 1 到 120。类的比较用验证段信息比率，不用测试段。Governs R3, R8。
- KTD4. 提示词名额仍是 48 和 24，但 48 条按验证段信息比率从高到低取，不再取时间上的最后 48 条。24 条仍是最近的已计数记录，避免模型只看见老公式。Governs R4。
- KTD5. 模型标签和 Spearman IC 使用同一股票 `shift(-h) / close - 1`，`h` 为 1、5 或 10。持仓记账始终用下一天收益 `shift(-1) / close - 1`。`h` 大于 1 时，禁止把重叠的 h 日收益当成每天的组合收益。比较周期时只看验证段持仓的信息比率，不比较 IC 数值。Governs R6。
- KTD6. 规模残差按日做：因子对 `log(circ_mv)` 回归，至少 30 只股票，残差再进入现有的按日标准化。市值缺失的股票当天不参与回归，残差为缺失。Governs R7。
- KTD7. 多因子的入选顺序和相关过滤都只用验证段。按验证段信息比率从高到低考察。与已入选因子在验证段的秩相关绝对值达到 0.7 则跳过。组合最多 20 个因子。测试段只给这条组合出最终持仓，不决定谁入选。模型标签用该筛选结果自己的 `label_horizon`，组合日收益仍是下一天收益。Governs R9。

### High-Level Design

数据流保持一条线。公式求值之后分出两条持仓：

1. 验证段持仓决定计数、公式类比较和提示词。
2. 测试段持仓只写入轮次，供现有筛选和记录页使用。

`lightgbm_scores` 增加分数区间参数。缺省区间仍是 `date > valid_end`，现有调用结果不变。验证段调用使用 `train_end < date <= valid_end`，并且关闭早停。

计划增加 `label_horizon`，缺省 1。旧文件没有该字段时按 1 读。筛选输出带上这个字段，避免不同周期排在同一列里被当成同一种信息比率。

重算是只读函数：读筛选结果和面板，写 `quantlab_runtime/qlib/horizon_rescore.json`。该目录已在忽略规则内，不提交行情或报告数据。

### Assumptions

- 现有计划的 `train_end` 是 2022-12-31，`valid_end` 是 2024-12-31。新逻辑使用计划自己的这两个字段。
- 基准是当天有分数的股票等权，不是沪深 300 指数本身。验证段和测试段都沿用这个定义。
- 策略函数内部失败时，现在的轮次 `error` 仍为空，从而计入 1000。新的计数必须把这种失败当成未过线。
- `circ_mv` 在面板中已有。行业列不存在。

### Sequencing

U1 和 U2 可以连续实现，因为它们改的是同一次循环。U2 依赖 U1 已经把验证段信息比率写在轮次上。

U3 依赖现有筛选和 U1 的验证段持仓函数，但不依赖 U2 的提示词。U4 依赖 U3 的报告和用户确认。U5 依赖 U1 的计数入口，这样残差发生在计数之前。U6 依赖 U2 的按类拒绝。U7 依赖现有筛选，不依赖 U6。

对照用的 50 轮新计划放在 U2 完成之后、U4 之前。实现者不启动它。

### Risks

- 验证段过线会让凑满 1000 变慢。这是计数定义的变化，不把上限自动加大。
- 验证段和测试段用了同一套验证标签的不同方式。验证段持仓若再拿验证标签早停，计数会被抬高。KTD2 禁止这条路径。
- 10 日标签的日收益重叠，IC 不可与 1 日 IC 比大小。只比较各自的验证段持仓。
- `formula_family` 今天只认识 `Ref`、`Mean`、`Std`。新算子若不加入这个函数，窗口微调会在筛选和拒绝里漏掉。

## Implementation Units

### U1. 用验证段持仓决定计数

Goal: 一轮只有验证段持仓过线才计入挖到的因子。

Requirements: R1, R2, R5

Files:

- `quantlab/services/qlib_strategy.py`
- `quantlab/services/qlib_factor_loop.py`
- `quantlab/web/pages/qlib_runs.html`
- `quantlab/web/assets/qlib/runs.js`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: 给 `lightgbm_scores` 增加分数日期区间和是否早停。缺省行为与现在一致。`run_factor_strategy` 增加验证段模式，训练样本只用 `date <= train_end`，分数只用验证段，组合日收益仍是下一天收益。`execute` 的顺序是：原文和名字查重、公式求值、验证段持仓、过线才做测试段持仓。年化未高于该段基准或信息比率不大于 0 时，轮次写入 `error`，不进入计数，也不计算测试段。轮次保存 `valid_annual_return` 和 `valid_information_ratio`。记录页在测试列之前显示这两列，并更新 `qlib_runs.html` 里脚本和样式的版本参数，避免浏览器继续用旧页面。

Test Scenarios:

- 缺省 `lightgbm_scores` 仍只预测 `valid_end` 之后，早停轮数仍是 10。
- 验证段模式的训练行日期全部不晚于 `train_end`，预测行全部落在验证段。
- 验证段信息比率为 0 时，`_mined_factor_count` 不变，轮次 `error` 非空。
- 验证段年化高于基准且信息比率大于 0 时计数增加，测试段字段仍被写入。
- `run_factor_strategy` 返回策略错误时不计数。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_mining_quality.py -q`

### U2. 按公式类拒绝，并改提示词

Goal: 窗口微调不再占用计数，提示词展示验证段最好的已计数公式。

Requirements: R3, R4

Files:

- `quantlab/services/qlib_factor_loop.py`
- `tests/quantlab/test_qlib_resume.py`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: 验证段持仓过线之后再算 `formula_family`。该类已有计数成员且新公式的验证段信息比率不更高时，把这条公式记入已见公式，加入 `rejections`，不写错误轮次，不算测试段，并在 `AGENT_ATTEMPTS` 内再问一次。三次都未计数时，沿用现在对重复公式的做法：外层循环继续，不追加轮次。退回说明只含名字、公式和验证段信息比率，不含测试段成绩。`_prompt_for_model` 的 48 条改为已计数公式按验证段信息比率排序后的前 48 条。最近 24 条只从已计数公式里取。测试段字段不进入字符串。

Test Scenarios:

- 已有 `Mean(close, 10)` 且验证段信息比率为 0.4 时，`Mean(close, 20)` 在验证段信息比率为 0.2 时不计数，不写入测试段字段，再次提交同一公式会被原文查重拒绝。
- 后者验证段信息比率为 0.5 时计数，并成为该类用于比较的成员。
- 三次尝试都因公式类被退回时，计划不新增轮次，循环也不因此停止。
- 60 条失败公式加 3 条已计数公式时，提示词含这 3 条，不含失败公式。
- 80 条已计数公式时，提示词含信息比率最高的那条，不含最低的那条。
- 提示词字符串不含 `test_information_ratio`，不含测试年化数字的字段名。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_resume.py tests/quantlab/test_qlib_mining_quality.py -q`

### U3. 重算 5 日和 10 日验证段持仓

Goal: 在改默认标签之前，用已筛选公式看两种持有周期的验证段持仓。

Requirements: R5, R6

Files:

- `quantlab/services/qlib_factor_loop.py`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: 增加纯函数，输入公式、面板和周期，输出验证段年化、基准年化和信息比率。模型标签用 `shift(-h)`，组合日收益仍用 `shift(-1)`。对 `screen_factor_picks` 的当前结果逐条重算，写入运行库 JSON。函数不调用 `start`，不写计划文件。报告按现有测试段信息比率列出前 10 类在 1 日、5 日、10 日标签下的验证段信息比率。

Test Scenarios:

- `h=1` 时，模型标签与组合日收益是同一条下一天收益。
- `h=10` 时，模型标签在样本末尾 10 个交易日为空，组合日收益仍是下一天收益，并且每日收益之间不重叠累加。
- 重算函数不接收计划写入函数，测试用临时目录断言原计划文件字节不变。
- 报告含 5 和 10 两个周期，不含对计划文件的修改。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_mining_quality.py -q -k horizon`

### U4. 新计划记录标签周期

Goal: 新计划可以带着确认后的周期开跑，旧计划仍按 1 日解释。

Requirements: R5, R6

Files:

- `quantlab/services/qlib_factor_loop.py`
- `quantlab/web/assets/qlib/page.js`
- `quantlab/web/pages/qlib.html`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: `save_plan` 接受 `label_horizon`，只允许 1、5、10，缺省 1。`execute` 用该字段构造标签。缺少该字段的旧计划按 1。筛选结果增加 `label_horizon`。页面在开始循环处显示当前周期，默认 1。用户确认 U3 报告之前，页面不把默认值改成 5 或 10。

Dependencies: U3 的报告。改默认值还要用户确认 Q1。把字段和求值接上不需要等待确认。

Test Scenarios:

- 不带 `label_horizon` 的旧计划求值得 1 日标签。
- 新建计划缺省周期是 1。传入 10 时标签使用 `shift(-10)`。
- 传入 7 时 `save_plan` 拒绝。
- 两条不同周期的筛选结果都带 `label_horizon`，调用方能分开排序。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_mining_quality.py -q -k horizon`

### U5. 用流通市值做规模残差

Goal: 计数和 IC 使用去掉当日规模暴露之后的因子。

Requirements: R7

Files:

- `quantlab/services/qlib_factor_loop.py`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: 在 `evaluate_formula` 之后、IC 和两段持仓之前，按日用 `log(circ_mv)` 做一元回归并取残差。股票少于 30 或市值全部缺失的日期，残差为缺失。不读取 `stock_basic`。回归发生在按日标准化之前，标准化仍由 `lightgbm_scores` 做。

Test Scenarios:

- 因子等于 `circ_mv` 时，残差在可回归的日期上接近 0。
- 因子与市值无关的常数横截面差异被保留。
- 当天有效股票少于 30 时该日残差为缺失。
- 求值结果不新增行业字段。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_mining_quality.py -q -k size`

### U6. 增加相关、排名和差分

Goal: 公式可以表达和均线不同的结构，窗口微调仍被 U2 拒绝。

Requirements: R8

Files:

- `quantlab/services/qlib_factor_loop.py`
- `tests/quantlab/test_qlib_extra_fields.py`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: `Corr(左, 右, 窗口)` 按股票滚动相关，窗口 2 到 120。`Rank(序列)` 是当日横截面百分位秩。`Delta(序列, 窗口)` 是序列减去 `Ref(序列, 窗口)`，窗口 1 到 120。三者进入白名单、提示词和 `formula_family`。`Corr` 与 `Delta` 的窗口收成 `W`。不允许负窗口。

Test Scenarios:

- `Corr(close, volume, 5)` 得到有限值。`Corr(close, volume, 1)` 和 `Corr(close, volume, -1)` 都报窗口错误。
- `Rank(close)` 在同一天的秩位于 0 到 1。
- `Delta(close, 1)` 等于 `close - Ref(close, 1)`。
- `Corr(close, volume, 5)` 与 `Corr(close, volume, 20)` 属于同一公式类。
- 提示词列出这三个函数名。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_extra_fields.py tests/quantlab/test_qlib_mining_quality.py -q`

### U7. 用低相关的筛选因子做一条多因子持仓

Goal: 单因子过线之后，用已有多因子 LightGBM 做一条组合，不计入单因子数量。

Requirements: R9

Files:

- `quantlab/services/qlib_factor_loop.py`
- `quantlab/services/qlib_strategy.py`
- `tests/quantlab/test_qlib_mining_quality.py`

Approach: 从同一 `label_horizon` 的筛选结果取因子，按验证段信息比率从高到低考察。与已入选因子在验证段的秩相关绝对值达到 0.7 则跳过。最多 20 个。入选名单确定后，才用 `lightgbm_multi_scores` 做测试段分数，再用 `backtest_topk`。模型标签用这个周期，组合日收益用下一天收益。结果写入运行库的组合报告，不追加到某个单因子计划的 `rounds`。

Test Scenarios:

- 两个验证段完全相同的因子只有一个进入组合。
- 验证段信息比率较低、测试段信息比率较高的因子，排在验证段信息比率较高的因子之后。
- 第 21 个低相关因子不进入。
- 组合报告的因子列表来自筛选结果，计划文件的轮次条数不变。
- 单因子 `max_loops` 计数不因这次组合增加。

Verification: `.venv/bin/python -m pytest tests/quantlab/test_qlib_mining_quality.py -q -k multi`

## Verification Contract

每个单元用该单元写下的 pytest 命令验收。全部单元完成后跑：

`.venv/bin/python -m pytest tests/quantlab/test_qlib_resume.py tests/quantlab/test_qlib_extra_fields.py tests/quantlab/test_qlib_mining_quality.py -q`

不把实盘挖因子、面板转换或回测当作测试。记录页改动用上述测试覆盖按钮和列所在的脚本字符串；没有浏览器自动化时不声称页面已点击通过。

## Definition of Done

全部单元的测试通过。废弃的试验代码不留在差异里。

- U1 完成时，不过线的轮次不计数，缺省测试段预测路径的测试仍在。
- U2 完成时，只改窗口且验证段信息比率不更高的公式不计数，提示词不含测试段字段。
- U3 完成时，运行库报告能区分 5 日和 10 日，原计划文件不变。
- U4 完成时，旧计划按 1 日读取，新计划缺省仍是 1 日。
- U5 完成时，市值本身的残差接近 0，且没有行业列。
- U6 完成时，三个新函数可求值，同类窗口被收成同一类。
- U7 完成时，入选顺序来自验证段信息比率，组合不写进单因子轮次。

50 轮对照、以及把默认标签改成 5 或 10，都要用户另行确认。它们不是本计划实现完成的条件。
