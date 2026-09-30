"""Target-weight account with stop-loss, take-profit, and max holding days.

Signals are dated on the close; fills happen at the next session's open.
Stop-loss and take-profit do not fire on the entry bar. A gap through the
stop fills at the open, not the stop price. Does not call the slot-engine
``run_portfolio``. Open-limit checks reuse ``eval_open_filters`` when limit
columns exist; missing columns block the buy. Default lot size is 100 shares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from quantlab.services.portfolio import eval_open_filters, open_expressions

_DEFAULTS: dict[str, Any] = {
    "rebalance_every": 5,
    "target_weight": 0.10,
    "stop_loss": 0.10,
    "take_profit": 0.25,
    "max_hold_days": 45,
    "initial_capital": 1_000_000,
    "buy_price": "open",
    "sell_price": "close",
    "buy_fee_rate": 0.0003,
    "sell_fee_rate": 0.0005,
    "buy_fee_minimum": 5.0,
    "sell_fee_minimum": 5.0,
    "stamp_tax_rate": 0.001,
    "slippage": 0.0005,
    "lot_size": 100,
    "skip_open_limit": True,
    "skip_close_down_limit": True,
}

_PRICE_ALIASES: dict[str, tuple[str, ...]] = {
    "open": ("open", "hfq_open"),
    "hfq_open": ("hfq_open", "open"),
    "close": ("close", "hfq_close"),
    "hfq_close": ("hfq_close", "close"),
    "high": ("high", "hfq_high"),
    "hfq_high": ("hfq_high", "high"),
    "low": ("low", "hfq_low"),
    "hfq_low": ("hfq_low", "low"),
}


@dataclass
class _Position:
    instrument: str
    quantity: int
    buy_price: float
    buy_date: str
    signal_date: str
    buy_amount: float
    buy_fee: float
    peak_price: float = 0.0


def run_target_weight_portfolio(
    frame: pd.DataFrame,
    signals: pd.DataFrame | None,
    config: dict[str, Any] | None,
) -> tuple[list[dict], list[dict]]:
    cfg = {**_DEFAULTS, **(config or {})}
    cfg["rebalance_every"] = max(1, int(cfg["rebalance_every"]))
    cfg["max_hold_days"] = max(1, int(cfg["max_hold_days"]))
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
    cfg["buy_fee_minimum"] = float(cfg.get("buy_fee_minimum", cfg.get("min_fee", 5.0)))
    cfg["sell_fee_minimum"] = float(cfg.get("sell_fee_minimum", cfg.get("min_fee", 5.0)))

    market = _normalize_frame(frame)
    calendar = sorted(market["date"].unique())
    if not calendar:
        return [], []

    by_day = _index_rows(market)
    signal_map = _signal_map(signals)
    rebalance_every = cfg["rebalance_every"]
    date_index = {day: index for index, day in enumerate(calendar)}
    exec_to_signal = {
        calendar[index + 1]: calendar[index]
        for index in range(0, len(calendar), rebalance_every)
        if index + 1 < len(calendar)
    }

    cash = float(cfg["initial_capital"])
    positions: dict[str, _Position] = {}
    trades: list[dict] = []
    equity: list[dict] = []

    for day in calendar:
        rows = by_day.get(day, {})
        exited_today: set[str] = set()

        signal_day = exec_to_signal.get(day)
        if signal_day is not None:
            target = _positive_weights(signal_map.get(signal_day, {}), float(cfg["target_weight"]))
            if bool(cfg.get("rebalance_sell", True)):
                for instrument in list(positions):
                    if instrument in target:
                        continue
                    row = rows.get(instrument)
                    if row is None or _is_suspended(row):
                        continue
                    price = _col_price(row, "open", "open")
                    if price is None:
                        continue
                    price *= 1.0 - float(cfg["slippage"] or 0.0)
                    cash = _close_position(
                        cash, positions, trades, instrument, day, price, "rebalance", cfg, row
                    )
                    exited_today.add(instrument)

            invested = 0.0
            for instrument, position in positions.items():
                row = rows.get(instrument)
                mark = _col_price(row, "open", "open") if row is not None else None
                if mark is None:
                    mark = position.buy_price
                invested += position.quantity * mark
            nav = cash + invested
            bought_today = 0
            max_new = cfg.get("max_new_buys")
            max_positions = cfg.get("max_positions")
            for instrument, weight in target.items():
                if max_new is not None and bought_today >= int(max_new):
                    break
                if max_positions is not None and len(positions) >= int(max_positions):
                    break
                if instrument in positions or instrument in exited_today:
                    continue
                row = rows.get(instrument)
                if row is None or _is_suspended(row):
                    continue
                if not _open_allowed(cfg, row):
                    continue
                raw_price = _col_price(row, cfg["buy_price"], "open")
                if raw_price is None:
                    continue
                price = raw_price * (1.0 + float(cfg["slippage"] or 0.0))
                quantity, amount, fee = _fit_buy(
                    cash,
                    price,
                    cfg["lot_size"],
                    float(cfg["buy_fee_rate"]),
                    float(cfg["buy_fee_minimum"]),
                    budget=float(nav * weight),
                )
                if quantity <= 0:
                    continue
                cash -= amount + fee
                positions[instrument] = _Position(
                    instrument=instrument,
                    quantity=quantity,
                    buy_price=price,
                    buy_date=day,
                    signal_date=signal_day,
                    buy_amount=amount,
                    buy_fee=fee,
                    peak_price=price,
                )
                bought_today += 1
                trades.append(
                    {
                        "date": day,
                        "instrument": instrument,
                        "side": "buy",
                        "price": price,
                        "quantity": quantity,
                        "amount": amount,
                        "fee": fee,
                        "reason": "buy",
                        "signal_date": signal_day,
                        "buy_date": day,
                        "status": "filled",
                    }
                )

        for instrument in list(positions):
            position = positions[instrument]
            if position.buy_date == day:
                continue
            row = rows.get(instrument)
            if row is None or _is_suspended(row):
                continue
            high = _col_price(row, "high", "high")
            low = _col_price(row, "low", "low")
            open_px = _col_price(row, "open", "open")
            stop_price = position.buy_price * (1.0 - float(cfg["stop_loss"]))
            take_price = position.buy_price * (1.0 + float(cfg["take_profit"]))
            hit_stop = low is not None and low <= stop_price + 1e-12
            hit_take = high is not None and high >= take_price - 1e-12
            trail_arm = cfg.get("trail_arm")
            if hit_stop:
                fill = stop_price
                if open_px is not None and open_px <= stop_price + 1e-12:
                    fill = open_px
                cash = _close_position(
                    cash, positions, trades, instrument, day, fill, "stop_loss", cfg, row
                )
                exited_today.add(instrument)
            elif trail_arm not in (None, ""):
                prior_peak = position.peak_price if position.peak_price > 0 else position.buy_price
                armed = prior_peak / position.buy_price - 1.0 >= float(trail_arm) - 1e-12
                trail_price = prior_peak - float(cfg.get("trail_giveback") or 0.0) * position.buy_price
                if armed and low is not None and low <= trail_price + 1e-12:
                    fill = trail_price
                    if open_px is not None and open_px <= trail_price + 1e-12:
                        fill = open_px
                    cash = _close_position(
                        cash, positions, trades, instrument, day, fill, "trail_giveback", cfg, row
                    )
                    exited_today.add(instrument)
                elif high is not None and high > position.peak_price:
                    position.peak_price = high
            elif hit_take:
                cash = _close_position(
                    cash, positions, trades, instrument, day, take_price, "take_profit", cfg, row
                )
                exited_today.add(instrument)

        for instrument in list(positions):
            position = positions[instrument]
            buy_i = date_index.get(position.buy_date)
            day_i = date_index.get(day)
            if buy_i is None or day_i is None:
                continue
            hold_days = day_i - buy_i + 1
            if hold_days < int(cfg["max_hold_days"]):
                continue
            row = rows.get(instrument)
            if row is None or _is_suspended(row):
                continue
            price = _col_price(row, cfg["sell_price"], "close")
            if price is None:
                continue
            price *= 1.0 - float(cfg["slippage"] or 0.0)
            cash = _close_position(
                cash, positions, trades, instrument, day, price, "max_hold", cfg, row
            )
            exited_today.add(instrument)

        invested = 0.0
        for instrument, position in positions.items():
            row = rows.get(instrument)
            mark = _col_price(row, "close", "close") if row is not None else None
            if mark is None:
                mark = position.buy_price
            invested += position.quantity * mark
        equity.append(
            {
                "date": day,
                "equity": cash + invested,
                "cash": cash,
                "invested": invested,
            }
        )

    return trades, equity


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = result["date"].map(_norm_date)
    result["instrument"] = result["instrument"].astype(str)
    for name, alias in (
        ("open", "hfq_open"),
        ("close", "hfq_close"),
        ("high", "hfq_high"),
        ("low", "hfq_low"),
    ):
        if name not in result.columns and alias in result.columns:
            result[name] = result[alias]
    return result


def _index_rows(frame: pd.DataFrame) -> dict[str, dict[str, dict[str, Any]]]:
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for record in frame.to_dict("records"):
        by_day.setdefault(str(record["date"]), {})[str(record["instrument"])] = record
    return by_day


def _signal_map(signals: pd.DataFrame | None) -> dict[str, dict[str, float | None]]:
    mapping: dict[str, dict[str, float | None]] = {}
    if signals is None or len(signals) == 0:
        return mapping
    for record in signals.to_dict("records"):
        day = _norm_date(record["date"])
        instrument = str(record["instrument"])
        raw = record.get("weight", None)
        weight: float | None
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            weight = None
        else:
            weight = float(raw)
        mapping.setdefault(day, {})[instrument] = weight
    return mapping


def _positive_weights(raw: dict[str, float | None], default_weight: float) -> dict[str, float]:
    target: dict[str, float] = {}
    for instrument, weight in raw.items():
        value = default_weight if weight is None else float(weight)
        if value > 0:
            target[instrument] = value
    return target


def _norm_date(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).replace("-", "").replace(" ", "").replace("T", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return pd.Timestamp(value).strftime("%Y%m%d")


def _finite(row: dict[str, Any], column: str) -> float | None:
    if column not in row:
        return None
    value = row[column]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _col_price(row: dict[str, Any] | None, preferred: str, fallback: str) -> float | None:
    if row is None:
        return None
    for key in (*_PRICE_ALIASES.get(preferred, (preferred,)), *_PRICE_ALIASES.get(fallback, (fallback,))):
        number = _finite(row, key)
        if number is not None:
            return number
    return None


def _flag_true(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


def _is_suspended(row: dict[str, Any]) -> bool:
    present = False
    for key in ("suspended", "is_suspended"):
        if key in row:
            present = True
            if _flag_true(row[key]):
                return True
    return not present


def _open_allowed(config: dict[str, Any], row: dict[str, Any]) -> bool:
    expressions = open_expressions(config)
    if not expressions:
        return True
    if _finite(row, "up_limit") is None or _finite(row, "down_limit") is None:
        return False
    payload = dict(row)
    open_price = _col_price(row, config.get("buy_price", "open"), "open")
    if open_price is not None:
        payload["open"] = open_price
    return bool(eval_open_filters(expressions, payload))


def _fit_buy(
    cash: float,
    price: float,
    lot: int,
    fee_rate: float,
    fee_min: float,
    budget: float | None = None,
) -> tuple[int, float, float]:
    if price <= 0 or cash <= 0 or lot <= 0:
        return 0, 0.0, 0.0
    notional_cap = cash if budget is None else min(cash, max(0.0, budget))
    quantity = int(math.floor(notional_cap / price / lot) * lot)
    while quantity >= lot:
        amount = quantity * price
        fee = max(amount * fee_rate, fee_min) if amount else 0.0
        if amount + fee <= cash + 1e-9:
            return quantity, amount, fee
        quantity -= lot
    return 0, 0.0, 0.0


def _close_position(
    cash: float,
    positions: dict[str, _Position],
    trades: list[dict],
    instrument: str,
    day: str,
    price: float,
    reason: str,
    config: dict[str, Any],
    row: dict[str, Any],
) -> float:
    if _sell_blocked(config, row, price):
        return cash
    position = positions.pop(instrument)
    amount = position.quantity * price
    fee = max(amount * float(config["sell_fee_rate"]), float(config["sell_fee_minimum"])) if amount else 0.0
    stamp = amount * float(config.get("stamp_tax_rate") or 0.0)
    cash += amount - fee - stamp
    trades.append(
        {
            "date": day,
            "instrument": instrument,
            "side": "sell",
            "price": price,
            "quantity": position.quantity,
            "amount": amount,
            "fee": fee,
            "reason": reason,
            "signal_date": position.signal_date,
            "buy_date": position.buy_date,
            "buy_price": position.buy_price,
            "status": "filled",
            "sell_fee": fee,
            "stamp_tax": stamp,
            "pnl": amount - fee - stamp - position.buy_amount - position.buy_fee,
        }
    )
    return cash


def _sell_blocked(config: dict[str, Any], row: dict[str, Any], price: float) -> bool:
    if not bool(config.get("skip_close_down_limit", True)):
        return False
    down = _finite(row, "down_limit")
    if down is None:
        return False
    return price <= down + 1e-12
