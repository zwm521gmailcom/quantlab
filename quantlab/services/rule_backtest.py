"""Rule-signal backtest: validate, generate signals, target-weight account."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.domain.status import BacktestRunStatus
from quantlab.repositories.run_lifecycle import transition_backtest_run_status
from quantlab.services.index_membership import latest_members, load_index_weight, members_on
from quantlab.services.rule_portfolio import run_target_weight_portfolio
from quantlab.strategies.registry import RULE_STRATEGIES
from quantlab.strategies.wiki_trend_follow import DEFAULTS as WIKI_DEFAULTS
from quantlab.strategies.wiki_trend_follow import generate_signals, normalize_rule_frame

_ACCOUNT_DEFAULTS: dict[str, Any] = {
    "account_mode": "target_weight_exits",
    "rebalance_every": 5,
    "stop_loss": 0.10,
    "take_profit": 0.25,
    "max_hold_days": 45,
    "sell_fee_rate": 0.0005,
    "stamp_tax_rate": 0.001,
    "buy_fee_rate": 0.0003,
    "buy_fee_minimum": 5.0,
    "sell_fee_minimum": 5.0,
    "slippage": 0.0005,
    "initial_capital": 1_000_000,
    "buy_price": "open",
    "sell_price": "close",
    "lot_size": 100,
    "target_weight": 0.10,
    "skip_open_limit": True,
    "skip_close_down_limit": True,
    "stock_scope": "中国A股（SH/SZ）",
}

_MEMBERSHIP_ERROR = "缺少沪深300/中证500成分股数据，请先在数据中心下载指数成分和权重。"
_ASOF_ERROR = "回测开始日没有沪深300或中证500成分快照，请把开始日调到成分数据覆盖之后，或在数据中心下载更早的指数成分和权重。"
_INDICATOR_WARMUP_CALENDAR_DAYS = 504


class RuleBacktestError(Exception):
    """Persisted as a failed run, not an HTTP 400."""


def validate_rule_config(config: dict[str, Any], database: Any | None = None) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("backtest config is required")
    out = dict(config)
    missing: list[str] = []
    for key in ("dataset_id", "dataset_version_id"):
        if not str(out.get(key) or "").strip():
            missing.append(key)
    test = out.get("test")
    if not isinstance(test, dict) or not str(test.get("date_from") or "").strip() or not str(test.get("date_to") or "").strip():
        missing.append("test")
    if missing:
        raise ValueError("missing config: " + ", ".join(missing))

    strategy_id = str(out.get("rule_strategy_id") or "wiki_trend_follow").strip() or "wiki_trend_follow"
    entry = RULE_STRATEGIES.get(strategy_id)
    if entry is None:
        raise ValueError(f"未知规则策略: {strategy_id}")

    if database is not None:
        _require_published_dataset(database, str(out["dataset_id"]), str(out["dataset_version_id"]))

    out["kind"] = "rule_signal"
    out["rule_strategy_id"] = strategy_id
    out["code_hash"] = entry["source_hash"]
    out["account_mode"] = "target_weight_exits"
    out.setdefault("name", "规则策略回测")
    for key, value in _ACCOUNT_DEFAULTS.items():
        out.setdefault(key, value)
    for key, value in (entry.get("defaults") or WIKI_DEFAULTS).items():
        out.setdefault(key, value)
    params = {**(entry.get("defaults") or {}), **(out.get("params") or {})}
    out["params"] = params
    out["strategy_entity_id"] = None
    out["strategy_version_id"] = None
    out["model"] = {}
    out["factor_versions"] = []

    hashed = {key: value for key, value in out.items() if key not in {"submission_token", "content_hash"}}
    digest = hashlib.sha256(
        json.dumps(hashed, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    out["content_hash"] = f"sha256:{digest}"
    return out


def execute_rule_signal(job_service: Any, run_id: str) -> dict[str, Any]:
    from quantlab.services.backtest_job import _performance_metrics

    run = job_service.get(run_id)
    if run is None:
        raise ValueError("backtest run not found")
    if run.get("status") == "failed":
        job_service._requeue_failed(run_id)
        run = job_service.get(run_id) or run
    if run.get("status") not in {"queued", "failed"}:
        raise ValueError("这条回测已经在运行或已经完成，不能重复执行。")

    job_service._prepare_steps(run_id)
    with job_service.database.transaction() as connection:
        next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.RUNNING)
        connection.execute(
            "UPDATE backtest_runs SET status=? WHERE run_id=? AND status IN ('queued', 'failed')",
            (next_status, run_id),
        )

    try:
        _run, path, _registered = job_service._path_and_row(run_id)
        config = dict(run.get("config") or {})
        raw_root = Path(job_service.settings.raw_root)
        date_from = _norm_yyyymmdd(config["test"]["date_from"])
        date_to = _norm_yyyymmdd(config["test"]["date_to"])
        job_service._step(run_id, 1, "running")
        membership_error = _membership_error(raw_root, date_from=date_from)
        if membership_error:
            raise RuleBacktestError(membership_error)
        job_service._step(run_id, 1, "completed")
        job_service._step(run_id, 2, "skipped")
        job_service._step(run_id, 3, "skipped")

        job_service._step(run_id, 4, "running")
        frame = _read_frame(path, date_from=date_from, date_to=date_to)
        history = _upto(frame, date_to)
        market = _window(frame, date_from, date_to)
        weights = load_index_weight(raw_root)
        params = _signal_params(config)
        signals = generate_signals(history, weights, params)
        window_signals = _window(signals, date_from, date_to) if signals is not None else signals
        job_service._step(run_id, 4, "completed")

        job_service._step(run_id, 5, "running")
        trades, equity = run_target_weight_portfolio(market, window_signals, config)
        job_service._step(run_id, 5, "completed")

        job_service._step(run_id, 6, "running")
        metrics = _performance_metrics(
            trades, config, market, equity_curve=equity, raw_root=raw_root
        )
        signal_rows = 0 if window_signals is None else int(len(window_signals))
        metrics["equity_curve"] = equity
        metrics["signal_rows"] = signal_rows
        metrics["flat_days"] = sum(1 for row in equity if float(row.get("invested") or 0) == 0.0)
        metrics["membership_asof"] = "monthly"
        _write_success(job_service, run_id, trades, equity, metrics)
        job_service._step(run_id, 6, "completed")
    except Exception as error:
        message = str(error)
        _write_failure(job_service, run_id, message)
        result = job_service.get(run_id) or {"run_id": run_id, "status": "failed", "error_message": message, "trades": [], "metrics": {}}
        result["error"] = result.get("error_message") or message
        return result

    result = job_service.get(run_id)
    return result


def _require_published_dataset(database: Any, dataset_id: str, dataset_version_id: str) -> None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT d.status ds, dv.status vs, dv.quality_status "
            "FROM datasets d JOIN dataset_versions dv ON d.entity_id=dv.entity_id "
            "WHERE dv.entity_id=? AND dv.version_id=?",
            (dataset_id, dataset_version_id),
        ).fetchone()
    if row is None or row["ds"] != "published" or row["vs"] != "published" or row["quality_status"] == "failed":
        raise ValueError("published quality-passed dataset version is required")


def _membership_error(raw_root: Path, date_from: str | None = None) -> str | None:
    path_300 = raw_root / "index_weight" / "index_weight_000300_SH.parquet"
    path_905 = raw_root / "index_weight" / "index_weight_000905_SH.parquet"
    if not path_300.is_file() or not path_905.is_file():
        return _MEMBERSHIP_ERROR
    weights = load_index_weight(raw_root)
    if weights.empty:
        return _MEMBERSHIP_ERROR
    codes = set(weights["index_code"].astype(str))
    if "000300.SH" not in codes or "000905.SH" not in codes:
        return _MEMBERSHIP_ERROR
    if date_from:
        if not members_on(weights, "000300.SH", date_from) or not members_on(weights, "000905.SH", date_from):
            return _ASOF_ERROR
        return None
    if not latest_members(weights, "000300.SH") or not latest_members(weights, "000905.SH"):
        return _MEMBERSHIP_ERROR
    return None


def _signal_params(config: dict[str, Any]) -> dict[str, Any]:
    params = {**WIKI_DEFAULTS, **(config.get("params") or {})}
    for key in WIKI_DEFAULTS:
        if key in config:
            params[key] = config[key]
    return params


def _read_frame(
    path: Path,
    date_from: str | None = None,
    date_to: str | None = None,
    warmup_calendar_days: int = _INDICATOR_WARMUP_CALENDAR_DAYS,
) -> pd.DataFrame:
    frame = _load_parquet_window(path, date_from, date_to, warmup_calendar_days)
    if frame.empty:
        raise RuleBacktestError("研究行情文件是空的。")
    try:
        return normalize_rule_frame(frame)
    except ValueError as error:
        raise RuleBacktestError(str(error)) from error


def _load_parquet_window(
    path: Path,
    date_from: str | None,
    date_to: str | None,
    warmup_calendar_days: int,
) -> pd.DataFrame:
    names = pq.ParquetFile(path).schema_arrow.names
    date_field = "trade_date" if "trade_date" in names else "date"
    end = _norm_yyyymmdd(date_to) if date_to else None
    start = _norm_yyyymmdd(date_from) if date_from else None
    if start and warmup_calendar_days:
        start = (datetime.strptime(start, "%Y%m%d") - timedelta(days=int(warmup_calendar_days))).strftime(
            "%Y%m%d"
        )
    dataset = ds.dataset(str(path), format="parquet")
    expression = None
    if start and end:
        expression = (pc.field(date_field) >= start) & (pc.field(date_field) <= end)
    elif end:
        expression = pc.field(date_field) <= end
    elif start:
        expression = pc.field(date_field) >= start
    try:
        table = dataset.to_table(filter=expression) if expression is not None else dataset.to_table()
    except Exception:
        table = dataset.to_table()
    frame = table.to_pandas()
    return _pandas_date_slice(frame, date_field, start, end)


def _pandas_date_slice(
    frame: pd.DataFrame,
    date_field: str,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    if frame.empty or (not start and not end):
        return frame
    field = date_field if date_field in frame.columns else ("trade_date" if "trade_date" in frame.columns else "date")
    keys = frame[field].map(_norm_yyyymmdd)
    mask = pd.Series(True, index=frame.index)
    if start:
        mask &= keys >= start
    if end:
        mask &= keys <= end
    return frame.loc[mask].copy()


def _norm_yyyymmdd(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).replace("-", "").replace(" ", "").replace("T", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return pd.Timestamp(value).strftime("%Y%m%d")


def _with_norm_date(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return frame
    result = frame.copy()
    result["_asof"] = result["date"].map(_norm_yyyymmdd)
    return result


def _upto(frame: pd.DataFrame, date_to: str) -> pd.DataFrame:
    tagged = _with_norm_date(frame)
    if tagged is None or tagged.empty:
        return frame
    return tagged.loc[tagged["_asof"] <= date_to].drop(columns=["_asof"])


def _window(frame: pd.DataFrame, date_from: str, date_to: str) -> pd.DataFrame:
    tagged = _with_norm_date(frame)
    if tagged is None or tagged.empty:
        return frame
    return tagged.loc[(tagged["_asof"] >= date_from) & (tagged["_asof"] <= date_to)].drop(columns=["_asof"])


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    return value


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False), encoding="utf-8")


def _write_success(job_service: Any, run_id: str, trades: list[dict], equity: list[dict], metrics: dict[str, Any]) -> None:
    from quantlab.services.backtest_job import _now

    output_dir = job_service.settings.runtime_root / "results" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = output_dir / "trades.json"
    metrics_path = output_dir / "metrics.json"
    curve_path = output_dir / "equity_curve.json"
    safe_metrics = _jsonable(metrics)
    _dump(trades_path, trades)
    _dump(metrics_path, safe_metrics)
    _dump(curve_path, equity)
    trade_artifact = job_service.artifacts.register(
        run_id=run_id, path=trades_path, display_name="成交明细", artifact_role="trades"
    )
    metrics_artifact = job_service.artifacts.register(
        run_id=run_id, path=metrics_path, display_name="回测指标", artifact_role="metrics"
    )
    curve_artifact = job_service.artifacts.register(
        run_id=run_id, path=curve_path, display_name="权益曲线", artifact_role="equity_curve"
    )
    artifact_ids = json.dumps(
        [trade_artifact.artifact_id, metrics_artifact.artifact_id, curve_artifact.artifact_id]
    )
    with job_service.database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_steps SET artifact_ids_json=? WHERE run_id=? AND ordinal=6",
            (artifact_ids, run_id),
        )
        next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.COMPLETED)
        connection.execute(
            "UPDATE backtest_runs SET status=?, metrics_json=? WHERE run_id=?",
            (next_status, json.dumps(safe_metrics, ensure_ascii=False), run_id),
        )
        connection.execute("UPDATE run_registry SET finished_at=? WHERE run_id=?", (_now(), run_id))


def _write_failure(job_service: Any, run_id: str, message: str) -> None:
    from quantlab.services.backtest_job import _now, STEPS

    failed_ordinal = 1
    for ordinal in range(1, len(STEPS) + 1):
        status = job_service._step_status(run_id, ordinal)
        if status in {"pending", "running"}:
            failed_ordinal = ordinal
            break
        failed_ordinal = ordinal
    job_service._step(run_id, failed_ordinal, "failed", message)
    with job_service.database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_steps SET status='skipped', finished_at=? WHERE run_id=? AND ordinal>? AND status='pending'",
            (_now(), run_id, failed_ordinal),
        )
        next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.FAILED)
        connection.execute(
            "UPDATE backtest_runs SET status=?, error_message=? WHERE run_id=?",
            (next_status, message, run_id),
        )
        connection.execute("UPDATE run_registry SET finished_at=? WHERE run_id=?", (_now(), run_id))
