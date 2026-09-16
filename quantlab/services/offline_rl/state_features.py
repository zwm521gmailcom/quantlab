"""Fixed-length state vectors for offline RL expert selection."""

from __future__ import annotations

import statistics


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _window_values(series: dict[str, float], date: str, window: int) -> list[float]:
    date = _norm_date(date)
    dates = sorted(d for d in series if d <= date)[-window:]
    return [float(series[d]) for d in dates]


def _mean_and_vol(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, 0.0
    return mean, statistics.stdev(values)


class StateBuilder:
    """Build fixed-length state vectors from market and expert summaries."""

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def dim(self, k: int) -> int:
        """Return vector length for k experts (actions 1..k plus cash)."""
        return 2 + k + (k + 1) + 1

    def observe(
        self,
        date: str,
        *,
        bench_rets: dict[str, float],
        expert_excess: dict[int, dict[str, float]],
        current_action: int,
        cash_ratio: float,
    ) -> list[float]:
        """Return state: bench mean/vol, per-expert excess means, one-hot, cash."""
        k = max([*expert_excess.keys(), current_action])

        bench_mean, bench_vol = _mean_and_vol(
            _window_values(bench_rets, date, self.window)
        )

        excess_means: list[float] = []
        for action_id in range(1, k + 1):
            series = expert_excess.get(action_id)
            if not series:
                excess_means.append(0.0)
            else:
                mean, _ = _mean_and_vol(_window_values(series, date, self.window))
                excess_means.append(mean)

        one_hot = [0.0] * (k + 1)
        if 0 <= current_action <= k:
            one_hot[current_action] = 1.0

        return [bench_mean, bench_vol, *excess_means, *one_hot, float(cash_ratio)]
