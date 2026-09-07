import sqlite3

import pytest

from quantlab.repositories.database import Database


def test_database_initializes_schema_and_foreign_keys(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"datasets", "dataset_versions", "factor_versions", "model_versions", "backtest_runs", "artifacts"} <= tables


def test_database_enforces_lineage_and_transaction_rollback(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'draft')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status) VALUES ('ds', 'v1', '/tmp/data', 'published')"
        )
        connection.execute(
            "INSERT INTO factors(entity_id, name, status) VALUES ('factor', '因子', 'draft')"
        )
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, status) "
            "VALUES ('factor', 'v1', 'ds', 'v1', 'x', 'published')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, status) "
                "VALUES ('factor', 'v1', 'ds', 'v1', 'duplicate', 'published')"
            )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM factor_versions").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM dataset_versions WHERE entity_id='ds' AND version_id='v1'")


def test_database_rejects_invalid_lifecycle_quality_and_run_statuses(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'draft')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status) "
            "VALUES ('ds', 'v1', '/tmp/data', 'draft')"
        )
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('factor', '因子', 'draft')")
        connection.execute("INSERT INTO models(entity_id, name, status) VALUES ('model', '模型', 'draft')")
        connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES ('strategy', '策略', 'draft')")
    lifecycle_statements = [
        "INSERT INTO datasets(entity_id, name, status) VALUES ('bad-ds', '数据', 'invalid')",
        "INSERT INTO dataset_versions(entity_id, version_id, path, status) VALUES ('ds', 'bad', '/tmp/data', 'invalid')",
        "INSERT INTO factors(entity_id, name, status) VALUES ('bad-factor', '因子', 'invalid')",
        "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, status) "
        "VALUES ('factor', 'bad', 'ds', 'v1', 'x', 'invalid')",
        "INSERT INTO models(entity_id, name, status) VALUES ('bad-model', '模型', 'invalid')",
        "INSERT INTO model_versions(entity_id, version_id, status) VALUES ('model', 'bad', 'invalid')",
        "INSERT INTO strategies(entity_id, name, status) VALUES ('bad-strategy', '策略', 'invalid')",
        "INSERT INTO strategy_versions(entity_id, version_id, status) VALUES ('strategy', 'bad', 'invalid')",
    ]
    for statement in lifecycle_statements:
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(statement)
    quality_statements = [
        "INSERT INTO dataset_versions(entity_id, version_id, path, quality_status) "
        "VALUES ('ds', 'bad-quality', '/tmp/data', 'invalid')",
        "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, quality_status) "
        "VALUES ('factor', 'bad-quality', 'ds', 'v1', 'x', 'invalid')",
        "INSERT INTO model_versions(entity_id, version_id, quality_status) VALUES ('model', 'bad-quality', 'invalid')",
        "INSERT INTO strategy_versions(entity_id, version_id, quality_status) "
        "VALUES ('strategy', 'bad-quality', 'invalid')",
    ]
    for statement in quality_statements:
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(statement)
    run_tables = [
        ("model_training_runs", "20260902-120000-0001", "model_training"),
        ("research_runs", "20260902-120000-0002", "research"),
        ("backtest_runs", "20260902-120000-0003", "backtest"),
    ]
    for table, run_id, run_type in run_tables:
        database.register_run(run_id, run_type)
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(
                    f"INSERT INTO {table}(run_id, status) VALUES (?, 'invalid')",
                    (run_id,),
                )


def test_published_dataset_version_is_immutable_except_quality_and_deprecation(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions("
            "entity_id, version_id, path, row_count, fields_json, date_min, date_max, manifest_hash, metadata_json, status"
            ") VALUES ('ds', 'v1', '/tmp/data', 1, '[\"x\"]', '20200101', '20200102', 'sha256:one', '{\"x\":1}', 'published')"
        )
    immutable_updates = {
        "path": "'/tmp/changed'",
        "row_count": "2",
        "fields_json": "'[\"changed\"]'",
        "date_min": "'20190101'",
        "date_max": "'20300101'",
        "manifest_hash": "'sha256:changed'",
        "metadata_json": "'{\"changed\":true}'",
    }
    for column, value in immutable_updates.items():
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(
                    f"UPDATE dataset_versions SET {column}={value} WHERE entity_id='ds' AND version_id='v1'"
                )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE dataset_versions SET status='draft' WHERE entity_id='ds' AND version_id='v1'"
            )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE dataset_versions SET quality_status='needs_review' WHERE entity_id='ds' AND version_id='v1'"
        )
        connection.execute(
            "UPDATE dataset_versions SET status='deprecated' WHERE entity_id='ds' AND version_id='v1'"
        )


@pytest.mark.parametrize("target_status", ["draft", "validated"])
def test_published_dataset_version_rejects_lifecycle_regression(tmp_path, target_status: str) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status) "
            "VALUES ('ds', 'v1', '/tmp/data', 'published')"
        )

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE dataset_versions SET status=? WHERE entity_id='ds' AND version_id='v1'",
                (target_status,),
            )


def test_sqlite_lifecycle_statuses_are_one_way(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'draft')")

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute("UPDATE datasets SET status='published' WHERE entity_id='ds'")
    with database.transaction() as connection:
        connection.execute("UPDATE datasets SET status='validated' WHERE entity_id='ds'")
        connection.execute("UPDATE datasets SET status='published' WHERE entity_id='ds'")
        connection.execute("UPDATE datasets SET status='deprecated' WHERE entity_id='ds'")
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute("UPDATE datasets SET status='published' WHERE entity_id='ds'")


@pytest.mark.parametrize(
    ("table", "run_id", "run_type"),
    [
        ("model_training_runs", "20260902-120000-0001", "model_training"),
        ("research_runs", "20260902-120000-0002", "research"),
        ("backtest_runs", "20260902-120000-0003", "backtest"),
    ],
)
def test_database_enforces_run_state_machine(tmp_path, table: str, run_id: str, run_type: str) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    database.register_run(run_id, run_type)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                f"INSERT INTO {table}(run_id, status) VALUES (?, 'running')",
                (run_id,),
            )
    with database.transaction() as connection:
        connection.execute(f"INSERT INTO {table}(run_id, status) VALUES (?, 'queued')", (run_id,))
    for terminal in ("completed", "failed"):
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(
                    f"UPDATE {table} SET status=? WHERE run_id=?",
                    (terminal, run_id),
                )
    with database.transaction() as connection:
        connection.execute(f"UPDATE {table} SET status='running' WHERE run_id=?", (run_id,))
        connection.execute(f"UPDATE {table} SET status='completed' WHERE run_id=?", (run_id,))
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(f"UPDATE {table} SET status='running' WHERE run_id=?", (run_id,))


def test_failed_backtest_can_restart_to_running(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    run_id = "20260902-120000-0004"
    database.register_run(run_id, "backtest")
    with database.transaction() as connection:
        connection.execute("INSERT INTO backtest_runs(run_id, status) VALUES (?, 'queued')", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='failed' WHERE run_id=?", (run_id,))
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        row = connection.execute("SELECT status FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "running"


def test_run_id_format_is_validated_in_python_and_sqlite(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with pytest.raises(ValueError, match="run_id"):
        database.register_run("run-1", "research")
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO run_registry(run_id, run_type) VALUES ('run-1', 'research')"
            )


def test_optional_composite_foreign_keys_must_be_both_null_or_both_set(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '数据', 'draft')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status) "
            "VALUES ('ds', 'v1', '/tmp/data', 'draft')"
        )
        connection.execute("INSERT INTO models(entity_id, name, status) VALUES ('model', '模型', 'draft')")
        connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES ('strategy', '策略', 'draft')")
    database.register_run("20260902-120000-0001", "model_training")
    database.register_run("20260902-120000-0002", "backtest")
    statements = [
        "INSERT INTO model_versions(entity_id, version_id, dataset_id) VALUES ('model', 'one-null-a', 'ds')",
        "INSERT INTO model_versions(entity_id, version_id, dataset_version_id) VALUES ('model', 'one-null-b', 'v1')",
        "INSERT INTO strategy_versions(entity_id, version_id, model_entity_id) "
        "VALUES ('strategy', 'one-null-a', 'model')",
        "INSERT INTO strategy_versions(entity_id, version_id, model_version_id) "
        "VALUES ('strategy', 'one-null-b', 'v1')",
        "INSERT INTO model_training_runs(run_id, status, model_entity_id) "
        "VALUES ('20260902-120000-0001', 'queued', 'model')",
        "INSERT INTO model_training_runs(run_id, status, dataset_version_id) "
        "VALUES ('20260902-120000-0001', 'queued', 'v1')",
        "INSERT INTO backtest_runs(run_id, status, strategy_entity_id) "
        "VALUES ('20260902-120000-0002', 'queued', 'strategy')",
        "INSERT INTO backtest_runs(run_id, status, dataset_version_id) "
        "VALUES ('20260902-120000-0002', 'queued', 'v1')",
    ]
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                connection.execute(statement)
