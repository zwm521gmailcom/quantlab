import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.factor_calculation import FactorCalculationService, build_factor_histogram, cross_section_diagnostics
from quantlab.services.factor_data import FEATURE_FIELDS


def _env(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "features.parquet"
    rows = []
    for date in ("20240102", "20240103", "20240104"):
        for symbol, factor, target in (
            ("000001.SZ", 0.5, 0.03),
            ("000002.SZ", 1.0, 0.06),
            ("600000.SH", -0.2, -0.04),
        ):
            rows.append({"trade_date": date, "ts_code": symbol, "momentum_5": factor, "future_return": target, "eligible": 1})
    pq.write_table(pa.Table.from_pylist(rows), path)
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds', '特征', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES ('ds', 'v1', ?, 9, ?, '20240102', '20240104', 'published', 'passed')",
            (str(path), '["trade_date","ts_code","momentum_5","future_return"]'),
        )
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('factor_momentum_5', '五日动量', 'published')")
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, status, quality_status) "
            "VALUES ('factor_momentum_5', 'v1', 'ds', 'v1', 'hfq_close[t]/hfq_close[t-5]-1', 'published', 'passed')",
        )
    return settings, database


def test_run_persists_calculation_history_and_latest(tmp_path: Path) -> None:
    settings, database = _env(tmp_path)
    service = FactorCalculationService(settings, database)

    first = service.run("momentum_5", date_from="20240102", date_to="20240104")

    assert first["calculation_id"] == 1
    assert first["serial_no"] == "#1"
    assert first["status"] == "completed"
    assert first["row_count"] == 9
    assert first["ic_mean"] is not None
    assert first["icir"] is None or first["icir"] > 0
    assert first["top_bottom_spread"] is not None and first["top_bottom_spread"] > 0
    assert first["monotonicity"] is not None
    assert "p50" in first["quantiles"]
    assert first["factor_mean"] is not None
    assert first["effective_days"] == 3
    assert len(first["histogram"]) == 60
    assert first["summary"]["histogram_range"]["basis"] == "p1_p99"
    assert first["summary"]["histogram_range"]["scale"] == "linear"
    assert first["summary"]["histogram_range"]["lower"] == first["quantiles"]["p1"]
    assert first["summary"]["histogram_range"]["upper"] == first["quantiles"]["p99"]
    assert "expected" in first["histogram"][0]
    assert first["summary"]["label"].startswith("t+1 open")

    second = service.run("momentum_5", date_from="20240103", date_to="20240104")
    assert second["calculation_id"] == 2
    history = service.list(factor_id="momentum_5")
    assert history["count"] == 2
    assert history["items"][0]["calculation_id"] == 2
    latest = service.latest(factor_id="momentum_5")
    assert latest is not None and latest["calculation_id"] == 2


def test_failed_run_keeps_error_and_period(tmp_path: Path) -> None:
    settings, database = _env(tmp_path)
    service = FactorCalculationService(settings, database)

    try:
        service.run("momentum_5", date_from="20250101", date_to="20250131")
    except ValueError as error:
        assert "所选日期范围内没有" in str(error)

    history = service.list(factor_id="momentum_5")
    assert history["count"] == 1
    assert history["items"][0]["status"] == "failed"
    assert history["items"][0]["error_message"]
    assert history["items"][0]["date_from"] == "20250101"


def _formula_env(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "market.parquet"
    rows = []
    dates = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109", "20240110", "20240111"]
    for offset, date in enumerate(dates):
        for rank, (symbol, base) in enumerate((("000001.SZ", 10.0), ("000002.SZ", 20.0), ("600000.SH", 30.0))):
            close = base + offset + rank * 0.25
            rows.append({
                "trade_date": date,
                "ts_code": symbol,
                "hfq_open": close * 0.99,
                "hfq_close": close,
                "close": close,
                "float_market_cap": float(1_000 + rank * 500 + offset),
                "turn": float(0.5 + rank * 0.2 + offset * 0.01),
                "eligible": 1,
            })
    pq.write_table(pa.Table.from_pylist(rows), path)
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds_market', '行情', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES ('ds_market', 'v1', ?, ?, ?, '20240102', '20240111', 'published', 'passed')",
            (str(path), len(rows), '["trade_date","ts_code","hfq_open","hfq_close","close","float_market_cap","turn"]'),
        )
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('factor_auto_demo_0', '原始收盘', 'draft')")
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, status, quality_status) "
            "VALUES ('factor_auto_demo_0', 'v1', 'ds_market', 'v1', 'close', ?, 'draft', 'needs_review')",
            ('["close"]',),
        )
    return settings, database


def test_formula_factor_outside_feature_fields_can_run(tmp_path: Path) -> None:
    settings, database = _formula_env(tmp_path)
    service = FactorCalculationService(settings, database)

    result = service.run("auto_demo_0", date_from="20240102", date_to="20240109")

    assert "auto_demo_0" not in FEATURE_FIELDS
    assert result["status"] == "completed"
    assert result["factor_entity_id"] == "factor_auto_demo_0"
    assert result["ic_mean"] is not None
    assert result["coverage"] is not None
    assert result["summary"]["label"].startswith("t+1 open")
    assert result["summary"]["daily_ic"]
    assert result["conditional_ic"]["by_float_market_cap"]["field"] == "float_market_cap"
    assert result["conditional_ic"]["by_turn"]["field"] == "turn"
    assert result["summary"]["conditional_ic"]["by_float_market_cap"]["status"] in {"available", "insufficient_data"}
    by_prefix = service.latest(factor_id="auto_demo_0")
    by_entity = service.latest(factor_id="factor_auto_demo_0")
    assert by_prefix is not None and by_entity is not None
    assert by_prefix["calculation_id"] == by_entity["calculation_id"] == result["calculation_id"]
    listed = service.list(factor_id="auto_demo_0")
    assert listed["count"] == 1


def test_formula_factor_respects_market_filter(tmp_path: Path) -> None:
    settings, database = _formula_env(tmp_path)
    service = FactorCalculationService(settings, database)

    all_markets = service.run("factor_auto_demo_0", date_from="20240102", date_to="20240109")
    sh_only = service.run("factor_auto_demo_0", date_from="20240102", date_to="20240109", markets=["SH"])

    assert all_markets["row_count"] > sh_only["row_count"]
    assert sh_only["status"] == "completed"


def test_formula_factor_failed_run_keeps_factor_row(tmp_path: Path) -> None:
    settings, database = _formula_env(tmp_path)
    service = FactorCalculationService(settings, database)

    with pytest.raises(ValueError):
        service.run("auto_demo_0", date_from="20250101", date_to="20250131")

    with database.connect() as connection:
        factor = connection.execute("SELECT entity_id FROM factors WHERE entity_id='factor_auto_demo_0'").fetchone()
        run = connection.execute("SELECT status, error_message FROM factor_calculation_runs").fetchone()
    assert factor is not None
    assert run["status"] == "failed"
    assert run["error_message"]


def _panel_frame() -> pd.DataFrame:
    rows = [
        ("20240102", "A", 1.0, 0.10),
        ("20240102", "B", 2.0, 0.20),
        ("20240102", "C", 3.0, 0.30),
        ("20240102", "D", 4.0, 0.40),
        ("20240102", "E", 5.0, 0.50),
        ("20240103", "F", 1.0, 0.10),
        ("20240103", "G", 2.0, 0.20),
        ("20240104", "A", 5.0, 0.10),
        ("20240104", "B", 4.0, 0.20),
        ("20240104", "C", 3.0, 0.30),
        ("20240104", "D", 2.0, 0.40),
        ("20240104", "E", 1.0, 0.50),
    ]
    return pd.DataFrame(rows, columns=["trade_date", "ts_code", "_factor", "_target"])


def test_cross_section_diagnostics_match_rank_ic_and_skip_thin_days() -> None:
    result = cross_section_diagnostics(_panel_frame())

    assert result["effective_days"] == 2
    assert result["group_days"] == 2
    assert [item["date"] for item in result["daily_ic"]] == ["20240102", "20240104"]
    assert result["daily_ic"][0]["count"] == 5
    assert result["daily_ic"][0]["ic"] == pytest.approx(1.0)
    assert result["daily_ic"][1]["ic"] == pytest.approx(-1.0)
    assert result["ic_mean"] == pytest.approx(0.0)
    assert result["ic_positive_ratio"] == pytest.approx(0.5)
    assert result["ic_std"] == pytest.approx(1.0)
    assert result["icir"] == pytest.approx(0.0)
    assert result["daily_top_bottom"][0]["spread"] == pytest.approx(0.4)
    assert result["daily_top_bottom"][1]["spread"] == pytest.approx(-0.4)
    assert result["top_bottom_spread"] == pytest.approx(0.0)
    assert result["monotonicity"] == pytest.approx(0.0)
    assert result["turnover_mean"] == pytest.approx(1.0)
    assert result["turnover_series"][0]["previous_count"] == 1
    assert result["turnover_series"][0]["current_count"] == 1
    cond = result["conditional_ic"]
    assert cond["by_float_market_cap"]["status"] == "missing_field"
    assert cond["by_float_market_cap"]["buckets"] == []
    assert cond["by_turn"]["status"] == "missing_field"
    assert cond["by_turn"]["buckets"] == []


def _legacy_cross_section(frame: pd.DataFrame) -> dict:
    daily_ic = []
    turnover_series = []
    top_bottom_series = []
    daily_top_bottom = []
    monotonic_series = []
    previous = None
    group_days = 0
    for date_value, group in frame.groupby("trade_date", sort=True):
        if len(group) < 3:
            continue
        date_text = str(date_value)
        ic = group["_factor"].rank(method="average").corr(group["_target"].rank(method="average"))
        if pd.notna(ic):
            daily_ic.append({"date": date_text, "ic": float(ic), "count": int(len(group))})
        try:
            bins = pd.qcut(group["_factor"], q=5, labels=False, duplicates="drop")
        except ValueError:
            continue
        grouped = group.assign(_group=bins).dropna(subset=["_group"])
        if len(grouped) < 3:
            continue
        means = grouped.groupby("_group", observed=True)["_target"].mean()
        if means.empty:
            continue
        group_days += 1
        ordered = means.sort_index()
        if len(ordered) >= 2:
            spread = float(ordered.iloc[-1] - ordered.iloc[0])
            top_bottom_series.append(spread)
            daily_top_bottom.append({"date": date_text, "spread": spread})
        if len(ordered) >= 3:
            rank_monotonic = pd.Series(range(1, len(ordered) + 1)).corr(pd.Series(ordered.values))
            if pd.notna(rank_monotonic):
                monotonic_series.append(float(rank_monotonic))
        top_index = int(means.index.max())
        members = set(grouped.loc[grouped["_group"] == top_index, "ts_code"].astype(str))
        if previous is not None:
            turnover = 1 - len(previous & members) / max(len(previous), 1)
            turnover_series.append(
                {
                    "date": date_text,
                    "turnover": float(turnover),
                    "previous_count": len(previous),
                    "current_count": len(members),
                }
            )
        previous = members
    ic_values = [item["ic"] for item in daily_ic]
    turnover_values = [item["turnover"] for item in turnover_series]
    ic_mean = float(pd.Series(ic_values).mean()) if ic_values else None
    ic_std = float(pd.Series(ic_values).std(ddof=0)) if len(ic_values) > 1 else None
    return {
        "daily_ic": daily_ic,
        "daily_top_bottom": daily_top_bottom,
        "turnover_series": turnover_series,
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "turnover_mean": float(pd.Series(turnover_values).mean()) if turnover_values else None,
        "top_bottom_spread": float(pd.Series(top_bottom_series).mean()) if top_bottom_series else None,
        "monotonicity": float(pd.Series(monotonic_series).mean()) if monotonic_series else None,
        "effective_days": len(daily_ic),
        "group_days": group_days,
    }


def test_cross_section_diagnostics_match_legacy_daily_loop() -> None:
    rows = []
    for day in range(12):
        date = f"202401{day + 1:02d}"
        n = 2 if day == 3 else 8
        for stock in range(n):
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": f"{stock:06d}.SZ",
                    "_factor": float((day * 3 + stock) % 7) + (0.01 if stock == 1 else 0.0),
                    "_target": float((stock - day) % 5) / 10.0,
                }
            )
    frame = pd.DataFrame(rows)
    got = cross_section_diagnostics(frame)
    expected = _legacy_cross_section(frame)
    assert got["effective_days"] == expected["effective_days"]
    assert got["group_days"] == expected["group_days"]
    assert got["daily_ic"] == expected["daily_ic"]
    assert got["daily_top_bottom"] == expected["daily_top_bottom"]
    assert got["turnover_series"] == expected["turnover_series"]
    assert got["ic_mean"] == pytest.approx(expected["ic_mean"])
    assert got["ic_std"] == pytest.approx(expected["ic_std"])
    assert got["turnover_mean"] == pytest.approx(expected["turnover_mean"])
    assert got["top_bottom_spread"] == pytest.approx(expected["top_bottom_spread"])
    assert got["monotonicity"] == pytest.approx(expected["monotonicity"])
    assert got["conditional_ic"]["by_float_market_cap"]["status"] == "missing_field"
    assert got["conditional_ic"]["by_turn"]["status"] == "missing_field"


def test_rerun_same_factor_keeps_equal_metrics_and_new_row(tmp_path: Path) -> None:
    settings, database = _env(tmp_path)
    service = FactorCalculationService(settings, database)

    first = service.run("momentum_5", date_from="20240102", date_to="20240104")
    second = service.run("momentum_5", date_from="20240102", date_to="20240104")

    assert second["calculation_id"] != first["calculation_id"]
    for key in (
        "ic_mean",
        "ic_positive_ratio",
        "ic_std",
        "icir",
        "coverage",
        "row_count",
        "effective_days",
        "top_bottom_spread",
        "monotonicity",
        "turnover_mean",
        "factor_mean",
        "factor_std",
    ):
        assert second[key] == first[key]
    assert second["summary"]["daily_ic"] == first["summary"]["daily_ic"]
    assert second["summary"]["daily_top_bottom"] == first["summary"]["daily_top_bottom"]
    assert second["summary"]["conditional_ic"] == first["summary"]["conditional_ic"]
    assert second["quantiles"] == first["quantiles"]


def test_conditional_ic_by_cap_and_turn_matches_within_bucket_spearman() -> None:
    rows = []
    for date in ("20240102", "20240104"):
        for index in range(15):
            cap_bucket = index // 3
            turn_bucket = index % 5
            rank = float(index % 3)
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": f"{index:06d}.SZ",
                    "_factor": rank,
                    "_target": rank,
                    "float_market_cap": float(10 + cap_bucket * 100 + index),
                    "turn": float(0.1 + turn_bucket + index * 0.001),
                }
            )
    frame = pd.DataFrame(rows)
    baseline = cross_section_diagnostics(frame.drop(columns=["float_market_cap", "turn"]))
    result = cross_section_diagnostics(frame)
    assert result["daily_ic"] == baseline["daily_ic"]
    assert result["ic_mean"] == baseline["ic_mean"]
    cap = result["conditional_ic"]["by_float_market_cap"]
    assert cap["status"] == "available"
    assert cap["field"] == "float_market_cap"
    assert [item["bucket"] for item in cap["buckets"]] == [1, 2, 3, 4, 5]
    assert cap["buckets"][0]["label"] == "Q1 小盘"
    assert cap["buckets"][4]["label"] == "Q5 大盘"
    for bucket in cap["buckets"]:
        assert bucket["effective_days"] == 2
        assert bucket["ic_mean"] == pytest.approx(1.0)
        assert bucket["ic_positive_ratio"] == pytest.approx(1.0)
        assert bucket["daily_ic"][0]["count"] == 3
    turn = result["conditional_ic"]["by_turn"]
    assert turn["status"] == "available"
    assert turn["field"] == "turn"
    assert turn["buckets"][0]["label"] == "Q1 低换手"
    assert turn["buckets"][4]["label"] == "Q5 高换手"
    for bucket in turn["buckets"]:
        assert bucket["effective_days"] == 2
        assert bucket["ic_mean"] == pytest.approx(1.0)


def test_histogram_uses_linear_bins_for_signed_values() -> None:
    values = pd.Series(np.random.default_rng(1).normal(0.0, 0.05, 8000))
    histogram, meta = build_factor_histogram(values)
    assert meta["scale"] == "linear"
    assert meta["basis"] == "p1_p99"
    assert len(histogram) == 60
    occupied = sum(1 for item in histogram if item["count"] > 40)
    assert occupied >= 8
    assert all("expected" in item for item in histogram)


def test_histogram_uses_log_bins_for_wide_positive_range() -> None:
    values = pd.Series(np.random.default_rng(2).lognormal(mean=3.8, sigma=0.9, size=12000))
    histogram, meta = build_factor_histogram(values)
    assert meta["scale"] == "log"
    assert meta["lower"] > 0
    assert meta["upper"] / meta["lower"] >= 10
    assert len(histogram) == 60
    occupied = sum(1 for item in histogram if item["count"] > 40)
    assert occupied >= 12
    assert histogram[0]["right"] > histogram[0]["left"]
    assert all(item["expected"] is None or item["expected"] >= 0 for item in histogram)


def test_histogram_uses_log_bins_when_wide_positive_has_negative_outliers() -> None:
    rng = np.random.default_rng(4)
    values = pd.Series(np.concatenate([
        rng.lognormal(mean=22.0, sigma=0.8, size=10000),
        np.full(200, -4.11e9),
    ]))
    histogram, meta = build_factor_histogram(values)
    assert float(values.quantile(0.01)) < 0
    assert meta["scale"] == "log"
    assert meta["basis"] == "p1_p99"
    assert meta["lower"] > 0
    occupied = sum(1 for item in histogram if item["count"] > 40)
    assert occupied >= 12
    peak = max(item["count"] for item in histogram)
    total = sum(item["count"] for item in histogram)
    assert peak / total < 0.4


def test_list_rebuilds_stale_full_range_histogram(tmp_path: Path) -> None:
    settings, database = _env(tmp_path)
    service = FactorCalculationService(settings, database)
    first = service.run("momentum_5", date_from="20240102", date_to="20240104")
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT summary_json FROM factor_calculation_runs WHERE calculation_id=?",
            (first["calculation_id"],),
        ).fetchone()
        summary = json.loads(row["summary_json"])
        summary["histogram"] = [{"left": -4.11e9, "right": 4.13e12, "count": 9}]
        summary.pop("histogram_range", None)
        connection.execute(
            "UPDATE factor_calculation_runs SET summary_json=? WHERE calculation_id=?",
            (json.dumps(summary), first["calculation_id"]),
        )
    listed = service.list(factor_id="momentum_5")
    item = listed["items"][0]
    assert item["histogram_range"]["basis"] == "p1_p99"
    assert item["histogram_range"]["scale"] == "linear"
    assert len(item["histogram"]) == 60
    assert "expected" in item["histogram"][0]
    assert abs(item["histogram"][0]["left"]) < 10
    assert abs(item["histogram"][-1]["right"]) < 10
