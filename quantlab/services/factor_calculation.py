"""Versioned factor metric calculations persisted as calculation runs."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.factor_data import FEATURE_FIELDS, TARGET_ONLY_FIELDS
from quantlab.services.factor_manual import ExpressionError, _eval, finite_factor_values, grouped_rolling, parse_expression
from quantlab.services.index_membership import apply_pit_index_universe, filter_listed_universe


MAX_SERIES_POINTS = 400
HISTOGRAM_BINS = 60
HISTOGRAM_LOG_RATIO = 10.0
HISTOGRAM_POSITIVE_SHARE = 0.90
DEFAULT_LABEL = "t+1 open -> t+2 close"
CONDITIONAL_FIELDS = ("float_market_cap", "turn")
CAP_BUCKET_LABELS = {1: "Q1 小盘", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 大盘"}
TURN_BUCKET_LABELS = {1: "Q1 低换手", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 高换手"}
CONDITIONAL_DIMENSIONS = (
    ("float_market_cap", "by_float_market_cap", CAP_BUCKET_LABELS),
    ("turn", "by_turn", TURN_BUCKET_LABELS),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normal_cdf(value: float, mean: float, std: float) -> float:
    if not math.isfinite(std) or std <= 0:
        return 0.0 if value < mean else 1.0
    return 0.5 * (1.0 + math.erf((value - mean) / (std * math.sqrt(2.0))))


def _can_log_window(lower: float, upper: float, window: pd.Series) -> bool:
    return bool(
        lower > 0
        and upper > lower
        and (upper / lower) >= HISTOGRAM_LOG_RATIO
        and len(window) > 0
        and bool((window > 0).all())
    )


def histogram_needs_rebuild(histogram: list[dict[str, Any]] | None, histogram_range: dict[str, Any] | None) -> bool:
    items = histogram or []
    meta = histogram_range or {}
    if meta.get("basis") != "p1_p99":
        return True
    if meta.get("scale") not in {"linear", "log"}:
        return True
    if not items:
        return True
    if "expected" not in items[0]:
        return True
    if len(items) >= 8:
        total = sum(int(item.get("count") or 0) for item in items)
        peak = max(int(item.get("count") or 0) for item in items)
        if total > 0 and peak / total >= 0.5:
            return True
    return False


def build_factor_histogram(values: pd.Series) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    empty_range = {
        "basis": "p1_p99",
        "scale": "linear",
        "lower": None,
        "upper": None,
        "lower_tail_count": 0,
        "upper_tail_count": 0,
        "fit_mean": None,
        "fit_std": None,
    }
    if numeric.empty:
        return [], empty_range
    p1 = float(numeric.quantile(0.01))
    p99 = float(numeric.quantile(0.99))
    core = numeric[(numeric >= p1) & (numeric <= p99)]
    lower_tail = int((numeric < p1).sum())
    upper_tail = int((numeric > p99).sum())
    use_log = _can_log_window(p1, p99, core)
    if not use_log and len(numeric) and float((numeric > 0).mean()) >= HISTOGRAM_POSITIVE_SHARE:
        positive = numeric[numeric > 0]
        p1p = float(positive.quantile(0.01))
        p99p = float(positive.quantile(0.99))
        core_p = positive[(positive >= p1p) & (positive <= p99p)]
        if _can_log_window(p1p, p99p, core_p):
            p1, p99, core = p1p, p99p, core_p
            lower_tail = int((numeric < p1p).sum())
            upper_tail = int((numeric > p99p).sum())
            use_log = True
    scale = "log" if use_log else "linear"
    meta = {
        "basis": "p1_p99",
        "scale": scale,
        "lower": p1,
        "upper": p99,
        "lower_tail_count": lower_tail,
        "upper_tail_count": upper_tail,
        "fit_mean": _number(core.mean()) if len(core) else p1,
        "fit_std": _number(core.std(ddof=0)) if len(core) > 1 else 0.0,
    }
    if core.empty or p1 == p99:
        count = int(core.size)
        return [{"left": p1, "right": p99, "count": count, "expected": float(count)}], meta
    if use_log:
        edges = np.logspace(math.log10(p1), math.log10(p99), HISTOGRAM_BINS + 1)
        fit = np.log(core.to_numpy())
    else:
        edges = np.linspace(p1, p99, HISTOGRAM_BINS + 1)
        fit = core.to_numpy()
    counts, edges = np.histogram(core.to_numpy(), bins=edges)
    fit_mean = float(np.mean(fit))
    fit_std = float(np.std(fit, ddof=0))
    meta["fit_mean"] = _number(fit_mean)
    meta["fit_std"] = _number(fit_std)
    sample_count = int(core.size)
    histogram: list[dict[str, Any]] = []
    for index, count in enumerate(counts):
        left = float(edges[index])
        right = float(edges[index + 1])
        if use_log:
            low, high = math.log(left), math.log(right)
        else:
            low, high = left, right
        expected = sample_count * max(0.0, _normal_cdf(high, fit_mean, fit_std) - _normal_cdf(low, fit_mean, fit_std))
        histogram.append({"left": left, "right": right, "count": int(count), "expected": _number(expected)})
    return histogram, meta


def _conditional_keep_columns(frame: pd.DataFrame) -> list[str]:
    keep = ["trade_date", "ts_code", "_factor", "_target"]
    keep.extend(column for column in CONDITIONAL_FIELDS if column in frame.columns)
    return keep


def _empty_conditional_dimension(field: str, labels: dict[int, str], *, status: str) -> dict[str, Any]:
    if status == "missing_field":
        return {"field": field, "status": status, "buckets": []}
    return {
        "field": field,
        "status": status,
        "buckets": [
            {
                "bucket": index,
                "label": labels[index],
                "ic_mean": None,
                "ic_positive_ratio": None,
                "effective_days": 0,
                "daily_ic": [],
            }
            for index in range(1, 6)
        ],
    }


def _assemble_conditional_dimension(
    field: str,
    labels: dict[int, str],
    daily_by_bucket: dict[int, list[dict[str, Any]]],
    *,
    present: bool,
) -> dict[str, Any]:
    if not present:
        return _empty_conditional_dimension(field, labels, status="missing_field")
    buckets = []
    any_points = False
    for index in range(1, 6):
        series = list(daily_by_bucket.get(index) or [])
        values = [item["ic"] for item in series]
        any_points = any_points or bool(values)
        buckets.append(
            {
                "bucket": index,
                "label": labels[index],
                "ic_mean": _number(pd.Series(values).mean()) if values else None,
                "ic_positive_ratio": float(sum(value > 0 for value in values) / len(values)) if values else None,
                "effective_days": len(values),
                "daily_ic": series,
            }
        )
    return {
        "field": field,
        "status": "available" if any_points else "insufficient_data",
        "buckets": buckets,
    }


def _truncate_conditional_ic(payload: dict[str, Any]) -> dict[str, Any]:
    truncated: dict[str, Any] = {}
    for key, dimension in payload.items():
        if not isinstance(dimension, dict):
            truncated[key] = dimension
            continue
        buckets = []
        for bucket in dimension.get("buckets") or []:
            item = dict(bucket)
            series = item.get("daily_ic") or []
            item["daily_ic"] = series[-MAX_SERIES_POINTS:]
            buckets.append(item)
        truncated[key] = {**dimension, "buckets": buckets}
    return truncated


def _append_conditional_daily(
    group: pd.DataFrame,
    date_text: str,
    field: str,
    store: dict[int, list[dict[str, Any]]],
) -> None:
    if field not in group.columns:
        return
    values = pd.to_numeric(group[field], errors="coerce")
    subset = group.loc[values.notna()].copy()
    if len(subset) < 3:
        return
    subset[field] = values.loc[subset.index]
    try:
        bins = pd.qcut(subset[field], q=5, labels=False, duplicates="drop")
    except ValueError:
        return
    subset = subset.assign(_cond_bucket=bins).dropna(subset=["_cond_bucket"])
    for bucket_id, members in subset.groupby("_cond_bucket"):
        if len(members) < 3:
            continue
        ic = members["_factor"].rank(method="average").corr(members["_target"].rank(method="average"))
        if pd.isna(ic):
            continue
        store[int(bucket_id) + 1].append({"date": date_text, "ic": float(ic), "count": int(len(members))})


def cross_section_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.loc[:, _conditional_keep_columns(frame)]
    daily_ic: list[dict[str, Any]] = []
    turnover_series: list[dict[str, Any]] = []
    top_bottom_series: list[float] = []
    daily_top_bottom: list[dict[str, Any]] = []
    monotonic_series: list[float] = []
    previous: set[str] | None = None
    group_days = 0
    conditional_daily = {
        field: {index: [] for index in range(1, 6)} for field, _key, _labels in CONDITIONAL_DIMENSIONS
    }
    for date_value, group in work.groupby("trade_date", sort=True):
        if len(group) < 3:
            continue
        date_text = str(date_value)
        ic = group["_factor"].rank(method="average").corr(group["_target"].rank(method="average"))
        if pd.notna(ic):
            daily_ic.append({"date": date_text, "ic": float(ic), "count": int(len(group))})
        for field, _key, _labels in CONDITIONAL_DIMENSIONS:
            _append_conditional_daily(group, date_text, field, conditional_daily[field])
        try:
            bins = pd.qcut(group["_factor"], q=5, labels=False, duplicates="drop")
        except ValueError:
            continue
        grouped = group.assign(_group=bins).dropna(subset=["_group"])
        if len(grouped) < 3:
            continue
        means = grouped.groupby("_group", observed=True)["_target"].mean()
        if means.empty:
            continue
        group_days += 1
        ordered = means.sort_index()
        if len(ordered) >= 2:
            spread = float(ordered.iloc[-1] - ordered.iloc[0])
            top_bottom_series.append(spread)
            daily_top_bottom.append({"date": date_text, "spread": spread})
        if len(ordered) >= 3:
            rank_monotonic = pd.Series(range(1, len(ordered) + 1)).corr(pd.Series(ordered.to_numpy()))
            if pd.notna(rank_monotonic):
                monotonic_series.append(float(rank_monotonic))
        top_index = int(means.index.max())
        members = set(grouped.loc[grouped["_group"] == top_index, "ts_code"].astype(str))
        if previous is not None:
            turnover = 1 - len(previous & members) / max(len(previous), 1)
            turnover_series.append(
                {
                    "date": date_text,
                    "turnover": float(turnover),
                    "previous_count": len(previous),
                    "current_count": len(members),
                }
            )
        previous = members
    ic_values = [item["ic"] for item in daily_ic]
    turnover_values = [item["turnover"] for item in turnover_series]
    ic_mean = _number(pd.Series(ic_values).mean()) if ic_values else None
    ic_std = _number(pd.Series(ic_values).std(ddof=0)) if len(ic_values) > 1 else None
    return {
        "daily_ic": daily_ic,
        "daily_top_bottom": daily_top_bottom,
        "turnover_series": turnover_series,
        "ic_mean": ic_mean,
        "ic_positive_ratio": float(sum(value > 0 for value in ic_values) / len(ic_values)) if ic_values else None,
        "ic_std": ic_std,
        "icir": _number(ic_mean / ic_std) if ic_mean is not None and ic_std else None,
        "turnover_mean": _number(pd.Series(turnover_values).mean()) if turnover_values else None,
        "top_bottom_spread": _number(pd.Series(top_bottom_series).mean()) if top_bottom_series else None,
        "monotonicity": _number(pd.Series(monotonic_series).mean()) if monotonic_series else None,
        "effective_days": len(daily_ic),
        "group_days": group_days,
        "conditional_ic": {
            key: _assemble_conditional_dimension(
                field,
                labels,
                conditional_daily[field],
                present=field in work.columns,
            )
            for field, key, labels in CONDITIONAL_DIMENSIONS
        },
    }


class FactorCalculationService:
    """Run daily cross-sectional factor diagnostics and persist each run."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    @staticmethod
    def _entity_candidates(factor_id: str) -> list[str]:
        text = str(factor_id or "").strip()
        if not text:
            return []
        names = [text]
        if text.startswith("factor_"):
            names.append(text.removeprefix("factor_"))
        else:
            names.append(f"factor_{text}")
        return list(dict.fromkeys(names))

    @staticmethod
    def _logical_id(factor_id: str) -> str:
        text = str(factor_id or "").strip()
        return text.removeprefix("factor_") if text.startswith("factor_") else text

    @staticmethod
    def _markets(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item).strip().upper() for item in value if str(item).strip()]

    @staticmethod
    def _shift_calendar(value: str, days: int) -> str:
        return (datetime.strptime(value, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")

    def _binding(self, factor_id: str, version_id: str = "v1") -> dict[str, Any]:
        with self.database.connect() as connection:
            for entity_id in self._entity_candidates(factor_id):
                row = connection.execute(
                    "SELECT fv.entity_id, fv.version_id, fv.dataset_id, fv.dataset_version_id, "
                    "fv.status AS version_status, fv.quality_status, fv.formula, fv.input_fields_json, "
                    "dv.path, dv.row_count, dv.date_min, dv.date_max, dv.quality_status AS dataset_quality "
                    "FROM factor_versions fv JOIN dataset_versions dv "
                    "ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
                    "WHERE fv.entity_id=? AND fv.version_id=? AND fv.status <> 'deprecated'",
                    (entity_id, version_id),
                ).fetchone()
                if row is not None:
                    return dict(row)
        raise ValueError(f"找不到这个因子：{factor_id}:{version_id}")

    def _normalize_date(self, value: str | None, fallback: str) -> str:
        if not value:
            return fallback
        text = str(value).strip().replace("-", "")
        if len(text) != 8 or not text.isdigit():
            raise ValueError(f"invalid date: {value}")
        return text

    def _canonical_path(self) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT path FROM dataset_versions WHERE entity_id='ds_canonical_market' AND version_id='current'",
            ).fetchone()
        if row is None:
            return None
        return self.settings.require_read_path(Path(row["path"]))

    def _read_market_frame(
        self,
        path: Path,
        *,
        extra_columns: list[str],
        calc_from: str,
        calc_to: str,
        warmup_window: int,
        markets: list[str],
    ) -> pd.DataFrame | None:
        names = set(pq.ParquetFile(path).schema_arrow.names)
        date_field = next((item for item in ("trade_date", "date") if item in names), None)
        instrument_field = next((item for item in ("ts_code", "instrument") if item in names), None)
        if date_field is None or instrument_field is None:
            return None
        wanted = {
            date_field,
            instrument_field,
            "hfq_open",
            "hfq_close",
            "future_return",
            "eligible",
            "list_date",
            "delist_date",
            *extra_columns,
        }
        columns = [name for name in wanted if name in names]
        warmup = self._shift_calendar(calc_from, -(max(warmup_window, 0) * 2 + 10))
        tail = self._shift_calendar(calc_to, 10)
        expression = (pc.field(date_field) >= warmup) & (pc.field(date_field) <= tail)
        if markets:
            parts = [pc.ends_with(pc.field(instrument_field), f".{item}") for item in markets]
            market_expr = parts[0]
            for part in parts[1:]:
                market_expr = market_expr | part
            expression = expression & market_expr
        table = ds.dataset(path, format="parquet").to_table(columns=columns, filter=expression)
        if table.num_rows == 0:
            return None
        frame = table.to_pandas()
        frame["trade_date"] = frame[date_field].astype(str).str.replace("-", "", regex=False)
        frame["ts_code"] = frame[instrument_field].astype(str)
        if "instrument" not in frame.columns:
            frame["instrument"] = frame["ts_code"]
        if "date" not in frame.columns:
            frame["date"] = frame["trade_date"]
        if markets:
            suffixes = tuple(f".{item}" for item in markets)
            frame = frame.loc[frame["ts_code"].str.endswith(suffixes)]
        if frame.empty:
            return None
        frame = filter_listed_universe(frame)
        frame = apply_pit_index_universe(frame, self.settings.raw_root)
        if frame.empty:
            return None
        return frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def _attach_label(self, frame: pd.DataFrame) -> pd.DataFrame:
        if "hfq_open" in frame.columns and "hfq_close" in frame.columns:
            group = frame.groupby("ts_code", sort=False)
            frame["_target"] = pd.to_numeric(group["hfq_close"].shift(-2) / group["hfq_open"].shift(-1) - 1, errors="coerce")
        elif "future_return" in frame.columns:
            frame["_target"] = pd.to_numeric(frame["future_return"], errors="coerce")
        else:
            raise ValueError("数据集缺少因子值或未来收益字段")
        return frame

    def _crop(self, frame: pd.DataFrame, calc_from: str, calc_to: str) -> pd.DataFrame | None:
        cropped = frame[(frame["trade_date"] >= calc_from) & (frame["trade_date"] <= calc_to)].copy()
        if cropped.empty:
            return None
        cropped["_factor"] = pd.to_numeric(cropped["factor_value"], errors="coerce")
        cropped["_target"] = pd.to_numeric(cropped["_target"], errors="coerce")
        return cropped[_conditional_keep_columns(cropped)]

    def _builtin_factor_value(self, frame: pd.DataFrame, factor_id: str) -> pd.Series:
        field_map = {
            "pe_ttm": "pe_ttm",
            "total_market_cap": "total_market_cap",
            "float_market_cap": "float_market_cap",
            "dividend_yield_ratio": "dividend_yield_ratio",
            "turn": "turn",
        }
        group = frame.groupby("ts_code", sort=False)
        if factor_id == "momentum_5":
            if "hfq_close" in frame.columns:
                return frame["hfq_close"] / group["hfq_close"].shift(5) - 1
            if "momentum_5" in frame.columns:
                return frame["momentum_5"]
        if factor_id == "volatility_5":
            if "hfq_close" in frame.columns:
                ret = frame["hfq_close"] / group["hfq_close"].shift(1) - 1
                return grouped_rolling(ret, frame["ts_code"], 5, "std")
            if "volatility_5" in frame.columns:
                return frame["volatility_5"]
        column = field_map.get(factor_id)
        if column and column in frame.columns:
            return frame[column]
        if factor_id in frame.columns:
            return frame[factor_id]
        raise ValueError("数据集缺少因子值或未来收益字段")

    def _builtin_frame(self, factor_id: str, binding: dict[str, Any]) -> pd.DataFrame | None:
        path = self._canonical_path()
        if path is None:
            path = self.settings.require_read_path(Path(binding["path"]))
        warmup = 5 if factor_id in {"momentum_5", "volatility_5"} else 0
        extra = [factor_id, *CONDITIONAL_FIELDS]
        field_map = {
            "pe_ttm": "pe_ttm",
            "total_market_cap": "total_market_cap",
            "float_market_cap": "float_market_cap",
            "dividend_yield_ratio": "dividend_yield_ratio",
            "turn": "turn",
        }
        if factor_id in field_map:
            extra.append(field_map[factor_id])
        frame = self._read_market_frame(
            path,
            extra_columns=extra,
            calc_from=binding["calc_from"],
            calc_to=binding["calc_to"],
            warmup_window=warmup,
            markets=binding.get("markets") or [],
        )
        if frame is None:
            return None
        frame["factor_value"] = self._builtin_factor_value(frame, factor_id)
        frame = self._attach_label(frame)
        return self._crop(frame, binding["calc_from"], binding["calc_to"])

    def _formula_frame(self, factor_id: str, binding: dict[str, Any]) -> pd.DataFrame | None:
        path = self.settings.require_read_path(Path(binding["path"]))
        names = set(pq.ParquetFile(path).schema_arrow.names)
        formula = str(binding.get("formula") or "").strip()
        stored_fields = json.loads(binding.get("input_fields_json") or "[]")
        extra = [item for item in stored_fields if isinstance(item, str)]
        extra.extend(name for name in (factor_id, self._logical_id(factor_id)) if name in names)
        extra.extend(CONDITIONAL_FIELDS)
        warmup = 0
        parsed = None
        if formula:
            available = set(names) - TARGET_ONLY_FIELDS
            try:
                parsed = parse_expression(formula, available)
            except ExpressionError as error:
                raise ValueError(f"公式算不出来：{error}") from error
            extra.extend(parsed.input_fields)
            warmup = parsed.max_window
        frame = self._read_market_frame(
            path,
            extra_columns=extra,
            calc_from=binding["calc_from"],
            calc_to=binding["calc_to"],
            warmup_window=warmup,
            markets=binding.get("markets") or [],
        )
        if frame is None:
            return None
        if parsed is not None:
            try:
                frame["factor_value"] = finite_factor_values(
                    pd.Series(_eval(parsed.tree, frame), index=frame.index)
                )
            except (ExpressionError, KeyError, TypeError, ValueError) as error:
                raise ValueError(f"公式算不出来：{error}") from error
        else:
            column = next((item for item in (factor_id, self._logical_id(factor_id), "factor_value") if item in frame.columns), None)
            if column is None:
                raise ValueError("数据集缺少因子值或未来收益字段")
            frame["factor_value"] = finite_factor_values(frame[column])
        frame = self._attach_label(frame)
        return self._crop(frame, binding["calc_from"], binding["calc_to"])

    def _factor_frame(self, factor_id: str, binding: dict[str, Any]) -> pd.DataFrame | None:
        logical = self._logical_id(str(binding.get("entity_id") or factor_id))
        if logical in FEATURE_FIELDS:
            return self._builtin_frame(logical, binding)
        return self._formula_frame(logical, binding)

    def _canonical_frame(self, factor_id: str, calc_from: str, calc_to: str) -> pd.DataFrame | None:
        path = self._canonical_path()
        if path is None:
            return None
        binding = {
            "path": str(path),
            "calc_from": calc_from,
            "calc_to": calc_to,
            "markets": [],
            "entity_id": f"factor_{factor_id}",
            "formula": "",
            "input_fields_json": "[]",
        }
        return self._builtin_frame(factor_id, binding)

    def _summary(self, factor_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        frame = self._factor_frame(factor_id, binding)
        if frame is None or frame.empty:
            return {"error": "所选日期范围内没有数据"}
        total_before_clean = len(frame)
        all_factor_values = frame["_factor"].dropna()
        factor_non_null = int(all_factor_values.size)
        if factor_non_null:
            histogram, histogram_range = build_factor_histogram(all_factor_values)
            factor_quantiles = {
                f"p{percentile}": _number(all_factor_values.quantile(percentile / 100))
                for percentile in (1, 5, 25, 50, 75, 95, 99)
            }
            factor_stats = {
                "factor_rows": int(total_before_clean),
                "factor_non_null": factor_non_null,
                "factor_missing": int(total_before_clean - factor_non_null),
                "factor_coverage": float(factor_non_null / total_before_clean) if total_before_clean else None,
                "factor_mean": _number(all_factor_values.mean()),
                "factor_std": _number(all_factor_values.std(ddof=0)),
                "factor_min": _number(all_factor_values.min()),
                "factor_max": _number(all_factor_values.max()),
                "factor_skew": _number(all_factor_values.skew()),
                "factor_kurtosis": _number(all_factor_values.kurtosis()),
                "quantiles": factor_quantiles,
                "histogram": histogram,
                "histogram_range": histogram_range,
            }
        else:
            factor_stats = {"factor_rows": int(total_before_clean), "factor_non_null": 0, "factor_missing": int(total_before_clean), "factor_coverage": 0.0, "factor_mean": None, "factor_std": None, "factor_min": None, "factor_max": None, "factor_skew": None, "factor_kurtosis": None, "quantiles": {f"p{p}": None for p in (1, 5, 25, 50, 75, 95, 99)}}
        frame = frame.dropna(subset=["_factor", "_target", "trade_date", "ts_code"])
        row_count = len(frame)
        coverage = float(row_count / total_before_clean) if total_before_clean else None
        if row_count == 0:
            return {
                "error": "所选日期范围内没有可用的因子值和收益",
                "row_count": 0,
                "coverage": coverage if coverage is not None else 0.0,
            }
        diagnostics = cross_section_diagnostics(frame)
        return {
            "status": "completed",
            "row_count": int(row_count),
            "coverage": _number(coverage),
            "missing_rows": max(0, int(total_before_clean - row_count)),
            "ic_mean": diagnostics["ic_mean"],
            "ic_positive_ratio": diagnostics["ic_positive_ratio"],
            "ic_std": diagnostics["ic_std"],
            "icir": diagnostics["icir"],
            "turnover_mean": diagnostics["turnover_mean"],
            "top_bottom_spread": diagnostics["top_bottom_spread"],
            "daily_top_bottom": diagnostics["daily_top_bottom"][-MAX_SERIES_POINTS:],
            "monotonicity": diagnostics["monotonicity"],
            "effective_days": diagnostics["effective_days"],
            "group_days": diagnostics["group_days"],
            **factor_stats,
            "label": DEFAULT_LABEL,
            "daily_ic": diagnostics["daily_ic"][-MAX_SERIES_POINTS:],
            "conditional_ic": _truncate_conditional_ic(diagnostics["conditional_ic"]),
        }

    def run(
        self,
        factor_id: str,
        *,
        version_id: str = "v1",
        date_from: str | None = None,
        date_to: str | None = None,
        markets: Any = None,
    ) -> dict[str, Any]:
        binding = self._binding(factor_id, version_id)
        calc_from = self._normalize_date(date_from, binding["date_min"] or "20170101")
        calc_to = self._normalize_date(date_to, binding["date_max"] or calc_from)
        if calc_from > calc_to:
            raise ValueError("开始日期不能晚于结束日期")
        binding["calc_from"] = calc_from
        binding["calc_to"] = calc_to
        binding["markets"] = self._markets(markets)
        started = _now()
        params = {
            "factor_id": factor_id,
            "version_id": version_id,
            "dataset_id": binding["dataset_id"],
            "dataset_version_id": binding["dataset_version_id"],
            "date_from": calc_from,
            "date_to": calc_to,
            "markets": binding["markets"],
            "label_definition": DEFAULT_LABEL,
        }
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO factor_calculation_runs("
                "factor_entity_id, factor_version_id, dataset_id, dataset_version_id, "
                "date_from, date_to, label_definition, status, started_at, params_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                (
                    binding["entity_id"],
                    binding["version_id"],
                    binding["dataset_id"],
                    binding["dataset_version_id"],
                    calc_from,
                    calc_to,
                    DEFAULT_LABEL,
                    started,
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                ),
            )
            calculation_id = int(cursor.lastrowid)
        try:
            summary = self._summary(factor_id, binding)
            if summary.get("error"):
                raise ValueError(str(summary["error"]))
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE factor_calculation_runs SET status='completed', finished_at=?, "
                    "summary_json=?, row_count=?, coverage=?, missing_rows=?, ic_mean=?, "
                    "ic_positive_ratio=?, ic_std=?, icir=?, turnover_mean=?, top_bottom_spread=?, "
                    "monotonicity=?, factor_mean=?, factor_std=?, factor_min=?, factor_max=?, "
                    "factor_skew=?, factor_kurtosis=?, quantiles_json=?, effective_days=? "
                    "WHERE calculation_id=?",
                    (
                        _now(),
                        json.dumps(summary, ensure_ascii=False, sort_keys=True),
                        summary.get("row_count"),
                        summary.get("coverage"),
                        summary.get("missing_rows"),
                        summary.get("ic_mean"),
                        summary.get("ic_positive_ratio"),
                        summary.get("ic_std"),
                        summary.get("icir"),
                        summary.get("turnover_mean"),
                        summary.get("top_bottom_spread"),
                        summary.get("monotonicity"),
                        summary.get("factor_mean"),
                        summary.get("factor_std"),
                        summary.get("factor_min"),
                        summary.get("factor_max"),
                        summary.get("factor_skew"),
                        summary.get("factor_kurtosis"),
                        json.dumps(summary.get("quantiles") or {}, ensure_ascii=False, sort_keys=True),
                        summary.get("effective_days"),
                        calculation_id,
                    ),
                )
        except Exception as error:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE factor_calculation_runs SET status='failed', finished_at=?, error_message=? WHERE calculation_id=?",
                    (_now(), str(error), calculation_id),
                )
            raise
        return self.get(calculation_id) or {}

    def _persist_histogram(self, calculation_id: int, summary: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE factor_calculation_runs SET summary_json=? WHERE calculation_id=?",
                (json.dumps(summary, ensure_ascii=False, sort_keys=True), calculation_id),
            )

    def _apply_histogram(self, item: dict[str, Any], histogram: list[dict[str, Any]], histogram_range: dict[str, Any]) -> dict[str, Any]:
        item["histogram"] = histogram
        item["histogram_range"] = histogram_range
        summary = dict(item.get("summary") or {})
        summary["histogram"] = histogram
        summary["histogram_range"] = histogram_range
        item["summary"] = summary
        return item

    def _refresh_histogram(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("status") != "completed":
            return item
        if not histogram_needs_rebuild(item.get("histogram"), item.get("histogram_range")):
            return item
        try:
            binding = self._binding(str(item.get("factor_entity_id") or ""), str(item.get("factor_version_id") or "v1"))
            binding["calc_from"] = str(item["date_from"])
            binding["calc_to"] = str(item["date_to"])
            binding["markets"] = (item.get("params") or {}).get("markets") or []
            frame = self._factor_frame(self._logical_id(str(item.get("factor_entity_id") or "")), binding)
            if frame is None or frame.empty:
                return item
            values = frame["_factor"].dropna()
            if values.empty:
                return item
            histogram, histogram_range = build_factor_histogram(values)
        except (ValueError, KeyError, TypeError):
            return item
        item = self._apply_histogram(item, histogram, histogram_range)
        if item.get("calculation_id") is not None:
            self._persist_histogram(int(item["calculation_id"]), item["summary"])
        return item

    def _refresh_histograms(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cache: dict[tuple[Any, ...], tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        refreshed: list[dict[str, Any]] = []
        for item in items:
            key = (
                item.get("factor_entity_id"),
                item.get("factor_version_id"),
                item.get("date_from"),
                item.get("date_to"),
                item.get("status"),
            )
            needs = histogram_needs_rebuild(item.get("histogram"), item.get("histogram_range"))
            if key in cache:
                histogram, histogram_range = cache[key]
                item = self._apply_histogram(item, histogram, histogram_range)
                if needs and item.get("calculation_id") is not None:
                    self._persist_histogram(int(item["calculation_id"]), item["summary"])
                refreshed.append(item)
                continue
            item = self._refresh_histogram(item)
            cache[key] = (item.get("histogram") or [], item.get("histogram_range") or {})
            refreshed.append(item)
        return refreshed

    def list(self, *, factor_id: str | None = None, version_id: str | None = None) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if factor_id:
            ids = self._entity_candidates(factor_id)
            where.append(f"factor_entity_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if version_id:
            where.append("factor_version_id=?")
            params.append(version_id)
        sql = "SELECT * FROM factor_calculation_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY calculation_id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        items = [self._project(dict(row)) for row in rows]
        if factor_id:
            items = self._refresh_histograms(items)
        return {"items": items, "count": len(items)}

    def latest(self, *, factor_id: str) -> dict[str, Any] | None:
        ids = self._entity_candidates(factor_id)
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM factor_calculation_runs WHERE factor_entity_id IN ({','.join('?' for _ in ids)}) "
                "ORDER BY calculation_id DESC LIMIT 1",
                ids,
            ).fetchone()
        return self._refresh_histogram(self._project(dict(row))) if row else None

    def get(self, calculation_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM factor_calculation_runs WHERE calculation_id=?",
                (calculation_id,),
            ).fetchone()
        return self._refresh_histogram(self._project(dict(row))) if row else None

    @staticmethod
    def _project(row: dict[str, Any]) -> dict[str, Any]:
        quantiles = json.loads(row.pop("quantiles_json") or "{}")
        summary = json.loads(row.pop("summary_json") or "{}")
        params = json.loads(row.pop("params_json") or "{}")
        return {
            "calculation_id": row["calculation_id"],
            "serial_no": f"#{row['calculation_id']}",
            "factor_entity_id": row["factor_entity_id"],
            "factor_version_id": row["factor_version_id"],
            "dataset_id": row["dataset_id"],
            "dataset_version_id": row["dataset_version_id"],
            "date_from": row["date_from"],
            "date_to": row["date_to"],
            "label_definition": row["label_definition"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error_message": row["error_message"],
            "params": params,
            "summary": summary,
            "row_count": row["row_count"],
            "coverage": row["coverage"],
            "missing_rows": row["missing_rows"],
            "ic_mean": row["ic_mean"],
            "ic_positive_ratio": row["ic_positive_ratio"],
            "ic_std": row["ic_std"],
            "icir": row["icir"],
            "turnover_mean": row["turnover_mean"],
            "top_bottom_spread": row["top_bottom_spread"],
            "monotonicity": row["monotonicity"],
            "factor_mean": row["factor_mean"],
            "factor_std": row["factor_std"],
            "factor_min": row["factor_min"],
            "factor_max": row["factor_max"],
            "factor_skew": row["factor_skew"],
            "factor_kurtosis": row["factor_kurtosis"],
            "factor_rows": summary.get("factor_rows"),
            "factor_non_null": summary.get("factor_non_null"),
            "factor_missing": summary.get("factor_missing"),
            "factor_coverage": summary.get("factor_coverage"),
            "quantiles": quantiles if quantiles else summary.get("quantiles") or {},
            "histogram": summary.get("histogram") or [],
            "histogram_range": summary.get("histogram_range") or {},
            "daily_top_bottom": summary.get("daily_top_bottom") or [],
            "conditional_ic": summary.get("conditional_ic") or {},
            "effective_days": row["effective_days"],
        }
