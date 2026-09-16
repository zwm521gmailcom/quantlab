from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.backtest_control import release_reservation, reserve_execution
from quantlab.services.offline_rl.experiment import OfflineRlExperimentService
from quantlab.services.offline_rl.fingerprint import config_fingerprint
from quantlab.services.offline_rl.fitted_q import FittedQ


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )


def _base_config(name: str, year: int) -> dict:
    return {
        "name": name,
        "kind": "factor_rank",
        "factor_versions": [{"field": f"sleeve_{name}"}],
        "top_n": 4,
        "weighting": "equal",
        "holding_days": 2,
        "rebalance_every": 1,
        "open_when_benchmark_gt_ma200": False,
        "model": {"kind": "factor_rank"},
        "test": {
            "date_from": f"{year}-01-02",
            "date_to": f"{year}-12-31",
            "filter": {"expressions": []},
        },
        "train": {"filter": {"expressions": []}},
    }


def _positive_curve(year: int, daily_rets: list[float]) -> list[dict]:
    equity = 100.0
    curve = [{"date": f"{year}0101", "equity": equity}]
    for idx, daily_ret in enumerate(daily_rets):
        day = f"{year}010{idx + 2}"
        equity *= 1.0 + daily_ret
        curve.append({"date": day, "equity": equity})
    return curve


def _write_result(
    settings: Settings,
    *,
    run_id: str,
    config: dict,
    metrics: dict,
    equity_curve: list[dict] | None = None,
) -> None:
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "status": "completed",
        "config": config,
        "metrics": metrics,
    }
    (folder / "run.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (folder / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    if equity_curve is not None:
        (folder / "equity_curve.json").write_text(
            json.dumps(equity_curve, ensure_ascii=False),
            encoding="utf-8",
        )


def _seed_results(settings: Settings) -> tuple[str, list[float]]:
    daily_rets = [0.01, 0.011, 0.009, 0.012]
    config_a_2019 = _base_config("A", 2019)
    config_a_2020 = _base_config("A", 2020)
    fp = config_fingerprint(config_a_2019)
    _write_result(
        settings,
        run_id="a2019",
        config=config_a_2019,
        metrics={"sharpe": 2.0, "excess_return": 0.2},
        equity_curve=_positive_curve(2019, daily_rets),
    )
    _write_result(
        settings,
        run_id="a2020",
        config=config_a_2020,
        metrics={"sharpe": 1.5, "excess_return": 0.15},
        equity_curve=_positive_curve(2020, daily_rets),
    )
    _write_result(
        settings,
        run_id="long",
        config=_base_config("long", 2020)
        | {"test": {"date_from": "2020-01-02", "date_to": "2026-08-31", "filter": {"expressions": []}}},
        metrics={"sharpe": 9.0, "excess_return": 9.0},
    )
    _write_result(
        settings,
        run_id="rl",
        config={"kind": "offline_rl", "test": {"date_from": "2019-01-02", "date_to": "2019-12-31"}},
        metrics={"sharpe": 8.0},
    )
    return fp, daily_rets


def _zero_bench(_dates) -> dict[str, float]:
    if not _dates:
        return {}
    return {str(day).replace("-", "")[:8]: 0.0 for day in _dates}


def test_preview_excludes_long_window_and_lists_scoreable_years(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fp, _ = _seed_results(settings)
    service = OfflineRlExperimentService(settings, Database(settings.database_path))

    preview = service.preview(k=8, window=20)

    assert preview["scoreable_years"] == [2020]
    excluded = {item["run_id"] for item in preview["excluded_long_windows"]}
    assert "long" in excluded
    assert preview["by_year"]["2019"] == 1
    assert preview["by_year"]["2020"] == 1
    assert len(preview["fingerprints"]) == 1
    assert "actions_by_year" in preview
    assert preview["actions_by_year"]["2020"] == [fp]


def test_run_returns_verdict_and_writes_offline_rl_artifacts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    _seed_results(settings)
    service = OfflineRlExperimentService(
        settings,
        database,
        bench_rets_provider=_zero_bench,
    )

    result = service.run(k=8, window=2, gamma=0.5, iterations=5)

    assert "verdict" in result
    assert isinstance(result["verdict"]["passed"], bool)
    assert result["scoreable_years"] == [2020]
    assert len(result["years"]) == 1
    assert result["years"][0]["year"] == 2020
    assert result["years"][0]["sharpe_policy"] is not None
    assert result["years"][0]["sharpe_replay"] is not None
    assert result["years"][0]["action_summary"]

    run_path = settings.runtime_root / "results" / result["run_id"] / "run.json"
    assert run_path.is_file()
    saved = json.loads(run_path.read_text(encoding="utf-8"))
    assert saved["config"]["kind"] == "offline_rl"
    assert "verdict" in saved["config"]
    year_policy = saved["config"]["years"][0]["policy"]
    assert "daily_actions" in year_policy
    assert len(year_policy["daily_actions"]) > 0
    assert (settings.runtime_root / "results" / result["run_id"] / "metrics.json").is_file()

    progress = service.progress()
    assert progress["status"] == "completed"
    assert progress["percent"] == 100


def test_run_raises_busy_when_plan_running(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    _seed_results(settings)
    stamp = "2026-09-14T12:00:00+00:00"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO backtest_plans(plan_id, name, status, closed, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("plan-busy", "busy", "running", 0, stamp, stamp),
        )
    service = OfflineRlExperimentService(
        settings,
        database,
        bench_rets_provider=_zero_bench,
    )

    with pytest.raises(RuntimeError, match="busy"):
        service.run(k=8, window=2)


def test_run_raises_busy_when_execution_reserved(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    _seed_results(settings)
    service = OfflineRlExperimentService(
        settings,
        database,
        bench_rets_provider=_zero_bench,
    )

    reserve_execution("plan:busy")
    try:
        with pytest.raises(RuntimeError, match="busy"):
            service.run(k=8, window=2)
    finally:
        release_reservation()


def test_preview_does_not_train(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_results(settings)
    service = OfflineRlExperimentService(settings, Database(settings.database_path))
    fit_calls: list[list] = []

    def _spy_fit(self, transitions):  # type: ignore[no-untyped-def]
        fit_calls.append(transitions)
        return None

    monkeypatch.setattr(FittedQ, "fit", _spy_fit)

    service.preview(k=8, window=20)

    assert fit_calls == []
