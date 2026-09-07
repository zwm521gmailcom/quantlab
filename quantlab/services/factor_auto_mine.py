"""Deterministic, local-only automatic factor mining."""
from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as pa_dataset
import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import TARGET_ONLY_FIELDS, FactorRepository
from quantlab.repositories.research_runs import ResearchRunRepository
from quantlab.services.factor_manual import (
    ExpressionError,
    WINDOWLESS_METHODS,
    _METHODS,
    _eval,
    grouped_rolling,
    parse_expression,
)
from quantlab.services.index_membership import apply_pit_index_universe, filter_listed_universe
from quantlab.services.run_identity import RunIdentity

SUPPORTED_OPERATORS = frozenset({"identity", *_METHODS})
WINDOWLESS_OPERATORS = frozenset({"identity", *WINDOWLESS_METHODS})
_FIELD_LABELS = {
    "hfq_open": "后复权开盘",
    "hfq_high": "后复权最高",
    "hfq_low": "后复权最低",
    "hfq_close": "后复权收盘",
    "hfq_up_limit": "后复权涨停价",
    "hfq_down_limit": "后复权跌停价",
    "open": "原始开盘",
    "high": "原始最高",
    "low": "原始最低",
    "close": "原始收盘",
    "vol": "成交量",
    "volume": "成交量",
    "amount": "成交额",
    "adj_factor": "复权因子",
    "pe_ttm": "市盈率",
    "total_mv": "总市值",
    "circ_mv": "流通市值",
    "dv_ttm": "股息率",
    "turnover_rate": "换手率",
    "turn": "换手率",
    "up_limit": "涨停价",
    "down_limit": "跌停价",
}
_REASON_LABELS = {
    "validation_thresholds_passed": "验证期达标，可勾选入库",
    "validation_thresholds_failed": "验证期有效值或排序相关性未达标",
    "correlated_with_existing_candidate": "与已有候选高度相关，任务内已去重",
}
_FORMULA_PATTERN = re.compile(r"^([A-Za-z_][\w]*)\.([A-Za-z_]+)\((\d+)\)$")


def formula_label(formula: str) -> str:
    text = str(formula or "").strip()
    if not text:
        return "未命名公式"
    if "." not in text:
        return _FIELD_LABELS.get(text, text)
    match = _FORMULA_PATTERN.fullmatch(text)
    if match is None:
        return text
    field_label = _FIELD_LABELS.get(match.group(1), match.group(1))
    operator = match.group(2)
    window = match.group(3)
    if operator == "identity":
        return field_label
    if operator == "cs_rank":
        return f"{field_label}当天截面排名"
    templates = {
        "shift": f"{field_label}往前看{window}日",
        "diff": f"{field_label}的{window}日差分",
        "pct_change": f"{field_label}的{window}日涨跌幅",
        "rolling_mean": f"{field_label}的{window}日均值",
        "rolling_std": f"{field_label}的{window}日波动",
        "rolling_max": f"{field_label}的{window}日最高",
        "rolling_min": f"{field_label}的{window}日最低",
        "rolling_sum": f"{field_label}的{window}日合计",
        "ts_rank": f"{field_label}过去{window}日分位",
        "ts_zscore": f"{field_label}相对历史标准化",
        "rolling_bias": f"{field_label}相对均线偏离",
        "ewm_mean": f"{field_label}的指数加权均值",
    }
    return templates.get(operator, text)


def reason_label(reason: str) -> str:
    text = str(reason or "").strip()
    if text.startswith("evaluation_error"):
        return "公式算不出来"
    return _REASON_LABELS.get(text, text or "未给出原因")


def _task_item(
    *,
    run_id: str,
    config: AutoMineConfig,
    candidate: dict[str, Any],
    index: int,
    decision: str,
    reason: str,
    metrics: dict[str, Any],
    decision_period: str,
    decision_inputs: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "candidate_id": candidate["dedupe_key"],
        "index": index,
        "formula": candidate["formula"],
        "formula_label": formula_label(candidate["formula"]),
        "dedupe_key": candidate["dedupe_key"],
        "input_fields": list(candidate.get("input_fields") or []),
        "ast": candidate.get("ast") or "",
        "complexity": candidate.get("complexity"),
        "parent_candidates": candidate.get("parent_candidates") or [],
        "decision": decision,
        "kept_in_task": decision == "accepted",
        "reason": reason,
        "reason_label": reason_label(reason),
        "period_metrics": metrics,
        "decision_period": decision_period,
        "decision_inputs": decision_inputs,
        "enabled": False,
        "enabled_entity_id": None,
        "enabled_version_id": None,
        "generation_run_id": run_id,
        "dataset_id": config.dataset_id,
        "dataset_version_id": config.dataset_version_id,
    }
    if extra:
        item.update(extra)
    return item


class AutoMineError(ValueError):
    """The requested search space is unsafe or invalid."""


@dataclass(frozen=True)
class AutoMineConfig:
    dataset_id: str
    dataset_version_id: str
    date_from: str
    train_end: str
    validation_end: str
    date_to: str
    seed: int = 0
    max_candidates: int = 20
    max_depth: int = 4
    correlation_threshold: float = 0.95
    min_validation_coverage: float = 0.5
    min_validation_rank_ic: float = -1.0
    fields: tuple[str, ...] = ()
    operators: tuple[str, ...] = ("identity", "shift", "rolling_mean")
    windows: tuple[int, ...] = (1, 2, 5)
    markets: tuple[str, ...] = ()


def _date(value: str) -> str:
    normalized = str(value).replace("-", "")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as error:
        raise AutoMineError(f"invalid date: {value}") from error
    return normalized


def build_time_splits(config: AutoMineConfig) -> dict[str, dict[str, str]]:
    values = [_date(item) for item in (config.date_from, config.train_end, config.validation_end, config.date_to)]
    if not values[0] <= values[1] < values[2] <= values[3]:
        raise AutoMineError("train, validation and test boundaries overlap or are reversed")
    def next_day(value: str) -> str:
        return (datetime.strptime(value, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    return {"train": {"from": values[0], "to": values[1]}, "validation": {"from": next_day(values[1]), "to": values[2]}, "test": {"from": next_day(values[2]), "to": values[3]}}


def _formula(field: str, operator: str, window: int | None) -> str:
    if operator == "identity":
        return field
    if operator in WINDOWLESS_OPERATORS:
        return f"{field}.{operator}(0)"
    return f"{field}.{operator}({window})"


def _iter_formulas(fields: list[str], operators: list[str], windows: list[int]) -> list[str]:
    formulas: list[str] = []
    for field in sorted(set(fields)):
        for operator in operators:
            if operator in WINDOWLESS_OPERATORS:
                formulas.append(_formula(field, operator, 0))
                continue
            for window in windows or [0]:
                if window > 0:
                    formulas.append(_formula(field, operator, window))
    return formulas


def generate_candidates(fields: list[str], operators: list[str], windows: list[int], *, seed: int = 0, max_candidates: int = 20, max_depth: int = 4, correlation_threshold: float = 0.95) -> list[dict[str, Any]]:
    if max_candidates < 1 or max_depth < 1 or not 0 <= correlation_threshold <= 1:
        raise AutoMineError("invalid search limits")
    target = sorted(set(fields) & TARGET_ONLY_FIELDS)
    if target:
        raise AutoMineError(f"target-only fields are not allowed: {', '.join(target)}")
    unknown = sorted(set(operators) - SUPPORTED_OPERATORS)
    if unknown:
        raise AutoMineError(f"future or unsupported operators: {', '.join(unknown)}")
    if any(not isinstance(window, int) or isinstance(window, bool) or window < 0 for window in windows):
        raise AutoMineError("windows must be non-negative integers")
    formulas = _iter_formulas(fields, operators, windows)
    random.Random(seed).shuffle(formulas)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for formula in formulas:
        key = hashlib.sha256(formula.encode()).hexdigest()[:16]
        if key in seen:
            continue
        parsed = parse_expression(formula, set(fields))
        complexity = sum(1 for _ in ast.walk(parsed.tree))
        if complexity > max_depth * 3:
            continue
        seen.add(key)
        result.append({"formula": formula, "input_fields": parsed.input_fields, "ast": ast.dump(parsed.tree, annotate_fields=True, include_attributes=False), "complexity": complexity, "dedupe_key": key, "parent_candidates": []})
        if len(result) >= max_candidates:
            break
    return result


def _panel_columns_for_fields(database: Database, selected: list[str]) -> set[str]:
    extra = set(selected)
    for name in selected:
        record = _lookup_factor(database, name)
        if record is None:
            continue
        extra.add(str(record["entity_id"]))
        for item in json.loads(record["input_fields_json"] or "[]"):
            if isinstance(item, str) and item.strip():
                field = item.strip()
                extra.add(field)
                extra.add(field.removeprefix("factor_"))
    return extra


def _read_dataset(settings: Settings, database: Database, config: AutoMineConfig) -> pd.DataFrame:
    with database.connect() as connection:
        row = connection.execute("SELECT dv.path, dv.status, d.status AS dataset_status FROM dataset_versions dv JOIN datasets d ON d.entity_id=dv.entity_id WHERE dv.entity_id=? AND dv.version_id=?", (config.dataset_id, config.dataset_version_id)).fetchone()
    if row is None or row["status"] != "published" or row["dataset_status"] != "published":
        raise AutoMineError("published dataset version is required")
    path = settings.require_read_path(row["path"])
    names = set(pq.ParquetFile(path).schema_arrow.names)
    date_field = next((item for item in ("trade_date", "date") if item in names), None)
    instrument_field = next((item for item in ("ts_code", "instrument") if item in names), None)
    if date_field is None or instrument_field is None:
        raise AutoMineError("dataset requires date and instrument")
    wanted = {date_field, instrument_field, "hfq_open", "hfq_close", "close", "future_return", "clipped_return", "binned_return", "label", "eligible", "list_date", "delist_date", *_panel_columns_for_fields(database, list(config.fields))}
    columns = [name for name in wanted if name in names]
    extra = max([window for window in config.windows if window > 0] or [0]) * 2 + 10
    warmup = (datetime.strptime(_date(config.date_from), "%Y%m%d") - timedelta(days=extra)).strftime("%Y%m%d")
    expression = (pc.field(date_field) >= warmup) & (pc.field(date_field) <= _date(config.date_to))
    if config.markets:
        parts = [pc.ends_with(pc.field(instrument_field), f".{item.upper()}") for item in config.markets]
        market_expr = parts[0]
        for part in parts[1:]:
            market_expr = market_expr | part
        expression = expression & market_expr
    frame = pa_dataset.dataset(path, format="parquet").to_table(columns=columns, filter=expression).to_pandas()
    if "instrument" not in frame and "ts_code" in frame:
        frame = frame.rename(columns={"ts_code": "instrument"})
    if date_field != "date":
        frame = frame.rename(columns={date_field: "date"})
    frame["date"] = frame["date"].astype(str).str.replace("-", "", regex=False)
    if config.markets:
        suffixes = tuple(f".{item.upper()}" for item in config.markets)
        frame = frame.loc[frame["instrument"].astype(str).str.endswith(suffixes)]
    frame = filter_listed_universe(frame)
    frame = apply_pit_index_universe(frame, settings.raw_root)
    frame = frame.sort_values(["instrument", "date"], kind="mergesort").reset_index(drop=True)
    if frame.empty:
        market_text = "、".join(config.markets) if config.markets else "全部市场"
        raise AutoMineError(f"所选范围没有行情：{market_text}，{config.date_from}–{config.date_to}")
    return _attach_selected_inputs(frame, settings, database, list(config.fields))


def _lookup_factor(database: Database, name: str) -> sqlite3.Row | None:
    with database.connect() as connection:
        return connection.execute(
            "SELECT f.entity_id, fv.formula, fv.input_fields_json, fv.dataset_id, fv.dataset_version_id, dv.path "
            "FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id "
            "JOIN dataset_versions dv ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
            "WHERE f.entity_id IN (?, ?) AND fv.status <> 'deprecated' "
            "ORDER BY CASE fv.status WHEN 'published' THEN 0 WHEN 'validated' THEN 1 ELSE 2 END, fv.version_id DESC",
            (name, f"factor_{name}"),
        ).fetchone()


def _compute_named_factor(frame: pd.DataFrame, name: str) -> pd.Series | None:
    if "hfq_close" not in frame:
        return None
    grouped = frame.groupby("instrument", sort=False)["hfq_close"]
    if name in {"momentum_5", "factor_momentum_5"}:
        return frame["hfq_close"] / grouped.shift(5) - 1
    if name in {"volatility_5", "factor_volatility_5"}:
        ret = frame["hfq_close"] / grouped.shift(1) - 1
        return grouped_rolling(ret, frame["instrument"], 5, "std")
    return None


def _join_stored_factor(settings: Settings, path: Path, name: str, frame: pd.DataFrame) -> pd.Series | None:
    parquet = pq.ParquetFile(settings.require_read_path(path))
    names = set(parquet.schema_arrow.names)
    value_field = next((item for item in (name, name.removeprefix("factor_"), "factor_value") if item in names), None)
    date_field = next((item for item in ("trade_date", "date") if item in names), None)
    instrument_field = next((item for item in ("ts_code", "instrument") if item in names), None)
    if value_field is None or date_field is None or instrument_field is None:
        return None
    stored = parquet.read(columns=[date_field, instrument_field, value_field]).to_pandas()
    stored["date"] = stored[date_field].astype(str).str.replace("-", "", regex=False)
    stored["instrument"] = stored[instrument_field].astype(str)
    merged = frame[["date", "instrument"]].merge(
        stored[["date", "instrument", value_field]],
        on=["date", "instrument"],
        how="left",
    )
    return pd.to_numeric(merged[value_field], errors="coerce")


def _attach_selected_inputs(
    frame: pd.DataFrame,
    settings: Settings,
    database: Database,
    selected: list[str],
) -> pd.DataFrame:
    if not selected:
        return frame
    for name in selected:
        if name in frame.columns:
            continue
        computed = _compute_named_factor(frame, name)
        if computed is not None:
            frame[name] = computed
            continue
        record = _lookup_factor(database, name)
        if record is not None and record["formula"]:
            try:
                parsed = parse_expression(str(record["formula"]), set(frame.columns) - TARGET_ONLY_FIELDS)
                frame[name] = pd.Series(_eval(parsed.tree, frame), index=frame.index)
                continue
            except (ExpressionError, KeyError, TypeError, ValueError):
                joined = _join_stored_factor(settings, Path(record["path"]), name, frame)
                if joined is not None:
                    frame[name] = joined
                    continue
        raise AutoMineError(f"cannot align factor to market panel: {name}")
    return frame


def _create_run(database: Database, config: AutoMineConfig, *, parent_run_id: str | None = None) -> str:
    run_id = RunIdentity(database).next_id()
    payload = {"dataset_id": config.dataset_id, "dataset_version_id": config.dataset_version_id, "splits": build_time_splits(config), "seed": config.seed, "search": {"fields": list(config.fields), "operators": list(config.operators), "windows": list(config.windows), "markets": list(config.markets), "max_candidates": config.max_candidates, "max_depth": config.max_depth, "correlation_threshold": config.correlation_threshold, "min_validation_coverage": config.min_validation_coverage, "min_validation_rank_ic": config.min_validation_rank_ic}, "pit": {"rule": "as-of observation date", "dataset_version_id": config.dataset_version_id}}
    with database.transaction() as connection:
        connection.execute("INSERT INTO run_registry(run_id, run_type) VALUES (?, 'research')", (run_id,))
        connection.execute("INSERT INTO research_runs(run_id, name, research_type, status, config_json, parent_run_id) VALUES (?, '自动因子挖掘', 'automatic', 'queued', ?, ?)", (run_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), parent_run_id))
    return run_id


def _is_correlated(first: pd.Series, second: pd.Series, threshold: float) -> bool:
    pair = pd.concat([first, second], axis=1).dropna()
    return len(pair) >= 2 and abs(pair.iloc[:, 0].corr(pair.iloc[:, 1])) >= threshold


def _outcome(frame: pd.DataFrame) -> pd.Series:
    if "hfq_open" in frame.columns and "hfq_close" in frame.columns:
        group = frame.groupby("instrument", sort=False)
        return pd.to_numeric(group["hfq_close"].shift(-2) / group["hfq_open"].shift(-1) - 1, errors="coerce")
    for field in ("future_return", "clipped_return", "binned_return", "label"):
        if field in frame:
            return pd.to_numeric(frame[field], errors="coerce")
    raise AutoMineError("挖掘标签需要后复权开盘/收盘，或已计算的未来收益字段")


def _period_metrics(values: pd.Series, outcome: pd.Series, frame: pd.DataFrame) -> dict[str, Any]:
    finite = values.replace([np.inf, -np.inf], np.nan).notna()
    valid = finite & outcome.replace([np.inf, -np.inf], np.nan).notna()
    daily_ic: list[float] = []
    spreads: list[float] = []
    for _, group in frame.assign(_value=values, _outcome=outcome).loc[valid].groupby("date", sort=True):
        if len(group) < 2:
            continue
        ic = group["_value"].corr(group["_outcome"], method="spearman")
        if pd.notna(ic):
            daily_ic.append(float(ic))
        high, low = group["_value"].quantile(0.9), group["_value"].quantile(0.1)
        spread = group.loc[group["_value"] >= high, "_outcome"].mean() - group.loc[group["_value"] <= low, "_outcome"].mean()
        if pd.notna(spread):
            spreads.append(float(spread))
    return {"rows": len(frame), "finite_rows": int(finite.sum()), "coverage": float(finite.mean()) if len(frame) else None, "rank_ic": float(np.mean(daily_ic)) if daily_ic else None, "rank_ic_days": len(daily_ic), "group_spread": float(np.mean(spreads)) if spreads else None, "positive_spread_ratio": float(np.mean(np.asarray(spreads) > 0)) if spreads else None}


def _period_slice_metrics(
    values: dict[str, pd.Series],
    outcome: pd.Series,
    periods: dict[str, pd.DataFrame],
    names: tuple[str, ...],
) -> dict[str, Any]:
    return {
        name: _period_metrics(values[name], outcome.loc[periods[name].index], periods[name])
        for name in names
        if name in periods
    }


def mine_candidates(settings: Settings, database: Database, factors: FactorRepository, config: AutoMineConfig) -> dict[str, Any]:
    splits = build_time_splits(config)
    if not 0 <= config.min_validation_coverage <= 1:
        raise AutoMineError("min_validation_coverage must be between 0 and 1")
    frame = _read_dataset(settings, database, config)
    listing_fields = {"eligible", "list_date", "delist_date", "is_suspended", "suspended", "st_status"}
    available = set(frame.columns) - {"date", "instrument"} - TARGET_ONLY_FIELDS - listing_fields
    candidates = generate_candidates(sorted(set(config.fields) & available) if config.fields else sorted(available), list(config.operators), list(config.windows), seed=config.seed, max_candidates=config.max_candidates, max_depth=config.max_depth, correlation_threshold=config.correlation_threshold)
    run_id = _create_run(database, config)
    outcome = _outcome(frame)
    periods = {name: frame.loc[(frame["date"] >= bounds["from"]) & (frame["date"] <= bounds["to"])] for name, bounds in splits.items()}
    decision_periods = ("train", "validation")
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    vectors: list[tuple[str, pd.Series]] = []
    evaluations: list[dict[str, Any]] = []
    task_items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        try:
            tree = ast.parse(candidate["formula"], mode="eval")
            # Evaluate once on the complete, ordered panel.  Slicing before
            # evaluating would reset shift/rolling windows at every split.
            full_values = pd.Series(_eval(tree, frame), index=frame.index, dtype="float64")
            values = {name: full_values.loc[part.index] for name, part in periods.items()}
        except (ExpressionError, KeyError, ZeroDivisionError) as error:
            reason = f"evaluation_error:{type(error).__name__}"
            nan_values = {name: pd.Series(np.nan, index=part.index) for name, part in periods.items()}
            metrics = _period_slice_metrics(nan_values, outcome, periods, decision_periods)
            evaluations.append({"formula": candidate["formula"], "dedupe_key": candidate["dedupe_key"], "decision": "rejected", "decision_period": "validation", "decision_inputs": {"period": "validation"}, "reason": reason, "period_metrics": metrics})
            rejections.append({"formula": candidate["formula"], "reason": reason, "reason_label": reason_label(reason)})
            task_items.append(_task_item(run_id=run_id, config=config, candidate=candidate, index=index, decision="rejected", reason=reason, metrics=metrics, decision_period="validation", decision_inputs={"period": "validation"}))
            continue
        metrics = _period_slice_metrics(values, outcome, periods, decision_periods)
        train_values = values["train"].replace([np.inf, -np.inf], np.nan)
        comparison = next(((formula, config.correlation_threshold) for formula, previous in vectors if _is_correlated(train_values, previous, config.correlation_threshold)), None)
        if comparison is not None:
            reason = "correlated_with_existing_candidate"
            evaluations.append({"formula": candidate["formula"], "dedupe_key": candidate["dedupe_key"], "decision": "rejected", "decision_period": "train", "decision_inputs": {"period": "train", "correlation_threshold": comparison[1], "compared_with": comparison[0]}, "reason": reason, "period_metrics": metrics})
            rejections.append({"formula": candidate["formula"], "reason": reason, "reason_label": reason_label(reason), "correlation_threshold": comparison[1], "compared_with": comparison[0]})
            task_items.append(_task_item(run_id=run_id, config=config, candidate=candidate, index=index, decision="rejected", reason=reason, metrics=metrics, decision_period="train", decision_inputs={"period": "train", "correlation_threshold": comparison[1], "compared_with": comparison[0]}, extra={"compared_with": comparison[0]}))
            continue
        vectors.append((candidate["formula"], train_values))
        validation = metrics["validation"]
        passes = bool((validation["coverage"] or 0) >= config.min_validation_coverage and validation["rank_ic"] is not None and validation["rank_ic"] >= config.min_validation_rank_ic)
        reason = "validation_thresholds_passed" if passes else "validation_thresholds_failed"
        decision_inputs = {"period": "validation", "min_coverage": config.min_validation_coverage, "min_rank_ic": config.min_validation_rank_ic}
        evaluation = {"formula": candidate["formula"], "dedupe_key": candidate["dedupe_key"], "decision": "accepted" if passes else "rejected", "decision_period": "validation", "decision_inputs": decision_inputs, "reason": reason, "period_metrics": metrics}
        evaluations.append(evaluation)
        if not passes:
            rejections.append({"formula": candidate["formula"], "reason": reason, "reason_label": reason_label(reason), "validation": validation})
            task_items.append(_task_item(run_id=run_id, config=config, candidate=candidate, index=index, decision="rejected", reason=reason, metrics=metrics, decision_period="validation", decision_inputs=decision_inputs))
            continue
        item = _task_item(run_id=run_id, config=config, candidate=candidate, index=index, decision="accepted", reason=reason, metrics=metrics, decision_period="validation", decision_inputs=decision_inputs)
        task_items.append(item)
        accepted.append(item)
    summary = {"candidate_count": len(candidates), "accepted_count": len(accepted), "rejected_count": len(rejections), "seed": config.seed, "splits": splits, "selection_period": "validation", "selection_inputs": {"dataset_version_id": config.dataset_version_id, "period": "validation", "min_coverage": config.min_validation_coverage, "min_rank_ic": config.min_validation_rank_ic}, "candidates": [{"formula": item["formula"], "formula_label": item["formula_label"], "dedupe_key": item["dedupe_key"], "candidate_id": item["candidate_id"], "kept_in_task": True, "enabled": False, "period_metrics": item["period_metrics"], "selection_reason": item["reason"]} for item in accepted], "task_items": task_items, "rejections": rejections, "evaluations": evaluations, "test_usage": "final_evaluation_only"}
    completed = ResearchRunRepository(settings, database).complete(run_id, summary)
    return {"research_run": completed, "candidates": accepted, "rejections": rejections, "splits": splits, "selection_period": "validation", "selection_inputs": summary["selection_inputs"]}
