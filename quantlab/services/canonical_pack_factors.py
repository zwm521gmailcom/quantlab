"""Materialize canonical pack formulas into a sidecar parquet for backtests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.config import posix_relative
from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK
from quantlab.services.factor_manual import _eval, finite_factor_values, parse_expression


PACK_SIDECAR_NAME = "canonical_pack_factors.parquet"
COMPOSITE_SIDECAR_NAME = "composite_pack_factors.parquet"
PACK_FIELDS = tuple(item.field for item in CANONICAL_FACTOR_PACK)
_PACK_FORMULAS = {item.field: item.formula for item in CANONICAL_FACTOR_PACK}
COMPOSITE_FORMULAS = {
    "sleeve_mv_div": "total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)",
    "sleeve_price_mv_div": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_mom_mv": "momentum_10.cs_rank(0) + total_market_cap.cs_rank(0)",
    "sleeve_mom_price": "momentum_10.cs_rank(0) + hfq_close.ts_rank(20)",
    "sleeve_mom_mv_div": (
        "momentum_10.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_mom_price_mv_div": (
        "momentum_10.cs_rank(0) + hfq_close.ts_rank(20) + "
        "total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_mv_div_vol": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + vol_mean_20.cs_rank(0)"
    ),
    "sleeve_price_mv_div_range": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + intraday_range.cs_rank(0)"
    ),
    "sleeve_price60_mv_div": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_float_div": (
        "hfq_close.ts_rank(20) + float_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_mv_div_cheap": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0))"
    ),
    "sleeve_price_mv_div_quiet": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price_small_div": (
        "hfq_close.ts_rank(20) + (1 - total_market_cap.cs_rank(0)) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv": "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0)",
    "sleeve_price60_div": "hfq_close.ts_rank(60) + dividend_yield_ratio.cs_rank(0)",
    "sleeve_price60_mv_div_cheap": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0))"
    ),
    "sleeve_price60_mv_div_quiet": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price60_mv_div_cheap_quiet": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0)) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price60_mv_div0": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv_div25": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0.25 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv_div50": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0.5 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_zscore60_mv_div": (
        "close_zscore_60.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_bias60_mv_div": (
        "close_bias_60.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
}


def _coef_text(coef: float) -> str:
    if float(coef).is_integer():
        return str(int(coef))
    return f"{coef:g}"


def _weighted_term(coef: float, expr: str) -> str:
    if coef == 0:
        return f"0 * {expr}"
    if coef == 1:
        return expr
    return f"{_coef_text(coef)} * {expr}"


def price60_cheap_formula(price: float, size: float, div: float, cheap: float) -> str:
    return " + ".join(
        (
            _weighted_term(price, "hfq_close.ts_rank(60)"),
            _weighted_term(size, "total_market_cap.cs_rank(0)"),
            _weighted_term(div, "dividend_yield_ratio.cs_rank(0)"),
            _weighted_term(cheap, "(1 - pe_ttm.cs_rank(0))"),
        )
    )


# field, display name, price60, size, dividend, cheap
PRICE60_CHEAP_WEIGHTS: tuple[tuple[str, str, float, float, float, float], ...] = (
    ("sleeve_p60_c025", "价格60加低估值 · 低估值0.25", 1, 1, 1, 0.25),
    ("sleeve_p60_c050", "价格60加低估值 · 低估值0.5", 1, 1, 1, 0.5),
    ("sleeve_p60_c075", "价格60加低估值 · 低估值0.75", 1, 1, 1, 0.75),
    ("sleeve_p60_c125", "价格60加低估值 · 低估值1.25", 1, 1, 1, 1.25),
    ("sleeve_p60_c150", "价格60加低估值 · 低估值1.5", 1, 1, 1, 1.5),
    ("sleeve_p60_c175", "价格60加低估值 · 低估值1.75", 1, 1, 1, 1.75),
    ("sleeve_p60_c200", "价格60加低估值 · 低估值2", 1, 1, 1, 2),
    ("sleeve_p60_c300", "价格60加低估值 · 低估值3", 1, 1, 1, 3),
    ("sleeve_p60_d000", "价格60加低估值 · 股息0", 1, 1, 0, 1),
    ("sleeve_p60_d025", "价格60加低估值 · 股息0.25", 1, 1, 0.25, 1),
    ("sleeve_p60_d050", "价格60加低估值 · 股息0.5", 1, 1, 0.5, 1),
    ("sleeve_p60_d075", "价格60加低估值 · 股息0.75", 1, 1, 0.75, 1),
    ("sleeve_p60_d150", "价格60加低估值 · 股息1.5", 1, 1, 1.5, 1),
    ("sleeve_p60_d200", "价格60加低估值 · 股息2", 1, 1, 2, 1),
    ("sleeve_p60_p050", "价格60加低估值 · 价格0.5", 0.5, 1, 1, 1),
    ("sleeve_p60_p150", "价格60加低估值 · 价格1.5", 1.5, 1, 1, 1),
    ("sleeve_p60_p200", "价格60加低估值 · 价格2", 2, 1, 1, 1),
    ("sleeve_p60_s050", "价格60加低估值 · 规模0.5", 1, 0.5, 1, 1),
    ("sleeve_p60_s150", "价格60加低估值 · 规模1.5", 1, 1.5, 1, 1),
    ("sleeve_p60_s200", "价格60加低估值 · 规模2", 1, 2, 1, 1),
    ("sleeve_p60_d050_c200", "价格60加低估值 · 股息0.5低估值2", 1, 1, 0.5, 2),
    ("sleeve_p60_d200_c050", "价格60加低估值 · 股息2低估值0.5", 1, 1, 2, 0.5),
    ("sleeve_p60_s050_c200", "价格60加低估值 · 规模0.5低估值2", 1, 0.5, 1, 2),
    ("sleeve_p60_p050_c200", "价格60加低估值 · 价格0.5低估值2", 0.5, 1, 1, 2),
    ("sleeve_p60_p200_c050", "价格60加低估值 · 价格2低估值0.5", 2, 1, 1, 0.5),
    ("sleeve_p60_d000_c200", "价格60加低估值 · 股息0低估值2", 1, 1, 0, 2),
)

# 在低估值0.75 上叠加 10 日动量，用来测 2019/2021 风格轮动能不能用动量腿对冲。
C075_MOM_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_mom025", "低估值0.75加动量0.25", 0.25),
    ("sleeve_c075_mom050", "低估值0.75加动量0.5", 0.5),
    ("sleeve_c075_mom100", "低估值0.75加动量1", 1.0),
)

# 规模权重插在 1 与 0.5 之间，其余与低估值0.75 相同。
C075_SIZE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_s075", "低估值0.75 · 规模0.75", 0.75),
)

# 价格权重插在 1 与已测的 1.5 之间，低估值仍是 0.75。p150 那条廉价腿是 1，和 c075 不是同一条。
C075_PRICE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_p125", "低估值0.75 · 价格1.25", 1.25),
)

# 在已过线的股息0.75（廉价腿已是 1）上再加重低估值。
D075_CHEAP_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_d075_c125", "股息0.75 · 低估值1.25", 1.25),
    ("sleeve_d075_c150", "股息0.75 · 低估值1.5", 1.5),
)


def c075_mom_formula(mom: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 0.75)
        + " + "
        + _weighted_term(mom, "momentum_10.cs_rank(0)")
    )


COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(price, size, div, cheap)
        for field, _name, price, size, div, cheap in PRICE60_CHEAP_WEIGHTS
    }
)
COMPOSITE_FORMULAS.update(
    {field: c075_mom_formula(mom) for field, _name, mom in C075_MOM_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, size, 1, 0.75)
        for field, _name, size in C075_SIZE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(price, 1, 1, 0.75)
        for field, _name, price in C075_PRICE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, 1, 0.75, cheap)
        for field, _name, cheap in D075_CHEAP_BLENDS
    }
)
COMPOSITE_FIELDS = tuple(COMPOSITE_FORMULAS)
_COMPOSITE_SOURCE_COLUMNS = (
    "total_mv_cs_rank",
    "div_yield_cs_rank",
    "close_ts_rank_20",
    "close_ts_rank_60",
    "close_zscore_60",
    "close_bias_60",
    "float_mv_cs_rank",
    "momentum_10",
    "vol_mean_20",
    "intraday_range",
    "pe_ttm_cs_rank",
    "turn_cs_rank",
)


def default_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / PACK_SIDECAR_NAME


def default_composite_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / COMPOSITE_SIDECAR_NAME


def _display_output_path(output: Path, base: Path) -> str:
    return posix_relative(output, base)


def _compact_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace("-", "", regex=False).str.replace(".", "", regex=False).str[:8]


def _needed_fields(
    frame: pd.DataFrame, refs: list[Any] | None, allowed: set[str]
) -> list[str]:
    needed: list[str] = []
    columns = set(getattr(frame, "columns", ()))
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("factor_id") or "").strip()
        if field.startswith("factor_"):
            field = field.removeprefix("factor_")
        if field in allowed and field not in columns and field not in needed:
            needed.append(field)
    return needed


def _needed_pack_fields(frame: pd.DataFrame, refs: list[Any] | None) -> list[str]:
    return _needed_fields(frame, refs, set(_PACK_FORMULAS))


def _merge_sidecar(
    frame: pd.DataFrame, needed: list[str], sidecar: Path, missing_message: str
) -> pd.DataFrame:
    if not needed:
        return frame
    if not sidecar.is_file():
        raise ValueError(missing_message)
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


def attach_pack_factor_columns(
    frame: pd.DataFrame,
    refs: list[Any] | None,
    sidecar_path: Path | str,
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return frame
    sidecar = Path(sidecar_path)
    result = _merge_sidecar(
        frame,
        _needed_fields(frame, refs, set(_PACK_FORMULAS)),
        sidecar,
        "可算因子还没有写入旁路文件。请先补全因子计算。",
    )
    composite_needed = _needed_fields(result, refs, set(COMPOSITE_FORMULAS))
    if not composite_needed:
        return result
    return _merge_sidecar(
        result,
        composite_needed,
        sidecar.parent / COMPOSITE_SIDECAR_NAME,
        "截面合成因子还没有写入旁路文件。请先生成 composite_pack_factors.parquet。",
    )


def _cs_rank(values: pd.Series, dates: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.groupby(dates, sort=False).rank(pct=True)


def materialize_composite_pack_factors(pack_sidecar_path: Path | str) -> dict[str, Any]:
    sidecar = Path(pack_sidecar_path).expanduser().resolve()
    if not sidecar.is_file():
        raise ValueError("找不到可算因子旁路文件")
    names = set(pq.ParquetFile(sidecar).schema_arrow.names)
    missing = [field for field in _COMPOSITE_SOURCE_COLUMNS if field not in names]
    if missing:
        raise ValueError(f"旁路因子文件缺少字段 {', '.join(missing)}。")
    code_key = "ts_code" if "ts_code" in names else "instrument"
    date_key = "trade_date" if "trade_date" in names else "date"
    frame = pq.read_table(
        sidecar,
        columns=[code_key, date_key, *_COMPOSITE_SOURCE_COLUMNS],
    ).to_pandas()
    dates = frame[date_key]
    mv = pd.to_numeric(frame["total_mv_cs_rank"], errors="coerce")
    div = pd.to_numeric(frame["div_yield_cs_rank"], errors="coerce")
    price = pd.to_numeric(frame["close_ts_rank_20"], errors="coerce")
    price60 = pd.to_numeric(frame["close_ts_rank_60"], errors="coerce")
    float_mv = pd.to_numeric(frame["float_mv_cs_rank"], errors="coerce")
    cheap = 1.0 - pd.to_numeric(frame["pe_ttm_cs_rank"], errors="coerce")
    quiet = 1.0 - pd.to_numeric(frame["turn_cs_rank"], errors="coerce")
    small = 1.0 - mv
    mom = _cs_rank(frame["momentum_10"], dates)
    vol = _cs_rank(frame["vol_mean_20"], dates)
    rng = _cs_rank(frame["intraday_range"], dates)
    zscore60 = _cs_rank(frame["close_zscore_60"], dates)
    bias60 = _cs_rank(frame["close_bias_60"], dates)
    columns: dict[str, Any] = {
            "ts_code": frame[code_key].astype(str).to_numpy(),
            "trade_date": _compact_date(dates).to_numpy(),
            "sleeve_mv_div": (mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div": (price + mv + div).to_numpy(dtype="float64"),
            "sleeve_mom_mv": (mom + mv).to_numpy(dtype="float64"),
            "sleeve_mom_price": (mom + price).to_numpy(dtype="float64"),
            "sleeve_mom_mv_div": (mom + mv + div).to_numpy(dtype="float64"),
            "sleeve_mom_price_mv_div": (mom + price + mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_vol": (price + mv + div + vol).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_range": (price + mv + div + rng).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div": (price60 + mv + div).to_numpy(dtype="float64"),
            "sleeve_price_float_div": (price + float_mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_cheap": (price + mv + div + cheap).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_quiet": (price + mv + div + quiet).to_numpy(dtype="float64"),
            "sleeve_price_small_div": (price + small + div).to_numpy(dtype="float64"),
            "sleeve_price60_mv": (price60 + mv).to_numpy(dtype="float64"),
            "sleeve_price60_div": (price60 + div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_cheap": (price60 + mv + div + cheap).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_quiet": (price60 + mv + div + quiet).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_cheap_quiet": (price60 + mv + div + cheap + quiet).to_numpy(
                dtype="float64"
            ),
            "sleeve_price60_mv_div0": (price60 + mv + 0.0 * div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div25": (price60 + mv + 0.25 * div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div50": (price60 + mv + 0.5 * div).to_numpy(dtype="float64"),
            "sleeve_zscore60_mv_div": (zscore60 + mv + div).to_numpy(dtype="float64"),
            "sleeve_bias60_mv_div": (bias60 + mv + div).to_numpy(dtype="float64"),
        }
    for field, _name, price_w, size_w, div_w, cheap_w in PRICE60_CHEAP_WEIGHTS:
        columns[field] = (price_w * price60 + size_w * mv + div_w * div + cheap_w * cheap).to_numpy(
            dtype="float64"
        )
    base_c075 = price60 + mv + div + 0.75 * cheap
    for field, _name, mom_w in C075_MOM_BLENDS:
        columns[field] = (base_c075 + mom_w * mom).to_numpy(dtype="float64")
    for field, _name, size_w in C075_SIZE_BLENDS:
        columns[field] = (price60 + size_w * mv + div + 0.75 * cheap).to_numpy(dtype="float64")
    for field, _name, price_w in C075_PRICE_BLENDS:
        columns[field] = (price_w * price60 + mv + div + 0.75 * cheap).to_numpy(dtype="float64")
    for field, _name, cheap_w in D075_CHEAP_BLENDS:
        columns[field] = (price60 + mv + 0.75 * div + cheap_w * cheap).to_numpy(dtype="float64")
    table = pa.table(columns)
    output = sidecar.with_name(COMPOSITE_SIDECAR_NAME)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": _display_output_path(output, sidecar.parent.parent),
        "rows": int(table.num_rows),
        "fields": list(COMPOSITE_FIELDS),
    }


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
        "path": _display_output_path(output, canonical.parent),
        "rows": int(table.num_rows),
        "fields": list(PACK_FIELDS),
    }
