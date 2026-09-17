from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.canonical_factor_pack import (
    CANONICAL_FACTOR_PACK,
    CanonicalFactorPackService,
    PackFactor,
    last_year_window,
    pack_entity_id,
    verify_pack_run,
)
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.factor_manual import finite_factor_values, parse_expression


CANONICAL_FIELDS = {
    "hfq_open",
    "hfq_high",
    "hfq_low",
    "hfq_close",
    "amount",
    "vol",
    "turn",
    "pe_ttm",
    "float_market_cap",
    "total_market_cap",
    "dividend_yield_ratio",
}


def test_pack_has_stable_formulas_including_overnight_cs_rank() -> None:
    fields = [item.field for item in CANONICAL_FACTOR_PACK]
    assert fields == [
        "momentum_1",
        "momentum_10",
        "momentum_20",
        "momentum_60",
        "momentum_120",
        "close_bias_20",
        "close_bias_60",
        "close_zscore_20",
        "close_zscore_60",
        "close_ts_rank_20",
        "close_ts_rank_60",
        "close_ts_rank_120",
        "close_ts_rank_252",
        "amount_zscore_20",
        "vol_mean_20",
        "volatility_20",
        "amount_cs_rank",
        "turn_cs_rank",
        "pe_ttm_cs_rank",
        "float_mv_cs_rank",
        "total_mv_cs_rank",
        "div_yield_cs_rank",
        "intraday_range",
        "overnight_ret",
        "overnight_ret_cs_rank",
        "vol_mean_20_cs_rank",
        "close_bias_20_cs_rank",
        "momentum_10_cs_rank",
        "close_location",
        "trade_vwap",
    ]
    assert "momentum_5" not in fields
    assert "volatility_5" not in fields
    assert pack_entity_id("momentum_20") == "factor_momentum_20"
    by_field = {item.field: item for item in CANONICAL_FACTOR_PACK}
    assert by_field["momentum_20"].formula == "hfq_close.pct_change(20)"
    assert by_field["momentum_120"].formula == "hfq_close.pct_change(120)"
    assert by_field["momentum_120"].direction == "positive"
    assert by_field["volatility_20"].formula == "hfq_close.pct_change(1).rolling_std(20)"
    assert by_field["volatility_20"].direction == "positive"
    assert by_field["intraday_range"].formula == "(hfq_high - hfq_low) / hfq_close"
    assert by_field["pe_ttm_cs_rank"].direction == "negative"
    assert by_field["pe_ttm_cs_rank"].coverage_floor == 0.60
    assert by_field["div_yield_cs_rank"].coverage_floor == 0.60
    assert by_field["overnight_ret_cs_rank"].formula == (
        "(hfq_open / hfq_close.shift(1) - 1).cs_rank(0)"
    )
    assert by_field["overnight_ret_cs_rank"].direction == "positive"
    assert by_field["vol_mean_20_cs_rank"].formula == "vol.rolling_mean(20).cs_rank(0)"
    assert by_field["close_bias_20_cs_rank"].formula == "hfq_close.rolling_bias(20).cs_rank(0)"
    assert by_field["momentum_10_cs_rank"].formula == "hfq_close.pct_change(10).cs_rank(0)"


def test_pack_formulas_parse_against_canonical_fields() -> None:
    for item in CANONICAL_FACTOR_PACK:
        parsed = parse_expression(item.formula, CANONICAL_FIELDS)
        assert parsed.input_fields
        assert set(parsed.input_fields) <= CANONICAL_FIELDS


def test_last_year_window_uses_date_max_calendar_year() -> None:
    assert last_year_window("20260831") == ("20260101", "20260831")


def test_verify_pack_run_ignores_ic_sign() -> None:
    spec = PackFactor(
        field="demo",
        name="demo",
        formula="hfq_close",
        direction="positive",
        category="技术",
        coverage_floor=0.9,
        min_effective_days=5,
    )
    ok, reason = verify_pack_run(
        {"status": "completed", "coverage": 0.96, "effective_days": 20, "ic_mean": -0.2},
        spec,
    )
    assert ok is True
    assert reason == "passed"
    failed, _ = verify_pack_run(
        {"status": "completed", "coverage": 0.5, "effective_days": 20},
        spec,
    )
    assert failed is False


def test_finite_factor_values_turn_inf_into_missing() -> None:
    out = finite_factor_values(pd.Series([1.0, float("inf"), float("-inf"), 0.0]))
    assert list(out.isna()) == [False, True, True, False]


def _pack_env(tmp_path: Path) -> tuple[Settings, Database, CanonicalFactorPackService]:
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2022-01-04", "2024-06-28")]
    symbols = ("000001.SZ", "000002.SZ", "600000.SH")
    rows = []
    for offset, date in enumerate(dates):
        for rank, symbol in enumerate(symbols):
            wave = 1.0 + 0.08 * ((offset + rank * 5) % 11 - 5)
            close = 10.0 + rank * 5 + offset * 0.02 * ((offset % 7) - 3) * wave
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": symbol,
                    "hfq_open": close * 0.995,
                    "hfq_high": close * 1.02,
                    "hfq_low": close * 0.98,
                    "hfq_close": close,
                    "amount": 1_000_000.0 + rank * 1000 + 80_000 * ((offset + rank * 3) % 13 - 6),
                    "vol": 10_000.0 + rank * 50 + (offset % 9) * 80,
                    "turn": 1.5 + rank * 0.2,
                    "pe_ttm": 15.0 + rank + offset * 0.01,
                    "float_market_cap": 1.0e9 + rank * 1.0e8,
                    "total_market_cap": 1.5e9 + rank * 1.0e8,
                    "dividend_yield_ratio": 0.02 + rank * 0.005,
                    "eligible": 1,
                    "list_date": "20100101",
                }
            )
    path = tmp_path / "data" / "canonical.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    fields = [
        "trade_date",
        "ts_code",
        "hfq_open",
        "hfq_high",
        "hfq_low",
        "hfq_close",
        "amount",
        "vol",
        "turn",
        "pe_ttm",
        "float_market_cap",
        "total_market_cap",
        "dividend_yield_ratio",
        "eligible",
        "list_date",
    ]
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('ds_canonical_market', '标准行情', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES ('ds_canonical_market', 'current', ?, ?, ?, ?, ?, 'published', 'needs_review')",
            (
                str(path),
                len(rows),
                json.dumps(fields),
                dates[0],
                dates[-1],
            ),
        )
    factors = FactorRepository(settings, database)
    service = CanonicalFactorPackService(
        settings, database, factors, FactorCalculationService(settings, database)
    )
    return settings, database, service


def test_ingest_computes_verifies_and_publishes_factor(tmp_path: Path) -> None:
    settings, database, service = _pack_env(tmp_path)
    first = service.ingest_one("momentum_20")
    assert first["status"] == "published"
    assert first["entity_id"] == "factor_momentum_20"
    assert first["date_from"] == "20240101"
    assert first["coverage"] is not None and first["coverage"] >= 0.95
    record = FactorRepository(settings, database).get("factor_momentum_20", "v1")
    assert record is not None
    assert record["status"] == "published"
    assert record["formula"] == "hfq_close.pct_change(20)"
    assert record["origin"] == "import"
    with database.connect() as connection:
        calc = connection.execute(
            "SELECT status, coverage FROM factor_calculation_runs WHERE factor_entity_id='factor_momentum_20'"
        ).fetchone()
    assert calc is not None
    assert calc["status"] == "completed"
    second = service.ingest_one("momentum_20")
    assert second["status"] == "skipped"
    assert second["reason"] == "already_published"


def test_ingest_two_column_formula_and_full_pack(tmp_path: Path) -> None:
    _settings, _database, service = _pack_env(tmp_path)
    overnight = service.ingest_one("overnight_ret")
    assert overnight["status"] == "published"
    packed = service.ingest()
    assert packed["published_count"] + packed["skipped_count"] == len(CANONICAL_FACTOR_PACK)
    assert packed["failed_count"] == 0
    assert packed["skipped_count"] >= 1


def test_pack_api_ingest_one_field(tmp_path: Path) -> None:
    settings, database, _service = _pack_env(tmp_path)
    client = TestClient(create_app(settings, database))
    listed = client.get("/api/factor-packs/canonical")
    assert listed.status_code == 200
    assert listed.json()["count"] == len(CANONICAL_FACTOR_PACK)
    created = client.post("/api/factor-packs/canonical/ingest", json={"field": "momentum_1"})
    assert created.status_code == 200
    payload = created.json()
    assert payload["published_count"] == 1
    assert payload["items"][0]["status"] == "published"
    assert client.get("/factors/new/manual").status_code == 200
