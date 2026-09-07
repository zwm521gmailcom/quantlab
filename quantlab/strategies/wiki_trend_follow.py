"""Wiki multi-indicator trend-following rule strategy."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from quantlab.services.index_membership import amount_unit, amount_yuan, is_main_sme_chinext, members_on
from quantlab.services.rule_indicators import enrich_indicators

_LOG = logging.getLogger(__name__)

HS300_CODE = "000300.SH"
CSI500_CODE = "000905.SH"

DEFAULTS: dict[str, Any] = {
    "rsi_low": 40.0,
    "rsi_high": 72.0,
    "top_n": 10,
    "target_weight": 0.10,
    "min_list_days": 365,
    "min_amount_yuan": 50_000_000,
}


def _normalize_date(value: Any) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    text = str(value).strip()
    if "-" in text:
        return pd.Timestamp(text).normalize()
    return pd.Timestamp(text[:8]).normalize()


def normalize_rule_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Map Tushare-style columns onto the rule-strategy schema."""
    result = frame.copy()
    rename: dict[str, str] = {}
    if "instrument" not in result.columns and "ts_code" in result.columns:
        rename["ts_code"] = "instrument"
    if "date" not in result.columns and "trade_date" in result.columns:
        rename["trade_date"] = "date"
    if rename:
        result = result.rename(columns=rename)
    if "instrument" not in result.columns or "date" not in result.columns:
        raise ValueError("研究行情缺少股票代码或日期列（需要 instrument/date 或 ts_code/trade_date）。")
    if "hfq_close" not in result.columns:
        raise ValueError("研究行情缺少后复权收盘价 hfq_close。")
    result["date"] = result["date"].map(_normalize_date)
    result["instrument"] = result["instrument"].astype(str)
    return result


def _normalize_frame_dates(frame: pd.DataFrame) -> pd.DataFrame:
    return normalize_rule_frame(frame)


def _universe_on(membership: pd.DataFrame | dict[Any, set[str]], trade_date: pd.Timestamp) -> set[str]:
    key = trade_date.strftime("%Y%m%d")
    if isinstance(membership, dict):
        universe: set[str] = set()
        best: str | None = None
        dated = False
        for raw_key, members in membership.items():
            day = _normalize_date(raw_key).strftime("%Y%m%d")
            dated = True
            if day <= key and (best is None or day > best):
                best = day
                universe = set(members)
        if dated and best is None:
            return set()
        if not dated:
            for members in membership.values():
                universe |= set(members)
        return universe
    return members_on(membership, HS300_CODE, key) | members_on(membership, CSI500_CODE, key)


def _truthy_flag(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


def _suspended_flag(row: pd.Series) -> bool:
    has_column = False
    for key in ("is_suspended", "suspended"):
        if key in row.index:
            has_column = True
            if _truthy_flag(row[key]):
                return True
    return not has_column


def _list_days(row: pd.Series, trade_date: pd.Timestamp) -> int:
    if "list_date" not in row.index or pd.isna(row["list_date"]):
        return 0
    text = str(row["list_date"]).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return 0
    list_date = _normalize_date(row["list_date"])
    return (trade_date - list_date).days


def _delisted(row: pd.Series, trade_date: pd.Timestamp) -> bool:
    if "delist_date" not in row.index or pd.isna(row["delist_date"]):
        return False
    text = str(row["delist_date"]).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return False
    return trade_date >= _normalize_date(row["delist_date"])


def _passes_stock_filters(row: pd.Series, trade_date: pd.Timestamp, params: dict[str, Any]) -> bool:
    st_status = row.get("st_status", 0)
    if pd.notna(st_status) and int(st_status) != 0:
        return False
    if _suspended_flag(row):
        return False
    if _delisted(row, trade_date):
        return False
    if _list_days(row, trade_date) <= params["min_list_days"]:
        return False
    if not is_main_sme_chinext(str(row["instrument"])):
        return False
    if "amount" in row.index and pd.notna(row["amount"]):
        if float(row["amount"]) <= params["min_amount_yuan"]:
            return False
    return True


def _passes_four_conditions(row: pd.Series, params: dict[str, Any]) -> bool:
    if pd.isna(row.get("ema_20")) or pd.isna(row.get("ema_60")):
        return False
    if row["ema_20"] <= row["ema_60"]:
        return False
    if pd.isna(row.get("macd_dif")) or pd.isna(row.get("macd_dea")) or pd.isna(row.get("macd_hist")):
        return False
    if row["macd_dif"] <= row["macd_dea"] or row["macd_hist"] <= 0:
        return False
    if pd.isna(row.get("rsi")):
        return False
    if row["rsi"] < params["rsi_low"] or row["rsi"] > params["rsi_high"]:
        return False
    if pd.isna(row.get("boll_mid")):
        return False
    if row["hfq_close"] <= row["boll_mid"]:
        return False
    return True


def _rank_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    ranked = candidates.copy()
    ranked["rank_score"] = (
        ranked["macd_hist"].rank(pct=True) * 0.40
        + ranked["mom_20"].rank(pct=True) * 0.35
        + ranked["rsi"].rank(pct=True) * 0.25
    )
    return ranked.sort_values("rank_score", ascending=False)


def generate_signals(
    frame: pd.DataFrame,
    membership: pd.DataFrame | dict[Any, set[str]],
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Generate daily target-weight signals for the Wiki trend strategy."""
    cfg = {**DEFAULTS, **(params or {})}
    normalized = _normalize_frame_dates(frame)
    if "amount" in normalized.columns:
        unit = amount_unit(normalized["amount"])
        converted = amount_yuan(normalized["amount"], unit=unit)
        _LOG.info(
            "成交额单位判定为%s（中位数=%s）",
            converted.attrs.get("unit"),
            converted.attrs.get("median"),
        )
        normalized = normalized.copy()
        normalized["amount"] = converted
    enriched = enrich_indicators(normalized)
    asof_cache: dict[str, set[str]] = {}

    output_rows: list[dict[str, Any]] = []

    for trade_date, day_rows in enriched.groupby("date", sort=True):
        trade_date = pd.Timestamp(trade_date).normalize()
        cache_key = trade_date.strftime("%Y%m%d")
        universe = asof_cache.get(cache_key)
        if universe is None:
            universe = _universe_on(membership, trade_date)
            asof_cache[cache_key] = universe
        if not universe:
            continue
        pool = day_rows[day_rows["instrument"].isin(universe)].copy()
        if pool.empty:
            continue

        mask = pool.apply(lambda row: _passes_stock_filters(row, trade_date, cfg), axis=1)
        pool = pool.loc[mask]
        mask = pool.apply(lambda row: _passes_four_conditions(row, cfg), axis=1)
        pool = pool.loc[mask]
        if pool.empty:
            continue

        ranked = _rank_candidates(pool)
        picks = ranked.head(int(cfg["top_n"]))
        for _, row in picks.iterrows():
            output_rows.append(
                {
                    "date": trade_date,
                    "instrument": row["instrument"],
                    "weight": float(cfg["target_weight"]),
                    "rank_score": float(row["rank_score"]),
                }
            )

    if not output_rows:
        return pd.DataFrame(columns=["date", "instrument", "weight", "rank_score"])
    return pd.DataFrame(output_rows)[["date", "instrument", "weight", "rank_score"]]
