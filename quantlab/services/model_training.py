"""Load the last compiled module while the .py source is missing."""

from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "model_training.pyc"
_code = SourcelessFileLoader(__name__, str(_pyc)).get_code(__name__)
if _code is None:
    raise ImportError(f"无法从字节码恢复：{_pyc}")
exec(_code, globals())

from contextlib import contextmanager
from typing import Any

import pandas as pd

_attach_label_impl = attach_label
_fit_lgb_impl = fit_lgb
_fit_xgb_impl = fit_xgb
_fit_estimator_impl = fit_estimator
_PLATFORM_DEFAULT_SEED = 123


def _norm_label_date(value) -> str:
    text = str(value).replace("-", "").replace(" ", "").replace("T", "")
    return text[:8]


def validation_date_cutoff(dates, fraction: float = 0.8) -> str | None:
    values = sorted({_norm_label_date(item) for item in pd.Series(dates).dropna() if str(item).strip()})
    if len(values) < 5:
        return None
    train_count = max(1, min(len(values) - 1, int(len(values) * fraction)))
    return values[train_count - 1]


def restrict_fit_sample(features, target, dates, params):
    if dates is None or features is None or getattr(features, "empty", False):
        return features, target, dates
    train_days, valid_days = holdout_split(dates, params)
    if not train_days or not valid_days:
        return features, target, dates
    ordered = pd.Series(dates, index=features.index).map(_norm_label_date)
    mask = ordered.isin(set(train_days))
    if int(mask.sum()) == 0:
        return features, target, dates
    sliced_target = target.loc[mask] if hasattr(target, "loc") else target
    return features.loc[mask], sliced_target, ordered.loc[mask]


def holdout_split(dates, params=None) -> tuple[list[str], list[str]]:
    params = params or {}
    unique = sorted({_norm_label_date(item) for item in pd.Series(dates).dropna() if str(item).strip()})
    val_from = params.get("validation_date_from")
    if val_from:
        val_from = _norm_label_date(val_from)
        val_to_raw = params.get("validation_date_to")
        val_to = _norm_label_date(val_to_raw) if val_to_raw else (unique[-1] if unique else val_from)
        fit_to_raw = params.get("train_fit_date_to")
        fit_to = _norm_label_date(fit_to_raw) if fit_to_raw else None
        train_days = [day for day in unique if day < val_from]
        if fit_to:
            train_days = [day for day in train_days if day <= fit_to]
        valid_days = [day for day in unique if val_from <= day <= val_to]
        return train_days, valid_days
    cutoff = validation_date_cutoff(unique)
    if not cutoff:
        return unique, []
    return [day for day in unique if day <= cutoff], [day for day in unique if day > cutoff]


def _configured_seed(params) -> int | None:
    if not isinstance(params, dict) or "random_seed" not in params:
        return None
    value = params.get("random_seed")
    if value is None or value == "":
        return None
    return int(value)


@contextmanager
def _temporary_ranker_seed(params):
    seed = _configured_seed(params)
    if seed is None:
        yield seed
        return
    previous = globals().get("LIGHTGBM_RANKER_SEED", _PLATFORM_DEFAULT_SEED)
    globals()["LIGHTGBM_RANKER_SEED"] = seed
    try:
        yield seed
    finally:
        globals()["LIGHTGBM_RANKER_SEED"] = previous


def attach_label(frame, holding_days=None):
    labeled = _attach_label_impl(frame, holding_days=holding_days)
    if "_raw" not in labeled.columns or "date" not in labeled.columns:
        return labeled
    labeled = labeled.copy()
    parts = []
    for _, group in labeled.groupby(labeled["date"].map(_norm_label_date), sort=False):
        updated = group.copy()
        updated["_target"] = _bin_future_return(updated["_raw"], LIGHTGBM_LABEL_BINS)
        parts.append(updated)
    return pd.concat(parts).sort_index()


def fit_lgb(features, target, params, dates=None):
    with _temporary_ranker_seed(params):
        return _fit_lgb_impl(features, target, params, dates)


def fit_xgb(features, target, params, dates=None):
    with _temporary_ranker_seed(params):
        return _fit_xgb_impl(features, target, params, dates)


def fit_estimator(kind, features, target, params, dates=None):
    resolved = resolve_kind(kind)
    seed = _configured_seed(params)
    if resolved == "random_forest" and seed is not None and seed != _PLATFORM_DEFAULT_SEED:
        try:
            from sklearn.ensemble import RandomForestRegressor
        except ImportError as error:
            raise RuntimeError("当前环境没有安装 scikit-learn，随机森林无法训练。") from error
        leaf = int(params.get("min_child_samples") or 20)
        model = RandomForestRegressor(
            n_estimators=int(params.get("number_of_trees") or 20),
            max_depth=int(params.get("max_depth") or 8),
            min_samples_leaf=max(1, leaf),
            n_jobs=-1,
            random_state=seed,
        )
        return model.fit(features.to_numpy(dtype=float), target.to_numpy(dtype=float))
    return _fit_estimator_impl(kind, features, target, params, dates)
