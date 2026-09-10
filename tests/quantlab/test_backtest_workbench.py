from pathlib import Path
import json
import multiprocessing
import time
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.backtest_workbench import BacktestWorkbenchService
from quantlab.services.backtest_job import BacktestJobService


def setup_env(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "features.parquet"
    pq.write_table(
        pa.table(
            {
                "date": ["20200101", "20200102", "20200103", "20200104"],
                "instrument": ["600000.SH", "000001.SZ", "000001.SZ", "000001.SZ"],
                "hfq_open": [20.0, 10.0, 10.0, 10.0],
                "hfq_close": [19.0, 10.0, 12.0, 14.0],
                "open": [20.0, 10.0, 10.0, 10.0],
                "close": [19.0, 10.0, 12.0, 14.0],
                "momentum_5": [0.2, 1.0, 1.0, 1.0],
                "st_status": [1, 0, 0, 0],
                "suspended": [False, False, False, False],
                "up_limit": [21.0, 11.0, 11.0, 15.0],
                "down_limit": [19.0, 9.0, 9.0, 5.0],
            }
        ),
        path,
    )
    s = Settings(
        project_root=tmp_path,
        data_root=data,
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    db = Database(s.database_path)
    db.initialize()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO datasets VALUES ('ds','特征','published',CURRENT_TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO dataset_versions(entity_id,version_id,path,row_count,fields_json,status,quality_status) VALUES ('ds','v1',?,4,?,'published','passed')",
            (
                str(path),
                json.dumps(
                    [
                        "date",
                        "instrument",
                        "hfq_open",
                        "hfq_close",
                        "open",
                        "close",
                        "momentum_5",
                        "st_status",
                        "suspended", "up_limit", "down_limit",
                    ]
                ),
            ),
        )
        c.execute("INSERT INTO factors VALUES ('f','动量','技术','published')")
        c.execute(
            "INSERT INTO factor_versions(entity_id,version_id,dataset_id,dataset_version_id,formula,status,quality_status) VALUES ('f','v1','ds','v1','momentum_5','published','passed')"
        )
        c.execute("INSERT INTO models VALUES ('m','模型','published')")
        c.execute(
            "INSERT INTO model_versions(entity_id,version_id,dataset_id,dataset_version_id,parameters_json,status,quality_status) VALUES ('m','v1','ds','v1',?,'published','passed')",
            (json.dumps({"kind": "factor_rank", "hyperparameters": {}}),),
        )
        c.execute("INSERT INTO strategies VALUES ('s','策略','published')")
        c.execute(
            "INSERT INTO strategy_versions(entity_id,version_id,model_entity_id,model_version_id,parameters_json,status,quality_status) VALUES ('s','v1','m','v1','{}','published','passed')"
        )
    return s, db


def config(token="tok"):
    return {
        "submission_token": token,
        "name": "动量回测",
        "dataset_id": "ds",
        "dataset_version_id": "v1",
        "strategy_entity_id": "s",
        "strategy_version_id": "v1",
        "factor_versions": [{"factor_id": "f", "version_id": "v1"}],
        "model": {"entity_id": "m", "version_id": "v1"},
        "stock_scope": "中国A股（SH/SZ）",
        "train": {
            "date_from": "2020-01-01",
            "date_to": "2020-01-01",
            "filter": {"st_status": 0, "suspended": False},
        },
        "test": {
            "date_from": "2020-01-02",
            "date_to": "2020-01-03",
            "filter": {"st_status": 0, "suspended": False},
        },
        "top_n": 10,
        "weighting": "equal",
        "rebalance_every": 2,
        "signal_time": "close",
        "buy_price": "hfq_open",
        "sell_price": "hfq_close",
        "buy_fee_rate": 0.0003,
        "buy_fee_minimum": 5,
        "sell_fee_rate": 0.0005,
        "sell_fee_minimum": 5,
        "stamp_tax_rate": 0,
        "slippage": 0,
        "initial_capital": 1000000,
        "benchmark": "000300.SH",
        "missing_policy": {"valuation": "drop", "technical": "drop"},
        "holding_days": 2,
        "kind": "factor_rank",
        "hyperparameters": {"number_of_trees": 5, "max_bins": 511},
    }


def test_config_normalizes_and_freezes_and_rejects_invalid_scope(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    out = svc.validate(config())
    assert out["stock_scope"] == "中国A股（SH/SZ）" and out["buy_fee_minimum"] == 5.0
    assert out["model"]["name"] == "模型"
    with pytest.raises(ValueError):
        svc.validate({**config(), "stock_scope": "中国A股（SH/SZ/BJ）"})


def test_preview_reports_stages_and_submit_is_idempotent(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    preview = svc.preview(config())
    assert preview["stages"][-1]["remaining_count"] == 2
    one = svc.submit(config())
    two = svc.submit(config())
    assert one["run_id"] == two["run_id"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 1


def test_preview_accepts_canonical_column_names(tmp_path):
    s, db = setup_env(tmp_path)
    path = s.data_root / "features.parquet"
    pq.write_table(
        pa.table(
            {
                "trade_date": ["20200101", "20200102", "20200103", "20200104"],
                "ts_code": ["600000.SH", "000001.SZ", "000001.SZ", "000001.SZ"],
                "st_status": [1, 0, 0, 0],
                "is_suspended": [0, 0, 0, 0],
                "close": [19.0, 10.0, 12.0, 14.0],
                "low": [18.0, 9.0, 11.0, 13.0],
                "up_limit": [21.0, 11.0, 11.0, 15.0],
                "down_limit": [19.0, 9.0, 9.0, 5.0],
            }
        ),
        path,
    )
    preview = BacktestWorkbenchService(s, db).preview(
        {
            **config(),
            "pretrade_filters": {
                "stock": ["st_status == 0", "is_suspended == 0", "close > low"],
                "benchmark": [],
            },
        }
    )
    assert preview["stages"][0]["remaining_count"] == 3
    assert preview["stages"][-1]["remaining_count"] == 2


def test_save_draft_persists_config_without_creating_a_run(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    saved = svc.save_draft(config())
    assert saved["revision"] == 1
    assert saved["draft_id"].startswith("draft-")
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM backtest_drafts").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0
        stored = c.execute(
            "SELECT config_json FROM backtest_drafts WHERE draft_id=?",
            (saved["draft_id"],),
        ).fetchone()
    assert json.loads(stored["config_json"])["benchmark"] == "000300.SH"


def test_save_draft_updates_revision_and_get_draft_round_trips(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    draft_id = svc.save_draft(config())["draft_id"]
    updated = svc.save_draft(
        {**config("updated"), "name": "更新后的回测"},
        draft_id=draft_id,
        expected_revision=1,
    )
    assert updated["revision"] == 2
    fetched = svc.get_draft(draft_id)
    assert fetched is not None
    assert fetched["complete"] is True
    assert fetched["config"]["name"] == "更新后的回测"
    assert fetched["factor_version_ids"] == [{"factor_id": "f", "version_id": "v1"}]


def test_api_page_and_frozen_config(tmp_path):
    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    assert client.get("/backtests/new").status_code == 200
    r = client.post("/api/backtests/validate", json=config())
    assert r.status_code == 200
    r = client.post("/api/backtests", json=config())
    assert r.status_code == 201
    assert r.json()["config"]["benchmark"] == "000300.SH"


def test_api_saves_and_loads_full_draft_without_submitting(tmp_path):
    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    saved = client.post(
        "/api/backtests/drafts",
        json={"config": config()},
    )
    assert saved.status_code == 201
    draft_id = saved.json()["draft_id"]
    loaded = client.get(f"/api/backtests/drafts/{draft_id}")
    assert loaded.status_code == 200
    assert loaded.json()["complete"] is True
    assert loaded.json()["config"]["name"] == "动量回测"
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0


def test_invalid_config_is_a_value_error_not_key_error(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    with pytest.raises(ValueError, match="missing config: train, test"):
        svc.validate({k: v for k, v in config().items() if k not in {"train", "test"}})


def test_api_returns_400_for_incomplete_config(tmp_path):
    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    response = client.post("/api/backtests/validate", json={"name": "不完整"})
    assert response.status_code == 400


def test_api_executes_and_status_is_queryable(tmp_path):
    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    created = client.post("/api/backtests", json=config())
    run_id = created.json()["run_id"]
    executed = client.post(f"/api/backtests/{run_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    status = client.get(f"/api/backtests/{run_id}/status")
    assert status.status_code == 200
    assert len(status.json()["steps"]) == 6


def test_api_force_stop_kills_worker_and_keeps_health_up(tmp_path):
    from quantlab.services import backtest_control

    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    created = client.post("/api/backtests", json=config())
    run_id = created.json()["run_id"]
    with db.transaction() as connection:
        connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.execute(
            "UPDATE backtest_steps SET status='running' WHERE run_id=? AND ordinal=1",
            (run_id,),
        )
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=time.sleep, args=(30,))
    proc.start()
    backtest_control.register_worker(run_id, proc)
    try:
        stopped = client.post(f"/api/backtests/{run_id}/stop")
        assert stopped.status_code == 200
        body = stopped.json()
        assert body["status"] == "failed"
        assert "强行停止" in str(body.get("error_message") or body.get("error") or "")
        proc.join(timeout=2)
        assert not proc.is_alive()
        assert client.get("/api/health").status_code == 200
        status = client.get(f"/api/backtests/{run_id}/status")
        assert status.json()["status"] == "failed"
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1)


def test_api_force_stop_missing_run_is_404(tmp_path):
    s, db = setup_env(tmp_path)
    client = TestClient(create_app(s, db))
    response = client.post("/api/backtests/20260909-010000-0001/stop")
    assert response.status_code == 404
    assert response.json()["error_code"] == "BACKTEST_NOT_FOUND"


def test_execute_runs_ordered_dag_and_keeps_artifacts(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    run = svc.submit({
        **config(),
        "test": {"date_from": "2020-01-02", "date_to": "2020-01-04", "filter": {"st_status": 0, "suspended": False}},
    })
    job = BacktestJobService(s, db)

    result = job.execute(run["run_id"])

    assert result["status"] == "completed"
    assert [step["step_name"] for step in result["steps"]] == [
        "snapshot_validation", "model_training", "prediction", "positions",
        "execution", "metrics",
    ]
    assert all(step["status"] == "completed" for step in result["steps"])
    assert result["artifacts"]
    assert result["metrics"]["trade_count"] == 1
    assert "rank_ic" in result["metrics"]
    assert "ndcg_at_10" in result["metrics"]
    assert "monthly_ranking" in result["metrics"]
    assert result["trades"][0]["signal_date"] == "20200102"
    assert result["trades"][0]["buy_date"] == "20200103"
    assert result["trades"][0]["sell_date"] == "20200104"


def test_execution_applies_lot_slippage_fees_stamp_tax_and_unfilled_policy(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    run = svc.submit({**config(), "test": {"date_from": "20200102", "date_to": "20200104", "filter": {"st_status": 0, "suspended": False}}, "top_n": 1, "slippage": 0.01, "stamp_tax_rate": 0.001, "lot_size": 100, "unfilled_policy": "keep_cash"})
    job = BacktestJobService(s, db)

    result = job.execute(run["run_id"])
    trade = result["trades"][0]
    assert trade["quantity"] == 98900
    assert trade["buy_price"] == pytest.approx(10.10)
    assert trade["sell_price"] == pytest.approx(13.86)
    assert trade["buy_fee"] >= 5
    assert trade["sell_fee"] > 5
    assert trade["stamp_tax"] > 0


def test_execution_failure_short_circuits_and_retains_completed_artifact(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    run = svc.submit(config())
    job = BacktestJobService(s, db)
    path = s.data_root / "features.parquet"
    path.write_bytes(path.read_bytes() + b"tampered")

    result = job.execute(run["run_id"])

    assert result["status"] == "failed"
    assert result["error_message"]
    assert result["steps"][0]["status"] == "failed"
    assert all(step["status"] == "skipped" for step in result["steps"][1:])
    assert result["artifacts"] == []


def test_limit_block_is_recorded_as_unfilled_or_cancelled(tmp_path):
    s, db = setup_env(tmp_path)
    path = s.data_root / "features.parquet"
    table = pq.read_table(path)
    opens = table["open"].to_pylist()
    hfq_opens = table["hfq_open"].to_pylist()
    opens[2] = 11.0
    hfq_opens[2] = 11.0
    table = table.set_column(table.column_names.index("open"), "open", pa.array(opens))
    table = table.set_column(table.column_names.index("hfq_open"), "hfq_open", pa.array(hfq_opens))
    pq.write_table(table, path)
    svc = BacktestWorkbenchService(s, db)
    raw = {**config(), "test": {"date_from": "20200102", "date_to": "20200104", "filter": {"st_status": 0, "suspended": False}}, "top_n": 1, "slippage": 0.2, "unfilled_policy": "keep_cash"}
    run = svc.submit(raw)
    result = BacktestJobService(s, db).execute(run["run_id"])
    assert result["trades"][0]["status"] == "unfilled"
    assert result["metrics"]["unfilled_count"] == 1

    raw_cancel = {**raw, "submission_token": "cancel-token", "unfilled_policy": "cancel"}
    cancel_run = svc.submit(raw_cancel)
    cancelled = BacktestJobService(s, db).execute(cancel_run["run_id"])
    assert cancelled["trades"] == []


def test_fill_benchmark_metrics_is_exported_and_fills_excess_return(tmp_path):
    from quantlab.services.backtest_job import fill_benchmark_metrics

    out = fill_benchmark_metrics(
        {"return": 0.10, "benchmark_return": 0.04},
        {"benchmark": "000300.SH", "test": {"date_from": "2020-01-01", "date_to": "2020-12-31"}, "initial_capital": 1_000_000},
        raw_root=tmp_path / "missing-raw",
    )
    assert out["return"] == pytest.approx(0.10)
    assert out["benchmark_return"] == pytest.approx(0.04)
    assert out["excess_return"] == pytest.approx(0.06)


def test_validate_accepts_unadjusted_open_and_close_without_strategy_ids(tmp_path):
    s, db = setup_env(tmp_path)
    raw = {k: v for k, v in config().items() if k not in {"strategy_entity_id", "strategy_version_id"}}
    raw["buy_price"] = "open"
    raw["sell_price"] = "close"
    out = BacktestWorkbenchService(s, db).validate(raw)
    assert out["buy_price"] == "open"
    assert out["sell_price"] == "close"


def test_validate_keeps_validation_window_between_train_and_test(tmp_path):
    s, db = setup_env(tmp_path)
    raw = config()
    raw["kind"] = "lightgbm_tree"
    raw["train"] = {
        "date_from": "2020-01-01",
        "date_to": "2020-01-01",
        "stock_scope": "沪市（SH）",
        "filter": {"st_status": 0, "suspended": False},
    }
    raw["validation"] = {
        "date_from": "2020-01-02",
        "date_to": "2020-01-02",
        "stock_scope": "沪市（SH）",
        "filter": {"st_status": 0, "suspended": False},
    }
    raw["test"] = {
        "date_from": "2020-01-03",
        "date_to": "2020-01-04",
        "stock_scope": "深市（SZ）",
        "filter": {"st_status": 0, "suspended": False},
    }
    out = BacktestWorkbenchService(s, db).validate(raw)
    assert out["validation"]["date_from"] == "2020-01-02"
    assert out["validation"]["date_to"] == "2020-01-02"
    assert out["train"]["fit_date_to"] == "2020-01-01"
    assert out["train"]["date_to"] == "2020-01-02"
    assert out["hyperparameters"]["validation_date_from"] == "2020-01-02"


def test_validate_rejects_overlapping_validation_window(tmp_path):
    s, db = setup_env(tmp_path)
    raw = config()
    raw["validation"] = {
        "date_from": "2020-01-01",
        "date_to": "2020-01-02",
        "filter": {"st_status": 0, "suspended": False},
    }
    with pytest.raises(ValueError, match="validation"):
        BacktestWorkbenchService(s, db).validate(raw)


def test_filter_drops_names_when_limit_columns_are_missing() -> None:
    import pandas as pd
    from quantlab.services.backtest_job import BacktestJobService

    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102"],
            "instrument": ["600000.SH", "000001.SZ"],
            "close": [10.0, 10.0],
            "low": [9.0, 9.0],
            "st_status": [0, 0],
            "suspended": [False, False],
        }
    )
    notes: list[str] = []
    out = BacktestJobService._filter(
        frame,
        {
            "date_from": "2020-01-02",
            "date_to": "2020-01-02",
            "filter": {"st_status": 0, "suspended": False, "skip_limit_close": True},
        },
        notes,
    )
    assert out.empty
    assert any("涨跌停" in item for item in notes)


def test_filter_applies_independent_stock_scope() -> None:
    import pandas as pd
    from quantlab.services.backtest_job import BacktestJobService

    frame = pd.DataFrame(
        {
            "date": ["20200101", "20200101", "20200102", "20200102"],
            "instrument": ["600000.SH", "000001.SZ", "600000.SH", "000001.SZ"],
            "close": [10.0, 10.0, 10.0, 10.0],
            "low": [9.0, 9.0, 9.0, 9.0],
            "st_status": [0, 0, 0, 0],
            "suspended": [False, False, False, False],
            "up_limit": [11.0, 11.0, 11.0, 11.0],
            "down_limit": [8.0, 8.0, 8.0, 8.0],
        }
    )
    filt = {"st_status": 0, "suspended": False, "close_gt_low": False, "skip_limit_close": False}
    sh = BacktestJobService._filter(
        frame,
        {"date_from": "2020-01-01", "date_to": "2020-01-02", "stock_scope": "沪市（SH）", "filter": filt},
    )
    sz = BacktestJobService._filter(
        frame,
        {"date_from": "2020-01-01", "date_to": "2020-01-02", "stock_scope": "深市（SZ）", "filter": filt},
    )
    both = BacktestJobService._filter(
        frame,
        {"date_from": "2020-01-01", "date_to": "2020-01-02", "stock_scope": "中国A股（SH/SZ）", "filter": filt},
    )
    assert set(sh["instrument"]) == {"600000.SH"}
    assert set(sz["instrument"]) == {"000001.SZ"}
    assert set(both["instrument"]) == {"600000.SH", "000001.SZ"}

    by_ts = frame.rename(columns={"instrument": "ts_code"})
    sh_ts = BacktestJobService._filter(
        by_ts,
        {"date_from": "2020-01-01", "date_to": "2020-01-02", "stock_scope": "沪市（SH）", "filter": filt},
    )
    assert set(sh_ts["ts_code"]) == {"600000.SH"}


def test_suspended_event_is_unfilled_under_keep_cash(tmp_path):
    s, db = setup_env(tmp_path)
    path = s.data_root / "features.parquet"
    table = pq.read_table(path)
    values = table["suspended"].to_pylist()
    values[2] = True
    table = table.set_column(table.column_names.index("suspended"), "suspended", pa.array(values))
    pq.write_table(table, path)
    svc = BacktestWorkbenchService(s, db)
    run = svc.submit({**config(), "test": {"date_from": "20200102", "date_to": "20200104", "filter": {"st_status": 0}}, "top_n": 1, "unfilled_policy": "keep_cash"})
    result = BacktestJobService(s, db).execute(run["run_id"])
    assert result["trades"][0]["status"] == "unfilled"


def test_validate_defaults_random_seed_into_hyperparameters(tmp_path):
    s, db = setup_env(tmp_path)
    out = BacktestWorkbenchService(s, db).validate(config())
    assert out["hyperparameters"]["random_seed"] == 123
    out = BacktestWorkbenchService(s, db).validate({**config(), "hyperparameters": {"number_of_trees": 5, "random_seed": 99}})
    assert out["hyperparameters"]["random_seed"] == 99


def test_submit_records_test_usage_without_requiring_ack(tmp_path):
    s, db = setup_env(tmp_path)
    svc = BacktestWorkbenchService(s, db)
    first = svc.submit(config("tok-a"))
    BacktestJobService(s, db).execute(first["run_id"])
    reused = svc.submit(config("tok-b"))
    assert reused["run_id"] != first["run_id"]
    assert reused["config"]["test_usage_count"] >= 1


def test_filter_applies_expression_list():
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102"],
            "instrument": ["600000.SH", "000001.SZ"],
            "close": [10.0, 5.0],
            "low": [9.0, 4.0],
            "st_status": [0, 0],
            "suspended": [False, False],
            "up_limit": [11.0, 11.0],
            "down_limit": [8.0, 3.0],
        }
    )
    out = BacktestJobService._filter(
        frame,
        {
            "date_from": "2020-01-02",
            "date_to": "2020-01-02",
            "filter": {
                "st_status": 0,
                "suspended": False,
                "close_gt_low": False,
                "skip_limit_close": False,
                "expressions": ["close > 8"],
            },
        },
    )
    assert list(out["instrument"]) == ["600000.SH"]


def test_filter_keeps_only_benchmark_allowed_dates():
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200103"],
            "instrument": ["000001.SZ", "000001.SZ"],
            "close": [10.0, 10.0],
            "low": [9.0, 9.0],
            "st_status": [0, 0],
            "suspended": [False, False],
            "up_limit": [11.0, 11.0],
            "down_limit": [8.0, 8.0],
        }
    )
    out = BacktestJobService._filter(
        frame,
        {
            "date_from": "2020-01-02",
            "date_to": "2020-01-03",
            "filter": {
                "st_status": 0,
                "suspended": False,
                "close_gt_low": False,
                "skip_limit_close": False,
                "allowed_dates": ["20200103"],
            },
        },
    )
    assert list(out["date"].map(str)) == ["20200103"]


def test_filter_uses_validation_expressions_after_cutoff():
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": ["20200101", "20200101", "20200102"],
            "instrument": ["000001.SZ", "000002.SZ", "000001.SZ"],
            "close": [10.0, 5.0, 5.0],
            "low": [9.0, 4.0, 4.0],
            "st_status": [0, 0, 0],
            "suspended": [False, False, False],
            "up_limit": [11.0, 11.0, 11.0],
            "down_limit": [8.0, 3.0, 3.0],
        }
    )
    out = BacktestJobService._filter(
        frame,
        {
            "date_from": "2020-01-01",
            "date_to": "2020-01-02",
            "filter": {
                "st_status": 0,
                "suspended": False,
                "close_gt_low": False,
                "skip_limit_close": False,
                "expressions": ["close > 8"],
                "validation_date_from": "20200102",
                "validation_expressions": ["close > 1"],
            },
        },
    )
    assert list(zip(out["date"].map(str), out["instrument"], strict=True)) == [
        ("20200101", "000001.SZ"),
        ("20200102", "000001.SZ"),
    ]
