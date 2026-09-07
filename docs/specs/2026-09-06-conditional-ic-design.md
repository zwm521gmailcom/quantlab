# 因子明细分层 IC（市值 / 换手）设计

日期：2026-09-06
页面：`/factors/{factor_id}`（因子数据明细）
状态：已确认

## 目标

在单个因子的计算任务里，除全体截面 RankIC 外，再给出 **流通市值五档、换手率五档内的 RankIC**（conditional IC），并在明细页用柱状图 + 每日曲线展示。用来判断：这个因子在小盘/大盘、低换手/高换手里是否同样有效。

## 非目标

- 不改已存因子 Parquet，不改写旧的 `factor_calculation_runs` 行。
- 不改全体 `ic_mean` / `daily_ic` / Top-Bottom 的定义与数字。
- 不做「高因子股票长什么样」的特征画像。
- 不做因子 × 市值双排收益热力图。
- 打开明细时不现算；旧任务缺字段时提示重新运行分析，不自动重跑。
- 不用总市值；不做 PE、成交额等其它分层。
- 不改 `FactorDetailService`（因子版本诊断页）。

## 口径

与现有每日 IC 相同：

- 标签：T+1 开盘 → T+2 收盘（`t+1 open -> t+2 close`）。
- 每日 IC：当天截面把 `_factor` 与 `_target` 分别 `rank(method="average")`，再 Spearman 相关。
- 当天全体样本 < 3 只：不记当日全体 IC，也不做分层。

分层额外规则：

- 市值字段：`float_market_cap`（流通市值）。档位标签：Q1 小盘、Q2、Q3、Q4、Q5 大盘。
- 换手字段：`turn`。档位标签：Q1 低换手、Q2、Q3、Q4、Q5 高换手。
- 每个交易日、每个字段：在当日 **该字段非空** 的股票上 `pd.qcut(..., q=5, labels=False, duplicates="drop")`。0 = 最小档 → bucket 1。
- 某档当天股票数 < 3：该日该档不记 IC。
- `qcut` 因取值重复分不出 5 档：只保留实际档，从小到大对应 Q1 起；缺档当天不记。
- 某只股票缺市值/换手：不进入该维分层，仍可进入全体 IC。
- 因子本身就是市值或换手：仍计算（档内区分度会变弱）。这是预期，不是错误。
- 分层 IC 与因子五分组（Top-Bottom）独立：因子 `qcut` 失败不影响分层 IC。

汇总：每档 `ic_mean`、`ic_positive_ratio`、`effective_days` 只对该档有 IC 的交易日计算。序列截断与全体 IC 一样，取最后 `MAX_SERIES_POINTS`（400）个点。

## 数据流

1. 读 canonical 时，公式/内置因子原有列之外，**始终尝试**带上 `float_market_cap`、`turn`（schema 没有就跳过）。
2. `_crop` 在 `trade_date/ts_code/_factor/_target` 之外，保留这两列（若存在）。
3. `cross_section_diagnostics` 不得丢掉这两列；在现有按日循环里、全体 RankIC 之后、因子五分组之前，计算分层 IC。
4. `_summary` 把 `conditional_ic` 写入 `summary_json`。SQLite 不新增列。
5. `_project` 把 `conditional_ic` 提到任务对象顶层（与 `histogram` 一样），方便明细页读取。

## 存储形状

`summary.conditional_ic`：

```json
{
  "by_float_market_cap": {
    "field": "float_market_cap",
    "status": "available",
    "buckets": [
      {
        "bucket": 1,
        "label": "Q1 小盘",
        "ic_mean": 0.02,
        "ic_positive_ratio": 0.55,
        "effective_days": 120,
        "daily_ic": [{"date": "20240102", "ic": 0.01, "count": 80}]
      }
    ]
  },
  "by_turn": {
    "field": "turn",
    "status": "available",
    "buckets": [
      {
        "bucket": 1,
        "label": "Q1 低换手",
        "ic_mean": 0.01,
        "ic_positive_ratio": 0.52,
        "effective_days": 118,
        "daily_ic": [{"date": "20240102", "ic": -0.02, "count": 75}]
      }
    ]
  }
}
```

`status`：

| 值 | 何时 |
| --- | --- |
| `available` | 至少一档有一个有效日 IC |
| `missing_field` | 本次读到的表没有该列；`buckets` 为空 |
| `insufficient_data` | 有该列，但没有任何一档达到「≥3 只且能算出 IC」 |

`available` 时 `buckets` 固定 5 项（bucket 1–5）。从未出现的档：`ic_mean` / `ic_positive_ratio` 为 `null`，`effective_days` 为 0，`daily_ic` 为 `[]`。

旧任务没有 `conditional_ic` 键：页面当缺失处理，不补算、不回写。

## 页面

现有每日 IC、Top-Bottom、因子值分布保留。插入顺序：

1. 每日 IC 曲线
2. Top-Bottom 每日价差
3. 分层 IC · 流通市值：五档 IC 均值柱（可正可负，零轴居中）+ 五条每日 IC 曲线（同一张 Lightweight Charts，图例为档位名）
4. 分层 IC · 换手率：同上
5. 因子值分布

「有效性指标」在 ICIR 后加两行：

- 小盘 IC = 市值 Q1 的 `ic_mean`
- 大盘 IC = 市值 Q5 的 `ic_mean`

缺字段时显示「—」，info 说明与图表一致：当日截面分档，档内 RankIC，口径与全体 IC 相同。

空态文案：

- 已完成但无 `conditional_ic`：`此任务未含分层 IC，重新运行分析后显示。`
- `missing_field`：`行情宽表没有流通市值字段，无法分层。`（换手替换字段名）
- `insufficient_data`：`有效样本不足以按市值分层（每档至少 3 只）。`

不自动点「运行分析」。

五条曲线配色固定（浅→深表示档位升高）：`#9ecae1`、`#6baed6`、`#3182bd`、`#08519c`、`#d94801`。

## 复现与性能

- 同一因子、同一周期连跑两次：全体 IC 数字与现网完全一致；`conditional_ic` 两次也相同。
- 分层在现有按日循环内多两次 `qcut`，不另做全表向量化。
- 不改已存计算任务。

## 测试

- 无市值/换手列时：现有 `cross_section_diagnostics` 断言全部成立；`conditional_ic.*.status == missing_field`。
- 构造 15 只股票、两日、每档 3 只：档内 RankIC 与手算 Spearman 一致；全体 IC 不因多了两列而改变。
- `FactorCalculationService.run` 两次：`summary.conditional_ic` 相等。
- `test_web_shell`：明细页 JS 含分层 IC 渲染。
- Playwright：拦截计算历史为带 `conditional_ic` 的完成任务，明细出现「分层 IC · 流通市值」「小盘 IC」，无 console error。

## 改动文件

- `quantlab/services/factor_calculation.py`
- `quantlab/web/assets/app.js`、`app.css`
- `quantlab/web/pages/factors.html`（资源 `?v=`）
- `tests/quantlab/test_factor_calculation.py`、`test_web_shell.py`
- `tests/quantlab/browser/test_factor_detail_browser.py`
