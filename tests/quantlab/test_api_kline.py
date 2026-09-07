from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from tests.quantlab.test_kline_contract import _service


def _rows(count: int) -> list[dict[str, object]]:
    template = {
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
    return [dict(template, trade_date=f"2024{i:04d}") for i in range(count)]


def test_kline_api_exposes_query_summary_quality_and_immediate_csv(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = create_app(service.settings, service.database)
    app.state.kline_service = service
    with TestClient(app) as client:
        query = client.get(
            "/api/kline/query",
            params={
                "ts_code": "000001.SZ",
                "date_from": "20240102",
                "date_to": "20240103",
                "mode": "raw",
                "fields": "trade_date,ts_code,raw_close",
                "page_size": 1,
                "max_rows": 2,
            },
        )
        assert query.status_code == 200
        assert len(query.json()["items"]) == 1
        assert query.json()["items"][0]["raw_close"] == 10.5

        summary = client.get("/api/kline/summary", params={"ts_code": "000001.SZ"})
        assert summary.status_code == 200
        assert summary.json()["symbol_count"] == 1

        quality = client.get("/api/kline/quality")
        assert quality.status_code == 200
        assert quality.json()["checks"]["required_fields"] is True

        csv_response = client.get(
            "/api/kline/export.csv",
            params={"ts_code": "000001.SZ", "fields": "trade_date,ts_code,raw_close", "max_rows": 2},
        )
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert "trade_date,ts_code,raw_close" in csv_response.text
        assert "Artifact" not in csv_response.text


def test_kline_api_rejects_unbounded_requests_and_qfq_mode(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = create_app(service.settings, service.database)
    app.state.kline_service = service
    with TestClient(app) as client:
        response = client.get("/api/kline/query", params={"page_size": 501})
        assert response.status_code == 422
        assert response.json()["error_code"] == "VALIDATION_ERROR"

        qfq = client.get("/api/kline/query", params={"ts_code": "000001.SZ", "mode": "qfq"})
        assert qfq.status_code == 422
        assert qfq.json()["error_code"] == "VALIDATION_ERROR"

        empty_qfq = client.get("/api/kline/query", params={"mode": "qfq"})
        assert empty_qfq.status_code == 422
        assert empty_qfq.json()["error_code"] == "VALIDATION_ERROR"


def test_kline_csv_accumulates_bounded_pages_without_duplicate_headers(tmp_path: Path) -> None:
    service = _service(tmp_path, rows=_rows(501))
    app = create_app(service.settings, service.database)
    with TestClient(app) as client:
        response = client.get(
            "/api/kline/export.csv",
            params={"fields": "trade_date,ts_code,raw_close", "max_rows": 501},
        )

    lines = response.text.splitlines()
    assert response.status_code == 200
    assert len(lines) == 502
    assert lines[0] == "trade_date,ts_code,raw_close"
    assert sum(line == lines[0] for line in lines) == 1
