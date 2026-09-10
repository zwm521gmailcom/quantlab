"""Time-sortable run identifiers allocated transactionally by SQLite."""

from __future__ import annotations

from datetime import datetime

from quantlab.repositories.database import Database
from quantlab.services.machine_identity import load_machine_identity


class RunIdentity:
    def __init__(self, database: Database, runtime_root=None) -> None:
        self.database = database
        self.runtime_root = runtime_root

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
                "SELECT next_serial FROM run_sequences WHERE stamp=?", (stamp,)
            ).fetchone()[0]
        prefix = int(load_machine_identity(self.runtime_root).get("serial_prefix") or 10)
        if self.runtime_root is not None:
            serial = prefix * 100 + (int(serial) % 100)
        return f"{stamp}-{serial:04d}"
