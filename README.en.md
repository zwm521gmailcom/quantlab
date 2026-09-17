# QuantLab

[中文](README.md) | **English**

A local research workbench for factors, models, and backtests. Each instance declares an asset version (A-share by default). You operate it in the browser. FastAPI listens on LAN `0.0.0.0:8765` by default (loopback `127.0.0.1` still works). The UI port and LAN sync port are configurable. Metadata lives in SQLite; market data and factors stay in local Parquet files.

Current version **`0.4.1`** (Alpha). The number lives in `quantlab/__init__.py`. Bump it on each user-facing feature so every machine shows the same sidebar version after `git pull` and restart. Runtime does not depend on [vnpy](https://github.com/vnpy/vnpy).

Public briefing (screenshots and contact, no source): [quantlab-intro](https://github.com/zwm521gmailcom/quantlab-intro).

## What it is

QuantLab keeps data, factors, models, backtests, and result archives in one local workbench. Authoritative market files stay where they are and are read-only. Pages load them on demand through a registry and API; the full wide table is never copied into the web tree. Factors, models, strategies, and backtests use immutable versions or run snapshots. Failed jobs still keep their config, logs, and any files they already wrote.

Official trading and factor math use backward-adjusted fields: `hfq_open`, `hfq_high`, `hfq_low`, `hfq_close`.

## Workbench

After start, open http://127.0.0.1:8765/ on this machine, or `http://<LAN-IP>:8765/` from another computer on the same LAN. The sidebar is built by page scripts and includes dark mode.

| Page | Path | Role |
|---|---|---|
| Overview | `/` | Datasets, factors, recent runs, quality alerts |
| Data center | `/data` | Registered raw / source tables, coverage, quality |
| Canonical bars | `/kline` | Read-only raw / hfq bar queries |
| Factor research | `/factors` | Factor catalog, diagnostics, detail |
| Manual factor | `/factors/new/manual` | Write a formula and register it |
| Factor mining | `/research/factor-mining` | Search candidate expressions on the wide table |
| Factor jobs | `/research/factor-jobs` | Calculation jobs and progress |
| Model center | `/models` | Register model kinds used by backtests; no training here |
| Backtest workbench | `/backtests/new` | Freeze data, factors, universe, model, and matching rules, then run |
| Backtest plan | `/backtests/plan` | A checklist of jobs; concurrent runs follow RAM and CPU |
| Rule backtest | `/backtests/rules` | Built-in rule templates, no model training |
| Offline policy learning | `/backtests/offline-rl` | Discrete fitted Q from archived calendar-year backtests; opening the page does not run |
| Result archive | `/backtests/runs` | History, metrics, artifacts; sortable columns; copy-as-draft does not auto-run |
| Settings | `/settings` | Paths, asset and ports, Tushare token and points, LAN sync, compute resources, draft defaults |

Model kinds include LightGBM / XGBoost ranking trees, random forest, Ridge, Lasso, and other regressors, plus single-factor `factor_rank`. Tree and regressor parameters are set on the workbench; **training starts when a backtest starts**. `factor_rank` ranks the chosen factor cross-sectionally and does not train; it reads only the selected constituents and the test window (plus MA warmup). The UI labels those steps “prepare data / factor rank”.

The stock universe is independent of the train/test date windows: exchange scope plus a multi-select of index memberships, shared by both splits, with duplicate codes removed. Membership is point-in-time from downloaded `index_weight` files (as-of, no next-period leak); multiple indexes are unioned. Default is CSI800 (CSI 300 ∪ CSI 500). The selected codes are stored in the run snapshot. An index without weight files cannot be used. The benchmark-MA open gate only blocks new entries. Batch scans may set `segment_curves: false` in the plan config to skip cap/turnover bucket equities; the main curve still runs.

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

Point an existing warehouse at QuantLab with environment variables or CLI flags; you do not need to move files. Use paths relative to the project root, such as `data` and `quantlab_runtime`.

Canonical bars come from `data/canonical.parquet` (one row per stock per day). Configure the Tushare token and points on the settings page. Download rate and per-API daily caps follow [doc_id=290](https://tushare.pro/document/1?doc_id=290); with no points saved, the 120-point tier is used. On a cap hit, downloads stop until the next day. Raw API files go to `data/raw/` (including index daily bars and `index_weight`). Formula-pack factors are materialized from the canonical table with `quantlab materialize-pack-factors`.

## LAN

All machines must listen on the LAN — not loopback only. Program updates still go through GitHub: push and merge here, then `git pull` and restart QuantLab on the other machines. If a machine cannot reach GitHub, copy the same commit with `git bundle` after merge and restart; do not sync source by copying disks. Each machine keeps its own SQLite.

- UI defaults to `0.0.0.0:8765`; file sync uses `8766` (HTTP + UDP beacons, no UI, no database port, no token)
- Both ports can be changed on Settings or via startup flags, and they must differ
- Discovery only lists peers with the same asset version; do not sync an A-share instance with a crypto instance
- Allow the UI port and sync port you actually use on the firewall
- From one browser, use “Open page” on the Settings LAN list to reach each machine

Sync behavior:

- **Backtest results:** fill missing `results/<run_id>/` trees (existing directories are not overwritten; `_deleted/` markers are merged). Plan snapshots come along. If a peer still has an old LAN index, plans are pulled from the UI API
- **Data:** compare size and mtime first; skip files the peer already has unchanged. Only push checked categories: canonical / derived / raw / source_tables. Do not sync the factor SQLite or `quantlab_runtime/factors/`
- **04:00 optional:** this machine as source for the checked categories while the service is running; a missed 04:00 is not caught up

`--host` accepts `127.0.0.1`, `0.0.0.0`, or an RFC1918 address — not a public IP.

Do not use git or disk copies to sync `quantlab_runtime/db/` or `config/` (machine id, compute settings, token). Do not mirror-delete the whole `results/` tree. Starting the service or opening the result archive backfills missing machine ids, applies delete markers from `_deleted/`, then scans `results/*/run.json` into the local database. Give each machine a different run-id serial prefix.

## Start

```bash
quantlab init-db
quantlab serve
```

The instance defaults to A-share, UI `0.0.0.0:8765`, LAN sync `8766`. Open http://127.0.0.1:8765/ locally. `--host` cannot be a public IP.

For a crypto instance, use a separate directory and declare asset plus ports:

```bash
quantlab serve --asset crypto --port 8775 --lan-port 8776
```

The Settings page also writes `quantlab_runtime/config/instance.json` (restart to rebind). Precedence: CLI flags `>` `QUANTLAB_ASSET` / `QUANTLAB_PORT` / `QUANTLAB_LAN_PORT` `>` `instance.json` `>` defaults.

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

## Backtest parallelism

Editable on the settings page; no UI-service restart required. Fold and bucket workers are per-run request values; the runtime may lower them when RAM is tight. Concurrent backtests are computed from installed RAM and CPU cores; there is no manual slot control. 16 GB machines stay at 1.

- `QUANTLAB_FOLD_WORKERS`: parallel walk-forward folds
- `QUANTLAB_BUCKET_WORKERS`: bucket-equity worker count
- `QUANTLAB_BUCKET_POOL`: `process` (default) or `thread`

On a 16 GB machine set bucket workers to `1`. Do not scale them to the CPU core count. Stop compute with “Force stop backtest” on the workbench; do not kill the whole QuantLab process. An OS OOM kill must not be recorded as a user force-stop.

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
  services/       data, factors, training, backtests, LAN sync
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
  results/        one directory per backtest run (includes plan snapshots)
  config/         machine id, compute settings, token, instance.json
  baselines/      data-baseline fingerprints
```

## Docs

Page and data contracts live in `docs/specs/` and `docs/plans/`. Extra local-start notes: [`docs/quantlab/start-local.md`](docs/quantlab/start-local.md).

## License

[MIT](LICENSE)
