from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database


def client(tmp_path: Path) -> TestClient:
    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data", calibration_root=tmp_path / "cal", runtime_root=tmp_path / "runtime")
    return TestClient(create_app(settings=settings, database=Database(settings.database_path)))


def test_settings_are_masked_and_persist_allowed_defaults(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["paths"]["data_root"] == str(tmp_path / "data")
    assert body["secrets"] == {"tushare_token": False}
    assert body["compute"] == {"fold_workers": 0, "bucket_workers": 0, "bucket_pool": "process"}
    assert body["compute_hint"]["cpu_count"] >= 1
    assert body["compute_hint"]["auto_workers"] >= 1
    saved = api.put("/api/settings", json={"defaults": {"top_n": 20, "rebalance_days": 3}})
    assert saved.status_code == 200
    assert saved.json()["defaults"]["top_n"] == 20
    assert api.get("/api/settings").json()["defaults"]["rebalance_days"] == 3


def test_settings_persist_compute_workers_without_touching_draft_defaults(tmp_path: Path) -> None:
    api = client(tmp_path)
    saved = api.put(
        "/api/settings",
        json={"compute": {"fold_workers": 2, "bucket_workers": 4, "bucket_pool": "thread"}},
    )
    assert saved.status_code == 200
    compute = saved.json()["compute"]
    assert compute["fold_workers"] == 2
    assert compute["bucket_workers"] == 4
    assert compute["bucket_pool"] == "thread"
    assert api.get("/api/settings").json()["defaults"]["top_n"] == 10
    bad = api.put("/api/settings", json={"compute": {"bucket_workers": -1}})
    assert bad.status_code == 400
    unknown = api.put("/api/settings", json={"compute": {"gpu_workers": 1}})
    assert unknown.status_code == 400
    reset = api.post("/api/settings/reset")
    assert reset.status_code == 200
    assert reset.json()["compute"]["bucket_workers"] == 0
    assert reset.json()["compute"]["bucket_pool"] == "process"


def test_saved_compute_is_used_by_worker_counts(tmp_path: Path, monkeypatch) -> None:
    from quantlab.services.backtest_job import fold_worker_count
    from quantlab.services.bucket_equity import bucket_worker_count

    monkeypatch.delenv("QUANTLAB_FOLD_WORKERS", raising=False)
    monkeypatch.delenv("QUANTLAB_BUCKET_WORKERS", raising=False)
    monkeypatch.setattr("quantlab.services.backtest_job.cpu_count", lambda: 10)
    monkeypatch.setattr("quantlab.services.bucket_equity.cpu_count", lambda: 10)
    api = client(tmp_path)
    api.put("/api/settings", json={"compute": {"fold_workers": 3, "bucket_workers": 4}})
    assert fold_worker_count(90) == 3
    assert bucket_worker_count(90) == 4


def test_settings_reject_unsafe_mutation_and_support_connection_scan_reset(tmp_path: Path) -> None:
    api = client(tmp_path)
    bad = api.put("/api/settings", json={"paths": {"data_root": str(tmp_path / "escape")}, "host": "0.0.0.0"})
    assert bad.status_code == 400
    assert api.post("/api/settings/test-connection").json()["ok"] is True
    scan = api.post("/api/settings/scan")
    assert scan.status_code == 200
    assert scan.json()["manual"] is True
    api.put("/api/settings", json={"defaults": {"top_n": 20}})
    reset = api.post("/api/settings/reset")
    assert reset.status_code == 200
    assert reset.json()["defaults"]["top_n"] == 10


def test_settings_page_is_separate_and_does_not_auto_run(tmp_path: Path) -> None:
    api = client(tmp_path)
    page = api.get("/settings")
    assert page.status_code == 200
    assert "系统设置" in page.text
    assert "/api/backtests" not in page.text
    assert 'id="bucket-workers"' in page.text
    assert 'id="fold-workers"' in page.text
    assert 'id="bucket-pool"' in page.text
    assert "QUANTLAB_BUCKET_WORKERS" in page.text
    assert "QUANTLAB_FOLD_WORKERS" in page.text
    assert "QUANTLAB_BUCKET_POOL" in page.text
    assert "不要按 CPU 核数去开分层进程" in page.text
    assert "16GB" in page.text
    assert "不必重启页面服务" in page.text
