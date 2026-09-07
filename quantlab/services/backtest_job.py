"""Load the last compiled module while the .py source is missing."""

from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "backtest_job.pyc"
_code = SourcelessFileLoader(__name__, str(_pyc)).get_code(__name__)
if _code is None:
    raise ImportError(f"无法从字节码恢复：{_pyc}")
exec(_code, globals())

from contextlib import contextmanager
from contextvars import ContextVar
from operator import eq, ge, gt, le, lt, ne
from typing import Any, Iterator

import pandas as pd

from quantlab.services.ranking_metrics import attach_ranking_metrics, capture_predictions
from quantlab.services.trade_filters import FIELD_ALIASES, needs_sma200, parse_expr

_attach_label = attach_label
_once_embargo: tuple[str, Any, str | None] | None = None
_pretrade_allowed_dates: ContextVar[set[str] | None] = ContextVar("pretrade_allowed_dates", default=None)
_COMPARE = {"==": eq, "!=": ne, ">": gt, "<": lt, ">=": ge, "<=": le}
_FIELD_ALIASES = {**FIELD_ALIASES, "sma200": "sma_200", "ma200": "sma_200"}

_attach_label = attach_label
_once_embargo: tuple[str, Any, str | None] | None = None


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
    return _run_portfolio(frame, predictions, config)


def _performance_metrics(trades, config, frame, equity_curve=None, raw_root=None):
    metrics = _compute_performance_metrics(
        trades, config, frame, equity_curve=equity_curve, raw_root=raw_root
    )
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


_original_filter = BacktestJobService._filter


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
    result = _original_filter(frame, section, notes)
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


BacktestJobService._filter = staticmethod(_filter)

_original_execute = BacktestJobService.execute


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
    token = _pretrade_allowed_dates.set(_benchmark_allowed_dates(self, config))
    try:
        if config.get("kind") == "rule_signal":
            from quantlab.services.rule_backtest import execute_rule_signal

            return execute_rule_signal(self, run_id)
        if walk_forward_mode(config) != "once":
            return _original_execute(self, run_id)
        test = config.get("test") or {}
        predict_from = test.get("date_from")
        if not predict_from:
            return _original_execute(self, run_id)
        hyper = config.get("hyperparameters") if isinstance(config.get("hyperparameters"), dict) else {}
        validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
        validation_from = hyper.get("validation_date_from") or validation.get("date_from")
        with once_label_embargo(predict_from, config.get("holding_days"), validation_from=validation_from):
            return _original_execute(self, run_id)
    finally:
        _pretrade_allowed_dates.reset(token)


BacktestJobService.execute = execute
