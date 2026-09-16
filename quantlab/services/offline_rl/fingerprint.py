"""Stable fingerprints for backtest configs used in offline RL expert matching."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _expr_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(str(item).strip() for item in value if str(item).strip())


def _filter_payload(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {"expressions": []}
    filt = section.get("filter")
    if not isinstance(filt, dict):
        return {"expressions": []}
    return {"expressions": _expr_list(filt.get("expressions"))}


def _factor_fields(factor_versions: Any) -> list[str]:
    if not isinstance(factor_versions, list):
        return []
    keys: list[str] = []
    for item in factor_versions:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        factor_id = str(item.get("factor_id") or "").strip()
        key = field or factor_id
        if key:
            keys.append(key)
    return sorted(keys)


def _fingerprint_payload(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    pretrade = config.get("pretrade_filters") if isinstance(config.get("pretrade_filters"), dict) else {}
    trade = config.get("trade_filters") if isinstance(config.get("trade_filters"), dict) else {}

    payload: dict[str, Any] = {
        "factor_fields": _factor_fields(config.get("factor_versions")),
        "top_n": config.get("top_n"),
        "weighting": config.get("weighting"),
        "holding_days": config.get("holding_days"),
        "rebalance_every": config.get("rebalance_every"),
        "open_when_benchmark_gt_ma200": config.get("open_when_benchmark_gt_ma200"),
        "open_gate_by_membership": config.get("open_gate_by_membership"),
        "test_filter": _filter_payload(config.get("test")),
        "train_filter": _filter_payload(config.get("train")),
        "model_kind": model.get("kind"),
    }
    gates = []
    for item in config.get("open_ma_gates") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        gates.append({"code": code, "window": item.get("window")})
    if gates:
        payload["open_ma_gates"] = sorted(gates, key=lambda item: (item["code"], item.get("window") or 0))

    for key in ("buy_price", "sell_price"):
        if key in config:
            payload[key] = config[key]

    stock = _expr_list(pretrade.get("stock"))
    benchmark = _expr_list(pretrade.get("benchmark"))
    if stock or benchmark:
        payload["pretrade_filters"] = {"benchmark": benchmark, "stock": stock}

    open_exprs = _expr_list(trade.get("open"))
    if open_exprs:
        payload["trade_filters_open"] = open_exprs

    return payload


def config_fingerprint(config: dict[str, Any]) -> str:
    """Return a stable 16-hex fingerprint for comparable backtest configs."""
    payload = _fingerprint_payload(config or {})
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_offline_rl_result(config: dict[str, Any]) -> bool:
    """True when the archived result belongs to offline RL, not an expert backtest."""
    return str((config or {}).get("kind") or "") == "offline_rl"
