from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database


def test_health_endpoint_returns_local_service_status(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings=settings, database=Database(settings.database_path)))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "quantlab"
    assert response.json()["asset"] == "a_share"
    assert response.json()["asset_label"] == "A股"
    assert response.json()["port"] == 8765
    assert response.json()["lan_port"] == 8766
    from quantlab import __version__
    assert response.json()["version"] == __version__


def test_health_endpoint_reports_crypto_instance(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
        asset="crypto",
        port=8775,
        lan_port=8776,
    )
    client = TestClient(create_app(settings=settings, database=Database(settings.database_path)))
    body = client.get("/api/health").json()
    assert body["asset"] == "crypto"
    assert body["asset_label"] == "数字货币"
    assert body["port"] == 8775
    assert body["lan_port"] == 8776
