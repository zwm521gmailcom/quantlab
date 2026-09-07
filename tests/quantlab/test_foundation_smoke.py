from quantlab.config import Settings
from quantlab.repositories.database import Database


def test_foundation_can_initialize_runtime_database(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "tushare_migration_data",
        calibration_root=tmp_path / "tushare_migration_calibration",
        runtime_root=tmp_path / "quantlab_runtime",
    )
    database = Database(settings.database_path)
    database.initialize()
    assert settings.database_path.exists()
