"""Apply raw suspend_d flags onto the canonical market table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def _norm_date(value: object) -> str:
    return str(value or "").replace("-", "").replace(".", "")[:8]


def _suspend_keys(suspend_path: Path) -> set[tuple[str, str]]:
    table = pq.read_table(suspend_path, columns=["ts_code", "trade_date", "suspend_type"])
    keys: set[tuple[str, str]] = set()
    for code, date, kind in zip(
        table["ts_code"].to_pylist(),
        table["trade_date"].to_pylist(),
        table["suspend_type"].to_pylist(),
        strict=True,
    ):
        if str(kind or "").strip().upper() != "S":
            continue
        compact = _norm_date(date)
        symbol = str(code or "").strip()
        if symbol and len(compact) == 8:
            keys.add((symbol, compact))
    return keys


def apply_suspend_d(*, canonical_path: Path, suspend_path: Path) -> dict[str, Any]:
    canonical = Path(canonical_path)
    suspend = Path(suspend_path)
    if not suspend.is_file():
        raise ValueError("suspend_d parquet is missing")
    if not canonical.is_file():
        raise ValueError("canonical.parquet is missing")
    keys = _suspend_keys(suspend)
    reader = pq.ParquetFile(canonical)
    names = reader.schema_arrow.names
    if "ts_code" not in names or "trade_date" not in names:
        raise ValueError("canonical.parquet missing ts_code/trade_date")
    tmp = canonical.with_name(canonical.name + ".next")
    writer: pq.ParquetWriter | None = None
    marked = 0
    eligible_cleared = 0
    rows = 0
    replaced = False
    try:
        for batch in reader.iter_batches(batch_size=1_048_576):
            table = pa.Table.from_batches([batch])
            codes = table["ts_code"].to_pylist()
            dates = [_norm_date(value) for value in table["trade_date"].to_pylist()]
            flags = [1 if (str(code), date) in keys else 0 for code, date in zip(codes, dates, strict=True)]
            marked += sum(flags)
            rows += len(flags)
            if "is_suspended" in table.column_names:
                table = table.set_column(
                    table.schema.get_field_index("is_suspended"),
                    "is_suspended",
                    pa.array(flags, type=table.schema.field("is_suspended").type),
                )
            else:
                table = table.append_column("is_suspended", pa.array(flags, type=pa.int64()))
            if "eligible" in table.column_names:
                previous = table["eligible"].to_pylist()
                updated = []
                for flag, value in zip(flags, previous, strict=True):
                    if flag:
                        if int(value or 0):
                            eligible_cleared += 1
                        updated.append(0)
                    else:
                        updated.append(int(value or 0))
                table = table.set_column(
                    table.schema.get_field_index("eligible"),
                    "eligible",
                    pa.array(updated, type=table.schema.field("eligible").type),
                )
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema, compression="snappy")
            writer.write_table(table)
        if writer is None:
            raise ValueError("canonical.parquet is empty")
        writer.close()
        writer = None
        tmp.replace(canonical)
        replaced = True
    finally:
        if writer is not None:
            writer.close()
        if not replaced and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {
        "canonical": str(canonical),
        "suspend_keys": len(keys),
        "rows": rows,
        "marked": marked,
        "eligible_cleared": eligible_cleared,
    }
