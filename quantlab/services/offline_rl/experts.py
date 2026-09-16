"""Natural-year expert grid for offline RL expert selection."""

from __future__ import annotations

from datetime import date
from typing import Any

from quantlab.services.offline_rl.fingerprint import config_fingerprint, is_offline_rl_result


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def natural_year_window(date_from: str, date_to: str) -> int | None:
    """Return the calendar year when both bounds fall in the same year, else None."""
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start.year != end.year:
        return None
    return start.year


def _test_window(config: dict[str, Any]) -> tuple[str, str] | None:
    test = config.get("test")
    if not isinstance(test, dict):
        return None
    date_from = test.get("date_from")
    date_to = test.get("date_to")
    if not date_from or not date_to:
        return None
    return str(date_from), str(date_to)


def _fingerprint_label(config: dict[str, Any]) -> str:
    name = str(config.get("name") or "").strip()
    if name:
        return name
    factor_versions = config.get("factor_versions")
    if isinstance(factor_versions, list):
        fields = [
            str(item.get("field") or item.get("factor_id") or "").strip()
            for item in factor_versions
            if isinstance(item, dict)
        ]
        fields = [field for field in fields if field]
        if fields:
            return ", ".join(fields)
    return config_fingerprint(config)


def build_expert_grid(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a natural-year grid of expert backtest runs keyed by config fingerprint."""
    excluded_long_windows: list[dict[str, str]] = []
    by_year: dict[int, list[dict[str, Any]]] = {}
    fingerprints: dict[str, dict[str, Any]] = {}

    for run in runs:
        if str(run.get("status") or "completed") != "completed":
            continue

        config = run.get("config")
        if not isinstance(config, dict):
            continue
        if is_offline_rl_result(config):
            continue

        window = _test_window(config)
        if window is None:
            continue
        date_from, date_to = window
        year = natural_year_window(date_from, date_to)
        if year is None:
            excluded_long_windows.append(
                {"run_id": str(run.get("run_id") or ""), "date_from": date_from, "date_to": date_to}
            )
            continue

        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        fingerprint = config_fingerprint(config)
        if fingerprint not in fingerprints:
            fingerprints[fingerprint] = {
                "label": _fingerprint_label(config),
                "sample_config": config,
            }

        cell = {
            "fingerprint": fingerprint,
            "run_id": str(run.get("run_id") or ""),
            "sharpe": metrics.get("sharpe"),
            "excess_return": metrics.get("excess_return"),
            "name": str(config.get("name") or ""),
        }
        by_year.setdefault(year, []).append(cell)

    return {
        "excluded_long_windows": excluded_long_windows,
        "by_year": by_year,
        "fingerprints": fingerprints,
    }


def select_experts_before_year(
    grid: dict[str, Any],
    year: int,
    *,
    k: int = 8,
    metric: str = "sharpe",
) -> list[str]:
    """Return top-K config fingerprints using the simple mean of metric for years before Y."""
    by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
    fp_year_values: dict[str, dict[int, list[float]]] = {}

    for yr, cells in by_year.items():
        if int(yr) >= int(year):
            continue
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            fingerprint = str(cell.get("fingerprint") or "")
            if not fingerprint:
                continue
            raw = cell.get(metric)
            if raw is None:
                continue
            fp_year_values.setdefault(fingerprint, {}).setdefault(int(yr), []).append(float(raw))

    ranked: list[tuple[float, str]] = []
    for fingerprint, year_map in fp_year_values.items():
        year_avgs = [sum(values) / len(values) for values in year_map.values() if values]
        if not year_avgs:
            continue
        ranked.append((sum(year_avgs) / len(year_avgs), fingerprint))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [fingerprint for _, fingerprint in ranked[: max(0, int(k))]]
