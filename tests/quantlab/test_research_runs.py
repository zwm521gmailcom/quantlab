from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.research_runs import ResearchRunRepository


def _repository(tmp_path: Path) -> ResearchRunRepository:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "runtime",
    )
    settings.data_root.mkdir()
    dataset = settings.data_root / "features.parquet"
    dataset.write_bytes(b"test dataset")
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES (?, ?, 'published')",
            ("ds_features", "七因子研究宽表"),
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, status, quality_status) "
            "VALUES ('ds_features', 'v1', ?, 'published', 'passed')",
            (str(dataset),),
        )
        connection.execute(
            "INSERT INTO factors(entity_id, name, status) VALUES ('factor_momentum_5', '五日动量', 'published')"
        )
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, status, quality_status) "
            "VALUES ('factor_momentum_5', 'v1', 'ds_features', 'v1', ?, ?, 'published', 'passed')",
            ("hfq_close[t] / hfq_close[t-5] - 1", json.dumps(["momentum_5"])),
        )
    return ResearchRunRepository(settings, database)


def _config() -> dict[str, object]:
    return {
        "dataset_id": "ds_features",
        "dataset_version_id": "v1",
        "factor_versions": [{"factor_id": "factor_momentum_5", "version_id": "v1"}],
        "sample": {"date_from": "20200101", "date_to": "20201231", "universe": "中国A股（SH/SZ）"},
        "filters": {"st_status": 0, "suspended": False},
        "filter_snapshot": {"st_status": 0, "suspended": False, "captured_at": "2026-09-02T00:00:00Z"},
        "label": {"definition": "t+1 open -> t+2 close", "price_fields": ["hfq_open", "hfq_close"]},
        "pit_snapshot": {"rule": "published factor and dataset versions only", "captured_at": "2026-09-02T00:00:00Z"},
    }


def test_research_run_create_locks_lineage_semantics_and_summary(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    created = repository.create(name="动量诊断", research_type="manual", config=_config())
    detail = repository.get(created["run_id"])

    assert created["status"] == "queued"
    assert detail is not None
    assert detail["name"] == "动量诊断"
    assert detail["research_type"] == "manual"
    assert detail["dataset_version_id"] == "v1"
    assert detail["factor_versions"] == [{"factor_id": "factor_momentum_5", "version_id": "v1"}]
    assert detail["config"]["label"]["definition"] == "t+1 open -> t+2 close"
    assert detail["config"]["label"]["price_fields"] == ["hfq_open", "hfq_close"]
    assert detail["summary"] == {}
    assert detail["artifacts"] == []


def test_research_run_rejects_missing_unpublished_and_target_leaking_config(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="published"):
        repository.create(name="坏版本", research_type="manual", config={**_config(), "dataset_version_id": "missing"})
    with pytest.raises(ValueError, match="target-only"):
        repository.create(
            name="泄漏因子", research_type="manual",
            config={**_config(), "factor_versions": [{"factor_id": "label", "version_id": "v1"}]},
        )
    with pytest.raises(ValueError, match="hfq_open.*hfq_close"):
        repository.create(
            name="错标签", research_type="manual",
            config={**_config(), "label": {"definition": "t+1 open -> t+2 close", "price_fields": ["close"]}},
        )


def test_research_run_state_machine_duplicate_id_and_copy_do_not_run(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.create(name="原始研究", research_type="automatic", config=_config(), run_id="20260902-120000-0001")

    with pytest.raises(ValueError, match="duplicate|already exists"):
        repository.create(name="重复", research_type="manual", config=_config(), run_id=first["run_id"])
    with pytest.raises(ValueError, match="not allowed"):
        repository.transition(first["run_id"], "completed")

    repository.transition(first["run_id"], "running")
    repository.complete(first["run_id"], {"ic": 0.018})
    before = repository.list()["total"]
    copied = repository.copy_config(first["run_id"], name="复制研究")

    assert copied == {
        "source_run_id": first["run_id"],
        "name": "复制研究",
        "research_type": "automatic",
        "config": _config(),
        "target_url": f"/research/factors/auto?copy_from_run_id={first['run_id']}",
    }
    assert repository.list()["total"] == before
    assert repository.get(copied["source_run_id"])["status"] == "completed"
    with pytest.raises(ValueError, match="not allowed"):
        repository.transition(first["run_id"], "running")


def test_research_run_list_filters_and_api_has_stable_detail_and_copy_routes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.create(name="手动诊断", research_type="manual", config=_config())
    repository.create(name="自动挖掘", research_type="automatic", config=_config())
    assert repository.list(research_type="manual")["total"] == 1

    app = create_app(repository.settings, repository.database)
    client = TestClient(app)
    response = client.get("/api/research-runs", params={"research_type": "manual"})
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "手动诊断"
    detail = client.get(f"/api/research-runs/{first['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["detail_url"] == f"/research/runs/{first['run_id']}"
    copied = client.post(f"/api/research-runs/{first['run_id']}/copy-config", json={"name": "复制配置"})
    assert copied.status_code == 200
    assert copied.json()["source_run_id"] == first["run_id"]
    assert copied.json()["target_url"] == f"/research/factors/manual?copy_from_run_id={first['run_id']}"
    assert client.get("/api/research-runs").json()["total"] == 2


def test_research_api_rejects_non_object_create_payload(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    client = TestClient(create_app(repository.settings, repository.database))

    response = client.post("/api/research-runs", json=[])

    assert response.status_code == 400
    assert response.json()["error_code"] == "RESEARCH_RUN_INVALID"


@pytest.mark.parametrize(
    "sample",
    [
        {"date_from": "20201231", "date_to": "20200101", "universe": "中国A股"},
        {"date_from": "2020/01/01", "date_to": "20201231", "universe": "中国A股"},
        {"date_from": "20200101", "date_to": "20201231", "universe": ""},
    ],
)
def test_research_run_rejects_invalid_sample_semantics(tmp_path: Path, sample: dict[str, str]) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="sample"):
        repository.create(name="无效样本", research_type="manual", config={**_config(), "sample": sample})


def test_research_run_normalizes_dates_and_requires_snapshot_semantics(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    config = {
        **_config(),
        "sample": {"date_from": "2020-01-01", "date_to": "2020-12-31", "universe": "中国A股"},
        "filter_snapshot": {"st_status": 0, "suspended": False, "captured_at": "2026-09-02T00:00:00Z"},
        "pit_snapshot": {"rule": "published factor and dataset versions only", "captured_at": "2026-09-02T00:00:00Z"},
    }

    created = repository.create(name="规范化样本", research_type="manual", config=config)

    assert created["config"]["sample"]["date_from"] == "20200101"
    assert created["config"]["sample"]["date_to"] == "20201231"
    with pytest.raises(ValueError, match="filter_snapshot"):
        repository.create(name="无过滤语义", research_type="manual", config={**_config(), "filter_snapshot": {"captured_at": "x"}})
    with pytest.raises(ValueError, match="pit_snapshot"):
        repository.create(name="无点时语义", research_type="manual", config={**_config(), "pit_snapshot": {"captured_at": "x"}})


def test_copy_config_revalidates_current_published_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created = repository.create(name="待失效", research_type="manual", config=_config())
    with repository.database.transaction() as connection:
        connection.execute("UPDATE factor_versions SET status='deprecated' WHERE entity_id='factor_momentum_5' AND version_id='v1'")

    with pytest.raises(ValueError, match="published factor version"):
        repository.copy_config(created["run_id"])
