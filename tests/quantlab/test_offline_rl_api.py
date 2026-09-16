from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.offline_rl.experiment import OfflineRlExperimentService
from quantlab.services.offline_rl.fingerprint import config_fingerprint


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


def _seed_results(settings: Settings) -> None:
    daily_rets = [0.01, 0.011, 0.009, 0.012]
    _write_result(
        settings,
        run_id="a2019",
        config=_base_config("A", 2019),
        metrics={"sharpe": 2.0, "excess_return": 0.2},
        equity_curve=_positive_curve(2019, daily_rets),
    )
    _write_result(
        settings,
        run_id="a2020",
        config=_base_config("A", 2020),
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


def _zero_bench(_dates) -> dict[str, float]:
    if not _dates:
        return {}
    return {str(day).replace("-", "")[:8]: 0.0 for day in _dates}


def _client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    _seed_results(settings)
    app = create_app(settings, database)
    app.state.offline_rl_service = OfflineRlExperimentService(
        settings,
        database,
        bench_rets_provider=_zero_bench,
    )
    return TestClient(app)


def test_preview_lists_scoreable_years_and_excludes_long_window(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/offline-rl/preview", params={"k": 8, "window": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["k"] == 8
    assert payload["window"] == 20
    assert payload["scoreable_years"] == [2020]
    excluded = {item["run_id"] for item in payload["excluded_long_windows"]}
    assert "long" in excluded
    assert payload["by_year"]["2019"] == 1
    assert payload["by_year"]["2020"] == 1
    assert len(payload["fingerprints"]) == 1
    assert config_fingerprint(_base_config("A", 2019)) in payload["fingerprints"]


def test_run_returns_verdict_and_writes_artifacts(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/offline-rl/run",
        json={"k": 8, "window": 2, "gamma": 0.5, "iterations": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "verdict" in payload
    assert isinstance(payload["verdict"]["passed"], bool)
    assert payload["scoreable_years"] == [2020]
    assert len(payload["years"]) == 1

    settings = _settings(tmp_path)
    run_path = settings.runtime_root / "results" / payload["run_id"] / "run.json"
    assert run_path.is_file()
    saved = json.loads(run_path.read_text(encoding="utf-8"))
    assert saved["config"]["kind"] == "offline_rl"


def test_progress_reports_completed_after_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    idle = client.get("/api/offline-rl/progress")
    assert idle.status_code == 200
    assert idle.json()["status"] == "idle"

    client.post("/api/offline-rl/run", json={"k": 8, "window": 2})

    progress = client.get("/api/offline-rl/progress")
    assert progress.status_code == 200
    assert progress.json()["status"] == "completed"
    assert progress.json()["percent"] == 100


def test_run_returns_409_when_busy(tmp_path: Path) -> None:
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
    app = create_app(settings, database)
    app.state.offline_rl_service = OfflineRlExperimentService(
        settings,
        database,
        bench_rets_provider=_zero_bench,
    )
    client = TestClient(app)

    response = client.post("/api/offline-rl/run", json={"k": 8, "window": 2})

    assert response.status_code == 409
    assert response.json()["error_code"] == "OFFLINE_RL_BUSY"


def test_preview_does_not_write_offline_rl_result(tmp_path: Path) -> None:
    client = _client(tmp_path)
    settings = _settings(tmp_path)
    before = list((settings.runtime_root / "results").iterdir())

    response = client.get("/api/offline-rl/preview", params={"k": 8, "window": 20})

    assert response.status_code == 200
    after = list((settings.runtime_root / "results").iterdir())
    assert {path.name for path in before} == {path.name for path in after}
