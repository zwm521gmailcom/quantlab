import hashlib
from pathlib import Path

import pytest

from quantlab.config import Settings
from quantlab.services.catalog import snapshot_authoritative_data


def test_snapshot_records_authoritative_file_fingerprints_without_copying_data(tmp_path: Path) -> None:
    data_root = tmp_path / "tushare_migration_data"
    calibration_root = tmp_path / "tushare_migration_calibration"
    data_root.mkdir()
    calibration_root.mkdir()
    (data_root / "manifest.json").write_text('{"version": "one"}')
    (calibration_root / "CURRENT").write_text("experiment-1")
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=calibration_root,
        runtime_root=tmp_path / "quantlab_runtime",
    )
    output = settings.runtime_root / "baselines/authoritative-data.json"

    snapshot_authoritative_data(settings, output)
    assert output.exists()
    assert hashlib.sha256((data_root / "manifest.json").read_bytes()).hexdigest() in output.read_text()
    assert not (tmp_path / "quantlab_runtime/baselines/manifest.json").exists()


@pytest.mark.parametrize("relative_output", ["authority.json", "../quantlab_runtime/results/baseline.json"])
def test_snapshot_rejects_output_outside_runtime_baselines(tmp_path: Path, relative_output: str) -> None:
    data_root = tmp_path / "tushare_migration_data"
    calibration_root = tmp_path / "tushare_migration_calibration"
    data_root.mkdir()
    calibration_root.mkdir()
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=calibration_root,
        runtime_root=tmp_path / "quantlab_runtime",
    )
    output = data_root / relative_output

    with pytest.raises(ValueError, match="baselines"):
        snapshot_authoritative_data(settings, output)


def test_snapshot_rejects_symlink_escape_from_baselines_root(tmp_path: Path) -> None:
    data_root = tmp_path / "tushare_migration_data"
    calibration_root = tmp_path / "tushare_migration_calibration"
    data_root.mkdir()
    calibration_root.mkdir()
    settings = Settings(
        project_root=tmp_path,
        data_root=data_root,
        calibration_root=calibration_root,
        runtime_root=tmp_path / "quantlab_runtime",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    baselines = settings.runtime_root / "baselines"
    baselines.mkdir(parents=True)
    (baselines / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="baselines"):
        snapshot_authoritative_data(settings, baselines / "escape/baseline.json")
