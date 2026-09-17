"""Target-weight portfolio matching for workbench backtests."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from quantlab.services.trade_filters import eval_open_filters as eval_open_filters
from quantlab.services.trade_filters import open_expressions, split_open_filters

_DAY_INDEX_ATTR = "_quantlab_day_index"


def _norm_date(value: Any) -> str:
    return str(value or "").replace("-", "")[:8]


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


def _finite_price(row: Any, column: str) -> float | None:
    if column not in getattr(row, "index", row):
        try:
            value = row[column]
        except (KeyError, TypeError, IndexError):
            return None
    else:
        try:
            value = row[column]
        except Exception:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _at_limit(price: float | None, limit: float | None, side: str) -> bool:
    if price is None or limit is None:
        return False
    if side == "up":
        return price >= limit
    return price <= limit


def _mark(
    cash: float,
    positions: dict[str, int],
    rows: dict[str, Any],
    column: str,
) -> tuple[float, float]:
    invested = 0.0
    for instrument, quantity in positions.items():
        if quantity <= 0 or instrument not in rows:
            continue
        price = _finite_price(rows[instrument], column)
        if price is None:
            continue
        invested += quantity * price
    return cash + invested, invested


def _offset_date(market: list[str], date: str, n: int) -> str | None:
    try:
        index = market.index(date)
    except ValueError:
        return None
    dest = index + n
    if dest < 0 or dest >= len(market):
        return None
    return market[dest]


def _held_count(positions: dict[str, int]) -> int:
    return sum(1 for quantity in positions.values() if quantity > 0)


def _fill_orders(
    candidates: pd.DataFrame,
    held: set[str],
    empty: int,
    top_n: int,
    weighting: str,
) -> dict[str, float]:
    if empty <= 0 or candidates.empty:
        return {}
    ranked = candidates.sort_values(["score", "instrument"], ascending=[False, True])
    picked: list[str] = []
    scores: dict[str, float] = {}
    for instrument, score in zip(
        ranked["instrument"].astype(str),
        pd.to_numeric(ranked["score"], errors="coerce"),
        strict=False,
    ):
        if instrument in held or instrument in scores:
            continue
        picked.append(instrument)
        scores[instrument] = max(float(score or 0), 0.0)
        if len(picked) >= empty:
            break
    if not picked:
        return {}
    slots = max(int(top_n), 1)
    if weighting == "score":
        total = sum(scores.values())
        budget = len(picked) / slots
        if total > 0:
            return {instrument: budget * scores[instrument] / total for instrument in picked}
    return {instrument: 1.0 / slots for instrument in picked}


def _fit_buy(
    cash: float,
    price: float,
    lot: int,
    fee_rate: float,
    fee_min: float,
    quantity: int,
) -> tuple[int, float, float]:
    qty = min(quantity, math.floor(cash / price / lot) * lot) if price > 0 else 0
    qty = max(qty, 0)
    while qty > 0:
        amount = qty * price
        fee = max(amount * fee_rate, fee_min) if amount else 0.0
        if amount + fee <= cash + 1e-09:
            return qty, amount, fee
        qty -= lot
    return (0, 0.0, 0.0)


def _close_lots(
    lots: list[dict[str, Any]],
    instrument: str,
    quantity: int,
    sell_date: str,
    sell_price: float,
    sell_fee: float,
    stamp_tax: float,
) -> list[dict[str, Any]]:
    remaining = quantity
    fee_left = sell_fee
    tax_left = stamp_tax
    trades: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for lot in lots:
        if lot["instrument"] != instrument or remaining <= 0:
            kept.append(lot)
            continue
        take = min(int(lot["quantity"]), remaining)
        if take <= 0:
            kept.append(lot)
            continue
        share = take / quantity if quantity else 0.0
        buy_amount = float(lot["buy_amount"]) * take / lot["quantity"]
        buy_fee = float(lot["buy_fee"]) * take / lot["quantity"]
        sell_amount = take * sell_price
        lot_fee = fee_left * share if remaining == take else min(fee_left, sell_fee * share)
        lot_tax = tax_left * share if remaining == take else min(tax_left, stamp_tax * share)
        fee_left -= lot_fee
        tax_left -= lot_tax
        trades.append(
            {
                "signal_date": lot["signal_date"],
                "instrument": instrument,
                "buy_date": lot["buy_date"],
                "sell_date": sell_date,
                "status": "filled",
                "quantity": take,
                "buy_price": lot["buy_price"],
                "sell_price": sell_price,
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "buy_fee": buy_fee,
                "sell_fee": lot_fee,
                "stamp_tax": lot_tax,
                "pnl": sell_amount - buy_amount - buy_fee - lot_fee - lot_tax,
            }
        )
        remaining -= take
        leftover = int(lot["quantity"]) - take
        if leftover <= 0:
            continue
        scale = leftover / lot["quantity"]
        kept.append(
            {
                **lot,
                "quantity": leftover,
                "buy_amount": float(lot["buy_amount"]) * scale,
                "buy_fee": float(lot["buy_fee"]) * scale,
            }
        )
    lots[:] = kept
    return trades


def run_portfolio(
    frame: pd.DataFrame,
    predictions: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    date_from = _norm_date(test.get("date_from"))
    date_to = _norm_date(test.get("date_to"))
    market = sorted({_norm_date(value) for value in frame["date"].tolist() if _norm_date(value)})
    test_dates = [date for date in market if date_from <= date <= date_to]
    if not test_dates:
        return [], []
    rebalance_every = max(int(config.get("rebalance_every") or 1), 1)
    holding_days = max(int(config.get("holding_days") or 2), 1)
    signal_dates = set(test_dates[::rebalance_every])
    top_n = max(int(config.get("top_n") or 1), 1)
    lot_size = max(int(config.get("lot_size") or 100), 1)
    slippage = float(config.get("slippage") or 0)
    buy_fee_rate = float(config.get("buy_fee_rate") or 0)
    buy_fee_min = float(config.get("buy_fee_minimum") or 0)
    sell_fee_rate = float(config.get("sell_fee_rate") or 0)
    sell_fee_min = float(config.get("sell_fee_minimum") or 0)
    stamp_tax_rate = float(config.get("stamp_tax_rate") or 0)
    skip_close_down_limit = bool(config.get("skip_close_down_limit", True))
    signal_exprs, fill_exprs = split_open_filters(open_expressions(config))
    membership_gate = bool(config.get("open_gate_by_membership"))
    open_gate = (not membership_gate) and (
        bool(config.get("open_when_benchmark_gt_ma200")) or bool(config.get("open_ma_gates"))
    )
    allowed_opens = None
    if open_gate:
        allowed_opens = {
            str(day).replace("-", "")[:8]
            for day in (config.get("benchmark_open_dates") or [])
            if str(day).replace("-", "")[:8]
        }
    membership_allow = None
    if membership_gate:
        raw_map = config.get("membership_open_allow")
        membership_allow = {}
        if isinstance(raw_map, dict):
            membership_allow = {
                str(day).replace("-", "")[:8]: {str(name) for name in (names or [])}
                for day, names in raw_map.items()
            }
    unfilled_policy = str(config.get("unfilled_policy") or "keep_cash")
    weighting = str(config.get("weighting") or "equal")
    buy_col = str(config.get("buy_price") or "open")
    if buy_col not in {"hfq_open", "open"}:
        buy_col = "open"
    sell_col = str(config.get("sell_price") or "close")
    if sell_col not in {"hfq_close", "close"}:
        sell_col = "close"
    cash = float(config.get("initial_capital") or 0)
    positions: dict[str, int] = {}
    lots: list[dict[str, Any]] = []
    pending = None
    pending_signal = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    scored = predictions.copy()
    if not scored.empty:
        scored["date"] = scored["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    last_test = test_dates[-1]
    loop_dates = [date for date in market if test_dates[0] <= date <= last_test]

    def record_unfilled(signal_date: str, instrument: str, buy_date: str, sell_date: str) -> None:
        if unfilled_policy == "cancel" or unfilled_policy == "next_available":
            return
        trades.append(
            {
                "signal_date": signal_date,
                "instrument": instrument,
                "buy_date": buy_date,
                "sell_date": sell_date,
                "status": "unfilled",
                "reason": "suspended_or_limit",
            }
        )

    def sell_instrument(
        instrument: str,
        quantity: int,
        sell_date: str,
        row,
        reason_date: str,
    ) -> None:
        nonlocal cash
        if quantity <= 0:
            return
        if bool(row.get("suspended", False)):
            record_unfilled(reason_date, instrument, reason_date, sell_date)
            return
        close_px = _finite_price(row, "close")
        fill_px = _finite_price(row, sell_col)
        if close_px is None or fill_px is None:
            record_unfilled(reason_date, instrument, reason_date, sell_date)
            return
        if skip_close_down_limit and _at_limit(close_px, _finite_price(row, "down_limit"), "down"):
            record_unfilled(reason_date, instrument, reason_date, sell_date)
            return
        sell_price = fill_px * (1 - slippage)
        sell_amount = quantity * sell_price
        sell_fee = max(sell_amount * sell_fee_rate, sell_fee_min) if sell_amount else 0.0
        stamp_tax = sell_amount * stamp_tax_rate
        cash += sell_amount - sell_fee - stamp_tax
        positions[instrument] = positions.get(instrument, 0) - quantity
        if positions.get(instrument, 0) <= 0:
            positions.pop(instrument, None)
        trades.extend(
            _close_lots(lots, instrument, quantity, sell_date, sell_price, sell_fee, stamp_tax)
        )

    def apply_pending(day: str, rows) -> None:
        nonlocal cash, pending, pending_signal
        if not pending:
            pending = None
            pending_signal = None
            return
        equity_open, _ = _mark(cash, positions, rows, buy_col)
        signal_date = pending_signal or day
        for instrument, weight in pending.items():
            if _held_count(positions) >= top_n:
                break
            if int(positions.get(instrument, 0)) > 0:
                continue
            row = rows.get(instrument)
            if row is None:
                record_unfilled(signal_date, instrument, day, day)
                continue
            if bool(row.get("suspended", False)):
                record_unfilled(signal_date, instrument, day, day)
                continue
            open_px = _finite_price(row, "open")
            fill_px = _finite_price(row, buy_col)
            if open_px is None or fill_px is None:
                record_unfilled(signal_date, instrument, day, day)
                continue
            if fill_exprs and not eval_open_filters(fill_exprs, row.to_dict()):
                record_unfilled(signal_date, instrument, day, day)
                continue
            buy_price = fill_px * (1 + slippage)
            target_qty = math.floor(equity_open * float(weight) / buy_price / lot_size) * lot_size if buy_price > 0 else 0
            qty, amount, fee = _fit_buy(
                cash, buy_price, lot_size, buy_fee_rate, buy_fee_min, max(int(target_qty), 0)
            )
            if qty <= 0:
                continue
            cash -= amount + fee
            positions[instrument] = int(positions.get(instrument, 0)) + qty
            lots.append(
                {
                    "instrument": instrument,
                    "quantity": qty,
                    "buy_date": day,
                    "buy_price": buy_price,
                    "buy_amount": amount,
                    "buy_fee": fee,
                    "signal_date": signal_date,
                }
            )
        pending = None
        pending_signal = None

    for day in loop_dates:
        rows = _day_rows(frame, day)
        if pending is not None:
            apply_pending(day, rows)
        if day != last_test:
            due: dict[str, int] = {}
            for lot in lots:
                sell_on = _offset_date(market, str(lot["buy_date"]), holding_days)
                if sell_on is None or day < sell_on:
                    continue
                instrument = str(lot["instrument"])
                due[instrument] = due.get(instrument, 0) + int(lot["quantity"])
            for instrument, quantity in due.items():
                row = rows.get(instrument)
                if row is None:
                    record_unfilled(day, instrument, day, day)
                    continue
                sell_instrument(instrument, quantity, day, row, reason_date=day)
        equity, invested = _mark(cash, positions, rows, sell_col)
        if day in signal_dates and day != last_test:
            if allowed_opens is not None and day not in allowed_opens:
                pending = {}
                pending_signal = day
            else:
                day_preds = scored if scored.empty else scored.loc[scored["date"] == day]
                if membership_allow is not None:
                    allowed_names = membership_allow.get(day) or set()
                    if day_preds.empty:
                        pass
                    else:
                        day_preds = day_preds.loc[day_preds["instrument"].astype(str).isin(allowed_names)]
                if signal_exprs and not day_preds.empty:
                    allowed: set[str] = set()
                    for instrument in day_preds["instrument"].astype(str):
                        row = rows.get(str(instrument))
                        if row is None:
                            continue
                        if not eval_open_filters(signal_exprs, row.to_dict()):
                            continue
                        allowed.add(str(instrument))
                    day_preds = day_preds.loc[day_preds["instrument"].astype(str).isin(allowed)]
                empty = max(top_n - _held_count(positions), 0)
                pending = _fill_orders(day_preds, set(positions), empty, top_n, weighting)
                pending_signal = day
        if day == last_test:
            for instrument in list(positions):
                row = rows.get(instrument)
                if row is None:
                    continue
                sell_instrument(
                    instrument,
                    int(positions.get(instrument, 0)),
                    day,
                    row,
                    reason_date=pending_signal or day,
                )
            pending = None
            pending_signal = None
            equity, invested = _mark(cash, positions, rows, sell_col)
        if date_from <= day <= last_test:
            curve.append({"date": day, "equity": equity, "cash": cash, "invested": invested})
    return trades, curve
