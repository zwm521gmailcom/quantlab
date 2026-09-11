"""Configuration and path-boundary enforcement for QuantLab."""

from __future__ import annotations

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


def allowed_service_host(host: str) -> str:
    text = str(host or "").strip()
    if text in {"127.0.0.1", "localhost"}:
        return "127.0.0.1"
    if text == "0.0.0.0":
        return "0.0.0.0"
    if _is_private_ipv4(text):
        return text
    raise ValueError("QuantLab only binds 127.0.0.1, 0.0.0.0, or a private LAN address")


@dataclass(frozen=True)
class Settings:
    """Resolved local paths and the LAN-reachable service setting."""

    project_root: Path = field(default_factory=_default_project_root)
    data_root: Path | None = None
    calibration_root: Path | None = None
    raw_root: Path | None = None
    runtime_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765

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
        object.__setattr__(self, "host", allowed_service_host(self.host))
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "calibration_root", calibration_root)
        object.__setattr__(self, "raw_root", raw_root)
        object.__setattr__(self, "runtime_root", runtime_root)

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
