from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.offline_rl.evaluate import (
    evaluate_policy_year,
    load_benchmark_daily_returns,
    replay_champion_curve,
    verdict,
    walk_forward_years,
)
from quantlab.services.offline_rl.experts import build_expert_grid
from quantlab.services.offline_rl.fitted_q import FittedQ
from quantlab.services.offline_rl.fingerprint import config_fingerprint
from quantlab.services.offline_rl.state_features import StateBuilder


def _base_config(name: str, year: int) -> dict:
    return {
        "name": name,
        "kind": "factor_rank",
        "factor_versions": [{"field": f"sleeve_{name}"}],
        "top_n": 4,
        "weighting": "equal",
        "holding_days": 2,
        "rebalance_every": 1,
        "open_when_benchmark_gt_ma200": False,
        "model": {"kind": "factor_rank"},
        "test": {
            "date_from": f"{year}-01-02",
            "date_to": f"{year}-12-31",
            "filter": {"expressions": []},
        },
        "train": {"filter": {"expressions": []}},
    }


def _run(name: str, year: int, *, sharpe: float, excess_return: float) -> dict:
    return {
        "run_id": f"{name}{year}",
        "config": _base_config(name, year),
        "metrics": {"sharpe": sharpe, "excess_return": excess_return},
    }


def _positive_curve(year: int, daily_rets: list[float]) -> list[dict]:
    equity = 100.0
    curve = [{"date": f"{year}0101", "equity": equity}]
    for idx, daily_ret in enumerate(daily_rets):
        day = f"{year}010{idx + 2}"
        equity *= 1.0 + daily_ret
        curve.append({"date": day, "equity": equity})
    return curve


def _grid_with_two_years() -> tuple[dict, str]:
    runs = [
        _run("A", 2019, sharpe=2.0, excess_return=0.2),
        _run("B", 2019, sharpe=1.0, excess_return=0.1),
        _run("A", 2020, sharpe=1.5, excess_return=0.15),
    ]
    grid = build_expert_grid(runs)
    fp_a = config_fingerprint(runs[0]["config"])
    return grid, fp_a


def test_walk_forward_years_requires_prior_year():
    grid, _ = _grid_with_two_years()
    assert walk_forward_years(grid, min_year=2020) == [2020]
    assert 2019 not in walk_forward_years(grid, min_year=2019)


def test_replay_champion_curve_positive_sharpe():
    grid, fp_a = _grid_with_two_years()
    daily_rets = [0.01, 0.011, 0.009, 0.012]
    curves = {(fp_a, 2020): _positive_curve(2020, daily_rets)}
    bench_rets = {f"2020010{idx + 2}": 0.0 for idx in range(4)}

    result = replay_champion_curve(
        grid,
        2020,
        curves=curves,
        bench_rets=bench_rets,
    )

    assert result["champion_fingerprint"] == fp_a
    assert result["sharpe"] is not None
    assert result["sharpe"] > 0
    assert result["excess_return"] > 0
    assert len(result["daily_returns"]) == 4


def test_evaluate_policy_year_champion_copy_ratio():
    grid, fp_a = _grid_with_two_years()
    daily_rets = [0.01, 0.011, 0.009, 0.012]
    curves = {(fp_a, 2020): _positive_curve(2020, daily_rets)}
    expert_returns = {(fp_a, 2020): {f"2020010{idx + 2}": ret for idx, ret in enumerate(daily_rets)}}
    expert_excess = {1: {f"2020010{idx + 2}": ret for idx, ret in enumerate(daily_rets)}}
    bench_rets = {f"2020010{idx + 2}": 0.0 for idx in range(4)}
    state = [0.0, 0.0, 0.01, 1.0, 0.0, 0.0]
    transitions = []
    for _ in range(30):
        transitions.append(
            {"state": state, "action": 1, "reward": 0.01, "next_state": state, "done": False}
        )
    model = FittedQ(n_actions=2, gamma=0.5, iterations=5)
    model.fit(transitions)

    result = evaluate_policy_year(
        grid=grid,
        year=2020,
        fitted_q=model,
        action_fingerprints={1: fp_a},
        state_builder=StateBuilder(window=2),
        bench_rets=bench_rets,
        expert_returns=expert_returns,
        expert_excess=expert_excess,
        curves=curves,
    )

    assert result["champion_copy_ratio"] == 1.0
    assert result["sharpe"] is not None
    assert result["sharpe"] > 0
    assert len(result["daily_actions"]) == 4
    assert all("date" in row and "action_id" in row for row in result["daily_actions"])
    assert result["action_summary"]


def test_verdict_passes_when_policy_beats_replay():
    rows = [
        {
            "sharpe_policy": 2.0,
            "sharpe_replay": 1.0,
            "excess_policy": 0.10,
            "excess_replay": 0.05,
            "champion_copy_ratio": 0.5,
        },
        {
            "sharpe_policy": 1.5,
            "sharpe_replay": 1.2,
            "excess_policy": 0.08,
            "excess_replay": 0.07,
            "champion_copy_ratio": 0.8,
        },
    ]
    out = verdict(rows)
    assert out["passed"] is True
    assert out["mean_sharpe_policy"] > out["mean_sharpe_replay"]
    assert out["mean_excess_policy"] >= out["mean_excess_replay"]
    assert 0.5 < out["mean_champion_copy_ratio"] < 1.0


def test_verdict_warns_when_champion_copy_ratio_high():
    rows = [
        {
            "sharpe_policy": 2.0,
            "sharpe_replay": 1.0,
            "excess_policy": 0.10,
            "excess_replay": 0.05,
            "champion_copy_ratio": 0.95,
        },
        {
            "sharpe_policy": 1.5,
            "sharpe_replay": 1.2,
            "excess_policy": 0.08,
            "excess_replay": 0.07,
            "champion_copy_ratio": 0.85,
        },
    ]
    out = verdict(rows)
    assert out["champion_copy_warning"] is True
    assert out["champion_copy_warning_message"]
    assert out["max_champion_copy_ratio"] == 0.95


def test_verdict_fails_when_sharpe_not_better():
    rows = [
        {
            "sharpe_policy": 1.0,
            "sharpe_replay": 2.0,
            "excess_policy": 0.10,
            "excess_replay": 0.05,
        }
    ]
    assert verdict(rows)["passed"] is False


def test_load_benchmark_daily_returns(tmp_path: Path):
    raw = tmp_path / "raw" / "index_daily"
    raw.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "ts_code": ["000300.SH", "000300.SH", "000300.SH"],
                "trade_date": ["20200102", "20200103", "20200106"],
                "close": [4000.0, 4040.0, 4080.4],
            }
        ),
        raw / "index_daily_000300_SH.parquet",
    )
    rets = load_benchmark_daily_returns(tmp_path / "raw", dates=["20200103", "20200106"])
    assert abs(rets["20200103"] - 0.01) < 1e-12
    assert abs(rets["20200106"] - 0.01) < 1e-12
    assert "20200102" not in rets
