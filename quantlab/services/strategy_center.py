"""Versioned model and strategy registry for the local research platform."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantlab.config import Settings
from quantlab.domain.status import LifecycleStatus, RunStatus, transition
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.run_identity import RunIdentity


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode()).hexdigest()


class StrategyCenterService:
    def __init__(self, settings: Settings, database: Database, factors: FactorRepository) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.artifacts = ArtifactRepository(settings, database)
        self.identity = RunIdentity(database)

    def _published_dataset(self, connection: sqlite3.Connection, dataset_id: str, version_id: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM datasets d JOIN dataset_versions dv ON dv.entity_id=d.entity_id "
            "AND dv.version_id=? WHERE d.entity_id=? AND d.status='published' "
            "AND dv.status='published' AND dv.quality_status IN ('passed','warning','needs_review')",
            (version_id, dataset_id),
        ).fetchone()
        return row is not None

    def _validate_factor_refs(self, connection: sqlite3.Connection, refs: Any, dataset_id: str, dataset_version_id: str) -> list[dict[str, Any]]:
        if refs in (None, []):
            return []
        if not isinstance(refs, list):
            raise ValueError("factor_versions must be a list")
        if not refs:
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in refs:
            if not isinstance(item, dict):
                raise ValueError("factor_versions must contain objects")
            factor_id, version_id = str(item.get("factor_id", "")).strip(), str(item.get("version_id", "")).strip()
            if not factor_id or not version_id or (factor_id, version_id) in seen:
                raise ValueError("factor_versions must contain unique exact references")
            row = connection.execute(
                "SELECT f.status factor_status, fv.status, fv.dataset_id, fv.dataset_version_id "
                "FROM factors f JOIN factor_versions fv ON fv.entity_id=f.entity_id "
                "WHERE f.entity_id=? AND fv.version_id=?",
                (factor_id, version_id),
            ).fetchone()
            if row is None or row["factor_status"] != "published" or row["status"] != "published":
                raise ValueError(f"published factor version is required: {factor_id}:{version_id}")
            if row["dataset_id"] != dataset_id or row["dataset_version_id"] != dataset_version_id:
                raise ValueError("all factor versions must lock the research dataset version")
            seen.add((factor_id, version_id))
            normalized.append({"factor_id": factor_id, "version_id": version_id, "direction": str(item.get("direction", "positive"))})
        return normalized

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_model(self, entity_id: str, name: str) -> dict[str, Any]:
        if not entity_id.strip() or not name.strip():
            raise ValueError("model entity_id and name are required")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO models(entity_id, name, status) VALUES (?, ?, 'draft')", (entity_id, name.strip()))
        return self.get_model(entity_id) or {}

    def create_model_version(self, entity_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("model version config must be an object")
        dataset_id, dataset_version_id = str(config.get("dataset_id", "")), str(config.get("dataset_version_id", ""))
        if not dataset_id or not dataset_version_id:
            raise ValueError("dataset_id and dataset_version_id are required")
        version_id = str(config.get("version_id", ""))
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM models WHERE entity_id=?", (entity_id,)).fetchone() is None:
                raise ValueError("model not found")
            if not self._published_dataset(connection, dataset_id, dataset_version_id):
                raise ValueError("published dataset version is required")
            refs = self._validate_factor_refs(connection, config.get("factor_versions"), dataset_id, dataset_version_id)
            if not version_id:
                count = connection.execute("SELECT COUNT(*) FROM model_versions WHERE entity_id=?", (entity_id,)).fetchone()[0]
                version_id = f"v{count + 1}"
            if connection.execute("SELECT 1 FROM model_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone():
                raise ValueError("ModelVersion already exists")
            normalized = {**config, "dataset_id": dataset_id, "dataset_version_id": dataset_version_id, "factor_versions": refs}
            connection.execute(
                "INSERT INTO model_versions(entity_id, version_id, dataset_id, dataset_version_id, parameters_json, status, quality_status, content_hash) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
                (entity_id, version_id, dataset_id, dataset_version_id, _json(normalized), str(config.get("quality_status", "passed")), _hash(normalized)),
            )
            for ref in refs:
                connection.execute("INSERT INTO model_factor_versions(model_entity_id, model_version_id, factor_entity_id, factor_version_id) VALUES (?, ?, ?, ?)", (entity_id, version_id, ref["factor_id"], ref["version_id"]))
        return self.get_model_version(entity_id, version_id) or {}

    def create_training_run(self, model_entity_id: str, model_version_id: str, config: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM model_versions WHERE entity_id=? AND version_id=?", (model_entity_id, model_version_id)).fetchone() is None:
                raise ValueError("model version not found")
        run_id = self.identity.next_id()
        self.database.register_run(run_id, "model_training")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO model_training_runs(run_id, status, model_entity_id, model_version_id, dataset_id, dataset_version_id, config_json) VALUES (?, 'queued', ?, ?, ?, ?, ?)", (run_id, model_entity_id, model_version_id, config.get("dataset_id"), config.get("dataset_version_id"), _json(config)))
        return self.get_training_run(run_id) or {}

    def transition_training_run(self, run_id: str, status: str) -> dict[str, Any]:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("invalid training run status")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM model_training_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("training run not found")
            current = row["status"]
            next_status = transition(RunStatus(current), RunStatus(status)).value
            connection.execute("UPDATE model_training_runs SET status=? WHERE run_id=?", (next_status, run_id))
            if status in {"completed", "failed"}:
                connection.execute("UPDATE run_registry SET finished_at=CURRENT_TIMESTAMP WHERE run_id=?", (run_id,))
        return self.get_training_run(run_id) or {}

    def attach_training_artifact(self, run_id: str, path: Path | str, display_name: str) -> dict[str, Any]:
        return self.artifacts.register(run_id=run_id, path=path, display_name=display_name, artifact_role="model") .__dict__

    def publish_model_version(self, entity_id: str, version_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status, quality_status FROM model_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone()
            if row is None:
                raise ValueError("model version not found")
            if row["status"] != "draft":
                raise ValueError("ModelVersion is immutable")
            if row["quality_status"] != "passed":
                raise ValueError("model version quality gate failed")
            next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
            connection.execute("UPDATE model_versions SET status=? WHERE entity_id=? AND version_id=?", (next_status, entity_id, version_id))
            next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
            connection.execute("UPDATE model_versions SET status=? WHERE entity_id=? AND version_id=?", (next_status, entity_id, version_id))
            parent_row = connection.execute("SELECT status FROM models WHERE entity_id=?", (entity_id,)).fetchone()
            if parent_row is not None:
                parent_status = parent_row["status"]
                if parent_status == "draft":
                    next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
                    connection.execute("UPDATE models SET status=? WHERE entity_id=?", (next_status, entity_id))
                    parent_status = next_status
                if parent_status == "validated":
                    next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
                    connection.execute("UPDATE models SET status=? WHERE entity_id=?", (next_status, entity_id))
        return self.get_model_version(entity_id, version_id) or {}

    def update_model_version(self, entity_id: str, version_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT status, parameters_json FROM model_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone()
        if row is None:
            raise ValueError("model version not found")
        if row["status"] != "draft":
            raise ValueError("ModelVersion is immutable")
        value = json.loads(row["parameters_json"] or "{}")
        value.update(patch)
        with self.database.transaction() as connection:
            connection.execute("UPDATE model_versions SET parameters_json=?, content_hash=? WHERE entity_id=? AND version_id=?", (_json(value), _hash(value), entity_id, version_id))
        return self.get_model_version(entity_id, version_id) or {}

    def create_strategy(self, entity_id: str, name: str) -> dict[str, Any]:
        if not entity_id.strip() or not name.strip():
            raise ValueError("strategy entity_id and name are required")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES (?, ?, 'draft')", (entity_id, name.strip()))
        return self.get_strategy(entity_id) or {}

    def create_strategy_version(self, entity_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("strategy version config must be an object")
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM strategies WHERE entity_id=?", (entity_id,)).fetchone() is None:
                raise ValueError("strategy not found")
            model_entity_id, model_version_id = config.get("model_entity_id"), config.get("model_version_id")
            if bool(model_entity_id) != bool(model_version_id):
                raise ValueError("model_entity_id and model_version_id must be provided together")
            if model_entity_id and connection.execute("SELECT 1 FROM model_versions WHERE entity_id=? AND version_id=? AND status='published'", (model_entity_id, model_version_id)).fetchone() is None:
                raise ValueError("published model version is required")
            refs = self._validate_factor_refs(connection, config.get("factor_versions"), str(config.get("dataset_id", "ds_canonical_market")), str(config.get("dataset_version_id", "current"))) if config.get("factor_versions") else []
            version_id = str(config.get("version_id", "")) or f"v{connection.execute('SELECT COUNT(*) FROM strategy_versions WHERE entity_id=?', (entity_id,)).fetchone()[0] + 1}"
            normalized = {**config, "factor_versions": refs}
            connection.execute("INSERT INTO strategy_versions(entity_id, version_id, model_entity_id, model_version_id, parameters_json, status, quality_status, content_hash) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)", (entity_id, version_id, model_entity_id, model_version_id, _json(normalized), str(config.get("quality_status", "passed")), _hash(normalized)))
            for ref in refs:
                connection.execute("INSERT INTO strategy_factor_versions(strategy_entity_id, strategy_version_id, factor_entity_id, factor_version_id) VALUES (?, ?, ?, ?)", (entity_id, version_id, ref["factor_id"], ref["version_id"]))
        return self.get_strategy_version(entity_id, version_id) or {}

    def publish_strategy_version(self, entity_id: str, version_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status, quality_status FROM strategy_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone()
            if row is None:
                raise ValueError("strategy version not found")
            if row["status"] != "draft" or row["quality_status"] != "passed":
                raise ValueError("strategy version cannot be published")
            next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
            connection.execute("UPDATE strategy_versions SET status=? WHERE entity_id=? AND version_id=?", (next_status, entity_id, version_id))
            next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
            connection.execute("UPDATE strategy_versions SET status=? WHERE entity_id=? AND version_id=?", (next_status, entity_id, version_id))
            parent_row = connection.execute("SELECT status FROM strategies WHERE entity_id=?", (entity_id,)).fetchone()
            if parent_row is not None:
                parent_status = parent_row["status"]
                if parent_status == "draft":
                    next_status = transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED).value
                    connection.execute("UPDATE strategies SET status=? WHERE entity_id=?", (next_status, entity_id))
                    parent_status = next_status
                if parent_status == "validated":
                    next_status = transition(LifecycleStatus.VALIDATED, LifecycleStatus.PUBLISHED).value
                    connection.execute("UPDATE strategies SET status=? WHERE entity_id=?", (next_status, entity_id))
        return self.get_strategy_version(entity_id, version_id) or {}

    def copy_strategy_version(self, entity_id: str, version_id: str) -> dict[str, Any]:
        source = self.get_strategy_version(entity_id, version_id)
        if source is None:
            raise ValueError("strategy version not found")
        config = dict(source["config"])
        config.pop("version_id", None)
        return self.create_strategy_version(entity_id, config)

    def diff_strategy_versions(self, entity_id: str, left: str, right: str) -> dict[str, Any]:
        a, b = self.get_strategy_version(entity_id, left), self.get_strategy_version(entity_id, right)
        if a is None or b is None:
            raise ValueError("strategy version not found")
        keys = sorted(set(a["config"]) | set(b["config"]))
        return {"left": left, "right": right, "changed": [key for key in keys if a["config"].get(key) != b["config"].get(key)], "values": {key: {"left": a["config"].get(key), "right": b["config"].get(key)} for key in keys if a["config"].get(key) != b["config"].get(key)}}

    def to_backtest_draft(self, entity_id: str, version_id: str) -> dict[str, Any]:
        strategy = self.get_strategy_version(entity_id, version_id)
        if strategy is None:
            raise ValueError("strategy version not found")
        draft_id = f"draft-{self.identity.next_id()}"
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO backtest_drafts(draft_id, revision, factor_version_ids_json, strategy_entity_id, strategy_version_id, config_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?)", (draft_id, _json(strategy["factor_versions"]), entity_id, version_id, _json(strategy["config"]), now, now))
        return {"draft_id": draft_id, "revision": 1, "strategy_version_id": f"{entity_id}:{version_id}", "factor_versions": strategy["factor_versions"], "config": strategy["config"]}

    def get_model(self, entity_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT entity_id, name, status FROM models WHERE entity_id=?", (entity_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "versions": self.list_model_versions(entity_id)}

    def list_models(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT entity_id, name, status FROM models ORDER BY entity_id").fetchall()
        return {"items": [{**dict(row), "versions": self.list_model_versions(row["entity_id"])} for row in rows]}

    def list_model_versions(self, entity_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            ids = connection.execute("SELECT version_id FROM model_versions WHERE entity_id=? ORDER BY version_id", (entity_id,)).fetchall()
        return [self.get_model_version(entity_id, row["version_id"]) for row in ids]

    def get_model_version(self, entity_id: str, version_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM model_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone()
            if not row:
                return None
            refs = connection.execute("SELECT factor_entity_id factor_id, factor_version_id version_id FROM model_factor_versions WHERE model_entity_id=? AND model_version_id=? ORDER BY rowid", (entity_id, version_id)).fetchall()
        return {"entity_id": entity_id, "version_id": version_id, "dataset_id": row["dataset_id"], "dataset_version_id": row["dataset_version_id"], "factor_versions": [dict(ref) for ref in refs], "config": json.loads(row["parameters_json"] or "{}"), "status": row["status"], "quality_status": row["quality_status"], "content_hash": row["content_hash"]}

    def get_training_run(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM model_training_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {"run_id": run_id, "status": row["status"], "model_entity_id": row["model_entity_id"], "model_version_id": row["model_version_id"], "config": json.loads(row["config_json"] or "{}")}

    def list_training_runs(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            ids = connection.execute("SELECT run_id FROM model_training_runs ORDER BY run_id DESC").fetchall()
        return [self.get_training_run(row["run_id"]) for row in ids]

    def get_strategy(self, entity_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT entity_id, name, status FROM strategies WHERE entity_id=?", (entity_id,)).fetchone()
        return {**dict(row), "versions": self.list_strategy_versions(entity_id)} if row else None

    def list_strategies(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT entity_id, name, status FROM strategies ORDER BY entity_id").fetchall()
        return {"items": [{**dict(row), "versions": self.list_strategy_versions(row["entity_id"])} for row in rows]}

    def list_strategy_versions(self, entity_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            ids = connection.execute("SELECT version_id FROM strategy_versions WHERE entity_id=? ORDER BY version_id", (entity_id,)).fetchall()
        return [self.get_strategy_version(entity_id, row["version_id"]) for row in ids]

    def get_strategy_version(self, entity_id: str, version_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM strategy_versions WHERE entity_id=? AND version_id=?", (entity_id, version_id)).fetchone()
            if not row:
                return None
            refs = connection.execute("SELECT factor_entity_id factor_id, factor_version_id version_id FROM strategy_factor_versions WHERE strategy_entity_id=? AND strategy_version_id=? ORDER BY rowid", (entity_id, version_id)).fetchall()
        config = json.loads(row["parameters_json"] or "{}")
        config["factor_versions"] = [dict(ref) for ref in refs]
        return {"entity_id": entity_id, "version_id": version_id, "model_entity_id": row["model_entity_id"], "model_version_id": row["model_version_id"], "factor_versions": config["factor_versions"], "config": config, "status": row["status"], "quality_status": row["quality_status"], "content_hash": row["content_hash"]}
