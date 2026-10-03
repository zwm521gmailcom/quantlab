"""Indexed result archive projections for completed and in-flight backtests."""

from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.backtest_job import fill_benchmark_metrics
from quantlab.services.backtest_summary import ensure_backtest_summaries
from quantlab.services.backtest_workbench import workbench_form_config
from quantlab.services.model_training import kind_display_name
from quantlab.services.result_sync import purge_backtest_run, write_deleted_marker


_STATUS_NAMES = {"queued": "排队中", "running": "运行中", "completed": "已完成", "failed": "失败"}
_DEFAULT_STRATEGY_SUFFIX = " · 默认策略"
_ERROR_ZH = {
    "dataset snapshot hash mismatch": "行情文件在登记后又改过，这次回测没真正跑起来。请点「重新回测」。",
    "dataset snapshot row count mismatch": "行情行数和登记时不一致，这次回测没真正跑起来。请点「重新回测」。",
    "行情文件已经更新，和登记时对不上。": "行情文件已经更新，和登记时对不上。请点「重新回测」。",
    "行情行数和登记时不一致。": "行情行数和登记时不一致。请点「重新回测」。",
}
_SORT_FIELDS = {
    "created_at": "created_at",
    "name": "name",
    "status": "status",
    "strategy": "strategy_name",
    "date_from": "date_from",
    "return": "total_return",
    "annual_return": "annual_return",
    "sharpe": "sharpe",
    "max_drawdown": "max_drawdown",
    "win_rate": "win_rate",
    "benchmark": "benchmark",
}
_METRIC_COLUMNS = {
    "return": "total_return",
    "annual_return": "annual_return",
    "sharpe": "sharpe",
    "sortino": "sortino",
    "calmar": "calmar",
    "max_drawdown": "max_drawdown",
    "max_loss_streak": "max_loss_streak",
    "win_rate": "win_rate",
    "benchmark_return": "benchmark_return",
    "excess_return": "excess_return",
    "turnover": "turnover",
    "capital_usage": "capital_usage",
    "rank_ic": "rank_ic",
    "ndcg_at_10": "ndcg_at_10",
}
_METRIC_SORT_FIELDS = {"return", "annual_return", "sharpe", "max_drawdown", "win_rate"}
_METRICS = (
    ("return", "累计收益"),
    ("annual_return", "年化收益"),
    ("sharpe", "夏普"),
    ("sortino", "索提诺"),
    ("calmar", "卡玛"),
    ("max_drawdown", "最大回撤"),
    ("max_loss_streak", "最长连亏"),
    ("win_rate", "胜率"),
    ("benchmark_return", "基准收益"),
    ("excess_return", "超额收益"),
    ("turnover", "日均换手"),
    ("capital_usage", "日均资金占用"),
    ("rank_ic", "Rank IC"),
    ("ndcg_at_10", "NDCG@10"),
)
_PERCENT_METRICS = {
    "return", "annual_return", "max_drawdown", "win_rate",
    "benchmark_return", "excess_return", "turnover", "capital_usage",
}
_INTEGER_METRICS = {"max_loss_streak"}
_STEP_LABELS = {
    "snapshot_validation": "快照校验",
    "model_training": "模型训练",
    "prediction": "预测打分",
    "positions": "生成仓位",
    "execution": "撮合成交",
    "metrics": "指标汇总",
}
_FACTOR_RANK_STEP_LABELS = {
    **_STEP_LABELS,
    "model_training": "准备数据",
    "prediction": "因子排序",
}
_STEP_STATUS_NAMES = {
    "pending": "等待",
    "running": "进行中",
    "completed": "完成",
    "failed": "失败",
    "skipped": "跳过",
}


def step_label(step_name: str, *, kind: str | None = None) -> str:
    names = _FACTOR_RANK_STEP_LABELS if kind == "factor_rank" else _STEP_LABELS
    key = str(step_name or "")
    return names.get(key, key)


class _Desc:
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _Desc):
            return NotImplemented
        return self.value > other.value


def _metric_sort_value(item: dict[str, Any], field: str) -> float | None:
    payload = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    metric = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text_sort_value(item: dict[str, Any], sort: str) -> str:
    if sort == "name":
        return str(item.get("name") or "").strip()
    if sort == "status":
        return str(item.get("status") or "").strip()
    if sort == "strategy":
        strategy = item.get("strategy") if isinstance(item.get("strategy"), dict) else {}
        name = str(strategy.get("name") or "").strip()
        factors = " ".join(str(part).strip() for part in (item.get("factors") or []) if str(part).strip())
        return f"{name} {factors}".strip()
    if sort == "date_from":
        window = item.get("test_window") if isinstance(item.get("test_window"), dict) else {}
        return f"{window.get('date_from') or ''} {window.get('date_to') or ''}".strip()
    if sort == "benchmark":
        text = str(item.get("benchmark") or "").strip()
        return "" if text == "未生成" else text
    return ""


def _archive_sort_key(item: dict[str, Any], sort: str, order: str) -> tuple[Any, ...]:
    tie = (str(item.get("created_at") or ""), str(item.get("run_id") or ""))
    if sort == "created_at":
        key = (str(item.get("created_at") or ""), str(item.get("run_id") or ""))
        return (_Desc(key) if order == "desc" else key,)
    if sort in _METRIC_SORT_FIELDS:
        value = _metric_sort_value(item, sort)
        if value is None:
            return (1, 0.0, *tie)
        number = -value if order == "desc" else value
        return (0, number, *tie)
    text = _text_sort_value(item, sort)
    if not text:
        return (1, "", *tie)
    return (0, _Desc(text) if order == "desc" else text, *tie)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    start = _parse_ts(started_at)
    end = _parse_ts(finished_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _format_duration_ms(ms: int | None, *, status: str = "completed") -> str:
    if status == "skipped":
        return "跳过"
    if status == "pending":
        return "—"
    if status == "running":
        return "进行中"
    if ms is None:
        return "—"
    if ms < 10:
        return "<0.01 秒"
    if ms < 60_000:
        seconds = ms / 1000
        if seconds < 10:
            return f"{seconds:.2f} 秒"
        return f"{seconds:.1f} 秒"
    minutes, rest = divmod(ms, 60_000)
    seconds = rest / 1000
    if minutes < 60:
        if rest < 10:
            return f"{minutes} 分"
        return f"{minutes} 分 {seconds:.1f} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def _annotate_step_timing(step: dict[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    status = str(step.get("status") or "pending")
    duration_ms = None
    if status not in {"pending", "running", "skipped"}:
        duration_ms = _duration_ms(step.get("started_at"), step.get("finished_at"))
    step["step_label"] = step_label(str(step.get("step_name") or ""), kind=kind)
    step["status_name"] = _STEP_STATUS_NAMES.get(status, status)
    step["duration_ms"] = duration_ms
    step["duration_display"] = _format_duration_ms(duration_ms, status=status)
    return step


def _run_timing(created_at: Any, finished_at: Any, status: str) -> dict[str, Any]:
    if status in {"queued", "running"}:
        return {
            "duration_ms": None,
            "duration_display": "进行中" if status == "running" else "—",
            "created_at": created_at,
            "finished_at": finished_at,
        }
    duration_ms = _duration_ms(created_at, finished_at)
    return {
        "duration_ms": duration_ms,
        "duration_display": _format_duration_ms(duration_ms, status="completed"),
        "created_at": created_at,
        "finished_at": finished_at,
    }


def _copy_redirect_path(config: dict[str, Any]) -> str:
    kind = str(config.get("kind") or "").strip()
    if kind == "rule_signal" or str(config.get("rule_strategy_id") or "").strip():
        return "/backtests/rules"
    return "/backtests/new"


def _strategy_label(name: Any) -> str:
    text = str(name or "").strip()
    if text.endswith(_DEFAULT_STRATEGY_SUFFIX):
        trimmed = text[: -len(_DEFAULT_STRATEGY_SUFFIX)].strip()
        return trimmed or text
    return text


def _detail_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    shown = {
        key: _metric(
            metrics.get(key),
            percentage=key in _PERCENT_METRICS,
            integer=key in _INTEGER_METRICS,
        )
        for key, _ in _METRICS
    }
    extras = (
        ("information_ratio", False),
        ("valid_annual_return", True),
        ("valid_information_ratio", False),
    )
    ordered: dict[str, Any] = {}
    for key, value in shown.items():
        ordered[key] = value
        if key == "annual_return":
            for extra, percentage in extras:
                ordered[extra] = _metric(metrics.get(extra), percentage=percentage)
    return ordered


def _metric(value: Any, *, percentage: bool = False, integer: bool = False) -> dict[str, Any]:
    if value is None or value == "":
        return {"value": None, "display": "未生成"}
    try:
        number = float(value)
    except (TypeError, ValueError):
        return {"value": value, "display": str(value)}
    if integer:
        return {"value": int(number), "display": str(int(number))}
    return {"value": number, "display": f"{number:.2%}" if percentage else f"{number:.4f}"}


def _parse_json_value(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list, int, float, bool)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, json.JSONDecodeError, ValueError):
        return None


def _list_metric_select() -> str:
    return ",\n              ".join(
        f"json_extract(br.metrics_json, '$.{key}') AS metric_{key}" for key, _ in _METRICS
    )


class ResultArchiveService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.artifacts = ArtifactRepository(settings, database)

    @staticmethod
    def _config(row: Any) -> dict[str, Any]:
        try:
            return json.loads(row["config_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _metrics(row: Any) -> dict[str, Any]:
        try:
            return json.loads(row["metrics_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _factor_labels(row: Any, config: dict[str, Any]) -> list[str]:
        joined = str(row["factor_names"] or "").strip()
        if joined and joined != "未登记因子":
            return [part.strip() for part in joined.split(",") if part.strip()]
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
        return labels

    def _artifact_rows(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT artifact_id, display_name, original_name, artifact_role, path, size_bytes, content_hash, created_at "
                "FROM artifacts WHERE run_id=? ORDER BY created_at, artifact_id",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("path", None)
            item["download_url"] = f"/api/artifacts/{item['artifact_id']}/download"
            result.append(item)
        return result

    def _project(self, row: Any, *, include_detail: bool = False) -> dict[str, Any]:
        config = self._config(row)
        metrics = fill_benchmark_metrics(self._metrics(row), config, raw_root=self.settings.raw_root)
        resources = metrics.get("resources") if isinstance(metrics.get("resources"), dict) else {}
        machine_id = str(resources.get("machine_id") or (config.get("machine_id") if isinstance(config, dict) else "") or "")
        test = config.get("test") if isinstance(config.get("test"), dict) else {}
        model = config.get("model") if isinstance(config.get("model"), dict) else {}
        kind = str(config.get("kind") or model.get("kind") or "").strip()
        model_name = (
            str(row["model_name"] or "").strip()
            or str(model.get("name") or "").strip()
            or kind_display_name(kind)
            or _strategy_label(row["strategy_name"] or config.get("strategy_entity_id") or "")
            or "未登记模型"
        )
        if model or model_name:
            config = {**config, "model": {**model, "name": model_name}}
        item: dict[str, Any] = {
            "run_id": row["run_id"],
            "name": str(config.get("name") or "未命名回测"),
            "status": row["status"],
            "status_name": _STATUS_NAMES.get(row["status"], row["status"]),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "strategy": {"entity_id": row["strategy_entity_id"], "name": model_name},
            "factors": self._factor_labels(row, config),
            "test_window": {"date_from": test.get("date_from"), "date_to": test.get("date_to")},
            "metrics": _detail_metrics(metrics),
            "benchmark": config.get("benchmark", "未生成"),
            "detail_url": f"/backtests/runs/{row['run_id']}",
            "copy_url": f"/api/backtests/runs/{row['run_id']}/copy-config",
            "execute_url": f"/api/backtests/{row['run_id']}/execute",
            "delete_url": f"/api/backtests/runs/{row['run_id']}",
            "retryable": row["status"] == "failed",
            "machine_id": machine_id,
        }
        if include_detail:
            item["config"] = config
            item["configuration"] = config
            item["metrics_raw"] = metrics
            item["resources"] = resources if isinstance(resources, dict) else {}
            item["error_message"] = _ERROR_ZH.get(row["error_message"] or "", row["error_message"])
            item["artifacts"] = self._artifact_rows(row["run_id"])
            item["results_root"] = self.settings.display_path(self.settings.runtime_root / "results" / row["run_id"])
            with self.database.connect() as connection:
                step_rows = connection.execute(
                    "SELECT ordinal, step_name, status, started_at, finished_at, error_message, artifact_ids_json "
                    "FROM backtest_steps WHERE run_id=? ORDER BY ordinal",
                    (row["run_id"],),
                ).fetchall()
            item["dag"] = [dict(step) for step in step_rows]
            for step in item["dag"]:
                if step.get("error_message"):
                    step["error_message"] = _ERROR_ZH.get(step["error_message"], step["error_message"])
                _annotate_step_timing(step, kind=kind)
            item["timing"] = _run_timing(row["created_at"], row["finished_at"], row["status"])
            failed_step = next((step for step in item["dag"] if step["status"] == "failed"), None)
            item["failure"] = {
                "reason": item["error_message"] or (failed_step["error_message"] if failed_step else None),
                "failed_step": failed_step["step_name"] if failed_step else None,
                "partial_result": bool(metrics) and row["status"] == "failed",
            }
            item["logs"] = [
                {"step_name": step["step_name"], "status": step["status"], "message": step["error_message"]}
                for step in item["dag"]
                if step["error_message"]
            ]
            item["actions"] = {
                "copy_config_url": f"/api/backtests/runs/{row['run_id']}/copy-config",
                "execute_url": f"/api/backtests/{row['run_id']}/execute",
                "delete_url": f"/api/backtests/runs/{row['run_id']}",
                "retryable": row["status"] == "failed",
            }
        return item

    def _run_filters(self, *, status: str | None = None, strategy: str | None = None, run_id: str | None = None) -> tuple[str, list[Any]]:
        where = ["registry.run_type='backtest'"]
        params: list[Any] = []
        if run_id:
            where.append("br.run_id=?")
            params.append(run_id)
        if status:
            if status not in _STATUS_NAMES:
                raise ValueError("invalid backtest status")
            where.append("br.status=?")
            params.append(status)
        if strategy:
            where.append("(br.strategy_entity_id=? OR s.name LIKE ? OR m.name LIKE ?)")
            params.extend([strategy, f"%{strategy}%", f"%{strategy}%"])
        return " AND ".join(where), params

    def _rows(self, *, status: str | None = None, strategy: str | None = None, run_id: str | None = None) -> list[Any]:
        predicate, params = self._run_filters(status=status, strategy=strategy, run_id=run_id)
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT br.*, registry.created_at, registry.finished_at, s.name AS strategy_name, "
                "m.name AS model_name, "
                "(SELECT group_concat(f.name, ',') FROM strategy_factor_versions sfv "
                "JOIN factors f ON f.entity_id=sfv.factor_entity_id "
                "WHERE sfv.strategy_entity_id=br.strategy_entity_id AND sfv.strategy_version_id=br.strategy_version_id) AS factor_names "
                "FROM backtest_runs br JOIN run_registry registry ON registry.run_id=br.run_id "
                "LEFT JOIN strategies s ON s.entity_id=br.strategy_entity_id "
                "LEFT JOIN models m ON m.entity_id=json_extract(br.config_json, '$.model.entity_id') "
                f"WHERE {predicate} ORDER BY registry.created_at DESC, br.run_id DESC",
                params,
            ).fetchall()

    def _list_rows(self, *, status: str | None = None, strategy: str | None = None) -> list[Any]:
        predicate, params = self._run_filters(status=status, strategy=strategy)
        with self.database.connect() as connection:
            return connection.execute(
                f"""
                SELECT
                  br.run_id,
                  br.status,
                  br.strategy_entity_id,
                  registry.created_at,
                  registry.finished_at,
                  s.name AS strategy_name,
                  m.name AS model_name,
                  factor_agg.factor_names AS factor_names,
                  json_extract(br.config_json, '$.name') AS config_name,
                  json_extract(br.config_json, '$.note') AS config_note,
                  json_extract(br.config_json, '$.kind') AS config_kind,
                  json_extract(br.config_json, '$.benchmark') AS benchmark,
                  json_extract(br.config_json, '$.machine_id') AS config_machine_id,
                  json_extract(br.config_json, '$.model.name') AS config_model_name,
                  json_extract(br.config_json, '$.model.kind') AS config_model_kind,
                  json_extract(br.config_json, '$.test.date_from') AS date_from,
                  json_extract(br.config_json, '$.test.date_to') AS date_to,
                  json_extract(br.config_json, '$.factor_versions') AS factor_versions_json,
                  json_extract(br.metrics_json, '$.resources.machine_id') AS metrics_machine_id,
                  {_list_metric_select()}
                FROM backtest_runs br
                JOIN run_registry registry ON registry.run_id=br.run_id
                LEFT JOIN strategies s ON s.entity_id=br.strategy_entity_id
                LEFT JOIN models m ON m.entity_id=json_extract(br.config_json, '$.model.entity_id')
                LEFT JOIN (
                  SELECT sfv.strategy_entity_id, sfv.strategy_version_id,
                         group_concat(f.name, ',') AS factor_names
                  FROM strategy_factor_versions sfv
                  JOIN factors f ON f.entity_id=sfv.factor_entity_id
                  GROUP BY sfv.strategy_entity_id, sfv.strategy_version_id
                ) factor_agg
                  ON factor_agg.strategy_entity_id=br.strategy_entity_id
                 AND factor_agg.strategy_version_id=br.strategy_version_id
                WHERE {predicate}
                """,
                params,
            ).fetchall()

    def _project_list(self, row: Any) -> dict[str, Any]:
        factor_versions = _parse_json_value(row["factor_versions_json"])
        config = {"factor_versions": factor_versions if isinstance(factor_versions, list) else []}
        kind = str(row["config_kind"] or row["config_model_kind"] or "").strip()
        model_name = (
            str(row["model_name"] or "").strip()
            or str(row["config_model_name"] or "").strip()
            or kind_display_name(kind)
            or _strategy_label(row["strategy_name"] or row["strategy_entity_id"] or "")
            or "未登记模型"
        )
        return {
            "run_id": row["run_id"],
            "name": str(row["config_name"] or "未命名回测"),
            "note": str(row["config_note"] or "").strip(),
            "status": row["status"],
            "status_name": _STATUS_NAMES.get(row["status"], row["status"]),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "strategy": {"entity_id": row["strategy_entity_id"], "name": model_name},
            "factors": self._factor_labels(row, config),
            "test_window": {"date_from": row["date_from"], "date_to": row["date_to"]},
            "metrics": {
                key: _metric(
                    row[f"metric_{key}"],
                    percentage=key in _PERCENT_METRICS,
                    integer=key in _INTEGER_METRICS,
                )
                for key, _ in _METRICS
            },
            "benchmark": row["benchmark"] or "未生成",
            "detail_url": f"/backtests/runs/{row['run_id']}",
            "copy_url": f"/api/backtests/runs/{row['run_id']}/copy-config",
            "execute_url": f"/api/backtests/{row['run_id']}/execute",
            "delete_url": f"/api/backtests/runs/{row['run_id']}",
            "retryable": row["status"] == "failed",
            "machine_id": str(row["metrics_machine_id"] or row["config_machine_id"] or ""),
        }

    def _project_summary(self, row: Any) -> dict[str, Any]:
        factors = [part.strip() for part in str(row["factor_names"] or "").split(",") if part.strip()]
        if not factors:
            factors = [part.strip() for part in str(row["factor_key"] or "").split(",") if part.strip()]
        return {
            "run_id": row["run_id"],
            "name": str(row["name"] or "未命名回测"),
            "note": str(row["note"] or "").strip(),
            "status": row["status"],
            "status_name": _STATUS_NAMES.get(row["status"], row["status"]),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "strategy": {"entity_id": row["strategy_entity_id"], "name": str(row["strategy_name"] or "未登记模型")},
            "factors": factors,
            "test_window": {"date_from": row["date_from"], "date_to": row["date_to"]},
            "metrics": {
                key: _metric(
                    row[_METRIC_COLUMNS[key]],
                    percentage=key in _PERCENT_METRICS,
                    integer=key in _INTEGER_METRICS,
                )
                for key, _ in _METRICS
            },
            "benchmark": row["benchmark"] or "未生成",
            "detail_url": f"/backtests/runs/{row['run_id']}",
            "copy_url": f"/api/backtests/runs/{row['run_id']}/copy-config",
            "execute_url": f"/api/backtests/{row['run_id']}/execute",
            "delete_url": f"/api/backtests/runs/{row['run_id']}",
            "retryable": row["status"] == "failed",
            "machine_id": str(row["machine_id"] or ""),
        }

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        strategy: str | None = None,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        gate: str | None = None,
        factor: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("page_size must be between 1 and 200")
        if sort not in _SORT_FIELDS or order not in {"asc", "desc"}:
            raise ValueError("invalid sort or order")
        ensure_backtest_summaries(self.database)
        where = ["1=1"]
        params: list[Any] = []
        if status:
            if status not in _STATUS_NAMES:
                raise ValueError("invalid backtest status")
            where.append("status=?")
            params.append(status)
        if strategy:
            needle = f"%{strategy}%"
            where.append("(strategy_entity_id=? OR strategy_name LIKE ? OR kind LIKE ?)")
            params.extend([strategy, needle, needle])
        query_text = query.lower().strip() if query else ""
        if query_text:
            like = f"%{query_text}%"
            where.append(
                "(LOWER(run_id) LIKE ? OR LOWER(name) LIKE ? OR LOWER(note) LIKE ? "
                "OR LOWER(strategy_name) LIKE ? OR LOWER(factor_key) LIKE ? OR LOWER(factor_names) LIKE ?)"
            )
            params.extend([like, like, like, like, like, like])
        if gate:
            where.append("gate_key LIKE ?")
            params.append(f"%{gate}%")
        if factor:
            like = f"%{factor}%"
            where.append("(factor_key LIKE ? OR factor_names LIKE ?)")
            params.extend([like, like])
        if date_from:
            where.append("date_to >= ?")
            params.append(date_from)
        if date_to:
            where.append("date_from <= ?")
            params.append(date_to)
        predicate = " AND ".join(where)
        sort_col = _SORT_FIELDS[sort]
        direction = "DESC" if order == "desc" else "ASC"
        if sort == "created_at":
            order_sql = f"created_at {direction}, run_id {direction}"
        else:
            order_sql = (
                f"CASE WHEN {sort_col} IS NULL OR {sort_col}='' THEN 1 ELSE 0 END, "
                f"{sort_col} {direction}, created_at DESC, run_id DESC"
            )
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM backtest_summaries WHERE {predicate}",
                params,
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM backtest_summaries WHERE {predicate} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        items = [self._project_summary(row) for row in rows]
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
            "results_root": self.settings.display_path(self.settings.runtime_root / "results"),
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = next(iter(self._rows(run_id=run_id)), None)
        return self._project(row, include_detail=True) if row else None

    def delete(self, run_id: str) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        row = next(iter(self._rows(run_id=run_id)), None)
        if row is None:
            raise ValueError("找不到这条回测。")
        write_deleted_marker(self.settings, run_id)
        purge_backtest_run(self.settings, self.database, run_id, audit_action="backtest_result_delete")
        return {"run_id": run_id}

    def copy_config(self, run_id: str) -> dict[str, Any]:
        source = self.get(run_id)
        if source is None:
            raise ValueError("backtest run not found")
        timestamp = _now()
        draft_id = f"bt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
        config = workbench_form_config(dict(source["config"]))
        config["name"] = f"{source['name']}（复制）"
        factors = config.get("factor_versions", [])
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO backtest_drafts(draft_id, revision, factor_version_ids_json, strategy_entity_id, strategy_version_id, config_json, created_at, updated_at) VALUES (?,1,?,?,?,?,?,?)",
                (draft_id, json.dumps(factors, ensure_ascii=False, sort_keys=True), config.get("strategy_entity_id"), config.get("strategy_version_id"), json.dumps(config, ensure_ascii=False, sort_keys=True), timestamp, timestamp),
            )
        return {
            "draft_id": draft_id,
            "revision": 1,
            "config": config,
            "redirect_url": f"{_copy_redirect_path(config)}?draft_id={draft_id}",
        }

    def csv_text(self, **filters: Any) -> str:
        listing = self.list(page=1, page_size=200, **filters)
        all_items = list(listing["items"])
        for page in range(2, int(listing["pages"]) + 1):
            all_items.extend(self.list(page=page, page_size=200, **filters)["items"])
        output = io.StringIO()
        fields = ["ID", "回测名称", "备注", "状态", "创建时间", "完成时间", "模型", "因子", "测试区间", "累计收益", "年化收益", "夏普", "最大回撤", "胜率", "基准收益", "超额收益", "日均换手", "日均资金占用", "Rank IC", "NDCG@10", "基准"]
        writer = csv.writer(output)
        writer.writerow(fields)
        for item in all_items:
            writer.writerow([
                item["run_id"], item["name"], item.get("note") or "", item["status_name"], item["created_at"], item["finished_at"] or "未生成",
                item["strategy"]["name"], "、".join(item["factors"]), f"{item['test_window']['date_from']}~{item['test_window']['date_to']}",
                *[item["metrics"][key]["display"] for key, _ in _METRICS], item["benchmark"],
            ])
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO system_audit_logs(audit_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                (f"audit-{secrets.token_hex(12)}", "backtest_result_export", json.dumps(filters, ensure_ascii=False, sort_keys=True), _now()),
            )
        return output.getvalue()
