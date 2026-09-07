from fastapi import HTTPException
from fastapi.testclient import TestClient

from quantlab.api.app import create_app


def _assert_error_contract(payload: dict[str, object]) -> None:
    assert set(payload) == {"error_code", "message", "entity_id", "details"}
    assert isinstance(payload["error_code"], str)
    assert isinstance(payload["message"], str)
    assert isinstance(payload["details"], dict)


def test_framework_default_404_uses_quantlab_error_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    _assert_error_contract(payload)
    assert payload["error_code"] == "NOT_FOUND"
    assert payload["entity_id"] is None


def test_http_exception_is_normalized_to_quantlab_error_contract() -> None:
    app = create_app()

    @app.get("/api/test-http-error")
    def test_http_error() -> None:
        raise HTTPException(status_code=409, detail="conflict")

    response = TestClient(app).get("/api/test-http-error")

    assert response.status_code == 409
    payload = response.json()
    _assert_error_contract(payload)
    assert payload == {
        "error_code": "HTTP_ERROR",
        "message": "conflict",
        "entity_id": None,
        "details": {},
    }


def test_request_validation_error_uses_quantlab_error_contract() -> None:
    app = create_app()

    @app.get("/api/test-validation/{item_id}")
    def test_validation(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    response = TestClient(app).get("/api/test-validation/not-an-integer")

    assert response.status_code == 422
    payload = response.json()
    _assert_error_contract(payload)
    assert payload["error_code"] == "VALIDATION_ERROR"
    assert payload["entity_id"] is None
    assert payload["details"]["errors"]


def test_unhandled_500_uses_safe_quantlab_error_contract() -> None:
    app = create_app()

    @app.get("/api/test-internal-error")
    def test_internal_error() -> None:
        raise RuntimeError("secret /Volumes/T2/private-value")

    response = TestClient(app, raise_server_exceptions=False).get("/api/test-internal-error")

    assert response.status_code == 500
    payload = response.json()
    _assert_error_contract(payload)
    assert payload == {
        "error_code": "INTERNAL_SERVER_ERROR",
        "message": "internal server error",
        "entity_id": None,
        "details": {},
    }
    assert "private-value" not in response.text
