"""Qlib Alpha158 features on a local panel.

The expressions follow microsoft/qlib Alpha158DL.get_feature_config. The panel has no
amount field, so VWAP is the typical price (high + low + close) / 3.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_WINDOWS = (5, 10, 20, 30, 60)
_REQUIRED = ("open", "high", "low", "close", "volume")


def alpha158_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Return the 158 Alpha158 columns, aligned to panel.index. Missing prices yield an empty frame."""
    if any(name not in panel.columns for name in _REQUIRED):
        return pd.DataFrame(index=panel.index)
    frame = panel.sort_index()
    open_ = pd.to_numeric(frame["open"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    vwap = (high + low + close) / 3
    eps = 1e-12
    span = high - low + eps
    columns: dict[str, pd.Series] = {
        "KMID": (close - open_) / open_,
        "KLEN": (high - low) / open_,
        "KMID2": (close - open_) / span,
        "KUP": (high - np.maximum(open_, close)) / open_,
        "KUP2": (high - np.maximum(open_, close)) / span,
        "KLOW": (np.minimum(open_, close) - low) / open_,
        "KLOW2": (np.minimum(open_, close) - low) / span,
        "KSFT": (2 * close - high - low) / open_,
        "KSFT2": (2 * close - high - low) / span,
        "OPEN0": open_ / close,
        "HIGH0": high / close,
        "LOW0": low / close,
        "VWAP0": vwap / close,
    }
    for window in _WINDOWS:
        previous = _shift(close, 1)
        gain = close - previous
        loss = previous - close
        abs_move = gain.abs()
        volume_move = volume - _shift(volume, 1)
        abs_volume = volume_move.abs()
        known = close.notna() & previous.notna()
        up = (close > previous).astype("float64").where(known)
        down = (close < previous).astype("float64").where(known)
        price_change = (close / previous).replace([np.inf, -np.inf], np.nan)
        volume_change = np.log((volume / _shift(volume, 1)).replace([np.inf, -np.inf], np.nan) + 1)
        wv = (price_change - 1).abs() * volume
        columns[f"ROC{window}"] = _shift(close, window) / close
        columns[f"MA{window}"] = _rolling(close, window, "mean") / close
        columns[f"STD{window}"] = _rolling(close, window, "std") / close
        slope = _group_apply(close, lambda values: _rolling_slope(values, window))
        columns[f"BETA{window}"] = slope / close
        columns[f"RSQR{window}"] = _group_apply(close, lambda values: _rolling_rsquare(values, window))
        columns[f"RESI{window}"] = _group_apply(close, lambda values: _rolling_residual(values, window)) / close
        columns[f"MAX{window}"] = _rolling(high, window, "max") / close
        columns[f"MIN{window}"] = _rolling(low, window, "min") / close
        columns[f"QTLU{window}"] = _group_apply(close, lambda values: _rolling_quantile(values, window, 0.8)) / close
        columns[f"QTLD{window}"] = _group_apply(close, lambda values: _rolling_quantile(values, window, 0.2)) / close
        columns[f"RANK{window}"] = _group_apply(close, lambda values: _rolling_rank(values, window))
        lowest = _rolling(low, window, "min")
        highest = _rolling(high, window, "max")
        columns[f"RSV{window}"] = (close - lowest) / (highest - lowest + eps)
        columns[f"IMAX{window}"] = _group_apply(high, lambda values: _rolling_since_extreme(values, window, "max")) / window
        columns[f"IMIN{window}"] = _group_apply(low, lambda values: _rolling_since_extreme(values, window, "min")) / window
        imax = _group_apply(high, lambda values: _rolling_since_extreme(values, window, "max"))
        imin = _group_apply(low, lambda values: _rolling_since_extreme(values, window, "min"))
        columns[f"IMXD{window}"] = (imax - imin) / window
        log_volume = np.log(volume + 1)
        columns[f"CORR{window}"] = _group_corr(close, log_volume, window)
        columns[f"CORD{window}"] = _group_corr(price_change, volume_change, window)
        columns[f"CNTP{window}"] = _rolling(up, window, "mean")
        columns[f"CNTN{window}"] = _rolling(down, window, "mean")
        columns[f"CNTD{window}"] = columns[f"CNTP{window}"] - columns[f"CNTN{window}"]
        sum_gain = _rolling(gain.clip(lower=0), window, "sum")
        sum_loss = _rolling(loss.clip(lower=0), window, "sum")
        sum_abs = _rolling(abs_move, window, "sum") + eps
        columns[f"SUMP{window}"] = sum_gain / sum_abs
        columns[f"SUMN{window}"] = sum_loss / sum_abs
        columns[f"SUMD{window}"] = (sum_gain - sum_loss) / sum_abs
        columns[f"VMA{window}"] = _rolling(volume, window, "mean") / (volume + eps)
        columns[f"VSTD{window}"] = _rolling(volume, window, "std") / (volume + eps)
        columns[f"WVMA{window}"] = _rolling(wv, window, "std") / (_rolling(wv, window, "mean") + eps)
        vol_up = volume_move.clip(lower=0)
        vol_down = (-volume_move).clip(lower=0)
        sum_vol_up = _rolling(vol_up, window, "sum")
        sum_vol_down = _rolling(vol_down, window, "sum")
        sum_vol_abs = _rolling(abs_volume, window, "sum") + eps
        columns[f"VSUMP{window}"] = sum_vol_up / sum_vol_abs
        columns[f"VSUMN{window}"] = sum_vol_down / sum_vol_abs
        columns[f"VSUMD{window}"] = (sum_vol_up - sum_vol_down) / sum_vol_abs
    out = pd.DataFrame(columns, index=frame.index)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out.reindex(panel.index)


def _shift(series: pd.Series, window: int) -> pd.Series:
    return series.groupby(level="instrument", sort=False).shift(window)


def _rolling(series: pd.Series, window: int, how: str) -> pd.Series:
    grouped = series.groupby(level="instrument", sort=False)
    values = getattr(grouped.rolling(window, min_periods=window), how)()
    if isinstance(values.index, pd.MultiIndex) and values.index.nlevels == series.index.nlevels + 1:
        values = values.droplevel(0)
    return values.reindex(series.index)


def _group_apply(series: pd.Series, fn) -> pd.Series:
    pieces: list[np.ndarray] = []
    expected = 0
    for _, values in series.groupby(level="instrument", sort=False):
        array = values.to_numpy(dtype=np.float64)
        pieces.append(fn(array))
        expected += len(array)
    if expected != len(series):
        raise ValueError("行情没有按股票排好，Alpha158 对不齐")
    return pd.Series(np.concatenate(pieces), index=series.index)


def _group_corr(left: pd.Series, right: pd.Series, window: int) -> pd.Series:
    aligned = pd.DataFrame({"left": left, "right": right})
    pieces: list[np.ndarray] = []
    expected = 0
    for _, values in aligned.groupby(level="instrument", sort=False):
        pieces.append(_rolling_corr(values["left"].to_numpy(dtype=np.float64), values["right"].to_numpy(dtype=np.float64), window))
        expected += len(values)
    if expected != len(aligned):
        raise ValueError("行情没有按股票排好，Alpha158 对不齐")
    return pd.Series(np.concatenate(pieces), index=aligned.index)


def _windows(values: np.ndarray, window: int) -> np.ndarray | None:
    if len(values) < window:
        return None
    view = np.lib.stride_tricks.sliding_window_view(values, window)
    if np.isnan(view).any():
        return view
    return view


def _finite_mask(view: np.ndarray) -> np.ndarray:
    return ~np.isnan(view).any(axis=1)


def _rolling_slope(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    slope, _, _ = _fit(view)
    out[window - 1 :] = slope
    return out


def _rolling_rsquare(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    _, rsquare, _ = _fit(view)
    out[window - 1 :] = rsquare
    return out


def _rolling_residual(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    _, _, residual = _fit(view)
    out[window - 1 :] = residual
    return out


def _fit(view: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    window = view.shape[1]
    x = np.arange(window, dtype=np.float64)
    x = x - x.mean()
    denom = float(np.dot(x, x))
    finite = _finite_mask(view)
    slope = np.full(len(view), np.nan)
    rsquare = np.full(len(view), np.nan)
    residual = np.full(len(view), np.nan)
    if not finite.any() or denom == 0:
        return slope, rsquare, residual
    block = view[finite]
    centered = block - block.mean(axis=1)[:, None]
    numer = (centered * x).sum(axis=1)
    fitted_slope = numer / denom
    vary = (centered**2).sum(axis=1)
    fitted_rsq = np.divide(numer**2, denom * vary, out=np.zeros(len(numer)), where=vary > 0)
    mean_x = (window - 1) / 2
    intercept = block.mean(axis=1) - fitted_slope * mean_x
    fitted_last = intercept + fitted_slope * (window - 1)
    slope[finite] = fitted_slope
    rsquare[finite] = fitted_rsq
    residual[finite] = block[:, -1] - fitted_last
    return slope, rsquare, residual


def _rolling_quantile(values: np.ndarray, window: int, quantile: float) -> np.ndarray:
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    finite = _finite_mask(view)
    if finite.any():
        out_block = np.full(finite.sum(), np.nan)
        out_block[:] = np.quantile(view[finite], quantile, axis=1)
        placed = np.full(len(view), np.nan)
        placed[finite] = out_block
        out[window - 1 :] = placed
    return out


def _rolling_rank(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    finite = _finite_mask(view)
    if finite.any():
        block = view[finite]
        pct = (block <= block[:, -1:]).sum(axis=1) / window
        placed = np.full(len(view), np.nan)
        placed[finite] = pct
        out[window - 1 :] = placed
    return out


def _rolling_since_extreme(values: np.ndarray, window: int, kind: str) -> np.ndarray:
    """Days from today back to the latest max or min. Today is 0."""
    out = np.full(len(values), np.nan)
    view = _windows(values, window)
    if view is None:
        return out
    finite = _finite_mask(view)
    if not finite.any():
        return out
    block = view[finite]
    if kind == "max":
        since = np.argmax(block[:, ::-1], axis=1)
    else:
        since = np.argmin(block[:, ::-1], axis=1)
    placed = np.full(len(view), np.nan)
    placed[finite] = since.astype(np.float64)
    out[window - 1 :] = placed
    return out


def _rolling_corr(left: np.ndarray, right: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(left), np.nan)
    if len(left) < window:
        return out
    view_left = np.lib.stride_tricks.sliding_window_view(left, window)
    view_right = np.lib.stride_tricks.sliding_window_view(right, window)
    finite = _finite_mask(view_left) & _finite_mask(view_right)
    if not finite.any():
        return out
    a = view_left[finite]
    b = view_right[finite]
    a = a - a.mean(axis=1)[:, None]
    b = b - b.mean(axis=1)[:, None]
    numer = (a * b).sum(axis=1)
    denom = np.sqrt((a**2).sum(axis=1) * (b**2).sum(axis=1))
    corr = np.divide(numer, denom, out=np.zeros(len(numer)), where=denom > 0)
    placed = np.full(len(view_left), np.nan)
    placed[finite] = corr
    out[window - 1 :] = placed
    return out
