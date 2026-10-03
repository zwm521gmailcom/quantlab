# QuantLab

[中文](README.md) | **English**

A local research workbench for factors, models, and backtests. Pages, program, and tests live in this repository. Each instance declares an asset version (A-share by default). You operate it in the browser. FastAPI listens on LAN `0.0.0.0:8765` by default (loopback `127.0.0.1` still works). The UI port and LAN sync port are configurable. Metadata lives in SQLite; market data and factors stay in local Parquet files.

Current version **`0.4.2`** (Alpha). The number lives in `quantlab/__init__.py`. Bump it on each user-facing feature so every machine shows the same sidebar version after `git pull` and restart.

UI screenshots live in [`docs/screenshots/`](docs/screenshots/). This file does not embed them.

## What it is

QuantLab keeps data, factors, models, backtests, and result archives in one local workbench. Authoritative market files stay where they are and are read-only. Pages load them on demand through a registry and API; the full wide table is never copied into the web tree. Factors, models, strategies, and backtests use immutable versions or run snapshots. Failed jobs still keep their config, logs, and any files they already wrote.

Official trading and factor math use backward-adjusted fields: `hfq_open`, `hfq_high`, `hfq_low`, `hfq_close`.

## Workbench

After start, open http://127.0.0.1:8765/ on this machine, or `http://<LAN-IP>:8765/` from another computer on the same LAN. The sidebar is built by page scripts. It starts in dark mode, can switch to light, and can auto-hide.

| Page | Path | Role |
|---|---|---|
| Overview | `/` | Distributions of screened factors and local books |
| Data center | `/data` | Registered raw / source tables, coverage, quality; each Tushare API can backfill its own tail or stop the current backfill |
| Canonical bars | `/kline` | Read-only raw / hfq bar queries |
| Factor research | `/factors` | Factor catalog, diagnostics, detail |
| Manual factor | `/factors/new/manual` | Write a formula and register it |
| Qlib factor mining | `/qlib` | Local GPT-OSS 120B proposes one economic hypothesis and up to four formulas; a formula is kept from its own book |
| Mining log | `/qlib/runs` | Validation book, test book, and status for each formula |
| Screened picks | `/qlib/picks` | Factors whose own test book beats that factor’s benchmark with positive IR; one formula per window family |
| Model center | `/models` | Register model kinds used by backtests; no training here |
| Backtest workbench | `/backtests/new` | Freeze data, factors, universe, model, and matching rules, then run |
| Backtest plan | `/backtests/plan` | A checklist of jobs; concurrent reads and the steady cap follow memory |
| Usable versions | `/backtests/usable` | Versions already screened and ready to reuse |
| Rule backtest | `/backtests/rules` | Built-in rule templates, no model training |
| Offline policy learning | `/backtests/offline-rl` | Discrete fitted Q from archived calendar-year backtests; opening the page does not run |
| Result archive | `/backtests/runs` | History, metrics, artifacts; sortable columns; copy-as-draft does not auto-run |
| Settings | `/settings` | Paths, asset and ports, Tushare token and points, LAN sync, compute resources, draft defaults |

Overview reads screened picks and `/api/overview/charts`. The upper charts are test information ratio, excess annual return, max drawdown, test IC, and benchmark annual return for factors that passed, plus information ratio against excess return and validation IC against test IC. The lower charts are backtest kind, completion, annual return or drawdown for mining Top-50 / multi-factor / single-factor / LightGBM books, factor-calculation IC, coverage, version quality, and dataset row counts.

Model kinds include LightGBM / XGBoost ranking trees, random forest, Ridge, Lasso, and other regressors, Qlib LightGBM regression, plus single-factor `factor_rank`. Tree and regressor parameters are set on the workbench; **training starts when a backtest starts**. `factor_rank` ranks the chosen factor cross-sectionally and does not train; it reads only the selected constituents and the test window (plus MA warmup). The UI labels those steps “prepare data / factor rank”. Multi-factor Qlib scores are trained inside the backtest; dates before the training end are not scored.

The stock universe is independent of the train/test date windows: exchange scope plus a multi-select of index memberships, shared by both splits, with duplicate codes removed. Membership is point-in-time from downloaded `index_weight` files (as-of, no next-period leak); multiple indexes are unioned. Default is CSI800 (CSI 300 ∪ CSI 500). The selected codes are stored in the run snapshot. An index without weight files cannot be used. Training and test both drop ST names and one-word limit boards. A limit-up or limit-down open is not bought. A limit-down open is not sold; the position is carried to the next session until it can be sold. The benchmark-MA open gate only blocks new entries. Batch scans may set `segment_curves: false` in the plan config to skip cap/turnover bucket equities; the main curve still runs.

The old automatic factor-mining and factor-job pages are retired. They redirect to Qlib mining and factor research. A data-center backfill does not extend history before existing files and does not rebuild the canonical table. Before 16:00 Shanghai time it does not write the current session into daily files. Only one backfill runs at a time. Stop finishes the write already in progress and keeps rows already stored.

## Qlib mining

Convert the canonical table into Qlib daily data first. Backward-adjusted prices become `open` / `high` / `low` / `close`. Volume stays `vol` (lots). The factor column is hfq close divided by raw close. Suspended sessions store empty price and volume. Conversion joins daily basic indicators and money-flow fields by stock and trade date. `000001.SZ` is written as `SZ000001`.

Formula fields:

- Prices: `open`, `high`, `low`, `close`, `volume`
- Daily basics: `turnover_rate`, `turnover_rate_f`, `volume_ratio`, `pe`, `pe_ttm`, `pb`, `ps`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `total_share`, `float_share`, `free_share`, `total_mv`, `circ_mv`
- Money flow: buy and sell volume and amount for small, medium, large, and extra-large orders, plus `net_mf_vol` and `net_mf_amount`

Operators are `+ - * /`, parentheses, `Ref(series, integer)`, `Mean(series, integer)`, and `Std(series, integer)`. Windows are integers from 1 to 120.

The loop uses local GPT-OSS 120B only. If llama-server is down when the loop starts, QuantLab launches it and waits until it can answer. Each reply is one economic hypothesis and one to four formulas. A name or formula that matches one already kept is skipped; the same text with spaces removed still counts as a duplicate. Every other formula trains its own LightGBM. The validation window and the test window each hold the 50 names with the highest score that day and sell a name the day it leaves that list. Annual return, drawdown, and information ratio on the page are that formula’s own book. An Alpha158 portfolio is a separate book and does not fill the mining quota.

The mining count is the number of formulas to keep. A value of 1000 stops after 1000 formulas pass their own test book. A pass means the test annual return is above that factor’s own benchmark and the test information ratio is positive. A model interruption, a failed prompt, a duplicate formula, or a formula whose own test book misses the line does not count. Stop ends the current mining job and shuts down the local model. Start stays disabled while a loop is running. Saving the form does not start. Dates on or before the training end are not scored. Resume picks an unfinished job from the dropdown.

The prompt lists formulas already kept, plus validation IC and validation information ratio for the recent kept formulas. The previous round’s feedback includes validation information ratio, test information ratio, and whether that formula passed. Test annual return and drawdown stay on the mining log.

Each mining-log row is one formula. Columns follow the scoring order: validation IC, validation annual return, validation information ratio, then test IC, test annual return, test drawdown, and test information ratio. The status is passed when the test annual return beats that factor’s own benchmark and the test information ratio is positive; scored when both windows have numbers and the line is missed; pending when those numbers are not ready yet. A broken formula or a failed calculation is an error. Errors and interruptions can be deleted, and a whole plan can be deleted.

Screened picks use the same own-book test annual return and information ratio. Formulas that differ only by `Ref`, `Mean`, or `Std` windows are one family; the family keeps the highest information ratio, and an equal ratio keeps the earlier formula. Negative validation or test IC with positive annual return means the model is trading low factor values. Columns are sortable.

## Backtest admission

The plan and the workbench share one gate. How many market-data reads run together, and how many backtests stay up in the steady phase, follow installed RAM and memory available at the time. Settings control walk-forward folds and bucket-equity workers. The concurrent backtest count shown there follows memory. A 16 GB machine still runs one backtest at a time. When free memory cannot cover the next read, the next job stays queued and jobs already running keep going. The plan status line shows running jobs, jobs reading data, and the cap.

A plan that is not running can drop selected jobs, or delete the whole plan. Deleting the plan also removes the backtests and artifacts it already produced. A running plan cannot be deleted.

- `QUANTLAB_FOLD_WORKERS`: parallel walk-forward folds
- `QUANTLAB_BUCKET_WORKERS`: bucket-equity worker count
- `QUANTLAB_BUCKET_POOL`: `process` (default) or `thread`

Fold and bucket workers are per-run request values; the runtime may lower them when RAM is tight. On a 16 GB machine set bucket workers to `1`. Do not scale them to the CPU core count. Stop compute with “Force stop backtest” on the workbench; do not kill the whole QuantLab process. An OS OOM kill must not be recorded as a user force-stop.

A result-archive detail row shows information ratio, validation annual return, and validation information ratio next to annual return.

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

Canonical bars come from `data/canonical.parquet` (one row per stock per day). Configure the Tushare token and points on the settings page. Download rate and per-API daily caps follow [doc_id=290](https://tushare.pro/document/1?doc_id=290); with no points saved, the 120-point tier is used. On a cap hit, downloads stop until the next day. Raw API files go to `data/raw/` (including index daily bars and `index_weight`). The commands below materialize formula-pack, money-flow, and Alpha191 columns from the canonical table, then register them in the factor catalog.

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
quantlab init-db                         # initialize SQLite and register configured datasets
quantlab serve                           # start the local web service (--asset / --port / --lan-port)
quantlab snapshot-data-baseline          # write current authoritative-data fingerprints
quantlab verify-data-baseline            # check whether authoritative data drifted
quantlab materialize-pack-factors        # compute the formula-pack factors from canonical.parquet
quantlab materialize-moneyflow-factors   # materialize money-flow factors from the canonical table and raw files
quantlab register-pack-factors           # publish formula-pack fields into the factor catalog (--field repeats)
quantlab register-moneyflow-factors      # publish money-flow factors into the factor catalog
quantlab register-alpha191-factors       # publish Alpha191 into the factor catalog
quantlab append-alpha191-factors         # append Alpha191 columns to the existing factor sidecar
```

Every subcommand accepts `--project-root`, `--data-root`, `--calibration-root`, and `--runtime-root`. `serve` also accepts `--asset` (`a_share` or `crypto`), `--port`, and `--lan-port`.

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
docs/screenshots/ UI screenshots (not embedded in this file)
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

The current description is this file and [`docs/quantlab/start-local.md`](docs/quantlab/start-local.md). Dated notes under `docs/plans/` and `docs/specs/` record how a change was built; they are not the current page list. Screenshots belong only in [`docs/screenshots/`](docs/screenshots/).

## License

[MIT](LICENSE)
