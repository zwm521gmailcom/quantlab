"""Runtime settings with a deliberately small, non-secret persistence boundary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantlab.config import Settings

DEFAULTS: dict[str, Any] = {"top_n": 10, "rebalance_days": 2, "capital": 1_000_000, "benchmark": "000300.SH", "buy_fee": 0.0003, "sell_fee": 0.0005, "slippage": 0.0005}


class SettingsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.runtime_root / "config/settings.json"

    def token_path(self) -> Path:
        return self.path.parent / "tushare_token.json"

    def token_configured(self) -> bool:
        token_path = self.token_path()
        if not token_path.is_file():
            return False
        try:
            value = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(value.get("token"))

    def update_tushare_token(self, token: str) -> dict[str, Any]:
        token_path = self.token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = str(token or "").strip()
        if cleaned:
            token_path.write_text(json.dumps({"token": cleaned}, ensure_ascii=False), encoding="utf-8")
            token_path.chmod(0o600)
        else:
            token_path.unlink(missing_ok=True)
        return {"configured": bool(cleaned)}

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"defaults": dict(DEFAULTS)}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"defaults": dict(DEFAULTS)}
        return {"defaults": {**DEFAULTS, **value.get("defaults", {})}}

    def _raw_override(self) -> str | None:
        if not self.path.parent.parent.exists():
            return None
        override_file = self.path.parent / "raw_path.json"
        if not override_file.is_file():
            return None
        try:
            value = json.loads(override_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value.get("path") if isinstance(value, dict) else None

    def raw_path(self) -> str:
        return self._raw_override() or str(self.settings.raw_root)

    def update_raw_root(self, path: str) -> dict[str, Any]:
        from pathlib import Path as _Path
        resolved = _Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError("raw directory must exist")
        override_file = self.path.parent / "raw_path.json"
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(json.dumps({"path": str(resolved), "updated_at": __import__("datetime").datetime.now().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"raw_root": str(resolved), "saved": True}

    def public(self) -> dict[str, Any]:
        return {"paths": {"project_root": str(self.settings.project_root), "data_root": str(self.settings.data_root), "calibration_root": str(self.settings.calibration_root), "runtime_root": str(self.settings.runtime_root), "raw_root": self.raw_path(), "results_root": str(self.settings.runtime_root / "results")}, "environment": {"host": self.settings.host, "port": self.settings.port, "service": "local-only"}, "defaults": self._read()["defaults"], "secrets": {"tushare_token": self.token_configured()}}

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "host" in payload and payload["host"] != "127.0.0.1":
            raise ValueError("QuantLab only accepts host 127.0.0.1")
        if "paths" in payload:
            raise ValueError("authoritative and runtime paths are startup-controlled")
        defaults = payload.get("defaults", {})
        unknown = set(defaults) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unsupported settings: {sorted(unknown)}")
        merged = {**self._read()["defaults"], **defaults}
        if not 1 <= int(merged["top_n"]) <= 1000 or int(merged["rebalance_days"]) < 1:
            raise ValueError("top_n or rebalance_days is invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"defaults": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public()

    def reset(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"defaults": DEFAULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public()

    def scan(self) -> dict[str, Any]:
        roots = {name: path for name, path in (("data_root", self.settings.data_root), ("calibration_root", self.settings.calibration_root), ("runtime_root", self.settings.runtime_root))}
        return {"manual": True, "roots": [{"name": name, "path": str(path), "exists": path.exists(), "is_dir": path.is_dir()} for name, path in roots.items()]}
