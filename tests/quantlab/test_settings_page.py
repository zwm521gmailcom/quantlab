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
    assert body["paths"]["data_root"] == "data"
    assert body["paths"]["project_root"] == "."
    assert body["paths"]["runtime_root"] == "runtime"
    assert body["paths"]["calibration_root"] == "cal"
    assert body["paths"]["results_root"] == "runtime/results"
    assert all(not Path(value).is_absolute() for value in body["paths"].values())
    assert body["secrets"] == {"tushare_token": False}
    assert body["compute"] == {"fold_workers": 0, "bucket_workers": 0, "bucket_pool": "process"}
    assert body["lan"] == {"market_sync_at_0400": False}
    assert body["compute_hint"]["cpu_count"] >= 1
    assert body["compute_hint"]["auto_workers"] >= 1
    assert body["compute_hint"]["max_concurrent_backtests"] == 1
    assert "safe_fold_workers" in body["compute_hint"]
    assert body["machine"]["machine_id"]
    assert 10 <= int(body["machine"]["serial_prefix"]) <= 99
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


def test_settings_persist_lan_market_sync_flag_without_touching_machine(tmp_path: Path) -> None:
    api = client(tmp_path)
    machine_id = api.get("/api/settings").json()["machine"]["machine_id"]
    saved = api.put("/api/settings", json={"lan": {"market_sync_at_0400": True}})
    assert saved.status_code == 200
    assert saved.json()["lan"]["market_sync_at_0400"] is True
    assert saved.json()["defaults"]["top_n"] == 10
    assert saved.json()["machine"]["machine_id"] == machine_id
    unknown = api.put("/api/settings", json={"lan": {"mirror_delete": True}})
    assert unknown.status_code == 400
    reset = api.post("/api/settings/reset")
    assert reset.status_code == 200
    assert reset.json()["lan"]["market_sync_at_0400"] is False
    assert reset.json()["machine"]["machine_id"] == machine_id


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
    assert all(not Path(item["path"]).is_absolute() for item in scan.json()["roots"])
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
    assert 'id="machine-id"' in page.text
    assert 'id="serial-prefix"' in page.text
    assert 'id="machine-id" type="text" readonly' in page.text
    assert 'id="serial-prefix-save"' in page.text
    assert 'id="serial-prefix" type="number" min="10" max="99"' in page.text
    assert "readonly" not in page.text.split('id="serial-prefix"')[1].split(">")[0]
    assert 'id="lan-peers"' in page.text
    assert 'id="lan-sync-results"' in page.text
    assert 'id="lan-sync-market"' in page.text
    assert 'id="lan-market-0400"' in page.text
    assert "8766" in page.text
    assert "防火墙" in page.text
    assert "局域网" in page.text
    assert "GitHub" in page.text
    assert "git pull" in page.text
    assert "打开页面" in page.text or "打开页面" in Path("quantlab/web/assets/settings.js").read_text(encoding="utf-8")
    assert "8765" in page.text
    assert "同时回测固定 1 条" in page.text or "同时回测固定 1 条" in Path("quantlab/web/assets/settings.js").read_text(encoding="utf-8")


def test_settings_can_update_serial_prefix_but_not_machine_id(tmp_path: Path) -> None:
    api = client(tmp_path)
    machine_id = api.get("/api/settings").json()["machine"]["machine_id"]
    saved = api.put("/api/settings", json={"machine": {"serial_prefix": 42}})
    assert saved.status_code == 200
    body = saved.json()["machine"]
    assert body["serial_prefix"] == 42
    assert body["machine_id"] == machine_id
    assert api.get("/api/settings").json()["defaults"]["top_n"] == 10
    bad = api.put("/api/settings", json={"machine": {"serial_prefix": 9}})
    assert bad.status_code == 400
    locked = api.put("/api/settings", json={"machine": {"machine_id": "ffffffff"}})
    assert locked.status_code == 400
    assert api.get("/api/settings").json()["machine"]["machine_id"] == machine_id
    assert api.get("/api/settings").json()["machine"]["serial_prefix"] == 42


def test_lan_peers_include_self_and_market_sync_rejects_unknown_machine(tmp_path: Path) -> None:
    api = client(tmp_path)
    listed = api.get("/api/lan/peers")
    assert listed.status_code == 200
    peers = listed.json()["peers"]
    assert listed.json()["lan_port"] == 8766
    assert len(peers) == 1
    assert peers[0]["self"] is True
    assert peers[0]["machine_id"] == api.get("/api/settings").json()["machine"]["machine_id"]
    assert int(peers[0]["ui_port"]) == 8765
    assert str(peers[0]["ui_url"]).startswith("http://")
    assert str(peers[0]["ui_url"]).endswith(":8765/")
    missing = api.post("/api/lan/sync/market", json={"machine_id": "deadbeef"})
    assert missing.status_code == 404
    local = api.post("/api/lan/sync/market", json={})
    assert local.status_code == 200
    assert local.json()["source_machine_id"] == peers[0]["machine_id"]
    results = api.post("/api/lan/sync/results")
    assert results.status_code == 200
    assert results.json()["pulled_runs"] == []
