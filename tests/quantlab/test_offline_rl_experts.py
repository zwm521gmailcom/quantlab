from quantlab.services.offline_rl.experts import (
    natural_year_window,
    build_expert_grid,
    select_experts_before_year,
)
from quantlab.services.offline_rl.fingerprint import config_fingerprint


def test_natural_year_and_long_window_excluded():
    assert natural_year_window("2019-01-02", "2019-12-31") == 2019
    assert natural_year_window("2020-01-02", "2026-08-31") is None


def test_grid_and_k_selection_uses_only_prior_years():
    runs = [
        {
            "run_id": "a2019",
            "config": {
                "name": "A",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_x"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2019-01-02", "date_to": "2019-12-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 1.0, "excess_return": 0.1},
        },
        {
            "run_id": "a2020",
            "config": {
                "name": "A",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_x"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2020-01-02", "date_to": "2020-12-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 2.0, "excess_return": 0.2},
        },
        {
            "run_id": "long",
            "config": {
                "name": "long",
                "kind": "factor_rank",
                "factor_versions": [{"field": "sleeve_y"}],
                "top_n": 4,
                "weighting": "equal",
                "holding_days": 2,
                "rebalance_every": 1,
                "open_when_benchmark_gt_ma200": False,
                "model": {"kind": "factor_rank"},
                "test": {"date_from": "2020-01-02", "date_to": "2026-08-31", "filter": {"expressions": []}},
                "train": {"filter": {"expressions": []}},
            },
            "metrics": {"sharpe": 9.0, "excess_return": 9.0},
        },
        {
            "run_id": "rl",
            "config": {"kind": "offline_rl", "test": {"date_from": "2019-01-02", "date_to": "2019-12-31"}},
            "metrics": {"sharpe": 8.0},
        },
    ]
    grid = build_expert_grid(runs)
    grid_run_ids = {cell["run_id"] for cells in grid["by_year"].values() for cell in cells}
    assert "long" not in grid_run_ids
    assert "rl" not in grid_run_ids
    excluded_run_ids = {item["run_id"] for item in grid["excluded_long_windows"]}
    assert "long" in excluded_run_ids

    sleeve_x_fp = config_fingerprint(runs[0]["config"])
    assert config_fingerprint(runs[1]["config"]) == sleeve_x_fp

    fps = select_experts_before_year(grid, 2021, k=1)
    # 2020 夏普更高的同指纹应排前；选 K 用 year<2021 的年均或末年夏普
    assert len(fps) == 1
    assert fps[0] == sleeve_x_fp


def test_select_experts_before_year_ignores_target_year_cells():
    prior_run = {
        "run_id": "prior2019",
        "config": {
            "name": "Prior",
            "kind": "factor_rank",
            "factor_versions": [{"field": "sleeve_x"}],
            "top_n": 4,
            "weighting": "equal",
            "holding_days": 2,
            "rebalance_every": 1,
            "open_when_benchmark_gt_ma200": False,
            "model": {"kind": "factor_rank"},
            "test": {"date_from": "2019-01-02", "date_to": "2019-12-31", "filter": {"expressions": []}},
            "train": {"filter": {"expressions": []}},
        },
        "metrics": {"sharpe": 1.0, "excess_return": 0.1},
    }
    current_year_only = {
        "run_id": "hot2020",
        "config": {
            "name": "Hot",
            "kind": "factor_rank",
            "factor_versions": [{"field": "sleeve_y"}],
            "top_n": 4,
            "weighting": "equal",
            "holding_days": 2,
            "rebalance_every": 1,
            "open_when_benchmark_gt_ma200": False,
            "model": {"kind": "factor_rank"},
            "test": {"date_from": "2020-01-02", "date_to": "2020-12-31", "filter": {"expressions": []}},
            "train": {"filter": {"expressions": []}},
        },
        "metrics": {"sharpe": 9.0, "excess_return": 0.9},
    }
    grid = build_expert_grid([prior_run, current_year_only])
    fps = select_experts_before_year(grid, 2020, k=1)
    assert fps == [config_fingerprint(prior_run["config"])]
