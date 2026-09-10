"""Export and import backtest run.json manifests for LAN file sync."""
from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.domain.status import BacktestRunStatus
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.repositories.run_lifecycle import transition_backtest_run_status
from quantlab.services.machine_identity import load_machine_identity

_ARTIFACT_FILES = (
    ("metrics", "metrics.json", "回测指标"),
    ("trades", "trades.json", "成交明细"),
    ("equity_curve", "equity_curve.json", "权益曲线"),
)
_DELETED_DIR_NAME = "_deleted"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_from_row(row: Any, key: str, fallback: str = "") -> str:
    try:
        return str(row[key] or fallback or "")
    except (KeyError, IndexError, TypeError):
        return fallback


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _machine_id_of(*parts: Any) -> str:
    for part in parts:
        if isinstance(part, dict):
            value = str(part.get("machine_id") or "").strip()
        else:
            value = str(part or "").strip()
        if value:
            return value
    return ""


def _row_token(row: Any) -> str:
    try:
        return str(row["submission_token"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def deleted_root(settings: Settings) -> Path:
    return Path(settings.runtime_root) / "results" / _DELETED_DIR_NAME


def list_deleted_run_ids(settings: Settings) -> set[str]:
    root = deleted_root(settings)
    if not root.is_dir():
        return set()
    found: set[str] = set()
    for path in root.glob("*.json"):
        try:
            found.add(validate_run_id(path.stem))
        except ValueError:
            continue
    return found


def write_deleted_marker(settings: Settings, run_id: str) -> Path:
    run_id = validate_run_id(run_id)
    results = settings.require_write_path(settings.runtime_root / "results")
    directory = results / _DELETED_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    path = settings.require_write_path(directory / f"{run_id}.json")
    if not path.is_file():
        payload = {
            "schema": 1,
            "run_id": run_id,
            "machine_id": str(load_machine_identity(settings.runtime_root).get("machine_id") or ""),
            "deleted_at": _utc_now(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _unlink_artifact_files(settings: Settings, paths: list[str]) -> None:
    for raw in paths:
        try:
            path = settings.require_write_path(raw)
        except ValueError:
            continue
        if path.is_file():
            path.unlink()


def _remove_run_folder(settings: Settings, run_id: str) -> None:
    folder = Path(settings.runtime_root) / "results" / run_id
    try:
        target = settings.require_write_path(folder)
    except ValueError:
        return
    results_root = (Path(settings.runtime_root) / "results").resolve()
    resolved = target.resolve()
    if resolved == results_root or results_root not in resolved.parents:
        return
    if resolved.name == _DELETED_DIR_NAME:
        return
    if target.is_dir():
        shutil.rmtree(target)


def purge_backtest_run(settings: Settings, database: Database, run_id: str, *, audit_action: str) -> bool:
    run_id = validate_run_id(run_id)
    with database.connect() as connection:
        row = connection.execute("SELECT 1 FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        artifact_rows = connection.execute("SELECT path FROM artifacts WHERE run_id=?", (run_id,)).fetchall()
    artifact_paths = [str(item["path"]) for item in artifact_rows]
    if row is None:
        _unlink_artifact_files(settings, artifact_paths)
        _remove_run_folder(settings, run_id)
        return False
    with database.transaction() as connection:
        connection.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
        connection.execute("DELETE FROM backtest_steps WHERE run_id=?", (run_id,))
        connection.execute("DELETE FROM backtest_model_versions WHERE backtest_run_id=?", (run_id,))
        connection.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
        connection.execute(
            "DELETE FROM run_registry WHERE run_id=? AND run_type='backtest'",
            (run_id,),
        )
        connection.execute(
            "INSERT INTO system_audit_logs(audit_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
            (
                f"audit-{secrets.token_hex(12)}",
                audit_action,
                json.dumps({"run_id": run_id}, ensure_ascii=False, sort_keys=True),
                _utc_now(),
            ),
        )
    _unlink_artifact_files(settings, artifact_paths)
    _remove_run_folder(settings, run_id)
    return True


def apply_deleted_markers(settings: Settings, database: Database) -> list[str]:
    removed: list[str] = []
    for run_id in sorted(list_deleted_run_ids(settings)):
        if purge_backtest_run(settings, database, run_id, audit_action="backtest_result_delete_sync"):
            removed.append(run_id)
        else:
            _remove_run_folder(settings, run_id)
    return removed


def write_run_manifest(settings: Settings, database: Database, run_id: str) -> Path | None:
    try:
        run_id = validate_run_id(run_id)
    except ValueError:
        return None
    if run_id in list_deleted_run_ids(settings):
        return None
    with database.connect() as connection:
        row = connection.execute(
            "SELECT br.run_id, br.status, br.config_json, br.metrics_json, br.error_message, "
            "br.dataset_id, br.dataset_version_id, br.strategy_entity_id, br.strategy_version_id, "
            "registry.created_at, registry.finished_at "
            "FROM backtest_runs br JOIN run_registry registry ON registry.run_id=br.run_id "
            "WHERE br.run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        steps = connection.execute(
            "SELECT ordinal, step_name, status, started_at, finished_at, error_message "
            "FROM backtest_steps WHERE run_id=? ORDER BY ordinal",
            (run_id,),
        ).fetchall()
    try:
        config = json.loads(row["config_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    machine = str(config.get("machine_id") or load_machine_identity(settings.runtime_root).get("machine_id") or "")
    if machine and not str(config.get("machine_id") or "").strip():
        config = {**config, "machine_id": machine}
    artifacts = []
    folder = settings.runtime_root / "results" / run_id
    for role, name, display in _ARTIFACT_FILES:
        if (folder / name).is_file():
            artifacts.append({"role": role, "name": name, "display_name": display})
    payload = {
        "schema": 1,
        "run_id": run_id,
        "machine_id": machine,
        "status": str(row["status"] or ""),
        "created_at": _now_from_row(row, "created_at"),
        "finished_at": _now_from_row(row, "finished_at"),
        "dataset_id": row["dataset_id"],
        "dataset_version_id": row["dataset_version_id"],
        "strategy_entity_id": row["strategy_entity_id"],
        "strategy_version_id": row["strategy_version_id"],
        "error_message": row["error_message"],
        "config": config,
        "steps": [dict(step) for step in steps],
        "artifacts": artifacts,
    }
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "run.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def backfill_missing_machine_ids(settings: Settings, database: Database) -> int:
    local = str(load_machine_identity(settings.runtime_root).get("machine_id") or "").strip()
    deleted = list_deleted_run_ids(settings)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, config_json, metrics_json, submission_token FROM backtest_runs"
        ).fetchall()
    updated = 0
    for row in rows:
        run_id = str(row["run_id"])
        if run_id in deleted:
            continue
        config = _json_object(row["config_json"])
        metrics = _json_object(row["metrics_json"])
        resources = metrics.get("resources") if isinstance(metrics.get("resources"), dict) else {}
        folder = settings.runtime_root / "results" / run_id
        manifest_path = folder / "run.json"
        manifest = _json_object(manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else {})
        manifest_config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
        known = _machine_id_of(manifest, manifest_config, config, resources)
        if not known:
            if _row_token(row).startswith("sync:") or not local:
                continue
            known = local
        db_changed = False
        if not _machine_id_of(config):
            config = {**config, "machine_id": known}
            db_changed = True
        if not _machine_id_of(resources):
            resources = {**resources, "machine_id": known}
            metrics = {**metrics, "resources": resources}
            db_changed = True
        if not db_changed:
            continue
        if db_changed:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE backtest_runs SET config_json=?, metrics_json=? WHERE run_id=?",
                    (
                        json.dumps(config, ensure_ascii=False),
                        json.dumps(metrics, ensure_ascii=False),
                        run_id,
                    ),
                )
        file_changed = False
        metrics_path = folder / "metrics.json"
        if metrics_path.is_file():
            disk_metrics = _load_metrics(folder)
            disk_resources = disk_metrics.get("resources") if isinstance(disk_metrics.get("resources"), dict) else {}
            if not _machine_id_of(disk_resources):
                disk_metrics = {**disk_metrics, "resources": {**disk_resources, "machine_id": known}}
                metrics_path.write_text(json.dumps(disk_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                file_changed = True
        need_manifest = (not manifest_path.is_file()) or (not _machine_id_of(manifest)) or (not _machine_id_of(manifest_config))
        if need_manifest and (db_changed or folder.is_dir() or manifest_path.is_file()):
            if write_run_manifest(settings, database, run_id) is not None:
                file_changed = True
        if db_changed or file_changed:
            updated += 1
    return updated


def export_missing_manifests(settings: Settings, database: Database) -> int:
    deleted = list_deleted_run_ids(settings)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id FROM backtest_runs WHERE status IN ('completed', 'failed')"
        ).fetchall()
    written = 0
    for row in rows:
        run_id = str(row["run_id"])
        if run_id in deleted:
            continue
        path = settings.runtime_root / "results" / run_id / "run.json"
        if path.is_file():
            continue
        if write_run_manifest(settings, database, run_id) is not None:
            written += 1
    return written


def _fk_pair(connection, table: str, entity_id: Any, version_id: Any) -> tuple[str | None, str | None]:
    entity = str(entity_id or "").strip()
    version = str(version_id or "").strip()
    if not entity or not version:
        return None, None
    found = connection.execute(
        f"SELECT 1 FROM {table} WHERE entity_id=? AND version_id=?",
        (entity, version),
    ).fetchone()
    if found is None:
        return None, None
    return entity, version


def _load_metrics(folder: Path) -> dict[str, Any]:
    path = folder / "metrics.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def import_result_manifests(settings: Settings, database: Database) -> list[str]:
    root = settings.runtime_root / "results"
    if not root.is_dir():
        return []
    deleted = list_deleted_run_ids(settings)
    imported: list[str] = []
    artifacts = ArtifactRepository(settings, database)
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_path = folder / "run.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        try:
            run_id = validate_run_id(str(manifest.get("run_id") or folder.name))
        except ValueError:
            continue
        if run_id in deleted:
            continue
        status = str(manifest.get("status") or "")
        if status not in {"completed", "failed"}:
            continue
        with database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM backtest_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if exists is not None:
            continue
        config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
        machine_id = str(manifest.get("machine_id") or config.get("machine_id") or "")
        if machine_id:
            config = {**config, "machine_id": machine_id}
        metrics = _load_metrics(folder)
        if machine_id:
            resources = metrics.get("resources") if isinstance(metrics.get("resources"), dict) else {}
            metrics = {**metrics, "resources": {**resources, "machine_id": machine_id}}
            if (folder / "metrics.json").is_file() and not _machine_id_of(resources):
                (folder / "metrics.json").write_text(
                    json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if not _machine_id_of(config) or not _machine_id_of(manifest):
                manifest = {**manifest, "machine_id": machine_id, "config": config}
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        created_at = str(manifest.get("created_at") or "").strip() or _utc_now()
        finished_at = str(manifest.get("finished_at") or "").strip() or None
        error_message = manifest.get("error_message")
        try:
            with database.transaction() as connection:
                dataset_id, dataset_version_id = _fk_pair(
                    connection,
                    "dataset_versions",
                    manifest.get("dataset_id") or config.get("dataset_id"),
                    manifest.get("dataset_version_id") or config.get("dataset_version_id"),
                )
                strategy_entity_id, strategy_version_id = _fk_pair(
                    connection,
                    "strategy_versions",
                    manifest.get("strategy_entity_id") or config.get("strategy_entity_id"),
                    manifest.get("strategy_version_id") or config.get("strategy_version_id"),
                )
                connection.execute(
                    "INSERT INTO run_registry(run_id, run_type, created_at, finished_at) VALUES (?, 'backtest', ?, ?)",
                    (run_id, created_at, finished_at),
                )
                connection.execute(
                    "INSERT INTO backtest_runs(run_id, status, strategy_entity_id, strategy_version_id, "
                    "dataset_id, dataset_version_id, config_json, metrics_json, error_message, submission_token) "
                    "VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        strategy_entity_id,
                        strategy_version_id,
                        dataset_id,
                        dataset_version_id,
                        json.dumps(config, ensure_ascii=False),
                        json.dumps(metrics, ensure_ascii=False),
                        error_message,
                        f"sync:{run_id}",
                    ),
                )
                next_status = transition_backtest_run_status(connection, run_id, BacktestRunStatus.RUNNING)
                connection.execute("UPDATE backtest_runs SET status=? WHERE run_id=?", (next_status, run_id))
                target = BacktestRunStatus.COMPLETED if status == "completed" else BacktestRunStatus.FAILED
                next_status = transition_backtest_run_status(connection, run_id, target)
                connection.execute(
                    "UPDATE backtest_runs SET status=?, error_message=? WHERE run_id=?",
                    (next_status, error_message, run_id),
                )
                steps = manifest.get("steps") if isinstance(manifest.get("steps"), list) else []
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO backtest_steps(run_id, ordinal, step_name, status, started_at, finished_at, error_message) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            run_id,
                            int(step.get("ordinal") or 0),
                            str(step.get("step_name") or ""),
                            str(step.get("status") or "completed"),
                            step.get("started_at"),
                            step.get("finished_at"),
                            step.get("error_message"),
                        ),
                    )
            listed = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
            for item in listed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                path = folder / name
                if not name or not path.is_file():
                    continue
                artifacts.register(
                    run_id=run_id,
                    path=path,
                    display_name=str(item.get("display_name") or name),
                    artifact_role=str(item.get("role") or "metrics"),
                )
        except Exception:
            continue
        imported.append(run_id)
    return imported


def sync_result_catalog(settings: Settings, database: Database) -> list[str]:
    apply_deleted_markers(settings, database)
    backfill_missing_machine_ids(settings, database)
    export_missing_manifests(settings, database)
    return import_result_manifests(settings, database)
