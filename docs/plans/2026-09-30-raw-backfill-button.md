# 数据中心按接口补数据

日期：2026-09-30

## 问题

数据中心表格在「Tushare 接口」后面没有补数入口。各 raw 接口的补法不同，已有下载函数只覆盖其中一部分，而且日线、复权、涨跌停、每日指标、指数日线、股票基础信息没有页面按钮。

本机积分是 2000（每分钟 200 次，每个接口每天 10 万次）。12 个接口的历史段中间没有缺日。缺口只在最近几个交易日，以及快照类接口需要整份刷新。

## 不做

- 不把起始日往 2016-10-10 之前延伸。
- 不重下已有文件，不新增本地还没有的指数。
- 不重建 `canonical.parquet`，不改 Qlib 面板，不启动回测。
- 不在收盘前把当天交易日写进日频文件。
- 不同时跑两个补数任务。

## 页面

`quantlab/web/assets/data/datasets.js` 的表头在「Tushare 接口」后增加「补数据」。每一行一个按钮，文案是「补数据」。

`quantlab/web/assets/app.css` 的 `.dataset-row` 在现有 10 列后加一列 `auto`。窄屏仍用两列换行。

按钮行为：

- 点击后该行按钮变为「补数中」并禁用；其他行的补数据按钮一并禁用。
- 请求 `POST /api/datasets/raw/{interface}/backfill`。`interface` 用该行的 `name`（如 `daily`），不用 `raw_` 前缀。
- 返回任务编号后，每 2 秒读取 `GET /api/datasets/raw/backfill/{job_id}`。
- 结束时按钮恢复。结果写在按钮旁：`已是最新`、`补入 N 个交易日`、`刷新 N 行`，或失败原因。成功后重新加载当前页列表。
- 不弹日期框。

## 截止日

用上海时区。交易日来自本地 `raw/trade_cal`，只取 SSE 且 `is_open=1`。

- 16:00 之前，截止日是今天之前的最后一个开市日。
- 16:00 及之后，若今天开市，截止日是今天。
- 日历盖不住这个区间时，先调用现有 `refresh_trade_cal`，再计算。

## 每个接口单独一条规则

12 个接口不共用补数分支。`daily`、`daily_basic`、`adj_factor`、`stk_limit` 可以调用同一个按日写入函数，但接口名、目录和测试必须分开。点某一行只会请求该行的接口名。

已有数据达到该接口自己的目标时，不调用 Tushare，结果为已是最新。

1. `daily`：目录 `raw/daily/{交易日}.parquet`。请求 `daily`，参数只有 `trade_date`。补本地最大文件日之后、不超过截止日的开市日。已有文件跳过。某日空表或失败只记下这一天，继续后面的日期。不补 2016-10-10 之前。
2. `daily_basic`：与日线相同的文件形状，但请求名必须是 `daily_basic`，目录是 `raw/daily_basic`。不能把日线的调用算成这个接口已验证。
3. `adj_factor`：请求名 `adj_factor`，目录 `raw/adj_factor/{交易日}.parquet`。规则与日线相同，验证单独写。
4. `stk_limit`：请求名 `stk_limit`，目录 `raw/stk_limit/{交易日}.parquet`。规则与日线相同，验证单独写。
5. `moneyflow`：不是按日各打一枪。用日线文件的行数估算，相邻缺失日在 5800 行以内才合并成一次 `start_date`/`end_date`。达到 6000 行就放弃这次合并，改回按日重拉，并且拒绝写入被截断的文件。中间已有的日期会把缺失段切开。目录仍是 `raw/moneyflow/{交易日}.parquet`。
6. `suspend_d`：只有 `raw/suspend_d/suspend_d.parquet`。按月调用 `suspend_d` 的 `start_date`/`end_date`，和旧文件按 `ts_code`、`trade_date`、`suspend_type` 合并。这个接口没有「文件已在就跳过」；补数范围必须从文件内最大 `trade_date` 的下一天算起，避免把历史月再请求一遍。
7. `index_daily`：一指数一文件 `raw/index_daily/index_daily_{代码}.parquet`。只续已有文件。最大 `trade_date` 早于截止日才请求 `index_daily`，参数是 `ts_code` 加这段日期。按 `ts_code`、`trade_date` 合并。不创建本地没有的指数。
8. `index_weight`：一指数一文件。`399300.SZ` 先映射成 `000300.SH` 再请求和落盘。先探测范围内最后一个月的成分数量，再按这个数量切块，使单次低于 7000 行；仍然顶到上限就改成逐月。与旧文件按 `index_code`、`con_code`、`trade_date` 合并。目标只到不晚于截止日的最近一个月末开市日；文件里已有该日就整只指数跳过。
9. `stk_week_month_adj`：同一次补数既拉 `freq=week` 也拉 `freq=month`。只取窗口内每一周、每一月的最后一个开市日，文件名是 `week_{该日}.parquet` 和 `month_{该日}.parquet`。已有文件跳过。分包和 6000 行拒绝规则与资金流向相同，但是周线和月线各算各的。
10. `stock_basic`：固定三次请求，`list_status` 分别是 `L`、`D`、`P`，覆盖 `stock_basic_L.parquet`、`stock_basic_D.parquet`、`stock_basic_P.parquet`。某一状态返回空表时，不覆盖那一份旧文件。
11. `index_basic`：一次无参数请求，整份覆盖 `raw/index_basic/index_basic.parquet`。返回空表则失败，不把旧文件写成空的。
12. `trade_cal`：只请求 `exchange=SSE`。本地日历已经同时盖住截止日和当年 12 月 31 日就不请求。否则按这段日期刷新，并按 `exchange`、`cal_date` 与旧日历合并。

未知接口名返回 400，不创建任务。

## 接口与任务

`quantlab/api/routes/datasets.py` 增加两个路由，逻辑放在 `quantlab/services/tushare_download.py` 的补数入口，不在路由里写分支表。

- `POST /api/datasets/raw/{interface}/backfill`：已有任务在跑时返回 409。否则后台线程执行，立即返回 `{job_id, interface, status: "running"}`。
- `GET /api/datasets/raw/backfill/{job_id}`：返回状态、已处理数、总数、补入数量、失败日期、错误文字。任务只留在内存，进程退出即消失。

频次继续用现有 2000 积分限额。单个接口达到当日上限时停止并在状态里写明，已写入的文件保留。

## 测试

`tests/quantlab/test_tushare_download.py` 用假日历和临时目录，不访问 Tushare。截止日另有两条共用检查：16:00 前不含今天，16:00 后开市日包含今天。除此之外每个接口一条独立测试，只构造该接口的文件，并断言它发出的接口名和参数：

- `daily`：只补最大文件日之后的开市日，请求名是 `daily`，不补更早的历史。
- `daily_basic`：同样形状，但请求名必须是 `daily_basic`，不能写进 `raw/daily`。
- `adj_factor`：请求名是 `adj_factor`，文件落在 `raw/adj_factor`。
- `stk_limit`：请求名是 `stk_limit`，文件落在 `raw/stk_limit`。
- `moneyflow`：两个相邻缺失日在行数预算内合并成一次；中间夹一个已有文件时拆成两段；6000 行的合并结果不落盘。
- `suspend_d`：只为最大日期之后的月份发出请求，合并键包含 `suspend_type`。
- `index_daily`：落后的已有指数发 `ts_code`；已不早于截止日的文件零请求；不创建新指数文件。
- `index_weight`：`399300.SZ` 变成 `000300.SH`；已有最近月末开市日的指数零请求；顶到 7000 行的切块改成逐月。
- `stk_week_month_adj`：同一次任务同时检查 `week_` 和 `month_`；已有周期文件不发请求。
- `stock_basic`：恰好 `L`、`D`、`P` 三次；空的 `D` 不覆盖旧的 `stock_basic_D.parquet`。
- `index_basic`：一次无参数请求；空结果不覆盖旧文件。
- `trade_cal`：已覆盖到当年年底时零请求；需要刷新时参数里的交易所是 `SSE`。

另有一条与接口无关的检查：第二个补数请求在第一个未完成时被拒绝。

`tests/quantlab/test_web_shell.py` 断言表头有「补数据」，且该列表在「Tushare 接口」之后；样式列数比现在多一列。

## 上线注意

路由在进程启动时注册。当前服务进程不重启就没有这两个地址。重启会打断正在跑的 Qlib 循环。实现合并后先不重启，等明确说再重启。
