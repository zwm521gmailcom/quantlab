"""Load the last compiled module while the .py source is missing."""

from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "backtest_workbench.pyc"
_code = SourcelessFileLoader(__name__, str(_pyc)).get_code(__name__)
if _code is None:
    raise ImportError(f"无法从字节码恢复：{_pyc}")
exec(_code, globals())

import json
from typing import Any

_original_validate = BacktestWorkbenchService.validate
_original_submit = BacktestWorkbenchService.submit
_DEFAULT_RANDOM_SEED = 123


def _compact_date(value) -> str:
    return str(value or "").replace("-", "").replace(".", "")[:8]


def _iso_date(value) -> str:
    compact = _compact_date(value)
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return str(value or "")


def _section_window(section):
    if not isinstance(section, dict):
        return None
    start, end = section.get("date_from"), section.get("date_to")
    if not start or not end:
        return None
    return _compact_date(start), _compact_date(end)


def _validation_section(config):
    section = config.get("validation") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return None
    if not section.get("date_from") or not section.get("date_to"):
        return None
    return section


def _expr_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _merge_exprs(*groups: Any) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for item in _expr_list(group):
            if item not in seen:
                seen.append(item)
    return seen


def _filter_dict(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {}
    filt = section.get("filter")
    return dict(filt) if isinstance(filt, dict) else {}


def _section_extra_expressions(section: Any) -> list[str]:
    filt = _filter_dict(section)
    extras = _expr_list(filt.get("expressions"))
    if filt.get("close_gt_ma200"):
        extras = _merge_exprs(extras, ["hfq_close > sma200"])
    if filt.get("close_lt_ma200"):
        extras = _merge_exprs(extras, ["hfq_close < sma200"])
    return extras


def _normalize_pretrade(raw: dict[str, Any]) -> dict[str, list[str]]:
    payload = raw.get("pretrade_filters") if isinstance(raw.get("pretrade_filters"), dict) else {}
    return {
        "stock": _expr_list(payload.get("stock")),
        "benchmark": _expr_list(payload.get("benchmark")),
    }


def _count_completed_test_usage(database, dataset_id, dataset_version_id, date_from, date_to) -> int:
    want = (_compact_date(date_from), _compact_date(date_to))
    if not dataset_id or not dataset_version_id or not want[0] or not want[1]:
        return 0
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT config_json FROM backtest_runs WHERE status='completed' AND dataset_id=? AND dataset_version_id=?",
            (str(dataset_id), str(dataset_version_id)),
        ).fetchall()
    count = 0
    for row in rows:
        try:
            config = json.loads(row["config_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        test = config.get("test") if isinstance(config.get("test"), dict) else {}
        if (_compact_date(test.get("date_from")), _compact_date(test.get("date_to"))) == want:
            count += 1
    return count


def _apply_pretrade_and_sample_filters(out: dict[str, Any], raw: dict[str, Any], validation) -> None:
    pretrade = _normalize_pretrade(raw)
    out["pretrade_filters"] = pretrade
    stock = pretrade["stock"]
    train = dict(out.get("train") or {})
    train_filter = _filter_dict(train)
    train_filter["expressions"] = _merge_exprs(stock, _section_extra_expressions(raw.get("train")))
    if validation is not None:
        val_from = _compact_date(out.get("validation", {}).get("date_from") or validation.get("date_from"))
        val_section = out.get("validation") if isinstance(out.get("validation"), dict) else validation
        val_exprs = _merge_exprs(stock, _section_extra_expressions(raw.get("validation") or val_section))
        train_filter["validation_date_from"] = val_from
        train_filter["validation_expressions"] = val_exprs
        if isinstance(out.get("validation"), dict):
            val_out = dict(out["validation"])
            val_filter = _filter_dict(val_out)
            val_filter["expressions"] = val_exprs
            val_out["filter"] = val_filter
            out["validation"] = val_out
    train["filter"] = train_filter
    out["train"] = train
    if isinstance(out.get("test"), dict):
        test = dict(out["test"])
        test_filter = _filter_dict(test)
        test_filter["expressions"] = _merge_exprs(stock, _section_extra_expressions(raw.get("test")))
        test["filter"] = test_filter
        out["test"] = test


def _attach_seed_and_test_usage(self, out: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    hp = dict(out.get("hyperparameters") or {})
    raw_hp = raw.get("hyperparameters") if isinstance(raw.get("hyperparameters"), dict) else {}
    if raw_hp.get("random_seed") is not None and raw_hp.get("random_seed") != "":
        hp["random_seed"] = int(raw_hp["random_seed"])
    elif hp.get("random_seed") not in (None, ""):
        hp["random_seed"] = int(hp["random_seed"])
    else:
        hp["random_seed"] = _DEFAULT_RANDOM_SEED
    out["hyperparameters"] = hp
    test = out.get("test") if isinstance(out.get("test"), dict) else {}
    if not test and isinstance(raw.get("test"), dict):
        test = raw["test"]
    out["test_usage_count"] = _count_completed_test_usage(
        self.database,
        out.get("dataset_id") or raw.get("dataset_id"),
        out.get("dataset_version_id") or raw.get("dataset_version_id"),
        test.get("date_from"),
        test.get("date_to"),
    )
    return out


def validate(self, raw):
    config = raw if isinstance(raw, dict) else {}
    if config.get("kind") == "rule_signal":
        from quantlab.services.rule_backtest import validate_rule_config

        out = validate_rule_config(config, database=self.database)
        return _attach_seed_and_test_usage(self, out, config)
    validation = _validation_section(config)
    if validation is not None:
        train_w = _section_window(config.get("train"))
        test_w = _section_window(config.get("test"))
        val_w = _section_window(validation)
        if train_w and val_w and not (train_w[1] < val_w[0]):
            raise ValueError("train and validation windows must not overlap")
        if val_w and test_w and not (val_w[1] < test_w[0]):
            raise ValueError("validation and test windows must not overlap")
    out = _original_validate(self, config)
    if validation is not None:
        train_to = out["train"]["date_to"]
        val_from = _iso_date(validation["date_from"])
        val_to = _iso_date(validation["date_to"])
        out["train"] = dict(out["train"])
        out["train"]["fit_date_to"] = train_to
        out["train"]["date_to"] = val_to
        train_filter = out["train"].get("filter") if isinstance(out["train"].get("filter"), dict) else {}
        val_filter = validation.get("filter") if isinstance(validation.get("filter"), dict) else train_filter
        out["validation"] = {
            "date_from": val_from,
            "date_to": val_to,
            "stock_scope": validation.get("stock_scope") or out["train"].get("stock_scope"),
            "filter": val_filter,
        }
        hp = dict(out.get("hyperparameters") or {})
        hp["validation_date_from"] = val_from
        hp["validation_date_to"] = val_to
        hp["train_fit_date_to"] = train_to
        out["hyperparameters"] = hp
    _apply_pretrade_and_sample_filters(out, config, validation)
    return _attach_seed_and_test_usage(self, out, config)


def submit(self, raw):
    config = raw if isinstance(raw, dict) else {}
    token = str(config.get("submission_token") or "").strip()
    if token:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT run_id FROM backtest_runs WHERE submission_token=?",
                (token,),
            ).fetchone()
        if existing:
            return _original_submit(self, config)
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    count = _count_completed_test_usage(
        self.database,
        config.get("dataset_id"),
        config.get("dataset_version_id"),
        test.get("date_from"),
        test.get("date_to"),
    )
    if count >= 1 and not config.get("acknowledge_test_reuse"):
        raise ValueError(f"这段 test 已经用于 {count} 次已完成回测，请勾选确认再次使用")
    payload = dict(config)
    payload["test_usage_count"] = count
    return _original_submit(self, payload)


BacktestWorkbenchService.validate = validate
BacktestWorkbenchService.submit = submit
