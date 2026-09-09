"""Configuration and submission boundary for reproducible backtests."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from pyarrow.lib import ArrowInvalid

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.run_identity import RunIdentity
from quantlab.services.trade_filters import normalize_trade_filters, split_open_filters

_DEFAULT_RANDOM_SEED = 123


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"yes", "skip", "true", "on", "1"}:
            return True
        if text in {"no", "0", "fill", "off", "false"}:
            return False
    return bool(value)


def _ymd(value: Any) -> str:
    return str(value or "").replace("-", "")[:8]


def _format_like(sample: Any, ymd: str) -> str:
    if "-" in str(sample or ""):
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return ymd


class BacktestWorkbenchService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.identity = RunIdentity(database)

    def _bind_published_model_and_strategy(self, db: Any, c: dict[str, Any]) -> None:
        model = c.get("model") if isinstance(c.get("model"), dict) else {}
        entity_id = str(model.get("entity_id") or "").strip()
        version_id = str(model.get("version_id") or "").strip()
        if not entity_id:
            raise ValueError("model version is required")
        entity = db.execute("SELECT status FROM models WHERE entity_id=?", (entity_id,)).fetchone()
        if entity is None or entity["status"] != "published":
            raise ValueError("请先在模型中心保存这种模型的默认参数。")
        requested = None
        if version_id:
            requested = db.execute(
                "SELECT version_id, status FROM model_versions WHERE entity_id=? AND version_id=?",
                (entity_id, version_id),
            ).fetchone()
        if requested is None or requested["status"] != "published":
            latest = db.execute(
                "SELECT version_id FROM model_versions WHERE entity_id=? AND status='published' ORDER BY rowid DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
            if latest is None:
                raise ValueError("请先在模型中心保存这种模型的默认参数。")
            version_id = latest["version_id"]
        c["model"] = {**model, "entity_id": entity_id, "version_id": version_id}
        strategy = db.execute(
            """
            SELECT s.entity_id, sv.version_id
            FROM strategies s
            JOIN strategy_versions sv ON s.entity_id=sv.entity_id
            WHERE s.status='published' AND sv.status='published'
              AND sv.model_entity_id=? AND sv.model_version_id=?
            ORDER BY sv.rowid DESC
            LIMIT 1
            """,
            (entity_id, version_id),
        ).fetchone()
        if strategy is None:
            strategy = db.execute(
                """
                SELECT s.entity_id, sv.version_id
                FROM strategies s
                JOIN strategy_versions sv ON s.entity_id=sv.entity_id
                WHERE s.status='published' AND sv.status='published'
                  AND sv.model_entity_id=?
                ORDER BY sv.rowid DESC
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()
        if strategy is None:
            raise ValueError("这个模型还没有默认交易规则。请到模型中心再保存一次默认参数。")
        c["strategy_entity_id"] = strategy["entity_id"]
        c["strategy_version_id"] = strategy["version_id"]

    def _validate_core(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("backtest config is required")
        c = json.loads(_json(raw))
        required = ("name", "dataset_id", "dataset_version_id", "factor_versions", "model", "train", "test")
        missing = [k for k in required if k not in c]
        if missing:
            raise ValueError("missing config: " + ", ".join(missing))
        scalar_missing = [k for k in ("name", "dataset_id", "dataset_version_id") if not str(c.get(k, "")).strip()]
        if scalar_missing:
            raise ValueError("missing config: " + ", ".join(scalar_missing))
        if c.get("stock_scope") != "中国A股（SH/SZ）":
            raise ValueError("stock_scope must be 中国A股（SH/SZ）")
        if not isinstance(c["factor_versions"], list) or not c["factor_versions"]:
            raise ValueError("请至少选择一个因子")
        if not isinstance(c["model"], dict) or not str(c["model"].get("entity_id") or "").strip():
            raise ValueError("model version is required")
        for part in ("train", "test"):
            section = c[part]
            if (
                not isinstance(section, dict)
                or not section.get("date_from")
                or not section.get("date_to")
                or section.get("date_from", "") > section.get("date_to", "")
            ):
                raise ValueError(f"invalid {part} window")
            if not isinstance(section.get("filter", {}), dict):
                raise ValueError(f"{part}.filter must be an object")
            filt = dict(section.get("filter") or {})
            filt["close_gt_low"] = _as_bool(filt.get("close_gt_low"), True)
            filt["skip_limit_close"] = _as_bool(filt.get("skip_limit_close"), True)
            filt["close_gt_ma200"] = _as_bool(filt.get("close_gt_ma200"), False)
            filt["close_lt_ma200"] = _as_bool(filt.get("close_lt_ma200"), False)
            if filt["close_gt_ma200"] and filt["close_lt_ma200"]:
                raise ValueError("个股200日均线不能同时选站上和低于")
            c[part]["filter"] = filt
        kind = str(c.get("kind") or (c.get("model") or {}).get("kind") or "").strip()
        if kind == "factor_rank":
            c["walk_forward"] = "once"
        else:
            from quantlab.services.backtest_job import (
                normalize_test_period_months,
                shift_calendar_months,
                train_period_months,
                walk_forward_mode,
            )

            mode = walk_forward_mode(c)
            c["walk_forward"] = mode
            if mode == "rolling":
                train_m = train_period_months(c)
                test_m = normalize_test_period_months(c)
                c["train_period_months"] = train_m
                c["test_period_months"] = test_m
                roll_start = _ymd(c["train"]["date_from"])
                roll_end = _ymd(c["test"]["date_to"])
                first_test = shift_calendar_months(roll_start, train_m)
                if len(roll_start) != 8 or len(roll_end) != 8 or first_test > roll_end:
                    raise ValueError("定长回看没有可用的切分。")
                train_to = (datetime.strptime(first_test, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
                c["train"]["date_to"] = _format_like(c["train"]["date_from"], train_to)
                c["test"]["date_from"] = _format_like(c["test"]["date_to"], first_test)
                if c["train"]["date_from"] > c["train"]["date_to"]:
                    raise ValueError("invalid train window")
                if c["test"]["date_from"] > c["test"]["date_to"]:
                    raise ValueError("invalid test window")
        if c["train"]["date_to"] >= c["test"]["date_from"]:
            raise ValueError("train and test windows must not overlap")
        try:
            c["top_n"] = int(c.get("top_n"))
        except (TypeError, ValueError):
            raise ValueError("每次持仓数量要填正整数") from None
        if c["top_n"] < 1:
            raise ValueError("每次持仓数量要填正整数")
        if c.get("weighting") not in {"equal", "score"}:
            raise ValueError("weighting must be equal or score")
        try:
            c["rebalance_every"] = int(c.get("rebalance_every"))
        except (TypeError, ValueError):
            raise ValueError("调仓间隔要填正整数") from None
        if c["rebalance_every"] < 1:
            raise ValueError("调仓间隔要填正整数")
        try:
            c["holding_days"] = int(c["holding_days"]) if c.get("holding_days") is not None else 2
        except (TypeError, ValueError):
            raise ValueError("持仓天数要填正整数") from None
        if c["holding_days"] < 1:
            raise ValueError("持仓天数要填正整数")
        buy_price = str(c.get("buy_price") or "open")
        sell_price = str(c.get("sell_price") or "close")
        if buy_price not in {"open", "hfq_open"}:
            raise ValueError("买入价格要选未复权开盘价或后复权开盘价")
        if sell_price not in {"hfq_close", "close"}:
            raise ValueError("卖出价格要选未复权收盘价或后复权收盘价")
        c["buy_price"] = buy_price
        c["sell_price"] = sell_price
        if "stamp_tax_rate" not in c:
            c["stamp_tax_rate"] = 0.001
        c["skip_open_limit"] = _as_bool(c.get("skip_open_limit"), True)
        c["skip_close_down_limit"] = _as_bool(c.get("skip_close_down_limit"), True)
        c["open_when_benchmark_gt_ma200"] = _as_bool(c.get("open_when_benchmark_gt_ma200"), False)
        for key in ("buy_fee_rate", "sell_fee_rate", "stamp_tax_rate", "slippage"):
            c[key] = float(c.get(key, 0) or 0)
            if c[key] < 0:
                raise ValueError(f"{key} must be non-negative")
        if int(c.get("lot_size", 100)) < 1:
            raise ValueError("lot_size must be positive")
        if c.get("unfilled_policy", "keep_cash") not in {"cancel", "keep_cash", "next_available"}:
            raise ValueError("invalid unfilled_policy")
        for key in ("buy_fee_minimum", "sell_fee_minimum"):
            if float(c.get(key, 0)) < 5:
                raise ValueError(f"{key} must be at least 5")
        if float(c.get("initial_capital", 0)) <= 0:
            raise ValueError("initial_capital must be positive")
        with self.database.connect() as db:
            self._bind_published_model_and_strategy(db, c)
            dataset = db.execute(
                "SELECT d.status ds, dv.status vs, dv.quality_status, dv.fields_json FROM datasets d JOIN dataset_versions dv ON d.entity_id=dv.entity_id WHERE dv.entity_id=? AND dv.version_id=?",
                (c["dataset_id"], c["dataset_version_id"]),
            ).fetchone()
            if (
                dataset is None
                or dataset["ds"] != "published"
                or dataset["vs"] != "published"
                or dataset["quality_status"] == "failed"
            ):
                raise ValueError("published quality-passed dataset version is required")
            for f in c["factor_versions"]:
                if not isinstance(f, dict):
                    raise ValueError("published quality-passed factor version is required")
                factor_id = str(f.get("factor_id") or "").strip()
                version_id = str(f.get("version_id") or "").strip()
                field = str(f.get("field") or factor_id.removeprefix("factor_") or "").strip()
                published = None
                if factor_id and version_id:
                    published = db.execute(
                        "SELECT 1 FROM factors f JOIN factor_versions fv ON f.entity_id=fv.entity_id WHERE fv.entity_id=? AND fv.version_id=? AND f.status='published' AND fv.status='published' AND fv.quality_status='passed'",
                        (factor_id, version_id),
                    ).fetchone()
                if published:
                    continue
                dataset_row = db.execute(
                    "SELECT fields_json FROM dataset_versions WHERE entity_id=? AND version_id=?",
                    (c["dataset_id"], c["dataset_version_id"]),
                ).fetchone()
                dataset_fields = json.loads(dataset_row["fields_json"] or "[]") if dataset_row else []
                if field and (field in dataset_fields or field in {"momentum_5", "volatility_5"}):
                    continue
                raise ValueError("published quality-passed factor version is required")
        allowed_fields = set()
        if dataset is not None:
            try:
                allowed_fields = {str(name) for name in json.loads(dataset["fields_json"] or "[]")}
            except (TypeError, ValueError):
                allowed_fields = set()
        allowed_fields.update({"hfq_close", "up_limit", "sma_200", "open", "down_limit", "hfq_open", "close", "sma200", "ma200"})
        c["trade_filters"] = normalize_trade_filters(
            c.get("trade_filters"),
            skip_open_limit=c["skip_open_limit"],
            allowed_fields=allowed_fields,
        )
        _, fill_exprs = split_open_filters(c["trade_filters"]["open"])
        c["skip_open_limit"] = bool(fill_exprs)
        c["buy_fee_minimum"], c["sell_fee_minimum"] = float(c["buy_fee_minimum"]), float(c["sell_fee_minimum"])
        c["content_hash"] = hashlib.sha256(
            _json({k: v for k, v in c.items() if k not in {"content_hash", "submission_token"}}).encode()
        ).hexdigest()
        return c

    def _submit_core(self, raw: dict[str, Any]) -> dict[str, Any]:
        c = self.validate(raw)
        token = str(c.get("submission_token", "")).strip()
        if not token:
            raise ValueError("submission_token is required")
        run_id = self.identity.next_id()
        with self.database.transaction() as db:
            existing = db.execute(
                "SELECT run_id FROM backtest_runs WHERE submission_token=?",
                (token,),
            ).fetchone()
            if existing:
                existing_id = str(existing["run_id"])
            else:
                existing_id = ""
            if not existing_id:
                db.execute(
                    "INSERT INTO run_registry(run_id,run_type,created_at) VALUES (?, 'backtest', ?)",
                    (run_id, _now()),
                )
                db.execute(
                    "INSERT INTO backtest_runs(run_id,status,strategy_entity_id,strategy_version_id,dataset_id,dataset_version_id,config_json,submission_token) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        c["strategy_entity_id"],
                        c["strategy_version_id"],
                        c["dataset_id"],
                        c["dataset_version_id"],
                        _json(c),
                        token,
                    ),
                )
        return self.get(existing_id or run_id) or {}

    def save_draft(
        self,
        raw: dict[str, Any],
        *,
        draft_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist a full workbench configuration without creating a run."""
        c = self.validate(raw)
        now = _now()
        factor_json = _json(c["factor_versions"])
        strategy_entity_id = str(c["strategy_entity_id"])
        strategy_version_id = str(c["strategy_version_id"])
        revision = 1
        with self.database.transaction() as db:
            if draft_id:
                existing = db.execute(
                    "SELECT revision FROM backtest_drafts WHERE draft_id=?",
                    (draft_id,),
                ).fetchone()
                if existing is None:
                    raise ValueError("backtest draft not found")
                if expected_revision is not None and int(existing["revision"]) != expected_revision:
                    raise ValueError("backtest draft revision conflict")
                revision = int(existing["revision"]) + 1
                db.execute(
                    "UPDATE backtest_drafts SET revision=?, factor_version_ids_json=?, strategy_entity_id=?, strategy_version_id=?, config_json=?, updated_at=? WHERE draft_id=?",
                    (
                        revision,
                        factor_json,
                        strategy_entity_id,
                        strategy_version_id,
                        _json(c),
                        now,
                        draft_id,
                    ),
                )
            else:
                draft_id = f"draft-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
                db.execute(
                    "INSERT INTO backtest_drafts(draft_id, revision, factor_version_ids_json, strategy_entity_id, strategy_version_id, config_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                    (
                        draft_id,
                        factor_json,
                        strategy_entity_id,
                        strategy_version_id,
                        _json(c),
                        now,
                        now,
                    ),
                )
        return {"draft_id": draft_id, "revision": revision, "config": c}

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self.database.connect() as db:
            row = db.execute(
                "SELECT draft_id, revision, factor_version_ids_json, strategy_entity_id, strategy_version_id, config_json, created_at, updated_at FROM backtest_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
        if row is None:
            return None
        raw_config = json.loads(row["config_json"] or "{}")
        return {
            "draft_id": row["draft_id"],
            "revision": row["revision"],
            "factor_version_ids": json.loads(row["factor_version_ids_json"]),
            "strategy_entity_id": row["strategy_entity_id"],
            "strategy_version_id": row["strategy_version_id"],
            "config": raw_config,
            "complete": bool(raw_config),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["config"] = json.loads(out.pop("config_json") or "{}")
        out["metrics"] = json.loads(out.pop("metrics_json") or "{}")
        return out


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
    out = self._validate_core(config)
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
            return self._submit_core(config)
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    count = _count_completed_test_usage(
        self.database,
        config.get("dataset_id"),
        config.get("dataset_version_id"),
        test.get("date_from"),
        test.get("date_to"),
    )
    payload = dict(config)
    payload["test_usage_count"] = count
    return self._submit_core(payload)


_PREVIEW_COLUMNS = {
    "date": ("date", "trade_date"),
    "instrument": ("instrument", "ts_code"),
    "st_status": ("st_status",),
    "suspended": ("suspended", "is_suspended"),
}


def _source_column(available: set[str], aliases: tuple[str, ...]) -> str | None:
    return next((name for name in aliases if name in available), None)


def _expr_field_names(node: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(node, tuple) or not node:
        return names
    if node[0] == "name":
        names.add(str(node[1]))
        return names
    for item in node[1:]:
        names.update(_expr_field_names(item))
    return names


def preview(self, raw):
    from quantlab.services.backtest_job import _FIELD_ALIASES, apply_expression_filters
    from quantlab.services.trade_filters import needs_sma200, parse_expr

    config = self.validate(raw if isinstance(raw, dict) else {})
    with self.database.connect() as connection:
        row = connection.execute(
            "SELECT path FROM dataset_versions WHERE entity_id=? AND version_id=?",
            (config.get("dataset_id"), config.get("dataset_version_id")),
        ).fetchone()
    if row is None:
        raise ValueError("dataset is missing preview filter fields")
    path = self.settings.require_read_path(Path(row["path"]))
    available = set(pq.ParquetFile(path).schema_arrow.names)
    mapping: dict[str, str] = {}
    for logical, aliases in _PREVIEW_COLUMNS.items():
        source = _source_column(available, aliases)
        if source is None:
            raise ValueError("dataset is missing preview filter fields")
        mapping[logical] = source

    expressions: list[str] = []
    for name in ("train", "test"):
        expressions.extend(_expr_list(_filter_dict(config.get(name)).get("expressions")))
    extra_names: set[str] = set()
    for text in expressions:
        try:
            extra_names.update(_expr_field_names(parse_expr(text)))
        except Exception:
            continue
    if needs_sma200(expressions):
        extra_names.update({"hfq_close", "close", "sma_200", "sma200"})

    read_cols = list(dict.fromkeys(mapping.values()))
    for name in extra_names:
        column = _FIELD_ALIASES.get(name, name)
        if column in available and column not in read_cols:
            read_cols.append(column)
        elif name in available and name not in read_cols:
            read_cols.append(name)

    try:
        date_from = min(
            _compact_date((config.get("train") or {}).get("date_from")),
            _compact_date((config.get("test") or {}).get("date_from")),
        )
        date_to = max(
            _compact_date((config.get("train") or {}).get("date_to")),
            _compact_date((config.get("test") or {}).get("date_to")),
        )
        filters = None
        if len(date_from) == 8 and len(date_to) == 8:
            filters = [(mapping["date"], ">=", date_from), (mapping["date"], "<=", date_to)]
        try:
            frame = pd.read_parquet(path, columns=read_cols, filters=filters)
        except (TypeError, ArrowInvalid):
            frame = pd.read_parquet(path, columns=read_cols)
    except (KeyError, ValueError, OSError) as error:
        raise ValueError("dataset is missing preview filter fields") from error

    frame = frame.copy()
    for logical, source in mapping.items():
        frame[logical] = frame[source]
    frame["date"] = frame["date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    frame["instrument"] = frame["instrument"].astype(str)

    scope = frame["instrument"].str.endswith((".SH", ".SZ"))
    stages = [{"stage": "中国A股（SH/SZ）", "remaining_count": int(scope.sum())}]
    current = frame.loc[scope]
    for name in ("train", "test"):
        section = config.get(name) if isinstance(config.get(name), dict) else {}
        filt = _filter_dict(section)
        date_from = _compact_date(section.get("date_from"))
        date_to = _compact_date(section.get("date_to"))
        selected = current.loc[(current["date"] >= date_from) & (current["date"] <= date_to)]
        if filt.get("st_status") == 0 and "st_status" in selected.columns:
            selected = selected.loc[selected["st_status"] == 0]
        if filt.get("suspended") is False and "suspended" in selected.columns:
            selected = selected.loc[selected["suspended"] == False]  # noqa: E712
        exprs = _expr_list(filt.get("expressions"))
        if exprs:
            selected = apply_expression_filters(selected, exprs)
        stages.append({"stage": name, "remaining_count": int(len(selected))})
    return {
        "config": config,
        "stages": stages,
        "estimated_train_rows": stages[1]["remaining_count"],
        "estimated_test_rows": stages[2]["remaining_count"],
    }


BacktestWorkbenchService.validate = validate
BacktestWorkbenchService.submit = submit
BacktestWorkbenchService.preview = preview
