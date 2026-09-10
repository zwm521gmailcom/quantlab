# QuantLab 基础服务

当前阶段提供本地基础服务、SQLite 元数据、只读数据注册表、Artifact 登记和共享导航壳。

## 启动

在工作树根目录执行：

```bash
.venv/bin/python -m quantlab.cli init-db
.venv/bin/python -m quantlab.cli serve --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。服务只接受 loopback 地址，不支持对外监听。程序更新仍走 GitHub（`git pull` 后重启）；机器之间的行情和回测产物走 `8766`，不要用 git 同步 `data/` 或 `quantlab_runtime/`。

K 线页位于 `http://127.0.0.1:8765/kline`，只读取已登记的
`ds_hfq_market_st_v1` 不可变版本。提供以下只读接口：

- `/api/kline/query`：按标的、日期、raw/qfq/hfq、字段和分页查询，单次最多 5,000 行、最多 50 个标的；
- `/api/kline/summary`：返回过滤后的行数、标的数和日期范围；
- `/api/kline/quality`：返回 manifest、字段、日期和唯一键质量摘要；
- `/api/kline/export.csv`：沿用同样的标的/日期/字段/版本限制，最多 5,000 行，作为即时下载，不登记 Artifact。

`raw_*` 和 `hfq_*` 是持久化字段。`qfq_*` 由所选不可变版本每只股票的最后有效
`adj_factor` 归一化，仅用于展示并标记为不可训练输入。停牌空档不补价，ST 与涨跌停
按数据中可用的状态/限价字段显示。

默认读取仓库下的 `data/`，运行元数据写入 `quantlab_runtime/`，完整 Parquet 不复制到网页目录。

从其他目录启动或沿用已有数据仓库时，显式指定四个根目录：

```bash
.venv/bin/python -m quantlab.cli init-db \
  --project-root . \
  --data-root data \
  --calibration-root data/calibration \
  --runtime-root quantlab_runtime
```

也可使用 `QUANTLAB_PROJECT_ROOT`、`QUANTLAB_DATA_ROOT`、
`QUANTLAB_CALIBRATION_ROOT` 和 `QUANTLAB_RUNTIME_ROOT`。未指定项目根时使用当前工作目录，
不会从 `site-packages` 安装位置推导项目路径。

数据基线只能写入 `<runtime-root>/baselines/`：

```bash
.venv/bin/python -m quantlab.cli snapshot-data-baseline \
  --project-root . \
  --data-root data \
  --calibration-root data/calibration \
  --runtime-root quantlab_runtime
```

## 测试

```bash
.venv/bin/pytest tests/quantlab -q
.venv/bin/ruff check quantlab tests/quantlab
```

浏览器测试需要先安装 Playwright Chromium：

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/pytest tests/quantlab/browser/test_foundation.py -q
```
