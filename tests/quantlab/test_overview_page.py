from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database


PAGE = Path(__file__).parents[2] / "quantlab/web/pages/index.html"
SCRIPT = Path(__file__).parents[2] / "quantlab/web/assets/data/overview.js"
DATASETS_SCRIPT = Path(__file__).parents[2] / "quantlab/web/assets/data/datasets.js"
DATA_PAGE = Path(__file__).parents[2] / "quantlab/web/pages/data.html"


def test_overview_page_contains_real_overview_containers_and_run_links() -> None:
    page = PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'id="overview-summary"' in page
    assert 'id="recent-runs"' in page
    assert 'id="quality-alerts"' in page
    assert 'fetch("/api/overview")' in script
    assert "detail_url" in script
    assert "dataset_count" in script
    assert "model_count" in script
    assert "backtest_count" in script


def test_overview_api_returns_error_contract_when_service_fails(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "cal",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    app = create_app(settings=settings, database=database)
    with database.connect() as connection:
        connection.execute("DROP TABLE run_registry")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/overview")

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "INTERNAL_SERVER_ERROR",
        "message": "internal server error",
        "entity_id": None,
        "details": {},
    }


def test_data_page_uses_real_dataset_api_and_safe_dom_rendering() -> None:
    page = DATA_PAGE.read_text(encoding="utf-8")
    script = DATASETS_SCRIPT.read_text(encoding="utf-8")

    assert 'id="dataset-table"' in page
    assert 'id="rescan-datasets"' in page
    assert "/api/datasets" in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "模拟" not in page + script
