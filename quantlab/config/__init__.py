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


def _default_project_root() -> Path:
    configured = os.environ.get("QUANTLAB_PROJECT_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _absolute(path: Path | str) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise ValueError(f"QuantLab paths must be absolute: {value}")
    return value.resolve()


def _default_authoritative_root(project_root: Path, directory_name: str) -> Path:
    if project_root.parent.name == ".worktrees":
        return project_root.parent.parent / directory_name
    return project_root / directory_name


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = path.resolve()
    return any(candidate == root or root in candidate.parents for root in roots)


@dataclass(frozen=True)
class Settings:
    """Resolved local paths and the loopback-only service setting."""

    project_root: Path = field(default_factory=_default_project_root)
    data_root: Path | None = None
    calibration_root: Path | None = None
    raw_root: Path | None = None
    runtime_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765

    def __post_init__(self) -> None:
        project_root = _absolute(self.project_root)
        data_root = _absolute(
            self.data_root
            or os.environ.get("QUANTLAB_DATA_ROOT")
            or project_root / "data"
        )
        calibration_root = _absolute(
            self.calibration_root
            or os.environ.get("QUANTLAB_CALIBRATION_ROOT")
            or project_root / "data" / "calibration"
        )
        raw_root = _absolute(
            self.raw_root
            or os.environ.get("QUANTLAB_RAW_ROOT")
            or project_root / "data" / "raw"
        )
        runtime_root = _absolute(
            self.runtime_root
            or os.environ.get("QUANTLAB_RUNTIME_ROOT")
            or project_root / "quantlab_runtime"
        )
        if self.host != "127.0.0.1":
            raise ValueError("QuantLab only accepts host 127.0.0.1")
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

    def is_read_path_allowed(self, path: Path | str) -> bool:
        return _inside(Path(path), (self.data_root, self.calibration_root, self.raw_root))

    def is_write_path_allowed(self, path: Path | str) -> bool:
        return _inside(Path(path), self.writable_roots)

    def require_read_path(self, path: Path | str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not self.is_read_path_allowed(resolved):
            raise ValueError(f"path is outside allowed read roots: {resolved}")
        return resolved

    def require_write_path(self, path: Path | str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not self.is_write_path_allowed(resolved):
            raise ValueError(f"path is outside allowed write roots: {resolved}")
        return resolved

    def require_baseline_path(self, path: Path | str) -> Path:
        resolved = Path(path).expanduser().resolve()
        baseline_root = self.baselines_root.resolve()
        if not _inside(resolved, (baseline_root,)):
            raise ValueError(f"path is outside runtime baselines root: {resolved}")
        return resolved

    def require_artifact_path(self, path: Path | str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not (
            self.is_read_path_allowed(resolved) or self.is_write_path_allowed(resolved)
        ):
            raise ValueError(f"path is outside allowed artifact roots: {resolved}")
        return resolved

    def with_host(self, host: str) -> Settings:
        if host != "127.0.0.1":
            raise ValueError("QuantLab only accepts host 127.0.0.1")
        return replace(self, host=host)
