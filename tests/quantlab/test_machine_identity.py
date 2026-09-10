from pathlib import Path

import pytest

from quantlab.services.machine_identity import load_machine_identity, save_serial_prefix


def test_machine_identity_persists_on_disk(tmp_path: Path) -> None:
    first = load_machine_identity(tmp_path / "runtime")
    second = load_machine_identity(tmp_path / "runtime")
    assert first["machine_id"] == second["machine_id"]
    assert 10 <= int(first["serial_prefix"]) <= 99
    path = tmp_path / "runtime" / "config" / "machine.json"
    assert path.is_file()
    assert first["machine_id"] in path.read_text(encoding="utf-8")


def test_save_serial_prefix_keeps_machine_id(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    original = load_machine_identity(root)
    saved = save_serial_prefix(root, 47)
    assert saved["machine_id"] == original["machine_id"]
    assert saved["serial_prefix"] == 47
    assert load_machine_identity(root)["serial_prefix"] == 47
    with pytest.raises(ValueError):
        save_serial_prefix(root, 9)
    with pytest.raises(ValueError):
        save_serial_prefix(root, 100)
    assert load_machine_identity(root)["serial_prefix"] == 47
