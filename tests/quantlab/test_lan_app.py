from pathlib import Path

from fastapi.testclient import TestClient

from quantlab.api.lan_app import create_lan_app
from quantlab.config import Settings
from quantlab.services.lan_peers import PeerRegistry, parse_beacon
from quantlab.services.machine_identity import load_machine_identity


def test_parse_beacon_and_ttl() -> None:
    raw = b'{"service":"quantlab","machine_id":"abcd1234","sync_port":8766,"serial_prefix":29,"host":"10.0.0.2","hostname":"box"}'
    peer = parse_beacon(raw, "10.0.0.9", now=100.0)
    assert peer is not None
    assert peer.host == "10.0.0.2"
    registry = PeerRegistry(ttl=15)
    registry.note(peer)
    assert registry.online(now=110, self_id="other")[0]["machine_id"] == "abcd1234"
    assert registry.online(now=120, self_id="other") == []


def test_prefer_lan_ip_skips_vpn_fakeip() -> None:
    from quantlab.services.lan_peers import prefer_lan_ip

    assert prefer_lan_ip(["198.18.0.1", "192.168.1.8", "127.0.0.1"]) == "192.168.1.8"
    assert prefer_lan_ip(["10.0.0.2", "172.16.0.4"]) == "10.0.0.2"
    assert prefer_lan_ip(["198.18.0.1", "127.0.0.1"]) == "127.0.0.1"


def test_lan_app_serves_results_and_rejects_escape(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data", calibration_root=tmp_path / "cal", runtime_root=tmp_path / "runtime")
    (settings.data_root).mkdir(parents=True)
    (settings.data_root / "mkt.parquet").write_bytes(b"mkt")
    load_machine_identity(settings.runtime_root)
    run_id = "20260910-120000-4404"
    folder = settings.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    client = TestClient(create_lan_app(settings))
    hello = client.get("/hello")
    assert hello.status_code == 200
    assert hello.json()["machine_id"]
    assert run_id in client.get("/results/index").json()["runs"]
    assert client.get(f"/results/{run_id}/file", params={"rel": "metrics.json"}).content == b"{}"
    assert client.get("/data/file", params={"rel": "../runtime/config/machine.json"}).status_code == 400
    assert client.get("/data/file", params={"rel": "mkt.parquet"}).content == b"mkt"
    assert "token" not in hello.json()
    bad_market = client.post("/pull-market", json={"source_host": "../evil", "source_port": 8766})
    assert bad_market.status_code == 400
    missing = client.post("/pull-market", json={})
    assert missing.status_code == 400
    bad_results = client.post("/pull-results", json={"sources": [{"host": "10.0.0.1/../x", "port": 8766}]})
    assert bad_results.status_code == 400


def test_http_source_pulls_via_lan_app(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import HttpLanSource, pull_market, pull_results

    source_root = tmp_path / "src"
    dest_root = tmp_path / "dst"
    source = Settings(project_root=source_root, data_root=source_root / "data", calibration_root=source_root / "cal", runtime_root=source_root / "runtime")
    dest = Settings(project_root=dest_root, data_root=dest_root / "data", calibration_root=dest_root / "cal", runtime_root=dest_root / "runtime")
    source.data_root.mkdir(parents=True)
    dest.data_root.mkdir(parents=True)
    load_machine_identity(source.runtime_root)
    load_machine_identity(dest.runtime_root)
    run_id = "20260910-120000-6606"
    folder = source.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_bytes(b"ok")
    (source.data_root / "mkt.parquet").write_bytes(b"mkt")
    client = TestClient(create_lan_app(source))
    remote = HttpLanSource("http://testserver", client=client)
    pulled = pull_results(dest, remote)
    market = pull_market(dest, remote)
    assert run_id in pulled["pulled_runs"]
    assert (dest.runtime_root / "results" / run_id / "metrics.json").read_bytes() == b"ok"
    assert market["copied"] == 1
    assert (dest.data_root / "mkt.parquet").read_bytes() == b"mkt"
