from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.factor_manual import (
    ExpressionError,
    ManualFactorService,
    grouped_rolling,
    parse_expression,
)


def _setup(tmp_path: Path) -> tuple[Settings, Database, FactorRepository]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "features.parquet"
    pq.write_table(
        pa.table(
            {
                "date": [
                    "20240102",
                    "20240103",
                    "20240104",
                    "20240105",
                    "20240108",
                    "20240109",
                ],
                "instrument": ["000001.SZ"] * 3 + ["000002.SZ"] * 3,
                "close": [10.0, 11.0, 12.0, 20.0, 21.0, 22.0],
                "volume": [100.0, 110.0, 120.0, 200.0, 210.0, 220.0],
            }
        ),
        path,
    )
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('ds_features', '特征', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_features', 'v1', ?, 6, ?, '20240102', '20240109', 'published', 'passed')",
            (str(path), json.dumps(["date", "instrument", "close", "volume"])),
        )
    return settings, database, FactorRepository(settings, database)


def test_expression_parser_rejects_code_execution_unknown_fields_and_future_shift() -> (
    None
):
    with pytest.raises(ExpressionError, match="not allowed"):
        parse_expression("__import__('os').system('whoami')", {"close"})
    with pytest.raises(ExpressionError, match="unknown field"):
        parse_expression("close + secret", {"close"})
    with pytest.raises(ExpressionError, match="non-negative"):
        parse_expression("close.shift(-1)", {"close"})
    with pytest.raises(ExpressionError, match="not allowed"):
        parse_expression("close ** 2", {"close"})


def test_expression_parser_accepts_registered_fields_and_reports_window() -> None:
    parsed = parse_expression("close / close.shift(2) - 1", {"close"})
    assert parsed.input_fields == ["close"]
    assert parsed.max_window == 2
    assert parsed.window_mode == "per_instrument_observation"


def test_expression_parser_accepts_grouped_transforms() -> None:
    parsed = parse_expression("close.ts_zscore(5) + close.cs_rank(0)", {"close"})
    assert parsed.input_fields == ["close"]
    assert parsed.max_window == 5


def test_manual_preview_evaluates_relative_smooth_and_cross_section_transforms(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "panel.parquet"
    dates = ["20240102", "20240103", "20240104"]
    pq.write_table(
        pa.table(
            {
                "date": dates * 2,
                "instrument": ["000001.SZ"] * 3 + ["000002.SZ"] * 3,
                "close": [10.0, 11.0, 12.0, 20.0, 21.0, 22.0],
            }
        ),
        path,
    )
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('ds_panel', '面板', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_panel', 'v1', ?, 6, ?, '20240102', '20240104', 'published', 'passed')",
            (str(path), json.dumps(["date", "instrument", "close"])),
        )
    service = ManualFactorService(settings, database, FactorRepository(settings, database))
    body = {
        "dataset_id": "ds_panel",
        "dataset_version_id": "v1",
        "date_from": "20240102",
        "date_to": "20240104",
        "max_rows": 20,
    }
    rank = service.preview({**body, "formula": "close.cs_rank(0)", "input_fields": ["close"]})
    by_date = {}
    for row in rank["sample"]:
        by_date.setdefault(str(row["date"]).replace("-", ""), []).append(row["value"])
    assert rank["status"] == "valid"
    assert all(len(values) == 2 and min(values) < max(values) for values in by_date.values())
    zscore = service.preview({**body, "formula": "close.ts_zscore(3)", "input_fields": ["close"]})
    bias = service.preview({**body, "formula": "close.rolling_bias(3)", "input_fields": ["close"]})
    ewm = service.preview({**body, "formula": "close.ewm_mean(3)", "input_fields": ["close"]})
    ts_rank = service.preview({**body, "formula": "close.ts_rank(3)", "input_fields": ["close"]})
    assert zscore["missing_rows"] == 4
    assert bias["missing_rows"] == 4
    assert ewm["missing_rows"] == 4
    assert ts_rank["missing_rows"] == 4
    last_close = [row for row in zscore["sample"] if row["instrument"] == "000001.SZ"][-1]
    assert last_close["value"] == pytest.approx(1.0)
    last_bias = [row for row in bias["sample"] if row["instrument"] == "000001.SZ"][-1]
    assert last_bias["value"] == pytest.approx(12.0 / 11.0 - 1)
    last_rank = [row for row in ts_rank["sample"] if row["instrument"] == "000001.SZ"][-1]
    assert last_rank["value"] == pytest.approx(1.0)


def test_manual_preview_is_grouped_by_instrument_and_converts_invalid_values_to_missing(
    tmp_path: Path,
) -> None:
    settings, database, factors = _setup(tmp_path)
    service = ManualFactorService(settings, database, factors)
    preview = service.preview(
        {
            "dataset_id": "ds_features",
            "dataset_version_id": "v1",
            "formula": "close / close.shift(1) - 1",
            "input_fields": ["close"],
            "date_from": "20240102",
            "date_to": "20240109",
            "max_rows": 20,
        }
    )
    assert preview["status"] == "valid"
    assert preview["rows"] == 6
    assert preview["missing_rows"] == 2
    assert preview["sample"][1]["value"] == pytest.approx(0.1)
    assert preview["sample"][4]["value"] == pytest.approx(0.05)


def test_manual_draft_save_diagnose_and_publish_require_real_quality_gate(
    tmp_path: Path,
) -> None:
    settings, database, factors = _setup(tmp_path)
    service = ManualFactorService(settings, database, factors)
    draft = service.create_draft(
        {
            "name": "一步动量",
            "category": "技术",
            "dataset_id": "ds_features",
            "dataset_version_id": "v1",
            "formula": "close / close.shift(1) - 1",
            "input_fields": ["close"],
            "direction": "positive",
            "missing_policy": "drop",
            "pit_policy": "as-of trade_date",
            "pit_lineage": {
                "rule": "as-of trade_date",
                "snapshot": "dataset:v1",
                "window_mode": "per_instrument_observation",
            },
            "date_from": "20240102",
            "date_to": "20240109",
            "author": "tester",
        }
    )
    assert draft["status"] == "draft"
    assert draft["pit_lineage"]["window_mode"] == "per_instrument_observation"
    with pytest.raises(ValueError, match="quality"):
        service.publish(draft["factor_entity_id"], draft["factor_version_id"])
    diagnosed = service.diagnose(draft["factor_entity_id"], draft["factor_version_id"])
    assert diagnosed["quality_status"] == "warning"
    assert diagnosed["research_run"]["status"] == "completed"
    with pytest.raises(ValueError, match="quality"):
        service.publish(draft["factor_entity_id"], draft["factor_version_id"])


def test_manual_draft_rejects_external_data_path_and_target_field(
    tmp_path: Path,
) -> None:
    settings, database, factors = _setup(tmp_path)
    service = ManualFactorService(settings, database, factors)
    with pytest.raises(ValueError, match="target-only"):
        service.preview(
            {
                "dataset_id": "ds_features",
                "dataset_version_id": "v1",
                "formula": "close + future_return",
                "input_fields": ["close", "future_return"],
            }
        )
    with pytest.raises(ValueError, match="target-only"):
        service.preview(
            {
                "dataset_id": "ds_features",
                "dataset_version_id": "v1",
                "formula": "close",
                "input_fields": ["close", "future_return"],
            }
        )


def test_manual_draft_patch_revalidates_formula_and_keeps_draft_status(
    tmp_path: Path,
) -> None:
    settings, database, factors = _setup(tmp_path)
    service = ManualFactorService(settings, database, factors)
    draft = service.create_draft(
        {
            "name": "动量",
            "category": "技术",
            "dataset_id": "ds_features",
            "dataset_version_id": "v1",
            "formula": "close.shift(1)",
            "input_fields": ["close"],
            "direction": "positive",
            "missing_policy": "drop",
            "pit_policy": "as-of trade_date",
        }
    )
    updated = service.update_draft(
        draft["factor_entity_id"],
        draft["factor_version_id"],
        {"formula": "close / close.shift(1) - 1"},
    )
    assert updated["formula"] == "close / close.shift(1) - 1"
    assert updated["status"] == "draft"
    with pytest.raises(ExpressionError, match="unknown field"):
        service.update_draft(
            draft["factor_entity_id"],
            draft["factor_version_id"],
            {"formula": "unknown"},
        )


def test_manual_factor_api_exposes_preview_draft_diagnose_publish_and_page(
    tmp_path: Path,
) -> None:
    settings, database, _ = _setup(tmp_path)
    client = TestClient(create_app(settings, database))
    body = {
        "name": "一步动量",
        "category": "技术",
        "dataset_id": "ds_features",
        "dataset_version_id": "v1",
        "formula": "close / close.shift(1) - 1",
        "input_fields": ["close"],
        "direction": "positive",
        "missing_policy": "drop",
        "pit_policy": "as-of trade_date",
        "date_from": "20240102",
        "date_to": "20240109",
    }
    assert client.post("/api/factor-drafts/preview", json=body).status_code == 200
    created = client.post("/api/factor-drafts", json=body)
    assert created.status_code == 201
    draft = created.json()
    assert client.get("/factors/new").status_code == 200
    diagnosed = client.post(
        f"/api/factor-drafts/{draft['factor_entity_id']}/{draft['factor_version_id']}/diagnose"
    )
    assert diagnosed.status_code == 200
    assert (
        client.post(
            f"/api/factor-drafts/{draft['factor_entity_id']}/{draft['factor_version_id']}/publish"
        ).status_code
        == 400
    )


def _date_major_panel(tmp_path: Path, *, symbols: int, days: int) -> Path:
    dates = [f"2024{index:04d}" for index in range(1, days + 1)]
    codes = [f"{index:06d}.SZ" for index in range(symbols)]
    table = pa.table(
        {
            "trade_date": [date for date in dates for _ in codes],
            "ts_code": [code for _ in dates for code in codes],
            "hfq_close": [
                10.0 + symbol + day / 100
                for day in range(days)
                for symbol in range(symbols)
            ],
        }
    )
    path = tmp_path / "data" / "canonical.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


def test_manual_preview_keeps_full_history_for_sampled_instruments_on_date_major_panel(
    tmp_path: Path,
) -> None:
    path = _date_major_panel(tmp_path, symbols=80, days=40)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('ds_canonical_market', '标准行情', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_canonical_market', 'current', ?, ?, ?, '20240001', '20240040', 'published', 'passed')",
            (
                str(path),
                80 * 40,
                json.dumps(["trade_date", "ts_code", "hfq_close"]),
            ),
        )
    service = ManualFactorService(settings, database, FactorRepository(settings, database))
    preview = service.preview(
        {
            "dataset_id": "ds_canonical_market",
            "dataset_version_id": "current",
            "formula": "hfq_close / hfq_close.rolling_mean(20) - 1",
            "input_fields": ["hfq_close"],
            "max_rows": 5000,
        }
    )
    instruments = {row["instrument"] for row in preview["sample"]}
    assert preview["status"] == "valid"
    assert len(instruments) <= 40
    assert preview["coverage"] >= 0.5


def test_manual_ma_deviation_draft_can_pass_quality_gate_and_publish(
    tmp_path: Path,
) -> None:
    path = _date_major_panel(tmp_path, symbols=8, days=400)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('ds_canonical_market', '标准行情', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) VALUES ('ds_canonical_market', 'current', ?, ?, ?, '20240001', '20240080', 'published', 'passed')",
            (
                str(path),
                12 * 80,
                json.dumps(["trade_date", "ts_code", "hfq_close"]),
            ),
        )
    service = ManualFactorService(settings, database, FactorRepository(settings, database))
    draft = service.create_draft(
        {
            "name": "二十日均线偏离",
            "category": "技术",
            "dataset_id": "ds_canonical_market",
            "dataset_version_id": "current",
            "formula": "hfq_close / hfq_close.rolling_mean(20) - 1",
            "input_fields": ["hfq_close"],
            "direction": "positive",
            "missing_policy": "drop",
        }
    )
    diagnosed = service.diagnose(draft["factor_entity_id"], draft["factor_version_id"])
    assert diagnosed["quality_status"] == "passed"
    published = service.publish(draft["factor_entity_id"], draft["factor_version_id"])
    assert published["status"] == "published"


def test_manual_factor_fields_api_lists_only_registered_dataset_columns(
    tmp_path: Path,
) -> None:
    settings, database, _ = _setup(tmp_path)
    client = TestClient(create_app(settings, database))
    response = client.get(
        "/api/factor-drafts/fields",
        params={"dataset_id": "ds_features", "dataset_version_id": "v1"},
    )
    assert response.status_code == 200
    assert response.json()["fields"] == ["date", "instrument", "close", "volume"]


def _rolling_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instrument": ["000001.SZ"] * 6 + ["000002.SZ"] * 6,
            "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 20.0, 22.0, 21.0, 25.0, 24.0, 26.0],
        }
    )


def _pandas_rolling(frame: pd.DataFrame, window: int, how: str) -> pd.Series:
    rolled = frame.groupby("instrument", sort=False, group_keys=False)["close"].rolling(window, min_periods=window)
    result = {
        "mean": rolled.mean,
        "std": rolled.std,
        "max": rolled.max,
        "min": rolled.min,
        "sum": rolled.sum,
        "rank": lambda: rolled.rank(pct=True),
    }[how]()
    if isinstance(result.index, pd.MultiIndex):
        result = result.reset_index(level=0, drop=True)
    return result.sort_index()


def test_grouped_rolling_matches_pandas_on_sorted_and_interleaved_rows() -> None:
    frame = _rolling_panel()
    for how in ("mean", "std", "max", "min", "sum", "rank"):
        got = grouped_rolling(frame["close"], frame["instrument"], 3, how)
        expected = _pandas_rolling(frame, 3, how)
        pd.testing.assert_series_equal(got.sort_index(), expected, check_names=False, atol=1e-9, rtol=1e-9)

    interleaved = pd.DataFrame(
        {
            "instrument": ["A", "B", "A", "B", "A", "B"],
            "close": [1.0, 10.0, 2.0, 20.0, 3.0, 30.0],
        }
    )
    got = grouped_rolling(interleaved["close"], interleaved["instrument"], 2, "mean")
    assert pd.isna(got.iloc[0]) and pd.isna(got.iloc[1])
    assert got.iloc[2] == pytest.approx(1.5)
    assert got.iloc[3] == pytest.approx(15.0)
    assert got.iloc[4] == pytest.approx(2.5)
    assert got.iloc[5] == pytest.approx(25.0)
