# QuantLab

本机 A 股因子、模型与回测研究平台。浏览器操作，FastAPI 只监听 `127.0.0.1`，元数据在 SQLite，行情与因子在本地 Parquet。

运行时不依赖 [vnpy](https://github.com/vnpy/vnpy)。本仓库是独立项目。

## 环境

Python 3.10+。建议在仓库根创建虚拟环境：

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

已有数据仓库可以只改环境变量，不必搬文件：

```bash
export QUANTLAB_DATA_ROOT=/path/to/market-data
export QUANTLAB_CALIBRATION_ROOT=/path/to/calibration
export QUANTLAB_RUNTIME_ROOT=/path/to/quantlab_runtime
```

也可以在命令行传 `--data-root`、`--calibration-root`、`--runtime-root`、`--project-root`。

## 启动

```bash
quantlab init-db
quantlab serve --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765/ 。服务只接受 loopback，不对外监听。

等价写法：`python -m quantlab.cli init-db` / `python -m quantlab.cli serve`。

Tushare 下载在设置页配置 token，原始接口文件写入 `data/raw/`。

## 测试

```bash
pytest tests/quantlab -q
```

浏览器测试：

```bash
pytest tests/quantlab/browser -q
```

## 目录

- `quantlab/` 应用：API、服务、页面、策略注册表
- `tests/quantlab/` 单元测试与 Playwright 页面测试
- `docs/specs/`、`docs/plans/` 设计与实现计划

部分训练/回测模块目前以恢复出的 `.pyc` 运行（`quantlab/services/_recovered_pyc/`），对应 `.py` 是加载器。
