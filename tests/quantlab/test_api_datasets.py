from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.catalog import DatasetCatalog


def _client(tmp_path):
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    catalog = DatasetCatalog(settings, database)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES (?, ?, ?)",
            ("d1", "行情", "published"),
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            ("d1", "v1", settings.store_path(settings.data_root / "x"), 10, '["close"]', "20240101", "20240131", "passed", '{"category":"canonical","path_alias":"data/x"}'),
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            ("d1", "v2", settings.store_path(settings.data_root / "x2"), 20, '["close"]', "20240201", "20240229", "needs_review", '{"category":"canonical","path_alias":"data/x2"}'),
        )
    app = create_app(settings, database)
    app.state.dataset_catalog = catalog
    return TestClient(app)


def test_dataset_endpoints_have_lightweight_contract() -> None:
    client = TestClient(create_app())
    response = client.get("/api/datasets")
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)
    assert client.get("/api/datasets/not-found").status_code == 404


def test_datasets_support_filters_pagination_and_safe_aliases(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/datasets", params={"quality_status": "needs_review", "page_size": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["version_id"] == "v2"
    assert payload["items"][0]["path_alias"] == "data/x2"
    assert "/" not in payload["items"][0]["path_alias"] or not payload["items"][0]["path_alias"].startswith("/")
    assert "path" not in payload["items"][0]


def test_versions_endpoint_returns_all_immutable_versions(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/datasets/d1/versions")

    assert response.status_code == 200
    assert [item["version_id"] for item in response.json()] == ["v1", "v2"]
    assert all("path" not in item for item in response.json())


def test_quality_summary_endpoint_returns_registered_metadata_checks(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/datasets/quality-summary")

    assert response.status_code == 200
    payload = response.json()["items"]
    assert len(payload) == 2
    assert "path" not in response.text
    assert str(tmp_path) not in response.text
    passed = next(item for item in payload if item["version_id"] == "v1")
    assert all(item["passed"] for item in passed["checks"])
    needs_review = next(item for item in payload if item["version_id"] == "v2")
    assert not next(item for item in needs_review["checks"] if item["name"] == "质量状态")["passed"]


def test_manual_rescan_returns_audit_without_exposing_filesystem_paths(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/datasets/rescan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["audit_id"]
    assert "path" not in response.text
    assert str(tmp_path) not in response.text
