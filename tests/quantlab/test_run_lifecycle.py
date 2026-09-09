import pytest

from quantlab.domain.status import BacktestRunStatus, RunStatus
from quantlab.repositories.database import Database
from quantlab.repositories.run_lifecycle import apply_run_status, transition_backtest_run_status


def test_apply_run_status_rejects_unknown_table(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    with pytest.raises(ValueError, match="unsupported run table"):
        apply_run_status(
            database,
            table="artifacts",
            run_id="20260902-120000-0001",
            target="running",
            status_enum=RunStatus,
        )


def test_apply_run_status_raises_when_run_missing(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    with pytest.raises(ValueError, match="run not found"):
        apply_run_status(
            database,
            table="backtest_runs",
            run_id="20260902-120000-0001",
            target="running",
            status_enum=BacktestRunStatus,
        )


def test_apply_run_status_updates_whitelisted_table(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    run_id = "20260902-120000-0003"
    database.register_run(run_id, "backtest")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status) VALUES (?, 'queued')",
            (run_id,),
        )

    next_status = apply_run_status(
        database,
        table="backtest_runs",
        run_id=run_id,
        target="running",
        status_enum=BacktestRunStatus,
    )

    assert next_status == "running"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM backtest_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row["status"] == "running"


def test_transition_backtest_run_status_rejects_queued_to_completed(tmp_path) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    run_id = "20260902-120000-0005"
    database.register_run(run_id, "backtest")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO backtest_runs(run_id, status) VALUES (?, 'queued')",
            (run_id,),
        )
        with pytest.raises(ValueError, match="not allowed"):
            transition_backtest_run_status(connection, run_id, BacktestRunStatus.COMPLETED)
