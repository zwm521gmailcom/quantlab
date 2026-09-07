import re

from quantlab.repositories.database import Database
from quantlab.services.run_identity import RunIdentity


def test_run_identity_is_time_serial_and_unique_within_second(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "quantlab.sqlite3")
    database.initialize()
    identity = RunIdentity(database)
    monkeypatch.setattr(identity, "now", lambda: __import__("datetime").datetime(2026, 9, 2, 12, 0, 0))
    first = identity.next_id()
    second = identity.next_id()
    assert re.fullmatch(r"20260902-120000-\d{4}", first)
    assert first != second
