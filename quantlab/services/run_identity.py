"""Time-sortable run identifiers allocated transactionally by SQLite."""

from __future__ import annotations

from datetime import datetime

from quantlab.repositories.database import Database


class RunIdentity:
    def __init__(self, database: Database) -> None:
        self.database = database

    def now(self) -> datetime:
        return datetime.now()

    def next_id(self) -> str:
        current = self.now()
        stamp = current.strftime("%Y%m%d-%H%M%S")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO run_sequences(stamp, next_serial) VALUES (?, 0)",
                (stamp,),
            )
            connection.execute(
                "UPDATE run_sequences SET next_serial = next_serial + 1 WHERE stamp = ?",
                (stamp,),
            )
            serial = connection.execute(
                "SELECT next_serial FROM run_sequences WHERE stamp = ?", (stamp,)
            ).fetchone()[0]
        return f"{stamp}-{serial:04d}"
