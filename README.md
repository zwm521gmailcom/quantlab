# QuantLab

**中文** | [English](README.en.md)

本机因子、模型与回测研究平台。每个实例标明资产版本（默认 A 股）。浏览器操作；FastAPI 默认监听局域网 `0.0.0.0:8765`（本机仍可用 `127.0.0.1`）。页面端口和局域网同步端口可自行设定。元数据在 SQLite，行情与因子在本地 Parquet。

当前版本 **`0.4.1`**（Alpha）。版本号在 `quantlab/__init__.py`。功能更新后改这个数字，各机 `git pull` 并重启，侧栏应显示同一版本。

界面截图放在 [`docs/screenshots/`](docs/screenshots/)，不嵌在本文件里。

## 这是什么

QuantLab 把数据、因子、模型、回测和结果档案收进同一个本机工作台。权威行情留在原目录只读使用；页面通过注册表和 API 按需读取，不把完整宽表复制进网站目录。因子、模型、策略和回测都带不可变版本或运行快照。失败任务也会留下配置、日志和已生成文件。

正式成交与因子计算使用后复权字段：`hfq_open`、`hfq_high`、`hfq_low`、`hfq_close`。

## 工作台

启动后本机打开 http://127.0.0.1:8765/ ；同一局域网的其他电脑打开 `http://<这台机器的局域网IP>:8765/` 。侧栏由页面脚本生成，含深色模式。

| 页面 | 路径 | 作用 |
|---|---|---|
| 研究总览 | `/` | 数据集、因子、最近运行与质量告警 |
| 数据中心 | `/data` | 已登记 raw / 来源整理表、覆盖范围与质量；每个 Tushare 接口一列「补数据」，只补该接口自己的尾部 |
| 标准行情宽表 | `/kline` | 只读查询 raw / 后复权 K 线 |
| 因子研究 | `/factors` | 因子目录、诊断与详情 |
| 手动建立因子 | `/factors/new/manual` | 写公式并登记入库 |
| Qlib 挖因子 | `/qlib` | 本机 GPT-OSS 120B 每次提一个公式，按验证段打分；跑起来后「开始循环」变灰，停止会结束任务并关掉本机模型 |
| 挖因子记录 | `/qlib/runs` | 每一轮的公式、验证分和测试持仓 |
| 筛选结果 | `/qlib/picks` | 从挖出的因子里筛测试段优于基准且 IR 为正的结果 |
| 模型中心 | `/models` | 登记回测可用的模型种类，不在这里训练 |
| 回测中心 | `/backtests/new` | 冻结数据、因子、股票池、模型与撮合规则后开跑；可用多因子 Qlib 打分 |
| 回测计划 | `/backtests/plan` | 把任务写进清单；同时跑几条由本机内存和核数自动定 |
| 大致可用 | `/backtests/usable` | 已筛过、可接着用的版本 |
| 规则回测 | `/backtests/rules` | 内置规则模板，不训练模型 |
| 离线策略学习 | `/backtests/offline-rl` | 用已归档的自然年回测做离散 Fitted Q；打开页面不自动开跑 |
| 结果档案 | `/backtests/runs` | 历史运行、指标、产物；列可排序；复制配置不自动再跑 |
| 设置 | `/settings` | 路径、资产与端口、Tushare token 与积分、局域网同步、运算资源、草稿默认参数 |

模型种类包括 LightGBM / XGBoost 排序树，随机森林、Ridge、Lasso 等回归，Qlib LightGBM 回归，以及单因子 `factor_rank`。树和回归的参数在回测中心设置，**开始回测时才训练**。单因子按所选因子值截面排名，不训练；只读所选成分和回测窗（另加年线预热），步骤显示为「准备数据 / 因子排序」。多因子 Qlib 打分在回测里训练，训练结束之前不参与打分。

股票池独立于训练、回测日期窗：交易所范围 + 成分指数下拉多选去重，两边共用。成分按已下载的 `index_weight` 做时点 as-of（不用未来一期），多指数取并集。默认 CSI800（沪深300∪中证500）。选股范围写入运行快照。缺权重文件时该指数不能用。训练和测试都会滤掉 ST 和一字板。买入时开盘涨跌停不买；卖出时若开盘跌停则不卖，留到下一交易日再卖，直到卖出。基准均线开仓只挡新开仓。大批量扫描可在计划配置写 `segment_curves: false`，跳过市值/换手分层净值，主曲线仍算。

旧的「自动挖掘因子」和「因子计算任务」页面不再使用，分别转到 Qlib 挖因子和因子研究。数据中心补数不把历史起点往已有文件之前延伸，也不重建标准宽表；16:00 前不把当天交易日写入日频文件。同一时间只跑一个补数任务。

## 环境

Python 3.10+，macOS 或 Linux。建议在仓库根创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

浏览器测试还需要：

```bash
python -m playwright install chromium
```

## 数据目录

默认读取仓库下的 `data/`，运行产物写入 `quantlab_runtime/`（均不进 git）。

| 根目录 | 默认位置 | 环境变量 |
|---|---|---|
| 项目根 | 当前工作目录 | `QUANTLAB_PROJECT_ROOT` |
| 行情与标准宽表 | `<project>/data` | `QUANTLAB_DATA_ROOT` |
| 校准 / 实验产物 | `<project>/data/calibration` | `QUANTLAB_CALIBRATION_ROOT` |
| Tushare 原始接口文件 | `<project>/data/raw` | `QUANTLAB_RAW_ROOT` |
| 运行时（SQLite、任务、结果） | `<project>/quantlab_runtime` | `QUANTLAB_RUNTIME_ROOT` |

已有数据仓库可以只改环境变量或命令行参数，不必搬文件。路径用相对项目根的写法，例如 `data`、`quantlab_runtime`。

标准行情来自 `data/canonical.parquet`（每股票每日一行）。Tushare token 和积分数在设置页配置。下载限速与单 API 日总量按 [doc_id=290](https://tushare.pro/document/1?doc_id=290) 对应档执行；未填积分时按 120 档保守限速，触顶停止、次日再续。原始接口文件写入 `data/raw/`（含指数日线与 `index_weight`）。公式包因子用 `quantlab materialize-pack-factors` 从标准宽表落地旁路 Parquet。

## 局域网

三台都要监听局域网，不能只绑 `127.0.0.1`。程序更新仍走 GitHub：本机 push 并合并后，其他机器 `git pull` 再重启 QuantLab。某台机器若不能访问 GitHub，合并后用 `git bundle` 把同一提交拷过去再重启，不要拷盘同步源码。各台使用自己的 SQLite。

- 页面默认 `0.0.0.0:8765`，文件同步另开 `8766`（HTTP + UDP 宣告，不提供页面、不开库、不传 Token）
- 两个端口都可在设置页或启动参数里改，且必须不同
- 只发现同一资产版本；A 股实例不要和数字货币实例互相同步
- 防火墙放行你实际使用的页面端口和同步端口
- 可在一台电脑的浏览器里，用设置页列表的「打开页面」进入各机 QuantLab

同步行为：

- **回测产物：** 互相补缺；已有目录不覆盖；合并 `results/_deleted/`；同时带上计划快照。对端若是旧索引，会改从页面接口把计划拉过来
- **数据：** 先比文件大小和修改时间，对端已有且相同的不下载。只推勾选的类别：标准宽表 / 旁路因子 / raw / 来源整理表。不同步因子库 SQLite 和 `quantlab_runtime/factors/`
- **每天 04:00：** 可选，本机当源头同步所选类别；服务须在跑；错过不补跑

`--host` 只接受 `127.0.0.1`、`0.0.0.0` 或 RFC1918 地址，不能绑公网 IP。

**不要**用 git 或拷盘同步 `quantlab_runtime/db/`、`config/`（机器码、运算设置、Token），也不要对整个 `results/` 做镜像删除。每台启动或打开结果档案时，会补齐缺的机器码、按 `_deleted/` 标记删掉对端已删的回测，并扫描 `results/*/run.json` 写入本机库。三台的运行序号前缀不要填一样。

## 启动

```bash
quantlab init-db
quantlab serve
```

默认本实例是 A 股，监听 `0.0.0.0:8765`，局域网同步 `8766`。本机打开 http://127.0.0.1:8765/ 。`--host` 不能绑公网 IP。

数字货币请另起目录，并标明资产与端口：

```bash
quantlab serve --asset crypto --port 8775 --lan-port 8776
```

也可在设置页保存，写入 `quantlab_runtime/config/instance.json`，重启后生效。优先级：命令行 `>` 环境变量 `QUANTLAB_ASSET` / `QUANTLAB_PORT` / `QUANTLAB_LAN_PORT` `>` `instance.json` `>` 默认。

等价写法：`python -m quantlab.cli init-db` / `python -m quantlab.cli serve`。

## 命令行

```bash
quantlab init-db                      # 初始化 SQLite 并登记已配置数据集
quantlab serve                        # 启动本机网页服务（--asset / --port / --lan-port）
quantlab snapshot-data-baseline       # 把当前权威数据指纹写入 runtime/baselines
quantlab verify-data-baseline         # 核对权威数据是否相对基线发生变化
quantlab materialize-pack-factors     # 从 canonical.parquet 计算并落地公式包因子
```

每个子命令都接受 `--project-root`、`--data-root`、`--calibration-root`、`--runtime-root`。`serve` 另接受 `--asset`（`a_share` 或 `crypto`）、`--port`、`--lan-port`。

## 回测并行

设置页可改，不必重启页面服务。折并行和分层进程是一条回测内部的请求值，内存不够时运行时会再降。同时回测条数按本机内存和核数自动算，没有手动档；16GB 仍是 1 条。

- `QUANTLAB_FOLD_WORKERS`：定长回看折并行数
- `QUANTLAB_BUCKET_WORKERS`：分层净值进程数
- `QUANTLAB_BUCKET_POOL`：`process`（默认）或 `thread`

16GB 内存机器请把分层进程数设为 `1`，不要按 CPU 核数开满。只停运算请用回测中心的「回测强行停止」，不要停整个 QuantLab。系统因内存杀掉的进程不要写成「已强行停止」。

## 测试

```bash
pytest tests/quantlab -q
ruff check quantlab tests/quantlab
```

浏览器测试：

```bash
pytest tests/quantlab/browser -q
```

## 目录

```text
quantlab/
  api/            FastAPI 路由与本机页面
  domain/         实体、标识与状态转移
  services/       数据、因子、训练、回测、局域网同步
  repositories/   SQLite 与文件注册表
  web/            HTML / CSS / JS
  config/         路径边界
  cli.py          启动与初始化
tests/quantlab/   单元测试与 Playwright 页面测试
docs/specs/       设计规格
docs/plans/       实现计划
docs/screenshots/ 界面截图（本说明不嵌图片）
```

运行时目录（不进 git）：

```text
quantlab_runtime/
  db/quantlab.sqlite3
  jobs/           任务日志与临时状态
  factors/        新计算的因子版本
  strategies/     策略快照
  results/        每次回测独立目录（含计划快照）
  config/         机器码、运算设置、Token、instance.json
  baselines/      数据基线指纹
```

## 文档

现行说明就是本文件和 [`docs/quantlab/start-local.md`](docs/quantlab/start-local.md)。`docs/plans/`、`docs/specs/` 里的日期文档是当时的实现记录，不代表现在的页面。界面截图只放 [`docs/screenshots/`](docs/screenshots/)。

## 许可证

[MIT](LICENSE)
