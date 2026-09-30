"""Small, synchronous, deterministic backtest DAG for the workbench."""
from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from operator import eq, ge, gt, le, lt, ne
from os import cpu_count
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.domain.status import BacktestRunStatus
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.run_lifecycle import transition_backtest_run_status
from quantlab.services.backtest_workbench import BacktestWorkbenchService, _as_bool, normalize_open_ma_gates
from quantlab.services.bucket_equity import attach_segment_curves, filter_rows_to_segment, frame_nbytes
from quantlab.services.compute_budget import (
    ResourceSampler,
    active_resource_prior,
    cap_workers,
    last_resource_sample,
    note_workers,
    persist_run_resources,
    reset_resource_notes,
    reset_resource_prior,
    run_signature,
    set_resource_notes,
    set_resource_prior,
)
from quantlab.services.machine_identity import load_machine_identity
from quantlab.services.result_sync import write_run_manifest
from quantlab.services.model_training import (
    MIN_TRAIN_ROWS,
    MODEL_KINDS,
    RANKER_KINDS,
    attach_label,
    booster_thread_limit,
    enrich_feature_columns,
    booster_thread_count,
    fit_estimator,
    normalize_hyperparams,
    normalize_market_frame,
    predict_estimator,
    rank_label_column,
    resolve_kind,
)
from quantlab.services.portfolio import run_portfolio
from quantlab.services.qlib_strategy import multi_regression_predictions, next_close_return, regression_predictions
from quantlab.services.ranking_metrics import attach_ranking_metrics, capture_predictions, peek_captured
from quantlab.services.settings import resolve_worker_count, total_ram_bytes
from quantlab.services.trade_filters import FIELD_ALIASES, needs_sma200, open_expressions, parse_expr

STEPS = ("snapshot_validation", "model_training", "prediction", "positions", "execution", "metrics")
DEFAULT_WARMUP_CALENDAR_DAYS = 20
MA200_WARMUP_CALENDAR_DAYS = 400
MA200_WINDOW = 200


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _year_fraction(date_from, date_to):
    start_text = str(date_from or "").replace("-", "")[:8]
    end_text = str(date_to or "").replace("-", "")[:8]
    if len(start_text) != 8 or len(end_text) != 8:
        return None
    try:
        start = datetime.strptime(start_text, "%Y%m%d")
        end = datetime.strptime(end_text, "%Y%m%d")
    except ValueError:
        return None
    days = max((end - start).days, 1)
    return days / 365.25


def _norm_yyyymmdd(value):
    return str(value or "").replace("-", "")[:8]


def uses_stock_ma200(config):
    for part in ("train", "test"):
        filt = (config.get(part) or {}).get("filter") or {}
        if filt.get("close_gt_ma200") or filt.get("close_lt_ma200"):
            return True
    try:
        return needs_sma200(open_expressions(config or {}))
    except ValueError:
        return False


def uses_close_gt_ma200(config):
    return uses_stock_ma200(config)


def load_warmup_start(start, config=None):
    days = MA200_WARMUP_CALENDAR_DAYS if uses_stock_ma200(config or {}) else DEFAULT_WARMUP_CALENDAR_DAYS
    return (datetime.strptime(start, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")


def walk_forward_mode(config):
    raw = str(config.get("walk_forward") or "").strip().lower()
    if raw in {"monthly", "lookback", "rolling", "expanding", "expanding_monthly"}:
        return "rolling"
    return "once"


def _positive_period_months(raw, default, label):
    if raw in {None, ""}:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label}必须是正整数。") from error
    if value < 1:
        raise ValueError(f"{label}必须是正整数。")
    return value


def train_period_months(config):
    raw = config.get("train_period_months")
    if raw in {None, ""}:
        raw = config.get("train_lookback_months")
    return _positive_period_months(raw, 12, "训练周期")


def normalize_test_period_months(config):
    raw = config.get("test_period_months")
    if raw in {None, ""}:
        default = 1 if walk_forward_mode(config) == "rolling" else 3
        value = default
    else:
        value = _positive_period_months(raw, 3, "回测周期")
    if value > train_period_months(config):
        raise ValueError("回测周期不能长于训练周期")
    return value


def unique_dates(values):
    return sorted({_norm_yyyymmdd(value) for value in list(values) if len(_norm_yyyymmdd(value)) == 8})


def _calendar_index(calendar, day):
    for index, item in enumerate(calendar):
        if item >= day:
            return index
    return len(calendar)


def shift_trading_date(calendar, day, steps):
    index = _calendar_index(calendar, day) + steps
    if index < 0 or index >= len(calendar):
        return None
    return calendar[index]


def shift_calendar_months(day, months):
    text = _norm_yyyymmdd(day)
    year = int(text[:4])
    month = int(text[4:6])
    date = int(text[6:8])
    month += months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    date = min(date, monthrange(year, month)[1])
    return f"{year:04d}{month:02d}{date:02d}"


def train_lookback_months(config):
    raw = config.get("train_lookback_months")
    if raw in {None, "", 0, "0"}:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value > 0:
        return value
    return None


def _prev_calendar_day(day):
    text = _norm_yyyymmdd(day)
    return (datetime.strptime(text, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")


def _first_trading_on_or_after(calendar, day):
    text = _norm_yyyymmdd(day)
    for item in calendar:
        if item >= text:
            return item
    return None


def _last_trading_on_or_before(calendar, day):
    text = _norm_yyyymmdd(day)
    previous = [item for item in calendar if item <= text]
    return previous[-1] if previous else None


def period_train_folds(*, roll_start, roll_end, calendar, holding_days, train_period_months, test_period_months):
    start = _norm_yyyymmdd(roll_start)
    end = _norm_yyyymmdd(roll_end)
    days = unique_dates(calendar)
    steps = -(1 + max(int(holding_days or 1), 1))
    train_m = max(int(train_period_months), 1)
    test_m = max(int(test_period_months), 1)
    folds = []
    test_from_cal = shift_calendar_months(start, train_m)
    prev_predict_to = None
    while test_from_cal <= end:
        predict_from = _first_trading_on_or_after(days, test_from_cal)
        if predict_from is None or predict_from > end:
            return folds
        test_to_excl = shift_calendar_months(test_from_cal, test_m)
        predict_dates = [item for item in days if predict_from <= item < test_to_excl and item <= end]
        if not predict_dates:
            return folds
        predict_to = predict_dates[-1]
        label_cutoff = shift_trading_date(days, predict_from, steps)
        if label_cutoff is None:
            test_from_cal = test_to_excl
            continue
        if prev_predict_to is None:
            train_end = _last_trading_on_or_before(days, _prev_calendar_day(test_from_cal)) or label_cutoff
            train_start = _prev_calendar_day(start)
        else:
            train_end = prev_predict_to
            train_start = shift_calendar_months(train_end, -train_m)
        folds.append(
            {
                "month": predict_from[:6],
                "train_start": train_start,
                "train_end": train_end,
                "label_cutoff": label_cutoff,
                "predict_from": predict_from,
                "predict_to": predict_to,
                "predict_dates": predict_dates,
            }
        )
        prev_predict_to = predict_to
        test_from_cal = test_to_excl
    return folds


def monthly_train_folds(test_dates, calendar, holding_days, lookback_months=None):
    dates = unique_dates(test_dates)
    if not dates:
        return []
    train_m = int(lookback_months or 12)
    first_month = f"{dates[0][:6]}01"
    return period_train_folds(
        roll_start=shift_calendar_months(first_month, -train_m),
        roll_end=dates[-1],
        calendar=calendar,
        holding_days=holding_days,
        train_period_months=train_m,
        test_period_months=1,
    )


def _select_benchmark_rows(frame, code, date_from, date_to):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "price"])
    code_col = next((name for name in ("instrument", "ts_code") if name in frame.columns), None)
    date_col = next((name for name in ("date", "trade_date") if name in frame.columns), None)
    price_col = next((name for name in ("hfq_close", "close") if name in frame.columns), None)
    if not code_col or not date_col or not price_col:
        return pd.DataFrame(columns=["date", "price"])
    dates = frame[date_col].astype(str).str.replace("-", "", regex=False).str[:8]
    picked = frame.loc[(frame[code_col].astype(str) == code) & (dates >= date_from) & (dates <= date_to)]
    if picked.empty:
        return pd.DataFrame(columns=["date", "price"])
    out = pd.DataFrame(
        {
            "date": dates.loc[picked.index].to_numpy(),
            "price": pd.to_numeric(picked[price_col], errors="coerce").to_numpy(),
        }
    )
    return out.loc[out["price"] > 0].drop_duplicates("date").sort_values("date")


def _index_daily_paths(raw_root, code):
    directory = Path(raw_root) / "index_daily"
    if not directory.is_dir():
        return []
    slug = code.replace(".", "_")
    preferred = [
        directory / f"index_daily_{slug}.parquet",
        directory / f"{slug}.parquet",
    ]
    rest = [path for path in sorted(directory.glob("*.parquet")) if path not in preferred]
    return [path for path in preferred + rest if path.is_file()]


def _load_index_daily(raw_root, code):
    if raw_root is None:
        return pd.DataFrame()
    for path in _index_daily_paths(Path(raw_root), code):
        try:
            frame = pq.read_table(path).to_pandas()
        except (OSError, ValueError):
            continue
        if not _select_benchmark_rows(frame, code, "00000000", "99999999").empty:
            return frame
    return pd.DataFrame()


def _benchmark_prices(frame, config, raw_root=None):
    code = str(config.get("benchmark") or "").strip()
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    date_from = _norm_yyyymmdd(test.get("date_from"))
    date_to = _norm_yyyymmdd(test.get("date_to"))
    if not code or len(date_from) != 8 or len(date_to) != 8:
        return pd.DataFrame(columns=["date", "price"])
    rows = _select_benchmark_rows(frame if frame is not None else pd.DataFrame(), code, date_from, date_to)
    if rows.empty:
        rows = _select_benchmark_rows(_load_index_daily(raw_root, code), code, date_from, date_to)
    return rows


def _benchmark_curve(frame, config, start_equity, raw_root=None, align_dates=None):
    rows = _benchmark_prices(frame, config, raw_root)
    if rows.empty or start_equity <= 0:
        return []
    first = float(rows.iloc[0]["price"] or 0)
    if first <= 0:
        return []
    by_date = {
        str(date): float(price)
        for date, price in zip(rows["date"].tolist(), rows["price"].tolist(), strict=False)
    }
    dates = [str(date) for date in (align_dates or rows["date"].tolist())]
    last_px = None
    curve = []
    for date in dates:
        px = by_date.get(date)
        if px:
            last_px = px
        if last_px is None:
            continue
        curve.append({"date": date, "equity": start_equity * last_px / first})
    return curve


def _benchmark_return(frame, config, raw_root=None):
    rows = _benchmark_prices(frame, config, raw_root)
    if len(rows) < 2:
        return None
    start_px = float(rows.iloc[0]["price"] or 0)
    end_px = float(rows.iloc[-1]["price"] or 0)
    if start_px <= 0:
        return None
    return end_px / start_px - 1.0


def index_trend_open_dates(frame, config, raw_root=None, *, code: str, window: int):
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    date_from = _norm_yyyymmdd(test.get("date_from"))
    date_to = _norm_yyyymmdd(test.get("date_to"))
    if len(date_from) != 8 or len(date_to) != 8 or int(window) < 1:
        return set()
    code = str(code or "").strip()
    rows = _select_benchmark_rows(frame if frame is not None else pd.DataFrame(), code, "00000000", "99999999")
    loaded = _select_benchmark_rows(_load_index_daily(raw_root, code), code, "00000000", "99999999")
    if len(loaded) > len(rows):
        rows = loaded
    if rows.empty:
        raise ValueError(f"缺少指数日线 {code}，请先在数据中心下载。")
    prices = pd.to_numeric(rows.set_index("date")["price"], errors="coerce")
    mean = prices.rolling(int(window), min_periods=int(window)).mean()
    above = prices > mean
    return {
        str(day)
        for day, flag in above.items()
        if bool(flag) and date_from <= str(day) <= date_to
    }


def benchmark_trend_open_dates(frame, config, raw_root=None, window=MA200_WINDOW):
    code = str(config.get("benchmark") or "").strip() or "000300.SH"
    return index_trend_open_dates(frame, config, raw_root, code=code, window=window)


def resolve_open_dates(frame, config, raw_root=None):
    gates_on = False
    allowed = None
    if _as_bool((config or {}).get("open_when_benchmark_gt_ma200"), False):
        gates_on = True
        dates = benchmark_trend_open_dates(frame, config, raw_root)
        allowed = dates if allowed is None else allowed.intersection(dates)
    for item in normalize_open_ma_gates((config or {}).get("open_ma_gates")):
        gates_on = True
        dates = index_trend_open_dates(frame, config, raw_root, code=item["code"], window=item["window"])
        allowed = dates if allowed is None else allowed.intersection(dates)
    if not gates_on:
        return None
    return allowed if allowed is not None else set()


def resolve_membership_open_allow(frame, config, raw_root=None):
    if not _as_bool((config or {}).get("open_gate_by_membership"), False):
        return None
    if raw_root is None:
        raise ValueError("按成分开仓闸需要原始数据目录")
    raw_root = Path(raw_root)
    try:
        window = int((config or {}).get("membership_ma_window") or MA200_WINDOW)
    except (TypeError, ValueError):
        raise ValueError("membership_ma_window 要填整数") from None
    from quantlab.services.index_membership import load_index_weight, members_on, parse_universe_index_codes

    index_codes = parse_universe_index_codes((config or {}).get("universe_index_codes"))
    weights = load_index_weight(raw_root, index_codes)
    present = {str(code) for code in (weights["index_code"].tolist() if not weights.empty else [])}
    missing = [code for code in index_codes if code not in present]
    if weights.empty or missing:
        raise ValueError(f"缺少股票池成分权重：{', '.join(missing) if missing else '（空）'}，请先在数据中心下载。")
    open_days = {
        code: index_trend_open_dates(frame, config, raw_root, code=code, window=window) for code in index_codes
    }
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    date_from = _norm_yyyymmdd(test.get("date_from"))
    date_to = _norm_yyyymmdd(test.get("date_to"))
    market: set[str] = set()
    if frame is not None and not getattr(frame, "empty", True) and "date" in frame.columns:
        market.update(_norm_yyyymmdd(value) for value in frame["date"].tolist())
    for code in index_codes:
        rows = _select_benchmark_rows(_load_index_daily(raw_root, code), code, date_from, date_to)
        if not rows.empty:
            market.update(str(day) for day in rows["date"].tolist())
        market.update(str(day) for day in open_days[code])
    allow: dict[str, list[str]] = {}
    cache: dict[str, dict[str, set[str]]] = {}
    for day in sorted(item for item in market if len(item) == 8 and date_from <= item <= date_to):
        by_index = cache.get(day)
        if by_index is None:
            by_index = {code: members_on(weights, code, day) for code in index_codes}
            cache[day] = by_index
        names: set[str] = set()
        # Prefer earlier codes on overlap (CSI800: 300 before 500), even if earlier MA is closed.
        claimed: set[str] = set()
        for code in index_codes:
            members = by_index.get(code) or set()
            if day in open_days[code]:
                names |= members - claimed
            claimed |= members
        if names:
            allow[day] = sorted(names)
    return allow


def fill_benchmark_metrics(metrics, config, *, frame=None, raw_root=None):
    out = dict(metrics)
    equity = [row for row in (out.get("equity_curve") or []) if isinstance(row, dict) and row.get("date")]
    start = float(equity[0]["equity"]) if equity else float(config.get("initial_capital") or 0) or 1.0
    align = [str(row["date"]) for row in equity] or None
    if not out.get("benchmark_curve"):
        out["benchmark_curve"] = _benchmark_curve(frame, config, start, raw_root=raw_root, align_dates=align)
    if out.get("benchmark_return") is None:
        out["benchmark_return"] = _benchmark_return(frame, config, raw_root=raw_root)
    if out.get("excess_return") is None and out.get("return") is not None and out.get("benchmark_return") is not None:
        try:
            out["excess_return"] = float(out["return"]) - float(out["benchmark_return"])
            return out
        except (TypeError, ValueError):
            return out
    return out


def _performance_metrics(trades, config, frame, equity_curve=None, raw_root=None):
    filled = [trade for trade in trades if trade.get("status") == "filled"]
    capital = float(config.get("initial_capital") or 0) or 1.0
    curve = [row for row in (equity_curve or []) if isinstance(row, dict) and row.get("date")]
    win_rate = None
    if filled:
        wins = sum(1 for trade in filled if float(trade.get("pnl") or 0) > 0)
        win_rate = wins / len(filled)
    if curve:
        start = float(curve[0].get("equity") or capital) or capital
        equity_end = float(curve[-1].get("equity") or start)
        ret = equity_end / start - 1.0 if start else 0.0
        peak = start
        max_dd = 0.0
        daily_rets = []
        previous = start
        for row in curve[1:]:
            equity = float(row.get("equity") or previous)
            if previous > 0:
                daily_rets.append(equity / previous - 1.0)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = min(max_dd, equity / peak - 1.0)
            previous = equity
        sharpe = None
        if len(daily_rets) >= 2:
            series = pd.Series(daily_rets, dtype="float64")
            std = float(series.std(ddof=1) or 0.0)
            if std > 0:
                sharpe = float(series.mean() / std * (252 ** 0.5))
        usage_vals = []
        for row in curve:
            equity = float(row.get("equity") or 0)
            if not equity > 0:
                continue
            usage_vals.append(float(row.get("invested") or 0) / equity)
        capital_usage = float(sum(usage_vals) / len(usage_vals)) if usage_vals else 0.0
        amounts = {}
        for trade in filled:
            buy_day = str(trade.get("buy_date") or "")
            sell_day = str(trade.get("sell_date") or "")
            if buy_day:
                amounts[buy_day] = amounts.get(buy_day, 0.0) + abs(float(trade.get("buy_amount") or 0))
            if not sell_day:
                continue
            amounts[sell_day] = amounts.get(sell_day, 0.0) + abs(float(trade.get("sell_amount") or 0))
        equity_by_day = {str(row["date"]): float(row.get("equity") or 0) for row in curve}
        turns = []
        for day, amount in amounts.items():
            equity = equity_by_day.get(day) or 0.0
            if not equity > 0:
                continue
            turns.append(amount / equity)
        turnover = float(sum(turns) / len(turns)) if turns else 0.0
        max_drawdown = max_dd if len(curve) > 1 else 0.0
    else:
        pnl = sum(float(trade.get("pnl") or 0) for trade in filled)
        ret = pnl / capital
        by_day = {}
        for trade in filled:
            day = str(trade.get("sell_date") or trade.get("buy_date") or "")
            if not day:
                continue
            by_day[day] = by_day.get(day, 0.0) + float(trade.get("pnl") or 0)
        days = sorted(by_day)
        equity = capital
        peak = capital
        max_drawdown = 0.0
        daily_rets = []
        for day in days:
            equity += by_day[day]
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = min(max_drawdown, equity / peak - 1.0)
            daily_rets.append(by_day[day] / capital)
        sharpe = None
        if len(daily_rets) >= 2:
            series = pd.Series(daily_rets, dtype="float64")
            std = float(series.std(ddof=1) or 0.0)
            if std > 0:
                sharpe = float(series.mean() / std * (252 ** 0.5))
        if not days:
            max_drawdown = None
        turnover = 0.0
        capital_usage = 0.0
    annual = None
    years = _year_fraction((config.get("test") or {}).get("date_from"), (config.get("test") or {}).get("date_to"))
    if years and ret > -1:
        annual = float((1.0 + ret) ** (1.0 / years) - 1.0)
    benchmark_return = _benchmark_return(frame, config, raw_root=raw_root)
    excess = None if benchmark_return is None else ret - benchmark_return
    return {
        "trade_count": len(filled),
        "unfilled_count": len(trades) - len(filled),
        "pnl": float(curve[-1]["equity"]) - capital if curve else sum(float(trade.get("pnl") or 0) for trade in filled),
        "return": ret,
        "annual_return": annual,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "benchmark_return": benchmark_return,
        "excess_return": excess,
        "turnover": turnover,
        "capital_usage": capital_usage,
    }


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1048576):
            digest.update(chunk)
    return digest.hexdigest()


def factor_rank_direction(config):
    refs = config.get("factor_versions") or []
    raw = str(refs[0].get("direction") or "") if refs else ""
    if not raw:
        raw = str((config.get("hyperparameters") or {}).get("factor_direction") or "")
    text = raw.strip().lower()
    if text in {"-1", "reverse", "negative", "reversal"}:
        return "negative"
    return "positive"


def _signed_factor_direction(raw) -> str:
    return factor_rank_direction({"factor_versions": [{"direction": raw}]})


def _factor_rank_refs(field, direction="positive", factor_versions=None) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(factor_versions, list):
        for item in factor_versions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("field") or str(item.get("factor_id") or "").removeprefix("factor_") or "").strip()
            if not name:
                continue
            refs.append((name, _signed_factor_direction(item.get("direction"))))
    if not refs:
        refs.append((field, _signed_factor_direction(direction)))
    return refs


def factor_rank_predictions(test, field, direction="positive", factor_versions=None):
    refs = _factor_rank_refs(field, direction=direction, factor_versions=factor_versions)
    if len(refs) == 1:
        name, signed = refs[0]
        predictions = test[["date", "instrument", name]].rename(columns={name: "score"}).dropna()
        if signed == "negative":
            predictions = predictions.copy()
            predictions["score"] = -pd.to_numeric(predictions["score"], errors="coerce")
            predictions = predictions.dropna(subset=["score"])
        return predictions
    dates = test["date"].astype(str)
    pieces = []
    for name, signed in refs:
        if name not in test.columns:
            raise ValueError(f"研究行情里没有因子字段 {name}。")
        numeric = pd.to_numeric(test[name], errors="coerce")
        ranked = numeric.groupby(dates, sort=False).rank(pct=True)
        if signed == "negative":
            ranked = 1.0 - ranked
        pieces.append(ranked)
    score = sum(pieces) / len(pieces)
    out = test[["date", "instrument"]].copy()
    out["score"] = score.to_numpy()
    return out.dropna(subset=["score"])


def _filter_core(frame, section, notes=None):
    date_from = str(section["date_from"]).replace("-", "")[:8]
    date_to = str(section["date_to"]).replace("-", "")[:8]
    filt = section.get("filter") if isinstance(section.get("filter"), dict) else {}
    source = attach_sma(frame, MA200_WINDOW) if filt.get("close_gt_ma200") or filt.get("close_lt_ma200") else frame
    result = source.loc[(source["date"] >= date_from) & (source["date"] <= date_to)].copy()
    if filt.get("st_status") == 0 and "st_status" in result:
        result = result.loc[result["st_status"] == 0]
    if filt.get("suspended") is False and "suspended" in result:
        result = result.loc[result["suspended"] == False]  # noqa: E712
    if filt.get("close_gt_low", True):
        if {"close", "low"} <= set(result.columns):
            close = pd.to_numeric(result["close"], errors="coerce")
            low = pd.to_numeric(result["low"], errors="coerce")
            result = result.loc[close > low]
        elif notes is not None:
            notes.append("缺少 close 或 low，跳过收盘>最低价过滤")
    if filt.get("skip_limit_close", True):
        needed = {"up_limit", "close", "down_limit"}
        if needed <= set(result.columns):
            close = pd.to_numeric(result["close"], errors="coerce")
            up = pd.to_numeric(result["up_limit"], errors="coerce")
            down = pd.to_numeric(result["down_limit"], errors="coerce")
            result = result.loc[(close < up) & (close > down)]
        elif notes is not None:
            notes.append("缺少涨跌停价，跳过收盘未涨跌停过滤")
    if filt.get("close_gt_ma200") or filt.get("close_lt_ma200"):
        price_name = "hfq_close" if "hfq_close" in result.columns else "close"
        ma_name = f"sma_{MA200_WINDOW}"
        if price_name in result.columns and ma_name in result.columns:
            price = pd.to_numeric(result[price_name], errors="coerce")
            ma = pd.to_numeric(result[ma_name], errors="coerce")
            if filt.get("close_gt_ma200"):
                result = result.loc[price > ma]
            if filt.get("close_lt_ma200"):
                result = result.loc[price < ma]
        elif notes is not None:
            notes.append("缺少后复权收盘或均线，跳过200日均线过滤")
    cap_limit = filt.get("max_float_market_cap")
    if cap_limit not in {None, "", False}:
        try:
            limit = float(cap_limit)
        except (TypeError, ValueError):
            limit = 0.0
        if limit > 0:
            cap = None
            if "float_market_cap" in result.columns:
                cap = pd.to_numeric(result["float_market_cap"], errors="coerce")
            elif "circ_mv" in result.columns:
                cap = pd.to_numeric(result["circ_mv"], errors="coerce") * 10000.0
            if cap is not None:
                result = result.loc[cap < limit]
                return result
            if notes is not None:
                notes.append("缺少流通市值，跳过市值上限过滤")
    return result


def research_frame_date_span(config):
    train, test = config["train"], config["test"]

    def compact(section, key):
        return str(section[key]).replace("-", "")[:8]

    test_from = compact(test, "date_from")
    test_to = compact(test, "date_to")
    nested = config.get("model") if isinstance(config.get("model"), dict) else {}
    kind = resolve_kind(config.get("kind") or nested.get("kind"), config.get("hyperparameters"))
    if kind == "factor_rank":
        return test_from, test_to
    train_from = compact(train, "date_from")
    train_to = compact(train, "date_to")
    return min(train_from, test_from), max(train_to, test_to)


def _load_frame_core(self, path, config):
    start, end = research_frame_date_span(config)
    names = set(pq.ParquetFile(path).schema_arrow.names)
    date_field = "trade_date" if "trade_date" in names else "date"
    warmup = load_warmup_start(start, config)
    horizon = (datetime.strptime(end, "%Y%m%d") + timedelta(days=10)).strftime("%Y%m%d")
    table = ds.dataset(path, format="parquet").to_table(
        filter=(pc.field(date_field) >= warmup) & (pc.field(date_field) <= horizon)
    )
    if table.num_rows == 0:
        raise ValueError("回测区间里没有行情。")
    return enrich_feature_columns(normalize_market_frame(table.to_pandas()), config.get("factor_versions") or [])


def _execute_core(self, run_id):
    run, path, registered = self._path_and_row(run_id)
    if run["status"] == "failed":
        self._requeue_failed(run_id)
        run, path, registered = self._path_and_row(run_id)
    if run["status"] not in {"queued", "failed"}:
        raise ValueError("这条回测已经在运行或已经完成，不能重复执行。")
    self._prepare_steps(run_id)
    try:
        with self.database.transaction() as connection:
            next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.RUNNING)
            connection.execute(
                "UPDATE backtest_runs SET status=? WHERE run_id=? AND status IN ('queued', 'failed')",
                (next_status, run_id),
            )
            from quantlab.services.backtest_summary import refresh_backtest_summary

            refresh_backtest_summary(connection, run_id)
        self._step(run_id, 1, "running")
        if not path.is_file():
            raise ValueError("找不到研究行情文件。")
        actual_rows = self._snapshot_rows(path)
        if actual_rows <= 0:
            raise ValueError("研究行情文件是空的。")
        live_current = str(run.get("dataset_version_id") or "") == "current"
        if not live_current:
            actual_hash = _sha256(path)
            stored_hash = str(registered["manifest_hash"] or "").replace("sha256:", "")
            if stored_hash and stored_hash != actual_hash:
                raise ValueError("行情文件已经更新，和登记时对不上。")
            if registered["row_count"] is not None and int(registered["row_count"]) != actual_rows:
                raise ValueError("行情行数和登记时不一致。")
        self._step(run_id, 1, "completed")
        self._step(run_id, 2, "running")
        config = run["config"]
        field = self._factor_field(config)
        frame = self._load_frame(path, config)
        note_workers(frame_nbytes=frame_nbytes(frame))
        if uses_stock_ma200(config):
            attach_sma(frame)
        filter_notes = []
        train = self._filter(frame, config["train"], filter_notes)
        raw_fields = [
            str(item.get("field") or str(item.get("factor_id", "")).removeprefix("factor_"))
            for item in (config.get("factor_versions") or [])
            if item.get("field") or item.get("factor_id")
        ]
        requested_fields = list(dict.fromkeys(name.strip() for name in raw_fields if name.strip()))
        feature_fields = list(raw_fields)
        if field not in feature_fields:
            feature_fields.insert(0, field)
        feature_fields = [name for name in dict.fromkeys(feature_fields) if name in train.columns]
        if not feature_fields:
            raise ValueError(f"研究行情里没有因子字段 {field}。")
        kind = resolve_kind(
            config.get("kind") or (config.get("model") or {}).get("kind"),
            config.get("hyperparameters"),
        )
        params = normalize_hyperparams(config.get("hyperparameters"), kind=kind)
        holding_days = max(int(config.get("holding_days") or 2), 1)
        model = {
            "factor_field": field,
            "train_rows": int(len(train)),
            "method": MODEL_KINDS.get(kind, {}).get("method", kind),
            "kind": kind,
            "hyperparameters": params,
            "label": {"definition": f"t+1 open -> t+{1 + holding_days} close", "holding_days": holding_days},
        }
        if filter_notes:
            model["filter_notes"] = list(dict.fromkeys(filter_notes))
        booster = None
        predictions = None
        test = filter_rows_to_segment(frame, self._filter(frame, config["test"], filter_notes), config)
        rolling = walk_forward_mode(config) == "rolling" and kind != "factor_rank"
        if kind == "qlib_lgb_regression":
            if len(feature_fields) != 1:
                raise ValueError("Qlib 回归打分这一次只训练一个因子。")
            span_from, span_to = research_frame_date_span(config)
            scoring_section = {
                "date_from": span_from,
                "date_to": span_to,
                "filter": dict((config.get("test") or {}).get("filter") or {}),
            }
            ordered = frame.sort_values(["instrument", "date"])
            future = next_close_return(ordered)
            filtered = self._filter(ordered, scoring_section, filter_notes)
            predictions = regression_predictions(
                filtered,
                feature_fields[0],
                future.loc[filtered.index],
                params,
                num_threads=booster_thread_count(1),
                seed=123,
            )
            model["method"] = "qlib_lightgbm_regression"
            model["label"] = {"definition": "close -> next close", "portfolio_holding_days": holding_days}
        elif kind == "qlib_lgb_multi":
            if len(requested_fields) < 2:
                raise ValueError("多因子 Qlib 打分至少要两个因子。")
            span_from, span_to = research_frame_date_span(config)
            scoring_section = {
                "date_from": span_from,
                "date_to": span_to,
                "filter": dict((config.get("test") or {}).get("filter") or {}),
            }
            ordered = frame.sort_values(["instrument", "date"])
            future = next_close_return(ordered)
            filtered = self._filter(ordered, scoring_section, filter_notes)
            predictions = multi_regression_predictions(
                filtered,
                requested_fields,
                future.loc[filtered.index],
                params,
                num_threads=booster_thread_count(1),
                seed=123,
            )
            model["method"] = "qlib_lightgbm_multi_regression"
            model["label"] = {"definition": "close -> next close", "portfolio_holding_days": holding_days}
        elif kind == "factor_rank":
            model["method"] = "deterministic_factor_rank_proxy"
            model["factor_direction"] = factor_rank_direction(config)
        elif rolling:
            predictions, labeled, fold_rows = self._rolling_predictions(
                frame=frame,
                train=train,
                test=test,
                config=config,
                kind=kind,
                params=params,
                feature_fields=feature_fields,
                holding_days=holding_days,
                filter_notes=filter_notes,
            )
            model = {
                **model,
                "feature_fields": feature_fields,
                "train_rows": int(fold_rows[-1]["train_rows"]) if fold_rows else 0,
                "method": MODEL_KINDS.get(kind, {}).get("method", kind),
                "label_open_column": labeled.attrs.get("label_open_column"),
                "label_close_column": labeled.attrs.get("label_close_column"),
                "walk_forward": {
                    "mode": "rolling",
                    "train_period_months": train_period_months(config),
                    "test_period_months": normalize_test_period_months(config),
                    "folds": fold_rows,
                },
            }
            if kind in RANKER_KINDS:
                model["ranker"] = {
                    "backend": "lgb.train" if kind == "lightgbm_tree" else "xgb.train",
                    "seed": 123,
                    "label_gain": params.get("label_gain") or "linear_0_19",
                    "metric": params.get("metric") or "ndcg",
                    "ndcg_eval_at": params.get("ndcg_eval_at"),
                }
                if kind == "lightgbm_tree":
                    model["ranker"]["ndcg_discount_base"] = params.get("ndcg_discount_base")
        else:
            labeled = attach_label(train, holding_days=holding_days)
            target_col = rank_label_column(kind)
            if target_col not in labeled.columns:
                raise ValueError("训练标签没有算出来。")
            usable = labeled.dropna(subset=feature_fields + [target_col])
            if len(usable) < MIN_TRAIN_ROWS:
                raise ValueError(f"训练样本不足（{len(usable)} 行，至少 {MIN_TRAIN_ROWS} 行）。")
            booster = fit_estimator(
                kind,
                usable[feature_fields],
                usable[target_col],
                params,
                dates=usable["date"] if "date" in usable.columns else None,
            )
            if booster is None:
                raise ValueError("模型没有训练出来。")
            model = {
                **model,
                "feature_fields": feature_fields,
                "train_rows": int(len(usable)),
                "method": MODEL_KINDS.get(kind, {}).get("method", kind),
                "label_open_column": labeled.attrs.get("label_open_column"),
                "label_close_column": labeled.attrs.get("label_close_column"),
            }
            if kind in RANKER_KINDS:
                model["ranker"] = {
                    "backend": "lgb.train" if kind == "lightgbm_tree" else "xgb.train",
                    "seed": 123,
                    "label_gain": params.get("label_gain") or "linear_0_19",
                    "metric": params.get("metric") or "ndcg",
                    "ndcg_eval_at": params.get("ndcg_eval_at"),
                }
                if kind == "lightgbm_tree":
                    model["ranker"]["ndcg_discount_base"] = params.get("ndcg_discount_base")
        self._step(run_id, 2, "completed")
        self._step(run_id, 3, "running")
        if kind == "factor_rank":
            direction = factor_rank_direction(config)
            predictions = factor_rank_predictions(
                test,
                field,
                direction,
                factor_versions=config.get("factor_versions"),
            )
            model["factor_direction"] = direction
            refs = _factor_rank_refs(field, direction=direction, factor_versions=config.get("factor_versions"))
            if len(refs) > 1:
                model["factor_blend"] = "equal_cs_rank"
                model["factor_legs"] = [{"field": name, "direction": signed} for name, signed in refs]
        elif predictions is None:
            if booster is None or not all(name in test.columns for name in feature_fields):
                raise ValueError("模型没有训练出来。")
            scored = test.dropna(subset=feature_fields).copy()
            scored["score"] = predict_estimator(kind, booster, scored[feature_fields])
            predictions = scored[["date", "instrument", "score"]]
        self._step(run_id, 3, "completed")
        self._step(run_id, 4, "running")
        portfolio_config = dict(config)
        if _as_bool(portfolio_config.get("open_gate_by_membership"), False):
            allow = resolve_membership_open_allow(frame, portfolio_config, raw_root=self.settings.raw_root)
            portfolio_config["membership_open_allow"] = allow or {}
            model["membership_open_gate"] = {
                "enabled": True,
                "window": int(portfolio_config.get("membership_ma_window") or MA200_WINDOW),
                "open_days": len(allow or {}),
            }
        else:
            allowed = resolve_open_dates(frame, portfolio_config, raw_root=self.settings.raw_root)
            if allowed is not None:
                portfolio_config["benchmark_open_dates"] = sorted(allowed)
                extras = normalize_open_ma_gates(portfolio_config.get("open_ma_gates"))
                model["benchmark_open_gate"] = {
                    "enabled": True,
                    "code": str(portfolio_config.get("benchmark") or "000300.SH"),
                    "window": MA200_WINDOW,
                    "open_days": len(allowed),
                    "year_line": _as_bool(portfolio_config.get("open_when_benchmark_gt_ma200"), False),
                    "extra_gates": extras,
                }
        trades, equity_curve = run_portfolio(frame, predictions, portfolio_config)
        self._step(run_id, 4, "completed")
        self._step(run_id, 5, "running")
        self._step(run_id, 5, "completed")
        self._step(run_id, 6, "running")
        start_equity = float(equity_curve[0]["equity"]) if equity_curve else float(config.get("initial_capital") or 0)
        metrics = {
            **_performance_metrics(trades, portfolio_config, frame, equity_curve, raw_root=self.settings.raw_root),
            "model": model,
            "equity_curve": equity_curve,
            "benchmark_curve": _benchmark_curve(
                frame,
                config,
                start_equity or 1.0,
                raw_root=self.settings.raw_root,
                align_dates=[str(row["date"]) for row in equity_curve],
            ),
        }
        output_dir = self.settings.runtime_root / "results" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        trades_path = output_dir / "trades.json"
        metrics_path = output_dir / "metrics.json"
        curve_path = output_dir / "equity_curve.json"
        trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        curve_path.write_text(json.dumps(equity_curve, ensure_ascii=False, indent=2), encoding="utf-8")
        trade_artifact = self.artifacts.register(
            run_id=run_id, path=trades_path, display_name="成交明细", artifact_role="trades"
        )
        metrics_artifact = self.artifacts.register(
            run_id=run_id, path=metrics_path, display_name="回测指标", artifact_role="metrics"
        )
        curve_artifact = self.artifacts.register(
            run_id=run_id, path=curve_path, display_name="权益曲线", artifact_role="equity_curve"
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE backtest_steps SET artifact_ids_json=? WHERE run_id=? AND ordinal=6",
                (
                    json.dumps(
                        [trade_artifact.artifact_id, metrics_artifact.artifact_id, curve_artifact.artifact_id]
                    ),
                    run_id,
                ),
            )
        self._step(run_id, 6, "completed")
        with self.database.transaction() as connection:
            next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.COMPLETED)
            connection.execute(
                "UPDATE backtest_runs SET status=?, metrics_json=? WHERE run_id=?",
                (next_status, json.dumps(metrics, ensure_ascii=False, sort_keys=True), run_id),
            )
            connection.execute(
                "UPDATE run_registry SET finished_at=? WHERE run_id=?",
                (_now(), run_id),
            )
            from quantlab.services.backtest_summary import refresh_backtest_summary

            refresh_backtest_summary(connection, run_id)
    except Exception as error:
        failed_ordinal = next(
            (i for i in range(1, len(STEPS) + 1) if self._step_status(run_id, i) in {"pending", "running"}),
            1,
        )
        self._step(run_id, failed_ordinal, "failed", str(error))
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE backtest_steps SET status='skipped', finished_at=? WHERE run_id=? AND ordinal>? AND status='pending'",
                (_now(), run_id, failed_ordinal),
            )
            next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.FAILED)
            connection.execute(
                "UPDATE backtest_runs SET status=?, error_message=? WHERE run_id=?",
                (next_status, str(error), run_id),
            )
            connection.execute(
                "UPDATE run_registry SET finished_at=? WHERE run_id=?",
                (_now(), run_id),
            )
            from quantlab.services.backtest_summary import refresh_backtest_summary

            refresh_backtest_summary(connection, run_id)
    return self.get(run_id)


class BacktestJobService:
    def __init__(self, settings, database):
        self.settings = settings
        self.database = database
        self.workbench = BacktestWorkbenchService(settings, database)
        self.artifacts = ArtifactRepository(settings, database)

    def submit(self, raw):
        """Submit an immutable workbench configuration for later execution.

        Configuration validation, version freezing, and idempotency belong to
        ``BacktestWorkbenchService``.  The job service owns execution only, so
        this explicit delegation keeps the API and direct service callers on
        the same submission path.
        """
        return self.workbench.submit(raw)

    def _path_and_row(self, run_id):
        run = self.workbench.get(run_id)
        if run is None:
            raise ValueError("backtest run not found")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT path, row_count, fields_json, manifest_hash FROM dataset_versions WHERE entity_id=? AND version_id=?",
                (run["dataset_id"], run["dataset_version_id"]),
            ).fetchone()
        if row is None:
            raise ValueError("dataset version not found")
        return run, self.settings.require_read_path(Path(row["path"])), dict(row)

    def _prepare_steps(self, run_id):
        with self.database.transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO backtest_steps(run_id, ordinal, step_name, status) VALUES (?, ?, ?, 'pending')",
                [(run_id, index, name) for index, name in enumerate(STEPS, 1)],
            )

    @staticmethod
    def _factor_field(config):
        refs = config["factor_versions"]
        return str(refs[0].get("field") or refs[0].get("factor") or "momentum_5")

    def _snapshot_rows(self, path):
        return int(pq.ParquetFile(path).metadata.num_rows)

    def _requeue_failed(self, run_id):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM backtest_steps WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
            connection.execute(
                "UPDATE backtest_runs SET error_message=NULL, metrics_json='{}' WHERE run_id=? AND status='failed'",
                (run_id,),
            )
            connection.execute("UPDATE run_registry SET finished_at=NULL WHERE run_id=?", (run_id,))
            from quantlab.services.backtest_summary import refresh_backtest_summary

            refresh_backtest_summary(connection, run_id)

    def get(self, run_id):
        run = self.workbench.get(run_id)
        if run is None:
            return None
        with self.database.connect() as connection:
            steps = connection.execute(
                "SELECT ordinal, step_name, status, started_at, finished_at, error_message, artifact_ids_json FROM backtest_steps WHERE run_id=? ORDER BY ordinal",
                (run_id,),
            ).fetchall()
            artifacts = connection.execute(
                "SELECT artifact_id, display_name, artifact_role, original_name, size_bytes, content_hash FROM artifacts WHERE run_id=? ORDER BY artifact_role",
                (run_id,),
            ).fetchall()
        run["steps"] = [dict(step) for step in steps]
        run["artifacts"] = [dict(artifact) for artifact in artifacts]
        run["trades"] = []
        for artifact in run["artifacts"]:
            if artifact["artifact_role"] == "trades":
                path = self.artifacts.get_download_path(artifact["artifact_id"])
                if path:
                    run["trades"] = json.loads(path.read_text(encoding="utf-8"))
        return run

    def _step_status(self, run_id, ordinal):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM backtest_steps WHERE run_id=? AND ordinal=?",
                (run_id, ordinal),
            ).fetchone()
        return str(row["status"]) if row else "pending"


_attach_label = attach_label
_once_embargo: tuple[str, Any, str | None] | None = None
_pretrade_allowed_dates: ContextVar[set[str] | None] = ContextVar("pretrade_allowed_dates", default=None)
_COMPARE = {"==": eq, "!=": ne, ">": gt, "<": lt, ">=": ge, "<=": le}
_FIELD_ALIASES = {**FIELD_ALIASES, "sma200": "sma_200", "ma200": "sma_200"}


def once_label_cutoff(calendar, predict_from, holding_days=None) -> str | None:
    predict = _norm_yyyymmdd(predict_from)
    if not predict:
        return None
    days = unique_dates(list(calendar) + [predict])
    hold = max(int(holding_days or 1), 1)
    return shift_trading_date(days, predict, -(hold + 1))


def apply_label_cutoff(frame, cutoff: str | None):
    if not cutoff or frame is None or getattr(frame, "empty", True) or "date" not in frame.columns:
        return frame
    dates = frame["date"].map(_norm_yyyymmdd)
    return frame.loc[dates <= cutoff].copy()


def apply_split_embargo(frame, test_from, holding_days=None, validation_from=None):
    if frame is None or getattr(frame, "empty", True) or "date" not in frame.columns:
        return frame
    dates = frame["date"].map(_norm_yyyymmdd)
    test_cutoff = once_label_cutoff(dates, test_from, holding_days)
    val_from = _norm_yyyymmdd(validation_from) if validation_from else None
    if not val_from:
        return apply_label_cutoff(frame, test_cutoff)
    val_cutoff = once_label_cutoff(dates, val_from, holding_days)
    train_keep = dates < val_from
    if val_cutoff:
        train_keep = train_keep & (dates <= val_cutoff)
    val_keep = dates >= val_from
    if test_cutoff:
        val_keep = val_keep & (dates <= test_cutoff)
    return frame.loc[train_keep | val_keep].copy()


@contextmanager
def once_label_embargo(predict_from, holding_days=None, validation_from=None) -> Iterator[None]:
    global _once_embargo
    previous = _once_embargo
    val_from = _norm_yyyymmdd(validation_from) if validation_from else None
    _once_embargo = (_norm_yyyymmdd(predict_from), holding_days, val_from)
    try:
        yield
    finally:
        _once_embargo = previous


def attach_label(frame, holding_days=None):
    labeled = _attach_label(frame, holding_days=holding_days)
    spec = _once_embargo
    if spec is None:
        return labeled
    predict_from, embargo_hold, validation_from = spec
    hold = holding_days if holding_days is not None else embargo_hold
    return apply_split_embargo(labeled, predict_from, hold, validation_from=validation_from)


_run_portfolio = run_portfolio
_compute_performance_metrics = _performance_metrics


def run_portfolio(frame, predictions, config):
    capture_predictions(frame, predictions)
    try:
        return _run_portfolio(frame, predictions, config)
    finally:
        if frame is not None:
            frame.attrs.pop("_quantlab_day_index", None)


def _segment_portfolio_config(config, frame, raw_root=None):
    portfolio_config = dict(config or {})
    if _as_bool(portfolio_config.get("open_gate_by_membership"), False):
        if "membership_open_allow" not in portfolio_config:
            allow = resolve_membership_open_allow(frame, portfolio_config, raw_root=raw_root)
            portfolio_config["membership_open_allow"] = allow or {}
        return portfolio_config
    if "benchmark_open_dates" in portfolio_config:
        return portfolio_config
    allowed = resolve_open_dates(frame, portfolio_config, raw_root=raw_root)
    if allowed is not None:
        portfolio_config["benchmark_open_dates"] = sorted(allowed)
    return portfolio_config


def wants_segment_curves(config) -> bool:
    payload = config if isinstance(config, dict) else {}
    if "segment_curves" not in payload:
        return True
    return _as_bool(payload.get("segment_curves"), True)


def _performance_metrics(trades, config, frame, equity_curve=None, raw_root=None):
    metrics = _compute_performance_metrics(
        trades, config, frame, equity_curve=equity_curve, raw_root=raw_root
    )
    captured_frame, predictions = peek_captured()
    source = captured_frame if captured_frame is not None else frame
    if wants_segment_curves(config):
        portfolio_config = _segment_portfolio_config(
            config, source if source is not None else frame, raw_root
        )
        metrics = attach_segment_curves(metrics, source, predictions, portfolio_config)
    else:
        metrics["segment_curves"] = {
            "by_float_market_cap": {"field": "float_market_cap", "status": "skipped", "buckets": []},
            "by_turn": {"field": "turn", "status": "skipped", "buckets": []},
        }
    metrics = attach_ranking_metrics(metrics, frame, config)
    return _attach_extra_performance_metrics(metrics, equity_curve)


def _compact_date_value(value: Any) -> str:
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y%m%d")
        except Exception:
            pass
    text = str(value or "").replace("-", "").replace(".", "").replace(" ", "").replace("T", "")
    return text[:8]


def _expression_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _series_for(frame: pd.DataFrame, name: str) -> pd.Series | None:
    column = _FIELD_ALIASES.get(name, name)
    if column in frame.columns:
        return frame[column]
    if name == "is_suspended" and "suspended" in frame.columns:
        return frame["suspended"]
    if name == "suspended" and "is_suspended" in frame.columns:
        return frame["is_suspended"]
    return None


def _as_numeric(value: Any) -> pd.Series | float:
    if isinstance(value, pd.Series):
        if pd.api.types.is_bool_dtype(value):
            return value.fillna(False).astype(float)
        return pd.to_numeric(value, errors="coerce")
    return float(value)


def _eval_node(frame: pd.DataFrame, node: Any) -> pd.Series | float | bool:
    if not isinstance(node, tuple) or not node:
        return pd.Series(False, index=frame.index)
    kind = node[0]
    if kind == "num":
        return float(node[1])
    if kind == "name":
        series = _series_for(frame, str(node[1]))
        if series is None:
            return pd.Series(pd.NA, index=frame.index, dtype="Float64")
        return series
    if kind == "not":
        value = _eval_node(frame, node[1])
        if isinstance(value, pd.Series):
            if not pd.api.types.is_bool_dtype(value):
                value = _as_numeric(value).fillna(0).ne(0)
            return ~value.fillna(False)
        return not bool(value)
    if kind in {"and", "or"}:
        left = _eval_node(frame, node[1])
        right = _eval_node(frame, node[2])
        if not isinstance(left, pd.Series):
            left = pd.Series(bool(left), index=frame.index)
        if not isinstance(right, pd.Series):
            right = pd.Series(bool(right), index=frame.index)
        if not pd.api.types.is_bool_dtype(left):
            left = _as_numeric(left).fillna(0).ne(0)
        if not pd.api.types.is_bool_dtype(right):
            right = _as_numeric(right).fillna(0).ne(0)
        left = left.fillna(False).astype(bool)
        right = right.fillna(False).astype(bool)
        return left & right if kind == "and" else left | right
    if kind == "cmp":
        op = _COMPARE.get(str(node[1]))
        if op is None:
            return pd.Series(False, index=frame.index)
        left = _as_numeric(_eval_node(frame, node[2]))
        right = _as_numeric(_eval_node(frame, node[3]))
        if not isinstance(left, pd.Series):
            left = pd.Series(left, index=frame.index)
        if not isinstance(right, pd.Series):
            right = pd.Series(right, index=frame.index)
        valid = left.notna() & right.notna()
        return valid & op(left, right)
    return pd.Series(False, index=frame.index)


def eval_expression_mask(frame: pd.DataFrame, expressions: list[str]) -> pd.Series:
    if frame is None or getattr(frame, "empty", True):
        return pd.Series(dtype=bool)
    mask = pd.Series(True, index=frame.index)
    for text in expressions:
        try:
            tree = parse_expr(text)
        except Exception:
            return pd.Series(False, index=frame.index)
        part = _eval_node(frame, tree)
        if not isinstance(part, pd.Series):
            part = pd.Series(bool(part), index=frame.index)
        if not pd.api.types.is_bool_dtype(part):
            part = _as_numeric(part).fillna(0).ne(0)
        mask = mask & part.fillna(False).astype(bool)
    return mask


def attach_sma(frame: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    column = f"sma_{int(window)}"
    if frame is None or getattr(frame, "empty", True) or column in frame.columns:
        return frame
    result = frame.copy()
    if "date" not in result.columns and "trade_date" in result.columns:
        result["date"] = result["trade_date"]
    if "instrument" not in result.columns and "ts_code" in result.columns:
        result["instrument"] = result["ts_code"]
    if "instrument" not in result.columns:
        result["instrument"] = "_index"
    if "date" not in result.columns:
        return result
    price_name = "hfq_close" if "hfq_close" in result.columns else "close"
    if price_name not in result.columns:
        return result
    order = result.sort_values(["instrument", "date"], kind="mergesort")
    price = pd.to_numeric(order[price_name], errors="coerce")
    sma = price.groupby(order["instrument"], sort=False).transform(
        lambda values: values.rolling(int(window), min_periods=int(window)).mean()
    )
    result[column] = sma.reindex(result.index)
    return result


def _sma_expressions_from_config(config: dict[str, Any] | None) -> list[str]:
    payload = config if isinstance(config, dict) else {}
    exprs: list[str] = []
    pretrade = payload.get("pretrade_filters") if isinstance(payload.get("pretrade_filters"), dict) else {}
    exprs.extend(_expression_list(pretrade.get("stock")))
    for key in ("train", "test", "validation"):
        section = payload.get(key)
        if not isinstance(section, dict):
            continue
        filt = section.get("filter") if isinstance(section.get("filter"), dict) else {}
        exprs.extend(_expression_list(filt.get("expressions")))
    return exprs


def _attach_config_sma(frame: pd.DataFrame, config: dict[str, Any] | None) -> pd.DataFrame:
    exprs = _sma_expressions_from_config(config)
    if not exprs or not needs_sma200(exprs):
        return frame
    return attach_sma(frame)


def _ensure_sma(frame: pd.DataFrame, expressions: list[str]) -> pd.DataFrame:
    if not expressions or not needs_sma200(expressions):
        return frame
    result = frame
    if "sma_200" in result.columns:
        return result
    if "instrument" not in result.columns:
        result = result.copy()
        result["instrument"] = "_index"
    if "hfq_close" not in result.columns and "close" in result.columns:
        result = result.copy()
        result["hfq_close"] = result["close"]
    return attach_sma(result)


def apply_expression_filters(frame: pd.DataFrame, expressions: list[str]) -> pd.DataFrame:
    exprs = _expression_list(expressions)
    if not exprs or frame is None or getattr(frame, "empty", True):
        return frame
    prepared = _ensure_sma(frame, exprs)
    mask = eval_expression_mask(prepared, exprs)
    return prepared.loc[mask].copy()


def apply_allowed_dates(frame: pd.DataFrame, allowed: Any) -> pd.DataFrame:
    if allowed is None or frame is None or getattr(frame, "empty", True) or "date" not in frame.columns:
        return frame
    keep = {item for item in (_compact_date_value(value) for value in allowed) if len(item) == 8}
    dates = frame["date"].map(_compact_date_value)
    return frame.loc[dates.isin(keep)].copy()


def benchmark_pass_dates(index_frame: pd.DataFrame | None, expressions: list[str]) -> set[str]:
    exprs = _expression_list(expressions)
    if not exprs:
        return set()
    if index_frame is None or getattr(index_frame, "empty", True):
        return set()
    frame = index_frame.copy()
    date_col = next((name for name in ("trade_date", "date") if name in frame.columns), None)
    if date_col is None:
        return set()
    if "hfq_close" not in frame.columns and "close" in frame.columns:
        frame["hfq_close"] = frame["close"]
    frame = _ensure_sma(frame, exprs)
    mask = eval_expression_mask(frame, exprs)
    return {_compact_date_value(value) for value, ok in zip(frame[date_col], mask, strict=False) if ok}


def apply_pretrade_filters(frame, config, index_frame=None):
    if frame is None or getattr(frame, "empty", True):
        return frame
    pretrade = config.get("pretrade_filters") if isinstance(config, dict) else {}
    if not isinstance(pretrade, dict):
        pretrade = {}
    result = apply_expression_filters(frame, pretrade.get("stock"))
    bench = _expression_list(pretrade.get("benchmark"))
    if bench:
        result = apply_allowed_dates(result, benchmark_pass_dates(index_frame, bench))
    return result


def _attach_extra_performance_metrics(metrics: dict[str, Any], equity_curve) -> dict[str, Any]:
    result = dict(metrics or {})
    rows = list(equity_curve or [])
    if len(rows) < 2:
        result.setdefault("sortino", None)
        result.setdefault("calmar", None)
        result.setdefault("max_loss_streak", 0)
        result.setdefault("annual_summary", [])
        return result
    frame = pd.DataFrame(rows)
    if "date" not in frame.columns or "equity" not in frame.columns:
        result.setdefault("sortino", None)
        result.setdefault("calmar", None)
        result.setdefault("max_loss_streak", 0)
        result.setdefault("annual_summary", [])
        return result
    frame = frame.copy()
    frame["date"] = frame["date"].map(_compact_date_value)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame = frame.dropna(subset=["date", "equity"]).sort_values("date")
    if len(frame) < 2:
        result["sortino"] = None
        result["calmar"] = None
        result["max_loss_streak"] = 0
        result["annual_summary"] = []
        return result
    returns = frame["equity"].pct_change().dropna()
    downside = returns[returns < 0]
    if downside.empty or float(downside.std(ddof=1) or 0) == 0:
        result["sortino"] = None if returns.empty else float(returns.mean() * (252 ** 0.5) / 1e-12)
    else:
        result["sortino"] = float(returns.mean() / float(downside.std(ddof=1)) * (252 ** 0.5))
    first = float(frame["equity"].iloc[0])
    last = float(frame["equity"].iloc[-1])
    total = (last / first - 1.0) if first else None
    start = pd.Timestamp(frame["date"].iloc[0])
    end = pd.Timestamp(frame["date"].iloc[-1])
    years = max((end - start).days / 365.25, 1e-9)
    annual = ((1.0 + total) ** (1.0 / years) - 1.0) if total is not None and total > -1 else None
    peak = frame["equity"].cummax()
    drawdown = frame["equity"] / peak - 1.0
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0
    if annual is not None and max_dd < 0:
        result["calmar"] = float(annual / abs(max_dd))
    else:
        result["calmar"] = None
    streak = 0
    longest = 0
    for value in returns:
        if value < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    result["max_loss_streak"] = int(longest)
    annual_rows: list[dict[str, Any]] = []
    frame["year"] = frame["date"].str[:4]
    for year, group in frame.groupby("year", sort=True):
        start_eq = float(group["equity"].iloc[0])
        end_eq = float(group["equity"].iloc[-1])
        year_return = (end_eq / start_eq - 1.0) if start_eq else 0.0
        year_peak = group["equity"].cummax()
        year_dd = float((group["equity"] / year_peak - 1.0).min()) if len(group) else 0.0
        annual_rows.append({"year": str(year), "return": year_return, "max_drawdown": year_dd})
    result["annual_summary"] = annual_rows
    return result


def _code_column(frame):
    columns = getattr(frame, "columns", ())
    if "instrument" in columns:
        return "instrument"
    if "ts_code" in columns:
        return "ts_code"
    return None


def apply_section_stock_scope(frame, stock_scope):
    if frame is None or getattr(frame, "empty", True):
        return frame
    scope = str(stock_scope or "").strip()
    column = _code_column(frame)
    if not scope or column is None:
        return frame
    codes = frame[column].astype(str)
    if scope == "沪市（SH）":
        mask = codes.str.endswith(".SH")
    elif scope == "深市（SZ）":
        mask = codes.str.endswith(".SZ")
    elif scope == "中国A股（SH/SZ）":
        mask = codes.str.endswith(".SH") | codes.str.endswith(".SZ")
    else:
        return frame
    return frame.loc[mask].copy()


def _usable_numeric(frame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def drop_unverified_halt_rows(frame, section, notes=None):
    if frame is None or getattr(frame, "empty", True):
        return frame
    filt = section.get("filter") if isinstance(section, dict) else {}
    if not isinstance(filt, dict):
        filt = {}
    result = frame
    if filt.get("skip_limit_close", True):
        needed = ("close", "up_limit", "down_limit")
        missing = [name for name in needed if name not in result.columns]
        if missing:
            if notes is not None:
                notes[:] = [item for item in notes if "跳过收盘未涨跌停" not in str(item)]
                notes.append("缺少涨跌停价，已排除无法校验的股票")
            return result.iloc[0:0].copy()
        close = _usable_numeric(result, "close")
        up_limit = _usable_numeric(result, "up_limit")
        down_limit = _usable_numeric(result, "down_limit")
        result = result.loc[close.notna() & up_limit.notna() & down_limit.notna()].copy()
    if filt.get("suspended") is False:
        column = next((name for name in ("suspended", "is_suspended") if name in result.columns), None)
        if column is None:
            if notes is not None:
                notes.append("缺少停牌标记，已排除无法校验的股票")
            return result.iloc[0:0].copy()
    return result


def _apply_section_expressions(frame, filt: dict[str, Any]):
    if frame is None or getattr(frame, "empty", True):
        return frame
    expressions = _expression_list(filt.get("expressions"))
    val_from = _compact_date_value(filt.get("validation_date_from")) if filt.get("validation_date_from") else ""
    val_exprs = filt.get("validation_expressions")
    if val_from and val_exprs is not None and "date" in frame.columns:
        dates = frame["date"].map(_compact_date_value)
        train_part = apply_expression_filters(frame.loc[dates < val_from], expressions)
        valid_part = apply_expression_filters(frame.loc[dates >= val_from], _expression_list(val_exprs))
        if (train_part is None or getattr(train_part, "empty", True)) and (
            valid_part is None or getattr(valid_part, "empty", True)
        ):
            return frame.iloc[0:0].copy()
        parts = [part for part in (train_part, valid_part) if part is not None and not getattr(part, "empty", True)]
        return pd.concat(parts).sort_index()
    return apply_expression_filters(frame, expressions)


def _filter(frame, section, notes=None):
    result = _filter_core(frame, section, notes)
    result = apply_section_stock_scope(result, (section or {}).get("stock_scope"))
    result = drop_unverified_halt_rows(result, section, notes)
    filt = section.get("filter") if isinstance(section, dict) else {}
    if not isinstance(filt, dict):
        filt = {}
    result = _apply_section_expressions(result, filt)
    allowed = filt.get("allowed_dates")
    if allowed is None:
        allowed = _pretrade_allowed_dates.get()
    if allowed is not None:
        result = apply_allowed_dates(result, allowed)
    return result


def _benchmark_allowed_dates(self, config: dict[str, Any]) -> set[str] | None:
    pretrade = config.get("pretrade_filters") if isinstance(config.get("pretrade_filters"), dict) else {}
    bench = _expression_list(pretrade.get("benchmark"))
    if not bench:
        return None
    code = str(config.get("benchmark") or "000300.SH")
    index = _load_index_daily(Path(self.settings.raw_root), code)
    return benchmark_pass_dates(index, bench)


def execute(self, run_id: str):
    row = self.get(run_id)
    config = (row or {}).get("config") or {}
    previous_status = (row or {}).get("status")
    machine = load_machine_identity(self.settings.runtime_root)
    if machine.get("machine_id") and not config.get("machine_id"):
        config = {**config, "machine_id": machine["machine_id"]}
    prior_token = set_resource_prior(
        last_resource_sample(self.database, run_signature(config), machine_id=machine.get("machine_id"))
    )
    notes_token = set_resource_notes({})
    sampler = ResourceSampler().start()
    token = _pretrade_allowed_dates.set(_benchmark_allowed_dates(self, config))
    result = None
    try:
        if config.get("kind") == "rule_signal":
            from quantlab.services.rule_backtest import execute_rule_signal

            result = execute_rule_signal(self, run_id)
        elif walk_forward_mode(config) != "once":
            result = _execute_core(self, run_id)
        else:
            test = config.get("test") or {}
            predict_from = test.get("date_from")
            if not predict_from:
                result = _execute_core(self, run_id)
            else:
                hyper = config.get("hyperparameters") if isinstance(config.get("hyperparameters"), dict) else {}
                validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
                validation_from = hyper.get("validation_date_from") or validation.get("date_from")
                with once_label_embargo(predict_from, config.get("holding_days"), validation_from=validation_from):
                    result = _execute_core(self, run_id)
    finally:
        sample = sampler.stop()
        persist_run_resources(
            self.database,
            run_id,
            config,
            sample,
            settings=self.settings,
            previous_status=previous_status,
        )
        write_run_manifest(self.settings, self.database, run_id)
        reset_resource_notes(notes_token)
        reset_resource_prior(prior_token)
        _pretrade_allowed_dates.reset(token)
    if isinstance(result, dict):
        latest = self.get(run_id)
        if latest and isinstance(latest.get("metrics"), dict):
            result["metrics"] = latest["metrics"]
    return result


def fold_worker_count(fold_count: int, *, prior_peak_rss_bytes: int = 0) -> int:
    requested = resolve_worker_count("QUANTLAB_FOLD_WORKERS", "fold_workers", fold_count, cpu_count)
    peak = int(prior_peak_rss_bytes or 0)
    if not peak:
        prior = active_resource_prior() or {}
        peak = int(prior.get("peak_rss_bytes") or 0)
    workers = cap_workers(
        requested=requested,
        task_count=fold_count,
        ram_bytes=total_ram_bytes(),
        unit_bytes=0,
        kind="fold",
        prior_peak_rss_bytes=peak,
    )
    note_workers(fold_workers=workers)
    return workers


def _rolling_predictions(
    self,
    *,
    frame,
    train,
    test,
    config,
    kind,
    params,
    feature_fields,
    holding_days,
    filter_notes,
):
    history = {
        "date_from": config["train"]["date_from"],
        "date_to": config["test"]["date_to"],
        "filter": config["train"].get("filter") or {},
    }
    eligible = self._filter(frame, history, filter_notes)
    if getattr(eligible, "empty", True):
        eligible = pd.concat([train, test], ignore_index=True)
    calendar = unique_dates(frame["date"] if "date" in frame.columns else eligible["date"])
    folds = period_train_folds(
        roll_start=config["train"]["date_from"],
        roll_end=config["test"]["date_to"],
        calendar=calendar,
        holding_days=holding_days,
        train_period_months=train_period_months(config),
        test_period_months=normalize_test_period_months(config),
    )
    if not folds:
        raise ValueError("定长回看没有可用的切分。")
    target_col = rank_label_column(kind)
    label_fields = feature_fields + [target_col]

    def run_fold(fold):
        predict_from = fold["predict_from"]
        train_start = fold.get("train_start")
        if train_start:
            slice_frame = eligible.loc[(eligible["date"] > train_start) & (eligible["date"] <= predict_from)]
        else:
            slice_frame = eligible.loc[eligible["date"] <= predict_from]
        labeled = attach_label(slice_frame, holding_days=holding_days)
        if target_col not in labeled.columns:
            raise ValueError("训练标签没有算出来。")
        cutoff = fold.get("label_cutoff") or fold["train_end"]
        usable = labeled.loc[labeled["date"] <= cutoff].dropna(subset=label_fields)
        if fold.get("train_start"):
            usable = usable.loc[usable["date"] > fold["train_start"]]
        if len(usable) < MIN_TRAIN_ROWS:
            raise ValueError(
                f"{fold['month']} 训练样本不足（{len(usable)} 行，至少 {MIN_TRAIN_ROWS} 行）。"
            )
        dates = usable["date"] if "date" in usable.columns else None
        booster = fit_estimator(kind, usable[feature_fields], usable[target_col], params, dates=dates)
        if booster is None:
            raise ValueError("模型没有训练出来。")
        pred_slice = (
            test.loc[test["date"].isin(fold["predict_dates"])]
            .dropna(subset=feature_fields)
            .copy()
        )
        fold_row = {
            "month": fold["month"],
            "train_start": fold.get("train_start"),
            "train_end": fold["train_end"],
            "label_cutoff": fold.get("label_cutoff"),
            "predict_from": fold["predict_from"],
            "predict_to": fold["predict_to"],
            "train_rows": int(len(usable)),
        }
        if pred_slice.empty:
            return None, fold_row, labeled
        pred_slice["score"] = predict_estimator(kind, booster, pred_slice[feature_fields])
        return pred_slice[["date", "instrument", "score"]], fold_row, labeled

    workers = fold_worker_count(len(folds))
    database = getattr(self, "database", None)
    prior = last_resource_sample(database, run_signature(config)) if database is not None else None
    if prior is None:
        prior = active_resource_prior()
    workers = fold_worker_count(
        len(folds),
        prior_peak_rss_bytes=int((prior or {}).get("peak_rss_bytes") or 0),
    )
    with booster_thread_limit(workers):
        if workers <= 1:
            results = [run_fold(fold) for fold in folds]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_fold, fold) for fold in folds]
                results = [future.result() for future in futures]

    chunks = []
    fold_rows = []
    labeled = eligible
    for chunk, fold_row, fold_labeled in results:
        labeled = fold_labeled
        fold_rows.append(fold_row)
        if chunk is not None:
            chunks.append(chunk)
    if not chunks:
        raise ValueError("滚动预测没有打出分数。")
    return pd.concat(chunks, ignore_index=True), labeled, fold_rows


def _step(self, run_id: str, ordinal: int, status: str, error=None) -> None:
    timestamp = _now()
    with self.database.transaction() as connection:
        if status == "running":
            connection.execute(
                "UPDATE backtest_steps SET status=?, started_at=COALESCE(started_at, ?), error_message=? "
                "WHERE run_id=? AND ordinal=?",
                (status, timestamp, error, run_id, ordinal),
            )
            return
        connection.execute(
            "UPDATE backtest_steps SET status=?, started_at=COALESCE(started_at, ?), finished_at=?, error_message=? "
            "WHERE run_id=? AND ordinal=?",
            (status, timestamp, timestamp, error, run_id, ordinal),
        )


def _load_frame_with_pack_factors(self, path, config):
    from quantlab.services.canonical_pack_factors import default_sidecar_path

    frame = _load_frame_core(self, path, config)
    return prepare_research_frame(frame, config, self.settings.raw_root, default_sidecar_path(path))


def prepare_research_frame(frame, config, raw_root, sidecar_path):
    from quantlab.services.canonical_pack_factors import attach_pack_factor_columns
    from quantlab.services.index_membership import apply_pit_index_universe, parse_universe_index_codes

    codes = parse_universe_index_codes((config or {}).get("universe_index_codes"))
    narrowed = apply_pit_index_universe(frame, raw_root, codes)
    refs = (config or {}).get("factor_versions") or []
    with_factors = attach_pack_factor_columns(narrowed, refs, sidecar_path)
    from quantlab.services.period_bar_gate import attach_period_bar_gate

    gated = attach_period_bar_gate(with_factors, raw_root, (config or {}).get("period_bar_gate"))
    return _attach_config_sma(gated, config)


BacktestJobService._filter = staticmethod(_filter)
BacktestJobService.execute = execute
BacktestJobService._rolling_predictions = _rolling_predictions
BacktestJobService._step = _step
BacktestJobService._load_frame = _load_frame_with_pack_factors
