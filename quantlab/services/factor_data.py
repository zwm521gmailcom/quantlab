"""Bounded, versioned queries over the immutable seven-factor matrix."""

from __future__ import annotations

import csv
import io
import json
import math
import numbers
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.database import Database


FEATURE_FIELDS = (
    "pe_ttm",
    "total_market_cap",
    "float_market_cap",
    "dividend_yield_ratio",
    "momentum_5",
    "volatility_5",
    "turn",
)
TARGET_ONLY_FIELDS = frozenset(
    {"label", "future_return", "future_return_1pct", "future_return_99pct", "clipped_return", "binned_return"}
)
MAX_PAGE_SIZE = 500
MAX_ROWS = 5_000
_RESERVOIR_SIZE = 100_000
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SZ|SH)$")


_FACTOR_SPECS: dict[str, dict[str, Any]] = {
    "pe_ttm": {
        "name": "市盈率 TTM",
        "formula": "pe_ttm[t] = daily_basic.pe_ttm[t]",
        "direction": "negative",
        "direction_label": "反向",
        "source": "daily_basic",
        "frequency": "每日",
        "missing_policy": "缺失值剔除，不以前值填充",
        "pit_policy": "从 canonical.parquet 读取该交易日的 daily_basic 字段值；不跨日填充",
        "formula_explanation": [
            "含义：TTM 市盈率，按最近四个季度滚动计算的总市值与净利润之比（单位：倍）。",
            "输入：Tushare daily_basic.pe_ttm 原值，不做单位换算。",
            "方向：反向——在样本期内认为市盈率越高、未来收益预期越低。",
            "点时：只使用该交易日的 daily_basic 快照值，不跨日填充。",
        ],
    },
    "total_market_cap": {
        "name": "总市值",
        "formula": "total_market_cap[t] = daily_basic.total_mv[t] × 10000",
        "direction": "negative",
        "direction_label": "反向",
        "source": "daily_basic",
        "frequency": "每日",
        "missing_policy": "缺失值剔除",
        "pit_policy": "从 canonical.parquet 读取该交易日的 daily_basic 字段值；不跨日填充",
        "formula_explanation": [
            "含义：全市场总市值（单位：元）。",
            "换算：Tushare 的 total_mv 单位是万元，注册字段乘以 10,000 转成元。",
            "方向：反向——规模因子，认为市值越大、未来收益预期越低。",
            "点时：只使用该交易日的 daily_basic 快照值，不跨日填充。",
        ],
    },
    "float_market_cap": {
        "name": "流通市值",
        "formula": "float_market_cap[t] = daily_basic.circ_mv[t] × 10000",
        "direction": "negative",
        "direction_label": "反向",
        "source": "daily_basic",
        "frequency": "每日",
        "missing_policy": "缺失值剔除",
        "pit_policy": "从 canonical.parquet 读取该交易日的 daily_basic 字段值；不跨日填充",
        "formula_explanation": [
            "含义：流通市值（单位：元）。",
            "换算：Tushare 的 circ_mv 单位是万元，注册字段乘以 10,000 转成元。",
            "方向：反向——规模因子，认为流通市值越大、未来收益预期越低。",
            "点时：只使用该交易日的 daily_basic 快照值，不跨日填充。",
        ],
    },
    "dividend_yield_ratio": {
        "name": "股息率",
        "formula": "dividend_yield_ratio[t] = daily_basic.dv_ttm[t] / 100",
        "direction": "positive",
        "direction_label": "正向",
        "source": "daily_basic",
        "frequency": "每日",
        "missing_policy": "缺失值剔除；零值保留",
        "pit_policy": "从 canonical.parquet 读取该交易日的 daily_basic 字段值；不跨日填充",
        "formula_explanation": [
            "含义：TTM 股息率（小数，如 0.03 表示 3%）。",
            "换算：Tushare 的 dv_ttm 按百分比提供，注册字段除以 100 转为小数。",
            "方向：正向——认为股息率越高、未来收益预期越高。",
            "点时：只使用该交易日的 daily_basic 快照值；0 值保留，不当作缺失。",
        ],
    },
    "momentum_5": {
        "name": "五日动量",
        "formula": "momentum_5[t] = hfq_close[t] / hfq_close[t-5 observations] - 1",
        "direction": "positive",
        "direction_label": "正向",
        "source": "hfq_daily_standard",
        "frequency": "每日",
        "missing_policy": "该股票不足 5 个有效后复权收盘观测时为空",
        "pit_policy": "只使用当日及此前有效观测，不使用未来价格",
        "formula_explanation": [
            "含义：该股票最近 5 个有效交易日区间的价格动量。",
            "输入：hfq_close 后复权收盘价；observations 指该股票自己的有效行情观测，不是自然日。",
            "行为：第 t 日的 hfq_close 除以该股票第 t-5 个有效观测日的 hfq_close，再减 1。",
            "空值：不足 5 个有效后复权收盘观测时为空；停牌不补价，缺失窗口不向前填充。",
            "点时：不使用未来价格，不使用跨股票窗口。",
        ],
    },
    "volatility_5": {
        "name": "五日波动率",
        "formula": "r[k] = hfq_close[k] / hfq_close[k-1] - 1\nvolatility_5[t] = std(r[t-4..t])",
        "direction": "negative",
        "direction_label": "反向",
        "source": "hfq_daily_standard",
        "frequency": "每日",
        "missing_policy": "按正式标准的有效窗口规则，缺失值剔除",
        "pit_policy": "只使用当日及此前有效观测，不使用未来价格",
        "formula_explanation": [
            "含义：最近 5 个该股票有效收益观测的波动率。",
            "第一步：r[k] = 当日 hfq_close / 前一有效观测日 hfq_close - 1。",
            "第二步：取 r[t-4] 至 r[t] 共 5 个有效收益计算标准差（正式标准）。",
            "空值：该股票不足 5 个有效收益观测时为空；停牌不补价。",
            "点时：只使用当日及此前有效观测，不使用未来价格。",
        ],
    },
    "turn": {
        "name": "换手率",
        "formula": "turn[t] = daily_basic.turnover_rate[t]",
        "direction": "positive",
        "direction_label": "正向",
        "source": "daily_basic",
        "frequency": "每日",
        "missing_policy": "缺失值剔除；零值保留",
        "pit_policy": "从 canonical.parquet 读取该交易日的 daily_basic 字段值；不跨日填充",
        "formula_explanation": [
            "含义：日换手率，保留 Tushare 原口径（百分比，如 2.5 表示 2.5%）。",
            "输入：daily_basic.turnover_rate 原值，不做单位换算。",
            "方向：正向——认为换手率越高、未来收益预期越高。",
            "点时：只使用该交易日的 daily_basic 快照值；0 值保留，不当作缺失。",
        ],
    },
}


_FACTOR_CATEGORIES = {
    "pe_ttm": "估值",
    "total_market_cap": "规模",
    "float_market_cap": "规模",
    "dividend_yield_ratio": "估值",
    "momentum_5": "动量",
    "volatility_5": "波动",
    "turn": "换手",
}



def _number(value: Any) -> float | None:
    if not isinstance(value, numbers.Real) or isinstance(value, bool):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


class FactorDataService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._duplicate_cache: dict[tuple[str, str], int] = {}

    @staticmethod
    def _spec(factor: str) -> tuple[str, dict[str, Any]]:
        normalized = str(factor).strip()
        if normalized in TARGET_ONLY_FIELDS:
            raise ValueError(f"{normalized} is target-only and cannot be used as a factor")
        if normalized not in _FACTOR_SPECS:
            raise ValueError(f"unknown factor: {normalized}")
        return normalized, _FACTOR_SPECS[normalized]

    def _dataset_binding(self, factor: str, version_id: str = "v1") -> dict[str, Any]:
        factor_id, _ = self._spec(factor)
        entity_id = f"factor_{factor_id}"
        self.sync_factor_versions()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT fv.entity_id, fv.version_id, fv.dataset_id, fv.dataset_version_id, "
                "fv.formula, fv.input_fields_json, fv.status, fv.quality_status, "
                "fv.source, fv.direction, fv.frequency, fv.missing_policy, fv.pit_policy, "
                "dv.path, dv.fields_json, dv.row_count, dv.date_min, dv.date_max, dv.metadata_json "
                "FROM factor_versions fv JOIN dataset_versions dv "
                "ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
                "WHERE fv.entity_id=? AND fv.version_id=?",
                (entity_id, version_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown factor version: {factor_id}:{version_id}")
        binding = dict(row)
        binding["input_fields"] = json.loads(binding.pop("input_fields_json") or "[]")
        binding["fields"] = json.loads(binding.pop("fields_json") or "[]")
        metadata = json.loads(binding.pop("metadata_json") or "{}")
        binding["storage_alias"] = metadata.get("path_alias") or ""
        return binding

    def _dataset(self, factor: str, version_id: str = "v1") -> tuple[ds.Dataset, dict[str, Any]]:
        binding = self._dataset_binding(factor, version_id)
        path = self.settings.require_read_path(binding["path"])
        if not path.is_file() or path.suffix != ".parquet":
            raise ValueError("factor dataset must be a registered parquet file")
        dataset = ds.dataset(path, format="parquet")
        if factor not in dataset.schema.names:
            raise ValueError(f"factor field is missing from dataset: {factor}")
        return dataset, binding

    def sync_factor_versions(self) -> int:
        """Create the immutable v1 physical mappings for the registered matrix."""
        with self.database.connect() as connection:
            dataset = connection.execute(
                "SELECT entity_id, version_id FROM dataset_versions "
                "WHERE entity_id='ds_canonical_market' AND version_id='current' LIMIT 1"
            ).fetchone()
        if dataset is None:
            return 0
        created = 0
        with self.database.transaction() as connection:
            for factor_id in FEATURE_FIELDS:
                spec = _FACTOR_SPECS[factor_id]
                connection.execute(
                    "INSERT INTO factors(entity_id, name, status) VALUES (?, ?, 'published') "
                    "ON CONFLICT(entity_id) DO NOTHING",
                    (f"factor_{factor_id}", spec["name"]),
                )
                connection.execute(
                    "UPDATE factors SET category=? WHERE entity_id=?",
                    (_FACTOR_CATEGORIES.get(factor_id, "未分类"), f"factor_{factor_id}"),
                )
                existing = connection.execute(
                    "SELECT 1 FROM factor_versions WHERE entity_id=? AND version_id='v1'",
                    (f"factor_{factor_id}",),
                ).fetchone()
                if existing is not None:
                    continue
                connection.execute(
                    "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, "
                    "source, direction, frequency, missing_policy, pit_policy, status, quality_status) "
                    "VALUES (?, 'v1', 'ds_canonical_market', ?, ?, ?, ?, ?, ?, ?, ?, 'published', 'passed')",
                    (
                        f"factor_{factor_id}",
                        dataset["version_id"],
                        spec["formula"],
                        json.dumps([factor_id]),
                        spec["source"],
                        spec["direction"],
                        spec["frequency"],
                        spec["missing_policy"],
                        spec["pit_policy"],
                    ),
                )
                created += 1
        return created

    @staticmethod
    def _footer_stats(path: Path, field: str) -> dict[str, Any]:
        parquet = pq.ParquetFile(path)
        names = parquet.schema_arrow.names
        if field not in names:
            raise ValueError(f"factor field is missing from dataset: {field}")
        index = names.index(field)
        total = 0
        missing: int | None = 0
        for group_index in range(parquet.metadata.num_row_groups):
            group = parquet.metadata.row_group(group_index)
            total += group.num_rows
            statistics = group.column(index).statistics
            if statistics is None or statistics.null_count is None:
                missing = None
            elif missing is not None:
                missing += statistics.null_count
        coverage = None if missing is None or total == 0 else (total - missing) / total
        return {"row_count": total, "missing_rows": missing, "coverage": coverage}

    def catalog(self) -> list[dict[str, Any]]:
        self.sync_factor_versions()
        stored_names: dict[str, str] = {}
        with self.database.connect() as connection:
            stored_names = {
                str(row["entity_id"]): str(row["name"])
                for row in connection.execute("SELECT entity_id, name FROM factors")
            }
        items: list[dict[str, Any]] = []
        for factor_id in FEATURE_FIELDS:
            try:
                binding = self._dataset_binding(factor_id)
            except ValueError:
                continue
            try:
                stats = self._footer_stats(self.settings.require_read_path(binding["path"]), factor_id)
                quality_status = binding["quality_status"]
            except (ValueError, OSError):
                stats = {"row_count": binding["row_count"], "missing_rows": None, "coverage": None}
                quality_status = binding["quality_status"] if binding["quality_status"] in {"passed", "warning"} else "needs_review"
            spec = _FACTOR_SPECS[factor_id]
            items.append({
                "factor_id": factor_id,
                "factor_entity_id": f"factor_{factor_id}",
                "name": stored_names.get(f"factor_{factor_id}", spec["name"]),
                "category": _FACTOR_CATEGORIES.get(factor_id, "未分类"),
                "version_id": binding["version_id"],
                "factor_version_id": binding["version_id"],
                "dataset_id": binding["dataset_id"],
                "dataset_version_id": binding["dataset_version_id"],
                "formula": spec["formula"],
                "formula_explanation": spec.get("formula_explanation") or [],
                "direction": binding["direction"],
                "direction_label": spec["direction_label"],
                "source": binding["source"],
                "frequency": binding["frequency"],
                "storage_alias": binding.get("storage_alias") or "",
                "missing_policy": binding["missing_policy"],
                "pit_policy": binding["pit_policy"],
                "target_only": False,
                "status": binding["status"],
                "quality_status": quality_status,
                **stats,
            })
        builtin_entities = {f"factor_{factor_id}" for factor_id in FEATURE_FIELDS}
        with self.database.connect() as connection:
            extra_rows = connection.execute(
                "SELECT f.entity_id AS entity_id, f.name AS name, f.category AS category, "
                "f.status AS factor_status, fv.version_id, fv.dataset_id, fv.dataset_version_id, "
                "fv.formula, fv.source, fv.direction, fv.frequency, fv.missing_policy, fv.pit_policy, "
                "fv.status AS version_status, fv.quality_status, dv.path, dv.row_count, dv.metadata_json "
                "FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id "
                "JOIN dataset_versions dv ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
                "WHERE fv.entity_id NOT IN ({}) AND fv.status <> 'deprecated' "
                "ORDER BY f.name, fv.version_id".format(
                    ",".join("?" for _ in builtin_entities)
                ),
                tuple(sorted(builtin_entities)),
            ).fetchall()
        for row in extra_rows:
            factor_entity_id = str(row["entity_id"])
            factor_id = factor_entity_id.removeprefix("factor_") if factor_entity_id.startswith("factor_") else factor_entity_id
            try:
                path = self.settings.require_read_path(row["path"])
                stats = self._footer_stats(path, factor_id)
            except (ValueError, OSError):
                sidecar = Path(str(row["path"])).expanduser().resolve().parent / "derived" / "canonical_pack_factors.parquet"
                try:
                    stats = self._footer_stats(self.settings.require_read_path(sidecar), factor_id)
                except (ValueError, OSError):
                    stats = {"row_count": row["row_count"], "missing_rows": None, "coverage": None}
            metadata = json.loads(row["metadata_json"] or "{}")
            items.append({
                "factor_id": factor_id,
                "factor_entity_id": factor_entity_id,
                "name": row["name"],
                "category": row["category"] or "未分类",
                "version_id": row["version_id"],
                "factor_version_id": row["version_id"],
                "dataset_id": row["dataset_id"],
                "dataset_version_id": row["dataset_version_id"],
                "formula": row["formula"],
                "formula_explanation": [],
                "direction": row["direction"],
                "direction_label": "正向" if str(row["direction"]).lower() == "positive" else "反向",
                "source": row["source"],
                "frequency": row["frequency"],
                "storage_alias": metadata.get("path_alias") or "",
                "missing_policy": row["missing_policy"],
                "pit_policy": row["pit_policy"],
                "target_only": False,
                "status": row["version_status"],
                "quality_status": row["quality_status"],
                **stats,
            })
        return items

    @staticmethod
    def _symbols(symbols: Sequence[str] | str | None) -> list[str]:
        if symbols is None:
            return []
        values = symbols.split(",") if isinstance(symbols, str) else list(symbols)
        normalized: list[str] = []
        for symbol in values:
            value = str(symbol).strip().upper()
            if not value:
                continue
            if not _SYMBOL_PATTERN.fullmatch(value):
                raise ValueError(f"invalid stock symbol: {value}")
            if value not in normalized:
                normalized.append(value)
        if len(normalized) > 50:
            raise ValueError("symbols cannot exceed 50")
        return normalized

    @staticmethod
    def _date(value: str | None, name: str) -> str | None:
        if value is None or not value.strip():
            return None
        for pattern in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), pattern).strftime("%Y%m%d")
            except ValueError:
                continue
        raise ValueError(f"invalid {name}: {value}")

    @staticmethod
    def _filter(symbols: list[str], date_from: str | None, date_to: str | None) -> ds.Expression | None:
        expression: ds.Expression | None = None
        if symbols:
            expression = ds.field("ts_code").isin(symbols)
        if date_from:
            value = ds.field("trade_date") >= date_from
            expression = value if expression is None else expression & value
        if date_to:
            value = ds.field("trade_date") <= date_to
            expression = value if expression is None else expression & value
        return expression

    @staticmethod
    def _fields(factor: str, fields: Sequence[str] | str | None) -> list[str]:
        allowed = {"date", "instrument", factor}
        if fields is None:
            return ["date", "instrument", factor]
        values = fields.split(",") if isinstance(fields, str) else list(fields)
        selected: list[str] = []
        for field in values:
            value = str(field).strip()
            if not value:
                continue
            if value in TARGET_ONLY_FIELDS:
                raise ValueError(f"{value} is target-only and cannot be used as a factor")
            if value not in allowed:
                raise ValueError(f"field is not allowed: {value}")
            if value not in selected:
                selected.append(value)
        if not selected:
            raise ValueError("at least one field is required")
        return selected

    def query(
        self,
        *,
        factor: str,
        symbols: Sequence[str] | str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = "v1",
        fields: Sequence[str] | str | None = None,
        page: int = 1,
        page_size: int = 100,
        max_rows: int = MAX_ROWS,
    ) -> dict[str, Any]:
        factor_id, _ = self._spec(factor)
        if page < 1:
            raise ValueError("page must be at least 1")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if max_rows < 1 or max_rows > MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {MAX_ROWS}")
        selected_fields = self._fields(factor_id, fields)
        selected_symbols = self._symbols(symbols)
        normalized_from = self._date(date_from, "date_from")
        normalized_to = self._date(date_to, "date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("date_from cannot be after date_to")
        try:
            dataset, binding = self._dataset(factor_id, version_id)
        except ValueError:
            binding = self._dataset_binding(factor_id, version_id)
            return {
                "factor": factor_id,
                "factor_version_id": binding["version_id"],
                "dataset_id": binding["dataset_id"],
                "dataset_version_id": binding["dataset_version_id"],
                "fields": selected_fields,
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "matching_rows": 0,
                "has_more": False,
                "truncated": False,
                "max_rows": max_rows,
                "training_eligible": True,
            }
        expression = self._filter(selected_symbols, normalized_from, normalized_to)
        matching_rows = dataset.count_rows(filter=expression)
        total = min(matching_rows, max_rows)
        start = (page - 1) * page_size
        stop = min(start + page_size, total)
        items: list[dict[str, Any]] = []
        if start < stop:
            scanner = dataset.scanner(columns=["trade_date", "ts_code", factor_id], filter=expression, batch_size=65_536)
            seen = 0
            for batch in scanner.to_batches():
                rows = batch.to_pydict()
                for values in zip(rows["trade_date"], rows["ts_code"], rows[factor_id], strict=True):
                    if seen >= stop:
                        break
                    if seen >= start:
                        item = {"date": values[0], "instrument": values[1], factor_id: values[2]}
                        items.append({field: item[field] for field in selected_fields})
                    seen += 1
                if seen >= stop:
                    break
        return {
            "factor": factor_id,
            "factor_version_id": binding["version_id"],
            "dataset_id": binding["dataset_id"],
            "dataset_version_id": binding["dataset_version_id"],
            "fields": selected_fields,
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "matching_rows": matching_rows,
            "has_more": stop < total,
            "truncated": matching_rows > max_rows,
            "max_rows": max_rows,
            "training_eligible": True,
        }

    def summary(
        self,
        *,
        factor: str,
        symbols: Sequence[str] | str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = "v1",
    ) -> dict[str, Any]:
        factor_id, _ = self._spec(factor)
        selected_symbols = self._symbols(symbols)
        normalized_from = self._date(date_from, "date_from")
        normalized_to = self._date(date_to, "date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("date_from cannot be after date_to")
        if not selected_symbols:
            if not normalized_from or not normalized_to:
                raise ValueError("summary requires a symbol or a bounded date range")
            start = datetime.strptime(normalized_from, "%Y%m%d")
            end = datetime.strptime(normalized_to, "%Y%m%d")
            if (end - start).days > 366:
                raise ValueError("summary date range cannot exceed 366 days")
        dataset, binding = self._dataset(factor_id, version_id)
        expression = self._filter(selected_symbols, normalized_from, normalized_to)
        row_count = dataset.count_rows(filter=expression)
        non_null = 0
        zero = 0
        negative = 0
        positive = 0
        minimum: float | None = None
        maximum: float | None = None
        daily: dict[str, dict[str, int]] = {}
        sample: list[float] = []
        seen_values = 0
        scanner = dataset.scanner(columns=["trade_date", "ts_code", factor_id], filter=expression, batch_size=65_536)
        for batch in scanner.to_batches():
            rows = batch.to_pydict()
            for trade_date, _symbol, raw_value in zip(rows["trade_date"], rows["ts_code"], rows[factor_id], strict=True):
                day = daily.setdefault(str(trade_date), {"row_count": 0, "non_null_count": 0})
                day["row_count"] += 1
                value = _number(raw_value)
                if value is None:
                    continue
                day["non_null_count"] += 1
                non_null += 1
                zero += value == 0
                negative += value < 0
                positive += value > 0
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
                if len(sample) < _RESERVOIR_SIZE:
                    sample.append(value)
                else:
                    index = (seen_values * 1_103_515_245 + 12_345) % _RESERVOIR_SIZE
                    sample[index] = value
                seen_values += 1
        sample.sort()
        def percentile(q: float) -> float | None:
            if not sample:
                return None
            position = (len(sample) - 1) * q
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return sample[lower]
            return sample[lower] + (sample[upper] - sample[lower]) * (position - lower)
        null_count = row_count - non_null
        duplicate_key_count = self._duplicate_key_count(dataset, expression)
        return {
            "factor": factor_id,
            "factor_version_id": binding["version_id"],
            "dataset_id": binding["dataset_id"],
            "dataset_version_id": binding["dataset_version_id"],
            "row_count": row_count,
            "non_null_count": non_null,
            "null_count": null_count,
            "coverage": (non_null / row_count) if row_count else None,
            "zero_count": zero,
            "negative_count": negative,
            "positive_count": positive,
            "min": minimum,
            "max": maximum,
            "quantiles": {"p01": percentile(0.01), "p25": percentile(0.25), "p50": percentile(0.5), "p75": percentile(0.75), "p99": percentile(0.99)},
            "daily_cross_section": [
                {"date": day, **daily[day]} for day in sorted(daily)
            ],
            "duplicate_key_count": duplicate_key_count,
            "quantile_sample_size": len(sample),
        }

    def quality(self, *, version_id: str = "v1") -> dict[str, Any]:
        self.sync_factor_versions()
        items = self.catalog()
        if not items:
            raise ValueError("registered canonical dataset is required")
        dataset, binding = self._dataset(FEATURE_FIELDS[0], version_id)
        cache_key = (binding["dataset_id"], binding["dataset_version_id"])
        duplicate_key_count = self._duplicate_cache.get(cache_key)
        if duplicate_key_count is None:
            duplicate_key_count = self._duplicate_key_count(dataset, None)
            self._duplicate_cache[cache_key] = duplicate_key_count
        return {
            "dataset_id": binding["dataset_id"],
            "dataset_version_id": binding["dataset_version_id"],
            "factor_version_id": version_id,
            "row_count": binding["row_count"],
            "date_min": binding["date_min"],
            "date_max": binding["date_max"],
            "duplicate_key_count": duplicate_key_count,
            "target_only_fields": sorted(TARGET_ONLY_FIELDS),
            "factors": {
                item["factor_id"]: {
                    "fields": ["trade_date", "ts_code", item["factor_id"]],
                    "coverage": item["coverage"],
                    "missing_rows": item["missing_rows"],
                    "status": item["quality_status"],
                }
                for item in items
            },
        }

    def csv_text(self, *, factor: str, max_rows: int = MAX_ROWS, **filters: Any) -> str:
        if max_rows < 1 or max_rows > MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {MAX_ROWS}")
        result = self.query(factor=factor, page=1, page_size=min(MAX_PAGE_SIZE, max_rows), max_rows=max_rows, **filters)
        rows = list(result["items"])
        page = 2
        while len(rows) < max_rows and result["has_more"]:
            result = self.query(factor=factor, page=page, page_size=min(MAX_PAGE_SIZE, max_rows), max_rows=max_rows, **filters)
            rows.extend(result["items"])
            page += 1
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=result["fields"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows[:max_rows])
        return output.getvalue()

    @staticmethod
    def _duplicate_key_count(
        dataset: ds.Dataset, expression: ds.Expression | None
    ) -> int:
        """Count duplicate (date, symbol) rows without retaining all keys in Python."""
        with tempfile.TemporaryDirectory(prefix="quantlab-factor-keys-") as directory:
            connection = sqlite3.connect(Path(directory) / "keys.sqlite3")
            try:
                connection.execute(
                    "CREATE TABLE keys (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, "
                    "PRIMARY KEY (trade_date, ts_code))"
                )
                total = 0
                scanner = dataset.scanner(
                    columns=["trade_date", "ts_code"],
                    filter=expression,
                    batch_size=65_536,
                )
                for batch in scanner.to_batches():
                    rows = batch.to_pydict()
                    keys = list(zip(rows["trade_date"], rows["ts_code"], strict=True))
                    total += len(keys)
                    connection.executemany(
                        "INSERT OR IGNORE INTO keys(trade_date, ts_code) VALUES (?, ?)",
                        keys,
                    )
                    connection.commit()
                unique = connection.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
                return total - unique
            finally:
                connection.close()

    def create_sample_export(
        self,
        *,
        artifacts: Any,
        identity: Any,
        factor: str,
        symbols: Sequence[str] | str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = "v1",
        max_rows: int = MAX_ROWS,
    ) -> dict[str, Any]:
        """Persist a bounded factor sample and its exact query manifest.

        The source parquet remains read-only.  Only the bounded result and the
        manifest are written below the approved runtime artifact root.
        """
        factor_id, _ = self._spec(factor)
        normalized_symbols = self._symbols(symbols)
        run_id = identity.next_id()
        config = {
            "factor": factor_id,
            "symbols": normalized_symbols,
            "date_from": date_from,
            "date_to": date_to,
            "version_id": version_id,
            "max_rows": max_rows,
        }
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type) VALUES (?, 'research')",
                (run_id,),
            )
            connection.execute(
                "INSERT INTO research_runs(run_id, status, config_json) VALUES (?, 'queued', ?)",
                (run_id, json.dumps(config, ensure_ascii=False, sort_keys=True)),
            )
            connection.execute(
                "UPDATE research_runs SET status='running' WHERE run_id=?", (run_id,)
            )
        try:
            result = self.query(
                factor=factor_id, symbols=normalized_symbols, date_from=date_from,
                date_to=date_to, version_id=version_id, page=1,
                page_size=min(MAX_PAGE_SIZE, max_rows), max_rows=max_rows,
            )
            output_dir = self.settings.runtime_root / "results" / run_id
            output_dir.mkdir(parents=True, exist_ok=False)
            sample_path = output_dir / f"{factor_id}-sample.csv"
            manifest_path = output_dir / "query-manifest.json"
            sample_text = self.csv_text(
                factor=factor_id, symbols=normalized_symbols, date_from=date_from,
                date_to=date_to, version_id=version_id, max_rows=max_rows,
            )
            sample_path.write_text(sample_text, encoding="utf-8")
            exported_rows = max(0, len(sample_text.splitlines()) - 1)
            manifest_path.write_text(json.dumps({"run_id": run_id, **config, **{
                "factor_version_id": result["factor_version_id"],
                "dataset_id": result["dataset_id"],
                "dataset_version_id": result["dataset_version_id"],
                "fields": result["fields"], "matching_rows": result["matching_rows"],
                "exported_rows": exported_rows,
            }}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            registered = [
                artifacts.register(run_id=run_id, path=sample_path,
                                   display_name="因子样本数据", artifact_role="factor_sample"),
                artifacts.register(run_id=run_id, path=manifest_path,
                                   display_name="因子查询清单", artifact_role="query_manifest"),
            ]
            with self.database.transaction() as connection:
                connection.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (run_id,))
        except Exception as error:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE research_runs SET status='failed', error_message=? WHERE run_id=?",
                    (str(error), run_id),
                )
            raise
        return {
            "run_id": run_id,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "display_name": item.display_name,
                    "artifact_role": item.artifact_role,
                    "path": item.path,
                    "content_hash": item.content_hash,
                }
                for item in registered
            ],
        }
