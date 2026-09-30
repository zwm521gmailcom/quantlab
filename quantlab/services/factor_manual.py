"""Safe, local-only manual factor authoring and preview service."""

from __future__ import annotations

import ast
import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
import pyarrow.compute as pc
import pyarrow.dataset as pa_dataset
import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import TARGET_ONLY_FIELDS, FactorRepository
from quantlab.repositories.research_runs import ResearchRunRepository


MAX_PREVIEW_ROWS = 5_000
MAX_PREVIEW_INSTRUMENTS = 40
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_COMPARES = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_METHODS = {
    "shift",
    "diff",
    "pct_change",
    "rolling_mean",
    "rolling_std",
    "rolling_max",
    "rolling_min",
    "rolling_sum",
    "ts_rank",
    "ts_zscore",
    "rolling_bias",
    "rolling_corr",
    "rolling_cov",
    "ewm_mean",
    "cs_rank",
}
WINDOWLESS_METHODS = frozenset({"cs_rank"})
PAIR_METHODS = frozenset({"rolling_corr", "rolling_cov"})


class ExpressionError(ValueError):
    """A formula is outside the deliberately small expression language."""


@dataclass(frozen=True)
class ParsedExpression:
    tree: ast.Expression
    input_fields: list[str]
    max_window: int
    window_mode: str = "per_instrument_observation"


def _ungroup(result: pd.Series) -> pd.Series:
    if isinstance(result.index, pd.MultiIndex):
        return result.reset_index(level=0, drop=True).sort_index()
    return result


def finite_factor_values(values: Any) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series(values, dtype="float64")
    series = pd.to_numeric(series, errors="coerce")
    return series.mask(~series.replace([float("inf"), float("-inf")], pd.NA).notna())


def grouped_rolling(source: pd.Series, keys: Any, window: int, how: str) -> pd.Series:
    values = pd.to_numeric(source, errors="coerce").to_numpy(dtype=float, copy=False)
    out = np.full(len(values), np.nan)
    if window < 1 or len(values) == 0:
        return pd.Series(out, index=source.index)
    grouped = pd.Series(np.arange(len(values)), index=source.index).groupby(keys, sort=False)
    for positions in grouped.indices.values():
        pos = np.asarray(positions, dtype=np.intp)
        size = int(pos.size)
        if size < window:
            continue
        contiguous = pos[-1] == pos[0] + size - 1
        arr = values[pos[0] : pos[-1] + 1] if contiguous else values[pos]
        view = sliding_window_view(arr, window)
        if how == "mean":
            reduced = np.mean(view, axis=-1)
        elif how == "std":
            reduced = np.std(view, axis=-1, ddof=1)
        elif how == "max":
            reduced = np.max(view, axis=-1)
        elif how == "min":
            reduced = np.min(view, axis=-1)
        elif how == "sum":
            reduced = np.sum(view, axis=-1)
        elif how == "rank":
            last = view[:, -1]
            less = np.sum(view < last[:, None], axis=-1)
            equal = np.sum(view == last[:, None], axis=-1)
            reduced = (less + (equal + 1) / 2.0) / window
        else:
            raise ValueError(f"unsupported rolling reduction: {how}")
        dest = pos[window - 1 :]
        out[dest] = reduced
    return pd.Series(out, index=source.index)


def grouped_rolling_pair(
    left: pd.Series,
    right: pd.Series,
    keys: Any,
    window: int,
    how: str,
) -> pd.Series:
    xs = pd.to_numeric(left, errors="coerce").to_numpy(dtype=float, copy=False)
    ys = pd.to_numeric(right, errors="coerce").to_numpy(dtype=float, copy=False)
    out = np.full(len(xs), np.nan)
    if window < 2 or len(xs) == 0 or len(xs) != len(ys):
        return pd.Series(out, index=left.index)
    grouped = pd.Series(np.arange(len(xs)), index=left.index).groupby(keys, sort=False)
    denom = window - 1
    for positions in grouped.indices.values():
        pos = np.asarray(positions, dtype=np.intp)
        size = int(pos.size)
        if size < window:
            continue
        contiguous = pos[-1] == pos[0] + size - 1
        x_arr = xs[pos[0] : pos[-1] + 1] if contiguous else xs[pos]
        y_arr = ys[pos[0] : pos[-1] + 1] if contiguous else ys[pos]
        x_view = sliding_window_view(x_arr, window)
        y_view = sliding_window_view(y_arr, window)
        x_centered = x_view - np.mean(x_view, axis=-1, keepdims=True)
        y_centered = y_view - np.mean(y_view, axis=-1, keepdims=True)
        cov = np.sum(x_centered * y_centered, axis=-1) / denom
        if how == "cov":
            reduced = cov
        elif how == "corr":
            sx = np.sqrt(np.sum(x_centered * x_centered, axis=-1) / denom)
            sy = np.sqrt(np.sum(y_centered * y_centered, axis=-1) / denom)
            with np.errstate(invalid="ignore", divide="ignore"):
                reduced = cov / (sx * sy)
            reduced = np.where((sx == 0) | (sy == 0), np.nan, reduced)
        else:
            raise ValueError(f"unsupported rolling pair reduction: {how}")
        out[pos[window - 1 :]] = reduced
    return pd.Series(out, index=left.index)


def _window_constant(node: ast.AST, *, minimum: int) -> int:
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
    ):
        raise ExpressionError("window must be non-negative")
    if (
        not isinstance(node, ast.Constant)
        or not isinstance(node.value, int)
        or isinstance(node.value, bool)
    ):
        raise ExpressionError("window must be an integer constant")
    if node.value < minimum:
        if minimum > 0 and node.value >= 0:
            raise ExpressionError(f"window must be at least {minimum}")
        raise ExpressionError("window must be non-negative")
    return int(node.value)


def parse_expression(formula: str, available_fields: set[str]) -> ParsedExpression:
    if not isinstance(formula, str) or not formula.strip():
        raise ExpressionError("formula is required")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(
            f"formula syntax is invalid at position {error.offset or 0}"
        ) from error
    fields: set[str] = set()
    max_window = 0

    def visit(node: ast.AST) -> None:
        nonlocal max_window
        if isinstance(node, ast.Expression):
            visit(node.body)
        elif isinstance(node, ast.Name):
            if node.id in {"True", "False"}:
                return
            if node.id in TARGET_ONLY_FIELDS:
                raise ExpressionError(f"{node.id} is target-only")
            if node.id not in available_fields:
                raise ExpressionError(f"unknown field: {node.id}")
            fields.add(node.id)
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ExpressionError("only numeric constants are allowed")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub, ast.Not)
        ):
            visit(node.operand)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for value in node.values:
                visit(value)
        elif isinstance(node, ast.Compare):
            visit(node.left)
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                if not isinstance(operator, _COMPARES):
                    raise ExpressionError("comparison operator is not allowed")
                visit(comparator)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name not in _METHODS:
                raise ExpressionError("function is not allowed")
            if node.keywords:
                raise ExpressionError(f"{name} accepts positional arguments only")
            if name in PAIR_METHODS:
                if len(node.args) != 2:
                    raise ExpressionError(f"{name} accepts a series and an integer window")
                window = _window_constant(node.args[1], minimum=2)
                max_window = max(max_window, window)
                visit(node.func.value)
                visit(node.args[0])
            else:
                if len(node.args) != 1:
                    raise ExpressionError(f"{name} accepts one positional argument")
                window = _window_constant(node.args[0], minimum=0)
                max_window = max(max_window, window)
                visit(node.func.value)
        else:
            raise ExpressionError(f"syntax node {type(node).__name__} is not allowed")

    visit(tree)
    if not fields:
        raise ExpressionError("formula must reference at least one field")
    return ParsedExpression(tree, sorted(fields), max_window)


def _eval(tree: ast.AST, frame: pd.DataFrame) -> pd.Series | float | bool:
    if isinstance(tree, ast.Expression):
        return _eval(tree.body, frame)
    if isinstance(tree, ast.Name):
        return frame[tree.id]
    if isinstance(tree, ast.Constant):
        return float(tree.value)
    if isinstance(tree, ast.UnaryOp):
        value = _eval(tree.operand, frame)
        if isinstance(tree.op, ast.USub):
            return -value
        if isinstance(tree.op, ast.Not):
            return ~value
        return value
    if isinstance(tree, ast.BinOp):
        left, right = _eval(tree.left, frame), _eval(tree.right, frame)
        if isinstance(tree.op, ast.Add):
            return left + right
        if isinstance(tree.op, ast.Sub):
            return left - right
        if isinstance(tree.op, ast.Mult):
            return left * right
        return left / right
    if isinstance(tree, ast.BoolOp):
        result = _eval(tree.values[0], frame)
        for item in tree.values[1:]:
            value = _eval(item, frame)
            result = result & value if isinstance(tree.op, ast.And) else result | value
        return result
    if isinstance(tree, ast.Compare):
        result = pd.Series(True, index=frame.index)
        left = _eval(tree.left, frame)
        for operator, comparator in zip(tree.ops, tree.comparators, strict=True):
            right = _eval(comparator, frame)
            if isinstance(operator, ast.Eq):
                current = left == right
            elif isinstance(operator, ast.NotEq):
                current = left != right
            elif isinstance(operator, ast.Lt):
                current = left < right
            elif isinstance(operator, ast.LtE):
                current = left <= right
            elif isinstance(operator, ast.Gt):
                current = left > right
            else:
                current = left >= right
            result = result & current
            left = right
        return result
    if isinstance(tree, ast.Call):
        source = _eval(tree.func.value, frame)
        name = tree.func.attr
        if name in PAIR_METHODS:
            other = _eval(tree.args[0], frame)
            if not isinstance(other, pd.Series):
                other = pd.Series(float(other), index=frame.index, dtype="float64")
            window = int(tree.args[1].value)
            how = "corr" if name == "rolling_corr" else "cov"
            return grouped_rolling_pair(source, other, frame["instrument"], window, how)
        window = int(tree.args[0].value)
        if name == "cs_rank":
            date = frame["date"] if "date" in frame.columns else frame.get("trade_date")
            if date is None:
                raise ExpressionError("cs_rank requires date")
            return source.groupby(date, sort=False).rank(pct=True)
        grouped = source.groupby([frame["instrument"]], sort=False, group_keys=False)
        if name == "shift":
            return grouped.shift(window)
        if name == "diff":
            return grouped.diff(window)
        if name == "pct_change":
            return grouped.pct_change(window)
        if name == "ewm_mean":
            return _ungroup(
                grouped.ewm(span=window, min_periods=window, adjust=False).mean()
            )
        keys = frame["instrument"]
        if name == "rolling_mean":
            return grouped_rolling(source, keys, window, "mean")
        if name == "rolling_std":
            return grouped_rolling(source, keys, window, "std")
        if name == "rolling_max":
            return grouped_rolling(source, keys, window, "max")
        if name == "rolling_min":
            return grouped_rolling(source, keys, window, "min")
        if name == "rolling_sum":
            return grouped_rolling(source, keys, window, "sum")
        if name == "ts_rank":
            return grouped_rolling(source, keys, window, "rank")
        if name == "ts_zscore":
            mean = grouped_rolling(source, keys, window, "mean")
            std = grouped_rolling(source, keys, window, "std").replace(0, float("nan"))
            return (source - mean) / std
        if name == "rolling_bias":
            mean = grouped_rolling(source, keys, window, "mean").replace(0, float("nan"))
            return source / mean - 1
        raise ExpressionError("function is not allowed")
    raise ExpressionError("expression cannot be evaluated")


class ManualFactorService:
    def __init__(
        self, settings: Settings, database: Database, factors: FactorRepository
    ) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.research_runs = ResearchRunRepository(settings, database)

    def _dataset(
        self, dataset_id: str, version_id: str
    ) -> tuple[sqlite3.Row, Path, list[str]]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT dv.path, dv.fields_json, dv.status, d.status AS dataset_status, "
                "dv.quality_status, dv.date_min, dv.date_max "
                "FROM dataset_versions dv JOIN datasets d ON d.entity_id=dv.entity_id "
                "WHERE dv.entity_id=? AND dv.version_id=?",
                (dataset_id, version_id),
            ).fetchone()
        if (
            row is None
            or row["dataset_status"] != "published"
            or row["status"] != "published"
        ):
            raise ValueError("published dataset version is required")
        path = self.settings.require_read_path(row["path"])
        if not path.is_file() or path.suffix != ".parquet":
            raise ValueError("dataset version must point to a registered parquet file")
        fields = json.loads(row["fields_json"] or "[]")
        return row, path, list(fields)

    @staticmethod
    def _calendar_year_window(date_max: str | None) -> tuple[str | None, str | None]:
        text = str(date_max or "").replace("-", "")
        if len(text) != 8 or not text.isdigit():
            return None, None
        month = int(text[4:6])
        day = int(text[6:8])
        if month < 1 or month > 12 or day < 1 or day > 31:
            return None, None
        return f"{text[:4]}0101", text

    @staticmethod
    def _read(
        path: Path,
        fields: list[str],
        date_from: str | None,
        date_to: str | None,
        markets: list[str] | None = None,
    ) -> pd.DataFrame:
        available = set(pq.ParquetFile(path).schema_arrow.names)
        date_field = next(
            (item for item in ("trade_date", "date") if item in available), None
        )
        instrument_field = next(
            (item for item in ("ts_code", "instrument") if item in available), None
        )
        columns = [field for field in fields if field in available]
        if date_field and date_field not in columns:
            columns.append(date_field)
        if markets and instrument_field and instrument_field not in columns:
            columns.append(instrument_field)
        expression = None
        if date_field and (date_from or date_to):
            if date_from:
                expression = pc.field(date_field) >= str(date_from).replace("-", "")
            if date_to:
                upper = pc.field(date_field) <= str(date_to).replace("-", "")
                expression = upper if expression is None else expression & upper
        if markets and instrument_field:
            suffixes = [f".{m.upper()}" for m in markets]
            parts = [pc.match_substring(pc.field(instrument_field), s) for s in suffixes]
            market_expr = parts[0]
            for part in parts[1:]:
                market_expr = market_expr | part
            expression = market_expr if expression is None else expression & market_expr
        frame = (
            pa_dataset.dataset(path, format="parquet")
            .to_table(columns=columns, filter=expression)
            .to_pandas()
        )
        if date_field and date_field in frame and (date_from or date_to):
            values = frame[date_field].astype(str).str.replace("-", "", regex=False)
            if date_from:
                frame = frame.loc[values >= str(date_from).replace("-", "")]
            if date_to:
                frame = frame.loc[values <= str(date_to).replace("-", "")]
        if "instrument" not in frame and "ts_code" in frame:
            frame = frame.rename(columns={"ts_code": "instrument"})
        if "instrument" not in frame:
            raise ValueError("dataset requires instrument for per-instrument windows")
        if markets:
            suffixes = tuple(f".{m.upper()}" for m in markets)
            frame = frame.loc[frame["instrument"].str.endswith(suffixes)]
        unique = sorted(str(item) for item in frame["instrument"].dropna().unique())
        if len(unique) > MAX_PREVIEW_INSTRUMENTS:
            frame = frame.loc[frame["instrument"].isin(unique[:MAX_PREVIEW_INSTRUMENTS])]
        sort_date = next(
            (item for item in ("date", "trade_date") if item in frame), None
        )
        if sort_date:
            frame = frame.sort_values(["instrument", sort_date], kind="mergesort")
        return frame.reset_index(drop=True)

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("preview request must be an object")
        dataset_id, version_id = (
            str(request.get("dataset_id", "")),
            str(request.get("dataset_version_id", "")),
        )
        _, path, metadata_fields = self._dataset(dataset_id, version_id)
        requested_fields = request.get("input_fields")
        if isinstance(requested_fields, list):
            leaked = sorted(
                set(str(field) for field in requested_fields) & TARGET_ONLY_FIELDS
            )
            if leaked:
                raise ValueError(
                    f"{', '.join(leaked)} is target-only and cannot be a factor input"
                )
        parsed = parse_expression(str(request.get("formula", "")), set(metadata_fields))
        frame = self._read(
            path,
            list(
                dict.fromkeys(
                    [
                        *parsed.input_fields,
                        "date",
                        "trade_date",
                        "instrument",
                        "ts_code",
                    ]
                )
            ),
            request.get("date_from"),
            request.get("date_to"),
            markets=request.get("markets") or None,
        )
        value = finite_factor_values(_eval(parsed.tree, frame))
        result = pd.DataFrame(
            {
                "date": frame.get("date", frame.get("trade_date")),
                "instrument": frame["instrument"],
                "value": value,
            }
        )
        result = result.head(
            min(int(request.get("max_rows", MAX_PREVIEW_ROWS)), MAX_PREVIEW_ROWS)
        )
        missing = int(result["value"].isna().sum())
        sample = result.where(pd.notna(result), None).to_dict("records")
        return {
            "status": "valid",
            "input_fields": parsed.input_fields,
            "max_window": parsed.max_window,
            "window_mode": parsed.window_mode,
            "rows": len(result),
            "missing_rows": missing,
            "coverage": (len(result) - missing) / len(result) if len(result) else None,
            "sample": sample,
        }

    def fields(self, dataset_id: str, version_id: str) -> dict[str, Any]:
        _, path, metadata_fields = self._dataset(dataset_id, version_id)
        actual = pq.ParquetFile(path).schema_arrow.names
        return {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "fields": [field for field in metadata_fields if field in actual],
        }

    def create_draft(self, request: dict[str, Any]) -> dict[str, Any]:
        preview = self.preview(request)
        if str(request.get("missing_policy", "drop")) not in {"drop", "keep_missing"}:
            raise ValueError("missing_policy is invalid")
        entity_id = f"factor_manual_{secrets.token_hex(6)}"
        definition = {
            "entity_id": entity_id,
            "name": str(request.get("name", "")).strip(),
            "category": str(request.get("category", "技术")),
            "asset_class": request.get("asset_class") or "cn_a",
            "version_id": "v1",
            "dataset_id": str(request["dataset_id"]),
            "dataset_version_id": str(request["dataset_version_id"]),
            "formula": str(request["formula"]),
            "input_fields": preview["input_fields"],
            "source": "manual_expression",
            "direction": str(request.get("direction", "positive")),
            "frequency": "daily",
            "missing_policy": str(request.get("missing_policy", "drop")),
            "pit_policy": str(request.get("pit_policy", "as-of trade_date")),
            "pit_lineage": {
                "rule": str(request.get("pit_policy", "as-of trade_date")),
                "snapshot": f"dataset:{request['dataset_version_id']}",
                "window_mode": preview["window_mode"],
            },
            "upstream_factor_versions": [],
            "quality_status": "needs_review",
            "origin": "manual",
            "author": str(request.get("author", "未登记")),
            "code_hash": "sha256:"
            + hashlib.sha256(str(request["formula"]).encode()).hexdigest(),
        }
        return self.factors.import_definition(definition)

    def diagnose(
        self,
        entity_id: str,
        version_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        record = self.factors.get(entity_id, version_id)
        if record is None:
            raise ValueError("FactorVersion not found")
        if record["status"] != "draft":
            raise ValueError("only draft FactorVersion can be diagnosed")
        with self.database.connect() as connection:
            bounds = connection.execute(
                "SELECT date_min, date_max FROM dataset_versions WHERE entity_id=? AND version_id=?",
                (record["dataset_id"], record["dataset_version_id"]),
            ).fetchone()
        year_from, year_to = self._calendar_year_window(
            None if bounds is None else bounds["date_max"]
        )
        preview_from = date_from or year_from
        preview_to = date_to or year_to
        preview = self.preview(
            {
                "dataset_id": record["dataset_id"],
                "dataset_version_id": record["dataset_version_id"],
                "formula": record["formula"],
                "input_fields": record["input_fields"],
                "date_from": preview_from,
                "date_to": preview_to,
            }
        )
        quality = (
            "passed"
            if preview["coverage"] is not None and preview["coverage"] >= 0.95
            else "warning"
        )
        run = self.research_runs.create(
            name=f"手动因子诊断：{record['name']}",
            research_type="manual",
            config={
                "dataset_id": record["dataset_id"],
                "dataset_version_id": record["dataset_version_id"],
                "factor_versions": [{"factor_id": entity_id, "version_id": version_id}],
                "sample": {
                    "date_from": preview_from or "20170101",
                    "date_to": preview_to or "20991231",
                    "universe": "登记 DatasetVersion",
                },
                "filters": {},
                "filter_snapshot": {
                    "captured_at": "manual-preview",
                    "st_status": "dataset",
                    "suspended": "dataset",
                },
                "label": {
                    "definition": "t+1 open -> t+2 close",
                    "price_fields": ["hfq_open", "hfq_close"],
                },
                "pit_snapshot": {
                    "rule": record["pit_policy"],
                    "captured_at": "manual-preview",
                },
            },
            allow_draft_factor_versions=True,
        )
        run = self.research_runs.transition(run["run_id"], "running")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE factor_versions SET quality_status=?, quality_json=? WHERE entity_id=? AND version_id=?",
                (
                    quality,
                    json.dumps(preview, ensure_ascii=False),
                    entity_id,
                    version_id,
                ),
            )
        run = self.research_runs.complete(
            run["run_id"], {"coverage": preview["coverage"], "quality_status": quality}
        )
        return {
            **self.factors.get(entity_id, version_id),
            "quality_status": quality,
            "preview": preview,
            "research_run": run,
        }

    def publish(self, entity_id: str, version_id: str) -> dict[str, Any]:
        record = self.factors.get(entity_id, version_id)
        if record is None:
            raise ValueError("FactorVersion not found")
        if record["quality_status"] != "passed":
            raise ValueError("quality gate requires quality_status=passed")
        return self.factors.publish(entity_id, version_id)

    def update_draft(
        self, entity_id: str, version_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        record = self.factors.get(entity_id, version_id)
        if record is None:
            raise ValueError("FactorVersion not found")
        if record["status"] != "draft":
            raise ValueError("only draft FactorVersion can be edited")
        if not isinstance(patch, dict):
            raise ValueError("draft patch must be an object")
        merged = {**record, **patch}
        preview = self.preview(
            {
                "dataset_id": merged["dataset_id"],
                "dataset_version_id": merged["dataset_version_id"],
                "formula": merged["formula"],
                "input_fields": merged.get("input_fields", []),
            }
        )
        allowed = {
            "name",
            "category",
            "asset_class",
            "formula",
            "input_fields",
            "direction",
            "missing_policy",
            "pit_policy",
            "pit_lineage",
            "author",
        }
        definition_patch = {
            key: merged[key] for key in allowed if key in patch or key == "formula"
        }
        definition_patch["input_fields"] = preview["input_fields"]
        definition_patch["quality_status"] = "needs_review"
        return self.factors.update_draft(entity_id, version_id, definition_patch)
