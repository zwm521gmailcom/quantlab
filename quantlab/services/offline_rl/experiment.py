"""Offline RL experiment orchestration: preview grid, walk-forward run, write artifacts."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.backtest_control import (
    execution_busy_label,
    release_reservation,
    reserve_execution,
)
from quantlab.services.offline_rl.evaluate import (
    evaluate_policy_year,
    load_benchmark_daily_returns,
    replay_champion_curve,
    verdict,
    walk_forward_years,
)
from quantlab.services.offline_rl.experts import build_expert_grid, select_experts_before_year
from quantlab.services.offline_rl.fitted_q import FittedQ
from quantlab.services.offline_rl.fingerprint import is_offline_rl_result
from quantlab.services.offline_rl.state_features import StateBuilder
from quantlab.services.offline_rl.trajectories import (
    Transition,
    align_excess_rewards,
    daily_returns_from_equity,
    transitions_for_expert,
)

BenchProvider = Callable[[list[str] | set[str] | None], dict[str, float]]


def _norm_date(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_run_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = secrets.randbelow(10000)
    return now.strftime("%Y%m%d-%H%M%S") + f"-{suffix:04d}"


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _metric_value(raw: Any) -> Any:
    if isinstance(raw, dict) and raw.get("value") is not None:
        return raw.get("value")
    return raw


def _normalize_metrics(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("sharpe", "excess_return", "return", "annual_return"):
        value = _metric_value(raw.get(key))
        if value is not None:
            out[key] = value
    return out


def _load_metrics(folder: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    disk = _normalize_metrics(_load_json(folder / "metrics.json"))
    if disk:
        return disk
    embedded = manifest.get("metrics")
    return _normalize_metrics(embedded)


class _ExperimentProgress:
    def __init__(self) -> None:
        self._state = {"status": "idle", "percent": 0, "message": ""}

    def snapshot(self) -> dict[str, Any]:
        return dict(self._state)

    def set(self, *, status: str, percent: int, message: str) -> None:
        self._state = {
            "status": str(status),
            "percent": max(0, min(100, int(percent))),
            "message": str(message),
        }


class OfflineRlExperimentService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        bench_rets_provider: BenchProvider | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self._bench_rets_provider = bench_rets_provider
        self._progress = _ExperimentProgress()

    def progress(self) -> dict[str, Any]:
        return self._progress.snapshot()

    def _is_busy(self) -> bool:
        if execution_busy_label() is not None:
            return True
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM backtest_plans WHERE status='running' LIMIT 1"
            ).fetchone()
        return row is not None

    def _scan_runs(self) -> list[dict[str, Any]]:
        root = self.settings.runtime_root / "results"
        if not root.is_dir():
            return []
        runs: list[dict[str, Any]] = []
        for folder in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest = _load_json(folder / "run.json")
            if not isinstance(manifest, dict):
                continue
            config = manifest.get("config")
            if not isinstance(config, dict):
                continue
            if is_offline_rl_result(config):
                continue
            run_id = str(manifest.get("run_id") or folder.name)
            runs.append(
                {
                    "run_id": run_id,
                    "status": str(manifest.get("status") or "completed"),
                    "config": config,
                    "metrics": _load_metrics(folder, manifest),
                }
            )
        return runs

    def _cell_run_ids(self, grid: dict[str, Any]) -> dict[tuple[str, int], str]:
        mapping: dict[tuple[str, int], str] = {}
        by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
        for year, cells in by_year.items():
            if not isinstance(cells, list):
                continue
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                fingerprint = str(cell.get("fingerprint") or "")
                run_id = str(cell.get("run_id") or "")
                if fingerprint and run_id:
                    mapping[(fingerprint, int(year))] = run_id
        return mapping

    def _load_equity_curve(self, run_id: str) -> list[dict]:
        payload = _load_json(self.settings.runtime_root / "results" / run_id / "equity_curve.json")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict) and row.get("date")]

    def _load_trades(self, run_id: str) -> list[dict]:
        payload = _load_json(self.settings.runtime_root / "results" / run_id / "trades.json")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def _resolve_bench_rets(self, dates: list[str] | set[str] | None) -> dict[str, float]:
        if self._bench_rets_provider is not None:
            return dict(self._bench_rets_provider(dates))
        return load_benchmark_daily_returns(self.settings.raw_root, dates=dates)

    def _build_curve_maps(
        self,
        grid: dict[str, Any],
    ) -> tuple[dict[tuple[str, int], list[dict]], dict[tuple[str, int], dict[str, float]]]:
        run_ids = self._cell_run_ids(grid)
        curves: dict[tuple[str, int], list[dict]] = {}
        expert_returns: dict[tuple[str, int], dict[str, float]] = {}
        for key, run_id in run_ids.items():
            curve = self._load_equity_curve(run_id)
            if curve:
                curves[key] = curve
                expert_returns[key] = daily_returns_from_equity(curve)
        return curves, expert_returns

    def _training_years(self, grid: dict[str, Any], target_year: int) -> list[int]:
        by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
        return sorted(int(year) for year in by_year if int(year) < int(target_year))

    def _unified_calendar(
        self,
        *,
        action_fingerprints: dict[int, str],
        training_years: list[int],
        expert_returns: dict[tuple[str, int], dict[str, float]],
        bench_rets: dict[str, float],
    ) -> list[str]:
        days = set(bench_rets)
        for fingerprint in action_fingerprints.values():
            for year in training_years:
                days.update(expert_returns.get((fingerprint, year), {}))
        return sorted(days)

    def _build_expert_excess(
        self,
        *,
        action_fingerprints: dict[int, str],
        training_years: list[int],
        expert_returns: dict[tuple[str, int], dict[str, float]],
        bench_rets: dict[str, float],
    ) -> dict[int, dict[str, float]]:
        expert_excess: dict[int, dict[str, float]] = {}
        for action_id, fingerprint in action_fingerprints.items():
            merged: dict[str, float] = {}
            for year in training_years:
                merged.update(
                    align_excess_rewards(
                        expert_returns.get((fingerprint, year), {}),
                        bench_rets,
                    )
                )
            expert_excess[action_id] = merged
        return expert_excess

    def _fit_transitions(
        self,
        *,
        expert_rows: list[Transition],
        action_id: int,
        calendar: list[str],
        state_builder: StateBuilder,
        bench_rets: dict[str, float],
        expert_excess: dict[int, dict[str, float]],
    ) -> list[dict[str, Any]]:
        if not calendar:
            return []
        day_index = {day: index for index, day in enumerate(calendar)}
        cash_ratio = 1.0 if action_id == 0 else 0.0
        fit_rows: list[dict[str, Any]] = []
        for row in expert_rows:
            if row.date not in day_index:
                continue
            state = state_builder.observe(
                row.date,
                bench_rets=bench_rets,
                expert_excess=expert_excess,
                current_action=action_id,
                cash_ratio=cash_ratio,
            )
            index = day_index[row.date]
            done = index >= len(calendar) - 1
            next_date = calendar[index + 1] if not done else row.date
            next_state = state_builder.observe(
                next_date,
                bench_rets=bench_rets,
                expert_excess=expert_excess,
                current_action=action_id,
                cash_ratio=cash_ratio,
            )
            fit_rows.append(
                {
                    "state": state,
                    "action": action_id,
                    "reward": float(row.reward),
                    "next_state": next_state,
                    "done": done,
                }
            )
        return fit_rows

    def _build_training_transitions(
        self,
        *,
        grid: dict[str, Any],
        target_year: int,
        action_fingerprints: dict[int, str],
        run_ids: dict[tuple[str, int], str],
        expert_returns: dict[tuple[str, int], dict[str, float]],
        state_builder: StateBuilder,
        bench_rets: dict[str, float],
    ) -> list[dict[str, Any]]:
        training_years = self._training_years(grid, target_year)
        expert_excess = self._build_expert_excess(
            action_fingerprints=action_fingerprints,
            training_years=training_years,
            expert_returns=expert_returns,
            bench_rets=bench_rets,
        )
        calendar = self._unified_calendar(
            action_fingerprints=action_fingerprints,
            training_years=training_years,
            expert_returns=expert_returns,
            bench_rets=bench_rets,
        )
        transitions: list[dict[str, Any]] = []
        cash_rows = transitions_for_expert(
            fingerprint="cash",
            action_id=0,
            equity_curve=[],
            trades=[],
            bench_rets=bench_rets,
        )
        transitions.extend(
            self._fit_transitions(
                expert_rows=cash_rows,
                action_id=0,
                calendar=calendar,
                state_builder=state_builder,
                bench_rets=bench_rets,
                expert_excess=expert_excess,
            )
        )
        for action_id, fingerprint in action_fingerprints.items():
            for year in training_years:
                run_id = run_ids.get((fingerprint, year))
                if not run_id:
                    continue
                expert_rows = transitions_for_expert(
                    fingerprint=fingerprint,
                    action_id=action_id,
                    equity_curve=self._load_equity_curve(run_id),
                    trades=self._load_trades(run_id),
                    bench_rets=bench_rets,
                )
                transitions.extend(
                    self._fit_transitions(
                        expert_rows=expert_rows,
                        action_id=action_id,
                        calendar=calendar,
                        state_builder=state_builder,
                        bench_rets=bench_rets,
                        expert_excess=expert_excess,
                    )
                )
        return transitions

    def preview(self, *, k: int = 8, window: int = 20) -> dict[str, Any]:
        grid = build_expert_grid(self._scan_runs())
        scoreable_years = walk_forward_years(grid)
        fingerprints = grid.get("fingerprints") if isinstance(grid.get("fingerprints"), dict) else {}
        by_year = grid.get("by_year") if isinstance(grid.get("by_year"), dict) else {}
        actions_by_year: dict[str, list[str]] = {}
        for year in scoreable_years:
            actions_by_year[str(year)] = select_experts_before_year(grid, year, k=k)

        return {
            "k": int(k),
            "window": int(window),
            "excluded_long_windows": list(grid.get("excluded_long_windows") or []),
            "scoreable_years": scoreable_years,
            "actions_by_year": actions_by_year,
            "fingerprints": {
                fp: {"label": str(meta.get("label") or fp)}
                for fp, meta in fingerprints.items()
                if isinstance(meta, dict)
            },
            "by_year": {
                str(year): len(cells) if isinstance(cells, list) else 0
                for year, cells in by_year.items()
            },
        }

    def run(
        self,
        *,
        k: int = 8,
        window: int = 20,
        gamma: float = 0.99,
        iterations: int = 10,
        write_results: bool = True,
    ) -> dict[str, Any]:
        if self._is_busy():
            raise RuntimeError("busy")

        try:
            reserve_execution("offline_rl")
        except ValueError:
            raise RuntimeError("busy") from None

        try:
            return self._run_experiment(
                k=k,
                window=window,
                gamma=gamma,
                iterations=iterations,
                write_results=write_results,
            )
        finally:
            release_reservation()

    def _run_experiment(
        self,
        *,
        k: int,
        window: int,
        gamma: float,
        iterations: int,
        write_results: bool,
    ) -> dict[str, Any]:
        self._progress.set(status="running", percent=0, message="loading runs")
        runs = self._scan_runs()
        grid = build_expert_grid(runs)
        scoreable_years = walk_forward_years(grid)
        curves, expert_returns = self._build_curve_maps(grid)
        run_ids = self._cell_run_ids(grid)
        state_builder = StateBuilder(window=window)

        all_dates: set[str] = set()
        for series in expert_returns.values():
            all_dates.update(series)
        bench_rets = self._resolve_bench_rets(all_dates)
        if not bench_rets and all_dates:
            bench_rets = {day: 0.0 for day in sorted(all_dates)}

        year_rows: list[dict[str, Any]] = []
        year_results: list[dict[str, Any]] = []
        total = max(len(scoreable_years), 1)

        for index, year in enumerate(scoreable_years):
            self._progress.set(
                status="running",
                percent=int((index / total) * 90),
                message=f"walk-forward {year}",
            )
            selected = select_experts_before_year(grid, year, k=k)
            action_fingerprints = {action: fp for action, fp in enumerate(selected, start=1)}
            n_actions = len(action_fingerprints) + 1

            fit_rows = self._build_training_transitions(
                grid=grid,
                target_year=year,
                action_fingerprints=action_fingerprints,
                run_ids=run_ids,
                expert_returns=expert_returns,
                state_builder=state_builder,
                bench_rets=bench_rets,
            )
            fitted_q = FittedQ(n_actions=n_actions, gamma=gamma, iterations=iterations)
            if fit_rows:
                fitted_q.fit(fit_rows)

            expert_excess = self._build_expert_excess(
                action_fingerprints=action_fingerprints,
                training_years=self._training_years(grid, year),
                expert_returns=expert_returns,
                bench_rets=bench_rets,
            )

            replay = replay_champion_curve(
                grid,
                year,
                curves=curves,
                expert_returns=expert_returns,
                bench_rets=bench_rets,
            )
            policy = evaluate_policy_year(
                grid=grid,
                year=year,
                fitted_q=fitted_q,
                action_fingerprints=action_fingerprints,
                state_builder=state_builder,
                bench_rets=bench_rets,
                expert_returns=expert_returns,
                expert_excess=expert_excess,
                curves=curves,
            )

            row = {
                "year": year,
                "sharpe_replay": replay.get("sharpe"),
                "excess_replay": replay.get("excess_return"),
                "sharpe_policy": policy.get("sharpe"),
                "excess_policy": policy.get("excess_return"),
                "champion_copy_ratio": policy.get("champion_copy_ratio"),
                "action_summary": policy.get("action_summary"),
                "champion_fingerprint": replay.get("champion_fingerprint"),
                "experts": list(action_fingerprints.values()),
            }
            year_rows.append(row)
            year_results.append(
                {
                    "year": year,
                    "replay": replay,
                    "policy": policy,
                    "experts": list(action_fingerprints.values()),
                }
            )

        final_verdict = verdict(year_rows)
        run_id = _new_run_id()
        payload = {
            "run_id": run_id,
            "status": "completed",
            "created_at": _utc_now(),
            "finished_at": _utc_now(),
            "config": {
                "kind": "offline_rl",
                "k": int(k),
                "window": int(window),
                "gamma": float(gamma),
                "iterations": int(iterations),
                "scoreable_years": scoreable_years,
                "years": year_results,
                "verdict": final_verdict,
            },
            "metrics": {
                "passed": final_verdict.get("passed"),
                "mean_sharpe_policy": final_verdict.get("mean_sharpe_policy"),
                "mean_sharpe_replay": final_verdict.get("mean_sharpe_replay"),
                "mean_excess_policy": final_verdict.get("mean_excess_policy"),
                "mean_excess_replay": final_verdict.get("mean_excess_replay"),
            },
        }

        if write_results:
            folder = self.settings.runtime_root / "results" / run_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "run.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (folder / "metrics.json").write_text(
                json.dumps(payload["metrics"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        self._progress.set(status="completed", percent=100, message="done")
        return {
            "run_id": run_id,
            "k": int(k),
            "window": int(window),
            "scoreable_years": scoreable_years,
            "years": year_rows,
            "verdict": final_verdict,
        }
