import statistics

from quantlab.services.offline_rl.state_features import StateBuilder


def _bench_window(bench_rets: dict[str, float], date: str, window: int) -> list[float]:
    dates = sorted(d for d in bench_rets if d <= date)[-window:]
    return [bench_rets[d] for d in dates]


def test_observe_is_deterministic():
    builder = StateBuilder(window=2)
    kwargs = {
        "bench_rets": {"20180101": 0.01, "20180102": 0.02, "20180103": -0.01},
        "expert_excess": {
            1: {"20180101": 0.005, "20180102": 0.01, "20180103": 0.0},
            2: {"20180101": -0.005, "20180102": -0.01},
        },
        "current_action": 1,
        "cash_ratio": 0.25,
    }
    first = builder.observe("20180103", **kwargs)
    second = builder.observe("20180103", **kwargs)
    assert first == second


def test_window_2_stable_length():
    k = 2
    builder = StateBuilder(window=2)
    assert builder.dim(k) == 2 + k + (k + 1) + 1

    vec = builder.observe(
        "20180103",
        bench_rets={"20180101": 0.01, "20180102": 0.02, "20180103": -0.01},
        expert_excess={
            1: {"20180101": 0.005, "20180102": 0.01, "20180103": 0.0},
            2: {"20180101": -0.005, "20180102": -0.01},
        },
        current_action=0,
        cash_ratio=1.0,
    )
    assert len(vec) == builder.dim(k)


def test_missing_expert_history_is_zero():
    builder = StateBuilder(window=2)
    bench_rets = {"20180101": 0.01, "20180102": 0.02, "20180103": -0.01}
    expert_excess = {1: {"20180101": 0.005, "20180102": 0.01, "20180103": 0.0}}

    vec = builder.observe(
        "20180103",
        bench_rets=bench_rets,
        expert_excess=expert_excess,
        current_action=2,
        cash_ratio=0.0,
    )

    bench_vals = _bench_window(bench_rets, "20180103", 2)
    assert abs(vec[0] - statistics.fmean(bench_vals)) < 1e-12
    assert abs(vec[1] - statistics.stdev(bench_vals)) < 1e-12
    assert abs(vec[2] - statistics.fmean([0.01, 0.0])) < 1e-12
    assert vec[3] == 0.0
    assert vec[4:7] == [0.0, 0.0, 1.0]
    assert vec[7] == 0.0
