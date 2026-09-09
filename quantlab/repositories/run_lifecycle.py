"""Shared run status transitions for whitelisted run tables."""

from __future__ import annotations

from enum import Enum
from typing import Type

from quantlab.domain.status import BacktestRunStatus, transition
from quantlab.repositories.database import Database

_ALLOWED_TABLES = frozenset(
    {
        "research_runs",
        "model_training_runs",
        "backtest_runs",
    }
)


def transition_backtest_run_status(
    connection,
    run_id: str,
    target: BacktestRunStatus,
) -> str | None:
    row = connection.execute(
        "SELECT status FROM backtest_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return transition(BacktestRunStatus(row["status"]), target).value


def apply_run_status(
    database: Database,
    *,
    table: str,
    run_id: str,
    target: str,
    status_enum: Type[Enum],
) -> str:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"unsupported run table: {table}")

    with database.transaction() as connection:
        row = connection.execute(
            f"SELECT status FROM {table} WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"run not found: {run_id}")

        next_status = transition(
            status_enum(row["status"]),
            status_enum(target),
        ).value
        connection.execute(
            f"UPDATE {table} SET status=? WHERE run_id=?",
            (next_status, run_id),
        )
        return next_status
