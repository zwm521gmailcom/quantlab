"""Monthly Rank IC and NDCG@10 from backtest prediction scores."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_CAPTURE: dict[str, Any] = {"frame": None, "predictions": None}


def capture_predictions(frame: pd.DataFrame, predictions: pd.DataFrame) -> None:
    _CAPTURE["frame"] = frame
    _CAPTURE["predictions"] = predictions


def take_captured() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    frame = _CAPTURE.get("frame")
    predictions = _CAPTURE.get("predictions")
    _CAPTURE["frame"] = None
    _CAPTURE["predictions"] = None
    return frame, predictions


def _month_key(value: Any) -> str:
    text = str(value or "").replace("-", "")
    return text[:6] if len(text) >= 6 else text


def _ndcg_at_k(scores: np.ndarray, returns: np.ndarray, k: int) -> float | None:
    count = len(scores)
    if count < 2:
        return None
    depth = max(1, min(int(k), count))
    relevance = pd.Series(returns).rank(method="average").to_numpy(dtype="float64")
    order = np.argsort(-np.asarray(scores, dtype="float64"), kind="mergesort")
    discounts = 1.0 / np.log2(np.arange(depth, dtype="float64") + 2.0)
    dcg = float(np.dot(relevance[order[:depth]], discounts))
    ideal = np.sort(relevance)[::-1][:depth]
    idcg = float(np.dot(ideal, discounts[: len(ideal)]))
    if idcg <= 0:
        return None
    return dcg / idcg


def monthly_ranking_metrics(
    frame: pd.DataFrame | None,
    predictions: pd.DataFrame | None,
    *,
    k: int = 10,
) -> dict[str, Any]:
    empty: dict[str, Any] = {"rank_ic": None, "ndcg_at_10": None, "monthly_ranking": []}
    if frame is None or predictions is None or predictions.empty:
        return empty
    if "score" not in predictions.columns or "date" not in predictions.columns or "instrument" not in predictions.columns:
        return empty
    ret_col = "_raw" if "_raw" in frame.columns else ("_target" if "_target" in frame.columns else None)
    if ret_col is None or "date" not in frame.columns or "instrument" not in frame.columns:
        return empty
    labeled = frame[["date", "instrument"]].copy()
    labeled["ret"] = pd.to_numeric(frame[ret_col], errors="coerce")
    scored = predictions[["date", "instrument", "score"]].copy()
    scored["score"] = pd.to_numeric(scored["score"], errors="coerce")
    merged = scored.merge(labeled, on=["date", "instrument"], how="inner")
    merged = merged.replace([np.inf, -np.inf], np.nan).dropna(subset=["score", "ret"])
    if merged.empty:
        return empty
    daily: list[dict[str, Any]] = []
    for date, group in merged.groupby("date", sort=True):
        if len(group) < 2:
            continue
        ic = group["score"].corr(group["ret"], method="spearman")
        ndcg = _ndcg_at_k(group["score"].to_numpy(), group["ret"].to_numpy(), k)
        if pd.isna(ic) and ndcg is None:
            continue
        daily.append({
            "date": str(date),
            "month": _month_key(date),
            "rank_ic": None if pd.isna(ic) else float(ic),
            "ndcg_at_10": None if ndcg is None else float(ndcg),
        })
    if not daily:
        return empty
    monthly: list[dict[str, Any]] = []
    by_month = pd.DataFrame(daily)
    for month, group in by_month.groupby("month", sort=True):
        ics = [value for value in group["rank_ic"].tolist() if value is not None]
        ndcgs = [value for value in group["ndcg_at_10"].tolist() if value is not None]
        monthly.append({
            "month": str(month),
            "days": int(len(group)),
            "rank_ic": float(np.mean(ics)) if ics else None,
            "ndcg_at_10": float(np.mean(ndcgs)) if ndcgs else None,
        })
    ics = [row["rank_ic"] for row in monthly if row["rank_ic"] is not None]
    ndcgs = [row["ndcg_at_10"] for row in monthly if row["ndcg_at_10"] is not None]
    return {
        "rank_ic": float(np.mean(ics)) if ics else None,
        "ndcg_at_10": float(np.mean(ndcgs)) if ndcgs else None,
        "monthly_ranking": monthly,
    }


def ranking_k(config: dict[str, Any] | None) -> int:
    payload = config or {}
    params = payload.get("hyperparameters") if isinstance(payload.get("hyperparameters"), dict) else {}
    raw = params.get("ndcg_eval_at") if params.get("ndcg_eval_at") is not None else payload.get("top_n")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 10
    return max(1, value)


def attach_ranking_metrics(metrics: dict[str, Any], frame: pd.DataFrame | None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    captured_frame, predictions = take_captured()
    source = captured_frame if captured_frame is not None else frame
    ranking = monthly_ranking_metrics(source, predictions, k=ranking_k(config))
    metrics["rank_ic"] = ranking["rank_ic"]
    metrics["ndcg_at_10"] = ranking["ndcg_at_10"]
    metrics["monthly_ranking"] = ranking["monthly_ranking"]
    model = metrics.get("model")
    if isinstance(model, dict):
        model["monthly_ranking"] = ranking["monthly_ranking"]
        model["rank_ic"] = ranking["rank_ic"]
        model["ndcg_at_10"] = ranking["ndcg_at_10"]
    return metrics
