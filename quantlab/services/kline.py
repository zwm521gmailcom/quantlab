"""Bounded, read-only K-line queries over the canonical standard market table."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from quantlab.config import Settings
from quantlab.repositories.database import Database


KLINE_DATASET_ID = "ds_canonical_market"
LEGACY_KLINE_DATASET_ID = "ds_hfq_market_st_v1"
DEFAULT_KLINE_VERSION = "current"
MAX_SYMBOLS = 50
MAX_PAGE_SIZE = 500
MAX_ROWS = 5_000

_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SZ|SH)$")
_CANONICAL_BASE = {"trade_date", "ts_code", "adj_factor", "vol", "amount", "st_status"}
_COMMON_FIELDS = [
    "trade_date", "ts_code", "vol", "amount", "adj_factor", "st_status",
    "is_suspended", "eligible", "exchange", "list_date", "delist_date",
    "pe_ttm", "total_mv", "circ_mv", "dv_ttm", "turnover_rate",
    "total_market_cap", "float_market_cap", "dividend_yield_ratio", "turn",
]
_RAW_COLUMNS = {
    "raw_open": "open", "raw_high": "high", "raw_low": "low", "raw_close": "close",
    "raw_up_limit": "up_limit", "raw_down_limit": "down_limit",
}
_HFQ_COLUMNS = {
    "hfq_open": "hfq_open", "hfq_high": "hfq_high", "hfq_low": "hfq_low",
    "hfq_close": "hfq_close", "hfq_up_limit": "hfq_up_limit", "hfq_down_limit": "hfq_down_limit",
}
_REQUIRED_FIELDS = {
    "trade_date", "ts_code", "open", "high", "low", "close",
    "raw_open", "raw_close", "hfq_open", "hfq_high", "hfq_low", "hfq_close", "adj_factor", "st_status",
}
_OHLC_GROUPS = (
    ("open", "high", "low", "close"),
    ("raw_open", "raw_high", "raw_low", "raw_close"),
    ("hfq_open", "hfq_high", "hfq_low", "hfq_close"),
)
_QUALITY_CACHE: dict[tuple[str, str | None, str | None], dict[str, int]] = {}


def _ohlc_batch_violations(batch: pa.RecordBatch, groups: Sequence[tuple[str, str, str, str]]) -> int:
    table = pa.Table.from_batches([batch])
    bad = None
    for open_key, high_key, low_key, close_key in groups:
        if open_key not in table.column_names:
            continue
        open_ = pc.cast(table[open_key], pa.float64())
        high = pc.cast(table[high_key], pa.float64())
        low = pc.cast(table[low_key], pa.float64())
        close = pc.cast(table[close_key], pa.float64())
        present = pc.and_(
            pc.and_(
                pc.and_(pc.is_valid(open_), pc.invert(pc.is_nan(open_))),
                pc.and_(pc.is_valid(high), pc.invert(pc.is_nan(high))),
            ),
            pc.and_(
                pc.and_(pc.is_valid(low), pc.invert(pc.is_nan(low))),
                pc.and_(pc.is_valid(close), pc.invert(pc.is_nan(close))),
            ),
        )
        ceiling = pc.max_element_wise(open_, close)
        floor = pc.min_element_wise(open_, close)
        group_bad = pc.and_(
            present,
            pc.or_(
                pc.less(high, ceiling),
                pc.or_(pc.greater(low, floor), pc.less(high, low)),
            ),
        )
        bad = group_bad if bad is None else pc.or_(bad, group_bad)
    if bad is None:
        return 0
    total = pc.sum(pc.cast(bad, pa.int64()))
    return int(total.as_py() or 0)


class KlineQueryService:
    """Read canonical (or legacy snapshot when canonical is not registered)."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def _binding(self, version_id: str) -> tuple[str, Path, dict[str, Any], bool]:
        for entity_id in (KLINE_DATASET_ID, LEGACY_KLINE_DATASET_ID):
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT path, fields_json, metadata_json, row_count, date_min, date_max "
                    "FROM dataset_versions WHERE entity_id=? AND version_id=?",
                    (entity_id, version_id),
                ).fetchone()
            if row is not None:
                path = self.settings.require_read_path(Path(row["path"]))
                metadata = json.loads(row["metadata_json"] or "{}")
                binding = {
                    "path": path,
                    "row_count": row["row_count"],
                    "date_min": row["date_min"],
                    "date_max": row["date_max"],
                    "fields": json.loads(row["fields_json"] or "[]"),
                    "metadata": metadata,
                }
                return entity_id, path, binding, entity_id == KLINE_DATASET_ID
        raise ValueError(f"unknown kline dataset version: {version_id}")

    def _dataset(self, version_id: str) -> tuple[ds.Dataset, dict[str, Any], bool]:
        entity_id, path, binding, canonical = self._binding(version_id)
        if path.is_file() and path.suffix == ".parquet":
            return ds.dataset(path, format="parquet"), binding, canonical
        # legacy partitioned snapshot fallback
        partitions = sorted(path.glob("year=*/part.parquet"))
        partitioning = ds.partitioning(pa.schema([pa.field("year", pa.int32())]), flavor="hive")
        return ds.dataset([str(p) for p in partitions], format="parquet", partitioning=partitioning), binding, canonical

    @staticmethod
    def _symbols(symbols: Sequence[str] | str | None) -> list[str]:
        if symbols is None:
            return []
        values = symbols.split(",") if isinstance(symbols, str) else list(symbols)
        normalized: list[str] = []
        for value in values:
            symbol = str(value).strip().upper()
            if not symbol:
                continue
            if not re.fullmatch(r"\d{6}\.(?:SZ|SH)", symbol):
                raise ValueError(f"invalid stock symbol: {symbol}")
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @staticmethod
    def _date(value: str | None, name: str) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        for pattern in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern).strftime("%Y%m%d")
            except ValueError:
                continue
        raise ValueError(f"invalid {name}: {value}")

    @staticmethod
    def _filter(symbols: list[str], date_from: str | None, date_to: str | None) -> ds.Expression | None:
        expression: ds.Expression | None = None
        if symbols:
            expression = ds.field("ts_code").isin(symbols)
        if date_from:
            expr = ds.field("trade_date") >= date_from
            expression = expr if expression is None else expression & expr
        if date_to:
            expr = ds.field("trade_date") <= date_to
            expression = expr if expression is None else expression & expr
        return expression

    def _fields(self, mode: str, fields: Sequence[str] | str | None) -> list[str]:
        if mode not in {"raw", "hfq"}:
            raise ValueError(f"invalid kline mode: {mode}")
        mode_fields = {
            "raw": list(_RAW_COLUMNS),
            "hfq": list(_HFQ_COLUMNS),
        }[mode]
        mode_field_set = set(mode_fields)
        defaults = [field for field in _COMMON_FIELDS if field not in mode_field_set] + mode_fields
        if fields is None:
            return defaults
        values = fields.split(",") if isinstance(fields, str) else list(fields)
        mode_allowed = {
            "raw": set(_RAW_COLUMNS),
            "hfq": set(_HFQ_COLUMNS),
        }[mode]
        allowed = set(_CANONICAL_BASE) | mode_allowed
        selected: list[str] = []
        for field in values:
            field = field.strip()
            if field not in allowed:
                raise ValueError(f"field is not allowed for {mode}: {field}")
            if field not in selected:
                selected.append(field)
        return selected or defaults

    def _read_columns(self, fields: list[str], mode: str, has_raw: bool = True) -> list[str]:
        columns = {"trade_date", "ts_code"}
        for field in fields:
            if field in _RAW_COLUMNS:
                columns.add(field if has_raw else _RAW_COLUMNS[field])
            elif field in _HFQ_COLUMNS:
                columns.add(field)
            else:
                columns.add(field)
        return sorted(columns)

    def query(self, *, symbols=None, date_from=None, date_to=None, version_id=DEFAULT_KLINE_VERSION,
              mode: str = "raw", fields: Sequence[str] | str | None = None, page: int = 1,
              page_size: int = 100, max_rows: int = MAX_ROWS, downsample: int | None = None,
              tail: bool = False) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be positive")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError("page_size must be between 1 and 500")
        if max_rows < 1 or max_rows > MAX_ROWS:
            raise ValueError("max_rows must be between 1 and 5000")
        if downsample is not None and (downsample < 2 or downsample > 200):
            raise ValueError("downsample must be between 2 and 200")
        selected_symbols = self._symbols(symbols)
        normalized_from = self._date(date_from, "date_from")
        normalized_to = self._date(date_to, "date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("date_from cannot be after date_to")
        if selected_symbols and len(selected_symbols) > MAX_SYMBOLS:
            raise ValueError("symbols cannot exceed 50")
        dataset, binding, canonical = self._dataset(version_id)
        dataset_names = set(dataset.schema.names)
        has_raw = "raw_open" in dataset_names
        selected_fields = self._fields(mode, fields)
        if fields is None:
            def _available(field: str) -> bool:
                if field in _RAW_COLUMNS:
                    source = _RAW_COLUMNS[field] if not has_raw else field
                elif field in _HFQ_COLUMNS:
                    source = field
                else:
                    source = field
                return source in dataset_names
            selected_fields = [field for field in selected_fields if _available(field)]
        expression = self._filter(selected_symbols, normalized_from, normalized_to)
        matching_rows = int(dataset.count_rows(filter=expression))
        if tail:
            start = max(matching_rows - page_size, 0)
            stop = matching_rows
            total = matching_rows
        else:
            total = min(matching_rows, max_rows)
            start = (page - 1) * page_size
            stop = min(start + page_size, total)
        items: list[dict[str, Any]] = []
        if start < stop:
            columns = self._read_columns(selected_fields, mode, has_raw=has_raw)
            scanner = dataset.scanner(columns=columns, filter=expression, batch_size=65_536)
            seen = 0
            for batch in scanner.to_batches():
                for row in batch.to_pylist():
                    if seen >= stop:
                        break
                    if seen >= start:
                        out: dict[str, Any] = {}
                        for field in selected_fields:
                            if field in _CANONICAL_BASE:
                                out[field] = row.get(field)
                            elif field in _RAW_COLUMNS:
                                source = field if has_raw else _RAW_COLUMNS[field]
                                out[field] = row.get(source)
                            elif field in _HFQ_COLUMNS:
                                out[field] = row.get(field)
                            else:
                                out[field] = row.get(field)
                        items.append(out)
                    seen += 1
                if seen >= stop:
                    break
        if downsample is not None and len(items) > downsample:
            indices = [round(index * (len(items) - 1) / (downsample - 1)) for index in range(downsample)]
            items = [items[index] for index in indices]
        return {
            "dataset_id": KLINE_DATASET_ID if canonical else LEGACY_KLINE_DATASET_ID,
            "version_id": version_id,
            "mode": mode,
            "fields": selected_fields,
            "persisted_fields": list(selected_fields),
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": False if tail else stop < total,
            "truncated": matching_rows > max_rows and not tail,
            "max_rows": max_rows,
            "training_eligible": True,
            "downsampled": downsample is not None,
            "sample_size": len(items),
        }

    def summary(self, *, symbols=None, date_from=None, date_to=None, version_id=DEFAULT_KLINE_VERSION) -> dict[str, Any]:
        selected_symbols = self._symbols(symbols)
        dataset, binding, canonical = self._dataset(version_id)
        expression = self._filter(selected_symbols, self._date(date_from, "date_from"), self._date(date_to, "date_to"))
        row_count = int(dataset.count_rows(filter=expression))
        found: set[str] = set()
        minimum: str | None = None
        maximum: str | None = None
        for batch in dataset.scanner(columns=["trade_date", "ts_code"], filter=expression, batch_size=65_536).to_batches():
            for row in batch.to_pylist():
                found.add(row["ts_code"])
                date = row["trade_date"]
                minimum = date if minimum is None else min(minimum, date)
                maximum = date if maximum is None else max(maximum, date)
        return {
            "dataset_id": KLINE_DATASET_ID if canonical else LEGACY_KLINE_DATASET_ID,
            "version_id": version_id,
            "row_count": row_count,
            "symbol_count": len(found),
            "symbols": sorted(found),
            "date_min": minimum,
            "date_max": maximum,
            "manifest_row_count": binding.get("row_count"),
            "manifest_date_min": binding.get("date_min"),
            "manifest_date_max": binding.get("date_max"),
        }

    def quality(self, *, version_id: str = DEFAULT_KLINE_VERSION) -> dict[str, Any]:
        dataset, binding, canonical = self._dataset(version_id)
        actual_fields = set(dataset.schema.names)
        integrity = self._integrity_counts(dataset, binding)
        row_count = integrity["row_count"]
        legacy_required = {"trade_date", "ts_code", "raw_open", "raw_high", "raw_low", "raw_close",
                           "hfq_open", "hfq_high", "hfq_low", "hfq_close", "adj_factor", "st_status"}
        canonical_required = {"trade_date", "ts_code", "open", "high", "low", "close",
                              "hfq_open", "hfq_high", "hfq_low", "hfq_close", "adj_factor", "st_status"}
        checks = {
            "required_fields": canonical_required <= actual_fields or legacy_required <= actual_fields,
            "manifest_row_count_matches": binding.get("row_count") in {None, row_count},
            "duplicate_key_count": integrity["duplicate_key_count"],
            "ohlc_violation_count": integrity["ohlc_violation_count"],
            "date_range_declared": bool(binding.get("date_min") and binding.get("date_max")),
        }
        passed = all(value is True or value == 0 for value in checks.values())
        return {
            "dataset_id": KLINE_DATASET_ID if canonical else LEGACY_KLINE_DATASET_ID,
            "version_id": version_id,
            "status": "passed" if passed else "needs_review",
            "row_count": row_count,
            "symbol_count": None,
            "date_min": binding.get("date_min"),
            "date_max": binding.get("date_max"),
            "schema": sorted(actual_fields),
            "formula_version": None,
            "observations": [],
            "checks": checks,
        }

    def _quality_cache_path(self) -> Path:
        return Path(self.settings.runtime_root) / "kline_quality_cache.json"

    def _integrity_counts(self, dataset: ds.Dataset, binding: dict[str, Any]) -> dict[str, int]:
        cache_key = (
            str(binding.get("path") or ""),
            binding.get("date_min"),
            binding.get("date_max"),
        )
        cached = _QUALITY_CACHE.get(cache_key)
        if cached is not None:
            return cached
        disk = self._read_quality_cache(cache_key, binding)
        if disk is not None:
            _QUALITY_CACHE[cache_key] = disk
            return disk
        names = set(dataset.schema.names)
        groups = [group for group in _OHLC_GROUPS if set(group) <= names]
        columns = ["trade_date", "ts_code", *[name for group in groups for name in group]]
        columns = [name for name in dict.fromkeys(columns) if name in names]
        with tempfile.TemporaryDirectory(prefix="quantlab-kline-keys-") as directory:
            connection = sqlite3.connect(Path(directory) / "keys.sqlite3")
            try:
                connection.execute("PRAGMA journal_mode=OFF")
                connection.execute("PRAGMA synchronous=OFF")
                connection.execute("PRAGMA temp_store=MEMORY")
                connection.execute(
                    "CREATE TABLE keys (trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, "
                    "PRIMARY KEY (trade_date, ts_code))"
                )
                total = 0
                ohlc_bad = 0
                scanner = dataset.scanner(columns=columns, batch_size=1_048_576)
                for batch in scanner.to_batches():
                    payload = batch.to_pydict()
                    keys = list(zip(payload["trade_date"], payload["ts_code"], strict=True))
                    total += len(keys)
                    connection.executemany(
                        "INSERT OR IGNORE INTO keys(trade_date, ts_code) VALUES (?, ?)",
                        keys,
                    )
                    ohlc_bad += _ohlc_batch_violations(batch, groups)
                unique = int(connection.execute("SELECT COUNT(*) FROM keys").fetchone()[0])
            finally:
                connection.close()
        counts = {
            "row_count": total,
            "duplicate_key_count": total - unique,
            "ohlc_violation_count": ohlc_bad,
        }
        _QUALITY_CACHE[cache_key] = counts
        self._write_quality_cache(cache_key, binding, counts)
        return counts

    def _read_quality_cache(self, cache_key: tuple, binding: dict[str, Any]) -> dict[str, int] | None:
        path = self._quality_cache_path()
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            payload.get("path") != cache_key[0]
            or payload.get("date_min") != cache_key[1]
            or payload.get("date_max") != cache_key[2]
            or payload.get("manifest_row_count") != binding.get("row_count")
        ):
            return None
        try:
            return {
                "row_count": int(payload["row_count"]),
                "duplicate_key_count": int(payload["duplicate_key_count"]),
                "ohlc_violation_count": int(payload["ohlc_violation_count"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _write_quality_cache(self, cache_key: tuple, binding: dict[str, Any], counts: dict[str, int]) -> None:
        try:
            path = self.settings.require_write_path(self._quality_cache_path())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "path": cache_key[0],
                        "date_min": cache_key[1],
                        "date_max": cache_key[2],
                        "manifest_row_count": binding.get("row_count"),
                        **counts,
                    }
                ),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            return

    def csv_text(self, **query: object) -> str:
        max_rows = int(query.get("max_rows", MAX_ROWS))
        query = {k: v for k, v in query.items() if k not in {"max_rows", "page", "page_size", "downsample"}}
        rows: list[dict[str, Any]] = []
        page = 1
        while len(rows) < max_rows:
            result = self.query(**query, page=page, page_size=min(MAX_PAGE_SIZE, max_rows), max_rows=max_rows)
            rows.extend(result["items"])
            if not result["has_more"]:
                break
            page += 1
        output = io.StringIO()
        fields = result.get("fields", [])
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            writer.writerow({key: item.get(key) for key in fields})
        return output.getvalue()
