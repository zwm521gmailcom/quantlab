# QuantLab

**中文** | [English](README.en.md)

本机 A 股因子、模型与回测研究平台。浏览器操作，FastAPI 默认监听局域网 `0.0.0.0:8765`（本机仍可用 `127.0.0.1`），元数据在 SQLite，行情与因子在本地 Parquet。

当前版本 `0.1.0`（Alpha）。运行时不依赖 [vnpy](https://github.com/vnpy/vnpy)，本仓库是独立项目。

## 这是什么

QuantLab 把数据、因子、模型、回测和结果档案收进同一个本机工作台。权威行情留在原目录只读使用，页面通过注册表和 API 按需读取，不把完整宽表复制进网站目录。因子、模型、策略和回测都带不可变版本或运行快照，失败任务也会留下配置、日志和已生成文件。

正式成交与因子计算使用后复权字段：`hfq_open`、`hfq_high`、`hfq_low`、`hfq_close`。

## 工作台

启动后本机打开 http://127.0.0.1:8765/ ；同一局域网的其他电脑打开 `http://<这台机器的局域网IP>:8765/` 。

| 页面 | 路径 | 作用 |
|---|---|---|
| 研究总览 | `/` | 数据集、因子、最近运行与质量告警 |
| 数据中心 | `/data` | 已登记数据集、版本、覆盖范围与质量 |
| 标准行情宽表 | `/kline` | 只读查询 raw / hfq K 线 |
| 因子研究 | `/factors` | 因子目录、诊断与详情 |
| 手动建立因子 | `/factors/new/manual` | 写公式并登记入库 |
| 自动挖掘因子 | `/research/factor-mining` | 从宽表搜索候选表达式 |
| 因子计算任务 | `/research/factor-jobs` | 计算任务与进度 |
| 模型中心 | `/models` | 登记回测可用的模型种类 |
| 回测中心 | `/backtests/new` | 冻结数据、因子、模型与撮合规则后开跑 |
| 回测计划 | `/backtests/plan` | 勾选任务，按顺序串行执行 |
| 规则回测 | `/backtests/rules` | 内置规则模板，不训练模型 |
| 结果档案 | `/backtests/runs` | 历史运行、指标、产物与复制配置 |
| 设置 | `/settings` | 路径、Tushare token、运算资源、草稿默认参数 |

模型种类包括 LightGBM / XGBoost 排序树，以及随机森林、Ridge、Lasso 等回归模型。因子组合和训练参数在回测中心设置，开始回测时才训练。

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

已有数据仓库可以只改环境变量，不必搬文件：

```bash
export QUANTLAB_DATA_ROOT=data
export QUANTLAB_CALIBRATION_ROOT=data/calibration
export QUANTLAB_RUNTIME_ROOT=quantlab_runtime
```

也可以在命令行传 `--data-root`、`--calibration-root`、`--runtime-root`、`--project-root`。路径用相对项目根的写法，例如 `data`、`quantlab_runtime`。

局域网多机：三台都要监听局域网，不能只绑 `127.0.0.1`。程序更新仍走 GitHub（本机 push 并合并后，其他机器 `git pull` 再重启 QuantLab）。各台使用自己的 SQLite。默认页面端口 `8765`（绑 `0.0.0.0`），文件同步另开 `8766`（HTTP + UDP 宣告，不提供页面、不开库、不传 Token）。请在防火墙放行 TCP 8765 和 UDP/TCP 8766。可在一台电脑的浏览器里，用设置页列表的「打开页面」进入各机 QuantLab，依次点开始回测；回到主机设置页再点「同步回测产物」和同步行情。产物互相补缺（已有目录不覆盖，合并 `results/_deleted/`）；点某台则从那台拉 `data/` 行情（源头覆盖同路径且内容不同的文件，对端多出来的不删）。可打开「每天 04:00 自动同步行情」（本机当源头，服务须在跑；错过不补跑）。`--host` 只接受 `127.0.0.1`、`0.0.0.0` 或 RFC1918 地址，不能绑公网 IP。**不要**用 git 或拷盘同步 `quantlab_runtime/db/`、`config/`（机器码、运算设置、Token）。不要对整个 `results/` 做镜像删除。每台启动或打开结果档案时，会补齐缺的机器码、按 `_deleted/` 标记删掉对端已删的回测，并扫描 `results/*/run.json` 写入本机库。

标准行情来自 `data/canonical.parquet`（每股票每日一行）。Tushare token 在设置页配置，原始接口文件写入 `data/raw/`。

## 启动

```bash
quantlab init-db
quantlab serve
```

默认监听 `0.0.0.0:8765`。本机打开 http://127.0.0.1:8765/ ；其他机器打开 `http://<局域网IP>:8765/` 。`--host` 不能绑公网 IP。

等价写法：`python -m quantlab.cli init-db` / `python -m quantlab.cli serve`。

## 命令行

```bash
quantlab init-db                      # 初始化 SQLite 并登记已配置数据集
quantlab serve                        # 启动本机网页服务
quantlab snapshot-data-baseline       # 把当前权威数据指纹写入 runtime/baselines
quantlab verify-data-baseline         # 核对权威数据是否相对基线发生变化
quantlab materialize-pack-factors     # 从 canonical.parquet 计算并落地公式包因子
```

每个子命令都接受 `--project-root`、`--data-root`、`--calibration-root`、`--runtime-root`。

回测并行相关环境变量（也可在设置页保存，不必重启服务）。这些是一条回测内部的请求值，内存不够时运行时会再降；它们不是同时多条回测。同时回测固定为 1 条。

- `QUANTLAB_FOLD_WORKERS`：定长回看折并行数
- `QUANTLAB_BUCKET_WORKERS`：分层净值进程数
- `QUANTLAB_BUCKET_POOL`：`process`（默认）或 `thread`

16GB 内存机器请把分层进程数设为 `1`，不要按 CPU 核数开满。

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
  services/       数据、因子、训练、回测
  repositories/   SQLite 与文件注册表
  web/            HTML / CSS / JS
  config/         路径边界
  cli.py          启动与初始化
tests/quantlab/   单元测试与 Playwright 页面测试
docs/specs/       设计规格
docs/plans/       实现计划
```

运行时目录（不进 git）：

```text
quantlab_runtime/
  db/quantlab.sqlite3
  jobs/           任务日志与临时状态
  factors/        新计算的因子版本
  strategies/     策略快照
  results/        每次回测独立目录
  baselines/      数据基线指纹
```

## 文档

更细的页面与数据契约见 `docs/specs/` 与 `docs/plans/`。本机启动补充说明见 [`docs/quantlab/start-local.md`](docs/quantlab/start-local.md)。

## 许可证

[MIT](LICENSE)
