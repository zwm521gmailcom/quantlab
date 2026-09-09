"""Explicit lifecycle and run-state transition rules."""

from __future__ import annotations

from enum import Enum
from typing import TypeVar


class LifecycleStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class QualityStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


_LIFECYCLE_TRANSITIONS = {
    LifecycleStatus.DRAFT: {LifecycleStatus.VALIDATED},
    LifecycleStatus.VALIDATED: {LifecycleStatus.PUBLISHED},
    LifecycleStatus.PUBLISHED: {LifecycleStatus.DEPRECATED},
    LifecycleStatus.DEPRECATED: set(),
}
_RUN_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
}
_BACKTEST_RUN_TRANSITIONS = {
    BacktestRunStatus.QUEUED: {BacktestRunStatus.RUNNING},
    BacktestRunStatus.RUNNING: {BacktestRunStatus.COMPLETED, BacktestRunStatus.FAILED},
    BacktestRunStatus.COMPLETED: set(),
    BacktestRunStatus.FAILED: {BacktestRunStatus.RUNNING},
}

_TRANSITIONS = {
    LifecycleStatus: _LIFECYCLE_TRANSITIONS,
    RunStatus: _RUN_TRANSITIONS,
    BacktestRunStatus: _BACKTEST_RUN_TRANSITIONS,
}

StatusType = TypeVar("StatusType", LifecycleStatus, RunStatus, BacktestRunStatus)


def transition(current: StatusType, target: StatusType) -> StatusType:
    """Validate one state change and return the target state."""

    if type(current) is not type(target):
        raise ValueError("status transition types do not match")
    allowed = _TRANSITIONS[type(current)]
    if target not in allowed[current]:
        raise ValueError(f"status transition {current.value} -> {target.value} is not allowed")
    return target
