"""Stable per-machine identity stored outside synced result files."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any


def machine_identity_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "config" / "machine.json"


def _write_identity(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_machine_identity(runtime_root: Path | None) -> dict[str, Any]:
    if runtime_root is None:
        return {"machine_id": "", "serial_prefix": 10}
    root = Path(runtime_root)
    path = machine_identity_path(root)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        machine_id = str(raw.get("machine_id") or "").strip()
        try:
            prefix = int(raw.get("serial_prefix") or 0)
        except (TypeError, ValueError):
            prefix = 0
        if machine_id and 10 <= prefix <= 99:
            return {"machine_id": machine_id, "serial_prefix": prefix}
    machine_id = secrets.token_hex(4)
    prefix = (int(machine_id, 16) % 90) + 10
    return _write_identity(path, {"machine_id": machine_id, "serial_prefix": prefix})


def save_serial_prefix(runtime_root: Path | None, serial_prefix: int) -> dict[str, Any]:
    try:
        prefix = int(serial_prefix)
    except (TypeError, ValueError) as error:
        raise ValueError("serial_prefix must be an integer from 10 to 99") from error
    if not 10 <= prefix <= 99:
        raise ValueError("serial_prefix must be an integer from 10 to 99")
    current = load_machine_identity(runtime_root)
    if not current.get("machine_id") or runtime_root is None:
        raise ValueError("machine identity is not available")
    return _write_identity(
        machine_identity_path(Path(runtime_root)),
        {"machine_id": current["machine_id"], "serial_prefix": prefix},
    )
