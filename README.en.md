# QuantLab

[中文](README.md) | **English**

A local research workbench for factors, models, and backtests. Each instance declares an asset version (A-share by default). You operate it in the browser. FastAPI listens on LAN `0.0.0.0:8765` by default (loopback `127.0.0.1` still works); the UI port and LAN sync port are configurable. Metadata lives in SQLite; market data and factors stay in local Parquet files.

Current version `0.4.0` (Alpha). Runtime does not depend on [vnpy](https://github.com/vnpy/vnpy). This repository is a standalone project. Bump `quantlab/__init__.py` on each feature so every machine shows the same sidebar version after `git pull` and restart.

## What it is

QuantLab keeps data, factors, models, backtests, and result archives in one local workbench. Authoritative market files stay where they are and are read-only. Pages load them on demand through a registry and API; the full wide table is never copied into the web tree. Factors, models, strategies, and backtests use immutable versions or run snapshots. Failed jobs still keep their config, logs, and any files they already wrote.

Official trading and factor math use backward-adjusted fields: `hfq_open`, `hfq_high`, `hfq_low`, `hfq_close`.

## Workbench

After start, open http://127.0.0.1:8765/ on this machine, or `http://<LAN-IP>:8765/` from another computer on the same LAN.

| Page | Path | Role |
|---|---|---|
| Overview | `/` | Datasets, factors, recent runs, quality alerts |
| Data center | `/data` | Registered datasets, versions, coverage, quality |
| Canonical bars | `/kline` | Read-only raw / hfq bar queries |
| Factor research | `/factors` | Factor catalog, diagnostics, detail |
| Manual factor | `/factors/new/manual` | Write a formula and register it |
| Factor mining | `/research/factor-mining` | Search candidate expressions on the wide table |
| Factor jobs | `/research/factor-jobs` | Calculation jobs and progress |
| Model center | `/models` | Register model kinds used by backtests |
| Backtest workbench | `/backtests/new` | Freeze data, factors, model, and matching rules, then run |
| Backtest plan | `/backtests/plan` | A checklist of jobs, executed one after another |
| Rule backtest | `/backtests/rules` | Built-in rule templates, no model training |
| Result archive | `/backtests/runs` | History, metrics, artifacts, copy-as-draft |
| Settings | `/settings` | Paths, asset version and ports, Tushare token, compute resources, draft defaults |

Model kinds include LightGBM / XGBoost ranking trees, plus random forest, Ridge, Lasso, and other regressors. Factor sets and training parameters are chosen on the backtest workbench; training starts when a backtest starts.

## Requirements

Python 3.10+, macOS or Linux. Create a virtualenv at the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Browser tests also need:

```bash
python -m playwright install chromium
```

## Data directories

By default the app reads `data/` under the repo and writes run output to `quantlab_runtime/` (neither is committed).

| Root | Default | Environment variable |
|---|---|---|
| Project | current working directory | `QUANTLAB_PROJECT_ROOT` |
| Market / canonical table | `<project>/data` | `QUANTLAB_DATA_ROOT` |
| Calibration / experiments | `<project>/data/calibration` | `QUANTLAB_CALIBRATION_ROOT` |
| Tushare raw API files | `<project>/data/raw` | `QUANTLAB_RAW_ROOT` |
| Runtime (SQLite, jobs, results) | `<project>/quantlab_runtime` | `QUANTLAB_RUNTIME_ROOT` |

Point an existing warehouse at QuantLab with environment variables; you do not need to move files:

```bash
export QUANTLAB_DATA_ROOT=data
export QUANTLAB_CALIBRATION_ROOT=data/calibration
export QUANTLAB_RUNTIME_ROOT=quantlab_runtime
```

The same roots can be passed as `--data-root`, `--calibration-root`, `--runtime-root`, and `--project-root`. Use paths relative to the project root, such as `data` and `quantlab_runtime`.

On a LAN, all three machines must listen on the LAN — not loopback only. Program updates still go through GitHub: push and merge here, then `git pull` and restart QuantLab on the other machines. Each machine keeps its own SQLite. The UI defaults to `0.0.0.0:8765`; file sync uses `8766` (HTTP + UDP beacons, no UI, no database port, no token). Both ports can be changed on the Settings page or via startup flags. Discovery only lists peers with the same asset version; do not sync an A-share instance with a crypto instance. Allow the UI port and sync port you actually use on the firewall. From one computer’s browser, use “Open page” on the Settings LAN list to reach each machine’s QuantLab and start backtests in turn; then return to the host Settings page to sync results and market data. “Sync backtest results” fills missing `results/<run_id>/` trees (existing directories are not overwritten; `_deleted/` markers are merged). Data sync compares size and mtime first and only copies missing or changed files in the checked categories (canonical / derived / raw / source_tables). Optional 04:00 sync uses this machine as the source for the checked categories while the service is running; a missed 04:00 is not caught up. `--host` accepts `127.0.0.1`, `0.0.0.0`, or an RFC1918 address — not a public IP. Do not use git to sync `quantlab_runtime/db/` or `config/` (machine id, compute settings, token). Do not mirror-delete the whole `results/` tree. Starting the service or opening the result archive backfills missing machine ids, applies delete markers from `_deleted/`, then scans `results/*/run.json` into the local database.

Canonical bars come from `data/canonical.parquet` (one row per stock per day). Configure the Tushare token on the settings page; raw API files go to `data/raw/`.

## Start

```bash
quantlab init-db
quantlab serve
```

The instance defaults to A-share, UI `0.0.0.0:8765`, LAN sync `8766`. Open http://127.0.0.1:8765/ locally, or `http://<LAN-IP>:8765/` from another machine. `--host` cannot be a public IP.

For a crypto instance, use a separate directory and declare asset plus ports:

```bash
quantlab serve --asset crypto --port 8775 --lan-port 8776
```

The Settings page also writes `quantlab_runtime/config/instance.json` (restart to rebind). Precedence: CLI flags `>` `QUANTLAB_ASSET` / `QUANTLAB_PORT` / `QUANTLAB_LAN_PORT` `>` `instance.json` `>` defaults. The UI port and LAN port must differ.

Equivalent: `python -m quantlab.cli init-db` / `python -m quantlab.cli serve`.

## CLI

```bash
quantlab init-db                      # initialize SQLite and register configured datasets
quantlab serve                        # start the local web service (--asset / --port / --lan-port)
quantlab snapshot-data-baseline       # write current authoritative-data fingerprints
quantlab verify-data-baseline         # check whether authoritative data drifted
quantlab materialize-pack-factors     # compute the formula-pack factors from canonical.parquet
```

Every subcommand accepts `--project-root`, `--data-root`, `--calibration-root`, and `--runtime-root`. `serve` also accepts `--asset` (`a_share` or `crypto`), `--port`, and `--lan-port`.

Backtest parallelism (also editable on the settings page; no service restart required). Fold and bucket workers are per-run request values; the runtime may lower them when RAM is tight. Concurrent backtests are computed from installed RAM and CPU cores; there is no manual slot control. 16 GB machines stay at 1.

- `QUANTLAB_FOLD_WORKERS`: parallel walk-forward folds
- `QUANTLAB_BUCKET_WORKERS`: bucket-equity worker count
- `QUANTLAB_BUCKET_POOL`: `process` (default) or `thread`

On a 16 GB machine set bucket workers to `1`. Do not scale them to the CPU core count.

## Tests

```bash
pytest tests/quantlab -q
ruff check quantlab tests/quantlab
```

Browser tests:

```bash
pytest tests/quantlab/browser -q
```

## Layout

```text
quantlab/
  api/            FastAPI routes and local pages
  domain/         entities, identifiers, status transitions
  services/       data, factors, training, backtests
  repositories/   SQLite and file registry
  web/            HTML / CSS / JS
  config/         path boundaries
  cli.py          start and initialize
tests/quantlab/   unit tests and Playwright page tests
docs/specs/       design specs
docs/plans/       implementation plans
```

Runtime tree (not in git):

```text
quantlab_runtime/
  db/quantlab.sqlite3
  jobs/           job logs and transient state
  factors/        newly computed factor versions
  strategies/     strategy snapshots
  results/        one directory per backtest run
  baselines/      data-baseline fingerprints
```

## Docs

Page and data contracts live in `docs/specs/` and `docs/plans/`. Extra local-start notes: [`docs/quantlab/start-local.md`](docs/quantlab/start-local.md).

## License

[MIT](LICENSE)
