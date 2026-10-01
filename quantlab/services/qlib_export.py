"""Write the canonical market table into Qlib's daily binary layout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

FIELDS = ("open", "high", "low", "close", "volume", "factor")
DAILY_BASIC_FIELDS = (
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
MONEYFLOW_FIELDS = (
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
)
EXTRA_FIELDS = DAILY_BASIC_FIELDS + MONEYFLOW_FIELDS
_PRICE_SOURCE = {
    "open": ("hfq_open", "open"),
    "high": ("hfq_high", "high"),
    "low": ("hfq_low", "low"),
    "close": ("hfq_close", "close"),
}


def to_qlib_symbol(ts_code: object) -> str:
    text = str(ts_code or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        code, exch = text.split(".", 1)
        if exch in {"SH", "SZ", "BJ"} and code:
            return f"{exch}{code}"
    return text.replace(".", "")


def to_iso_date(value: object) -> str:
    text = str(value or "").strip().replace("-", "").replace(".", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def read_feature_bin(path: Path) -> tuple[int, np.ndarray]:
    raw = np.fromfile(path, dtype="<f4")
    if raw.size < 1:
        raise ValueError(f"empty qlib bin: {path}")
    return int(raw[0]), raw[1:].astype("<f8")


def write_feature_bin(path: Path, start_index: int, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = np.empty(len(values) + 1, dtype="<f4")
    payload[0] = float(start_index)
    payload[1:] = np.asarray(values, dtype="<f4")
    payload.tofile(path)


def membership_spans(frame: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Collapse point-in-time membership snapshots into inclusive date spans."""
    if frame.empty:
        return []
    snaps = sorted(set(frame["date"].astype(str)))
    order = {day: index for index, day in enumerate(snaps)}
    spans: list[tuple[str, str, str]] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        indexes = sorted({order[day] for day in group["date"].astype(str)})
        start = prev = indexes[0]
        for index in indexes[1:]:
            if index == prev + 1:
                prev = index
                continue
            spans.append((str(symbol), snaps[start], snaps[prev]))
            start = prev = index
        spans.append((str(symbol), snaps[start], snaps[prev]))
    return spans


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def _prepare_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows = pd.DataFrame(index=frame.index)
    rows["symbol"] = frame["ts_code"].map(to_qlib_symbol) if "ts_code" in frame.columns else ""
    rows["date"] = frame["trade_date"].map(to_iso_date) if "trade_date" in frame.columns else ""
    for field, sources in _PRICE_SOURCE.items():
        rows[field] = _first_column(frame, sources)
    rows["volume"] = _first_column(frame, ("vol", "volume"))
    raw_close = _first_column(frame, ("close",))
    hfq_close = rows["close"]
    ratio = hfq_close / raw_close.where(raw_close != 0)
    if "adj_factor" in frame.columns:
        factor = pd.to_numeric(frame["adj_factor"], errors="coerce")
        rows["factor"] = ratio.where(ratio.notna(), factor)
    else:
        rows["factor"] = ratio.where(ratio.notna(), 1.0)
    if "is_suspended" in frame.columns:
        suspended = pd.to_numeric(frame["is_suspended"], errors="coerce").fillna(0).astype(int) == 1
        for field in ("open", "high", "low", "close", "volume"):
            rows.loc[suspended, field] = np.nan
    rows = rows[(rows["symbol"] != "") & (rows["date"] != "")]
    rows = rows.drop_duplicates(["symbol", "date"], keep="last")
    return rows.sort_values(["symbol", "date"])


def _write_instruments(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{symbol}\t{start}\t{end}\n" for symbol, start, end in rows]
    path.write_text("".join(lines), encoding="utf-8")


def _dated_files(folder: Path, wanted: set[str] | None) -> list[Path]:
    if not folder.is_dir():
        raise ValueError(f"找不到 {folder.name} 原始文件")
    paths: list[Path] = []
    for path in sorted(folder.glob("*.parquet")):
        stamp = path.stem
        if len(stamp) != 8 or not stamp.isdigit():
            continue
        if wanted is not None and stamp not in wanted:
            continue
        paths.append(path)
    if not paths:
        raise ValueError(f"{folder.name} 没有可并入的交易日文件")
    return paths


def _read_interface(folder: Path, fields: tuple[str, ...], wanted: set[str] | None) -> pd.DataFrame:
    """Read dated files even when later days gained columns the early files do not have."""
    import pyarrow.dataset as ds

    paths = _dated_files(folder, wanted)
    groups: dict[tuple[str, ...], list[Path]] = {}
    for path in paths:
        names = set(pq.read_schema(path).names)
        present = tuple(field for field in fields if field in names)
        groups.setdefault(present, []).append(path)
    frames: list[pd.DataFrame] = []
    for present, group in groups.items():
        table = ds.dataset([str(path) for path in group], format="parquet").to_table(
            columns=["ts_code", "trade_date", *present]
        )
        frame = table.to_pandas()
        out = pd.DataFrame(
            {
                "symbol": frame["ts_code"].map(to_qlib_symbol),
                "date": frame["trade_date"].map(to_iso_date),
            }
        )
        for field in fields:
            if field in frame.columns:
                out[field] = pd.to_numeric(frame[field], errors="coerce")
            else:
                out[field] = np.nan
        frames.append(out)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined[(combined["symbol"] != "") & (combined["date"] != "")]
    return combined.drop_duplicates(["symbol", "date"], keep="last")


def load_extra_fields(raw_root: Path, dates: set[str] | None = None) -> pd.DataFrame:
    """Daily basic and moneyflow aligned to Qlib symbols. Missing days stay absent for a later left join."""
    wanted = None if not dates else {str(day).replace("-", "")[:8] for day in dates}
    basic = _read_interface(Path(raw_root) / "daily_basic", DAILY_BASIC_FIELDS, wanted)
    flow = _read_interface(Path(raw_root) / "moneyflow", MONEYFLOW_FIELDS, wanted)
    merged = basic.merge(flow, on=["symbol", "date"], how="outer")
    for field in EXTRA_FIELDS:
        if field not in merged.columns:
            merged[field] = np.nan
    return merged[["symbol", "date", *EXTRA_FIELDS]]


def convert_frame(
    frame: pd.DataFrame,
    qlib_dir: Path,
    *,
    membership: pd.DataFrame | None = None,
    raw_root: Path | None = None,
) -> dict[str, object]:
    qlib_dir = Path(qlib_dir)
    prepared = _prepare_rows(frame)
    if prepared.empty:
        raise ValueError("没有可转换的行情行")
    panel_fields = list(FIELDS)
    if raw_root is not None:
        extras = load_extra_fields(raw_root, dates=set(prepared["date"].astype(str)))
        prepared = prepared.merge(extras, on=["symbol", "date"], how="left")
        panel_fields = list(FIELDS) + list(EXTRA_FIELDS)
    calendar = sorted(prepared["date"].unique())
    index = {day: pos for pos, day in enumerate(calendar)}
    features = qlib_dir / "features"
    instruments: list[tuple[str, str, str]] = []
    panel_parts: list[pd.DataFrame] = []
    for symbol, group in prepared.groupby("symbol", sort=True):
        group = group.sort_values("date")
        start = index[group["date"].iloc[0]]
        end = index[group["date"].iloc[-1]]
        width = end - start + 1
        placed = {day: row for day, row in group.set_index("date").iterrows()}
        columns: dict[str, np.ndarray] = {}
        for field in panel_fields:
            values = np.full(width, np.nan, dtype="<f8")
            for offset, day in enumerate(calendar[start : end + 1]):
                row = placed.get(day)
                if row is not None and pd.notna(row[field]):
                    values[offset] = row[field]
            columns[field] = values
            if field in FIELDS:
                write_feature_bin(features / symbol.lower() / f"{field}.day.bin", start, values)
        instruments.append((str(symbol), calendar[start], calendar[end]))
        piece = pd.DataFrame({"date": calendar[start : end + 1], "symbol": symbol, **columns})
        panel_parts.append(piece)
    (qlib_dir / "calendars").mkdir(parents=True, exist_ok=True)
    (qlib_dir / "calendars" / "day.txt").write_text("\n".join(calendar) + "\n", encoding="utf-8")
    _write_instruments(qlib_dir / "instruments" / "all.txt", instruments)
    universe = "all"
    if membership is not None and not membership.empty:
        spans = membership_spans(membership)
        _write_instruments(qlib_dir / "instruments" / "csi300.txt", spans)
        universe = "csi300"
    panel = pd.concat(panel_parts, ignore_index=True)
    panel_path = qlib_dir.parent / "panel.parquet"
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(panel_path, index=False)
    summary = {
        "rows": int(len(prepared)),
        "symbols": int(prepared["symbol"].nunique()),
        "date_min": calendar[0],
        "date_max": calendar[-1],
        "universe": universe,
        "qlib_dir": str(qlib_dir),
        "panel_path": str(panel_path),
        "fields": panel_fields,
    }
    manifest = qlib_dir.parent / "manifest.json"
    manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_source_frame(path: Path) -> pd.DataFrame:
    file = pq.ParquetFile(path)
    names = set(file.schema_arrow.names)
    needed = [
        name
        for name in (
            "ts_code",
            "trade_date",
            "hfq_open",
            "hfq_high",
            "hfq_low",
            "hfq_close",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "volume",
            "adj_factor",
            "is_suspended",
        )
        if name in names
    ]
    if "ts_code" not in needed or "trade_date" not in needed:
        raise ValueError("canonical.parquet 缺少 ts_code 或 trade_date")
    return pq.read_table(path, columns=needed).to_pandas()
