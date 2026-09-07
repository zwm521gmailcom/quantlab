"""Version-locked research runs and their immutable configuration snapshots."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.domain.status import RunStatus, transition
from quantlab.repositories.database import Database
from quantlab.services.run_identity import RunIdentity


_TARGET_ONLY_FIELDS = frozenset(
    {
        "label",
        "future_return",
        "future_return_1pct",
        "future_return_99pct",
        "clipped_return",
        "binned_return",
    }
)
_RESEARCH_TYPES = frozenset({"manual", "automatic"})
_MAX_PAGE_SIZE = 100
_DATE_PATTERN = re.compile(r"^(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ResearchRunRepository:
    """Own the ResearchRun lifecycle; no execution is performed by this repository."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.identity = RunIdentity(database)

    @staticmethod
    def _validate_target_values(value: Any, context: str = "config") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {
                    "input_fields",
                    "predict_fields",
                    "feature_fields",
                    "fields",
                    "features",
                    "predictor_fields",
                    "columns",
                }:
                    values = item if isinstance(item, list) else [item]
                    leaked = sorted(
                        {str(entry) for entry in values} & _TARGET_ONLY_FIELDS
                    )
                    if leaked:
                        raise ValueError(
                            f"target-only fields cannot be prediction inputs: {', '.join(leaked)}"
                        )
                ResearchRunRepository._validate_target_values(item, f"{context}.{key}")
        elif isinstance(value, list):
            for item in value:
                ResearchRunRepository._validate_target_values(item, context)

    def _validate_config(
        self, config: dict[str, Any], *, allow_draft_factor_versions: bool = False
    ) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("research config is required")
        dataset_id = str(config.get("dataset_id", "")).strip()
        dataset_version_id = str(config.get("dataset_version_id", "")).strip()
        factor_versions = config.get("factor_versions")
        if not dataset_id or not dataset_version_id:
            raise ValueError("published dataset_id and dataset_version_id are required")
        if not isinstance(factor_versions, list) or not factor_versions:
            raise ValueError("at least one factor version is required")
        sample = config.get("sample")
        if not isinstance(sample, dict):
            raise ValueError("sample is required")
        date_values: dict[str, str] = {}
        for field in ("date_from", "date_to"):
            value = sample.get(field)
            if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
                raise ValueError(f"sample.{field} must be YYYYMMDD or YYYY-MM-DD")
            normalized = value.replace("-", "")
            try:
                datetime.strptime(normalized, "%Y%m%d")
            except ValueError as error:
                raise ValueError(f"sample.{field} must be a valid date") from error
            date_values[field] = normalized
        if date_values["date_from"] > date_values["date_to"]:
            raise ValueError("sample date_from must not be after date_to")
        if (
            not isinstance(sample.get("universe"), str)
            or not sample["universe"].strip()
        ):
            raise ValueError("sample.universe is required")
        label = config.get("label")
        if (
            not isinstance(label, dict)
            or label.get("definition") != "t+1 open -> t+2 close"
        ):
            raise ValueError("label definition must be t+1 open -> t+2 close")
        price_fields = label.get("price_fields")
        if not isinstance(price_fields, list) or price_fields != [
            "hfq_open",
            "hfq_close",
        ]:
            raise ValueError("label price_fields must be hfq_open and hfq_close")
        filter_snapshot = config.get("filter_snapshot")
        if (
            not isinstance(filter_snapshot, dict)
            or "st_status" not in filter_snapshot
            or "suspended" not in filter_snapshot
            or not isinstance(filter_snapshot.get("captured_at"), str)
            or not filter_snapshot["captured_at"].strip()
        ):
            raise ValueError(
                "filter_snapshot requires st_status, suspended, captured_at"
            )
        pit_snapshot = config.get("pit_snapshot")
        if (
            not isinstance(pit_snapshot, dict)
            or not isinstance(pit_snapshot.get("rule"), str)
            or not pit_snapshot["rule"].strip()
            or not isinstance(pit_snapshot.get("captured_at"), str)
            or not pit_snapshot["captured_at"].strip()
        ):
            raise ValueError("pit_snapshot requires rule and captured_at")
        self._validate_target_values(config)
        with self.database.connect() as connection:
            dataset = connection.execute(
                "SELECT d.status AS dataset_status, dv.status, dv.quality_status "
                "FROM dataset_versions dv JOIN datasets d ON d.entity_id=dv.entity_id "
                "WHERE dv.entity_id=? AND dv.version_id=?",
                (dataset_id, dataset_version_id),
            ).fetchone()
            if (
                dataset is None
                or dataset["dataset_status"] != "published"
                or dataset["status"] != "published"
            ):
                raise ValueError("published dataset version is required")
            for item in factor_versions:
                if not isinstance(item, dict):
                    raise ValueError("factor_versions must contain objects")
                factor_id = str(item.get("factor_id", "")).strip()
                version_id = str(item.get("version_id", "")).strip()
                if (
                    factor_id in _TARGET_ONLY_FIELDS
                    or factor_id.removeprefix("factor_") in _TARGET_ONLY_FIELDS
                ):
                    raise ValueError(
                        f"{factor_id} is target-only and cannot be a prediction factor"
                    )
                factor = connection.execute(
                    "SELECT f.status AS factor_status, fv.status, fv.dataset_id, fv.dataset_version_id "
                    "FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id "
                    "WHERE fv.entity_id=? AND fv.version_id=?",
                    (factor_id, version_id),
                ).fetchone()
                if (
                    factor is None
                    or factor["factor_status"]
                    not in (
                        {"draft", "validated", "published"}
                        if allow_draft_factor_versions
                        else {"published"}
                    )
                    or factor["status"]
                    not in (
                        {"draft", "validated", "published"}
                        if allow_draft_factor_versions
                        else {"published"}
                    )
                ):
                    raise ValueError(
                        f"published factor version is required: {factor_id}:{version_id}"
                    )
                if (
                    factor["dataset_id"] != dataset_id
                    or factor["dataset_version_id"] != dataset_version_id
                ):
                    raise ValueError(
                        "all factor versions must lock the research dataset version"
                    )
        normalized_config = dict(config)
        normalized_config["sample"] = {**sample, **date_values}
        return normalized_config

    @staticmethod
    def _duration(created_at: str | None, finished_at: str | None) -> float | None:
        if not created_at or not finished_at:
            return None
        try:
            start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return max(0.0, (end - start).total_seconds())
        except ValueError:
            return None

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        config = json.loads(row["config_json"] or "{}")
        summary = json.loads(row["summary_json"] or "{}")
        return {
            "run_id": row["run_id"],
            "name": row["name"],
            "research_type": row["research_type"],
            "status": row["status"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "duration_seconds": self._duration(row["created_at"], row["finished_at"]),
            "dataset_id": config.get("dataset_id"),
            "dataset_version_id": config.get("dataset_version_id"),
            "factor_versions": config.get("factor_versions", []),
            "summary": summary,
            "copied_from_run_id": row["copied_from_run_id"],
            "parent_run_id": row["parent_run_id"],
            "detail_url": f"/research/runs/{row['run_id']}",
        }

    def _row(self, run_id: str) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT rr.run_id, rr.name, rr.research_type, rr.status, rr.config_json, rr.summary_json, "
                "rr.copied_from_run_id, rr.parent_run_id, rr.error_message, registry.created_at, registry.finished_at "
                "FROM research_runs rr JOIN run_registry registry ON registry.run_id=rr.run_id "
                "WHERE rr.run_id=?",
                (run_id,),
            ).fetchone()

    def create(
        self,
        *,
        name: str,
        research_type: str,
        config: dict[str, Any],
        run_id: str | None = None,
        copied_from_run_id: str | None = None,
        parent_run_id: str | None = None,
        allow_draft_factor_versions: bool = False,
    ) -> dict[str, Any]:
        name = str(name).strip()
        if not name:
            raise ValueError("research name is required")
        if research_type not in _RESEARCH_TYPES:
            raise ValueError("research_type must be manual or automatic")
        validated = self._validate_config(
            config, allow_draft_factor_versions=allow_draft_factor_versions
        )
        resolved_run_id = run_id or self.identity.next_id()
        validate_run_id(resolved_run_id)
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO run_registry(run_id, run_type, created_at) VALUES (?, 'research', ?)",
                    (resolved_run_id, _now()),
                )
                connection.execute(
                    "INSERT INTO research_runs(run_id, name, research_type, status, config_json, summary_json, copied_from_run_id, parent_run_id) "
                    "VALUES (?, ?, ?, 'queued', ?, '{}', ?, ?)",
                    (
                        resolved_run_id,
                        name,
                        research_type,
                        _json(validated),
                        copied_from_run_id,
                        parent_run_id,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "UNIQUE" in str(error) or "PRIMARY KEY" in str(error):
                raise ValueError(f"run_id already exists: {resolved_run_id}") from error
            raise ValueError(f"research run could not be created: {error}") from error
        created = self.get(resolved_run_id)
        if created is None:
            raise RuntimeError("created research run could not be loaded")
        return created

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self._row(run_id)
        if row is None:
            return None
        result = self._public(row)
        result["config"] = json.loads(row["config_json"] or "{}")
        result["error_message"] = row["error_message"]
        with self.database.connect() as connection:
            artifacts = connection.execute(
                "SELECT artifact_id, display_name, original_name, artifact_role, size_bytes, content_hash, created_at "
                "FROM artifacts WHERE run_id=? ORDER BY created_at, artifact_id",
                (run_id,),
            ).fetchall()
        result["artifacts"] = [dict(item) for item in artifacts]
        return result

    def list(
        self,
        *,
        query: str | None = None,
        research_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > _MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        if research_type is not None and research_type not in _RESEARCH_TYPES:
            raise ValueError("invalid research_type")
        if status is not None:
            try:
                RunStatus(status)
            except ValueError as error:
                raise ValueError("invalid research status") from error
        where = []
        params: list[Any] = []
        if query:
            where.append("(rr.name LIKE ? OR rr.run_id LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if research_type:
            where.append("rr.research_type=?")
            params.append(research_type)
        if status:
            where.append("rr.status=?")
            params.append(status)
        predicate = f"WHERE {' AND '.join(where)}" if where else ""
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM research_runs rr {predicate}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT rr.run_id, rr.name, rr.research_type, rr.status, rr.config_json, rr.summary_json, "
                "rr.copied_from_run_id, rr.parent_run_id, rr.error_message, registry.created_at, registry.finished_at "
                f"FROM research_runs rr JOIN run_registry registry ON registry.run_id=rr.run_id {predicate} "
                "ORDER BY registry.created_at DESC, rr.run_id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [self._public(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": page * page_size < total,
        }

    def transition(self, run_id: str, target: str) -> dict[str, Any]:
        row = self._row(run_id)
        if row is None:
            raise ValueError("research run not found")
        try:
            next_status = transition(RunStatus(row["status"]), RunStatus(target)).value
        except ValueError as error:
            raise ValueError(str(error)) from error
        finished_at = _now() if next_status in {"completed", "failed"} else None
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE research_runs SET status=? WHERE run_id=?",
                (next_status, run_id),
            )
            if finished_at:
                connection.execute(
                    "UPDATE run_registry SET finished_at=? WHERE run_id=?",
                    (finished_at, run_id),
                )
        return self.get(run_id) or {}

    def complete(self, run_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        self.transition(run_id, "running") if (self._row(run_id) or {"status": ""})[
            "status"
        ] == "queued" else None
        row = self._row(run_id)
        if row is None:
            raise ValueError("research run not found")
        if row["status"] != "running":
            raise ValueError("research run must be running before completion")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE research_runs SET summary_json=? WHERE run_id=?",
                (_json(summary), run_id),
            )
        return self.transition(run_id, "completed")

    def patch_summary(self, run_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        row = self._row(run_id)
        if row is None:
            raise ValueError("research run not found")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE research_runs SET summary_json=? WHERE run_id=?",
                (_json(summary), run_id),
            )
        return self.get(run_id) or {}

    def fail(self, run_id: str, message: str) -> dict[str, Any]:
        row = self._row(run_id)
        if row is None:
            raise ValueError("research run not found")
        if row["status"] == "queued":
            self.transition(run_id, "running")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE research_runs SET error_message=? WHERE run_id=?",
                (str(message), run_id),
            )
        return self.transition(run_id, "failed")

    def save_checkpoint(self, run_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
        """Persist resumable job state while keeping the normal run lifecycle."""
        row = self._row(run_id)
        if row is None:
            raise ValueError("research run not found")
        if row["status"] not in {"queued", "running"}:
            raise ValueError("checkpoint requires a queued or running research run")
        summary = json.loads(row["summary_json"] or "{}")
        summary["checkpoint"] = checkpoint
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE research_runs SET summary_json=? WHERE run_id=?",
                (_json(summary), run_id),
            )
        return self.get(run_id) or {}

    def copy_config(self, run_id: str, *, name: str | None = None) -> dict[str, Any]:
        source = self.get(run_id)
        if source is None:
            raise ValueError("research run not found")
        validated = self._validate_config(source["config"])
        target_path = (
            "/research/factors/manual"
            if source["research_type"] == "manual"
            else "/research/factors/auto"
        )
        return {
            "source_run_id": run_id,
            "name": name or f"复制：{source['name']}",
            "research_type": source["research_type"],
            "config": validated,
            "target_url": f"{target_path}?copy_from_run_id={run_id}",
        }
