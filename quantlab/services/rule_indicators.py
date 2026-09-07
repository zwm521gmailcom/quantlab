"""Technical indicators for rule-based strategies."""

from __future__ import annotations

import pandas as pd


def add_ema(frame: pd.DataFrame, column: str = "hfq_close", spans: tuple[int, ...] = (20, 60)) -> pd.DataFrame:
    result = frame.copy()
    grouped = result.groupby("instrument", sort=False)[column]
    for span in spans:
        result[f"ema_{span}"] = grouped.transform(lambda values: values.ewm(span=span, adjust=False).mean())
    return result


def add_macd(
    frame: pd.DataFrame,
    column: str = "hfq_close",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    result = frame.copy()
    grouped = result.groupby("instrument", sort=False)[column]
    ema_fast = grouped.transform(lambda values: values.ewm(span=fast, adjust=False).mean())
    ema_slow = grouped.transform(lambda values: values.ewm(span=slow, adjust=False).mean())
    dif = ema_fast - ema_slow
    dea = dif.groupby(result["instrument"], sort=False).transform(
        lambda values: values.ewm(span=signal, adjust=False).mean()
    )
    result["macd_dif"] = dif
    result["macd_dea"] = dea
    result["macd_hist"] = 2 * (dif - dea)
    return result


def add_rsi(frame: pd.DataFrame, column: str = "hfq_close", period: int = 14) -> pd.DataFrame:
    result = frame.copy()
    grouped = result.groupby("instrument", sort=False)[column]
    delta = grouped.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    alpha = 1 / period
    avg_gain = gain.groupby(result["instrument"], sort=False).transform(
        lambda values: values.ewm(alpha=alpha, adjust=False).mean()
    )
    avg_loss = loss.groupby(result["instrument"], sort=False).transform(
        lambda values: values.ewm(alpha=alpha, adjust=False).mean()
    )
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    result["rsi"] = rsi.fillna(100.0).astype(float)
    return result


def add_boll_mid(frame: pd.DataFrame, column: str = "hfq_close", window: int = 20) -> pd.DataFrame:
    result = frame.copy()
    result["boll_mid"] = result.groupby("instrument", sort=False)[column].transform(
        lambda values: values.rolling(window, min_periods=window).mean()
    )
    return result


def add_momentum(frame: pd.DataFrame, column: str = "hfq_close", window: int = 20) -> pd.DataFrame:
    result = frame.copy()
    values = result[column]
    shifted = result.groupby("instrument", sort=False)[column].shift(window)
    result[f"mom_{window}"] = values / shifted - 1.0
    return result


def add_price_shifts(frame: pd.DataFrame, column: str = "hfq_close", windows: tuple[int, ...] = (20, 60)) -> pd.DataFrame:
    result = frame.copy()
    grouped = result.groupby("instrument", sort=False)[column]
    for window in windows:
        result[f"{column}_{window}"] = grouped.shift(window)
    return result


def enrich_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add EMA, MACD, RSI, Bollinger mid, momentum and price shifts."""
    ordered = frame.sort_values(["instrument", "date"]).reset_index(drop=True)
    enriched = add_ema(ordered)
    enriched = add_macd(enriched)
    enriched = add_rsi(enriched)
    enriched = add_boll_mid(enriched)
    enriched = add_momentum(enriched, window=20)
    enriched = add_price_shifts(enriched, windows=(20, 60))
    return enriched
