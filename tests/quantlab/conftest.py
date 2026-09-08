from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _keep_unit_tests_off_spawn(monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_BUCKET_POOL", "thread")
    monkeypatch.setenv("QUANTLAB_BUCKET_WORKERS", "1")
    monkeypatch.setenv("QUANTLAB_BACKTEST_INLINE", "1")


@pytest.fixture(autouse=True)
def _reset_compute_settings_cache() -> None:
    from quantlab.services.settings import COMPUTE_DEFAULTS, apply_compute

    apply_compute(dict(COMPUTE_DEFAULTS))
    yield
    apply_compute(dict(COMPUTE_DEFAULTS))


@pytest.fixture(autouse=True)
def _reset_backtest_plan_runtime() -> None:
    from quantlab.services.backtest_control import reset_execution_gate
    from quantlab.services.backtest_plan import reset_plan_runtime

    reset_execution_gate()
    reset_plan_runtime()
    yield
    reset_plan_runtime()
    reset_execution_gate()
