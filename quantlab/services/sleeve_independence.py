"""Independence metrics between two long-only sleeve equity curves."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

TRADING_DAYS_PER_YEAR = 243
MIN_PEARSON_POINTS = 20


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def daily_returns_from_equity(curve: list[dict[str, Any]]) -> dict[str, float]:
    """Return daily portfolio returns as equity[t] / equity[t-1] - 1."""
    if len(curve) < 2:
        return {}
    rets: dict[str, float] = {}
    prev_equity = float(curve[0]["equity"])
    for point in curve[1:]:
        equity = float(point["equity"])
        date = _norm_date(point["date"])
        if prev_equity != 0.0:
            rets[date] = equity / prev_equity - 1.0
        else:
            rets[date] = 0.0
        prev_equity = equity
    return rets


def pearson_active_days(left: dict[str, float], right: dict[str, float]) -> float | None:
    """Pearson correlation on dates where at least one sleeve return is non-zero."""
    xs: list[float] = []
    ys: list[float] = []
    for date in sorted(set(left) & set(right)):
        x = float(left[date])
        y = float(right[date])
        if x == 0.0 and y == 0.0:
            continue
        xs.append(x)
        ys.append(y)
    if len(xs) < MIN_PEARSON_POINTS:
        return None
    corr = np.corrcoef(np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))[0, 1]
    if not np.isfinite(corr):
        return None
    return float(corr)


def _cagr_and_drawdown(rets: dict[str, float]) -> tuple[float | None, float | None]:
    if not rets:
        return None, None
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for date in sorted(rets):
        equity *= 1.0 + float(rets[date])
        if equity > peak:
            peak = equity
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
    n = len(rets)
    if equity <= 0 or n <= 0:
        return None, max_drawdown
    cagr = equity ** (TRADING_DAYS_PER_YEAR / n) - 1.0
    return float(cagr), float(max_drawdown)


def _mar(cagr: float | None, max_drawdown: float | None) -> float | None:
    if cagr is None or max_drawdown is None or max_drawdown == 0.0:
        return None
    return float(cagr) / abs(float(max_drawdown))


def incremental_mar(
    sleeve_a: dict[str, float],
    sleeve_b: dict[str, float],
) -> tuple[float | None, float | None, float | None]:
    """Return (MAR_A, MAR_50/50, delta). Delta is None when either MAR is undefined."""
    blend: dict[str, float] = {}
    for date in set(sleeve_a) | set(sleeve_b):
        blend[date] = 0.5 * float(sleeve_a.get(date, 0.0)) + 0.5 * float(sleeve_b.get(date, 0.0))
    mar_a = _mar(*_cagr_and_drawdown(sleeve_a))
    mar_blend = _mar(*_cagr_and_drawdown(blend))
    if mar_a is None or mar_blend is None:
        return mar_a, mar_blend, None
    return mar_a, mar_blend, mar_blend - mar_a


def month_returns(daily: dict[str, float]) -> dict[str, float]:
    """Compound daily returns into calendar-month returns keyed YYYYMM."""
    compounded: dict[str, float] = defaultdict(lambda: 1.0)
    for date, value in daily.items():
        compounded[_norm_date(date)[:6]] *= 1.0 + float(value)
    return {month: growth - 1.0 for month, growth in compounded.items()}


def top_name_pnl_share(trades: list[dict[str, Any]]) -> float:
    """Largest name's share of total positive PnL. 0 if nothing is profitable."""
    by_name: dict[str, float] = defaultdict(float)
    for trade in trades:
        name = str(trade.get("instrument") or trade.get("ts_code") or "").strip()
        if not name:
            continue
        pnl = trade.get("pnl")
        if pnl is None:
            pnl = trade.get("profit")
        by_name[name] += float(pnl or 0.0)
    gross = sum(value for value in by_name.values() if value > 0)
    if gross <= 0:
        return 0.0
    return max(by_name.values()) / gross
