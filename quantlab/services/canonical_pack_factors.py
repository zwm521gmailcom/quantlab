"""Materialize canonical pack formulas into a sidecar parquet for backtests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK
from quantlab.services.factor_manual import _eval, finite_factor_values, parse_expression


PACK_SIDECAR_NAME = "canonical_pack_factors.parquet"
PACK_FIELDS = tuple(item.field for item in CANONICAL_FACTOR_PACK)
_PACK_FORMULAS = {item.field: item.formula for item in CANONICAL_FACTOR_PACK}


def default_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / PACK_SIDECAR_NAME


def _compact_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace("-", "", regex=False).str.replace(".", "", regex=False).str[:8]


def _needed_pack_fields(frame: pd.DataFrame, refs: list[Any] | None) -> list[str]:
    needed: list[str] = []
    columns = set(getattr(frame, "columns", ()))
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("factor_id") or "").strip()
        if field.startswith("factor_"):
            field = field.removeprefix("factor_")
        if field in _PACK_FORMULAS and field not in columns and field not in needed:
            needed.append(field)
    return needed


def attach_pack_factor_columns(
    frame: pd.DataFrame,
    refs: list[Any] | None,
    sidecar_path: Path | str,
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return frame
    needed = _needed_pack_fields(frame, refs)
    if not needed:
        return frame
    sidecar = Path(sidecar_path)
    if not sidecar.is_file():
        raise ValueError("可算因子还没有写入旁路文件。请先补全因子计算。")
    names = set(pq.ParquetFile(sidecar).schema_arrow.names)
    missing = [field for field in needed if field not in names]
    if missing:
        raise ValueError(f"旁路因子文件缺少字段 {', '.join(missing)}。")
    code_key = "ts_code" if "ts_code" in names else "instrument"
    date_key = "trade_date" if "trade_date" in names else "date"
    extra = pq.read_table(sidecar, columns=[code_key, date_key, *needed]).to_pandas()
    extra["_code"] = extra[code_key].astype(str)
    extra["_date"] = _compact_date(extra[date_key])
    result = frame.copy()
    left_code = result["instrument"] if "instrument" in result.columns else result["ts_code"]
    left_date = result["date"] if "date" in result.columns else result["trade_date"]
    result["_code"] = left_code.astype(str)
    result["_date"] = _compact_date(left_date)
    merged = result.merge(extra[["_code", "_date", *needed]], on=["_code", "_date"], how="left")
    return merged.drop(columns=["_code", "_date"])


def materialize_canonical_pack_factors(
    canonical_path: Path | str,
    output_path: Path | str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    canonical = Path(canonical_path).expanduser().resolve()
    if not canonical.is_file():
        raise ValueError("找不到 canonical.parquet")
    output = Path(output_path) if output_path else default_sidecar_path(canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    names = set(pq.ParquetFile(canonical).schema_arrow.names)
    inputs: list[str] = []
    for spec in CANONICAL_FACTOR_PACK:
        parsed = parse_expression(spec.formula, names | {"instrument", "date", "trade_date", "ts_code"})
        for field in parsed.input_fields:
            if field in names and field not in inputs:
                inputs.append(field)
    columns = [name for name in ("ts_code", "trade_date", *inputs) if name in names]
    frame = pq.read_table(canonical, columns=columns).to_pandas()
    if "ts_code" not in frame.columns or "trade_date" not in frame.columns:
        raise ValueError("canonical.parquet 缺少 ts_code/trade_date")
    frame["instrument"] = frame["ts_code"].astype(str)
    frame["date"] = _compact_date(frame["trade_date"])
    frame = frame.sort_values(["instrument", "date"], kind="mergesort").reset_index(drop=True)
    available = set(frame.columns)
    payload: dict[str, Any] = {
        "ts_code": frame["instrument"].to_numpy(),
        "trade_date": frame["date"].to_numpy(),
    }
    total = len(CANONICAL_FACTOR_PACK)
    for index, spec in enumerate(CANONICAL_FACTOR_PACK, start=1):
        if progress is not None:
            progress(spec.field, index, total)
        parsed = parse_expression(spec.formula, available)
        values = finite_factor_values(_eval(parsed.tree, frame))
        payload[spec.field] = pd.to_numeric(values, errors="coerce").to_numpy(dtype="float64")
    table = pa.table(payload)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": str(output),
        "rows": int(table.num_rows),
        "fields": list(PACK_FIELDS),
    }
