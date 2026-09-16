"""Walk-forward evaluation and champion replay for offline RL."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from math import sqrt
from pathlib import Path
from typing import Any

from quantlab.services.backtest_job import _load_index_daily, _select_benchmark_rows
from quantlab.services.offline_rl.fitted_q import FittedQ
from quantlab.services.offline_rl.state_features import StateBuilder
from quantlab.services.offline_rl.trajectories import (
    align_excess_rewards,
    daily_returns_from_equity,
)

CurveLoader = Callable[[str, int], list[dict] | None]
CurveMap = dict[tuple[str, int], list[dict]]
ReturnMap = dict[tuple[str, int], dict[str, float]]


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _sharpe(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 2:
        return None
    std = statistics.stdev(daily_rets)
    if std <= 0:
        return None
    return statistics.fmean(daily_rets) / std * sqrt(252)


def _total_return(daily_rets: list[float]) -> float:
    compound = 1.0
    for ret in daily_rets:
        compound *= 1.0 + float(ret)
    return compound - 1.0


def _cells_for_year(grid: dict[str, Any], year: int) -> list[dict[str, Any]]:
    by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
    cells = by_year.get(int(year))
    return [cell for cell in cells if isinstance(cell, dict)] if isinstance(cells, list) else []


def _champion_fingerprint(grid: dict[str, Any], year: int, *, metric: str = "sharpe") -> str | None:
    prior = _cells_for_year(grid, int(year) - 1)
    ranked: list[tuple[float, str]] = []
    for cell in prior:
        fingerprint = str(cell.get("fingerprint") or "")
        raw = cell.get(metric)
        if not fingerprint or raw is None:
            continue
        ranked.append((float(raw), fingerprint))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1]


def _resolve_curve(
    fingerprint: str,
    year: int,
    *,
    curves: CurveMap | None = None,
    curve_loader: CurveLoader | None = None,
) -> list[dict]:
    key = (fingerprint, int(year))
    if curves is not None and key in curves:
        return list(curves[key])
    if curve_loader is not None:
        loaded = curve_loader(fingerprint, int(year))
        if loaded:
            return list(loaded)
    return []


def _resolve_returns(
    fingerprint: str,
    year: int,
    *,
    expert_returns: ReturnMap | None = None,
    curves: CurveMap | None = None,
    curve_loader: CurveLoader | None = None,
) -> dict[str, float]:
    key = (fingerprint, int(year))
    if expert_returns is not None and key in expert_returns:
        return dict(expert_returns[key])
    curve = _resolve_curve(fingerprint, year, curves=curves, curve_loader=curve_loader)
    return daily_returns_from_equity(curve)


def walk_forward_years(grid: dict[str, Any], *, min_year: int = 2020) -> list[int]:
    """Return scorable years that have expert cells for year and year-1."""
    by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
    years = sorted(int(year) for year in by_year)
    return [year for year in years if year >= int(min_year) and (year - 1) in by_year]


def load_benchmark_daily_returns(
    raw_root: str | Path | None,
    *,
    code: str = "000300.SH",
    dates: list[str] | set[str] | None = None,
) -> dict[str, float]:
    """Load benchmark daily returns from existing raw index_daily parquet files."""
    frame = _load_index_daily(raw_root, code)
    rows = _select_benchmark_rows(frame, code, "00000000", "99999999")
    if rows.empty or len(rows) < 2:
        return {}

    wanted = {_norm_date(date) for date in (dates or [])}
    rets: dict[str, float] = {}
    prev_price = float(rows.iloc[0]["price"] or 0)
    for date, price in zip(rows["date"].tolist(), rows["price"].tolist(), strict=False):
        day = _norm_date(date)
        current = float(price or 0)
        if prev_price > 0:
            ret = current / prev_price - 1.0
            if not wanted or day in wanted:
                rets[day] = ret
        prev_price = current
    return rets


def replay_champion_curve(
    grid: dict[str, Any],
    year: int,
    *,
    metric: str = "sharpe",
    curves: CurveMap | None = None,
    curve_loader: CurveLoader | None = None,
    expert_returns: ReturnMap | None = None,
    bench_rets: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Replay prior-year champion on the target year and compute sharpe/excess."""
    champion = _champion_fingerprint(grid, year, metric=metric)
    if not champion:
        return {
            "year": int(year),
            "champion_fingerprint": None,
            "daily_returns": {},
            "sharpe": None,
            "excess_return": None,
            "skipped": True,
        }

    daily_returns = _resolve_returns(
        champion,
        year,
        expert_returns=expert_returns,
        curves=curves,
        curve_loader=curve_loader,
    )
    ordered = [daily_returns[day] for day in sorted(daily_returns)]
    sharpe = _sharpe(ordered)
    excess_return = None
    if bench_rets is not None and ordered:
        aligned = align_excess_rewards(daily_returns, bench_rets)
        excess_return = _total_return([aligned[day] for day in sorted(aligned)])

    return {
        "year": int(year),
        "champion_fingerprint": champion,
        "daily_returns": daily_returns,
        "sharpe": sharpe,
        "excess_return": excess_return,
        "skipped": not ordered,
    }


def evaluate_policy_year(
    *,
    grid: dict[str, Any],
    year: int,
    fitted_q: FittedQ,
    action_fingerprints: dict[int, str],
    state_builder: StateBuilder,
    bench_rets: dict[str, float],
    expert_returns: ReturnMap,
    expert_excess: dict[int, dict[str, float]],
    curves: CurveMap | None = None,
    curve_loader: CurveLoader | None = None,
    metric: str = "sharpe",
) -> dict[str, Any]:
    """Greedy Q policy over trading days in year; stitch selected expert daily returns."""
    champion = _champion_fingerprint(grid, year, metric=metric)
    fp_to_action = {fp: action for action, fp in action_fingerprints.items()}

    action_day_returns: dict[int, dict[str, float]] = {}
    for action, fingerprint in action_fingerprints.items():
        action_day_returns[action] = _resolve_returns(
            fingerprint,
            year,
            expert_returns=expert_returns,
            curves=curves,
            curve_loader=curve_loader,
        )

    trading_days = sorted({day for series in action_day_returns.values() for day in series})
    if not trading_days:
        return {
            "year": int(year),
            "champion_fingerprint": champion,
            "daily_returns": {},
            "sharpe": None,
            "excess_return": None,
            "champion_copy_ratio": None,
            "skipped": True,
        }

    current_action = 0
    cash_ratio = 0.0
    policy_returns: dict[str, float] = {}
    champion_days = 0
    daily_actions: list[dict[str, Any]] = []
    fingerprint_counts: dict[str, int] = {}

    for day in trading_days:
        available = {0}
        for action, series in action_day_returns.items():
            if day in series:
                available.add(action)

        state = state_builder.observe(
            day,
            bench_rets=bench_rets,
            expert_excess=expert_excess,
            current_action=current_action,
            cash_ratio=cash_ratio,
        )
        chosen = fitted_q.greedy(state, available=available)
        current_action = chosen
        chosen_fp = action_fingerprints.get(chosen) if chosen else None
        daily_actions.append(
            {
                "date": day,
                "action_id": int(chosen),
                **({"fingerprint": chosen_fp} if chosen_fp else {}),
            }
        )
        if chosen == 0:
            policy_returns[day] = 0.0
            cash_ratio = 1.0
            fingerprint_counts["cash"] = fingerprint_counts.get("cash", 0) + 1
        else:
            policy_returns[day] = float(action_day_returns[chosen][day])
            cash_ratio = 0.0
            if chosen_fp:
                fingerprint_counts[chosen_fp] = fingerprint_counts.get(chosen_fp, 0) + 1
            if champion and chosen_fp == champion:
                champion_days += 1

    ordered = [policy_returns[day] for day in trading_days]
    sharpe = _sharpe(ordered)
    aligned = align_excess_rewards(policy_returns, bench_rets)
    excess_return = _total_return([aligned[day] for day in sorted(aligned)]) if aligned else None
    champion_copy_ratio = champion_days / len(trading_days) if trading_days else None
    action_summary = sorted(
        fingerprint_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )

    return {
        "year": int(year),
        "champion_fingerprint": champion,
        "daily_returns": policy_returns,
        "daily_actions": daily_actions,
        "action_summary": action_summary,
        "sharpe": sharpe,
        "excess_return": excess_return,
        "champion_copy_ratio": champion_copy_ratio,
        "skipped": False,
    }


def verdict(year_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass when mean policy sharpe beats replay and mean excess is not worse."""
    rows = [row for row in year_rows if isinstance(row, dict)]
    if not rows:
        return {
            "passed": False,
            "mean_sharpe_policy": None,
            "mean_sharpe_replay": None,
            "mean_excess_policy": None,
            "mean_excess_replay": None,
            "mean_champion_copy_ratio": None,
        }

    def _mean(key: str) -> float | None:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return statistics.fmean(values) if values else None

    mean_sharpe_policy = _mean("sharpe_policy")
    mean_sharpe_replay = _mean("sharpe_replay")
    mean_excess_policy = _mean("excess_policy")
    mean_excess_replay = _mean("excess_replay")
    mean_champion_copy_ratio = _mean("champion_copy_ratio")
    copy_values = [
        float(row["champion_copy_ratio"])
        for row in rows
        if row.get("champion_copy_ratio") is not None
    ]
    max_champion_copy_ratio = max(copy_values) if copy_values else None

    passed = False
    if (
        mean_sharpe_policy is not None
        and mean_sharpe_replay is not None
        and mean_excess_policy is not None
        and mean_excess_replay is not None
    ):
        passed = mean_sharpe_policy > mean_sharpe_replay and mean_excess_policy >= mean_excess_replay

    champion_copy_warning = False
    champion_copy_warning_message: str | None = None
    if (
        mean_champion_copy_ratio is not None and mean_champion_copy_ratio >= 0.9
    ) or (max_champion_copy_ratio is not None and max_champion_copy_ratio >= 0.9):
        champion_copy_warning = True
        champion_copy_warning_message = (
            "策略与重放冠军高度重合（复制占比≥90%），学习结果几乎未偏离上年冠军。"
        )

    return {
        "passed": passed,
        "mean_sharpe_policy": mean_sharpe_policy,
        "mean_sharpe_replay": mean_sharpe_replay,
        "mean_excess_policy": mean_excess_policy,
        "mean_excess_replay": mean_excess_replay,
        "mean_champion_copy_ratio": mean_champion_copy_ratio,
        "max_champion_copy_ratio": max_champion_copy_ratio,
        "champion_copy_warning": champion_copy_warning,
        "champion_copy_warning_message": champion_copy_warning_message,
    }
