import hashlib
import sqlite3
from pathlib import Path

import pytest

from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database


def test_artifact_registers_chinese_name_and_hash_per_run(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    output = settings.runtime_root / "results/run-1/metrics.csv"
    output.parent.mkdir(parents=True)
    output.write_text("total_return,0.2\n")
    database = Database(settings.database_path)
    database.initialize()
    database.register_run("20260902-120000-0001", "backtest")
    repository = ArtifactRepository(settings, database)
    artifact = repository.register(
        run_id="20260902-120000-0001",
        path=output,
        display_name="绩效指标",
        artifact_role="metrics",
    )
    assert artifact.display_name == "绩效指标"
    assert artifact.content_hash.startswith("sha256:")
    stored = repository.get(artifact.artifact_id)
    assert stored is not None
    assert stored["created_at"]


def test_artifact_rejects_file_outside_allowlist(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    with pytest.raises(ValueError, match="allowed"):
        ArtifactRepository(settings, database).register(
            run_id="20260902-120000-0001",
            path=tmp_path / "outside.csv",
            display_name="越界",
            artifact_role="metrics",
        )


def test_artifact_requires_existing_registered_run(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    output = settings.runtime_root / "results/run-1/metrics.csv"
    output.parent.mkdir(parents=True)
    output.write_text("total_return,0.2\n")
    database = Database(settings.database_path)
    database.initialize()
    repository = ArtifactRepository(settings, database)
    with pytest.raises(sqlite3.IntegrityError):
        repository.register(
            run_id="20260902-120000-9999",
            path=output,
            display_name="不存在运行",
            artifact_role="metrics",
        )

    database.register_run("20260902-120000-0001", "backtest")
    artifact = repository.register(
        run_id="20260902-120000-0001",
        path=output,
        display_name="绩效指标",
        artifact_role="metrics",
    )
    assert artifact.run_id == "20260902-120000-0001"


def test_artifact_hashes_large_file_without_read_bytes(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    output = settings.runtime_root / "results/run-1/large.bin"
    output.parent.mkdir(parents=True)
    payload = (b"quantlab-streaming-hash\n" * 131072) + b"tail"
    output.write_bytes(payload)
    database = Database(settings.database_path)
    database.initialize()
    database.register_run("20260902-120000-0001", "backtest")

    def fail_read_bytes(self):
        raise AssertionError("large artifact hashing must not call Path.read_bytes")

    monkeypatch.setattr(type(output), "read_bytes", fail_read_bytes)
    artifact = ArtifactRepository(settings, database).register(
        run_id="20260902-120000-0001",
        path=output,
        display_name="大文件",
        artifact_role="raw",
    )
    assert artifact.content_hash == "sha256:" + hashlib.sha256(payload).hexdigest()


def test_artifact_can_register_file_from_authoritative_read_root(tmp_path) -> None:
    data_root = tmp_path / "tushare_migration_data"
    data_root.mkdir()
    source = data_root / "registered-source.csv"
    source.write_text("trade_date,value\n20200102,1\n")
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    database.register_run("20260902-120000-0001", "research")

    artifact = ArtifactRepository(settings, database).register(
        run_id="20260902-120000-0001",
        path=source,
        display_name="权威数据引用",
        artifact_role="source",
    )

    assert artifact.path == "tushare_migration_data/registered-source.csv"
    assert not Path(artifact.path).is_absolute()
