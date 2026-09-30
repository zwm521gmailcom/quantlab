"""Index membership as-of lookup and stock board filtering."""

from __future__ import annotations

import bisect
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


def index_weight_path(raw_root: Path, index_code: str) -> Path:
    slug = str(index_code).strip().upper().replace(".", "_")
    return Path(raw_root) / "index_weight" / f"index_weight_{slug}.parquet"


def load_index_weight(
    raw_root: Path,
    index_codes: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    raw_root = Path(raw_root)
    if index_codes is None:
        index_dir = raw_root / "index_weight"
        if index_dir.is_dir():
            pattern = index_dir / "index_weight_*.parquet"
        else:
            pattern = raw_root / "index_weight_*.parquet"
        paths = sorted(pattern.parent.glob(pattern.name))
    else:
        paths = []
        seen: set[str] = set()
        for raw in index_codes:
            code = str(raw or "").strip().upper().replace("-", ".")
            if not code or code in seen:
                continue
            seen.add(code)
            path = index_weight_path(raw_root, code)
            if path.is_file():
                paths.append(path)
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


def _member_snapshots(weights: pd.DataFrame, index_code: str) -> tuple[list[str], list[set[str]]]:
    if weights is None or getattr(weights, "empty", True):
        return [], []
    subset = weights.loc[weights["index_code"].astype(str) == str(index_code)]
    if subset.empty:
        return [], []
    grouped: dict[str, set[str]] = {}
    dates = subset["trade_date"].map(_normalize_trade_date)
    codes = subset["con_code"].astype(str).str.strip()
    for date, code in zip(dates.tolist(), codes.tolist(), strict=False):
        if not date or not code or code.lower() == "nan":
            continue
        grouped.setdefault(date, set()).add(code)
    ordered = sorted(grouped)
    return ordered, [grouped[day] for day in ordered]


def _members_asof(ordered: list[str], snaps: list[set[str]], trade_date: str) -> set[str]:
    query_date = _normalize_trade_date(trade_date)
    if not ordered or not query_date:
        return set()
    index = bisect.bisect_right(ordered, query_date) - 1
    if index < 0:
        return set()
    return set(snaps[index])


def members_on(weights: pd.DataFrame, index_code: str, trade_date: str) -> set[str]:
    ordered, snaps = _member_snapshots(weights, index_code)
    return _members_asof(ordered, snaps, trade_date)


PIT_INDEX_CODES = ("000300.SH", "000905.SH")
DEFAULT_UNIVERSE_INDEX_CODES = PIT_INDEX_CODES

UNIVERSE_PRESETS: dict[str, tuple[str, ...]] = {
    "csi800": ("000300.SH", "000905.SH"),
    "csi1000": ("000852.SH",),
    "csi2000": ("932000.CSI",),
    "csi800_single": ("000906.SH",),
    "csi_all": ("000985.CSI",),
    "cni2000": ("399303.SZ",),
}


def parse_universe_index_codes(value: object | None) -> tuple[str, ...]:
    """Normalize universe_index_codes. None/blank → CSI800; [] → unconstrained 全 A."""
    if value is None:
        return DEFAULT_UNIVERSE_INDEX_CODES
    raw: list[str]
    explicit_empty = False
    if isinstance(value, str):
        raw = [part.strip().upper().replace("-", ".") for part in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        raw = [str(item).strip().upper().replace("-", ".") for item in value]
        explicit_empty = len(value) == 0
    else:
        raise ValueError("universe_index_codes 要填指数代码列表")
    codes: list[str] = []
    seen: set[str] = set()
    for part in raw:
        if not part or part in seen:
            continue
        seen.add(part)
        codes.append(part)
    if codes:
        return tuple(codes)
    if explicit_empty:
        return ()
    return DEFAULT_UNIVERSE_INDEX_CODES


def membership_coverage_error(
    raw_root: Path,
    index_codes: tuple[str, ...] | list[str] | None = None,
    *,
    date_from: str | None = None,
) -> str | None:
    codes = parse_universe_index_codes(index_codes)
    if not codes:
        return None
    missing = [code for code in codes if not index_weight_path(raw_root, code).is_file()]
    if missing:
        return f"缺少指数成分权重：{', '.join(missing)}。请先在数据中心下载 index_weight。"
    weights = load_index_weight(Path(raw_root), codes)
    if weights.empty:
        return "指数成分权重文件是空的。"
    present = set(weights["index_code"].astype(str))
    absent = [code for code in codes if code not in present]
    if absent:
        return f"成分权重里没有这些指数：{', '.join(absent)}。"
    if date_from:
        for code in codes:
            if not members_on(weights, code, date_from):
                return f"{code} 在回测起点 {date_from} 之前没有可用成分（as-of）。"
        return None
    for code in codes:
        if not latest_members(weights, code):
            return f"{code} 没有可用的最新成分。"
    return None


def filter_index_universe_asof(
    frame: pd.DataFrame,
    weights: pd.DataFrame,
    index_codes: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return frame
    if weights is None or getattr(weights, "empty", True):
        return frame
    codes = parse_universe_index_codes(index_codes)
    if not codes:
        return frame
    present = set(weights["index_code"].astype(str))
    if present.isdisjoint(codes):
        return frame
    date_col = next((name for name in ("date", "trade_date") if name in frame.columns), None)
    code_col = next((name for name in ("instrument", "ts_code") if name in frame.columns), None)
    if date_col is None or code_col is None:
        return frame
    union: dict[str, set[str]] = {}
    for index_code in codes:
        ordered, snaps = _member_snapshots(weights, index_code)
        for day, members in zip(ordered, snaps, strict=False):
            if not day:
                continue
            bucket = union.get(day)
            if bucket is None:
                union[day] = set(members)
            else:
                bucket.update(members)
    snap_dates = sorted(union)
    if not snap_dates:
        return frame.iloc[0:0].copy()

    day_keys = frame[date_col].map(_normalize_trade_date)
    code_keys = frame[code_col].astype(str).str.strip()
    unique_days = [day for day in dict.fromkeys(day_keys.tolist()) if day]
    day_to_snap: dict[str, str] = {}
    for day in unique_days:
        index = bisect.bisect_right(snap_dates, day) - 1
        if index >= 0:
            day_to_snap[day] = snap_dates[index]
    asof = day_keys.map(day_to_snap)
    members = pd.DataFrame(
        ((day, name) for day, names in union.items() for name in names),
        columns=["_snap", "_code"],
    )
    if members.empty:
        return frame.iloc[0:0].copy()
    members = members.drop_duplicates()
    keyed = pd.DataFrame(
        {"_row": frame.index, "_snap": asof.to_numpy(), "_code": code_keys.to_numpy()}
    )
    matched = keyed.merge(members, on=["_snap", "_code"], how="inner")
    return frame.loc[matched["_row"].to_numpy()].copy()


def apply_pit_index_universe(
    frame: pd.DataFrame,
    raw_root: Path | None,
    index_codes: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    if raw_root is None:
        return frame
    codes = parse_universe_index_codes(index_codes)
    if not codes:
        return frame
    return filter_index_universe_asof(
        frame,
        load_index_weight(Path(raw_root), codes),
        codes,
    )


def latest_members(weights: pd.DataFrame, index_code: str) -> set[str]:
    ordered, snaps = _member_snapshots(weights, index_code)
    if not ordered:
        return set()
    return set(snaps[-1])


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
