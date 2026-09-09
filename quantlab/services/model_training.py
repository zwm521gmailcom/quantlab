"""Register a LightGBM model design and share training helpers with backtests."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from os import cpu_count
from typing import Any

import numpy as np
import pandas as pd

from quantlab.config import Settings
from quantlab.domain.status import LifecycleStatus, transition
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.factor_manual import grouped_rolling
from quantlab.services.strategy_center import StrategyCenterService

LABEL_GAIN_LINEAR = "linear_0_19"
LABEL_GAIN_EXPONENTIAL = "exponential"
LABEL_GAIN_CHOICES = {LABEL_GAIN_LINEAR, LABEL_GAIN_EXPONENTIAL}
METRIC_NDCG = "ndcg"
METRIC_NONE = "none"
METRIC_CHOICES = {METRIC_NDCG, METRIC_NONE}
DEFAULT_NDCG_EVAL_AT = 10
DEFAULT_NDCG_DISCOUNT_BASE = 1.0
WALK_FORWARD_ONCE = "once"
WALK_FORWARD_LOOKBACK = "lookback"
WALK_FORWARD_CHOICES = {WALK_FORWARD_ONCE, WALK_FORWARD_LOOKBACK}
DEFAULT_LOOKBACK_MONTHS = 12
DEFAULT_HYPERPARAMS = {
    "number_of_trees": 5,
    "max_bins": 511,
    "num_leaves": 30,
    "min_child_samples": 1000,
    "learning_rate": 0.1,
    "label_gain": LABEL_GAIN_LINEAR,
    "metric": METRIC_NDCG,
    "ndcg_eval_at": DEFAULT_NDCG_EVAL_AT,
    "ndcg_discount_base": DEFAULT_NDCG_DISCOUNT_BASE,
}
XGBOOST_HYPERPARAMS = {
    "number_of_trees": 5,
    "max_bins": 256,
    "max_depth": 6,
    "min_child_samples": 1,
    "learning_rate": 0.1,
    "label_gain": LABEL_GAIN_LINEAR,
    "metric": METRIC_NDCG,
    "ndcg_eval_at": DEFAULT_NDCG_EVAL_AT,
}
RANKER_KINDS = frozenset({"lightgbm_tree", "xgboost_tree"})
MODEL_KINDS = {
    "lightgbm_tree": {
        "kind": "lightgbm_tree",
        "name": "树模型（LightGBM）",
        "method": "lightgbm_ranker",
        "summary": "用排序树给股票打分。回测时按截面排序训练。",
        "hyperparameters": DEFAULT_HYPERPARAMS,
    },
    "xgboost_tree": {
        "kind": "xgboost_tree",
        "name": "树模型（XGBoost）",
        "method": "xgboost_ranker",
        "summary": "用 XGBoost 排序树给股票打分。回测时按截面排序训练。",
        "hyperparameters": XGBOOST_HYPERPARAMS,
    },
    "random_forest": {
        "kind": "random_forest",
        "name": "随机森林",
        "method": "random_forest_regressor",
        "summary": "多棵决策树投票打分，通常比单棵树更稳。",
        "hyperparameters": {
            "number_of_trees": 20,
            "max_depth": 8,
            "min_child_samples": 20,
        },
    },
    "ridge_linear": {
        "kind": "ridge_linear",
        "name": "线性模型（Ridge）",
        "method": "ridge_regressor",
        "summary": "用线性回归把因子拟合成分数，带 L2 正则。",
        "hyperparameters": {"alpha": 1.0},
    },
    "lasso": {
        "kind": "lasso",
        "name": "LASSO",
        "method": "lasso_regressor",
        "summary": "线性模型，会自动把弱因子系数压到 0。",
        "hyperparameters": {"alpha": 0.001},
    },
    "elastic_net": {
        "kind": "elastic_net",
        "name": "弹性网络",
        "method": "elastic_net_regressor",
        "summary": "Ridge 和 LASSO 的折中，可调 L1 比例。",
        "hyperparameters": {"alpha": 0.001, "l1_ratio": 0.5},
    },
    "ols": {
        "kind": "ols",
        "name": "普通线性回归",
        "method": "linear_regressor",
        "summary": "不加正则，直接用所选因子拟合分数。",
        "hyperparameters": {},
    },
    "huber": {
        "kind": "huber",
        "name": "稳健回归",
        "method": "huber_regressor",
        "summary": "对极端收益不那么敏感，适合有不少异常股票的时候。",
        "hyperparameters": {"alpha": 0.0001, "epsilon": 1.35},
    },
    "factor_rank": {
        "kind": "factor_rank",
        "name": "单因子排名",
        "method": "deterministic_factor_rank_proxy",
        "summary": "不训练，直接按回测页所选因子值排名。",
        "hyperparameters": {},
    },
}
MIN_TRAIN_ROWS = 30
LIGHTGBM_RANKER_SEED = 123
LIGHTGBM_LABEL_BINS = 20
DEFAULT_STRATEGY = {
    "stock_scope": "中国A股（SH/SZ）",
    "rebalance_every": 1,
    "holding_days": 2,
    "top_n": 10,
    "weighting": "equal",
    "signal_time": "close",
    "buy_price": "open",
    "sell_price": "close",
    "buy_fee": {"rate": 0.0003, "minimum": 5},
    "sell_fee": {"rate": 0.0005, "minimum": 5},
    "slippage": 0,
    "benchmark": "000300.SH",
}


def rank_label_column(kind: str, params: Any = None) -> str:
    if resolve_kind(kind, params) in RANKER_KINDS:
        return "_target"
    return "_raw"


def normalize_date(value: str | None, fallback: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return fallback


def normalize_ndcg_metric(value: Any, default: str = "ndcg") -> str:
    text = str(value if value is not None and str(value).strip() != "" else default).strip().lower()
    if text in {METRIC_NDCG, "on", "true", "1", "yes"}:
        return METRIC_NDCG
    if text in {METRIC_NONE, "off", "false", "0", "no"}:
        return METRIC_NONE
    raise ValueError("NDCG 评估只支持开启或关闭。")


def normalize_ndcg_eval_at(value: Any, default: int = 10) -> int:
    parsed = int(value if value is not None and str(value).strip() != "" else default)
    if parsed < 1:
        raise ValueError("NDCG eval_at 要填正整数。")
    return parsed


def normalize_ndcg_discount_base(value: Any, default: float = 1.0) -> float:
    parsed = float(value if value is not None and str(value).strip() != "" else default)
    if parsed < 1:
        raise ValueError("NDCG discount_base 不能小于 1。")
    return parsed


def resolve_kind(kind: Any, params: Any = None) -> str:
    text = str(kind or "").strip()
    if text in MODEL_KINDS:
        return text
    if isinstance(params, dict):
        nested = str(params.get("kind") or "").strip()
        if nested in MODEL_KINDS:
            return nested
    return "lightgbm_tree"


def normalize_walk_forward(raw: Any, default: str = "once") -> str:
    value = str(raw if raw not in {None, ""} else default).strip().lower()
    aliases = {
        "rolling": WALK_FORWARD_LOOKBACK,
        "monthly": WALK_FORWARD_LOOKBACK,
        "expanding": WALK_FORWARD_LOOKBACK,
        "expanding_monthly": WALK_FORWARD_LOOKBACK,
        "lookback_months": WALK_FORWARD_LOOKBACK,
        "fixed_lookback": WALK_FORWARD_LOOKBACK,
    }
    value = aliases.get(value, value)
    if value not in WALK_FORWARD_CHOICES:
        raise ValueError("训练方式只支持一次训练或定长回看。")
    return value


def normalize_train_lookback_months(walk_forward: str, raw: Any) -> int | None:
    if walk_forward != WALK_FORWARD_LOOKBACK:
        return None
    if raw in {None, ""}:
        return DEFAULT_LOOKBACK_MONTHS
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("回看月数必须是正整数。")
        return value
    except (TypeError, ValueError) as error:
        raise ValueError("回看月数必须是正整数。") from error


def with_train_protocol(
    params: dict[str, Any],
    src: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    walk = normalize_walk_forward(src.get("walk_forward"), defaults.get("walk_forward", WALK_FORWARD_ONCE))
    lookback = normalize_train_lookback_months(
        walk,
        src.get("train_lookback_months", src.get("train_period_months", defaults.get("train_lookback_months"))),
    )
    out = {**params, "walk_forward": walk}
    if lookback is not None:
        out["train_lookback_months"] = lookback
    return out


def normalize_hyperparams(raw: Any, kind: str | None = None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    resolved = resolve_kind(kind, src)
    defaults = MODEL_KINDS[resolved]["hyperparameters"]
    if resolved == "lightgbm_tree":
        trees = max(1, int(src.get("number_of_trees") or defaults["number_of_trees"]))
        bins = max(16, min(511, int(src.get("max_bins") or defaults["max_bins"])))
        leaves = max(2, int(src.get("num_leaves") or defaults["num_leaves"]))
        samples = max(1, int(src.get("min_child_samples") or defaults["min_child_samples"]))
        rate = float(src.get("learning_rate") or defaults["learning_rate"])
        if rate <= 0 or rate > 1:
            raise ValueError("学习率要在 0 到 1 之间。")
        gain = str(src.get("label_gain") or defaults["label_gain"]).strip()
        if gain not in LABEL_GAIN_CHOICES:
            raise ValueError("label_gain 只支持线性 0–19 或指数默认。")
        return with_train_protocol(
            {
                "number_of_trees": trees,
                "max_bins": bins,
                "num_leaves": leaves,
                "min_child_samples": samples,
                "learning_rate": rate,
                "label_gain": gain,
                "metric": normalize_ndcg_metric(src.get("metric"), defaults["metric"]),
                "ndcg_eval_at": normalize_ndcg_eval_at(src.get("ndcg_eval_at"), defaults["ndcg_eval_at"]),
                "ndcg_discount_base": normalize_ndcg_discount_base(
                    src.get("ndcg_discount_base"), defaults["ndcg_discount_base"]
                ),
            },
            src,
            defaults,
        )
    if resolved == "xgboost_tree":
        trees = max(1, int(src.get("number_of_trees") or defaults["number_of_trees"]))
        bins = max(16, min(256, int(src.get("max_bins") or defaults["max_bins"])))
        depth = max(1, int(src.get("max_depth") or defaults["max_depth"]))
        samples = max(1, int(src.get("min_child_samples") or defaults["min_child_samples"]))
        rate = float(src.get("learning_rate") or defaults["learning_rate"])
        if rate <= 0 or rate > 1:
            raise ValueError("学习率要在 0 到 1 之间。")
        gain = str(src.get("label_gain") or defaults["label_gain"]).strip()
        if gain not in LABEL_GAIN_CHOICES:
            raise ValueError("label_gain 只支持线性 0–19 或指数默认。")
        return with_train_protocol(
            {
                "number_of_trees": trees,
                "max_bins": bins,
                "max_depth": depth,
                "min_child_samples": samples,
                "learning_rate": rate,
                "label_gain": gain,
                "metric": normalize_ndcg_metric(src.get("metric"), defaults["metric"]),
                "ndcg_eval_at": normalize_ndcg_eval_at(src.get("ndcg_eval_at"), defaults["ndcg_eval_at"]),
            },
            src,
            defaults,
        )
    if resolved == "random_forest":
        trees = max(1, int(src.get("number_of_trees") or defaults["number_of_trees"]))
        depth = max(1, int(src.get("max_depth") or defaults["max_depth"]))
        samples = max(1, int(src.get("min_child_samples") or defaults["min_child_samples"]))
        return with_train_protocol(
            {"number_of_trees": trees, "max_depth": depth, "min_child_samples": samples},
            src,
            defaults,
        )
    if resolved in {"ridge_linear", "lasso"}:
        alpha = float(src["alpha"]) if src.get("alpha") is not None else float(defaults["alpha"])
        if alpha < 0:
            raise ValueError("正则强度不能为负数。")
        return with_train_protocol({"alpha": alpha}, src, defaults)
    if resolved == "elastic_net":
        alpha = float(src["alpha"]) if src.get("alpha") is not None else float(defaults["alpha"])
        ratio = float(src["l1_ratio"]) if src.get("l1_ratio") is not None else float(defaults["l1_ratio"])
        if alpha < 0:
            raise ValueError("正则强度不能为负数。")
        if ratio < 0 or ratio > 1:
            raise ValueError("L1 比例要在 0 到 1 之间。")
        return with_train_protocol({"alpha": alpha, "l1_ratio": ratio}, src, defaults)
    if resolved == "huber":
        alpha = float(src["alpha"]) if src.get("alpha") is not None else float(defaults["alpha"])
        epsilon = float(src["epsilon"]) if src.get("epsilon") is not None else float(defaults["epsilon"])
        if alpha < 0:
            raise ValueError("正则强度不能为负数。")
        if epsilon < 1:
            raise ValueError("稳健回归的阈值不能小于 1。")
        return with_train_protocol({"alpha": alpha, "epsilon": epsilon}, src, defaults)
    if resolved == "ols":
        return with_train_protocol({}, src, {"walk_forward": WALK_FORWARD_ONCE})
    return {}


def normalize_market_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "date" not in result.columns and "trade_date" in result.columns:
        result["date"] = result["trade_date"]
    if "instrument" not in result.columns and "ts_code" in result.columns:
        result["instrument"] = result["ts_code"]
    if "trade_date" not in result.columns and "date" in result.columns:
        result["trade_date"] = result["date"]
    if "ts_code" not in result.columns and "instrument" in result.columns:
        result["ts_code"] = result["instrument"]
    if "date" in result.columns:
        result["date"] = result["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    if "trade_date" in result.columns:
        result["trade_date"] = result["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    if "suspended" not in result.columns and "is_suspended" in result.columns:
        result["suspended"] = result["is_suspended"].astype(bool)
    return result


def feature_name(item: Any) -> str:
    if isinstance(item, str):
        return item.removeprefix("factor_")
    if not isinstance(item, dict):
        return ""
    if item.get("field"):
        return str(item["field"]).removeprefix("factor_")
    return str(item.get("factor_id") or item.get("factor") or "").removeprefix("factor_")


def enrich_feature_columns(frame: pd.DataFrame, refs: list[Any]) -> pd.DataFrame:
    result = normalize_market_frame(frame)
    if "instrument" not in result.columns or "hfq_close" not in result.columns:
        return result
    result["hfq_close"] = pd.to_numeric(result["hfq_close"], errors="coerce")
    group = result.groupby("instrument", sort=False)
    for item in refs:
        field = feature_name(item)
        if not field or field in result.columns:
            continue
        if field == "momentum_5":
            result[field] = result["hfq_close"] / group["hfq_close"].shift(5) - 1
            continue
        if field != "volatility_5":
            continue
        ret = result["hfq_close"] / group["hfq_close"].shift(1) - 1
        result[field] = grouped_rolling(ret, result["instrument"], 5, "std")
    return result


def _attach_label_impl(frame: pd.DataFrame, holding_days: int = 2) -> pd.DataFrame:
    result = frame.sort_values(["instrument", "date"]).copy()
    open_col = "hfq_open" if "hfq_open" in result.columns else "open"
    close_col = "hfq_close" if "hfq_close" in result.columns else "close"
    if open_col not in result.columns or close_col not in result.columns:
        raise ValueError("行情里没有开盘价或收盘价，算不出训练标签。")
    hold = max(int(holding_days or 2), 1)
    group = result.groupby("instrument", sort=False)
    next_open = pd.to_numeric(group[open_col].shift(-1), errors="coerce")
    future_close = pd.to_numeric(group[close_col].shift(-(1 + hold)), errors="coerce")
    raw = future_close.div(next_open).sub(1)
    if "high" in result.columns and "low" in result.columns:
        next_high = pd.to_numeric(group["high"].shift(-1), errors="coerce")
        next_low = pd.to_numeric(group["low"].shift(-1), errors="coerce")
        one_word = next_high.notna() & next_low.notna() & next_high.eq(next_low)
        raw = raw.where(~one_word)
    result["_raw"] = raw
    result["_target"] = _bin_future_return(raw)
    result.attrs["label_open_column"] = open_col
    result.attrs["label_close_column"] = close_col
    return result


def label_gain_values(mode: str) -> list[int]:
    if str(mode) == LABEL_GAIN_EXPONENTIAL:
        return [2**i - 1 for i in range(LIGHTGBM_LABEL_BINS)]
    return list(range(LIGHTGBM_LABEL_BINS))


def _bin_future_return(raw: pd.Series, bins: int = 20) -> pd.Series:
    valid = raw.dropna()
    target = pd.Series(np.nan, index=raw.index, dtype="float64")
    if valid.empty or bins < 1:
        return target
    clipped = valid.clip(lower=float(valid.quantile(0.01)), upper=float(valid.quantile(0.99)))
    quantiles = min(int(bins), len(clipped))
    if quantiles < 2:
        return target
    edges = np.quantile(clipped.to_numpy(dtype=float), np.arange(1, quantiles) / quantiles)
    labels = np.searchsorted(edges, clipped.to_numpy(dtype=float), side="left").astype("float64")
    target.loc[clipped.index] = labels
    return target


def _ordered_rank_set(
    features: pd.DataFrame,
    target: pd.Series,
    dates: pd.Series | None,
) -> tuple[pd.DataFrame, pd.Series, list[int]]:
    if dates is None:
        raise ValueError("排序树训练需要按日期分组。")
    order = features.copy()
    order["_date"] = pd.Series(np.asarray(dates), index=features.index).astype(str)
    order["_y"] = pd.Series(np.asarray(target), index=features.index)
    order = order.sort_values("_date")
    group = [int(size) for size in order.groupby("_date", sort=True).size().tolist()]
    fitted = order.drop(columns=["_date", "_y"])
    return fitted, order["_y"], group


def _fit_lgb_impl(
    features: pd.DataFrame,
    target: pd.Series,
    params: dict[str, Any],
    dates: pd.Series | None = None,
) -> Any:
    try:
        import lightgbm as lgb
    except ImportError as error:
        raise RuntimeError("当前环境没有安装 lightgbm，树模型无法训练。") from error
    fitted, labels, group = _ordered_rank_set(features, target, dates)
    train_set = lgb.Dataset(
        fitted.to_numpy(dtype=float),
        label=labels.to_numpy(dtype=float),
        group=group,
    )
    spec = {
        "objective": "lambdarank",
        "learning_rate": float(params["learning_rate"]),
        "num_leaves": int(params["num_leaves"]),
        "min_data_in_leaf": int(params["min_child_samples"]),
        "max_bin": int(params["max_bins"]),
        "label_gain": label_gain_values(params.get("label_gain") or LABEL_GAIN_LINEAR),
        "feature_fraction": 1.0,
        "bagging_fraction": 1.0,
        "seed": LIGHTGBM_RANKER_SEED,
        "verbosity": -1,
        "force_col_wise": True,
    }
    if str(params.get("metric") or METRIC_NDCG) == METRIC_NDCG:
        spec["metric"] = "ndcg"
        spec["eval_at"] = [int(params.get("ndcg_eval_at") or DEFAULT_NDCG_EVAL_AT)]
        spec["ndcg_discount_base"] = float(params.get("ndcg_discount_base") or DEFAULT_NDCG_DISCOUNT_BASE)
    else:
        spec["metric"] = "None"
    booster = lgb.train(
        spec,
        train_set,
        num_boost_round=int(params["number_of_trees"]),
        callbacks=[lgb.log_evaluation(period=0)],
    )
    return booster


def predict_lgb(model: Any, features: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict(features.to_numpy(dtype=float)), dtype=float)


def _fit_xgb_impl(
    features: pd.DataFrame,
    target: pd.Series,
    params: dict[str, Any],
    dates: pd.Series | None = None,
) -> Any:
    try:
        import xgboost as xgb
    except ImportError as error:
        raise RuntimeError("当前环境没有安装 xgboost，树模型无法训练。") from error
    fitted, labels, group = _ordered_rank_set(features, target, dates)
    train_set = xgb.DMatrix(fitted.to_numpy(dtype=float), label=labels.to_numpy(dtype=float))
    train_set.set_group(group)
    spec = {
        "objective": "rank:ndcg",
        "eta": float(params["learning_rate"]),
        "max_depth": int(params["max_depth"]),
        "min_child_weight": float(params["min_child_samples"]),
        "max_bin": int(params["max_bins"]),
        "tree_method": "hist",
        "ndcg_exp_gain": params.get("label_gain") != LABEL_GAIN_LINEAR,
        "seed": LIGHTGBM_RANKER_SEED,
        "verbosity": 0,
    }
    if str(params.get("metric") or METRIC_NDCG) == METRIC_NDCG:
        spec["eval_metric"] = f"ndcg@{int(params.get('ndcg_eval_at') or DEFAULT_NDCG_EVAL_AT)}"
    else:
        spec["disable_default_eval_metric"] = 1
    return xgb.train(spec, train_set, num_boost_round=int(params["number_of_trees"]))


def predict_xgb(model: Any, features: pd.DataFrame) -> np.ndarray:
    import xgboost as xgb
    return np.asarray(model.predict(xgb.DMatrix(features.to_numpy(dtype=float))), dtype=float)


def _fit_estimator_impl(
    kind: str,
    features: pd.DataFrame,
    target: pd.Series,
    params: dict[str, Any],
    dates: pd.Series | None = None,
) -> Any:
    resolved = resolve_kind(kind, params)
    if resolved == "factor_rank":
        return None
    if resolved == "lightgbm_tree":
        return fit_lgb(features, target, params, dates=dates)
    if resolved == "xgboost_tree":
        return fit_xgb(features, target, params, dates=dates)
    if resolved == "random_forest":
        try:
            from sklearn.ensemble import RandomForestRegressor
        except ImportError as error:
            raise RuntimeError("当前环境没有安装 scikit-learn，随机森林无法训练。") from error
        model = RandomForestRegressor(
            n_estimators=int(params.get("number_of_trees", 20)),
            max_depth=int(params.get("max_depth", 8)),
            min_samples_leaf=int(params.get("min_child_samples", 20)),
            n_jobs=-1,
            random_state=7,
        )
        model.fit(features.to_numpy(dtype=float), target.to_numpy(dtype=float))
        return model
    try:
        from sklearn.linear_model import ElasticNet, HuberRegressor, Lasso, LinearRegression, Ridge
    except ImportError as error:
        raise RuntimeError("当前环境没有安装 scikit-learn，线性模型无法训练。") from error
    matrix = features.to_numpy(dtype=float)
    labels = target.to_numpy(dtype=float)
    if resolved == "ridge_linear":
        model = Ridge(alpha=float(params.get("alpha", 1.0)))
    elif resolved == "lasso":
        model = Lasso(alpha=float(params.get("alpha", 0.001)), max_iter=4000)
    elif resolved == "elastic_net":
        model = ElasticNet(
            alpha=float(params.get("alpha", 0.001)),
            l1_ratio=float(params.get("l1_ratio", 0.5)),
            max_iter=4000,
        )
    elif resolved == "ols":
        model = LinearRegression()
    elif resolved == "huber":
        model = HuberRegressor(
            alpha=float(params.get("alpha", 0.0001)),
            epsilon=float(params.get("epsilon", 1.35)),
            max_iter=400,
        )
    else:
        raise ValueError("还不支持这种模型。")
    model.fit(matrix, labels)
    return model


def predict_estimator(kind: str, model: Any, features: pd.DataFrame) -> np.ndarray:
    resolved = resolve_kind(kind)
    if model is None or resolved == "factor_rank":
        return pd.to_numeric(features.iloc[:, 0], errors="coerce").to_numpy(dtype=float)
    if resolved == "lightgbm_tree":
        return predict_lgb(model, features)
    if resolved == "xgboost_tree":
        return predict_xgb(model, features)
    return np.asarray(model.predict(features.to_numpy(dtype=float)), dtype=float)


_PLATFORM_DEFAULT_SEED = 123
_BOOSTER_THREAD_LIMIT: int | None = None


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
    date_key = labeled["date"].map(_norm_label_date)
    labeled["_target"] = labeled.groupby(date_key, sort=False)["_raw"].transform(
        lambda values: _bin_future_return(values, LIGHTGBM_LABEL_BINS)
    )
    return labeled


def booster_thread_count(workers: int, cpu_fn: Callable[[], int | None] | None = None) -> int | None:
    if int(workers) <= 1:
        return None
    count = (cpu_fn or cpu_count)() or 1
    return max(1, int(count) // max(1, int(workers)))


@contextmanager
def booster_thread_limit(workers: int, cpu_fn: Callable[[], int | None] | None = None):
    global _BOOSTER_THREAD_LIMIT
    previous = _BOOSTER_THREAD_LIMIT
    _BOOSTER_THREAD_LIMIT = booster_thread_count(workers, cpu_fn)
    try:
        yield _BOOSTER_THREAD_LIMIT
    finally:
        _BOOSTER_THREAD_LIMIT = previous


def _call_with_capped_train(module_name: str, param_key: str, impl, *args):
    threads = _BOOSTER_THREAD_LIMIT
    if not threads:
        return impl(*args)
    module = __import__(module_name)
    original = module.train

    def wrapped(params_map, *rest, **kwargs):
        spec = dict(params_map)
        spec[param_key] = int(threads)
        return original(spec, *rest, **kwargs)

    module.train = wrapped
    try:
        return impl(*args)
    finally:
        module.train = original


def fit_lgb(features, target, params, dates=None):
    with _temporary_ranker_seed(params):
        return _call_with_capped_train("lightgbm", "num_threads", _fit_lgb_impl, features, target, params, dates)


def fit_xgb(features, target, params, dates=None):
    with _temporary_ranker_seed(params):
        return _call_with_capped_train("xgboost", "nthread", _fit_xgb_impl, features, target, params, dates)


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
            n_jobs=_BOOSTER_THREAD_LIMIT or -1,
            random_state=seed,
        )
        return model.fit(features.to_numpy(dtype=float), target.to_numpy(dtype=float))
    return _fit_estimator_impl(kind, features, target, params, dates)


class ModelTrainingService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        factors: FactorRepository,
        calculations: FactorCalculationService,
        strategy_center: StrategyCenterService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.calculations = calculations
        self.strategy_center = strategy_center

    def list_kinds(self) -> dict[str, Any]:
        items = []
        for spec in MODEL_KINDS.values():
            row = dict(spec)
            row["hyperparameters"] = dict(spec["hyperparameters"])
            keeper = self._keeper_for_kind(row["kind"])
            if keeper is None:
                items.append(row)
                continue
            row["name"] = keeper["model"].get("name") or row["name"]
            params = (keeper["version"].get("config") or {}).get("hyperparameters")
            if isinstance(params, dict):
                row["hyperparameters"] = {**spec["hyperparameters"], **params}
            items.append(row)
        return {"items": items}

    def registered_kinds(self) -> set[str]:
        kinds = set()
        for item in self.strategy_center.list_models().get("items") or []:
            for version in item.get("versions") or []:
                if version.get("status") != "published":
                    continue
                kind = str((version.get("config") or {}).get("kind") or "").strip()
                if not kind:
                    continue
                kinds.add(kind)
        return kinds

    def ensure_catalog(self) -> dict[str, Any]:
        existing = self.registered_kinds()
        created = []
        for kind, spec in MODEL_KINDS.items():
            if kind in existing:
                continue
            self.create_design({"kind": kind, "name": spec["name"]})
            created.append(kind)
        for kind in MODEL_KINDS:
            self._collapse_kind(kind)
        return {"created": created, "kinds": list(MODEL_KINDS)}

    def _research_dataset(self) -> tuple[str, str]:
        with self.database.connect() as connection:
            if not self.strategy_center._published_dataset(connection, "ds_canonical_market", "current"):
                raise ValueError("还没有可用的标准行情宽表，先去数据中心确认。")
        return ("ds_canonical_market", "current")

    def _kind_of(self, version: dict[str, Any]) -> str:
        return str((version.get("config") or {}).get("kind") or "").strip()

    def _latest_version(self, versions: list[dict[str, Any]]) -> dict[str, Any]:
        def key(version: dict[str, Any]) -> int:
            text = str(version.get("version_id") or "v0").lstrip("v")
            if text.isdigit():
                return int(text)
            return 0

        return max(versions, key=key)

    def _entries_for_kind(self, kind: str) -> list[dict[str, Any]]:
        entries = []
        for item in self.strategy_center.list_models().get("items") or []:
            published = [
                version
                for version in item.get("versions") or []
                if version.get("status") == "published" and self._kind_of(version) == kind
            ]
            if not published:
                continue
            entries.append({"model": item, "version": self._latest_version(published)})
        return entries

    def _keeper_for_kind(self, kind: str) -> dict[str, Any] | None:
        entries = self._entries_for_kind(kind)
        if not entries:
            return None
        preferred = [entry for entry in entries if entry["model"]["entity_id"] == f"model_{kind}"]
        if preferred:
            return preferred[0]
        ids = [entry["model"]["entity_id"] for entry in entries]
        placeholders = ",".join("?" * len(ids))
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT entity_id FROM models WHERE entity_id IN ({placeholders}) ORDER BY rowid DESC LIMIT 1",
                ids,
            ).fetchone()
        keep_id = str(row["entity_id"]) if row else ids[-1]
        return next(entry for entry in entries if entry["model"]["entity_id"] == keep_id)

    def _rename_entity(self, table: str, entity_id: str, name: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE {table} SET name=? WHERE entity_id=?", (name, entity_id))

    def _deprecate_other_versions(self, table: str, entity_id: str, keep_version_id: str) -> None:
        deprecated_status = transition(LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED).value
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE {table} SET status=? WHERE entity_id=? AND version_id!=? AND status='published'",
                (deprecated_status, entity_id, keep_version_id),
            )

    def _deprecate_entity(self, versions_table: str, parent_table: str, entity_id: str) -> None:
        deprecated_status = transition(LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED).value
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE {versions_table} SET status=? WHERE entity_id=? AND status='published'",
                (deprecated_status, entity_id),
            )
            parent_row = connection.execute(f"SELECT status FROM {parent_table} WHERE entity_id=?", (entity_id,)).fetchone()
            if parent_row is not None and parent_row["status"] == "published":
                connection.execute(f"UPDATE {parent_table} SET status=? WHERE entity_id=?", (deprecated_status, entity_id))

    def _collapse_kind(
        self,
        kind: str,
        keep_entity_id: str | None = None,
        keep_model_version_id: str | None = None,
        keep_strategy_id: str | None = None,
        keep_strategy_version_id: str | None = None,
    ) -> None:
        keeper = None if keep_entity_id else self._keeper_for_kind(kind)
        entity_id = keep_entity_id or (keeper["model"]["entity_id"] if keeper else "")
        if not entity_id:
            return None
        version_id = keep_model_version_id or (keeper["version"]["version_id"] if keeper else "")
        extra_model_ids = [
            entry["model"]["entity_id"]
            for entry in self._entries_for_kind(kind)
            if entry["model"]["entity_id"] != entity_id
        ]
        if version_id:
            self._deprecate_other_versions("model_versions", entity_id, version_id)
        for extra_id in extra_model_ids:
            self._deprecate_entity("model_versions", "models", extra_id)
        strategy_id = keep_strategy_id or f"strategy_{entity_id}"
        if keep_strategy_version_id:
            self._deprecate_other_versions("strategy_versions", strategy_id, keep_strategy_version_id)
        elif self.strategy_center.get_strategy(strategy_id):
            published = [
                version
                for version in (self.strategy_center.get_strategy(strategy_id) or {}).get("versions") or []
                if version.get("status") == "published"
            ]
            if published:
                self._deprecate_other_versions(
                    "strategy_versions",
                    strategy_id,
                    self._latest_version(published)["version_id"],
                )
        for extra_id in extra_model_ids:
            extra_strategy_id = f"strategy_{extra_id}"
            if not self.strategy_center.get_strategy(extra_strategy_id):
                continue
            self._deprecate_entity("strategy_versions", "strategies", extra_strategy_id)

    def create_design(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("请提交模型种类。")
        kind = str(body.get("kind") or "lightgbm_tree").strip() or "lightgbm_tree"
        spec = MODEL_KINDS.get(kind)
        if spec is None:
            raise ValueError("还不支持这种模型。")
        name = str(body.get("name") or "").strip() or spec["name"]
        params = normalize_hyperparams(body.get("hyperparameters") or spec["hyperparameters"], kind=kind)
        dataset_id, dataset_version_id = self._research_dataset()
        keeper = self._keeper_for_kind(kind)
        entity_id = keeper["model"]["entity_id"] if keeper else f"model_{kind}"
        if self.strategy_center.get_model(entity_id) is None:
            self.strategy_center.create_model(entity_id, name)
        else:
            self._rename_entity("models", entity_id, name)
        config = {
            "dataset_id": dataset_id,
            "dataset_version_id": dataset_version_id,
            "factor_versions": [],
            "hyperparameters": params,
            "method": spec["method"],
            "kind": kind,
            "label": {"definition": "t+1 open -> t+2 close"},
        }
        version = self.strategy_center.create_model_version(entity_id, config)
        published = self.strategy_center.publish_model_version(entity_id, version["version_id"])
        strategy_id = f"strategy_{entity_id}"
        strategy = self.strategy_center.get_strategy(strategy_id)
        if strategy is None:
            strategy = self.strategy_center.create_strategy(strategy_id, name)
        else:
            self._rename_entity("strategies", strategy_id, name)
        strategy_version = self.strategy_center.create_strategy_version(
            strategy_id,
            {
                **DEFAULT_STRATEGY,
                "dataset_id": dataset_id,
                "dataset_version_id": dataset_version_id,
                "model_entity_id": entity_id,
                "model_version_id": version["version_id"],
            },
        )
        published_strategy = self.strategy_center.publish_strategy_version(
            strategy_id, strategy_version["version_id"]
        )
        self._collapse_kind(kind, entity_id, version["version_id"], strategy_id, strategy_version["version_id"])
        model = self.strategy_center.get_model(entity_id) or {}
        return {
            "model": {**model, "status": "published"},
            "version": published,
            "strategy": {**strategy, "status": "published", "version": published_strategy},
            "design": {"kind": kind, "hyperparameters": params},
            "backtest_url": f"/backtests/new?model={entity_id}::{version['version_id']}",
        }
