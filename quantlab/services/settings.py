"""Runtime settings with a deliberately small, non-secret persistence boundary."""
from __future__ import annotations

import json
from os import cpu_count, environ, sysconf
from pathlib import Path
from collections.abc import Callable
from typing import Any

from quantlab.config import Settings

DEFAULTS: dict[str, Any] = {"top_n": 10, "rebalance_days": 2, "capital": 1_000_000, "benchmark": "000300.SH", "buy_fee": 0.0003, "sell_fee": 0.0005, "slippage": 0.0005}
COMPUTE_DEFAULTS: dict[str, Any] = {"fold_workers": 0, "bucket_workers": 0, "bucket_pool": "process"}
_ACTIVE_COMPUTE: dict[str, Any] = dict(COMPUTE_DEFAULTS)


def apply_compute(compute: dict[str, Any] | None) -> None:
    _ACTIVE_COMPUTE.clear()
    _ACTIVE_COMPUTE.update({**COMPUTE_DEFAULTS, **(compute or {})})


def total_ram_bytes() -> int:
    try:
        pages = sysconf("SC_PHYS_PAGES")
        size = sysconf("SC_PAGE_SIZE")
        if pages and size:
            return int(pages) * int(size)
    except (ValueError, OSError, AttributeError):
        pass
    return 0


def memory_safe_process_workers(nbytes: int, requested: int, ram: int | None = None) -> int:
    requested = max(1, int(requested))
    if requested <= 1:
        return 1
    total = int(ram if ram is not None else total_ram_bytes() or 0)
    used = max(0, int(nbytes or 0))
    if total <= 0 or used <= 0:
        return min(requested, 2)
    budget = int(total * 0.40)
    copies = max(1, budget // used)
    if copies <= 1:
        return 1
    return min(requested, copies - 1)


def compute_hint() -> dict[str, Any]:
    cpu = cpu_count() or 1
    ram = total_ram_bytes()
    ram_gb = ram / (1024**3) if ram else 0.0
    auto = max(1, int(round(cpu * 0.8)))
    if ram_gb and ram_gb < 24:
        safe_bucket = 1
    elif ram_gb and ram_gb < 48:
        safe_bucket = 2
    else:
        safe_bucket = min(4, auto)
    return {
        "cpu_count": int(cpu),
        "auto_workers": auto,
        "ram_gb": round(ram_gb, 1),
        "safe_bucket_workers": safe_bucket,
        "safe_fold_workers": min(4, auto),
    }


def resolve_worker_count(env_name: str, setting_key: str, task_count: int, cpu_fn: Callable[[], int | None]) -> int:
    raw = str(environ.get(env_name) or "").strip()
    if raw:
        workers = max(1, int(raw))
    else:
        configured = int(_ACTIVE_COMPUTE.get(setting_key) or 0)
        if configured > 0:
            workers = configured
        else:
            workers = max(1, int(round((cpu_fn() or 1) * 0.8)))
    return max(1, min(workers, int(task_count)))


def resolve_bucket_pool() -> str:
    raw = str(environ.get("QUANTLAB_BUCKET_POOL") or "").strip().lower()
    if raw:
        return raw
    return str(_ACTIVE_COMPUTE.get("bucket_pool") or "process").strip().lower()


def _coerce_compute(value: dict[str, Any]) -> dict[str, Any]:
    merged = {**COMPUTE_DEFAULTS, **value}
    try:
        fold = int(merged.get("fold_workers") or 0)
        bucket = int(merged.get("bucket_workers") or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("fold_workers or bucket_workers is invalid") from error
    pool = str(merged.get("bucket_pool") or "process").strip().lower()
    if not 0 <= fold <= 256 or not 0 <= bucket <= 256:
        raise ValueError("fold_workers or bucket_workers is invalid")
    if pool not in {"process", "thread"}:
        raise ValueError("bucket_pool must be process or thread")
    return {"fold_workers": fold, "bucket_workers": bucket, "bucket_pool": pool}


class SettingsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.runtime_root / "config/settings.json"
        apply_compute(self._read()["compute"])

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
        empty = {"defaults": dict(DEFAULTS), "compute": dict(COMPUTE_DEFAULTS)}
        if not self.path.is_file():
            return empty
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty
        if not isinstance(value, dict):
            return empty
        compute_raw = value.get("compute") or {}
        if not isinstance(compute_raw, dict):
            compute_raw = {}
        compute_raw = {key: compute_raw[key] for key in COMPUTE_DEFAULTS if key in compute_raw}
        try:
            compute = _coerce_compute(compute_raw)
        except ValueError:
            compute = dict(COMPUTE_DEFAULTS)
        return {"defaults": {**DEFAULTS, **(value.get("defaults") or {})}, "compute": compute}

    def _write(self, defaults: dict[str, Any], compute: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"defaults": defaults, "compute": compute}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        apply_compute(compute)

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
        value = self._read()
        return {
            "paths": {
                "project_root": str(self.settings.project_root),
                "data_root": str(self.settings.data_root),
                "calibration_root": str(self.settings.calibration_root),
                "runtime_root": str(self.settings.runtime_root),
                "raw_root": self.raw_path(),
                "results_root": str(self.settings.runtime_root / "results"),
            },
            "environment": {"host": self.settings.host, "port": self.settings.port, "service": "local-only"},
            "defaults": value["defaults"],
            "compute": value["compute"],
            "compute_hint": compute_hint(),
            "secrets": {"tushare_token": self.token_configured()},
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "host" in payload and payload["host"] != "127.0.0.1":
            raise ValueError("QuantLab only accepts host 127.0.0.1")
        if "paths" in payload:
            raise ValueError("authoritative and runtime paths are startup-controlled")
        defaults = payload.get("defaults", {})
        compute = payload.get("compute", {})
        if defaults and not isinstance(defaults, dict):
            raise ValueError("unsupported settings: defaults")
        if compute and not isinstance(compute, dict):
            raise ValueError("unsupported settings: compute")
        defaults = defaults if isinstance(defaults, dict) else {}
        compute = compute if isinstance(compute, dict) else {}
        unknown = (set(defaults) - set(DEFAULTS)) | (set(compute) - set(COMPUTE_DEFAULTS))
        if unknown:
            raise ValueError(f"unsupported settings: {sorted(unknown)}")
        current = self._read()
        merged = {**current["defaults"], **defaults}
        if not 1 <= int(merged["top_n"]) <= 1000 or int(merged["rebalance_days"]) < 1:
            raise ValueError("top_n or rebalance_days is invalid")
        merged_compute = _coerce_compute({**current["compute"], **compute})
        self._write(merged, merged_compute)
        return self.public()

    def reset(self) -> dict[str, Any]:
        self._write(dict(DEFAULTS), dict(COMPUTE_DEFAULTS))
        return self.public()

    def scan(self) -> dict[str, Any]:
        roots = {name: path for name, path in (("data_root", self.settings.data_root), ("calibration_root", self.settings.calibration_root), ("runtime_root", self.settings.runtime_root))}
        return {"manual": True, "roots": [{"name": name, "path": str(path), "exists": path.exists(), "is_dir": path.is_dir()} for name, path in roots.items()]}
