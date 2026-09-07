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


def _repository(tmp_path: Path) -> FactorRepository:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "features.parquet"
    pq.write_table(
        pa.table(
            {
                "trade_date": ["20240102", "20240103", "20240104"],
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "momentum_5": [0.1, 0.2, -0.1],
                "volatility_5": [0.02, 0.03, 0.04],
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
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES (?, ?, 'published')",
            ("ds_features", "因子宽表"),
        )
        connection.execute(
            "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, status, quality_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'published', 'passed')",
            (
                "ds_features",
                "v2024",
                str(path),
                3,
                json.dumps(["trade_date", "ts_code", "momentum_5", "volatility_5"]),
                "20240102",
                "20240104",
            ),
        )
    return FactorRepository(settings, database)


def _definition(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "entity_id": "factor_momentum_5",
        "name": "五日动量",
        "category": "技术",
        "version_id": "v1",
        "dataset_id": "ds_features",
        "dataset_version_id": "v2024",
        "formula": "hfq_close[t] / hfq_close[t-5] - 1",
        "input_fields": ["momentum_5"],
        "source": "hfq_daily_standard",
        "direction": "positive",
        "frequency": "daily",
        "missing_policy": "缺失值剔除",
        "pit_policy": "只使用当日及此前数据",
        "pit_lineage": {"rule": "as-of trade_date", "snapshot": "dataset:v2024"},
        "upstream_factor_versions": [],
        "quality_status": "passed",
    }
    result.update(overrides)
    return result


def test_factor_repository_imports_strict_definition_and_lists_filterable_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    created = repository.import_definition(_definition())
    assert created["entity_id"] == "factor_momentum_5"
    assert created["status"] == "draft"
    assert repository.list(category="技术", source="hfq_daily_standard", lifecycle="draft")["total"] == 1
    assert repository.list(query="动量")["items"][0]["detail_url"] == "/factors/factor_momentum_5/versions/v1"

    with pytest.raises(ValueError, match="additional|额外"):
        repository.import_definition(_definition(unexpected="reject"))
    with pytest.raises(ValueError, match="target-only"):
        repository.import_definition(_definition(input_fields=["label"]))


def test_publish_is_atomic_and_requires_quality_real_field_and_pit_lineage(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())

    published = repository.publish("factor_momentum_5", "v1")
    assert published["status"] == "published"
    assert published["quality_status"] == "passed"

    with pytest.raises(ValueError, match="field"):
        repository.import_definition(_definition(entity_id="factor_missing", version_id="v1", input_fields=["not_a_field"]))

    with pytest.raises(ValueError, match="PIT|pit"):
        repository.import_definition(_definition(entity_id="factor_bad_pit", version_id="v1", pit_lineage={}))

    repository.import_definition(_definition(entity_id="factor_bad_quality", version_id="v1", quality_status="warning"))
    with pytest.raises(ValueError, match="quality"):
        repository.publish("factor_bad_quality", "v1")
    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM factor_versions WHERE entity_id='factor_bad_quality' AND version_id='v1'"
        ).fetchone()[0] == "draft"

    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO factors(entity_id, name, status) VALUES ('factor_bad_target', '目标泄漏', 'draft')")
        connection.execute(
            "INSERT INTO factor_versions(entity_id, version_id, dataset_id, dataset_version_id, formula, input_fields_json, "
            "source, direction, frequency, missing_policy, pit_policy, pit_lineage_json, status, quality_status) "
            "VALUES ('factor_bad_target', 'v1', 'ds_features', 'v2024', 'label', '[\"label\"]', "
            "'test', 'positive', 'daily', 'drop', 'as-of', '{\"rule\":\"as-of\",\"snapshot\":\"v2024\"}', 'draft', 'passed')"
        )
    with pytest.raises(ValueError, match="target-only"):
        repository.publish("factor_bad_target", "v1")


def test_factor_version_is_immutable_and_referenced_version_can_only_be_deprecated(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    repository.publish("factor_momentum_5", "v1")
    with pytest.raises(ValueError, match="immutable"):
        repository.update_draft("factor_momentum_5", "v1", {"formula": "changed"})

    repository.import_definition(_definition(entity_id="factor_custom_ref"))
    repository.publish("factor_custom_ref", "v1")
    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES ('s1', '策略', 'published')")
        connection.execute(
            "INSERT INTO strategy_versions(entity_id, version_id, status, quality_status) VALUES ('s1', 'v1', 'published', 'passed')"
        )
        connection.execute(
            "INSERT INTO strategy_factor_versions(strategy_entity_id, strategy_version_id, factor_entity_id, factor_version_id) "
            "VALUES ('s1', 'v1', 'factor_custom_ref', 'v1')"
        )
    with pytest.raises(ValueError, match="策略|引用"):
        repository.delete("factor_custom_ref", "v1")
    assert repository.deprecate("factor_custom_ref", "v1")["status"] == "deprecated"
    assert repository.get("factor_custom_ref", "v1")["formula"].startswith("hfq_close")


def test_factor_delete_removes_unreferenced_version_and_entity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition(entity_id="factor_custom_alpha"))
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO factor_calculation_runs("
            "factor_entity_id, factor_version_id, dataset_id, dataset_version_id, "
            "date_from, date_to, status, started_at) "
            "VALUES ('factor_custom_alpha', 'v1', 'ds_features', 'v2024', '20240102', '20240104', 'completed', '2024-01-01T00:00:00+00:00')"
        )

    repository.delete("factor_custom_alpha", "v1")
    assert repository.get("factor_custom_alpha", "v1") is None
    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM factors WHERE entity_id='factor_custom_alpha'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM factor_calculation_runs WHERE factor_entity_id='factor_custom_alpha'"
        ).fetchone() is None


def test_factor_delete_accepts_unprefixed_auto_id_and_keeps_other_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition(entity_id="factor_auto_20260904_011443_0001_0", version_id="v1"))
    repository.import_definition(_definition(entity_id="factor_auto_20260904_011443_0001_0", version_id="v2"))

    repository.delete("auto_20260904_011443_0001_0", "v1")
    assert repository.get("factor_auto_20260904_011443_0001_0", "v1") is None
    assert repository.get("factor_auto_20260904_011443_0001_0", "v2") is not None
    with repository.database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM factors WHERE entity_id='factor_auto_20260904_011443_0001_0'"
        ).fetchone() is not None


def test_factor_delete_rejects_builtin_and_referenced_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    with pytest.raises(ValueError, match="系统自带"):
        repository.delete("momentum_5", "v1")
    assert repository.get("factor_momentum_5", "v1") is not None

    repository.import_definition(_definition(entity_id="factor_custom_alpha"))
    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO models(entity_id, name, status) VALUES ('m1', '模型', 'published')")
        connection.execute(
            "INSERT INTO model_versions(entity_id, version_id, status, quality_status) VALUES ('m1', 'v1', 'published', 'passed')"
        )
        connection.execute(
            "INSERT INTO model_factor_versions(model_entity_id, model_version_id, factor_entity_id, factor_version_id) "
            "VALUES ('m1', 'v1', 'factor_custom_alpha', 'v1')"
        )
    with pytest.raises(ValueError, match="模型"):
        repository.delete("factor_custom_alpha", "v1")
    assert repository.get("factor_custom_alpha", "v1") is not None

    repository.import_definition(_definition(entity_id="factor_custom_beta"))
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_registry(run_id, run_type) VALUES ('20240102-000000-0001', 'research')"
        )
        connection.execute(
            "INSERT INTO research_runs(run_id, name, status, config_json) VALUES (?, '研究', 'queued', ?)",
            (
                "20240102-000000-0001",
                json.dumps({"factor_versions": [{"factor_id": "custom_beta", "version_id": "v1"}]}),
            ),
        )
    with pytest.raises(ValueError, match="研究任务「研究」"):
        repository.delete("factor_custom_beta", "v1")
    assert repository.get("factor_custom_beta", "v1") is not None

    repository.import_definition(_definition(entity_id="factor_custom_amp", name="日内振幅"))
    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO run_registry(run_id, run_type) VALUES ('20260903-215828-0001', 'research')")
        connection.execute(
            "INSERT INTO research_runs(run_id, name, status, config_json) VALUES (?, '手动因子诊断：日内振幅', 'queued', ?)",
            (
                "20260903-215828-0001",
                json.dumps(
                    {
                        "factor_versions": [{"factor_id": "factor_custom_amp", "version_id": "v1"}],
                        "pit_snapshot": {"captured_at": "manual-preview"},
                    }
                ),
            ),
        )
    repository.delete("factor_custom_amp", "v1")
    assert repository.get("factor_custom_amp", "v1") is None


def test_factor_rename_updates_name_and_accepts_unprefixed_id(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition(entity_id="factor_auto_job_1", name="自动候选 1"))
    renamed = repository.rename("auto_job_1", "日内振幅改名")
    assert renamed == {"entity_id": "factor_auto_job_1", "name": "日内振幅改名"}
    assert repository.get("factor_auto_job_1", "v1")["name"] == "日内振幅改名"
    with pytest.raises(ValueError, match="找不到"):
        repository.rename("factor_missing", "不会保存")


def test_batch_publish_is_atomic_and_batch_deprecate_is_supported(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    repository.import_definition(_definition(entity_id="factor_volatility_5", input_fields=["volatility_5"]))

    published = repository.publish_many(
        [
            {"entity_id": "factor_momentum_5", "version_id": "v1"},
            {"entity_id": "factor_volatility_5", "version_id": "v1"},
        ]
    )
    assert [item["status"] for item in published["items"]] == ["published", "published"]

    repository.import_definition(_definition(entity_id="factor_bad_quality", quality_status="warning"))
    repository.import_definition(_definition(entity_id="factor_good_again"))
    with pytest.raises(ValueError, match="quality"):
        repository.publish_many(
            [
                {"entity_id": "factor_good_again", "version_id": "v1"},
                {"entity_id": "factor_bad_quality", "version_id": "v1"},
            ]
        )
    assert repository.get("factor_good_again", "v1")["status"] == "draft"
    assert repository.get("factor_bad_quality", "v1")["status"] == "draft"

    deprecated = repository.deprecate_many(
        [
            {"entity_id": "factor_momentum_5", "version_id": "v1"},
            {"entity_id": "factor_volatility_5", "version_id": "v1"},
        ]
    )
    assert [item["status"] for item in deprecated["items"]] == ["deprecated", "deprecated"]


def test_upstream_lineage_rejects_fake_and_circular_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition(entity_id="factor_base", version_id="v1"))
    repository.publish("factor_base", "v1")
    repository.import_definition(
        _definition(
            entity_id="factor_child",
            version_id="v1",
            input_fields=["momentum_5"],
            upstream_factor_versions=[{"entity_id": "factor_base", "version_id": "v1"}],
        )
    )
    assert repository.publish("factor_child", "v1")["status"] == "published"
    with pytest.raises(ValueError, match="upstream"):
        repository.import_definition(
            _definition(
                entity_id="factor_fake",
                version_id="v1",
                upstream_factor_versions=[{"entity_id": "does_not_exist", "version_id": "v1"}],
            )
        )


def test_batch_diagnosis_reports_quality_and_rejects_target_fields(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    result = repository.diagnose([{"entity_id": "factor_momentum_5", "version_id": "v1"}])
    assert result["items"][0]["factor_entity_id"] == "factor_momentum_5"
    assert result["items"][0]["coverage"] == pytest.approx(1.0)
    assert result["items"][0]["missing_rows"] == 0
    with pytest.raises(ValueError, match="target-only"):
        repository.diagnose([{"entity_id": "label", "version_id": "v1"}])


def test_factor_api_exposes_catalog_actions_without_detail_implementation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    client = TestClient(create_app(repository.settings, repository.database))

    response = client.get("/api/factors", params={"category": "技术", "lifecycle": "draft"})
    assert response.status_code == 200
    assert response.json()["items"][0]["detail_url"] == "/factors/factor_momentum_5/versions/v1"
    assert client.post("/api/factors/diagnose", json={"items": [{"entity_id": "factor_momentum_5", "version_id": "v1"}]}).status_code == 200
    assert client.post("/api/factors/factor_momentum_5/v1/publish").status_code == 200
    assert client.post("/api/factors/factor_momentum_5/v1/deprecate").status_code == 200
    assert client.post(
        "/api/factors/quality-diagnose",
        json={"items": [{"entity_id": "factor_momentum_5", "version_id": "v1"}]},
    ).status_code == 200
    invalid_json = client.post(
        "/api/factors/import",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert invalid_json.status_code == 400
    assert invalid_json.json()["error_code"] == "FACTOR_LIBRARY_INVALID"


def test_factor_api_supports_atomic_batch_actions_and_delete_rules(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_definition(_definition())
    repository.import_definition(_definition(entity_id="factor_volatility_5", input_fields=["volatility_5"]))
    repository.import_definition(_definition(entity_id="factor_custom_alpha"))
    repository.import_definition(_definition(entity_id="factor_auto_20260904_011443_0001_0", name="自动候选 1"))
    client = TestClient(create_app(repository.settings, repository.database))

    response = client.post(
        "/api/factors/publish",
        json={
            "items": [
                {"entity_id": "factor_momentum_5", "version_id": "v1"},
                {"entity_id": "factor_volatility_5", "version_id": "v1"},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["count"] == 2

    renamed = client.patch("/api/factors/auto_20260904_011443_0001_0", json={"name": "自动候选甲"})
    assert renamed.status_code == 200
    assert renamed.json() == {"entity_id": "factor_auto_20260904_011443_0001_0", "name": "自动候选甲"}
    assert client.patch("/api/factors/factor_missing", json={"name": "x"}).status_code == 404
    assert client.patch("/api/factors/factor_custom_alpha", json={"name": "  "}).status_code == 400

    deleted = client.delete("/api/factors/auto_20260904_011443_0001_0/v1")
    assert deleted.status_code == 204
    assert client.get("/api/factors/factor_auto_20260904_011443_0001_0/v1").status_code == 404

    missing = client.delete("/api/factors/factor_custom_alpha/v9")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "FACTOR_LIBRARY_INVALID"

    builtin = client.delete("/api/factors/factor_momentum_5/v1")
    assert builtin.status_code == 409
    assert "系统自带" in builtin.json()["message"]

    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO strategies(entity_id, name, status) VALUES ('s1', '策略', 'published')")
        connection.execute(
            "INSERT INTO strategy_versions(entity_id, version_id, status, quality_status) VALUES ('s1', 'v1', 'published', 'passed')"
        )
        connection.execute(
            "INSERT INTO strategy_factor_versions(strategy_entity_id, strategy_version_id, factor_entity_id, factor_version_id) "
            "VALUES ('s1', 'v1', 'factor_custom_alpha', 'v1')"
        )
    referenced = client.delete("/api/factors/factor_custom_alpha/v1")
    assert referenced.status_code == 409
    assert referenced.json()["error_code"] == "FACTOR_LIBRARY_INVALID"
    assert "策略" in referenced.json()["message"]

    response = client.post(
        "/api/factors/deprecate",
        json={"items": [{"entity_id": "factor_momentum_5", "version_id": "v1"}]},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "deprecated"
