from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.factor_auto_mine import (
    AutoMineConfig,
    AutoMineError,
    _outcome,
    build_time_splits,
    formula_label,
    generate_candidates,
    mine_candidates,
)
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.factor_mining import FactorMiningService


def _setup(tmp_path: Path) -> tuple[Settings, Database, FactorRepository]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "features.parquet"
    rows = []
    for date, close, volume, future_return in zip(
        ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"],
        [10, 11, 12, 13, 14, 15],
        [100, 110, 120, 130, 140, 150],
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        strict=True,
    ):
        rows.append((date, "000001.SZ", float(close), float(volume), future_return))
        rows.append((date, "000002.SZ", float(close) + 1, float(volume) + 10, future_return + 0.01))
    pq.write_table(pa.table({"date": [r[0] for r in rows], "instrument": [r[1] for r in rows], "close": [r[2] for r in rows], "volume": [r[3] for r in rows], "future_return": [r[4] for r in rows], "eligible": [1] * len(rows)}), path)
    settings = Settings(project_root=tmp_path, data_root=data_root, calibration_root=tmp_path / "calibration", runtime_root=tmp_path / "runtime")
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds_features', '特征', 'published')")
        connection.execute("INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_features', 'v1', ?, 12, ?, '20240102', '20240109', 'published', 'passed')", (str(path), json.dumps(["date", "instrument", "close", "volume", "future_return"])))
    return settings, database, FactorRepository(settings, database)


def test_time_splits_are_disjoint_and_deterministic() -> None:
    config = AutoMineConfig(dataset_id="ds", dataset_version_id="v1", date_from="20240101", train_end="20240103", validation_end="20240105", date_to="20240109", seed=7)
    first = build_time_splits(config)
    assert first == build_time_splits(config)
    assert first["train"]["to"] < first["validation"]["from"] < first["test"]["from"]
    assert first["validation"]["to"] < first["test"]["from"]


def test_search_space_rejects_target_fields_and_future_operators() -> None:
    with pytest.raises(AutoMineError, match="target-only"):
        generate_candidates(["future_return", "close"], ["shift"], [1], seed=1)
    with pytest.raises(AutoMineError, match="future"):
        generate_candidates(["close"], ["lead"], [1], seed=1)


def test_windowless_operators_do_not_multiply_by_lookback() -> None:
    formulas = {item["formula"] for item in generate_candidates(["close"], ["identity", "cs_rank"], [1, 2, 5], seed=1, max_candidates=20)}
    assert formulas == {"close", "close.cs_rank(0)"}


def test_candidate_generation_is_deterministic_and_prunes_duplicate_and_correlated() -> None:
    kwargs = dict(fields=["close", "volume"], operators=["identity", "shift", "rolling_mean"], windows=[1, 2], seed=11, max_candidates=20, correlation_threshold=0.99)
    assert generate_candidates(**kwargs) == generate_candidates(**kwargs)
    candidates = generate_candidates(**kwargs)
    assert candidates
    assert len({item["dedupe_key"] for item in candidates}) == len(candidates)
    assert all(item["complexity"] <= 12 and "future_return" not in item["formula"] for item in candidates)


def test_candidate_search_records_rejection_reasons_and_keeps_test_out_of_selection(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        seed=3, max_candidates=20, correlation_threshold=0.9,
    )
    result = mine_candidates(settings, database, factors, config)
    assert result["splits"]["train"]["to"] < result["splits"]["validation"]["from"]
    assert result["splits"]["validation"]["to"] < result["splits"]["test"]["from"]
    assert result["rejections"]
    assert all(item["reason"] for item in result["rejections"])
    assert result["selection_period"] == "validation"
    assert result["selection_inputs"]["period"] == "validation"


def test_mining_records_real_metrics_for_all_three_periods_and_validation_decision(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        seed=3, max_candidates=4, min_validation_coverage=1.0,
    )
    result = mine_candidates(settings, database, factors, config)
    summary = result["research_run"]["summary"]
    assert summary["selection_period"] == "validation"
    assert summary["test_usage"] == "final_evaluation_only"
    assert summary["evaluations"]
    for evaluation in summary["evaluations"]:
        assert "test_result" not in evaluation
        assert "test" not in evaluation["period_metrics"]
        assert set(evaluation["period_metrics"]) <= {"train", "validation"}
        for metrics in evaluation["period_metrics"].values():
            assert {"rows", "finite_rows", "coverage", "rank_ic", "group_spread", "positive_spread_ratio"} <= set(metrics)
        assert evaluation["decision_period"] in {"train", "validation"}
    for item in summary["candidates"]:
        assert "test" not in item["period_metrics"]


def test_test_values_cannot_enter_selection_inputs(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        seed=3, max_candidates=4,
    )
    result = mine_candidates(settings, database, factors, config)
    summary = result["research_run"]["summary"]
    assert "test" not in summary["selection_inputs"]
    assert all("test" not in item["decision_inputs"] for item in summary["evaluations"])
    assert all("test_result" not in item for item in summary["evaluations"])
    assert all("test" not in (item.get("period_metrics") or {}) for item in summary["candidates"])


def test_mining_rejects_empty_market_slice(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        markets=("BJ",), max_candidates=2,
    )
    with pytest.raises(AutoMineError, match="没有行情"):
        mine_candidates(settings, database, factors, config)


def test_mining_keeps_selected_market_suffix(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        markets=("SZ",), fields=("close",), operators=("identity",), windows=(1,),
        max_candidates=2, min_validation_coverage=0.0,
    )
    result = mine_candidates(settings, database, factors, config)
    assert result["research_run"]["status"] == "completed"
    assert result["research_run"]["config"]["search"]["markets"] == ["SZ"]


def test_formula_label_uses_plain_chinese() -> None:
    assert formula_label("close") == "原始收盘"
    assert formula_label("hfq_close.rolling_std(5)") == "后复权收盘的5日波动"
    assert formula_label("close.cs_rank(0)") == "原始收盘当天截面排名"


def test_outcome_rejects_next_close_as_label_fallback() -> None:
    frame = pd.DataFrame(
        {
            "date": ["20240102", "20240103"],
            "instrument": ["000001.SZ", "000001.SZ"],
            "close": [10.0, 11.0],
        }
    )
    with pytest.raises(AutoMineError, match="后复权"):
        _outcome(frame)


def test_mining_uses_library_factor_aligned_to_market_panel(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    with database.transaction() as connection:
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('factor_named_close', '登记收盘', 'published')")
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, status, quality_status) "
            "VALUES ('factor_named_close', 'v1', 'ds_features', 'v1', 'close', ?, 'published', 'passed')",
            (json.dumps(["close"]),),
        )
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        fields=("factor_named_close",), operators=("identity",), windows=(1,),
        max_candidates=2, min_validation_coverage=0.0,
    )
    result = mine_candidates(settings, database, factors, config)
    formulas = [item["formula"] for item in result["research_run"]["summary"]["task_items"]]
    assert formulas == ["factor_named_close"]
    assert result["research_run"]["status"] == "completed"


def test_mining_writes_task_checklist_instead_of_library_factors(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(dataset_id="ds_features", dataset_version_id="v1", date_from="20240102", train_end="20240103", validation_end="20240105", date_to="20240109", seed=3, max_candidates=4)
    result = mine_candidates(settings, database, factors, config)
    assert result["research_run"]["status"] == "completed"
    assert result["research_run"]["research_type"] == "automatic"
    assert result["candidates"]
    for candidate in result["candidates"]:
        assert candidate["kept_in_task"] is True
        assert candidate["enabled"] is False
        assert "status" not in candidate
        assert candidate["generation_run_id"] == result["research_run"]["run_id"]
        assert candidate["dataset_version_id"] == "v1"
        assert candidate["formula"]
        assert candidate["formula_label"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM factor_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM factors").fetchone()[0] == 0
    summary = result["research_run"]["summary"]
    assert summary["task_items"]
    assert {item["decision"] for item in summary["task_items"]} <= {"accepted", "rejected"}


def test_enabling_task_candidates_inserts_only_checked_items_into_library(tmp_path: Path) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        seed=3, max_candidates=4, min_validation_coverage=0.0,
    )
    result = mine_candidates(settings, database, factors, config)
    kept = [item for item in result["research_run"]["summary"]["task_items"] if item["kept_in_task"]]
    assert kept
    selected = kept[0]["candidate_id"]
    enabled = FactorMiningService(settings, database, factors).enable(
        result["research_run"]["run_id"], [selected]
    )
    assert enabled["enabled_count"] == 1
    assert factors.list()["total"] == 1
    item = factors.list()["items"][0]
    assert item["origin"] == "automatic"
    assert item["status"] == "draft"
    assert item["quality"]["enabled_from_task"] is True
    assert item["generation_run_id"] == result["research_run"]["run_id"]
    leftover = {item["candidate_id"] for item in kept[1:]}
    assert leftover or factors.list()["total"] == 1
    calculation = enabled["items"][0].get("calculation") or {}
    assert calculation.get("status") == "completed"
    entity_id = enabled["items"][0]["enabled_entity_id"]
    factor_id = entity_id.removeprefix("factor_")
    latest = FactorCalculationService(settings, database).latest(factor_id=factor_id)
    assert latest is not None
    assert latest["status"] == "completed"
    assert "ic_mean" in latest
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM factor_calculation_runs").fetchone()[0] == 1
    again = FactorMiningService(settings, database, factors).enable(
        result["research_run"]["run_id"], [selected]
    )
    assert again["enabled_count"] == 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM factor_calculation_runs").fetchone()[0] == 1


def test_enable_keeps_library_row_when_analysis_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, database, factors = _setup(tmp_path)
    config = AutoMineConfig(
        dataset_id="ds_features", dataset_version_id="v1", date_from="20240102",
        train_end="20240103", validation_end="20240105", date_to="20240109",
        seed=3, max_candidates=4, min_validation_coverage=0.0,
    )
    result = mine_candidates(settings, database, factors, config)
    kept = [item for item in result["research_run"]["summary"]["task_items"] if item["kept_in_task"]]
    selected = kept[0]["candidate_id"]

    def fail_summary(self, factor_id, binding):  # noqa: ANN001
        raise ValueError("模拟分析失败")

    monkeypatch.setattr(FactorCalculationService, "_summary", fail_summary)
    enabled = FactorMiningService(settings, database, factors).enable(
        result["research_run"]["run_id"], [selected]
    )
    assert factors.list()["total"] == 1
    assert enabled["items"][0]["enabled_entity_id"]
    assert enabled["items"][0]["calculation"]["status"] == "failed"
    assert "模拟分析失败" in str(enabled["items"][0]["calculation"].get("error_message") or "")
    with database.connect() as connection:
        run = connection.execute("SELECT status, error_message FROM factor_calculation_runs").fetchone()
        factor = connection.execute("SELECT COUNT(*) FROM factors").fetchone()[0]
    assert factor == 1
    assert run["status"] == "failed"
    assert run["error_message"]


def test_mining_outcome_prefers_open_to_close_label() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "instrument": ["AAA.SZ"] * 4,
            "hfq_open": [10.0, 11.0, 12.0, 13.0],
            "hfq_close": [10.5, 11.5, 12.5, 13.5],
            "future_return": [0.99, 0.99, 0.99, 0.99],
        }
    )
    values = _outcome(frame)
    assert values.iloc[0] == pytest.approx(12.5 / 11.0 - 1)
