"""Configuration and path-boundary enforcement for QuantLab."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path


_WRITE_SUBDIRECTORIES = (
    "db",
    "jobs",
    "factors",
    "strategies",
    "results",
    "baselines",
    "logs",
)


def posix_relative(path: Path | str, base: Path | str) -> str:
    """Return a POSIX path relative to ``base``. Never returns an absolute path."""

    resolved = Path(path).expanduser().resolve()
    origin = Path(base).expanduser().resolve()
    relative = os.path.relpath(resolved, origin)
    if os.path.isabs(relative):
        raise ValueError("path cannot be expressed relative to project root")
    posix = Path(relative).as_posix()
    return "." if posix in {"", "."} else posix


def _default_project_root() -> Path:
    configured = os.environ.get("QUANTLAB_PROJECT_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _resolve_path(path: Path | str, *, base: Path) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path(base) / value
    return value.resolve()


def _default_authoritative_root(project_root: Path, directory_name: str) -> Path:
    if project_root.parent.name == ".worktrees":
        return project_root.parent.parent / directory_name
    return project_root / directory_name


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = path.resolve()
    return any(candidate == root or root in candidate.parents for root in roots)


def _is_private_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
        third = int(parts[2])
        fourth = int(parts[3])
    except ValueError:
        return False
    if not all(0 <= value <= 255 for value in (first, second, third, fourth)):
        return False
    if first == 10:
        return True
    if first == 192 and second == 168:
        return True
    return first == 172 and 16 <= second <= 31


ASSET_CHOICES = ("a_share", "crypto")
ASSET_LABELS = {"a_share": "A股", "crypto": "数字货币"}
DEFAULT_UI_PORT = 8765
DEFAULT_LAN_PORT = 8766
_INSTANCE_FILE = "config/instance.json"
_ASSET_ALIASES = {
    "a_share": "a_share",
    "ashare": "a_share",
    "equity": "a_share",
    "stock": "a_share",
    "crypto": "crypto",
    "digital": "crypto",
    "digital_asset": "crypto",
}


def allowed_service_host(host: str) -> str:
    text = str(host or "").strip()
    if text in {"127.0.0.1", "localhost"}:
        return "127.0.0.1"
    if text == "0.0.0.0":
        return "0.0.0.0"
    if _is_private_ipv4(text):
        return text
    raise ValueError("QuantLab only binds 127.0.0.1, 0.0.0.0, or a private LAN address")


def normalize_asset(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"a股", "沪深a股"}:
        return "a_share"
    if text in {"数字货币", "数字币"}:
        return "crypto"
    asset = _ASSET_ALIASES.get(text, text)
    if asset not in ASSET_CHOICES:
        raise ValueError("asset must be a_share or crypto")
    return asset


def asset_label(asset: str) -> str:
    return ASSET_LABELS[normalize_asset(asset)]


def coerce_service_port(value: object, name: str = "port") -> int:
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be between 1 and 65535") from error
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def instance_file(runtime_root: Path) -> Path:
    return Path(runtime_root) / _INSTANCE_FILE


def load_instance_file(runtime_root: Path) -> dict[str, object]:
    path = instance_file(runtime_root)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    out: dict[str, object] = {}
    if "asset" in value:
        try:
            out["asset"] = normalize_asset(value.get("asset"))
        except ValueError:
            pass
    if "port" in value:
        try:
            out["port"] = coerce_service_port(value.get("port"), "port")
        except ValueError:
            pass
    if "lan_port" in value:
        try:
            out["lan_port"] = coerce_service_port(value.get("lan_port"), "lan_port")
        except ValueError:
            pass
    return out


def save_instance_file(runtime_root: Path, *, asset: str, port: int, lan_port: int) -> dict[str, object]:
    asset = normalize_asset(asset)
    port = coerce_service_port(port, "port")
    lan_port = coerce_service_port(lan_port, "lan_port")
    if port == lan_port:
        raise ValueError("port and lan_port must be different")
    payload = {"asset": asset, "port": port, "lan_port": lan_port}
    path = instance_file(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


@dataclass(frozen=True)
class Settings:
    """Resolved local paths and the LAN-reachable service setting."""

    project_root: Path = field(default_factory=_default_project_root)
    data_root: Path | None = None
    calibration_root: Path | None = None
    raw_root: Path | None = None
    runtime_root: Path | None = None
    host: str = "127.0.0.1"
    asset: str | None = None
    port: int | None = None
    lan_port: int | None = None

    def __post_init__(self) -> None:
        project_root = _resolve_path(self.project_root, base=Path.cwd())
        data_root = _resolve_path(
            self.data_root
            or os.environ.get("QUANTLAB_DATA_ROOT")
            or Path("data"),
            base=project_root,
        )
        calibration_root = _resolve_path(
            self.calibration_root
            or os.environ.get("QUANTLAB_CALIBRATION_ROOT")
            or Path("data") / "calibration",
            base=project_root,
        )
        raw_root = _resolve_path(
            self.raw_root
            or os.environ.get("QUANTLAB_RAW_ROOT")
            or Path("data") / "raw",
            base=project_root,
        )
        runtime_root = _resolve_path(
            self.runtime_root
            or os.environ.get("QUANTLAB_RUNTIME_ROOT")
            or Path("quantlab_runtime"),
            base=project_root,
        )
        instance = load_instance_file(runtime_root)
        asset = normalize_asset(
            self.asset or os.environ.get("QUANTLAB_ASSET") or instance.get("asset") or "a_share"
        )
        port = coerce_service_port(
            self.port
            if self.port is not None
            else os.environ.get("QUANTLAB_PORT") or instance.get("port") or DEFAULT_UI_PORT,
            "port",
        )
        lan_port = coerce_service_port(
            self.lan_port
            if self.lan_port is not None
            else os.environ.get("QUANTLAB_LAN_PORT") or instance.get("lan_port") or DEFAULT_LAN_PORT,
            "lan_port",
        )
        if port == lan_port:
            raise ValueError("port and lan_port must be different")
        object.__setattr__(self, "host", allowed_service_host(self.host))
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "lan_port", lan_port)
        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "calibration_root", calibration_root)
        object.__setattr__(self, "raw_root", raw_root)
        object.__setattr__(self, "runtime_root", runtime_root)

    @property
    def asset_label(self) -> str:
        return asset_label(str(self.asset))

    @property
    def writable_roots(self) -> tuple[Path, ...]:
        return tuple(self.runtime_root / name for name in _WRITE_SUBDIRECTORIES)

    @property
    def database_path(self) -> Path:
        return self.runtime_root / "db/quantlab.sqlite3"

    @property
    def baselines_root(self) -> Path:
        return self.runtime_root / "baselines"

    def resolve_user_path(self, path: Path | str) -> Path:
        text = str(path).strip()
        if not text:
            raise ValueError("path is required")
        value = Path(path).expanduser()
        if not value.is_absolute():
            return (self.project_root / value).resolve()
        resolved = value.resolve()
        allowed = (self.data_root, self.calibration_root, self.raw_root, self.runtime_root)
        if _inside(resolved, allowed):
            return resolved
        relocated = self._relocate_foreign_path(value)
        return relocated if relocated is not None else resolved

    def _relocate_foreign_path(self, value: Path) -> Path | None:
        parts = value.parts
        start = 1 if value.is_absolute() else 0
        allowed = (self.data_root, self.calibration_root, self.raw_root, self.runtime_root)
        for index in range(start, len(parts)):
            candidate = (self.project_root / Path(*parts[index:])).resolve()
            if _inside(candidate, allowed):
                return candidate
        return None

    def display_path(self, path: Path | str) -> str:
        return posix_relative(self.resolve_user_path(path), self.project_root)

    def store_path(self, path: Path | str) -> str:
        return self.display_path(path)

    def is_read_path_allowed(self, path: Path | str) -> bool:
        return _inside(self.resolve_user_path(path), (self.data_root, self.calibration_root, self.raw_root))

    def is_write_path_allowed(self, path: Path | str) -> bool:
        return _inside(self.resolve_user_path(path), self.writable_roots)

    def require_read_path(self, path: Path | str) -> Path:
        resolved = self.resolve_user_path(path)
        if not _inside(resolved, (self.data_root, self.calibration_root, self.raw_root)):
            raise ValueError(f"path is outside allowed read roots: {self.display_path(resolved)}")
        return resolved

    def require_write_path(self, path: Path | str) -> Path:
        resolved = self.resolve_user_path(path)
        if not _inside(resolved, self.writable_roots):
            raise ValueError(f"path is outside allowed write roots: {self.display_path(resolved)}")
        return resolved

    def require_baseline_path(self, path: Path | str) -> Path:
        resolved = self.resolve_user_path(path)
        if not _inside(resolved, (self.baselines_root,)):
            raise ValueError(f"path is outside runtime baselines root: {self.display_path(resolved)}")
        return resolved

    def require_artifact_path(self, path: Path | str) -> Path:
        resolved = self.resolve_user_path(path)
        if not (
            _inside(resolved, (self.data_root, self.calibration_root, self.raw_root))
            or _inside(resolved, self.writable_roots)
        ):
            raise ValueError(f"path is outside allowed artifact roots: {self.display_path(resolved)}")
        return resolved

    def with_host(self, host: str) -> Settings:
        return replace(self, host=allowed_service_host(host))
