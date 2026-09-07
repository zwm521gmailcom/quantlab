from fastapi.testclient import TestClient

from quantlab.api.app import create_app


def test_health_endpoint_returns_local_service_status() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "quantlab"
