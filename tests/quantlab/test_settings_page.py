from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database


def client(tmp_path: Path) -> TestClient:
    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data", calibration_root=tmp_path / "cal", runtime_root=tmp_path / "runtime")
    return TestClient(create_app(settings=settings, database=Database(settings.database_path)))


def test_settings_are_masked_and_persist_allowed_defaults(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["paths"]["data_root"] == str(tmp_path / "data")
    assert body["secrets"] == {"tushare_token": False}
    saved = api.put("/api/settings", json={"defaults": {"top_n": 20, "rebalance_days": 3}})
    assert saved.status_code == 200
    assert saved.json()["defaults"]["top_n"] == 20
    assert api.get("/api/settings").json()["defaults"]["rebalance_days"] == 3


def test_settings_reject_unsafe_mutation_and_support_connection_scan_reset(tmp_path: Path) -> None:
    api = client(tmp_path)
    bad = api.put("/api/settings", json={"paths": {"data_root": str(tmp_path / "escape")}, "host": "0.0.0.0"})
    assert bad.status_code == 400
    assert api.post("/api/settings/test-connection").json()["ok"] is True
    scan = api.post("/api/settings/scan")
    assert scan.status_code == 200
    assert scan.json()["manual"] is True
    api.put("/api/settings", json={"defaults": {"top_n": 20}})
    reset = api.post("/api/settings/reset")
    assert reset.status_code == 200
    assert reset.json()["defaults"]["top_n"] == 10


def test_settings_page_is_separate_and_does_not_auto_run(tmp_path: Path) -> None:
    api = client(tmp_path)
    page = api.get("/settings")
    assert page.status_code == 200
    assert "系统设置" in page.text
    assert "/api/backtests" not in page.text
