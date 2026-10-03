"""Runtime settings with a deliberately small, non-secret persistence boundary."""
from __future__ import annotations

import json
from os import cpu_count, environ, sysconf
from pathlib import Path
from collections.abc import Callable
from typing import Any

from quantlab import __version__
from quantlab.config import Settings, asset_label, load_instance_file, save_instance_file
from quantlab.services.asset_layout import directory_plan
from quantlab.services.lan_files import DATA_SYNC_CATEGORIES, normalize_data_categories
from quantlab.services.machine_identity import load_machine_identity, save_serial_prefix

DEFAULTS: dict[str, Any] = {"top_n": 10, "rebalance_days": 2, "capital": 1_000_000, "benchmark": "000300.SH", "buy_fee": 0.0003, "sell_fee": 0.0005, "slippage": 0.0005}
COMPUTE_DEFAULTS: dict[str, Any] = {"fold_workers": 0, "bucket_workers": 0, "bucket_pool": "process"}
LAN_DEFAULTS: dict[str, Any] = {
    "market_sync_at_0400": False,
    "market_sync_categories": list(DATA_SYNC_CATEGORIES),
}
_ACTIVE_COMPUTE: dict[str, Any] = dict(COMPUTE_DEFAULTS)
FOLD_PROCESS_BYTES = 8 * 1024**3


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
    slots = max(1, max_concurrent_backtests(total))
    budget = int(total * 0.40 / slots)
    copies = max(1, budget // used)
    if copies <= 1:
        return 1
    return min(requested, copies - 1)


def _admission_ceiling(ram: int) -> int:
    from quantlab.services.backtest_admission import memory_ceiling

    return memory_ceiling(int(ram or 0))


def _admission_load_slots(ram: int) -> int:
    from quantlab.services.backtest_admission import load_ceiling

    return load_ceiling(int(ram or 0))


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
        "safe_fold_workers": memory_safe_process_workers(FOLD_PROCESS_BYTES, min(4, auto), ram=ram),
        "max_concurrent_backtests": max_concurrent_backtests(ram, cpu=int(cpu)),
        "admission_ceiling": _admission_ceiling(ram),
        "admission_load_slots": _admission_load_slots(ram),
    }


def hardware_concurrent_cap(ram: int | None = None, cpu: int | None = None) -> int:
    total = int(ram if ram is not None else total_ram_bytes() or 0)
    gb = total / (1024**3) if total else 0.0
    cores = max(1, int(cpu if cpu is not None else (cpu_count() or 1)))
    if gb <= 0:
        by_ram = 1
    else:
        by_ram = max(1, int(max(0.0, gb - 8.0) // 19))
    if gb >= 120:
        by_ram = max(by_ram, 12)
        by_cpu = max(1, cores // 2 if cores >= 16 else cores // 4)
    else:
        by_cpu = max(1, cores // 4)
    return min(by_ram, by_cpu, 16)


def max_concurrent_backtests(ram: int | None = None, cpu: int | None = None) -> int:
    hardware = hardware_concurrent_cap(ram, cpu)
    raw = str(environ.get("QUANTLAB_MAX_CONCURRENT_BACKTESTS") or "").strip()
    if raw:
        try:
            requested = max(1, min(int(raw), 16))
        except ValueError:
            return hardware
        return min(requested, hardware)
    return hardware


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


def _coerce_lan(value: dict[str, Any]) -> dict[str, Any]:
    raw_cats = value.get("market_sync_categories", LAN_DEFAULTS["market_sync_categories"])
    try:
        categories = list(normalize_data_categories(raw_cats))
    except ValueError as error:
        raise ValueError("market_sync_categories is invalid") from error
    return {
        "market_sync_at_0400": bool(value.get("market_sync_at_0400")),
        "market_sync_categories": categories,
    }


class SettingsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.runtime_root / "config/settings.json"
        apply_compute(self._read()["compute"])

    def token_path(self) -> Path:
        return self.path.parent / "tushare_token.json"

    def points_path(self) -> Path:
        return self.path.parent / "tushare_points.json"

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

    def tushare_points_public(self) -> dict[str, Any]:
        from quantlab.services.tushare_download import quota_public

        return quota_public(self.settings.runtime_root)

    def update_tushare_points(self, points: int | None) -> dict[str, Any]:
        from quantlab.services.tushare_download import resolve_tushare_quota

        path = self.points_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if points is None:
            path.unlink(missing_ok=True)
            return self.tushare_points_public()
        value = int(points)
        if value < 0:
            raise ValueError("tushare points must be >= 0")
        resolve_tushare_quota(value)  # validate mapping
        path.write_text(json.dumps({"points": value}, ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
        return self.tushare_points_public()

    def _read(self) -> dict[str, Any]:
        empty = {"defaults": dict(DEFAULTS), "compute": dict(COMPUTE_DEFAULTS), "lan": dict(LAN_DEFAULTS)}
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
        lan_raw = value.get("lan") or {}
        if not isinstance(lan_raw, dict):
            lan_raw = {}
        return {
            "defaults": {**DEFAULTS, **(value.get("defaults") or {})},
            "compute": compute,
            "lan": _coerce_lan(lan_raw),
        }

    def _write(self, defaults: dict[str, Any], compute: dict[str, Any], lan: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"defaults": defaults, "compute": compute, "lan": lan}, ensure_ascii=False, indent=2),
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
        override = self._raw_override()
        if override:
            return self.settings.display_path(override)
        return self.settings.display_path(self.settings.raw_root)

    def update_raw_root(self, path: str) -> dict[str, Any]:
        resolved = self.settings.resolve_user_path(path)
        if not resolved.is_dir():
            raise ValueError("raw directory must exist")
        stored = self.settings.store_path(resolved)
        override_file = self.path.parent / "raw_path.json"
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(json.dumps({"path": stored, "updated_at": __import__("datetime").datetime.now().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"raw_root": stored, "saved": True}

    def _saved_instance(self) -> dict[str, object]:
        saved = load_instance_file(self.settings.runtime_root)
        return {
            "asset": str(saved.get("asset") or self.settings.asset),
            "port": int(saved.get("port") if saved.get("port") is not None else self.settings.port),
            "lan_port": int(saved.get("lan_port") if saved.get("lan_port") is not None else self.settings.lan_port),
        }

    def _instance_public(self) -> dict[str, object]:
        saved = self._saved_instance()
        live_asset = str(self.settings.asset)
        live_port = int(self.settings.port)
        live_lan = int(self.settings.lan_port)
        restart_required = (
            str(saved["asset"]) != live_asset
            or int(saved["port"]) != live_port
            or int(saved["lan_port"]) != live_lan
        )
        return {
            "asset": str(saved["asset"]),
            "asset_label": asset_label(str(saved["asset"])),
            "port": int(saved["port"]),
            "lan_port": int(saved["lan_port"]),
            "restart_required": restart_required,
        }

    def public(self) -> dict[str, Any]:
        value = self._read()
        paths = {
            "project_root": self.settings.display_path(self.settings.project_root),
            "data_root": self.settings.display_path(self.settings.data_root),
            "calibration_root": self.settings.display_path(self.settings.calibration_root),
            "runtime_root": self.settings.display_path(self.settings.runtime_root),
            "raw_root": self.raw_path(),
            "results_root": self.settings.display_path(self.settings.runtime_root / "results"),
        }
        return {
            "paths": paths,
            "directory_plan": directory_plan(
                data_root=paths["data_root"],
                raw_root=paths["raw_root"],
                runtime_root=paths["runtime_root"],
            ),
            "environment": {
                "host": self.settings.host,
                "port": int(self.settings.port),
                "lan_port": int(self.settings.lan_port),
                "asset": str(self.settings.asset),
                "asset_label": self.settings.asset_label,
                "service": "local-only",
                "version": __version__,
            },
            "instance": self._instance_public(),
            "defaults": value["defaults"],
            "compute": value["compute"],
            "lan": value["lan"],
            "compute_hint": compute_hint(),
            "machine": load_machine_identity(self.settings.runtime_root),
            "secrets": {
                "tushare_token": self.token_configured(),
                "tushare_points": self.tushare_points_public()["configured"],
            },
            "tushare_quota": self.tushare_points_public(),
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "host" in payload and payload["host"] != self.settings.host:
            raise ValueError("host is startup-controlled")
        if "paths" in payload:
            raise ValueError("authoritative and runtime paths are startup-controlled")
        defaults = payload.get("defaults", {})
        compute = payload.get("compute", {})
        machine = payload.get("machine", {})
        lan = payload.get("lan", {})
        instance = payload.get("instance", {})
        if defaults and not isinstance(defaults, dict):
            raise ValueError("unsupported settings: defaults")
        if compute and not isinstance(compute, dict):
            raise ValueError("unsupported settings: compute")
        if machine and not isinstance(machine, dict):
            raise ValueError("unsupported settings: machine")
        if lan and not isinstance(lan, dict):
            raise ValueError("unsupported settings: lan")
        if instance and not isinstance(instance, dict):
            raise ValueError("unsupported settings: instance")
        defaults = defaults if isinstance(defaults, dict) else {}
        compute = compute if isinstance(compute, dict) else {}
        machine = machine if isinstance(machine, dict) else {}
        lan = lan if isinstance(lan, dict) else {}
        instance = instance if isinstance(instance, dict) else {}
        unknown = (
            (set(defaults) - set(DEFAULTS))
            | (set(compute) - set(COMPUTE_DEFAULTS))
            | (set(machine) - {"serial_prefix"})
            | (set(lan) - set(LAN_DEFAULTS))
            | (set(instance) - {"asset", "port", "lan_port"})
        )
        if unknown:
            raise ValueError(f"unsupported settings: {sorted(unknown)}")
        current = self._read()
        merged = {**current["defaults"], **defaults}
        if not 1 <= int(merged["top_n"]) <= 1000 or int(merged["rebalance_days"]) < 1:
            raise ValueError("top_n or rebalance_days is invalid")
        merged_compute = _coerce_compute({**current["compute"], **compute})
        merged_lan = _coerce_lan({**current["lan"], **lan})
        if "serial_prefix" in machine:
            save_serial_prefix(self.settings.runtime_root, machine["serial_prefix"])
        if instance:
            current_instance = self._saved_instance()
            save_instance_file(
                self.settings.runtime_root,
                asset=str(instance.get("asset", current_instance["asset"])),
                port=int(instance.get("port", current_instance["port"])),
                lan_port=int(instance.get("lan_port", current_instance["lan_port"])),
            )
        self._write(merged, merged_compute, merged_lan)
        return self.public()

    def reset(self) -> dict[str, Any]:
        self._write(dict(DEFAULTS), dict(COMPUTE_DEFAULTS), dict(LAN_DEFAULTS))
        return self.public()

    def scan(self) -> dict[str, Any]:
        roots = {name: path for name, path in (("data_root", self.settings.data_root), ("calibration_root", self.settings.calibration_root), ("runtime_root", self.settings.runtime_root))}
        return {
            "manual": True,
            "roots": [
                {
                    "name": name,
                    "path": self.settings.display_path(path),
                    "exists": path.exists(),
                    "is_dir": path.is_dir(),
                }
                for name, path in roots.items()
            ],
        }
