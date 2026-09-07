"""Index membership as-of lookup and stock board filtering."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLUMNS = ["index_code", "con_code", "trade_date", "weight"]

_MAIN_SME_CHINEXT_PREFIXES = frozenset(
    ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")
)


def _empty_weights() -> pd.DataFrame:
    return pd.DataFrame(columns=_COLUMNS)


def _normalize_trade_date(value: str) -> str:
    text = str(value).strip()
    if "-" in text:
        return text.replace("-", "")[:8]
    return text[:8]


def load_index_weight(raw_root: Path) -> pd.DataFrame:
    index_dir = raw_root / "index_weight"
    if index_dir.is_dir():
        pattern = index_dir / "index_weight_*.parquet"
    else:
        pattern = raw_root / "index_weight_*.parquet"

    paths = sorted(pattern.parent.glob(pattern.name))
    if not paths:
        return _empty_weights()

    frames = [pd.read_parquet(path) for path in paths]
    if not frames:
        return _empty_weights()

    result = pd.concat(frames, ignore_index=True)
    for column in _COLUMNS:
        if column not in result.columns:
            result[column] = pd.Series(dtype="object")
    return result[_COLUMNS]


def members_on(weights: pd.DataFrame, index_code: str, trade_date: str) -> set[str]:
    if weights.empty:
        return set()

    query_date = _normalize_trade_date(trade_date)
    subset = weights.loc[weights["index_code"] == index_code].copy()
    if subset.empty:
        return set()

    subset["trade_date"] = subset["trade_date"].map(_normalize_trade_date)
    eligible = subset.loc[subset["trade_date"] <= query_date]
    if eligible.empty:
        return set()

    as_of_date = eligible["trade_date"].max()
    members = eligible.loc[eligible["trade_date"] == as_of_date, "con_code"].astype(str).str.strip()
    return {code for code in members if code and code.lower() != "nan"}


PIT_INDEX_CODES = ("000300.SH", "000905.SH")


def filter_index_universe_asof(
    frame: pd.DataFrame,
    weights: pd.DataFrame,
    index_codes: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return frame
    if weights is None or getattr(weights, "empty", True):
        return frame
    codes = tuple(str(item).strip() for item in (index_codes or PIT_INDEX_CODES) if str(item).strip())
    if not codes:
        return frame
    present = set(weights["index_code"].astype(str))
    if present.isdisjoint(codes):
        return frame
    date_col = next((name for name in ("date", "trade_date") if name in frame.columns), None)
    code_col = next((name for name in ("instrument", "ts_code") if name in frame.columns), None)
    if date_col is None or code_col is None:
        return frame
    day_keys = frame[date_col].map(_normalize_trade_date)
    cache: dict[str, set[str]] = {}
    mask = pd.Series(False, index=frame.index)
    for day in day_keys.unique():
        if not day:
            continue
        members = cache.get(day)
        if members is None:
            members = set()
            for index_code in codes:
                members |= members_on(weights, index_code, day)
            cache[day] = members
        if not members:
            continue
        mask |= day_keys.eq(day) & frame[code_col].astype(str).isin(members)
    return frame.loc[mask].copy()


def apply_pit_index_universe(frame: pd.DataFrame, raw_root: Path | None) -> pd.DataFrame:
    if raw_root is None:
        return frame
    return filter_index_universe_asof(frame, load_index_weight(Path(raw_root)), PIT_INDEX_CODES)


def latest_members(weights: pd.DataFrame, index_code: str) -> set[str]:
    if weights.empty:
        return set()

    subset = weights.loc[weights["index_code"] == index_code].copy()
    if subset.empty:
        return set()

    subset["trade_date"] = subset["trade_date"].map(_normalize_trade_date)
    as_of_date = subset["trade_date"].max()
    members = subset.loc[subset["trade_date"] == as_of_date, "con_code"].astype(str).str.strip()
    return {code for code in members if code and code.lower() != "nan"}


def amount_unit(series: pd.Series) -> str:
    values = pd.to_numeric(series, errors="coerce")
    median = values.median()
    if pd.isna(median) or median >= 1e7:
        return "yuan"
    return "thousand_yuan"


def amount_yuan(series: pd.Series, unit: str | None = None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    resolved = unit or amount_unit(values)
    if resolved == "thousand_yuan":
        result = values * 1000
        result.attrs["unit"] = "thousand_yuan"
        result.attrs["median"] = float(values.median()) if len(values) else None
        return result

    result = values.copy()
    result.attrs["unit"] = "yuan"
    result.attrs["median"] = float(values.median()) if len(values) else None
    return result


def _normalize_date_series(series: pd.Series) -> pd.Series:
    text = series.astype("string").fillna("").str.replace("-", "", regex=False).str.strip()
    return text.str.replace(r"\D", "", regex=True).str[:8]


def _truthy_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0).ne(0)
    text = series.astype("string").str.strip().str.lower().fillna("")
    return text.isin({"1", "true", "t", "yes", "y"})


def filter_listed_universe(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop names that were not listed, not eligible, or already delisted that day."""
    if frame is None or frame.empty:
        return frame

    mask = pd.Series(True, index=frame.index)
    applied = False
    if "eligible" in frame.columns:
        mask &= _truthy_mask(frame["eligible"])
        applied = True

    date_col = next((name for name in ("date", "trade_date") if name in frame.columns), None)
    if date_col is not None:
        day = _normalize_date_series(frame[date_col])
        if "list_date" in frame.columns:
            listed = _normalize_date_series(frame["list_date"])
            mask &= listed.eq("") | ((listed.str.len() == 8) & (listed <= day))
            applied = True
        if "delist_date" in frame.columns:
            gone = _normalize_date_series(frame["delist_date"])
            mask &= gone.eq("") | ((gone.str.len() == 8) & (day < gone))
            applied = True

    if not applied:
        raise ValueError("行情缺少上市状态列（eligible / list_date / delist_date），无法校验退市。")
    if bool(mask.all()):
        return frame
    return frame.loc[mask].copy()


def is_main_sme_chinext(ts_code: str) -> bool:
    code = str(ts_code).strip().upper()
    if code.endswith(".BJ"):
        return False

    symbol, exchange = code.split(".", 1) if "." in code else (code, "")
    if exchange == "SH" and symbol.startswith("688"):
        return False

    return symbol[:3] in _MAIN_SME_CHINEXT_PREFIXES
