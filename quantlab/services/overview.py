"""Read-only aggregation for the QuantLab research overview."""

from __future__ import annotations

import json
from typing import Any

from quantlab.repositories.database import Database


_RUN_TABLES = {
    "research": ("research_runs", "研究运行"),
    "model_training": ("model_training_runs", "模型训练"),
    "backtest": ("backtest_runs", "回测运行"),
}
_DETAIL_URLS = {
    "research": "/research/runs/{}",
    "model_training": "/models/runs/{}",
    "backtest": "/backtests/runs/{}",
}
_RECENT_RUN_LIMIT = 10


class OverviewService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_overview(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            summary = {
                "dataset_count": connection.execute(
                    "SELECT COUNT(*) FROM datasets WHERE status = 'published'"
                ).fetchone()[0],
                "published_factor_count": connection.execute(
                    "SELECT COUNT(*) FROM factors WHERE status = 'published'"
                ).fetchone()[0],
                "model_count": connection.execute(
                    "SELECT COUNT(*) FROM models WHERE status = 'published'"
                ).fetchone()[0],
                "backtest_count": connection.execute(
                    "SELECT COUNT(*) FROM backtest_runs"
                ).fetchone()[0],
                "strategy_count": connection.execute(
                    "SELECT COUNT(*) FROM strategies WHERE status = 'published'"
                ).fetchone()[0],
            }
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM "
                "(SELECT status FROM research_runs UNION ALL "
                "SELECT status FROM model_training_runs UNION ALL "
                "SELECT status FROM backtest_runs) GROUP BY status ORDER BY status"
            ).fetchall()
            recent_runs = self._recent_runs(connection)
            quality_alerts = self._quality_alerts(connection)
        return {
            "summary": summary,
            "run_status_counts": {row["status"]: row["count"] for row in status_rows},
            "recent_runs": recent_runs,
            "quality_alerts": quality_alerts,
        }

    @staticmethod
    def _recent_runs(connection) -> list[dict[str, Any]]:
        projections: list[dict[str, Any]] = []
        for run_type, (table, fallback_name) in _RUN_TABLES.items():
            name_sql = "?"
            params: list[object] = [fallback_name]
            if run_type == "model_training":
                name_sql = "COALESCE(models.name, ?)"
            elif run_type == "backtest":
                name_sql = "COALESCE(NULLIF(json_extract(run.config_json, '$.name'), ''), strategies.name, ?)"
            joins = ""
            if run_type == "model_training":
                joins = " LEFT JOIN models ON models.entity_id = run.model_entity_id"
            elif run_type == "backtest":
                joins = " LEFT JOIN strategies ON strategies.entity_id = run.strategy_entity_id"
            rows = connection.execute(
                f"SELECT registry.run_type, registry.run_id, {name_sql} AS name, "
                "run.status, registry.created_at, registry.finished_at "
                f"FROM {table} AS run JOIN run_registry AS registry ON registry.run_id = run.run_id"
                f"{joins} ORDER BY registry.created_at DESC, registry.run_id DESC LIMIT {_RECENT_RUN_LIMIT}",
                params,
            ).fetchall()
            projections.extend(
                {
                    "run_type": row["run_type"],
                    "run_id": row["run_id"],
                    "name": row["name"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "finished_at": row["finished_at"],
                    "detail_url": _DETAIL_URLS[run_type].format(row["run_id"]),
                }
                for row in rows
            )
        projections.sort(key=lambda item: (item["created_at"], item["run_id"]), reverse=True)
        return projections[:_RECENT_RUN_LIMIT]

    @staticmethod
    def _quality_alerts(connection) -> list[dict[str, Any]]:
        sources = (
            ("dataset_version", "dataset_versions", "entity_id, version_id, quality_status, metadata_json"),
            ("factor_version", "factor_versions", "entity_id, version_id, quality_status, '{}' AS metadata_json"),
            ("model_version", "model_versions", "entity_id, version_id, quality_status, '{}' AS metadata_json"),
            ("strategy_version", "strategy_versions", "entity_id, version_id, quality_status, '{}' AS metadata_json"),
        )
        alerts: list[dict[str, Any]] = []
        for entity_type, table, columns in sources:
            rows = connection.execute(
                f"SELECT {columns} FROM {table} WHERE quality_status != 'passed' "
                "ORDER BY entity_id, version_id"
            ).fetchall()
            for row in rows:
                metadata = json.loads(row["metadata_json"] or "{}")
                reason = metadata.get("quality_reason")
                status = row["quality_status"]
                alerts.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": row["entity_id"],
                        "version_id": row["version_id"],
                        "quality_status": status,
                        "message": reason or f"质量状态为 {status}",
                    }
                )
        return alerts
