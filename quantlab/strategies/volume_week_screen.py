"""问句选股：周成交量翻倍，按周涨跌幅从小到大。

信号记在 T 日收盘。账户在 T+1 开盘买。周按周五截止。
本周成交量用截至 T 的累计，对比上一整周。周涨跌幅用后复权收盘相对上一周最后一个交易日。
昨日涨幅是 T-1 的后复权收益。近 120 日是 120 个交易日。
换手率用 Tushare 百分比。市值用元。排除 ST、科创板和北交所。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from quantlab.services.index_membership import is_main_sme_chinext
from quantlab.strategies.wiki_trend_follow import normalize_rule_frame

DEFAULTS: dict[str, Any] = {
    "target_weight": 0.20,
    "min_market_cap": 2_000_000_000.0,
    "max_market_cap": 30_000_000_000.0,
    "max_turn": 20.0,
    "max_ret_120": 0.20,
    "min_yesterday_ret": 0.0,
    "max_yesterday_ret": 0.03,
    "min_week_vol_growth": 1.0,
    "ret_120_days": 120,
}


def generate_signals(frame: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = {**DEFAULTS, **(params or {})}
    normalized = normalize_rule_frame(frame)
    featured = _screen_features(normalized, int(cfg["ret_120_days"]))
    rows: list[dict[str, Any]] = []
    weight = float(cfg["target_weight"])
    for trade_date, day in featured.groupby("date", sort=True):
        picked = day.loc[_passes(day, cfg)].sort_values(
            ["week_ret", "instrument"], ascending=[True, True]
        )
        for instrument in picked["instrument"]:
            rows.append(
                {
                    "date": pd.Timestamp(trade_date).strftime("%Y%m%d"),
                    "instrument": instrument,
                    "weight": weight,
                }
            )
    return pd.DataFrame(rows, columns=["date", "instrument", "weight"])


def _screen_features(frame: pd.DataFrame, ret_days: int) -> pd.DataFrame:
    required = ("hfq_close", "vol", "turn", "total_market_cap")
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError("研究行情缺少列：" + ", ".join(missing))
    out = frame.sort_values(["instrument", "date"], kind="mergesort").copy()
    grouped = out.groupby("instrument", sort=False)
    close = pd.to_numeric(out["hfq_close"], errors="coerce")
    out["yesterday_ret"] = grouped["hfq_close"].pct_change().groupby(out["instrument"]).shift(1)
    out["ret_120"] = close / grouped["hfq_close"].shift(ret_days) - 1.0
    out["week"] = out["date"].dt.to_period("W-FRI").astype(str)
    out["_vol"] = pd.to_numeric(out["vol"], errors="coerce")
    week_vol = out.groupby(["instrument", "week"], sort=False)["_vol"].sum()
    prev_week_vol = week_vol.groupby(level=0).shift(1)
    week_close = out.groupby(["instrument", "week"], sort=False)["hfq_close"].last()
    prev_week_close = week_close.groupby(level=0).shift(1)
    keys = pd.MultiIndex.from_arrays([out["instrument"], out["week"]])
    out["prev_week_vol"] = prev_week_vol.reindex(keys).to_numpy()
    out["prev_week_close"] = pd.to_numeric(prev_week_close.reindex(keys), errors="coerce").to_numpy()
    out["week_vol_to_date"] = out.groupby(["instrument", "week"], sort=False)["_vol"].cumsum()
    out["week_vol_growth"] = out["week_vol_to_date"] / out["prev_week_vol"] - 1.0
    out["week_ret"] = close / out["prev_week_close"] - 1.0
    out["turn"] = pd.to_numeric(out["turn"], errors="coerce")
    out["total_market_cap"] = pd.to_numeric(out["total_market_cap"], errors="coerce")
    return out


def _passes(day: pd.DataFrame, cfg: dict[str, Any]) -> pd.Series:
    mask = day["instrument"].map(is_main_sme_chinext)
    if "st_status" in day.columns:
        st_status = pd.to_numeric(day["st_status"], errors="coerce")
        mask &= st_status.eq(0)
    else:
        mask &= False
    if "is_suspended" in day.columns:
        suspended = pd.to_numeric(day["is_suspended"], errors="coerce").fillna(0).ne(0)
        mask &= ~suspended
    elif "suspended" in day.columns:
        suspended = pd.to_numeric(day["suspended"], errors="coerce").fillna(0).ne(0)
        mask &= ~suspended
    cap = day["total_market_cap"]
    mask &= cap.gt(float(cfg["min_market_cap"])) & cap.lt(float(cfg["max_market_cap"]))
    mask &= day["turn"].lt(float(cfg["max_turn"]))
    mask &= day["ret_120"].lt(float(cfg["max_ret_120"]))
    yesterday = day["yesterday_ret"]
    mask &= yesterday.gt(float(cfg["min_yesterday_ret"])) & yesterday.lt(float(cfg["max_yesterday_ret"]))
    mask &= day["week_vol_growth"].gt(float(cfg["min_week_vol_growth"]))
    mask &= day["week_ret"].notna()
    return mask.fillna(False)
