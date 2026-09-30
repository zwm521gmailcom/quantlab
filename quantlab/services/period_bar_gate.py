"""As-of weekly or monthly adjusted close for a stock gate."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def period_close_table(raw_root: Path | str, freq: str) -> pd.DataFrame:
    kind = str(freq or "").strip()
    if kind not in {"week", "month"}:
        raise ValueError("freq 必须是 week 或 month")
    root = Path(raw_root)
    key = (str(root.resolve()), kind)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    folder = root / "stk_week_month_adj"
    files = sorted(folder.glob(f"{kind}_*.parquet"))
    if not files:
        raise FileNotFoundError(f"没有{kind}线复权文件：{folder}")
    frames = [
        pd.read_parquet(path, columns=["ts_code", "trade_date", "close_hfq"])
        for path in files
    ]
    table = pd.concat(frames, ignore_index=True)
    table["ts_code"] = table["ts_code"].astype(str)
    table["trade_date"] = (
        table["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    )
    table["close_hfq"] = pd.to_numeric(table["close_hfq"], errors="coerce")
    table = table.dropna(subset=["ts_code", "trade_date", "close_hfq"])
    table = table.sort_values(["ts_code", "trade_date"], kind="mergesort")
    table = table.drop_duplicates(["ts_code", "trade_date"], keep="last")
    table["trade_date_num"] = pd.to_numeric(table["trade_date"], errors="coerce").astype("int64")
    kept = table[["ts_code", "trade_date_num", "close_hfq"]].reset_index(drop=True)
    _CACHE[key] = kept
    return kept


def attach_period_bar_gate(frame: pd.DataFrame, raw_root: Path | str, gate: str | None) -> pd.DataFrame:
    kind = str(gate or "").strip()
    if kind not in {"week", "month"} or frame is None or getattr(frame, "empty", True):
        return frame
    code = "instrument" if "instrument" in frame.columns else "ts_code"
    date = "date" if "date" in frame.columns else "trade_date"
    if code not in frame.columns or date not in frame.columns:
        return frame
    column = f"{kind}_hfq_close"
    table = period_close_table(raw_root, kind).rename(
        columns={"ts_code": "_code", "trade_date_num": "_date_num", "close_hfq": column}
    )
    left = frame.copy()
    left["_ord"] = range(len(left))
    left["_code"] = left[code].astype(str)
    left["_date_num"] = pd.to_numeric(
        left[date].astype(str).str.replace("-", "", regex=False).str[:8],
        errors="coerce",
    )
    left_sorted = left.sort_values("_date_num", kind="mergesort")
    right = table.sort_values("_date_num", kind="mergesort")
    merged = pd.merge_asof(
        left_sorted,
        right,
        on="_date_num",
        by="_code",
        direction="backward",
    ).sort_values("_ord", kind="mergesort")
    out = frame.copy()
    out[column] = pd.to_numeric(merged[column], errors="coerce").to_numpy()
    return out
