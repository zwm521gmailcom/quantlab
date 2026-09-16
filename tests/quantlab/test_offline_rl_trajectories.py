from quantlab.services.offline_rl.trajectories import (
    align_excess_rewards,
    daily_returns_from_equity,
    transitions_for_expert,
)


def test_daily_excess_reward():
    curve = [
        {"date": "20180102", "equity": 100.0},
        {"date": "20180103", "equity": 101.0},
        {"date": "20180104", "equity": 102.01},
    ]
    port = daily_returns_from_equity(curve)
    assert abs(port["20180103"] - 0.01) < 1e-12
    bench = {"20180103": 0.002, "20180104": 0.0}
    exc = align_excess_rewards(port, bench)
    assert abs(exc["20180103"] - 0.008) < 1e-12


def test_transitions_for_expert():
    equity_curve = [
        {"date": "20180102", "equity": 100.0},
        {"date": "20180103", "equity": 101.0},
        {"date": "20180104", "equity": 102.01},
    ]
    trades = [
        {
            "instrument": "600000.SH",
            "buy_date": "20180103",
            "sell_date": "20180105",
            "status": "filled",
        },
    ]
    bench_rets = {"20180103": 0.002, "20180104": 0.0}
    transitions = transitions_for_expert(
        fingerprint="abc123",
        action_id=1,
        equity_curve=equity_curve,
        trades=trades,
        bench_rets=bench_rets,
    )
    assert len(transitions) == 2
    assert transitions[0].date == "20180103"
    assert transitions[0].action_id == 1
    assert abs(transitions[0].port_ret - 0.01) < 1e-12
    assert abs(transitions[0].reward - 0.008) < 1e-12
    assert transitions[0].holdings == frozenset({"600000.SH"})
    assert transitions[1].date == "20180104"
    assert abs(transitions[1].port_ret - 0.01) < 1e-12
    assert abs(transitions[1].reward - 0.01) < 1e-12


def test_align_excess_rewards_accepts_hyphenated_bench_dates():
    port = {"20180103": 0.01, "20180104": 0.01}
    bench = {"2018-01-03": 0.002, "2018-01-04": 0.0}
    exc = align_excess_rewards(port, bench)
    assert abs(exc["20180103"] - 0.008) < 1e-12
    assert abs(exc["20180104"] - 0.01) < 1e-12


def test_transitions_cash_action():
    bench_rets = {"20180103": 0.002, "20180104": 0.0}
    transitions = transitions_for_expert(
        fingerprint="cash",
        action_id=0,
        equity_curve=[],
        trades=[],
        bench_rets=bench_rets,
    )
    assert len(transitions) == 2
    assert transitions[0].port_ret == 0.0
    assert transitions[0].holdings == frozenset()
    assert abs(transitions[0].reward - (-0.002)) < 1e-12
