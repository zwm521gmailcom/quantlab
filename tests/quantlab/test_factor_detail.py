from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.services.factor_detail import FactorDetailService


def _setup(tmp_path: Path) -> tuple[Settings, Database, FactorRepository]:
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    path = data_root / "features.parquet"
    pq.write_table(
        pa.table(
            {
                "trade_date": ["20240102", "20240103", "20240104"],
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "momentum_5": [0.1, 0.2, -0.1],
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
        connection.execute("INSERT INTO datasets(entity_id, name, status) VALUES ('ds_features', '因子宽表', 'published')")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES ('ds_features', 'v2024', ?, 3, ?, '20240102', '20240104', 'published', 'passed')",
            (str(path), json.dumps(["trade_date", "ts_code", "momentum_5"])),
        )
    database.register_run("20260902-120000-0001", "research")
    return settings, database, FactorRepository(settings, database)


def _definition(version_id: str = "v1") -> dict[str, object]:
    return {
        "entity_id": "factor_momentum_5",
        "name": "五日动量",
        "category": "技术",
        "version_id": version_id,
        "dataset_id": "ds_features",
        "dataset_version_id": "v2024",
        "formula": "hfq_close[t] / hfq_close[t-5] - 1",
        "input_fields": ["momentum_5"],
        "source": "hfq_daily_standard",
        "direction": "positive",
        "frequency": "daily",
        "missing_policy": "drop",
        "pit_policy": "as-of trade_date",
        "pit_lineage": {"rule": "as-of trade_date", "snapshot": "dataset:v2024"},
        "upstream_factor_versions": [],
        "quality_status": "passed",
        "code_hash": "sha256:factor-code-v1",
        "generation_run_id": "20260902-120000-0001",
    }


def test_factor_detail_aggregates_definition_lineage_and_not_diagnosed_state(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    service = FactorDetailService(settings, database, repository)

    detail = service.get("factor_momentum_5", "v1")
    assert detail["definition"]["formula"].startswith("hfq_close")
    assert detail["lineage"]["dataset_id"] == "ds_features"
    assert detail["lineage"]["dataset_version_id"] == "v2024"
    assert detail["lineage"]["input_fields"] == ["momentum_5"]
    assert detail["lineage"]["upstream_factor_versions"] == []
    assert detail["lineage"]["pit_lineage"]["rule"] == "as-of trade_date"
    assert detail["diagnostics"]["status"] == "not_diagnosed"
    assert detail["diagnostics"]["message"] == "尚未诊断"
    assert detail["history"]["items"][0]["version_id"] == "v1"

    with pytest.raises(ValueError, match="not found"):
        service.get("factor_momentum_5", "v99")


def test_factor_diagnosis_is_stored_separately_and_detail_reads_latest_result(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    service = FactorDetailService(settings, database, repository)

    result = service.diagnose("factor_momentum_5", "v1")
    assert result["status"] == "diagnosed"
    assert result["quality"]["coverage"] == pytest.approx(1.0)
    assert result["quality"]["registered_coverage"] == pytest.approx(1.0)
    assert result["research_run"]["status"] == "completed"
    assert result["artifact"]["artifact_role"] == "factor_diagnostics"
    detail = service.get("factor_momentum_5", "v1")
    assert detail["diagnostics"]["status"] == "diagnosed"
    assert detail["diagnostics"]["quality"]["missing_rows"] == 0
    assert detail["diagnostics"]["research_run"]["run_id"] == result["research_run"]["run_id"]
    assert detail["diagnostics"]["artifact"]["artifact_id"] == result["artifact"]["artifact_id"]
    assert repository.get("factor_momentum_5", "v1")["status"] == "published"


def test_factor_diagnosis_reports_real_distribution_and_target_metrics_without_fabrication(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "diagnostic.parquet"
    pq.write_table(
        pa.table(
            {
                "date": ["20240102", "20240102", "20240102", "20240102", "20240102", "20240103", "20240103", "20240103", "20240103", "20240103"],
                "instrument": [f"00000{i}.SZ" for i in range(1, 6)] * 2,
                "momentum_5": [1.0, 2.0, 3.0, 4.0, None, 2.0, 1.0, 4.0, 3.0, 5.0],
                "future_return": [0.01, 0.02, 0.03, 0.04, 0.00, 0.02, 0.01, 0.04, 0.03, 0.05],
            }
        ),
        path,
    )
    settings, database, repository = _setup(tmp_path)
    with database.transaction() as connection:
        connection.execute("DELETE FROM dataset_versions WHERE entity_id='ds_features' AND version_id='v2024'")
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES ('ds_features', 'v2024', ?, 10, ?, '20240102', '20240103', 'published', 'passed')",
            (str(path), json.dumps(["date", "instrument", "momentum_5", "future_return"])),
        )
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    service = FactorDetailService(settings, database, repository)

    result = service.diagnose("factor_momentum_5", "v1")
    quality = result["quality"]
    assert quality["coverage"] == pytest.approx(0.9)
    assert quality["quantiles"]["p50"] == pytest.approx(3.0)
    assert quality["distribution"]["count"] == 9
    assert quality["daily_ic"]["status"] == "available"
    assert quality["group_returns"]["status"] == "available"
    assert quality["turnover"]["status"] == "available"
    assert quality["stability"]["status"] == "available"
    assert "尚未诊断" not in result["message"]


def test_factor_detail_exposes_artifact_lineage_and_copy_inherits_source_hashes(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    run_id = "20260902-120000-0001"
    artifact_path = settings.runtime_root / "results" / "definition.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("{}", encoding="utf-8")
    artifact = ArtifactRepository(settings, database).register(
        run_id=run_id, path=artifact_path, display_name="因子定义", artifact_role="factor_definition"
    )
    definition = _definition()
    definition["artifact_id"] = artifact.artifact_id
    repository.import_definition(definition)
    service = FactorDetailService(settings, database, repository)
    detail = service.get("factor_momentum_5", "v1")
    assert detail["lineage"]["code_hash"] == "sha256:factor-code-v1"
    assert detail["lineage"]["generation_run_id"] == run_id
    assert detail["lineage"]["artifact"]["artifact_id"] == artifact.artifact_id
    copied = service.copy_version("factor_momentum_5", "v1")
    assert copied["code_hash"] == definition["code_hash"]
    assert copied["generation_run_id"] == run_id
    assert copied["artifact_id"] == artifact.artifact_id


def test_factor_detail_api_supports_canonical_and_legacy_urls_and_artifact_download(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    client = TestClient(create_app(settings, database))
    canonical = client.get("/api/factors/factor_momentum_5/versions/v1")
    legacy = client.get("/api/factors/factor_momentum_5/v1")
    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert client.get("/factors/factor_momentum_5/versions/v1").status_code == 200
    assert client.get("/factors/factor_momentum_5/v1").status_code == 200


def test_copy_version_creates_new_draft_without_mutating_published_version(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    service = FactorDetailService(settings, database, repository)

    copied = service.copy_version("factor_momentum_5", "v1")
    assert copied["version_id"] == "v2"
    assert copied["status"] == "draft"
    assert copied["quality_status"] == "needs_review"
    assert copied["formula"] == repository.get("factor_momentum_5", "v1")["formula"]
    assert repository.get("factor_momentum_5", "v1")["status"] == "published"


def test_backtest_draft_is_persistent_and_references_exact_factor_version(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    service = FactorDetailService(settings, database, repository)

    draft = service.create_backtest_draft([{"factor_id": "factor_momentum_5", "version_id": "v1"}])
    assert draft["revision"] == 1
    assert draft["factor_version_ids"] == [{"factor_id": "factor_momentum_5", "version_id": "v1"}]
    assert service.get_backtest_draft(draft["draft_id"]) == draft
    updated = service.update_backtest_draft(
        draft["draft_id"], [{"factor_id": "factor_momentum_5", "version_id": "v1"}], expected_revision=1
    )
    assert updated["draft_id"] == draft["draft_id"]
    assert updated["revision"] == 2
    with pytest.raises(ValueError, match="published"):
        service.create_backtest_draft([{"factor_id": "factor_momentum_5", "version_id": "v99"}])


def test_backtest_draft_rejects_stale_revision(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    service = FactorDetailService(settings, database, repository)
    draft = service.create_backtest_draft([{"factor_id": "factor_momentum_5", "version_id": "v1"}])

    service.update_backtest_draft(
        draft["draft_id"],
        [{"factor_id": "factor_momentum_5", "version_id": "v1"}],
        expected_revision=draft["revision"],
    )
    with pytest.raises(ValueError, match="revision conflict"):
        service.update_backtest_draft(
            draft["draft_id"],
            [{"factor_id": "factor_momentum_5", "version_id": "v1"}],
            expected_revision=draft["revision"],
        )


def test_factor_detail_api_exposes_detail_copy_and_draft_routes(tmp_path: Path) -> None:
    settings, database, repository = _setup(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    client = TestClient(create_app(settings, database))

    detail = client.get("/api/factors/factor_momentum_5/versions/v1")
    assert detail.status_code == 200
    assert detail.json()["diagnostics"]["status"] == "not_diagnosed"
    copied = client.post("/api/factors/factor_momentum_5/versions/v1/copy")
    assert copied.status_code == 201
    assert copied.json()["version_id"] == "v2"
    draft = client.post(
        "/api/backtest-drafts",
        json={"factor_version_ids": [{"factor_id": "factor_momentum_5", "version_id": "v1"}]},
    )
    assert draft.status_code == 201
    assert client.get(f"/api/backtest-drafts/{draft.json()['draft_id']}").json()["revision"] == 1
    updated = client.post(
        "/api/backtest-drafts",
        json={"draft_id": draft.json()["draft_id"], "revision": 1, "factor_version_ids": [{"factor_id": "factor_momentum_5", "version_id": "v1"}]},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert client.get("/api/factors/factor_momentum_5/versions/v99").status_code == 404
