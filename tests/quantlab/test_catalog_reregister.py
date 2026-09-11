from pathlib import Path

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.catalog import DatasetCatalog


def _catalog(tmp_path: Path) -> tuple[Settings, Database, DatasetCatalog]:
    data_root = tmp_path / "data"
    (data_root / "source_tables").mkdir(parents=True)
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    return settings, database, DatasetCatalog(settings, database)


def test_reregister_published_dataset_keeps_copied_absolute_path(tmp_path: Path) -> None:
    settings, database, catalog = _catalog(tmp_path)
    source = settings.data_root / "source_tables"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(entity_id, name, status) VALUES ('dataset_source_tables', '来源整理表', 'published')"
        )
        connection.execute(
            "INSERT INTO dataset_versions("
            "entity_id, version_id, path, row_count, fields_json, manifest_hash, metadata_json, status, quality_status"
            ") VALUES ("
            "'dataset_source_tables', 'current', '/Volumes/T2/quantlab/data/source_tables', 0, '[]', "
            "'sha256:old', '{\"kind\":\"directory\",\"content_fingerprint\":\"sha256:old\"}', 'published', 'passed')"
        )
    entry = {
        "entity_id": "dataset_source_tables",
        "name": "来源整理表",
        "kind": "directory",
        "path": "source_tables",
        "category": "source_tables",
        "path_alias": "data/source_tables",
    }

    item = catalog._register_entry(entry, source)

    with database.connect() as connection:
        row = connection.execute(
            "SELECT path, quality_status FROM dataset_versions "
            "WHERE entity_id='dataset_source_tables' AND version_id='current'"
        ).fetchone()
    assert item["entity_id"] == "dataset_source_tables"
    assert row["path"] == "/Volumes/T2/quantlab/data/source_tables"
    assert row["quality_status"] == "needs_review"
