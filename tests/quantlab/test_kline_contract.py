from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.kline import (
    DEFAULT_KLINE_VERSION,
    KlineQueryService,
)


def _service(tmp_path: Path, *, rows: list[dict[str, object]] | None = None) -> KlineQueryService:
    data_root = tmp_path / "tushare_migration_data"
    version_root = data_root / f"derived/hfq_market_st_v1/versions/{DEFAULT_KLINE_VERSION}"
    partition = version_root / "year=2024/part.parquet"
    partition.parent.mkdir(parents=True)
    if rows is None:
        rows = [
            {
                "trade_date": "20240102",
                "ts_code": "000001.SZ",
                "raw_open": 10.0,
                "raw_high": 11.0,
                "raw_low": 9.0,
                "raw_close": 10.5,
                "raw_up_limit": 11.0,
                "raw_down_limit": 9.0,
                "adj_factor": 2.0,
                "hfq_open": 20.0,
                "hfq_high": 22.0,
                "hfq_low": 18.0,
                "hfq_close": 21.0,
                "hfq_up_limit": 22.0,
                "hfq_down_limit": 18.0,
                "vol": 100.0,
                "amount": 1000.0,
                "st_status": 0,
            },
            {
                "trade_date": "20240103",
                "ts_code": "000001.SZ",
                "raw_open": 11.0,
                "raw_high": 12.0,
                "raw_low": 10.0,
                "raw_close": 11.5,
                "raw_up_limit": 12.65,
                "raw_down_limit": 9.35,
                "adj_factor": 4.0,
                "hfq_open": 44.0,
                "hfq_high": 48.0,
                "hfq_low": 40.0,
                "hfq_close": 46.0,
                "hfq_up_limit": 50.6,
                "hfq_down_limit": 37.4,
                "vol": 120.0,
                "amount": 1400.0,
                "st_status": 1,
            },
            {
                "trade_date": "20240102",
                "ts_code": "000002.SZ",
                "raw_open": 20.0,
                "raw_high": 21.0,
                "raw_low": 19.0,
                "raw_close": 20.5,
                "raw_up_limit": 22.55,
                "raw_down_limit": 18.45,
                "adj_factor": 1.0,
                "hfq_open": 20.0,
                "hfq_high": 21.0,
                "hfq_low": 19.0,
                "hfq_close": 20.5,
                "hfq_up_limit": 22.55,
                "hfq_down_limit": 18.45,
                "vol": 200.0,
                "amount": 4000.0,
                "st_status": 0,
            },
        ]
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, partition)
    (data_root / "derived/hfq_market_st_v1/CURRENT").parent.mkdir(parents=True, exist_ok=True)
    (data_root / "derived/hfq_market_st_v1/CURRENT").write_text(
        f"{DEFAULT_KLINE_VERSION}\n", encoding="utf-8"
    )
    (version_root / "manifest.json").write_text(
        json.dumps(
            {
                "version": DEFAULT_KLINE_VERSION,
                "row_count": table.num_rows,
                "date_min": "20240102",
                "date_max": "20240103",
                "schema": table.column_names,
                "partitions": ["year=2024/part.parquet"],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES (?, ?, 'published')",
            ("ds_hfq_market_st_v1", "后复权加 ST 标准行情"),
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'published', 'passed', ?)",
            (
                "ds_hfq_market_st_v1",
                DEFAULT_KLINE_VERSION,
                str(version_root),
                table.num_rows,
                json.dumps(table.column_names),
                "20240102",
                "20240103",
                json.dumps({"category": "canonical"}),
            ),
        )
    return KlineQueryService(settings, database)


def test_query_uses_stock_date_field_filters_and_stays_page_bounded(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.query(
        symbols=["000001.SZ"],
        date_from="20240103",
        date_to="20240103",
        fields=["trade_date", "ts_code", "raw_close", "st_status"],
        page=1,
        page_size=1,
        max_rows=1,
    )

    assert result["version_id"] == DEFAULT_KLINE_VERSION
    assert result["fields"] == ["trade_date", "ts_code", "raw_close", "st_status"]
    assert result["items"] == [
        {"trade_date": "20240103", "ts_code": "000001.SZ", "raw_close": 11.5, "st_status": 1}
    ]
    assert result["total"] == 1
    assert result["training_eligible"] is True


def test_query_rejects_unknown_version_fields_and_limits(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="version"):
        service.query(version_id="not-a-version")
    with pytest.raises(ValueError, match="field"):
        service.query(fields=["qfq_close"])
    with pytest.raises(ValueError, match="page_size"):
        service.query(page_size=501)
    with pytest.raises(ValueError, match="max_rows"):
        service.query(max_rows=5001)
    with pytest.raises(ValueError, match="date"):
        service.query(date_from="20240103", date_to="20240102")
    with pytest.raises(ValueError, match="mode"):
        service.query(mode="qfq")
    with pytest.raises(ValueError, match="invalid stock symbol"):
        service.query(symbols=["000001.BJ"])
    with pytest.raises(ValueError, match="50"):
        service.query(symbols=[f"{index:06d}.SZ" for index in range(51)])


def test_summary_and_quality_report_are_streaming_and_report_key_integrity(tmp_path: Path) -> None:
    service = _service(tmp_path)

    summary = service.summary(symbols=["000001.SZ"], date_from="20240102", date_to="20240103")
    quality = service.quality()

    assert summary["row_count"] == 2
    assert summary["symbol_count"] == 1
    assert summary["date_min"] == "20240102"
    assert summary["date_max"] == "20240103"
    assert quality["dataset_id"] == "ds_hfq_market_st_v1"
    assert quality["checks"]["duplicate_key_count"] == 0
    assert quality["checks"]["ohlc_violation_count"] == 0
    assert quality["checks"]["required_fields"] is True
    assert quality["status"] == "passed"


def test_quality_counts_duplicate_keys_and_ohlc_violations(tmp_path: Path) -> None:
    good = {
        "trade_date": "20240102",
        "ts_code": "000001.SZ",
        "raw_open": 10.0,
        "raw_high": 11.0,
        "raw_low": 9.0,
        "raw_close": 10.5,
        "raw_up_limit": 11.0,
        "raw_down_limit": 9.0,
        "adj_factor": 1.0,
        "hfq_open": 10.0,
        "hfq_high": 11.0,
        "hfq_low": 9.0,
        "hfq_close": 10.5,
        "hfq_up_limit": 11.0,
        "hfq_down_limit": 9.0,
        "vol": 100.0,
        "amount": 1000.0,
        "st_status": 0,
    }
    broken = dict(
        good,
        trade_date="20240103",
        raw_high=8.0,
        raw_low=12.0,
        hfq_high=8.0,
        hfq_low=12.0,
    )
    quality = _service(tmp_path, rows=[good, dict(good), broken]).quality()
    assert quality["checks"]["duplicate_key_count"] == 1
    assert quality["checks"]["ohlc_violation_count"] == 1
    assert quality["status"] == "needs_review"


def test_chart_query_returns_a_bounded_downsample_of_the_api_rows(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.query(page_size=3, max_rows=3, downsample=2)

    assert result["downsampled"] is True
    assert result["sample_size"] == 2
    assert len(result["items"]) == 2


def test_tail_returns_only_the_last_rows_of_the_matching_set(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.query(page_size=1, tail=True)

    assert len(result["items"]) == 1
    assert result["total"] == 3
    assert result["has_more"] is False
    assert result["truncated"] is False
    assert result["items"][0]["trade_date"] == "20240102"
    assert result["items"][0]["ts_code"] == "000002.SZ"


def test_max_rows_truncation_is_distinct_from_page_has_more(tmp_path: Path) -> None:
    service = _service(tmp_path)

    first_page = service.query(page=1, page_size=1, max_rows=2)
    last_page = service.query(page=2, page_size=1, max_rows=2)

    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert first_page["truncated"] is True
    assert last_page["has_more"] is False
    assert last_page["truncated"] is True
