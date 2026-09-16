"""Daily returns, excess rewards, and expert trajectory transitions."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.services.offline_rl.holdings import daily_holdings_from_trades


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _normalize_rets_keys(rets: dict[str, float]) -> dict[str, float]:
    return {_norm_date(date): float(value) for date, value in rets.items()}


@dataclass(frozen=True)
class Transition:
    date: str
    action_id: int
    reward: float
    holdings: frozenset[str]
    port_ret: float


def daily_returns_from_equity(curve: list[dict]) -> dict[str, float]:
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


def align_excess_rewards(
    port_rets: dict[str, float], bench_rets: dict[str, float]
) -> dict[str, float]:
    """Return port minus benchmark on dates present in both series."""
    bench_rets = _normalize_rets_keys(bench_rets)
    common = set(port_rets) & set(bench_rets)
    return {date: float(port_rets[date]) - float(bench_rets[date]) for date in common}


def transitions_for_expert(
    *,
    fingerprint: str,
    action_id: int,
    equity_curve: list[dict],
    trades: list[dict],
    bench_rets: dict[str, float],
) -> list[Transition]:
    """Build per-day transitions for one expert (or cash when action_id is 0)."""
    _ = fingerprint
    bench_rets = _normalize_rets_keys(bench_rets)

    if action_id == 0:
        dates = sorted(bench_rets)
        return [
            Transition(
                date=date,
                action_id=0,
                reward=-float(bench_rets[date]),
                holdings=frozenset(),
                port_ret=0.0,
            )
            for date in dates
        ]

    port_rets = daily_returns_from_equity(equity_curve)
    excess = align_excess_rewards(port_rets, bench_rets)
    curve_dates = [_norm_date(point["date"]) for point in equity_curve]
    holdings_by_date = daily_holdings_from_trades(trades, curve_dates)

    transitions: list[Transition] = []
    for date in sorted(excess):
        transitions.append(
            Transition(
                date=date,
                action_id=action_id,
                reward=excess[date],
                holdings=frozenset(holdings_by_date.get(date, set())),
                port_ret=port_rets[date],
            )
        )
    return transitions
