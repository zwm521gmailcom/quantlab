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


def _fixed_bins(values: list[float], start: float, end: float, bins: int) -> dict[str, Any]:
    width = (end - start) / bins
    counts = [0] * bins
    for value in values:
        index = int((float(value) - start) / width)
        if index < 0:
            index = 0
        elif index >= bins:
            index = bins - 1
        counts[index] += 1
    return {"start": start, "end": end, "counts": counts}


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

    def chart_data(self) -> dict[str, Any]:
        """Read stored columns into fixed bins for the overview charts."""
        annual_kinds = (
            "qlib_topk_dropout",
            "qlib_lgb_multi",
            "qlib_lgb_regression",
            "lightgbm_tree",
            "factor_rank",
        )
        drawdown_kinds = ("qlib_topk_dropout", "qlib_lgb_multi", "lightgbm_tree")
        with self.database.connect() as connection:
            kind_counts: dict[str, int] = {}
            status_counts: dict[str, int] = {}
            for row in connection.execute(
                "SELECT kind, status, COUNT(*) AS count FROM backtest_summaries GROUP BY kind, status"
            ):
                kind = str(row["kind"] or "") or "unknown"
                status = str(row["status"] or "") or "unknown"
                count = int(row["count"])
                kind_counts[kind] = kind_counts.get(kind, 0) + count
                status_counts[status] = status_counts.get(status, 0) + count
            annual = {kind: [] for kind in annual_kinds}
            drawdown = {kind: [] for kind in drawdown_kinds}
            for row in connection.execute(
                "SELECT kind, annual_return, max_drawdown FROM backtest_summaries WHERE annual_return IS NOT NULL"
            ):
                kind = str(row["kind"] or "")
                if kind in annual and row["annual_return"] is not None:
                    annual[kind].append(float(row["annual_return"]))
                if kind in drawdown and row["max_drawdown"] is not None:
                    drawdown[kind].append(float(row["max_drawdown"]))
            ic_values: list[float] = []
            coverage_values: list[float] = []
            for row in connection.execute(
                "SELECT ic_mean, coverage FROM factor_calculation_runs WHERE status = 'completed'"
            ):
                if row["ic_mean"] is not None:
                    ic_values.append(float(row["ic_mean"]))
                if row["coverage"] is not None:
                    coverage_values.append(float(row["coverage"]))
            quality: list[dict[str, Any]] = []
            for source, table in (
                ("dataset_version", "dataset_versions"),
                ("factor_version", "factor_versions"),
                ("model_version", "model_versions"),
                ("strategy_version", "strategy_versions"),
            ):
                for row in connection.execute(
                    f"SELECT quality_status, COUNT(*) AS count FROM {table} GROUP BY quality_status"
                ):
                    quality.append(
                        {
                            "source": source,
                            "status": str(row["quality_status"] or ""),
                            "count": int(row["count"]),
                        }
                    )
            datasets = [
                {
                    "id": str(row["entity_id"] or ""),
                    "rows": int(row["row_count"] or 0),
                    "date_min": str(row["date_min"] or ""),
                    "date_max": str(row["date_max"] or ""),
                    "quality_status": str(row["quality_status"] or ""),
                }
                for row in connection.execute(
                    "SELECT entity_id, row_count, date_min, date_max, quality_status "
                    "FROM dataset_versions ORDER BY entity_id"
                )
            ]
        return {
            "kind_counts": [{"kind": kind, "count": count} for kind, count in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))],
            "status_counts": status_counts,
            "annual": {kind: _fixed_bins(annual[kind], -0.8, 1.2, 20) for kind in annual_kinds},
            "drawdown": {kind: _fixed_bins(drawdown[kind], -1.0, 0.0, 16) for kind in drawdown_kinds},
            "factor_ic": _fixed_bins(ic_values, -0.05, 0.05, 16),
            "factor_coverage": _fixed_bins(coverage_values, 0.6, 1.0, 16),
            "quality": quality,
            "datasets": datasets,
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
