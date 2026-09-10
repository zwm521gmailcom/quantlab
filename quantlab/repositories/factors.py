"""Versioned factor definitions and their guarded lifecycle operations."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.domain.status import LifecycleStatus, transition
from quantlab.repositories.database import Database
from quantlab.services.asset_layout import asset_class_fields, normalize_asset_class
from quantlab.services.factor_data import FEATURE_FIELDS


TARGET_ONLY_FIELDS = frozenset(
    {
        "label",
        "future_return",
        "future_return_1pct",
        "future_return_99pct",
        "clipped_return",
        "binned_return",
    }
)
QUALITY_STATUSES = frozenset({"passed", "warning", "failed", "needs_review"})
LIFECYCLE_STATUSES = frozenset({"draft", "validated", "published", "deprecated"})
ORIGINS = frozenset({"manual", "automatic", "import"})
MAX_PAGE_SIZE = 100
MAX_DIAGNOSE_ITEMS = 50
MAX_ACTION_ITEMS = 50
_ENTITY_PATTERN = re.compile(r"^factor_[a-z0-9_]+$")
_VERSION_PATTERN = re.compile(r"^v[0-9][a-zA-Z0-9._-]*$")

_DEFINITION_FIELDS = {
    "entity_id",
    "name",
    "category",
    "asset_class",
    "version_id",
    "dataset_id",
    "dataset_version_id",
    "formula",
    "input_fields",
    "source",
    "direction",
    "frequency",
    "missing_policy",
    "pit_policy",
    "pit_lineage",
    "upstream_factor_versions",
    "quality_status",
    "origin",
    "author",
    "code_hash",
    "generation_run_id",
    "artifact_id",
    "quality",
}
_UPSTREAM_FIELDS = {"entity_id", "version_id"}
_TEXT_FIELDS = {
    "name",
    "category",
    "dataset_id",
    "dataset_version_id",
    "formula",
    "source",
    "frequency",
    "missing_policy",
    "pit_policy",
}
_OPTIONAL_TEXT_FIELDS = {"code_hash", "generation_run_id", "artifact_id"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FactorRepository:
    """Repository for Factor and immutable FactorVersion records.

    Source datasets are opened read-only.  All lifecycle changes happen inside
    an explicit SQLite transaction and are additionally protected by schema
    triggers.
    """

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    @staticmethod
    def _reject_target(value: str) -> None:
        if value in TARGET_ONLY_FIELDS:
            raise ValueError(f"{value} is target-only and cannot be a factor input")

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @classmethod
    def _normalize(cls, definition: dict[str, Any], *, default_origin: str = "import") -> dict[str, Any]:
        if not isinstance(definition, dict):
            raise ValueError("factor definition must be an object")
        extra = sorted(set(definition) - _DEFINITION_FIELDS)
        if extra:
            raise ValueError(f"additional fields are not allowed: {', '.join(extra)}")
        normalized: dict[str, Any] = {}
        entity_id = cls._require_text(definition.get("entity_id"), "entity_id")
        version_id = cls._require_text(definition.get("version_id"), "version_id")
        if not _ENTITY_PATTERN.fullmatch(entity_id):
            raise ValueError("entity_id must match factor_<lower_snake_case>")
        if not _VERSION_PATTERN.fullmatch(version_id):
            raise ValueError("version_id must match v<number>...")
        normalized["entity_id"] = entity_id
        normalized["version_id"] = version_id
        for field in _TEXT_FIELDS:
            normalized[field] = cls._require_text(definition.get(field), field)
        for field in _OPTIONAL_TEXT_FIELDS:
            value = definition.get(field, "")
            if value is None:
                value = ""
            normalized[field] = cls._require_text(value, field) if value else ""
        direction = cls._require_text(definition.get("direction"), "direction")
        if direction not in {"positive", "negative"}:
            raise ValueError("direction must be positive or negative")
        normalized["direction"] = direction
        input_fields = definition.get("input_fields")
        if not isinstance(input_fields, list) or not input_fields or any(
            not isinstance(item, str) or not item.strip() for item in input_fields
        ):
            raise ValueError("input_fields must be a non-empty string array")
        input_fields = list(dict.fromkeys(item.strip() for item in input_fields))
        for field in input_fields:
            cls._reject_target(field)
        normalized["input_fields"] = input_fields
        pit_lineage = definition.get("pit_lineage")
        if not isinstance(pit_lineage, dict):
            raise ValueError("pit_lineage must be an object")
        pit_extra = sorted(set(pit_lineage) - {"rule", "snapshot", "window_mode"})
        if pit_extra:
            raise ValueError(f"additional PIT lineage fields are not allowed: {', '.join(pit_extra)}")
        if pit_lineage.get("window_mode", "per_instrument_observation") not in {"per_instrument_observation", "market_calendar"}:
            raise ValueError("pit_lineage window_mode is invalid")
        normalized["pit_lineage"] = dict(pit_lineage)
        normalized["pit_policy"] = normalized["pit_policy"]
        upstream = definition.get("upstream_factor_versions", [])
        if not isinstance(upstream, list):
            raise ValueError("upstream_factor_versions must be an array")
        normalized_upstream: list[dict[str, str]] = []
        for item in upstream:
            if not isinstance(item, dict):
                raise ValueError("upstream_factor_versions must contain objects")
            extra_upstream = sorted(set(item) - _UPSTREAM_FIELDS)
            if extra_upstream:
                raise ValueError(f"additional upstream fields are not allowed: {', '.join(extra_upstream)}")
            upstream_entity = cls._require_text(item.get("entity_id"), "upstream.entity_id")
            upstream_version = cls._require_text(item.get("version_id"), "upstream.version_id")
            if not _ENTITY_PATTERN.fullmatch(upstream_entity) or not _VERSION_PATTERN.fullmatch(upstream_version):
                raise ValueError("upstream FactorVersion reference is invalid")
            normalized_upstream.append({"entity_id": upstream_entity, "version_id": upstream_version})
        normalized["upstream_factor_versions"] = normalized_upstream
        normalized["quality_status"] = definition.get("quality_status", "needs_review")
        if normalized["quality_status"] not in QUALITY_STATUSES:
            raise ValueError("quality_status is invalid")
        normalized["origin"] = definition.get("origin", default_origin)
        if normalized["origin"] not in ORIGINS:
            raise ValueError("origin must be manual, automatic, or import")
        author = definition.get("author", "未登记")
        normalized["author"] = cls._require_text(author, "author")
        quality = definition.get("quality", {})
        if not isinstance(quality, dict):
            raise ValueError("quality must be an object")
        normalized["quality"] = quality
        normalized.update(asset_class_fields(definition.get("asset_class")))
        return normalized

    @staticmethod
    def _row_to_definition(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "category": row["category"],
            "asset_class": normalize_asset_class(row["asset_class"] if "asset_class" in row.keys() else None),
            "version_id": row["version_id"],
            "dataset_id": row["dataset_id"],
            "dataset_version_id": row["dataset_version_id"],
            "formula": row["formula"],
            "input_fields": json.loads(row["input_fields_json"] or "[]"),
            "source": row["source"],
            "direction": row["direction"],
            "frequency": row["frequency"],
            "missing_policy": row["missing_policy"],
            "pit_policy": row["pit_policy"],
            "pit_lineage": json.loads(row["pit_lineage_json"] or "{}"),
            "upstream_factor_versions": json.loads(row["upstream_factor_versions_json"] or "[]"),
            "quality_status": row["quality_status"],
            "origin": row["origin"],
            "author": row["author"],
            "code_hash": row["code_hash"],
            "generation_run_id": row["generation_run_id"],
            "artifact_id": row["artifact_id"],
            "quality": json.loads(row["quality_json"] or "{}"),
        }

    @staticmethod
    def _canonical_entity_id(entity_id: str) -> str:
        value = str(entity_id or "").strip()
        if not value or value.startswith("factor_"):
            return value
        return f"factor_{value}"

    @staticmethod
    def _entity_id_candidates(entity_id: str) -> list[str]:
        value = str(entity_id or "").strip()
        if not value:
            return []
        candidates = [value]
        if value.startswith("factor_"):
            bare = value.removeprefix("factor_")
            if bare:
                candidates.append(bare)
        else:
            candidates.append(f"factor_{value}")
        ordered: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    @classmethod
    def _is_builtin_factor(cls, entity_id: str) -> bool:
        canonical = cls._canonical_entity_id(entity_id)
        bare = canonical.removeprefix("factor_") if canonical.startswith("factor_") else canonical
        return bare in FEATURE_FIELDS

    def _resolve_entity_id(self, entity_id: str, connection: sqlite3.Connection | None = None) -> str | None:
        candidates = self._entity_id_candidates(entity_id)
        if not candidates:
            return None
        own_connection = connection is None
        connection = connection or self.database.connect()
        try:
            for candidate in candidates:
                row = connection.execute(
                    "SELECT entity_id FROM factors WHERE entity_id=?", (candidate,)
                ).fetchone()
                if row is not None:
                    return str(row["entity_id"])
            return None
        finally:
            if own_connection:
                connection.close()

    @classmethod
    def _reference_key(cls, entity_id: str, version_id: str) -> str:
        return f"{cls._canonical_entity_id(entity_id)}:{version_id}"

    @staticmethod
    def _is_diagnostic_research_run(name: object, config: object) -> bool:
        if str(name or "").startswith("手动因子诊断："):
            return True
        if not isinstance(config, dict):
            return False
        snapshots = [config.get("pit_snapshot"), config.get("filter_snapshot")]
        return any(
            isinstance(item, dict) and item.get("captured_at") == "manual-preview"
            for item in snapshots
        )

    def _row(self, entity_id: str, version_id: str, connection: sqlite3.Connection | None = None) -> sqlite3.Row | None:
        own_connection = connection is None
        connection = connection or self.database.connect()
        try:
            return connection.execute(
                "SELECT f.entity_id, f.name, f.category, f.asset_class, f.status AS factor_status, "
                "fv.version_id, fv.dataset_id, fv.dataset_version_id, fv.formula, fv.input_fields_json, "
                "fv.source, fv.direction, fv.frequency, fv.missing_policy, fv.pit_policy, "
                "fv.pit_lineage_json, fv.upstream_factor_versions_json, fv.origin, fv.author, fv.code_hash, "
                "fv.generation_run_id, fv.artifact_id, fv.last_verified_at, "
                "fv.quality_json, fv.status, fv.quality_status, dv.path, dv.fields_json, dv.row_count, "
                "dv.date_min, dv.date_max, dv.quality_status AS dataset_quality_status "
                "FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id "
                "JOIN dataset_versions dv ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
                "WHERE fv.entity_id=? AND fv.version_id=?",
                (entity_id, version_id),
            ).fetchone()
        finally:
            if own_connection:
                connection.close()

    def _dataset_info(self, definition: dict[str, Any], connection: sqlite3.Connection) -> tuple[sqlite3.Row, list[str], Path]:
        row = connection.execute(
            "SELECT d.status AS dataset_status, dv.status, dv.quality_status, dv.path, dv.fields_json "
            "FROM dataset_versions dv JOIN datasets d ON d.entity_id=dv.entity_id "
            "WHERE dv.entity_id=? AND dv.version_id=?",
            (definition["dataset_id"], definition["dataset_version_id"]),
        ).fetchone()
        if row is None:
            raise ValueError("dataset version does not exist")
        fields = json.loads(row["fields_json"] or "[]")
        if not isinstance(fields, list):
            raise ValueError("dataset fields metadata is invalid")
        try:
            path = self.settings.require_read_path(row["path"])
        except ValueError as error:
            raise ValueError("dataset version path is outside the allowed read roots") from error
        if not path.is_file() or path.suffix != ".parquet":
            raise ValueError("dataset version must point to a registered parquet file")
        actual_fields = pq.ParquetFile(path).schema_arrow.names
        missing_metadata = sorted(set(definition["input_fields"]) - set(fields))
        missing_actual = sorted(set(definition["input_fields"]) - set(actual_fields))
        if missing_metadata or missing_actual:
            missing = sorted(set(missing_metadata) | set(missing_actual))
            raise ValueError(f"factor input field does not exist: {', '.join(missing)}")
        return row, list(actual_fields), path

    def _validate_lineage(
        self,
        definition: dict[str, Any],
        connection: sqlite3.Connection,
        *,
        require_published: bool,
    ) -> None:
        pit = definition["pit_lineage"]
        if not isinstance(pit.get("rule"), str) or not pit["rule"].strip():
            raise ValueError("PIT lineage rule is required")
        if not isinstance(pit.get("snapshot"), str) or not pit["snapshot"].strip():
            raise ValueError("PIT lineage snapshot is required")
        current_key = self._reference_key(definition["entity_id"], definition["version_id"])
        graph: dict[str, list[str]] = {}
        for row in connection.execute("SELECT entity_id, version_id, upstream_factor_versions_json FROM factor_versions"):
            graph[self._reference_key(row["entity_id"], row["version_id"])] = [
                self._reference_key(item["entity_id"], item["version_id"])
                for item in json.loads(row["upstream_factor_versions_json"] or "[]")
            ]
        upstream_keys = [self._reference_key(item["entity_id"], item["version_id"]) for item in definition["upstream_factor_versions"]]
        graph[current_key] = upstream_keys
        for item, upstream_key in zip(definition["upstream_factor_versions"], upstream_keys, strict=True):
            if upstream_key == current_key:
                raise ValueError("circular upstream lineage is not allowed")
            row = connection.execute(
                "SELECT f.status AS factor_status, fv.status FROM factor_versions fv "
                "JOIN factors f ON f.entity_id=fv.entity_id WHERE fv.entity_id=? AND fv.version_id=?",
                (item["entity_id"], item["version_id"]),
            ).fetchone()
            if row is None:
                raise ValueError("upstream FactorVersion does not exist")
            if require_published and (row["factor_status"] != "published" or row["status"] != "published"):
                raise ValueError("upstream FactorVersion must be published")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("circular upstream lineage is not allowed")
            if node in visited:
                return
            visiting.add(node)
            for child in graph.get(node, []):
                visit(child)
            visiting.remove(node)
            visited.add(node)

        visit(current_key)

    def _validate_definition(
        self,
        definition: dict[str, Any],
        connection: sqlite3.Connection,
        *,
        require_published_dataset: bool,
        require_published_upstream: bool,
        require_dataset_quality_passed: bool = True,
    ) -> None:
        leaked = sorted(set(definition["input_fields"]) & TARGET_ONLY_FIELDS)
        if leaked:
            raise ValueError(f"{', '.join(leaked)} is target-only and cannot be a factor input")
        dataset, _, _ = self._dataset_info(definition, connection)
        if require_published_dataset and (
            dataset["dataset_status"] != "published" or dataset["status"] != "published"
        ):
            raise ValueError("published dataset version is required")
        if (
            require_published_dataset
            and require_dataset_quality_passed
            and dataset["quality_status"] != "passed"
        ):
            raise ValueError("dataset quality gate failed")
        self._validate_lineage(definition, connection, require_published=require_published_upstream)
        if definition.get("generation_run_id"):
            run = connection.execute(
                "SELECT run_id FROM run_registry WHERE run_id=?",
                (definition["generation_run_id"],),
            ).fetchone()
            if run is None:
                raise ValueError("generation run does not exist")
        if definition.get("artifact_id"):
            artifact = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id=?",
                (definition["artifact_id"],),
            ).fetchone()
            if artifact is None:
                raise ValueError("lineage artifact does not exist")

    @staticmethod
    def _footer_stats(path: Path, field: str) -> dict[str, Any]:
        parquet = pq.ParquetFile(path)
        names = parquet.schema_arrow.names
        if field not in names:
            raise ValueError(f"factor field does not exist: {field}")
        index = names.index(field)
        total = 0
        missing: int | None = 0
        minimum: float | None = None
        maximum: float | None = None
        for row_group_index in range(parquet.metadata.num_row_groups):
            group = parquet.metadata.row_group(row_group_index)
            total += group.num_rows
            statistics = group.column(index).statistics
            if statistics is None:
                missing = None
                continue
            if statistics.null_count is None:
                missing = None
            elif missing is not None:
                missing += statistics.null_count
            if statistics.has_min_max:
                minimum = statistics.min if minimum is None else min(minimum, statistics.min)
                maximum = statistics.max if maximum is None else max(maximum, statistics.max)
        return {
            "row_count": total,
            "missing_rows": missing,
            "coverage": None if missing is None or total == 0 else (total - missing) / total,
            "min": minimum,
            "max": maximum,
        }

    def _public(self, row: sqlite3.Row, *, stats: dict[str, Any] | None = None) -> dict[str, Any]:
        definition = self._row_to_definition(row)
        if stats is None:
            stats = {}
        quality = json.loads(row["quality_json"] or "{}")
        return {
            **definition,
            **asset_class_fields(definition.get("asset_class")),
            "factor_entity_id": row["entity_id"],
            "factor_version_id": row["version_id"],
            "factor_status": row["factor_status"],
            "status": row["status"],
            "quality_status": row["quality_status"],
            "dataset_quality_status": row["dataset_quality_status"],
            "last_verified_at": row["last_verified_at"],
            "quality": quality,
            "row_count": stats.get("row_count", row["row_count"]),
            "missing_rows": stats.get("missing_rows"),
            "coverage": stats.get("coverage"),
            "min": stats.get("min"),
            "max": stats.get("max"),
            "dataset_date_min": row["date_min"],
            "dataset_date_max": row["date_max"],
            "detail_url": f"/factors/{row['entity_id']}/versions/{row['version_id']}",
        }

    def get(self, entity_id: str, version_id: str) -> dict[str, Any] | None:
        row = self._row(entity_id, version_id)
        if row is None:
            return None
        try:
            stats = self._footer_stats(self.settings.require_read_path(row["path"]), row["input_fields_json"] and json.loads(row["input_fields_json"])[0])
        except (ValueError, IndexError, TypeError):
            stats = None
        return self._public(row, stats=stats)

    def list(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        asset_class: str | None = None,
        source: str | None = None,
        lifecycle: str | None = None,
        quality: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if lifecycle is not None and lifecycle not in LIFECYCLE_STATUSES:
            raise ValueError("lifecycle is invalid")
        if quality is not None and quality not in QUALITY_STATUSES:
            raise ValueError("quality is invalid")
        conditions: list[str] = []
        params: list[str] = []
        if query:
            conditions.append("(f.name LIKE ? OR f.entity_id LIKE ? OR fv.formula LIKE ?)")
            needle = f"%{query.strip()}%"
            params.extend([needle, needle, needle])
        if asset_class:
            asset_class = normalize_asset_class(asset_class)
        for column, value in (("f.category", category), ("f.asset_class", asset_class), ("fv.source", source), ("fv.status", lifecycle), ("fv.quality_status", quality)):
            if value:
                conditions.append(f"{column}=?")
                params.append(value)
        conditions.append(
            "(fv.status = 'published' OR fv.origin IN ('manual', 'import') "
            "OR json_extract(fv.quality_json, '$.enabled_from_task') = 1)"
        )
        where = f"WHERE {' AND '.join(conditions)}"
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id {where}",
                params,
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT f.entity_id, f.name, f.category, f.asset_class, f.status AS factor_status, "
                "fv.version_id, fv.dataset_id, fv.dataset_version_id, fv.formula, fv.input_fields_json, "
                "fv.source, fv.direction, fv.frequency, fv.missing_policy, fv.pit_policy, fv.pit_lineage_json, "
                "fv.upstream_factor_versions_json, fv.origin, fv.author, fv.code_hash, fv.generation_run_id, fv.artifact_id, "
                "fv.last_verified_at, fv.quality_json, fv.status, "
                "fv.quality_status, dv.path, dv.fields_json, dv.row_count, dv.date_min, dv.date_max, "
                "dv.quality_status AS dataset_quality_status "
                f"FROM factor_versions fv JOIN factors f ON f.entity_id=fv.entity_id "
                "JOIN dataset_versions dv ON dv.entity_id=fv.dataset_id AND dv.version_id=fv.dataset_version_id "
                f"{where} ORDER BY f.entity_id, fv.version_id LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                field = json.loads(row["input_fields_json"] or "[]")[0]
                stats = self._footer_stats(self.settings.require_read_path(row["path"]), field)
            except (ValueError, IndexError, TypeError):
                stats = None
            items.append(self._public(row, stats=stats))
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        }

    def import_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(definition)
        with self.database.transaction() as connection:
            if self._row(normalized["entity_id"], normalized["version_id"], connection) is not None:
                raise ValueError("FactorVersion already exists")
            self._validate_definition(
                normalized,
                connection,
                require_published_dataset=False,
                require_published_upstream=False,
            )
            connection.execute(
                "INSERT INTO factors(entity_id, name, category, asset_class, status) VALUES (?, ?, ?, ?, 'draft') "
                "ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name, category=excluded.category, asset_class=excluded.asset_class",
                (normalized["entity_id"], normalized["name"], normalized["category"], normalized["asset_class"]),
            )
            connection.execute(
                "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, "
                "source, direction, frequency, missing_policy, pit_policy, pit_lineage_json, upstream_factor_versions_json, "
                "origin, author, code_hash, generation_run_id, artifact_id, quality_status, quality_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')",
                (
                    normalized["entity_id"], normalized["version_id"], normalized["dataset_id"], normalized["dataset_version_id"],
                    normalized["formula"], _json(normalized["input_fields"]), normalized["source"], normalized["direction"],
                    normalized["frequency"], normalized["missing_policy"], normalized["pit_policy"], _json(normalized["pit_lineage"]),
                    _json(normalized["upstream_factor_versions"]), normalized["origin"], normalized["author"], normalized["code_hash"],
                normalized["generation_run_id"], normalized["artifact_id"], normalized["quality_status"], _json(normalized["quality"]),
                ),
            )
        return self.get(normalized["entity_id"], normalized["version_id"]) or {}

    def update_draft(self, entity_id: str, version_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        row = self._row(entity_id, version_id)
        if row is None:
            raise ValueError("FactorVersion not found")
        if row["status"] != "draft":
            raise ValueError("FactorVersion is immutable")
        extra = sorted(set(patch) - _DEFINITION_FIELDS - {"entity_id", "version_id"})
        if extra:
            raise ValueError(f"additional fields are not allowed: {', '.join(extra)}")
        definition = self._row_to_definition(row)
        definition.update(patch)
        definition["entity_id"] = entity_id
        definition["version_id"] = version_id
        normalized = self._normalize(definition, default_origin=definition["origin"])
        with self.database.transaction() as connection:
            self._validate_definition(normalized, connection, require_published_dataset=False, require_published_upstream=False)
            connection.execute(
                "UPDATE factors SET name=?, category=?, asset_class=? WHERE entity_id=?",
                (normalized["name"], normalized["category"], normalized["asset_class"], entity_id),
            )
            connection.execute(
                "UPDATE factor_versions SET dataset_id=?, dataset_version_id=?, formula=?, input_fields_json=?, source=?, direction=?, "
                "frequency=?, missing_policy=?, pit_policy=?, pit_lineage_json=?, upstream_factor_versions_json=?, "
                "author=?, code_hash=?, generation_run_id=?, artifact_id=?, quality_status=?, quality_json=? WHERE entity_id=? AND version_id=?",
                (
                    normalized["dataset_id"], normalized["dataset_version_id"], normalized["formula"], _json(normalized["input_fields"]),
                    normalized["source"], normalized["direction"], normalized["frequency"], normalized["missing_policy"],
                    normalized["pit_policy"], _json(normalized["pit_lineage"]), _json(normalized["upstream_factor_versions"]),
                    normalized["author"], normalized["code_hash"], normalized["generation_run_id"], normalized["artifact_id"],
                    normalized["quality_status"], _json(normalized["quality"]), entity_id, version_id,
                ),
            )
        return self.get(entity_id, version_id) or {}

    @staticmethod
    def _action_items(items: Any, action: str) -> list[tuple[str, str]]:
        if not isinstance(items, list) or not items:
            raise ValueError(f"{action} items are required")
        if len(items) > MAX_ACTION_ITEMS:
            raise ValueError(f"{action} supports at most {MAX_ACTION_ITEMS} items")
        references: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{action} items must be objects")
            entity_id = str(item.get("entity_id", item.get("factor_id", ""))).strip()
            version_id = str(item.get("version_id", "")).strip()
            if entity_id in TARGET_ONLY_FIELDS:
                raise ValueError(f"{entity_id} is target-only and cannot be a factor")
            if not entity_id or not version_id:
                raise ValueError(f"{action} items require entity_id and version_id")
            reference = (entity_id, version_id)
            if reference in references:
                raise ValueError(f"duplicate FactorVersion reference: {entity_id}:{version_id}")
            references.append(reference)
        return references

    def _publish_in_transaction(
        self,
        entity_id: str,
        version_id: str,
        connection: sqlite3.Connection,
        *,
        require_dataset_quality_passed: bool = True,
    ) -> dict[str, Any]:
        row = self._row(entity_id, version_id, connection)
        if row is None:
            raise ValueError("FactorVersion not found")
        if row["quality_status"] != "passed":
            raise ValueError("quality gate requires quality_status=passed")
        if row["status"] not in {"draft", "validated"}:
            raise ValueError("only draft or validated FactorVersion can be published")
        self._validate_definition(
            self._row_to_definition(row),
            connection,
            require_published_dataset=True,
            require_published_upstream=True,
            require_dataset_quality_passed=require_dataset_quality_passed,
        )
        timestamp = _now()
        version_status = row["status"]
        if version_status == "draft":
            next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
            connection.execute(
                "UPDATE factor_versions SET status=?, last_verified_at=? WHERE entity_id=? AND version_id=?",
                (next_status, timestamp, entity_id, version_id),
            )
            version_status = next_status
        if version_status == "validated":
            next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
            connection.execute(
                "UPDATE factor_versions SET status=?, last_verified_at=? WHERE entity_id=? AND version_id=?",
                (next_status, timestamp, entity_id, version_id),
            )
        factor_status_row = connection.execute("SELECT status FROM factors WHERE entity_id=?", (entity_id,)).fetchone()
        if factor_status_row is None:
            raise ValueError("Factor not found")
        factor_status = factor_status_row[0]
        if factor_status == "draft":
            next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
            connection.execute("UPDATE factors SET status=? WHERE entity_id=?", (next_status, entity_id))
            factor_status = next_status
        if factor_status == "validated":
            next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
            connection.execute("UPDATE factors SET status=? WHERE entity_id=?", (next_status, entity_id))
        updated = self._row(entity_id, version_id, connection)
        return self._public(updated) if updated is not None else {}

    def rename(self, entity_id: str, name: str) -> dict[str, Any]:
        name = self._require_text(name, "name")
        with self.database.transaction() as connection:
            resolved = self._resolve_entity_id(entity_id, connection)
            if resolved is None:
                raise ValueError("找不到这个因子。")
            connection.execute(
                "UPDATE factors SET name=? WHERE entity_id=?", (name, resolved)
            )
        return {"entity_id": resolved, "name": name}

    def publish(self, entity_id: str, version_id: str) -> dict[str, Any]:
        return self.publish_many([{"entity_id": entity_id, "version_id": version_id}])["items"][0]

    def publish_verified_pack(self, entity_id: str, version_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            return self._publish_in_transaction(
                entity_id,
                version_id,
                connection,
                require_dataset_quality_passed=False,
            )

    def publish_many(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        references = self._action_items(items, "publish")
        with self.database.transaction() as connection:
            results = [self._publish_in_transaction(entity_id, version_id, connection) for entity_id, version_id in references]
        return {"items": results, "count": len(results)}

    def _deprecate_in_transaction(self, entity_id: str, version_id: str, connection: sqlite3.Connection) -> dict[str, Any]:
        row = self._row(entity_id, version_id, connection)
        if row is None:
            raise ValueError("FactorVersion not found")
        if row["status"] != "published":
            raise ValueError("only published FactorVersion can be deprecated")
        next_status = transition(LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED).value
        connection.execute(
            "UPDATE factor_versions SET status=? WHERE entity_id=? AND version_id=?",
            (next_status, entity_id, version_id),
        )
        updated = self._row(entity_id, version_id, connection)
        return self._public(updated) if updated is not None else {}

    def deprecate(self, entity_id: str, version_id: str) -> dict[str, Any]:
        return self.deprecate_many([{"entity_id": entity_id, "version_id": version_id}])["items"][0]

    def deprecate_many(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        references = self._action_items(items, "deprecate")
        with self.database.transaction() as connection:
            results = [self._deprecate_in_transaction(entity_id, version_id, connection) for entity_id, version_id in references]
        return {"items": results, "count": len(results)}

    def _reference_reason(self, entity_id: str, version_id: str, connection: sqlite3.Connection) -> str | None:
        for candidate in self._entity_id_candidates(entity_id):
            if connection.execute(
                "SELECT 1 FROM strategy_factor_versions WHERE factor_entity_id=? AND factor_version_id=? LIMIT 1",
                (candidate, version_id),
            ).fetchone() is not None:
                return "这个因子已经被策略用上了，不能删。"
            if connection.execute(
                "SELECT 1 FROM model_factor_versions WHERE factor_entity_id=? AND factor_version_id=? LIMIT 1",
                (candidate, version_id),
            ).fetchone() is not None:
                return "这个因子已经被模型用上了，不能删。"
        needle = self._reference_key(entity_id, version_id)
        for row in connection.execute("SELECT name, config_json FROM research_runs"):
            try:
                config = json.loads(row["config_json"] or "{}")
            except json.JSONDecodeError:
                continue
            refs = config.get("factor_versions", []) if isinstance(config, dict) else []
            matched = any(
                isinstance(item, dict)
                and self._reference_key(
                    str(item.get("factor_id", item.get("entity_id", item.get("factor_entity_id", "")))),
                    str(item.get("version_id", item.get("factor_version_id", ""))),
                ) == needle
                for item in refs
            )
            if not matched or self._is_diagnostic_research_run(row["name"], config):
                continue
            run_name = str(row["name"] or "").strip() or "未命名任务"
            return f"这个因子还在研究任务「{run_name}」里，不能删。"
        return None

    def _referenced(self, entity_id: str, version_id: str, connection: sqlite3.Connection) -> bool:
        return self._reference_reason(entity_id, version_id, connection) is not None

    def delete(self, entity_id: str, version_id: str) -> None:
        with self.database.transaction() as connection:
            resolved = self._resolve_entity_id(entity_id, connection)
            if resolved is None or self._row(resolved, version_id, connection) is None:
                raise ValueError("找不到这个因子。")
            if self._is_builtin_factor(resolved):
                raise ValueError("系统自带的行情因子不能删除。")
            reason = self._reference_reason(resolved, version_id, connection)
            if reason:
                raise ValueError(reason)
            try:
                connection.execute(
                    "DELETE FROM factor_diagnostics WHERE entity_id=? AND version_id=?",
                    (resolved, version_id),
                )
                connection.execute(
                    "DELETE FROM factor_calculation_runs WHERE factor_entity_id=? AND factor_version_id=?",
                    (resolved, version_id),
                )
                connection.execute(
                    "DELETE FROM factor_versions WHERE entity_id=? AND version_id=?",
                    (resolved, version_id),
                )
                remaining = connection.execute(
                    "SELECT 1 FROM factor_versions WHERE entity_id=? LIMIT 1",
                    (resolved,),
                ).fetchone()
                if remaining is None:
                    connection.execute("DELETE FROM factors WHERE entity_id=?", (resolved,))
            except sqlite3.IntegrityError as error:
                raise ValueError("这个因子还被其他记录占用，不能删。") from error

    def diagnose(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(items, list) or not items:
            raise ValueError("diagnose items are required")
        if len(items) > MAX_DIAGNOSE_ITEMS:
            raise ValueError(f"diagnose supports at most {MAX_DIAGNOSE_ITEMS} items")
        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("diagnose items must be objects")
            entity_id = str(item.get("entity_id", item.get("factor_id", ""))).strip()
            version_id = str(item.get("version_id", "")).strip()
            if entity_id in TARGET_ONLY_FIELDS:
                raise ValueError(f"{entity_id} is target-only and cannot be diagnosed as a factor")
            record = self.get(entity_id, version_id)
            if record is None:
                raise ValueError("FactorVersion not found")
            stats = self._footer_stats(
                self.settings.require_read_path(self._row(entity_id, version_id)["path"]),
                record["input_fields"][0],
            )
            quality = {
                "coverage": stats["coverage"],
                "missing_rows": stats["missing_rows"],
                "row_count": stats["row_count"],
                "min": stats["min"],
                "max": stats["max"],
            }
            results.append({**record, **quality, "factor_entity_id": entity_id, "factor_version_id": version_id})
        return {"items": results, "count": len(results)}
