"""Project backtest config/metrics JSON into queryable SQLite columns."""

from __future__ import annotations

import json
import re
from typing import Any

from quantlab.repositories.database import Database
from quantlab.services.backtest_workbench import normalize_open_ma_gates
from quantlab.services.model_training import kind_display_name, model_center_name


_INDEX_CODE = re.compile(r"^\d{6}\.(SH|SZ)$")
_DEFAULT_STRATEGY_SUFFIX = " · 默认策略"
_METRIC_COLUMNS = (
    ("return", "total_return"),
    ("annual_return", "annual_return"),
    ("sharpe", "sharpe"),
    ("sortino", "sortino"),
    ("calmar", "calmar"),
    ("max_drawdown", "max_drawdown"),
    ("max_loss_streak", "max_loss_streak"),
    ("win_rate", "win_rate"),
    ("benchmark_return", "benchmark_return"),
    ("excess_return", "excess_return"),
    ("turnover", "turnover"),
    ("capital_usage", "capital_usage"),
    ("rank_ic", "rank_ic"),
    ("ndcg_at_10", "ndcg_at_10"),
)
_SUMMARY_COLUMNS = (
    "run_id",
    "name",
    "note",
    "status",
    "kind",
    "machine_id",
    "strategy_entity_id",
    "strategy_name",
    "factor_names",
    "date_from",
    "date_to",
    "top_n",
    "weighting",
    "holding_days",
    "rebalance_every",
    "rebalance_mode",
    "slippage",
    "gate_key",
    "factor_key",
    "benchmark",
    *(column for _, column in _METRIC_COLUMNS),
    "created_at",
    "finished_at",
)
_SELECT_RUN = """
SELECT
  br.run_id,
  br.status,
  br.config_json,
  br.metrics_json,
  br.strategy_entity_id,
  br.strategy_version_id,
  registry.created_at,
  registry.finished_at,
  s.name AS strategy_name,
  m.name AS model_name,
  factor_agg.factor_names AS factor_names
FROM backtest_runs br
JOIN run_registry registry ON registry.run_id = br.run_id
LEFT JOIN strategies s ON s.entity_id = br.strategy_entity_id
LEFT JOIN models m ON m.entity_id = json_extract(br.config_json, '$.model.entity_id')
LEFT JOIN (
  SELECT sfv.strategy_entity_id, sfv.strategy_version_id,
         group_concat(f.name, ',') AS factor_names
  FROM strategy_factor_versions sfv
  JOIN factors f ON f.entity_id = sfv.factor_entity_id
  GROUP BY sfv.strategy_entity_id, sfv.strategy_version_id
) factor_agg
  ON factor_agg.strategy_entity_id = br.strategy_entity_id
 AND factor_agg.strategy_version_id = br.strategy_version_id
"""
_UPSERT_SQL = (
    "INSERT INTO backtest_summaries ("
    + ", ".join(_SUMMARY_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _SUMMARY_COLUMNS)
    + ") ON CONFLICT(run_id) DO UPDATE SET "
    + ", ".join(f"{column}=excluded.{column}" for column in _SUMMARY_COLUMNS if column != "run_id")
)


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _iso_date(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value or "").strip()


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strategy_label(name: Any) -> str:
    text = str(name or "").strip()
    if text.endswith(_DEFAULT_STRATEGY_SUFFIX):
        trimmed = text[: -len(_DEFAULT_STRATEGY_SUFFIX)].strip()
        return trimmed or text
    return text


def gate_key(config: dict[str, Any] | None) -> str:
    payload = config if isinstance(config, dict) else {}
    try:
        gates = normalize_open_ma_gates(payload.get("open_ma_gates"))
    except (TypeError, ValueError):
        gates = []
    parts = sorted((str(item["code"]), int(item["window"])) for item in gates)
    if parts:
        return "+".join(f"{code}:{window}" for code, window in parts)
    if not _truthy(payload.get("open_when_benchmark_gt_ma200")):
        return ""
    bench = str(payload.get("benchmark") or "000300.SH").strip().upper().replace("-", ".")
    if _INDEX_CODE.match(bench):
        return f"{bench}:200"
    return "000300.SH:200"


def factor_key(config: dict[str, Any] | None) -> str:
    payload = config if isinstance(config, dict) else {}
    fields: list[str] = []
    seen: set[str] = set()
    refs = payload.get("factor_versions")
    if not isinstance(refs, list):
        return ""
    for item in refs:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("factor") or "").strip()
        if not field:
            field = str(item.get("factor_id") or "").strip().removeprefix("factor_")
        if field and field not in seen:
            seen.add(field)
            fields.append(field)
    return ",".join(sorted(fields))


def _factor_labels(row: Any, config: dict[str, Any]) -> str:
    joined = str(row["factor_names"] or "").strip()
    if joined and joined != "未登记因子":
        return joined
    labels: list[str] = []
    seen: set[str] = set()
    for item in config.get("factor_versions") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        factor_id = str(item.get("factor_id") or "").strip()
        label = field or factor_id.removeprefix("factor_")
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return ",".join(labels)


def _display_name(row: Any, config: dict[str, Any], connection: Any) -> str:
    kind = str(config.get("kind") or (config.get("model") or {}).get("kind") or "").strip()
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    entity_id = str(model.get("entity_id") or "").strip()
    named = model_center_name(connection, entity_id=entity_id, kind=kind)
    if named:
        return named
    from_config = str(model.get("name") or "").strip()
    if from_config:
        return from_config
    labeled = _strategy_label(row["strategy_name"] or row["strategy_entity_id"] or "")
    return labeled or kind_display_name(kind) or "未登记模型"


def project_summary_values(row: Any, connection: Any) -> tuple[Any, ...]:
    config = _json_object(row["config_json"])
    metrics = _json_object(row["metrics_json"])
    test = config.get("test") if isinstance(config.get("test"), dict) else {}
    resources = metrics.get("resources") if isinstance(metrics.get("resources"), dict) else {}
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    kind = str(config.get("kind") or model.get("kind") or "").strip()
    values: dict[str, Any] = {
        "run_id": row["run_id"],
        "name": str(config.get("name") or "未命名回测"),
        "note": str(config.get("note") or "").strip(),
        "status": str(row["status"] or ""),
        "kind": kind,
        "machine_id": str(resources.get("machine_id") or config.get("machine_id") or ""),
        "strategy_entity_id": str(row["strategy_entity_id"] or ""),
        "strategy_name": _display_name(row, config, connection),
        "factor_names": _factor_labels(row, config),
        "date_from": _iso_date(test.get("date_from")),
        "date_to": _iso_date(test.get("date_to")),
        "top_n": _int(config.get("top_n")),
        "weighting": str(config.get("weighting") or "").strip(),
        "holding_days": _int(config.get("holding_days")),
        "rebalance_every": _int(config.get("rebalance_every")),
        "rebalance_mode": str(config.get("rebalance_mode") or "").strip(),
        "slippage": _float(config.get("slippage")),
        "gate_key": gate_key(config),
        "factor_key": factor_key(config),
        "benchmark": str(config.get("benchmark") or ""),
        "created_at": str(row["created_at"] or ""),
        "finished_at": row["finished_at"],
    }
    for source, column in _METRIC_COLUMNS:
        values[column] = _float(metrics.get(source))
        if column == "max_loss_streak" and values[column] is not None:
            values[column] = int(values[column])
    return tuple(values[column] for column in _SUMMARY_COLUMNS)


def refresh_backtest_summary(connection: Any, run_id: str) -> None:
    row = connection.execute(_SELECT_RUN + " WHERE br.run_id=?", (run_id,)).fetchone()
    if row is None:
        connection.execute("DELETE FROM backtest_summaries WHERE run_id=?", (run_id,))
        return
    connection.execute(_UPSERT_SQL, project_summary_values(row, connection))


def backfill_backtest_summaries(database: Database) -> int:
    with database.transaction() as connection:
        rows = connection.execute(
            _SELECT_RUN
            + " WHERE NOT EXISTS (SELECT 1 FROM backtest_summaries s WHERE s.run_id = br.run_id)"
        ).fetchall()
        if not rows:
            return 0
        connection.executemany(
            _UPSERT_SQL,
            [project_summary_values(row, connection) for row in rows],
        )
        return len(rows)


def ensure_backtest_summaries(database: Database) -> int:
    with database.connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backtest_summaries'"
        ).fetchone()
        if exists is None:
            return 0
        missing = connection.execute(
            "SELECT EXISTS("
            "SELECT 1 FROM backtest_runs br "
            "LEFT JOIN backtest_summaries s ON s.run_id = br.run_id "
            "WHERE s.run_id IS NULL"
            ")"
        ).fetchone()[0]
    if not missing:
        return 0
    return backfill_backtest_summaries(database)
