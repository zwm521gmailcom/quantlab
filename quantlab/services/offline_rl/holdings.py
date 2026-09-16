"""Reconstruct daily holdings from trade records."""

from __future__ import annotations


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def daily_holdings_from_trades(
    trades: list[dict], dates: list[str]
) -> dict[str, set[str]]:
    """Return instruments held on each date (buy_date <= d < sell_date)."""
    held: dict[str, set[str]] = {date: set() for date in dates}

    for trade in trades:
        if trade.get("status") != "filled":
            continue
        instrument = trade["instrument"]
        buy_date = _norm_date(trade["buy_date"])
        sell_date = (
            _norm_date(trade["sell_date"]) if trade.get("sell_date") else None
        )

        for date in dates:
            day = _norm_date(date)
            if buy_date <= day and (sell_date is None or day < sell_date):
                held[date].add(instrument)

    return held
