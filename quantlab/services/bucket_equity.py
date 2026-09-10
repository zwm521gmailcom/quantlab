"""Same Top-N backtest inside daily market-cap and turnover quintiles."""

from __future__ import annotations

import multiprocessing
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from os import cpu_count
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantlab.services.compute_budget import active_resource_prior, cap_workers, note_workers
from quantlab.services.portfolio import _DAY_INDEX_ATTR, _cached_day_index, run_portfolio as engine_portfolio
from quantlab.services.settings import resolve_bucket_pool, resolve_worker_count, total_ram_bytes

CAP_BUCKET_LABELS = {1: "Q1 小盘", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 大盘"}
TURN_BUCKET_LABELS = {1: "Q1 低换手", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 高换手"}
SEGMENT_DIMENSIONS = (
    ("float_market_cap", "by_float_market_cap", CAP_BUCKET_LABELS),
    ("turn", "by_turn", TURN_BUCKET_LABELS),
)

_WORKER_FRAME: pd.DataFrame | None = None
_WORKER_CONFIG: dict[str, Any] | None = None


def _norm_date(value: Any) -> str:
    text = str(value or "").replace("-", "").replace(".", "").replace(" ", "").replace("T", "")
    return text[:8]


def bucket_worker_count(task_count: int) -> int:
    return resolve_worker_count("QUANTLAB_BUCKET_WORKERS", "bucket_workers", task_count, cpu_count)


def frame_nbytes(frame: pd.DataFrame) -> int:
    try:
        return int(frame.memory_usage(deep=False).sum())
    except Exception:
        return 0


def assign_daily_quintiles(frame: pd.DataFrame, field: str) -> pd.Series:
    buckets = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    if frame is None or getattr(frame, "empty", True) or field not in frame.columns:
        return buckets
    if "date" not in frame.columns:
        return buckets
    values = pd.to_numeric(frame[field], errors="coerce").to_numpy()
    dates = np.frompyfunc(_norm_date, 1, 1)(frame["date"].to_numpy())
    date_ids, _ = pd.factorize(dates, sort=False)
    order = np.argsort(date_ids, kind="mergesort")
    sorted_ids = date_ids[order]
    change = np.ones(len(sorted_ids), dtype=bool)
    if len(sorted_ids) > 1:
        change[1:] = sorted_ids[1:] != sorted_ids[:-1]
    starts = np.flatnonzero(change)
    out = np.zeros(len(frame), dtype=np.int8)
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(order)
        idx = order[start:end]
        part = np.asarray(values[idx], dtype="float64")
        finite = np.isfinite(part)
        if int(finite.sum()) < 5:
            continue
        finite_idx = idx[finite]
        try:
            codes = pd.qcut(part[finite], q=5, labels=False, duplicates="drop")
        except ValueError:
            continue
        codes = np.asarray(pd.to_numeric(codes, errors="coerce"), dtype="float64") + 1.0
        valid = np.isfinite(codes)
        out[finite_idx[valid]] = codes[valid].astype(np.int8)
    series = pd.Series(out, index=frame.index, dtype="Int64")
    return series.mask(series < 1)


def predictions_by_bucket(
    predictions: pd.DataFrame,
    frame: pd.DataFrame,
    buckets: pd.Series,
) -> dict[int, pd.DataFrame]:
    if predictions is None or getattr(predictions, "empty", True):
        return {bucket_id: predictions for bucket_id in range(1, 6)}
    keys = pd.DataFrame(
        {
            "date": frame["date"].map(_norm_date),
            "instrument": frame["instrument"].astype(str),
            "bucket": buckets,
        }
    )
    scored = predictions.copy()
    scored["date"] = scored["date"].map(_norm_date)
    scored["instrument"] = scored["instrument"].astype(str)
    merged = scored.merge(keys, on=["date", "instrument"], how="left")
    columns = [name for name in predictions.columns if name in merged.columns]
    split: dict[int, pd.DataFrame] = {}
    for bucket_id in range(1, 6):
        picked = merged.loc[merged["bucket"] == bucket_id]
        split[bucket_id] = picked.loc[:, columns].reset_index(drop=True)
    return split


def bucket_predictions(
    predictions: pd.DataFrame,
    frame: pd.DataFrame,
    buckets: pd.Series,
    bucket_id: int,
) -> pd.DataFrame:
    return predictions_by_bucket(predictions, frame, buckets)[int(bucket_id)]


def _compact_curve(curve: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = []
    for row in curve or []:
        if not isinstance(row, dict):
            continue
        equity = row.get("equity")
        try:
            number = float(equity)
        except (TypeError, ValueError):
            continue
        rows.append({"date": _norm_date(row.get("date")), "equity": number})
    return rows


def _curve_return(curve: list[dict[str, Any]]) -> float | None:
    if len(curve) < 2:
        return None
    start = curve[0]["equity"]
    end = curve[-1]["equity"]
    if not start:
        return None
    return end / start - 1.0


def _filled_instruments(trades: list[dict[str, Any]] | None) -> list[str]:
    names = []
    seen = set()
    for trade in trades or []:
        if str(trade.get("status") or "filled") not in {"filled", ""}:
            continue
        name = str(trade.get("instrument") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _run_bucket(frame: pd.DataFrame, predictions: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    if predictions is None or getattr(predictions, "empty", True):
        return {"equity_curve": [], "instruments": [], "trade_count": 0, "total_return": None}
    trades, curve = engine_portfolio(frame, predictions, config)
    compact = _compact_curve(curve)
    filled = [row for row in (trades or []) if str(row.get("status") or "filled") == "filled"]
    return {
        "equity_curve": compact,
        "instruments": _filled_instruments(trades),
        "trade_count": int(len(filled)),
        "total_return": _curve_return(compact),
    }


def _process_bucket(preds: pd.DataFrame) -> dict[str, Any]:
    return _run_bucket(_WORKER_FRAME, preds, _WORKER_CONFIG or {})


def _init_worker(path: str, config: dict[str, Any]) -> None:
    global _WORKER_FRAME, _WORKER_CONFIG
    _WORKER_FRAME = pd.read_parquet(path)
    _WORKER_CONFIG = config
    _cached_day_index(_WORKER_FRAME)


def _dump_frame(frame: pd.DataFrame) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    handle.close()
    saved = dict(frame.attrs)
    frame.attrs.clear()
    try:
        frame.to_parquet(handle.name, index=False)
    finally:
        frame.attrs.update(saved)
    return handle.name


def _run_bucket_jobs(frame: pd.DataFrame, config: dict[str, Any], pred_list: list[pd.DataFrame]) -> list[dict[str, Any]]:
    global _WORKER_FRAME, _WORKER_CONFIG
    if not pred_list:
        return []
    _WORKER_FRAME = frame
    _WORKER_CONFIG = config
    workers = bucket_worker_count(len(pred_list))
    pool_kind = resolve_bucket_pool()
    if pool_kind != "thread":
        prior = active_resource_prior() or {}
        workers = cap_workers(
            requested=workers,
            task_count=len(pred_list),
            ram_bytes=total_ram_bytes(),
            unit_bytes=frame_nbytes(frame),
            kind="bucket",
            prior_peak_rss_bytes=int(prior.get("peak_rss_bytes") or 0),
            prior_workers=int(prior.get("bucket_workers") or 0),
        )
    note_workers(bucket_workers=workers, bucket_pool=pool_kind)
    if workers <= 1:
        _cached_day_index(frame)
        return [_run_bucket(frame, preds, config) for preds in pred_list]
    if pool_kind != "thread":
        path = _dump_frame(frame)
        try:
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                initializer=_init_worker,
                initargs=(path, config),
            ) as pool:
                return list(pool.map(_process_bucket, pred_list, chunksize=1))
        finally:
            Path(path).unlink(missing_ok=True)
    _cached_day_index(frame)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_process_bucket, pred_list))


def attach_segment_curves(
    metrics: dict[str, Any],
    frame: pd.DataFrame | None,
    predictions: pd.DataFrame | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(metrics or {})
    payload: dict[str, Any] = {}
    if frame is None or getattr(frame, "empty", True) or predictions is None or getattr(predictions, "empty", True):
        result["segment_curves"] = {
            "by_float_market_cap": {"field": "float_market_cap", "status": "no_predictions", "buckets": []},
            "by_turn": {"field": "turn", "status": "no_predictions", "buckets": []},
        }
        return result
    try:
        meta: list[tuple[str, int, str]] = []
        pred_list: list[pd.DataFrame] = []
        for field, key, labels in SEGMENT_DIMENSIONS:
            if field not in frame.columns:
                payload[key] = {"field": field, "status": "missing_field", "buckets": []}
                continue
            split = predictions_by_bucket(predictions, frame, assign_daily_quintiles(frame, field))
            for bucket_id in range(1, 6):
                meta.append((key, bucket_id, labels[bucket_id]))
                pred_list.append(split[bucket_id])
        outputs = _run_bucket_jobs(frame, config or {}, pred_list)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for (key, bucket_id, label), row in zip(meta, outputs, strict=True):
            grouped.setdefault(key, []).append({"id": bucket_id, "label": label, **row})
        for field, key, _labels in SEGMENT_DIMENSIONS:
            if key in payload:
                continue
            items = grouped.get(key) or []
            available = any(item.get("equity_curve") for item in items)
            payload[key] = {
                "field": field,
                "status": "available" if available else "insufficient_data",
                "buckets": items,
            }
    finally:
        if hasattr(frame, "attrs"):
            frame.attrs.pop(_DAY_INDEX_ATTR, None)
    result["segment_curves"] = payload
    return result
