from __future__ import annotations

import json
from pathlib import Path

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.lan_sync import LocalLanSource, pull_market, sync_results
from quantlab.services.machine_identity import load_machine_identity
from quantlab.services.result_archive import ResultArchiveService


def _settings(root: Path) -> Settings:
    data = root / "data"
    data.mkdir(parents=True)
    return Settings(project_root=root, data_root=data, calibration_root=root / "cal", runtime_root=root / "runtime")


def test_sync_results_copies_missing_run_and_tombstone_without_overwrite(tmp_path: Path) -> None:
    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    load_machine_identity(left.runtime_root)
    load_machine_identity(right.runtime_root)
    run_keep = "20260910-120000-1101"
    run_new = "20260910-120000-2202"
    keep_dir = left.runtime_root / "results" / run_keep
    keep_dir.mkdir(parents=True)
    (keep_dir / "metrics.json").write_text('{"local":1}', encoding="utf-8")
    peer_keep = right.runtime_root / "results" / run_keep
    peer_keep.mkdir(parents=True)
    (peer_keep / "metrics.json").write_text('{"peer":1}', encoding="utf-8")
    new_dir = right.runtime_root / "results" / run_new
    new_dir.mkdir(parents=True)
    (new_dir / "run.json").write_text(
        json.dumps({"schema": 1, "run_id": run_new, "machine_id": "aabbccdd", "status": "completed", "config": {"name": "对端"}, "steps": [], "artifacts": []}),
        encoding="utf-8",
    )
    marker = right.runtime_root / "results" / "_deleted" / "20260910-120000-3303.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"schema": 1, "run_id": "20260910-120000-3303", "machine_id": "aabbccdd"}), encoding="utf-8")

    database = Database(left.database_path)
    database.initialize()
    stats = sync_results(left, database, [LocalLanSource(right)])
    assert run_new in stats["pulled_runs"]
    assert "20260910-120000-3303" in stats["pulled_deleted"]
    assert json.loads((keep_dir / "metrics.json").read_text(encoding="utf-8"))["local"] == 1
    detail = ResultArchiveService(left, database).get(run_new)
    assert detail is not None
    assert detail["machine_id"] == "aabbccdd"
    assert (left.runtime_root / "results" / "_deleted" / "20260910-120000-3303.json").is_file()


def test_pull_market_copies_missing_and_overwrites_different(tmp_path: Path) -> None:
    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    (right.data_root / "raw").mkdir(parents=True)
    (right.data_root / "raw" / "x.parquet").write_bytes(b"new")
    (left.data_root / "raw").mkdir(parents=True)
    (left.data_root / "raw" / "x.parquet").write_bytes(b"old")
    (left.data_root / "raw" / "only-left.parquet").write_bytes(b"keep")
    (right.data_root / "raw" / "y.parquet").write_bytes(b"yy")
    stats = pull_market(left, LocalLanSource(right))
    assert stats["copied"] >= 2
    assert (left.data_root / "raw" / "x.parquet").read_bytes() == b"new"
    assert (left.data_root / "raw" / "y.parquet").read_bytes() == b"yy"
    assert (left.data_root / "raw" / "only-left.parquet").read_bytes() == b"keep"


def test_coordinate_results_copies_from_source_and_asks_peers(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import coordinate_results_sync

    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    load_machine_identity(left.runtime_root)
    load_machine_identity(right.runtime_root)
    run_id = "20260910-120000-5505"
    folder = right.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    (folder / "run.json").write_text(
        json.dumps({"schema": 1, "run_id": run_id, "machine_id": "aabbccdd", "status": "completed", "config": {"name": "对端"}, "steps": [], "artifacts": []}),
        encoding="utf-8",
    )
    asked: list[tuple[str, list[dict[str, object]]]] = []

    def ask(peer: dict, sources: list[dict[str, object]]) -> dict[str, list[str]]:
        asked.append((str(peer["machine_id"]), sources))
        return {}

    database = Database(left.database_path)
    database.initialize()
    stats = coordinate_results_sync(
        left,
        database,
        [{"machine_id": "peer", "host": "peer-host", "port": 8766, "self": False}],
        self_host="10.0.0.1",
        source_for=lambda host, port: LocalLanSource(right),
        ask_peer=ask,
    )
    assert run_id in stats["pulled_runs"]
    assert asked[0][0] == "peer"
    assert asked[0][1][0] == {"host": "10.0.0.1", "port": 8766}
    assert (left.runtime_root / "results" / run_id / "metrics.json").is_file()


def test_coordinate_market_pulls_when_source_is_not_self(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import coordinate_market_sync

    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    (right.data_root / "raw").mkdir(parents=True)
    (right.data_root / "raw" / "x.parquet").write_bytes(b"src")
    (left.data_root / "raw").mkdir(parents=True)
    (left.data_root / "raw" / "x.parquet").write_bytes(b"old")
    notified: list[tuple[str, str, int]] = []

    def ask(peer: dict, host: str, port: int) -> dict[str, int]:
        notified.append((str(peer["machine_id"]), host, port))
        return {"copied": 1}

    stats = coordinate_market_sync(
        left,
        [
            {"machine_id": "self", "host": "10.0.0.1", "port": 8766, "self": True},
            {"machine_id": "peer", "host": "peer-host", "port": 8766, "self": False},
            {"machine_id": "other", "host": "other-host", "port": 8766, "self": False},
        ],
        "peer",
        self_id="self",
        source_for=lambda host, port: LocalLanSource(right),
        ask_peer=ask,
    )
    assert (left.data_root / "raw" / "x.parquet").read_bytes() == b"src"
    assert stats["local"]["copied"] >= 1
    assert notified == [("other", "peer-host", 8766)]


def test_coordinate_market_self_source_does_not_rewrite_local(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import coordinate_market_sync

    left = _settings(tmp_path / "a")
    (left.data_root / "keep.parquet").write_bytes(b"mine")
    stats = coordinate_market_sync(
        left,
        [{"machine_id": "self", "host": "10.0.0.1", "port": 8766, "self": True}],
        "self",
        self_id="self",
        source_for=lambda host, port: LocalLanSource(left),
        ask_peer=lambda peer, host, port: {"copied": 0},
    )
    assert stats["local"]["copied"] == 0
    assert (left.data_root / "keep.parquet").read_bytes() == b"mine"
