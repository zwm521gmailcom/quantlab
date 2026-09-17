from quantlab.services.sleeve_independence import (
    daily_returns_from_equity,
    incremental_mar,
    month_returns,
    pearson_active_days,
    top_name_pnl_share,
)


def test_pearson_skips_days_both_zero() -> None:
    left = {"20200102": 0.0, "20200103": 0.01, "20200106": -0.01}
    right = {"20200102": 0.0, "20200103": 0.02, "20200106": 0.02}
    pad = {f"202002{day:02d}": 0.001 * (1 if day % 2 == 0 else -1) for day in range(3, 23)}
    pad_b = {day: value * 2 for day, value in pad.items()}
    corr_with_zero = pearson_active_days({**left, **pad}, {**right, **pad_b})
    corr_without_zero = pearson_active_days(
        {"20200103": 0.01, "20200106": -0.01, **pad},
        {"20200103": 0.02, "20200106": 0.02, **pad_b},
    )
    assert corr_with_zero is not None
    assert corr_without_zero is not None
    assert abs(corr_with_zero - corr_without_zero) < 1e-12


def test_identical_series_corr_is_one() -> None:
    rets = {f"202001{day:02d}": 0.01 * ((-1) ** day) for day in range(2, 28)}
    assert abs(pearson_active_days(rets, rets) - 1.0) < 1e-12


def test_incremental_mar_fifty_fifty_beats_worse_leg() -> None:
    dates = [f"202001{day:02d}" for day in range(2, 32)] + [f"202002{day:02d}" for day in range(1, 21)]
    sleeve_a = {date: 0.01 for date in dates}
    sleeve_a[dates[10]] = -0.25
    sleeve_b = {date: 0.002 for date in dates}
    mar_a, mar_blend, delta = incremental_mar(sleeve_a, sleeve_b)
    assert mar_a is not None
    assert mar_blend is not None
    assert delta is not None
    assert delta > 0


def test_month_returns_2019_apr_may() -> None:
    curve = [
        {"date": "20190401", "equity": 1.0},
        {"date": "20190430", "equity": 0.9},
        {"date": "20190531", "equity": 0.81},
    ]
    months = month_returns(daily_returns_from_equity(curve))
    assert abs(months["201904"] - (0.9 / 1.0 - 1.0)) < 1e-12
    assert abs(months["201905"] - (0.81 / 0.9 - 1.0)) < 1e-12


def test_top_name_pnl_share_flags_lottery() -> None:
    trades = [
        {"instrument": "AAA.SZ", "pnl": 80.0},
        {"instrument": "BBB.SZ", "pnl": 10.0},
        {"instrument": "CCC.SZ", "pnl": 10.0},
    ]
    assert top_name_pnl_share(trades) == 0.8
