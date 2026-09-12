from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.repositories.database import Database
from quantlab.services.result_archive import ResultArchiveService

from test_result_archive import setup_archive


def _add_dag(database: Database, run_id: str, *, failed: bool = False) -> None:
    steps = [
        (1, "snapshot_validation", "completed", "2026-09-02T12:00:00.000+00:00", "2026-09-02T12:00:00.120+00:00"),
        (2, "model_training", "completed", "2026-09-02T12:00:00.120+00:00", "2026-09-02T12:00:53.120+00:00"),
        (3, "prediction", "completed", "2026-09-02T12:00:53.120+00:00", "2026-09-02T12:00:53.180+00:00"),
        (4, "positions", "completed", "2026-09-02T12:00:53.180+00:00", "2026-09-02T12:01:24.180+00:00"),
        (5, "execution", "failed" if failed else "completed", "2026-09-02T12:01:24.180+00:00", "2026-09-02T12:01:24.240+00:00"),
        (6, "metrics", "skipped" if failed else "completed", None if failed else "2026-09-02T12:01:24.240+00:00", None if failed else "2026-09-02T12:01:24.300+00:00"),
    ]
    with database.transaction() as connection:
        for ordinal, name, status, started_at, finished_at in steps:
            connection.execute(
                "INSERT INTO backtest_steps(run_id, ordinal, step_name, status, started_at, finished_at, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, ordinal, name, status, started_at, finished_at, "撮合失败" if failed and status == "failed" else None),
            )


def test_run_record_aggregates_dag_config_metrics_and_artifacts_without_trades_scan(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    _add_dag(database, "20260902-120000-0001")

    record = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert record is not None
    assert record["status_name"] == "已完成"
    assert record["metrics"]["return"]["value"] == 0.25
    assert [step["status"] for step in record["dag"]] == ["completed"] * 6
    assert record["configuration"]["name"] == "Alpha动量"
    assert record["artifacts"][0]["display_name"] == "回测指标"
    assert "trades" not in record
    assert record["actions"]["copy_config_url"].endswith("/copy-config")
    training = record["dag"][1]
    assert training["step_name"] == "model_training"
    assert training["step_label"] == "模型训练"
    assert training["duration_ms"] == 53000
    assert training["duration_display"] == "53.0 秒"
    assert record["dag"][2]["duration_ms"] == 60
    assert record["dag"][2]["duration_display"] == "0.06 秒"
    assert record["timing"]["duration_ms"] == 60000
    assert record["timing"]["duration_display"] == "1 分"


def test_run_record_exposes_failure_reason_partial_metrics_and_skipped_steps(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    _add_dag(database, "20260902-120001-0002", failed=True)
    with database.transaction() as connection:
        connection.execute("UPDATE backtest_runs SET error_message=? WHERE run_id=?", ("撮合失败：涨停未成交", "20260902-120001-0002"))

    record = ResultArchiveService(settings, database).get("20260902-120001-0002")
    assert record is not None
    assert record["status"] == "failed"
    assert record["failure"]["reason"] == "撮合失败：涨停未成交"
    assert record["failure"]["failed_step"] == "execution"
    assert record["metrics"]["return"]["display"] == "未生成"
    assert record["actions"]["retryable"] is True
    assert record["actions"]["execute_url"].endswith("/execute")
    assert record["dag"][-1]["status"] == "skipped"
    assert record["dag"][-1]["duration_ms"] is None
    assert record["dag"][-1]["duration_display"] == "跳过"
    assert record["dag"][4]["duration_ms"] == 60
    assert record["dag"][4]["duration_display"] == "0.06 秒"


def test_backtest_step_records_millisecond_start_and_keeps_running_unfinished(tmp_path: Path) -> None:
    from quantlab.services.backtest_job import BacktestJobService, _now

    settings, database = setup_archive(tmp_path)
    stamp = _now()
    assert "." in stamp
    job = BacktestJobService(settings, database)
    job._prepare_steps("20260902-120002-0003")
    job._step("20260902-120002-0003", 2, "running")
    with database.connect() as connection:
        row = dict(
            connection.execute(
                "SELECT status, started_at, finished_at FROM backtest_steps WHERE run_id=? AND ordinal=2",
                ("20260902-120002-0003",),
            ).fetchone()
        )
    assert row["status"] == "running"
    assert row["started_at"]
    assert "." in row["started_at"]
    assert row["finished_at"] is None
    job._step("20260902-120002-0003", 2, "completed")
    with database.connect() as connection:
        done = dict(
            connection.execute(
                "SELECT status, started_at, finished_at FROM backtest_steps WHERE run_id=? AND ordinal=2",
                ("20260902-120002-0003",),
            ).fetchone()
        )
    assert done["status"] == "completed"
    assert done["finished_at"]
    assert done["started_at"] == row["started_at"]
    assert done["finished_at"] >= done["started_at"]


def test_run_record_api_and_page_use_run_record_not_workbench(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    _add_dag(database, "20260902-120000-0001")
    client = TestClient(create_app(settings, database))

    response = client.get("/api/backtests/runs/20260902-120000-0001")
    assert response.status_code == 200
    assert response.json()["dag"][0]["step_name"] == "snapshot_validation"
    page = client.get("/backtests/runs/20260902-120000-0001")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    html = page.text
    js = Path("quantlab/web/assets/backtest/run-record.js").read_text(encoding="utf-8")
    source = html + js
    assert "运行记录" in html
    assert "仓位与交易规则" not in html
    assert "保存为模板" not in html
    assert "可追溯性检查" in html
    assert "模块耗时" in html
    assert "总耗时" in html
    assert response.json()["dag"][1]["duration_display"] == "53.0 秒"
    assert "运行目录" in html
    assert "重新回测" in html
    assert "删除" in html
    assert "handleScale: false" in js
    assert "handleScroll: false" in js
    assert 'id="equity-legend"' in html
    assert "function bindChartLegend" in js
    assert "equity-legend-toggle" in source
    assert "setData(visible ? [] : entry.points)" in js


def test_run_record_fills_benchmark_curve_from_index_daily(tmp_path: Path) -> None:
    settings, database = setup_archive(tmp_path)
    raw = settings.raw_root / "index_daily"
    raw.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "ts_code": ["000300.SH", "000300.SH"],
                "trade_date": ["20200102", "20200103"],
                "close": [4000.0, 4400.0],
            }
        ),
        raw / "index_daily_000300_SH.parquet",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE backtest_runs SET config_json=?, metrics_json=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "name": "Alpha动量",
                        "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"},
                        "benchmark": "000300.SH",
                        "initial_capital": 1_000_000,
                    }
                ),
                json.dumps(
                    {
                        "return": 0.25,
                        "benchmark_return": None,
                        "equity_curve": [
                            {"date": "20200102", "equity": 1_000_000},
                            {"date": "20200103", "equity": 1_250_000},
                        ],
                        "benchmark_curve": [],
                    }
                ),
                "20260902-120000-0001",
            ),
        )
    record = ResultArchiveService(settings, database).get("20260902-120000-0001")
    assert record is not None
    curve = record["metrics_raw"]["benchmark_curve"]
    assert len(curve) >= 2
    assert curve[0]["equity"] == pytest.approx(1_000_000)
    assert curve[-1]["equity"] == pytest.approx(1_100_000)
    assert record["metrics"]["benchmark_return"]["value"] == pytest.approx(0.10)
    assert record["metrics"]["excess_return"]["value"] == pytest.approx(0.15)
    listed = ResultArchiveService(settings, database).list(status="completed")
    assert listed["items"][0]["metrics"]["return"]["value"] == pytest.approx(0.25)
    assert listed["items"][0]["metrics"]["benchmark_return"]["value"] is None
