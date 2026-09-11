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


def test_settings_allow_lan_bind_but_reject_public_host() -> None:
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert Settings(host="0.0.0.0").host == "0.0.0.0"
    assert Settings(host="192.168.1.39").host == "192.168.1.39"
    assert Settings(host="localhost").host == "127.0.0.1"
    with pytest.raises(ValueError, match="private LAN"):
        Settings(host="8.8.8.8")
    with pytest.raises(ValueError, match="private LAN"):
        settings.with_host("1.2.3.4")


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


def test_cli_rejects_public_bind_host() -> None:
    with pytest.raises(SystemExit):
        main(["serve", "--host", "8.8.8.8"])


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


def test_settings_relocate_absolute_paths_from_copied_project(tmp_path: Path) -> None:
    project = tmp_path / "quantlab"
    data_root = project / "data"
    data_root.mkdir(parents=True)
    (data_root / "canonical.parquet").write_bytes(b"parquet")
    (data_root / "calibration").mkdir()
    settings = Settings(
        project_root=project,
        data_root=data_root,
        calibration_root=data_root / "calibration",
        runtime_root=project / "quantlab_runtime",
    )
    foreign = Path("/Volumes/T2/quantlab/data/canonical.parquet")
    nested = Path("/home/zwm521/桌面/quantlab/data/calibration/run.json")
    assert settings.require_read_path(foreign) == (data_root / "canonical.parquet").resolve()
    assert settings.require_read_path(nested) == (data_root / "calibration/run.json").resolve()
    assert settings.require_artifact_path("/old/quantlab/quantlab_runtime/results/run-1") == (
        project / "quantlab_runtime/results/run-1"
    ).resolve()


def test_settings_default_to_a_share_and_standard_ports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("QUANTLAB_ASSET", raising=False)
    monkeypatch.delenv("QUANTLAB_PORT", raising=False)
    monkeypatch.delenv("QUANTLAB_LAN_PORT", raising=False)
    settings = Settings(project_root=tmp_path, runtime_root=tmp_path / "runtime")
    assert settings.asset == "a_share"
    assert settings.asset_label == "A股"
    assert settings.port == 8765
    assert settings.lan_port == 8766


def test_settings_normalize_chinese_asset_alias(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path, runtime_root=tmp_path / "runtime", asset="数字货币")
    assert settings.asset == "crypto"
    assert settings.asset_label == "数字货币"


def test_settings_read_asset_and_ports_from_instance_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config/instance.json").write_text(
        '{"asset":"crypto","port":8775,"lan_port":8776}', encoding="utf-8"
    )
    settings = Settings(project_root=tmp_path, runtime_root=runtime)
    assert settings.asset == "crypto"
    assert settings.asset_label == "数字货币"
    assert settings.port == 8775
    assert settings.lan_port == 8776


def test_cli_asset_and_ports_override_instance_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config/instance.json").write_text(
        '{"asset":"a_share","port":8765,"lan_port":8766}', encoding="utf-8"
    )
    settings = Settings(
        project_root=tmp_path,
        runtime_root=runtime,
        asset="crypto",
        port=8775,
        lan_port=8776,
    )
    assert settings.asset == "crypto"
    assert settings.port == 8775
    assert settings.lan_port == 8776


def test_settings_env_overrides_instance_file(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config/instance.json").write_text(
        '{"asset":"a_share","port":8765,"lan_port":8766}', encoding="utf-8"
    )
    monkeypatch.setenv("QUANTLAB_ASSET", "crypto")
    monkeypatch.setenv("QUANTLAB_PORT", "8775")
    monkeypatch.setenv("QUANTLAB_LAN_PORT", "8776")
    settings = Settings(project_root=tmp_path, runtime_root=runtime)
    assert settings.asset == "crypto"
    assert settings.port == 8775
    assert settings.lan_port == 8776


def test_settings_reject_same_ui_and_lan_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="different"):
        Settings(project_root=tmp_path, port=8765, lan_port=8765)


def test_cli_rejects_unknown_asset_and_same_ports(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["serve", "--project-root", str(tmp_path), "--asset", "fx"])
    with pytest.raises(SystemExit):
        main(
            [
                "serve",
                "--project-root",
                str(tmp_path),
                "--port",
                "8765",
                "--lan-port",
                "8765",
            ]
        )


def test_cli_serve_uses_asset_and_ports(tmp_path: Path, monkeypatch) -> None:
    import uvicorn

    captured: dict[str, object] = {}

    def fake_run(app, host, port):
        captured["host"] = host
        captured["port"] = port
        captured["asset"] = app.state.settings.asset
        captured["lan_port"] = app.state.settings.lan_port

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("quantlab.services.lan_runtime.start_lan_sidecar", lambda app: None)
    result = main(
        [
            "serve",
            "--project-root",
            str(tmp_path),
            "--asset",
            "crypto",
            "--port",
            "8775",
            "--lan-port",
            "8776",
            "--host",
            "127.0.0.1",
        ]
    )
    assert result == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8775
    assert captured["asset"] == "crypto"
    assert captured["lan_port"] == 8776


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
