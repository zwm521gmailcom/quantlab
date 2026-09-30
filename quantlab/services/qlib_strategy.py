"""LightGBM scores plus Qlib's TopkDropout holdings, scored on the test window."""

from __future__ import annotations

import numpy as np
import pandas as pd

TOPK = 50
N_DROP = 5
BUY_COST = 0.0005
SELL_COST = 0.0015
RISK_DEGREE = 0.95
BOOK_CAPITAL = 1_000_000.0


def topk_dropout_targets(score: pd.Series, held: list[str], *, topk: int = TOPK, n_drop: int = N_DROP) -> list[str]:
    """Match Qlib TopkDropoutStrategy: keep the book, sell the weakest n_drop, buy replacements."""
    ranked = score.dropna().sort_values(ascending=False)
    held_index = pd.Index(dict.fromkeys(held))
    last = ranked.reindex(held_index.intersection(ranked.index)).sort_values(ascending=False).index
    missing = [code for code in held_index if code not in ranked.index]
    last = pd.Index(list(last) + missing)
    fresh = ranked.index.difference(last)
    buy_cap = n_drop + topk - len(last)
    today = pd.Index(fresh[: max(buy_cap, 0)])
    combined = ranked.reindex(last.union(today)).sort_values(ascending=False).index
    weakest = set(combined[-n_drop:]) if n_drop and len(combined) else set()
    sell = [code for code in last if code in weakest]
    buy = list(today[: max(len(sell) + topk - len(last), 0)])
    kept = [code for code in last if code not in set(sell)]
    return (kept + buy)[:topk]


def _daily_zscore(factor: pd.Series, dates: pd.Series) -> pd.Series:
    frame = pd.DataFrame({"factor": factor.to_numpy(), "date": dates.to_numpy()})
    grouped = frame.groupby("date")["factor"]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, np.nan)
    values = ((frame["factor"] - mean) / std).to_numpy()
    return pd.Series(values, index=factor.index)


def lightgbm_scores(
    factor: pd.Series,
    future: pd.Series,
    dates: pd.Series,
    *,
    train_end: str,
    valid_end: str,
    learning_rate: float = 0.05,
    num_leaves: int = 15,
    min_data_in_leaf: int = 20,
    num_boost_round: int = 80,
    early_stopping_rounds: int = 10,
    short_valid_rounds: int | None = None,
    num_threads: int | None = None,
    seed: int | None = None,
) -> pd.Series:
    import lightgbm as lgb

    features = _daily_zscore(factor, dates)
    rows = pd.DataFrame(
        {
            "x": features.to_numpy(),
            "y": future.to_numpy(),
            "date": dates.to_numpy(),
        }
    )
    train = rows[rows["date"] <= train_end].dropna()
    valid = rows[(rows["date"] > train_end) & (rows["date"] <= valid_end)].dropna()
    if len(train) < 30:
        raise ValueError("训练样本不够")
    train_set = lgb.Dataset(train[["x"]], label=train["y"])
    params = {
        "objective": "regression",
        "learning_rate": learning_rate,
        "num_leaves": num_leaves,
        "min_data_in_leaf": min_data_in_leaf,
        "verbosity": -1,
    }
    if seed is not None:
        params["seed"] = seed
    if num_threads is not None:
        params["num_threads"] = num_threads
    if len(valid) >= 10:
        valid_set = lgb.Dataset(valid[["x"]], label=valid["y"])
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=num_boost_round,
            valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
        )
    else:
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=40 if short_valid_rounds is None else short_valid_rounds,
        )
    scores = pd.Series(np.nan, index=factor.index, dtype="float64")
    test_rows = rows[(rows["date"] > valid_end) & rows["x"].notna()]
    if test_rows.empty:
        raise ValueError("测试段没有可预测的样本")
    predicted = booster.predict(test_rows[["x"]])
    scores.iloc[test_rows.index.to_numpy()] = predicted
    return scores


def next_close_return(frame: pd.DataFrame) -> pd.Series:
    if "close" not in frame.columns:
        raise ValueError("研究行情里没有 close，无法做 Qlib 回归标签。")
    close = pd.to_numeric(frame["close"], errors="coerce")
    instrument = frame["instrument"] if "instrument" in frame.columns else pd.Series("", index=frame.index)
    shifted = close.groupby(instrument, sort=False).shift(-1)
    values = shifted / close - 1
    return pd.Series(values.to_numpy(), index=frame.index, dtype="float64")


def _iso_date(value: object) -> str:
    text = str(value).replace("-", "").replace(".", "")[:8]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


def regression_predictions(
    frame: pd.DataFrame,
    field: str,
    future: pd.Series,
    params: dict,
    *,
    num_threads: int | None = None,
    seed: int = 123,
) -> pd.DataFrame:
    if field not in frame.columns:
        raise ValueError(f"研究行情里没有因子字段 {field}。")
    work = frame.reset_index(drop=True)
    aligned = future.loc[frame.index]
    if isinstance(aligned, pd.DataFrame):
        aligned = aligned.iloc[:, 0]
    label = pd.Series(pd.to_numeric(aligned, errors="coerce").to_numpy(), index=work.index)
    iso_dates = work["date"].map(_iso_date)
    scores = lightgbm_scores(
        pd.to_numeric(work[field], errors="coerce"),
        label,
        iso_dates,
        train_end=str(params["train_end"]),
        valid_end=str(params["valid_end"]),
        learning_rate=float(params["learning_rate"]),
        num_leaves=int(params["num_leaves"]),
        min_data_in_leaf=int(params["min_child_samples"]),
        num_boost_round=int(params["number_of_trees"]),
        early_stopping_rounds=int(params["early_stopping_rounds"]),
        short_valid_rounds=int(params["number_of_trees"]),
        num_threads=num_threads,
        seed=seed,
    )
    mask = scores.notna()
    out = work.loc[mask.to_numpy(), ["date", "instrument"]].copy()
    out["score"] = scores.loc[mask].to_numpy()
    return out.reset_index(drop=True)


def lightgbm_multi_scores(
    features: pd.DataFrame,
    future: pd.Series,
    dates: pd.Series,
    *,
    train_end: str,
    valid_end: str,
    learning_rate: float = 0.05,
    num_leaves: int = 15,
    min_data_in_leaf: int = 20,
    num_boost_round: int = 80,
    early_stopping_rounds: int = 10,
    short_valid_rounds: int | None = None,
    num_threads: int | None = None,
    seed: int | None = None,
) -> pd.Series:
    import lightgbm as lgb

    columns = list(features.columns)
    zscored = pd.DataFrame(
        {
            name: _daily_zscore(pd.to_numeric(features[name], errors="coerce"), dates).to_numpy()
            for name in columns
        },
        index=features.index,
    )
    label = pd.Series(pd.to_numeric(future, errors="coerce").to_numpy(), index=features.index)
    day = pd.Series(dates.to_numpy(), index=features.index)
    train_mask = day <= train_end
    train_ready = train_mask & zscored.notna().all(axis=1) & label.notna()
    valid_mask = (day > train_end) & (day <= valid_end)
    valid_ready = valid_mask & zscored.notna().all(axis=1) & label.notna()
    if int(train_ready.sum()) < 30:
        raise ValueError("训练样本不够")
    train_set = lgb.Dataset(zscored.loc[train_ready, columns], label=label.loc[train_ready])
    params = {
        "objective": "regression",
        "learning_rate": learning_rate,
        "num_leaves": num_leaves,
        "min_data_in_leaf": min_data_in_leaf,
        "verbosity": -1,
    }
    if seed is not None:
        params["seed"] = seed
    if num_threads is not None:
        params["num_threads"] = num_threads
    if int(valid_ready.sum()) >= 10:
        valid_set = lgb.Dataset(zscored.loc[valid_ready, columns], label=label.loc[valid_ready])
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=num_boost_round,
            valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
        )
    else:
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=40 if short_valid_rounds is None else short_valid_rounds,
        )
    scores = pd.Series(np.nan, index=features.index, dtype="float64")
    score_mask = (day > valid_end) & zscored.notna().all(axis=1)
    if not bool(score_mask.any()):
        raise ValueError("测试段没有可预测的样本")
    scores.loc[score_mask] = booster.predict(zscored.loc[score_mask, columns])
    return scores


def multi_regression_predictions(
    frame: pd.DataFrame,
    fields: list[str],
    future: pd.Series,
    params: dict,
    *,
    num_threads: int | None = None,
    seed: int = 123,
) -> pd.DataFrame:
    missing = next((name for name in fields if name not in frame.columns), None)
    if missing is not None:
        raise ValueError(f"研究行情里没有因子字段 {missing}。")
    work = frame.reset_index(drop=True)
    aligned = future.loc[frame.index]
    if isinstance(aligned, pd.DataFrame):
        aligned = aligned.iloc[:, 0]
    label = pd.Series(pd.to_numeric(aligned, errors="coerce").to_numpy(), index=work.index)
    iso_dates = work["date"].map(_iso_date)
    features = work[list(fields)].apply(pd.to_numeric, errors="coerce")
    features.index = work.index
    scores = lightgbm_multi_scores(
        features,
        label,
        iso_dates,
        train_end=str(params["train_end"]),
        valid_end=str(params["valid_end"]),
        learning_rate=float(params["learning_rate"]),
        num_leaves=int(params["num_leaves"]),
        min_data_in_leaf=int(params["min_child_samples"]),
        num_boost_round=int(params["number_of_trees"]),
        early_stopping_rounds=int(params["early_stopping_rounds"]),
        short_valid_rounds=int(params["number_of_trees"]),
        num_threads=num_threads,
        seed=seed,
    )
    mask = scores.notna()
    out = work.loc[mask.to_numpy(), ["date", "instrument"]].copy()
    out["score"] = scores.loc[mask].to_numpy()
    return out.reset_index(drop=True)


def _performance(returns: pd.Series) -> dict[str, float | int]:
    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1
    ann = float((1 + returns).prod() ** (252 / len(returns)) - 1) if len(returns) else 0.0
    vol = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / vol * np.sqrt(252)) if vol and np.isfinite(vol) else 0.0
    return {
        "test_annual_return": ann,
        "test_max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "test_sharpe": sharpe,
        "test_days": int(len(returns)),
    }


def backtest_topk(
    scores: pd.Series,
    future: pd.Series,
    dates: pd.Series,
    instruments: pd.Series,
    *,
    prices: pd.Series | None = None,
    topk: int = TOPK,
    n_drop: int = N_DROP,
) -> dict[str, object]:
    frame = pd.DataFrame(
        {
            "score": scores.to_numpy(),
            "ret": future.to_numpy(),
            "date": dates.to_numpy(),
            "instrument": instruments.to_numpy(),
            "price": np.ones(len(scores)) if prices is None else prices.to_numpy(),
        }
    ).dropna(subset=["score", "ret", "date", "instrument"])
    if frame["date"].nunique() < 5:
        raise ValueError("测试段没有足够交易日")
    held: list[str] = []
    previous: dict[str, float] = {}
    portfolio: list[float] = []
    benchmark: list[float] = []
    day_labels: list[str] = []
    turnovers: list[float] = []
    trades: list[dict[str, object]] = []
    episodes: dict[str, dict[str, float | str]] = {}
    wealth = BOOK_CAPITAL
    for _, day in frame.groupby("date", sort=True):
        score = day.set_index("instrument")["score"]
        ret = day.set_index("instrument")["ret"]
        price = day.set_index("instrument")["price"]
        held = topk_dropout_targets(score, held, topk=topk, n_drop=n_drop)
        names = [code for code in held if code in ret.index]
        if not names:
            continue
        new_weight = {code: RISK_DEGREE / len(names) for code in names}
        cost = _turnover_cost(previous, new_weight)
        day_label = str(day["date"].iloc[0])[:10]
        _record_weight_trades(episodes, trades, previous, new_weight, price, wealth, day_label)
        for code, weight in new_weight.items():
            episode = episodes.get(code)
            if episode is not None:
                episode["hold_pnl"] = float(episode["hold_pnl"]) + weight * float(ret.loc[code]) * wealth
        turnovers.append(_one_way_turnover(previous, new_weight))
        previous = new_weight
        portfolio.append(float(ret.loc[names].mean()) * RISK_DEGREE - cost)
        benchmark.append(float(ret.mean()))
        day_labels.append(day_label)
        wealth *= 1 + portfolio[-1]
    if len(portfolio) < 5:
        raise ValueError("测试段没有足够交易日")
    port = pd.Series(portfolio, dtype="float64")
    bench = pd.Series(benchmark, dtype="float64")
    stats = _performance(port)
    excess = port - bench
    vol = float(excess.std(ddof=1))
    stats["test_information_ratio"] = float(excess.mean() / vol * np.sqrt(252)) if vol else 0.0
    stats["test_return"] = float((1 + port).prod() - 1)
    stats["test_benchmark_return"] = float((1 + bench).prod() - 1)
    stats["test_benchmark_annual_return"] = _performance(bench)["test_annual_return"]
    stats["test_turnover"] = float(np.mean(turnovers)) if turnovers else 0.0
    stats["test_capital_usage"] = RISK_DEGREE
    stats["equity_curve"] = _equity_curve(day_labels, port)
    stats["benchmark_curve"] = _equity_curve(day_labels, bench)
    trades.extend(_open_episodes(episodes, day_labels[-1] if day_labels else ""))
    closed = [row for row in trades if row.get("reason") in {"平仓", "减仓"} and isinstance(row.get("pnl"), float)]
    stats["trades"] = trades
    stats["trade_count"] = len(trades)
    stats["win_rate"] = (
        sum(1 for row in closed if float(row["pnl"]) > 0) / len(closed) if closed else None
    )
    stats["book_capital"] = BOOK_CAPITAL
    stats["topk"] = topk
    stats["n_drop"] = n_drop
    stats["model"] = "LightGBM"
    stats["strategy"] = "TopkDropout"
    return stats


def _record_weight_trades(
    episodes: dict[str, dict[str, float | str]],
    trades: list[dict[str, object]],
    previous: dict[str, float],
    current: dict[str, float],
    prices: pd.Series,
    wealth: float,
    day: str,
) -> None:
    """Record buys and sells when the target weight changes. Drift back to the same weight is not a fill."""
    for code in sorted(set(previous) | set(current)):
        old = float(previous.get(code, 0.0))
        new = float(current.get(code, 0.0))
        if abs(new - old) < 1e-12:
            continue
        price = float(prices.get(code, 1.0) or 1.0)
        if not np.isfinite(price) or price <= 0:
            price = 1.0
        if new > old:
            amount = (new - old) * wealth
            fee = amount * BUY_COST
            episode = episodes.get(code)
            shares = amount / price
            if episode is None:
                episodes[code] = {
                    "buy_date": day,
                    "buy_price": price,
                    "buy_amount": amount,
                    "buy_fee": fee,
                    "shares": shares,
                    "hold_pnl": 0.0,
                    "weight": new,
                }
                reason = "开仓"
            else:
                bought = float(episode["buy_amount"])
                episode["buy_price"] = (float(episode["buy_price"]) * bought + price * amount) / (bought + amount)
                episode["buy_amount"] = bought + amount
                episode["buy_fee"] = float(episode["buy_fee"]) + fee
                episode["shares"] = float(episode["shares"]) + shares
                episode["weight"] = new
                reason = "加仓"
            trades.append(_fill_row(
                day=day, code=code, side="buy", price=price, amount=amount, fee=fee,
                shares=shares, pnl=None, reason=reason,
            ))
            continue
        episode = episodes.get(code)
        if episode is None or old <= 0:
            continue
        fraction = min((old - new) / old, 1.0)
        amount = (old - new) * wealth
        fee = amount * SELL_COST
        pnl = float(episode["hold_pnl"]) * fraction - float(episode["buy_fee"]) * fraction - fee
        entry_date = str(episode["buy_date"])
        entry_price = float(episode["buy_price"])
        entry_amount = float(episode["buy_amount"]) * fraction
        entry_fee = float(episode["buy_fee"]) * fraction
        episode["hold_pnl"] = float(episode["hold_pnl"]) * (1 - fraction)
        episode["buy_amount"] = float(episode["buy_amount"]) * (1 - fraction)
        episode["buy_fee"] = float(episode["buy_fee"]) * (1 - fraction)
        episode["shares"] = float(episode["shares"]) * (1 - fraction)
        episode["weight"] = new
        closed = new <= 1e-12
        if closed:
            episodes.pop(code, None)
        closing = _fill_row(
            day=day, code=code, side="sell", price=price, amount=amount, fee=fee,
            shares=amount / price, pnl=pnl, reason="平仓" if closed else "减仓",
        )
        closing["buy_date"] = entry_date
        closing["buy_price"] = entry_price
        closing["buy_amount"] = entry_amount
        closing["buy_fee"] = entry_fee
        trades.append(closing)


def _open_episodes(episodes: dict[str, dict[str, float | str]], day: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for code in sorted(episodes):
        episode = episodes[code]
        row = _fill_row(
            day=str(episode["buy_date"]),
            code=code,
            side="buy",
            price=float(episode["buy_price"]),
            amount=float(episode["buy_amount"]),
            fee=float(episode["buy_fee"]),
            shares=float(episode["shares"]),
            pnl=float(episode["hold_pnl"]) - float(episode["buy_fee"]),
            reason="期末仍持有",
        )
        row["sell_date"] = None
        row["date"] = day
        rows.append(row)
    return rows


def _fill_row(
    *,
    day: str,
    code: str,
    side: str,
    price: float,
    amount: float,
    fee: float,
    shares: float,
    pnl: float | None,
    reason: str,
) -> dict[str, object]:
    buy = side == "buy"
    sell = side == "sell"
    return {
        "signal_date": day,
        "date": day,
        "instrument": code,
        "side": side,
        "status": "filled",
        "quantity": shares,
        "price": price,
        "amount": amount,
        "fee": fee,
        "buy_date": day if buy else None,
        "sell_date": day if sell else None,
        "buy_price": price if buy else None,
        "sell_price": price if sell else None,
        "buy_amount": amount if buy else None,
        "sell_amount": amount if sell else None,
        "buy_fee": fee if buy else None,
        "sell_fee": fee if sell else None,
        "stamp_tax": 0.0,
        "pnl": pnl,
        "reason": reason,
    }


def _equity_curve(dates: list[str], returns: pd.Series) -> list[dict[str, float | str]]:
    level = 1.0
    curve: list[dict[str, float | str]] = []
    for day, value in zip(dates, returns, strict=True):
        level *= 1 + float(value)
        curve.append({"date": day, "equity": level})
    return curve


def _one_way_turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    bought = 0.0
    for code in set(previous) | set(current):
        bought += max(current.get(code, 0.0) - previous.get(code, 0.0), 0.0)
    return bought


def _turnover_cost(previous: dict[str, float], current: dict[str, float]) -> float:
    cost = 0.0
    for code in set(previous) | set(current):
        delta = current.get(code, 0.0) - previous.get(code, 0.0)
        if delta > 0:
            cost += delta * BUY_COST
        elif delta < 0:
            cost += -delta * SELL_COST
    return cost


def run_factor_strategy(
    factor: pd.Series,
    future: pd.Series,
    dates: pd.Series,
    instruments: pd.Series,
    *,
    prices: pd.Series | None = None,
    train_end: str,
    valid_end: str,
) -> dict[str, object]:
    try:
        scores = lightgbm_scores(factor, future, dates, train_end=train_end, valid_end=valid_end)
        return backtest_topk(scores, future, dates, instruments, prices=prices)
    except Exception as error:  # noqa: BLE001 — keep the factor row and show why the book was not built
        return {"error": str(error), "strategy": "TopkDropout", "model": "LightGBM", "topk": TOPK, "n_drop": N_DROP}
