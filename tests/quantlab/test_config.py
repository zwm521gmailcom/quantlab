from pathlib import Path

import pytest

from quantlab.config import Settings
from quantlab.cli import main


def test_settings_expose_authoritative_roots_and_controlled_write_roots(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )

    assert settings.display_path(settings.data_root) == "tushare_migration_data"
    assert settings.display_path(settings.calibration_root) == "tushare_migration_calibration"
    assert settings.display_path(settings.runtime_root) == "quantlab_runtime"
    assert not Path(settings.display_path(settings.data_root)).is_absolute()
    assert settings.is_read_path_allowed(settings.data_root / "source_tables/daily.parquet")
    assert settings.is_read_path_allowed(settings.calibration_root / "run/summary.json")
    assert settings.is_write_path_allowed(settings.runtime_root / "results/run-1")


def test_settings_reject_paths_outside_authoritative_or_runtime_roots(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )

    with pytest.raises(ValueError, match="allowed") as read_error:
        settings.require_read_path(tmp_path / "secret.txt")
    assert "secret.txt" in str(read_error.value)
    assert str(tmp_path) not in str(read_error.value)
    with pytest.raises(ValueError, match="allowed") as write_error:
        settings.require_write_path(tmp_path / "outside")
    assert str(tmp_path) not in str(write_error.value)


def test_settings_require_localhost() -> None:
    settings = Settings()
    assert settings.host == "127.0.0.1"
    with pytest.raises(ValueError, match="127.0.0.1"):
        settings.with_host("0.0.0.0")


def test_default_runtime_root_is_repository_root_not_python_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QUANTLAB_PROJECT_ROOT", str(tmp_path))
    settings = Settings()
    assert settings.database_path == tmp_path / "quantlab_runtime/db/quantlab.sqlite3"
    assert "quantlab/quantlab_runtime" not in str(settings.database_path)


def test_worktree_defaults_keep_runtime_and_data_inside_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees/quantlab-platform"
    worktree.mkdir(parents=True)
    settings = Settings(project_root=worktree)
    assert settings.runtime_root == worktree / "quantlab_runtime"
    assert settings.data_root == worktree / "data"
    assert settings.calibration_root == worktree / "data/calibration"


def test_settings_read_all_roots_from_environment(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    data_root = tmp_path / "data"
    calibration_root = tmp_path / "calibration"
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("QUANTLAB_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("QUANTLAB_DATA_ROOT", str(data_root))
    monkeypatch.setenv("QUANTLAB_CALIBRATION_ROOT", str(calibration_root))
    monkeypatch.setenv("QUANTLAB_RUNTIME_ROOT", str(runtime_root))

    settings = Settings()

    assert settings.project_root == project_root
    assert settings.data_root == data_root
    assert settings.calibration_root == calibration_root
    assert settings.runtime_root == runtime_root


def test_default_project_root_uses_working_directory_not_package_location(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("QUANTLAB_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert Settings().project_root == tmp_path


def test_cli_accepts_explicit_roots_for_init_db(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    data_root = tmp_path / "data"
    calibration_root = tmp_path / "calibration"
    runtime_root = tmp_path / "runtime"
    data_root.mkdir()
    calibration_root.mkdir()

    result = main(
        [
            "init-db",
            "--project-root",
            str(project_root),
            "--data-root",
            str(data_root),
            "--calibration-root",
            str(calibration_root),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert result == 0
    assert (runtime_root / "db/quantlab.sqlite3").is_file()


def test_settings_accept_relative_roots_and_round_trip_stored_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    settings = Settings(
        project_root=".",
        data_root="data",
        calibration_root="data/calibration",
        runtime_root="quantlab_runtime",
    )
    assert settings.display_path(settings.project_root) == "."
    assert settings.display_path(settings.data_root) == "data"
    assert settings.display_path(settings.runtime_root) == "quantlab_runtime"
    stored = settings.store_path(settings.data_root / "canonical.parquet")
    assert stored == "data/canonical.parquet"
    assert settings.require_read_path(stored) == (tmp_path / "data/canonical.parquet").resolve()
    assert settings.require_read_path(tmp_path / "data/canonical.parquet") == (
        tmp_path / "data/canonical.parquet"
    ).resolve()


def test_cli_accepts_relative_roots_for_init_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "calibration").mkdir()
    result = main(
        [
            "init-db",
            "--project-root",
            ".",
            "--data-root",
            "data",
            "--calibration-root",
            "calibration",
            "--runtime-root",
            "runtime",
        ]
    )
    assert result == 0
    assert (tmp_path / "runtime/db/quantlab.sqlite3").is_file()
