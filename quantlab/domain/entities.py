"""Small immutable entities shared by the foundation repositories."""

from __future__ import annotations

from dataclasses import dataclass, field

from .identifiers import validate_run_id
from .status import LifecycleStatus, QualityStatus, RunStatus


def _require(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class Dataset:
    entity_id: str
    name: str
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")


@dataclass(frozen=True)
class DatasetVersion:
    entity_id: str
    version_id: str
    path: str
    row_count: int | None = None
    fields: tuple[str, ...] = ()
    date_min: str | None = None
    date_max: str | None = None
    status: LifecycleStatus = LifecycleStatus.DRAFT
    quality_status: QualityStatus = QualityStatus.PASSED

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")
        _require(self.version_id, "version_id")


@dataclass(frozen=True)
class Factor:
    entity_id: str
    name: str
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")


@dataclass(frozen=True)
class FactorVersion:
    entity_id: str
    version_id: str
    dataset_id: str
    dataset_version_id: str
    formula: str
    input_fields: tuple[str, ...] = ()
    source: str = ""
    direction: str = ""
    frequency: str = ""
    missing_policy: str = ""
    pit_policy: str = ""
    status: LifecycleStatus = LifecycleStatus.DRAFT
    quality_status: QualityStatus = QualityStatus.PASSED

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")
        _require(self.version_id, "version_id")
        _require(self.dataset_id, "dataset_id")
        _require(self.dataset_version_id, "dataset_version_id")
        _require(self.formula, "formula")


@dataclass(frozen=True)
class Model:
    entity_id: str
    name: str
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")


@dataclass(frozen=True)
class ModelVersion:
    entity_id: str
    version_id: str
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    factor_version_ids: tuple[str, ...] = ()
    parameters: dict[str, object] = field(default_factory=dict)
    status: LifecycleStatus = LifecycleStatus.DRAFT
    quality_status: QualityStatus = QualityStatus.PASSED

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")
        _require(self.version_id, "version_id")


@dataclass(frozen=True)
class Strategy:
    entity_id: str
    name: str
    status: LifecycleStatus = LifecycleStatus.DRAFT

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")


@dataclass(frozen=True)
class StrategyVersion:
    entity_id: str
    version_id: str
    factor_version_ids: tuple[str, ...] = ()
    model_version_id: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)
    status: LifecycleStatus = LifecycleStatus.DRAFT
    quality_status: QualityStatus = QualityStatus.PASSED

    def __post_init__(self) -> None:
        _require(self.entity_id, "entity_id")
        _require(self.version_id, "version_id")


@dataclass(frozen=True)
class ModelTrainingRun:
    run_id: str
    status: RunStatus = RunStatus.QUEUED
    model_entity_id: str | None = None
    model_version_id: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    status: RunStatus = RunStatus.QUEUED

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)


@dataclass(frozen=True)
class BacktestRun:
    run_id: str
    status: RunStatus = RunStatus.QUEUED
    strategy_id: str | None = None
    strategy_version_id: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    run_id: str
    display_name: str = ""
    original_name: str = ""
    artifact_role: str = ""
    path: str = ""
    content_hash: str = ""
    size_bytes: int = 0

    def __post_init__(self) -> None:
        _require(self.artifact_id, "artifact_id")
        validate_run_id(self.run_id)
