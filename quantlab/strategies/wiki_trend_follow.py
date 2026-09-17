"""Wiki multi-indicator trend-following rule strategy."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from quantlab.services.index_membership import (
    amount_unit,
    amount_yuan,
    is_main_sme_chinext,
    members_on,
    parse_universe_index_codes,
)
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
    "universe_index_codes": [HS300_CODE, CSI500_CODE],
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


def _universe_on(
    membership: pd.DataFrame | dict[Any, set[str]],
    trade_date: pd.Timestamp,
    index_codes: tuple[str, ...] | list[str] | None = None,
) -> set[str]:
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
    codes = parse_universe_index_codes(index_codes)
    out: set[str] = set()
    for code in codes:
        out |= members_on(membership, code, key)
    return out


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


def _truthy_series(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    numeric = pd.to_numeric(values, errors="coerce")
    if not numeric.isna().all():
        flagged = numeric.fillna(0).ne(0)
        text = values.astype(str).str.strip().str.lower()
        return flagged | text.isin({"1", "true", "t", "yes", "y"})
    text = values.astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "t", "yes", "y"})


def _normalize_date_series(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values):
        return values.dt.normalize()
    text = values.astype(str).str.strip()
    empty = text.eq("") | text.str.lower().isin({"nan", "none", "nat"})
    parsed = pd.to_datetime(text.str.slice(0, 8), format="%Y%m%d", errors="coerce")
    hyphen = text.str.contains("-", regex=False)
    parsed = parsed.where(~hyphen, pd.to_datetime(text, errors="coerce"))
    return parsed.where(~empty)


def _passes_stock_filters_mask(pool: pd.DataFrame, trade_date: pd.Timestamp, params: dict[str, Any]) -> pd.Series:
    mask = pd.Series(True, index=pool.index)
    if "st_status" in pool.columns:
        st_status = pd.to_numeric(pool["st_status"], errors="coerce")
        mask &= st_status.isna() | st_status.eq(0)
    has_suspend_column = False
    suspended = pd.Series(False, index=pool.index)
    for key in ("is_suspended", "suspended"):
        if key in pool.columns:
            has_suspend_column = True
            suspended = suspended | _truthy_series(pool[key])
    if not has_suspend_column:
        mask &= False
    else:
        mask &= ~suspended
    if "delist_date" in pool.columns:
        delist = _normalize_date_series(pool["delist_date"])
        mask &= delist.isna() | (trade_date < delist)
    if "list_date" in pool.columns:
        listed = _normalize_date_series(pool["list_date"])
        list_days = (trade_date - listed).dt.days
        mask &= listed.notna() & (list_days > params["min_list_days"])
    else:
        mask &= False
    mask &= pool["instrument"].map(is_main_sme_chinext)
    if "amount" in pool.columns:
        amount = pd.to_numeric(pool["amount"], errors="coerce")
        mask &= amount.isna() | (amount > params["min_amount_yuan"])
    return mask.fillna(False)


def _passes_four_conditions_mask(pool: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    mask = pool["ema_20"].notna() & pool["ema_60"].notna()
    mask &= pool["ema_20"] > pool["ema_60"]
    mask &= pool["macd_dif"].notna() & pool["macd_dea"].notna() & pool["macd_hist"].notna()
    mask &= (pool["macd_dif"] > pool["macd_dea"]) & (pool["macd_hist"] > 0)
    mask &= pool["rsi"].notna()
    mask &= (pool["rsi"] >= params["rsi_low"]) & (pool["rsi"] <= params["rsi_high"])
    mask &= pool["boll_mid"].notna()
    mask &= pool["hfq_close"] > pool["boll_mid"]
    return mask.fillna(False)


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
            universe = _universe_on(membership, trade_date, cfg.get("universe_index_codes"))
            asof_cache[cache_key] = universe
        if not universe:
            continue
        pool = day_rows[day_rows["instrument"].isin(universe)].copy()
        if pool.empty:
            continue

        pool = pool.loc[_passes_stock_filters_mask(pool, trade_date, cfg)]
        pool = pool.loc[_passes_four_conditions_mask(pool, cfg)]
        if pool.empty:
            continue

        ranked = _rank_candidates(pool)
        picks = ranked.head(int(cfg["top_n"]))
        for row in picks.itertuples(index=False):
            output_rows.append(
                {
                    "date": trade_date,
                    "instrument": row.instrument,
                    "weight": float(cfg["target_weight"]),
                    "rank_score": float(row.rank_score),
                }
            )

    if not output_rows:
        return pd.DataFrame(columns=["date", "instrument", "weight", "rank_score"])
    return pd.DataFrame(output_rows)[["date", "instrument", "weight", "rank_score"]]
