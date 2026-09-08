"""Indexed result archive projections for completed and in-flight backtests."""

from __future__ import annotations

import csv
import io
import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.backtest_job import fill_benchmark_metrics


_STATUS_NAMES = {"queued": "排队中", "running": "运行中", "completed": "已完成", "failed": "失败"}
_DEFAULT_STRATEGY_SUFFIX = " · 默认策略"
_ERROR_ZH = {
    "dataset snapshot hash mismatch": "行情文件在登记后又改过，这次回测没真正跑起来。请点「重新回测」。",
    "dataset snapshot row count mismatch": "行情行数和登记时不一致，这次回测没真正跑起来。请点「重新回测」。",
    "行情文件已经更新，和登记时对不上。": "行情文件已经更新，和登记时对不上。请点「重新回测」。",
    "行情行数和登记时不一致。": "行情行数和登记时不一致。请点「重新回测」。",
}
_SORT_FIELDS = {
    "created_at": "registry.created_at",
    "name": "br.config_json",
    "return": "br.metrics_json",
    "status": "br.status",
}
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
_STEP_STATUS_NAMES = {
    "pending": "等待",
    "running": "进行中",
    "completed": "完成",
    "failed": "失败",
    "skipped": "跳过",
}


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


def _annotate_step_timing(step: dict[str, Any]) -> dict[str, Any]:
    status = str(step.get("status") or "pending")
    duration_ms = None
    if status not in {"pending", "running", "skipped"}:
        duration_ms = _duration_ms(step.get("started_at"), step.get("finished_at"))
    step["step_label"] = _STEP_LABELS.get(str(step.get("step_name") or ""), step.get("step_name"))
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
        test = config.get("test") if isinstance(config.get("test"), dict) else {}
        strategy_name = _strategy_label(row["strategy_name"] or config.get("strategy_entity_id") or "未登记模型")
        item: dict[str, Any] = {
            "run_id": row["run_id"],
            "name": str(config.get("name") or "未命名回测"),
            "status": row["status"],
            "status_name": _STATUS_NAMES.get(row["status"], row["status"]),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "strategy": {"entity_id": row["strategy_entity_id"], "name": strategy_name},
            "factors": self._factor_labels(row, config),
            "test_window": {"date_from": test.get("date_from"), "date_to": test.get("date_to")},
            "metrics": {
                key: _metric(
                    metrics.get(key),
                    percentage=key in _PERCENT_METRICS,
                    integer=key in _INTEGER_METRICS,
                )
                for key, _ in _METRICS
            },
            "benchmark": config.get("benchmark", "未生成"),
            "detail_url": f"/backtests/runs/{row['run_id']}",
            "copy_url": f"/api/backtests/runs/{row['run_id']}/copy-config",
            "execute_url": f"/api/backtests/{row['run_id']}/execute",
            "delete_url": f"/api/backtests/runs/{row['run_id']}",
            "retryable": row["status"] == "failed",
        }
        if include_detail:
            item["config"] = config
            item["configuration"] = config
            item["metrics_raw"] = metrics
            item["error_message"] = _ERROR_ZH.get(row["error_message"] or "", row["error_message"])
            item["artifacts"] = self._artifact_rows(row["run_id"])
            item["results_root"] = str(self.settings.runtime_root / "results" / row["run_id"])
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
                _annotate_step_timing(step)
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

    def _rows(self, *, status: str | None = None, strategy: str | None = None) -> list[Any]:
        where = ["registry.run_type='backtest'"]
        params: list[Any] = []
        if status:
            if status not in _STATUS_NAMES:
                raise ValueError("invalid backtest status")
            where.append("br.status=?")
            params.append(status)
        if strategy:
            where.append("(br.strategy_entity_id=? OR s.name LIKE ?)")
            params.extend([strategy, f"%{strategy}%"])
        predicate = " AND ".join(where)
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT br.*, registry.created_at, registry.finished_at, s.name AS strategy_name, "
                "(SELECT group_concat(f.name, ',') FROM strategy_factor_versions sfv "
                "JOIN factors f ON f.entity_id=sfv.factor_entity_id "
                "WHERE sfv.strategy_entity_id=br.strategy_entity_id AND sfv.strategy_version_id=br.strategy_version_id) AS factor_names "
                "FROM backtest_runs br JOIN run_registry registry ON registry.run_id=br.run_id "
                "LEFT JOIN strategies s ON s.entity_id=br.strategy_entity_id "
                f"WHERE {predicate} ORDER BY registry.created_at DESC, br.run_id DESC",
                params,
            ).fetchall()

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
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("page_size must be between 1 and 200")
        if sort not in _SORT_FIELDS or order not in {"asc", "desc"}:
            raise ValueError("invalid sort or order")
        rows = self._rows(status=status, strategy=strategy)
        items = []
        query_text = query.lower().strip() if query else ""
        for row in rows:
            item = self._project(row)
            window = item["test_window"]
            haystack = " ".join([item["run_id"], item["name"], item["strategy"]["name"], *item["factors"]]).lower()
            if query_text and query_text not in haystack:
                continue
            if date_from and (window["date_to"] or "") < date_from:
                continue
            if date_to and (window["date_from"] or "") > date_to:
                continue
            items.append((row, item))
        if sort == "name":
            items.sort(key=lambda pair: pair[1]["name"], reverse=order == "desc")
        elif sort == "return":
            items.sort(key=lambda pair: pair[1]["metrics"]["return"]["value"] if pair[1]["metrics"]["return"]["value"] is not None else float("-inf"), reverse=order == "desc")
        elif sort == "status":
            items.sort(key=lambda pair: pair[1]["status"], reverse=order == "desc")
        else:
            items.sort(key=lambda pair: (pair[1]["created_at"], pair[1]["run_id"]), reverse=order == "desc")
        total = len(items)
        start = (page - 1) * page_size
        return {"items": [item for _, item in items[start : start + page_size]], "page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size, "results_root": str(self.settings.runtime_root / "results")}

    def get(self, run_id: str) -> dict[str, Any] | None:
        rows = self._rows()
        row = next((item for item in rows if item["run_id"] == run_id), None)
        return self._project(row, include_detail=True) if row else None

    def _result_dir(self, run_id: str) -> Path:
        root = (self.settings.runtime_root / "results").resolve()
        target = (root / run_id).resolve()
        if target == root or root not in target.parents:
            raise ValueError("result path is outside results root")
        return self.settings.require_write_path(target)

    def _remove_result_files(self, run_id: str, artifact_paths: list[str]) -> None:
        for raw in artifact_paths:
            try:
                path = self.settings.require_write_path(raw)
            except ValueError:
                continue
            if path.is_file():
                path.unlink()
        try:
            target = self._result_dir(run_id)
        except ValueError:
            return
        if target.is_dir():
            shutil.rmtree(target)

    def delete(self, run_id: str) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        row = next((item for item in self._rows() if item["run_id"] == run_id), None)
        if row is None:
            raise ValueError("找不到这条回测。")
        with self.database.connect() as connection:
            artifact_rows = connection.execute("SELECT path FROM artifacts WHERE run_id=?", (run_id,)).fetchall()
        artifact_paths = [str(item["path"]) for item in artifact_rows]
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM backtest_steps WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM backtest_model_versions WHERE backtest_run_id=?", (run_id,))
            connection.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
            deleted = connection.execute(
                "DELETE FROM run_registry WHERE run_id=? AND run_type='backtest'",
                (run_id,),
            )
            if deleted.rowcount != 1:
                raise ValueError("找不到这条回测。")
            connection.execute(
                "INSERT INTO system_audit_logs(audit_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    f"audit-{secrets.token_hex(12)}",
                    "backtest_result_delete",
                    json.dumps({"run_id": run_id}, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
        self._remove_result_files(run_id, artifact_paths)
        return {"run_id": run_id}

    def copy_config(self, run_id: str) -> dict[str, Any]:
        source = self.get(run_id)
        if source is None:
            raise ValueError("backtest run not found")
        timestamp = _now()
        draft_id = f"bt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
        config = dict(source["config"])
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
        fields = ["ID", "回测名称", "状态", "创建时间", "完成时间", "模型", "因子", "测试区间", "累计收益", "年化收益", "夏普", "最大回撤", "胜率", "基准收益", "超额收益", "日均换手", "日均资金占用", "Rank IC", "NDCG@10", "基准"]
        writer = csv.writer(output)
        writer.writerow(fields)
        for item in all_items:
            writer.writerow([
                item["run_id"], item["name"], item["status_name"], item["created_at"], item["finished_at"] or "未生成",
                item["strategy"]["name"], "、".join(item["factors"]), f"{item['test_window']['date_from']}~{item['test_window']['date_to']}",
                *[item["metrics"][key]["display"] for key, _ in _METRICS], item["benchmark"],
            ])
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO system_audit_logs(audit_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                (f"audit-{secrets.token_hex(12)}", "backtest_result_export", json.dumps(filters, ensure_ascii=False, sort_keys=True), _now()),
            )
        return output.getvalue()
