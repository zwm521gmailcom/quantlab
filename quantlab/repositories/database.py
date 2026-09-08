"""SQLite setup with explicit transactions and foreign-key enforcement."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from quantlab.domain.identifiers import validate_run_id


_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(run_registry)").fetchall()
            }
            if "finished_at" not in columns:
                connection.execute("ALTER TABLE run_registry ADD COLUMN finished_at TEXT")
            factor_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(factor_versions)").fetchall()
            }
            for column in (
                "source", "direction", "frequency", "missing_policy", "pit_policy"
            ):
                if column not in factor_columns:
                    connection.execute(
                        f"ALTER TABLE factor_versions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            factor_entity_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(factors)").fetchall()
            }
            if "category" not in factor_entity_columns:
                connection.execute(
                    "ALTER TABLE factors ADD COLUMN category TEXT NOT NULL DEFAULT '未分类'"
                )
            factor_version_migrations = {
                "pit_lineage_json": "ALTER TABLE factor_versions ADD COLUMN pit_lineage_json TEXT NOT NULL DEFAULT '{}'",
                "upstream_factor_versions_json": "ALTER TABLE factor_versions ADD COLUMN upstream_factor_versions_json TEXT NOT NULL DEFAULT '[]'",
                "origin": "ALTER TABLE factor_versions ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'",
                "author": "ALTER TABLE factor_versions ADD COLUMN author TEXT NOT NULL DEFAULT '未登记'",
                "last_verified_at": "ALTER TABLE factor_versions ADD COLUMN last_verified_at TEXT",
                "quality_json": "ALTER TABLE factor_versions ADD COLUMN quality_json TEXT NOT NULL DEFAULT '{}'",
                "code_hash": "ALTER TABLE factor_versions ADD COLUMN code_hash TEXT NOT NULL DEFAULT ''",
                "generation_run_id": "ALTER TABLE factor_versions ADD COLUMN generation_run_id TEXT NOT NULL DEFAULT ''",
                "artifact_id": "ALTER TABLE factor_versions ADD COLUMN artifact_id TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in factor_version_migrations.items():
                if column not in factor_columns:
                    connection.execute(statement)
            for table, column, statement in (
                ("model_versions", "content_hash", "ALTER TABLE model_versions ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"),
                ("strategy_versions", "content_hash", "ALTER TABLE strategy_versions ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"),
                ("backtest_drafts", "strategy_entity_id", "ALTER TABLE backtest_drafts ADD COLUMN strategy_entity_id TEXT"),
                ("backtest_drafts", "strategy_version_id", "ALTER TABLE backtest_drafts ADD COLUMN strategy_version_id TEXT"),
                ("backtest_drafts", "config_json", "ALTER TABLE backtest_drafts ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'"),
                ("backtest_runs", "submission_token", "ALTER TABLE backtest_runs ADD COLUMN submission_token TEXT"),
            ):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in columns:
                    connection.execute(statement)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS backtest_runs_submission_token_uq "
                "ON backtest_runs(submission_token) WHERE submission_token IS NOT NULL"
            )
            diagnostic_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(factor_diagnostics)").fetchall()
            }
            diagnostic_migrations = {
                "diagnostic_run_id": "ALTER TABLE factor_diagnostics ADD COLUMN diagnostic_run_id TEXT NOT NULL DEFAULT ''",
                "artifact_id": "ALTER TABLE factor_diagnostics ADD COLUMN artifact_id TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in diagnostic_migrations.items():
                if column not in diagnostic_columns:
                    connection.execute(statement)
            calculation_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(factor_calculation_runs)").fetchall()
            }
            calculation_migrations = {
                "icir": "ALTER TABLE factor_calculation_runs ADD COLUMN icir REAL",
                "top_bottom_spread": "ALTER TABLE factor_calculation_runs ADD COLUMN top_bottom_spread REAL",
                "monotonicity": "ALTER TABLE factor_calculation_runs ADD COLUMN monotonicity REAL",
                "factor_mean": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_mean REAL",
                "factor_std": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_std REAL",
                "factor_min": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_min REAL",
                "factor_max": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_max REAL",
                "factor_skew": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_skew REAL",
                "factor_kurtosis": "ALTER TABLE factor_calculation_runs ADD COLUMN factor_kurtosis REAL",
                "quantiles_json": "ALTER TABLE factor_calculation_runs ADD COLUMN quantiles_json TEXT NOT NULL DEFAULT '{}'",
            }
            for column, statement in calculation_migrations.items():
                if column not in calculation_columns:
                    connection.execute(statement)
            research_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(research_runs)").fetchall()
            }
            migrations = {
                "name": "ALTER TABLE research_runs ADD COLUMN name TEXT NOT NULL DEFAULT '未命名研究'",
                "research_type": "ALTER TABLE research_runs ADD COLUMN research_type TEXT NOT NULL DEFAULT 'manual'",
                "summary_json": "ALTER TABLE research_runs ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'",
                "copied_from_run_id": "ALTER TABLE research_runs ADD COLUMN copied_from_run_id TEXT",
                "parent_run_id": "ALTER TABLE research_runs ADD COLUMN parent_run_id TEXT",
            }
            for column, statement in migrations.items():
                if column not in research_columns:
                    connection.execute(statement)
            plan_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(backtest_plans)").fetchall()
            }
            if plan_columns and "closed" not in plan_columns:
                connection.execute(
                    "ALTER TABLE backtest_plans ADD COLUMN closed INTEGER NOT NULL DEFAULT 0"
                )
            # Recreate this trigger so databases initialized before task 04 also
            # protect the newly added semantic mapping fields.
            connection.executescript(
                """
                DROP TRIGGER IF EXISTS factor_versions_immutable_published;
                CREATE TRIGGER factor_versions_immutable_published
                BEFORE UPDATE ON factor_versions
                WHEN OLD.status IN ('published', 'deprecated') AND (
                    NEW.dataset_id IS NOT OLD.dataset_id OR
                    NEW.dataset_version_id IS NOT OLD.dataset_version_id OR
                    NEW.formula IS NOT OLD.formula OR
                    NEW.input_fields_json IS NOT OLD.input_fields_json OR
                    NEW.source IS NOT OLD.source OR
                    NEW.direction IS NOT OLD.direction OR
                    NEW.frequency IS NOT OLD.frequency OR
                    NEW.missing_policy IS NOT OLD.missing_policy OR
                    NEW.pit_policy IS NOT OLD.pit_policy OR
                    NEW.pit_lineage_json IS NOT OLD.pit_lineage_json OR
                    NEW.upstream_factor_versions_json IS NOT OLD.upstream_factor_versions_json OR
                    NEW.origin IS NOT OLD.origin OR
                    NEW.author IS NOT OLD.author OR
                    NEW.code_hash IS NOT OLD.code_hash OR
                    NEW.generation_run_id IS NOT OLD.generation_run_id OR
                    NEW.artifact_id IS NOT OLD.artifact_id OR
                    NEW.last_verified_at IS NOT OLD.last_verified_at OR
                    NEW.quality_json IS NOT OLD.quality_json
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published factor version is immutable');
                END;
                DROP TRIGGER IF EXISTS backtest_runs_state_machine;
                CREATE TRIGGER backtest_runs_state_machine
                BEFORE UPDATE OF status ON backtest_runs
                WHEN NOT (
                    NEW.status = OLD.status OR
                    (OLD.status = 'queued' AND NEW.status = 'running') OR
                    (OLD.status = 'failed' AND NEW.status = 'running') OR
                    (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid run status transition');
                END;
                """
            )

    def register_run(self, run_id: str, run_type: str) -> None:
        validate_run_id(run_id)
        if run_type not in {"research", "model_training", "backtest"}:
            raise ValueError(f"unsupported run type: {run_type}")
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type) VALUES (?, ?)",
                (run_id, run_type),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
