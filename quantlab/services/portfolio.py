"""Load the last compiled module while the .py source is missing."""

from __future__ import annotations

from importlib.machinery import SourcelessFileLoader
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "portfolio.pyc"
_code = SourcelessFileLoader(__name__, str(_pyc)).get_code(__name__)
if _code is None:
    raise ImportError(f"无法从字节码恢复：{_pyc}")
exec(_code, globals())

from quantlab.services.trade_filters import eval_open_filters as eval_open_filters

_DAY_INDEX_ATTR = "_quantlab_day_index"


class _DayIndex:
    __slots__ = ("columns", "values", "positions")

    def __init__(
        self,
        columns: list[str],
        values: dict[str, Any],
        positions: dict[str, dict[str, int]],
    ) -> None:
        self.columns = columns
        self.values = values
        self.positions = positions

    def row_dict(self, pos: int) -> dict[str, Any]:
        return {name: _cell(self.values[name][pos]) for name in self.columns}


class _RowView:
    __slots__ = ("_index", "_pos")

    def __init__(self, index: _DayIndex, pos: int) -> None:
        self._index = index
        self._pos = pos

    @property
    def index(self):
        return self._index.columns

    def to_dict(self) -> dict[str, Any]:
        return self._index.row_dict(self._pos)

    def __getitem__(self, key):
        values = self._index.values
        if key not in values:
            raise KeyError(key)
        return _cell(values[key][self._pos])

    def get(self, key, default=None):
        values = self._index.values
        if key not in values:
            return default
        return _cell(values[key][self._pos])

    def __contains__(self, key) -> bool:
        return key in self._index.values


class _DayRows:
    __slots__ = ("_index", "_bucket")

    def __init__(self, index: _DayIndex, date: str) -> None:
        key = str(date)
        bucket = index.positions.get(key)
        if bucket is None:
            bucket = index.positions.get(_norm_date(date), {})
        self._index = index
        self._bucket = bucket

    def get(self, key, default=None):
        pos = self._bucket.get(str(key))
        if pos is None:
            return default
        return _RowView(self._index, pos)

    def __getitem__(self, key):
        row = self.get(key)
        if row is None:
            raise KeyError(key)
        return row

    def __contains__(self, key) -> bool:
        return str(key) in self._bucket

    def __iter__(self):
        return iter(self._bucket)

    def __len__(self) -> int:
        return len(self._bucket)


def _cell(value: Any) -> Any:
    typ = type(value)
    if typ is float:
        return None if value != value else value
    if typ is int or typ is str or typ is bool:
        return value
    if value is None:
        return None
    if isinstance(value, np.generic):
        if value != value:
            return None
        return value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def build_day_index(frame: pd.DataFrame) -> _DayIndex:
    if frame is None or getattr(frame, "empty", True):
        return _DayIndex([], {}, {})
    if "date" not in frame.columns or "instrument" not in frame.columns:
        return _DayIndex([], {}, {})
    columns = list(frame.columns)
    values = {name: frame[name].to_numpy() for name in columns}
    if "suspended" not in values and "is_suspended" in values:
        values["suspended"] = values["is_suspended"]
        columns = [*columns, "suspended"]
    raw_dates = values["date"]
    instruments = values["instrument"]
    positions: dict[str, dict[str, int]] = {}
    for pos, (raw_date, instrument) in enumerate(zip(raw_dates, instruments, strict=False)):
        date_key = _norm_date(raw_date)
        if not date_key:
            continue
        bucket = positions.get(date_key)
        if bucket is None:
            bucket = {}
            positions[date_key] = bucket
        bucket[str(instrument)] = pos
    return _DayIndex(columns, values, positions)


def index_day_rows(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build date -> instrument -> row maps with one pass instead of loc per day."""
    indexed: dict[str, dict[str, Any]] = {}
    index = build_day_index(frame)
    for date_key, bucket in index.positions.items():
        indexed[date_key] = {instrument: index.row_dict(pos) for instrument, pos in bucket.items()}
    return indexed


def _cached_day_index(frame: pd.DataFrame) -> _DayIndex:
    cached = frame.attrs.get(_DAY_INDEX_ATTR)
    if cached is None:
        cached = build_day_index(frame)
        frame.attrs[_DAY_INDEX_ATTR] = cached
    return cached


def _day_rows(frame: pd.DataFrame, date: str) -> _DayRows:
    return _DayRows(_cached_day_index(frame), str(date))
