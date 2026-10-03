import pandas as pd

from quantlab.services.qlib_strategy import backtest_topk, topk_dropout_targets


def test_names_outside_the_top_list_are_sold_even_when_more_than_n_drop_leave() -> None:
    score = pd.Series({f"S{i:03d}": float(1000 - i) for i in range(80)})
    held = [f"S{i:03d}" for i in range(20, 70)]

    targets = topk_dropout_targets(score, held, topk=50, n_drop=5)

    assert targets == [f"S{i:03d}" for i in range(50)]
    assert "S050" not in targets


def test_equal_scores_keep_names_already_held() -> None:
    score = pd.Series({f"S{i:03d}": 1.0 for i in range(80)})
    held = [f"S{i:03d}" for i in range(50)]

    targets = topk_dropout_targets(score, held, topk=50, n_drop=5)

    assert targets == held


def test_better_names_enter_the_same_day_past_the_old_swap_limit() -> None:
    score = {f"H{index:02d}": 1.0 for index in range(50)}
    score.update({f"N{index:02d}": 2.0 for index in range(12)})
    held = [f"H{index:02d}" for index in range(50)]

    targets = topk_dropout_targets(pd.Series(score), held, topk=50, n_drop=5)

    assert len(targets) == 50
    assert sum(code.startswith("N") for code in targets) == 12
    assert sum(code.startswith("H") for code in targets) == 38


def test_backtest_sells_every_name_that_left_the_top_list() -> None:
    names = ["S0", "S1", "S2", "S3"]
    dates: list[str] = []
    instruments: list[str] = []
    scores: list[float] = []
    returns: list[float] = []
    prices: list[float] = []
    for day in range(5):
        for index, name in enumerate(names):
            dates.append(f"2025-01-{day + 1:02d}")
            instruments.append(name)
            scores.append(float(10 - index if day == 0 else index))
            returns.append(0.0)
            prices.append(10.0)

    result = backtest_topk(
        pd.Series(scores),
        pd.Series(returns),
        pd.Series(dates),
        pd.Series(instruments),
        prices=pd.Series(prices),
        topk=2,
        n_drop=1,
    )

    sold = {row["instrument"] for row in result["trades"] if row.get("reason") == "平仓"}
    assert sold == {"S0", "S1"}
