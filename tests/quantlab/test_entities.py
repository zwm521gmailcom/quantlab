from dataclasses import FrozenInstanceError

import pytest

from quantlab.domain.entities import (
    Artifact,
    BacktestRun,
    Dataset,
    DatasetVersion,
    FactorVersion,
    Model,
    ModelTrainingRun,
    ModelVersion,
    ResearchRun,
    Strategy,
    StrategyVersion,
)
from quantlab.domain.status import BacktestRunStatus, LifecycleStatus, RunStatus, transition


def test_entities_require_stable_ids() -> None:
    with pytest.raises(ValueError, match="entity_id"):
        Dataset(entity_id="", name="K线")
    with pytest.raises(ValueError, match="version_id"):
        DatasetVersion(entity_id="ds", version_id="", path="/tmp/x")


def test_version_entities_are_immutable_and_keep_lineage() -> None:
    version = FactorVersion(
        entity_id="factor_momentum_5",
        version_id="v1",
        dataset_id="ds_hfq_market_st_v1",
        dataset_version_id="20260830T173152Z-50e42e72",
        formula="hfq_close[t] / hfq_close[t-5 observations] - 1",
        input_fields=("hfq_close",),
    )
    assert version.dataset_version_id == "20260830T173152Z-50e42e72"
    with pytest.raises(FrozenInstanceError):
        version.version_id = "v2"


def test_all_core_entities_can_be_constructed() -> None:
    assert Model(entity_id="model_lgbm", name="LightGBM").entity_id == "model_lgbm"
    assert ModelVersion(entity_id="model_lgbm", version_id="v1").version_id == "v1"
    assert Strategy(entity_id="strategy_mom", name="Momentum").entity_id == "strategy_mom"
    assert (
        StrategyVersion(
            entity_id="strategy_mom",
            version_id="v1",
            factor_version_ids=("factor_momentum_5:v1",),
            model_version_id="model_lgbm:v1",
        ).model_version_id
        == "model_lgbm:v1"
    )
    assert ModelTrainingRun(run_id="20260902-120000-0001").run_id.endswith("0001")
    assert ResearchRun(run_id="20260902-120000-0002").run_id.endswith("0002")
    assert BacktestRun(run_id="20260902-120000-0003").run_id.endswith("0003")
    assert Artifact(artifact_id="artifact-1", run_id="20260902-120000-0003").artifact_id == "artifact-1"


@pytest.mark.parametrize("entity", [ModelTrainingRun, ResearchRun, BacktestRun])
def test_run_entities_reject_invalid_run_id_format(entity) -> None:
    with pytest.raises(ValueError, match="run_id"):
        entity(run_id="run-1")


def test_illegal_lifecycle_and_run_transitions_are_rejected() -> None:
    assert transition(LifecycleStatus.DRAFT, LifecycleStatus.VALIDATED) == LifecycleStatus.VALIDATED
    with pytest.raises(ValueError, match="not allowed"):
        transition(LifecycleStatus.DRAFT, LifecycleStatus.PUBLISHED)
    with pytest.raises(ValueError, match="not allowed"):
        transition(LifecycleStatus.PUBLISHED, LifecycleStatus.DRAFT)
    with pytest.raises(ValueError, match="not allowed"):
        transition(LifecycleStatus.DEPRECATED, LifecycleStatus.PUBLISHED)
    with pytest.raises(ValueError, match="not allowed"):
        transition(RunStatus.COMPLETED, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="not allowed"):
        transition(RunStatus.FAILED, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="not allowed"):
        transition(RunStatus.QUEUED, RunStatus.FAILED)
    assert transition(RunStatus.RUNNING, RunStatus.FAILED) == RunStatus.FAILED


def test_backtest_run_allows_failed_to_running() -> None:
    assert transition(BacktestRunStatus.FAILED, BacktestRunStatus.RUNNING) == BacktestRunStatus.RUNNING
    assert transition(BacktestRunStatus.QUEUED, BacktestRunStatus.RUNNING) == BacktestRunStatus.RUNNING
    with pytest.raises(ValueError, match="not allowed"):
        transition(BacktestRunStatus.QUEUED, BacktestRunStatus.COMPLETED)
    with pytest.raises(ValueError, match="not allowed"):
        transition(BacktestRunStatus.COMPLETED, BacktestRunStatus.RUNNING)
    with pytest.raises(ValueError, match="not allowed"):
        transition(RunStatus.FAILED, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="types do not match"):
        transition(RunStatus.FAILED, BacktestRunStatus.RUNNING)
