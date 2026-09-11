from __future__ import annotations

import json
from pathlib import Path

import httpx

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.lan_sync import HttpLanSource, LocalLanSource, SyncProgress, bind_sync_progress, pull_market, pull_results, sync_progress, sync_results
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
    (right.data_root / "raw" / "x.parquet").write_bytes(b"new-bytes")
    (left.data_root / "raw").mkdir(parents=True)
    (left.data_root / "raw" / "x.parquet").write_bytes(b"old")
    (left.data_root / "raw" / "only-left.parquet").write_bytes(b"keep")
    (right.data_root / "raw" / "y.parquet").write_bytes(b"yy")
    stats = pull_market(left, LocalLanSource(right))
    assert stats["copied"] >= 2
    assert (left.data_root / "raw" / "x.parquet").read_bytes() == b"new-bytes"
    assert (left.data_root / "raw" / "y.parquet").read_bytes() == b"yy"
    assert (left.data_root / "raw" / "only-left.parquet").read_bytes() == b"keep"
    assert stats["skipped"] == 0


def test_pull_market_skips_identical_file_without_downloading(tmp_path: Path) -> None:
    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    (right.data_root / "canonical.parquet").write_bytes(b"same-bytes")
    first = pull_market(left, LocalLanSource(right), categories=["canonical"])
    assert first["copied"] == 1
    assert first["skipped"] == 0

    class Boom(LocalLanSource):
        def read_data_file(self, rel: str) -> bytes:
            raise AssertionError("identical file should not be downloaded")

    second = pull_market(left, Boom(right), categories=["canonical"])
    assert second["copied"] == 0
    assert second["skipped"] == 1
    assert (left.data_root / "canonical.parquet").read_bytes() == b"same-bytes"


def test_pull_market_only_copies_selected_category(tmp_path: Path) -> None:
    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    (right.data_root / "canonical.parquet").write_bytes(b"can")
    (right.data_root / "raw").mkdir(parents=True)
    (right.data_root / "raw" / "x.parquet").write_bytes(b"raw")
    (right.data_root / "derived").mkdir(parents=True)
    (right.data_root / "derived" / "f.parquet").write_bytes(b"fac")
    stats = pull_market(left, LocalLanSource(right), categories=["canonical"])
    assert stats["copied"] == 1
    assert stats["categories"] == ["canonical"]
    assert (left.data_root / "canonical.parquet").read_bytes() == b"can"
    assert not (left.data_root / "raw").exists()
    assert not (left.data_root / "derived").exists()


def test_pull_results_skips_queued_remote_and_upgrades_local_stub(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import LocalLanSource, pull_results

    dest = _settings(tmp_path / "dst")
    source = _settings(tmp_path / "src")
    queued_id = "20260910-120000-4101"
    done_id = "20260910-120000-4202"
    (source.runtime_root / "results" / queued_id).mkdir(parents=True)
    (source.runtime_root / "results" / queued_id / "run.json").write_text(
        json.dumps({"run_id": queued_id, "status": "queued", "config": {"name": "还在排队"}}),
        encoding="utf-8",
    )
    stub = dest.runtime_root / "results" / done_id
    stub.mkdir(parents=True)
    (stub / "run.json").write_text(
        json.dumps({"run_id": done_id, "status": "queued", "config": {"name": "空目录"}}),
        encoding="utf-8",
    )
    done = source.runtime_root / "results" / done_id
    done.mkdir(parents=True)
    (done / "run.json").write_text(
        json.dumps({"run_id": done_id, "status": "completed", "config": {"name": "空目录"}}),
        encoding="utf-8",
    )
    (done / "metrics.json").write_text('{"return":0.3}', encoding="utf-8")
    stats = pull_results(dest, LocalLanSource(source))
    assert queued_id not in stats["pulled_runs"]
    assert not (dest.runtime_root / "results" / queued_id).exists()
    assert done_id in stats["pulled_runs"]
    assert json.loads((stub / "run.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert (stub / "metrics.json").read_text(encoding="utf-8") == '{"return":0.3}'


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
    (right.data_root / "raw" / "x.parquet").write_bytes(b"src-bytes")
    (left.data_root / "raw").mkdir(parents=True)
    (left.data_root / "raw" / "x.parquet").write_bytes(b"old")
    notified: list[tuple[str, str, int]] = []

    def ask(peer: dict, host: str, port: int, categories=None) -> dict[str, int]:
        notified.append((str(peer["machine_id"]), host, port, tuple(categories or ())))
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
    assert (left.data_root / "raw" / "x.parquet").read_bytes() == b"src-bytes"
    assert stats["local"]["copied"] >= 1
    assert notified == [("other", "peer-host", 8766, ("canonical", "derived", "raw", "source_tables"))]


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
        ask_peer=lambda peer, host, port, categories=None: {"copied": 0},
    )
    assert stats["local"]["copied"] == 0
    assert (left.data_root / "keep.parquet").read_bytes() == b"mine"
    snap = sync_progress().snapshot()
    assert snap["status"] == "completed"
    assert snap["kind"] == "market"
    assert snap["percent"] == 100
    assert snap["total"] >= 0
    assert snap["done"] == snap["total"]


def test_sync_progress_reports_counts_and_percent() -> None:
    progress = SyncProgress()
    assert progress.snapshot()["status"] == "idle"
    assert progress.snapshot()["percent"] == 0
    progress.start("market", message="正在同步行情…", source_machine_id="peer-1")
    progress.update(done=1, total=4, detail="raw/a.parquet")
    snap = progress.snapshot()
    assert snap["status"] == "running"
    assert snap["kind"] == "market"
    assert snap["done"] == 1
    assert snap["total"] == 4
    assert snap["percent"] == 25
    assert snap["source_machine_id"] == "peer-1"
    progress.tick(detail="raw/b.parquet")
    assert progress.snapshot()["done"] == 2
    assert progress.snapshot()["percent"] == 50
    progress.finish(message="行情已同步")
    done = progress.snapshot()
    assert done["status"] == "completed"
    assert done["done"] == 4
    assert done["percent"] == 100


def test_pull_market_ticks_each_file(tmp_path: Path) -> None:
    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    (right.data_root / "raw").mkdir(parents=True)
    (right.data_root / "raw" / "x.parquet").write_bytes(b"new")
    (right.data_root / "raw" / "y.parquet").write_bytes(b"yy")
    (left.data_root / "raw").mkdir(parents=True)
    (left.data_root / "raw" / "x.parquet").write_bytes(b"old")
    percents: list[int] = []

    class Recording(SyncProgress):
        def tick(self, *, detail: str = "") -> None:
            super().tick(detail=detail)
            percents.append(int(self.snapshot()["percent"]))

    progress = Recording()
    progress.start("market")
    pull_market(left, LocalLanSource(right), progress=progress)
    assert percents[-1] == 100
    assert progress.snapshot()["done"] == progress.snapshot()["total"]
    assert progress.snapshot()["total"] >= 2


def test_pull_results_ticks_copied_files(tmp_path: Path) -> None:
    dest = _settings(tmp_path / "dst")
    source = _settings(tmp_path / "src")
    run_id = "20260910-120000-7707"
    folder = source.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": "completed", "config": {"name": "对端"}}),
        encoding="utf-8",
    )
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    progress = SyncProgress()
    progress.start("results")
    stats = pull_results(dest, LocalLanSource(source), progress=progress)
    assert run_id in stats["pulled_runs"]
    snap = progress.snapshot()
    assert snap["total"] >= 2
    assert snap["done"] == snap["total"]
    assert snap["percent"] == 100


def test_coordinate_results_fills_global_progress(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import coordinate_results_sync

    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    load_machine_identity(left.runtime_root)
    run_id = "20260910-120000-8808"
    folder = right.runtime_root / "results" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": "completed", "config": {"name": "对端"}}),
        encoding="utf-8",
    )
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    database = Database(left.database_path)
    database.initialize()
    coordinate_results_sync(
        left,
        database,
        [{"machine_id": "peer", "host": "peer-host", "port": 8766, "self": False}],
        self_host="10.0.0.1",
        source_for=lambda host, port: LocalLanSource(right),
        ask_peer=lambda peer, sources: {},
    )
    snap = sync_progress().snapshot()
    assert snap["kind"] == "results"
    assert snap["status"] == "completed"
    assert snap["percent"] == 100
    assert snap["done"] == snap["total"]
    assert snap["total"] >= 1


def test_http_source_falls_back_to_ui_plans_when_old_lan_index_omits_them(tmp_path: Path) -> None:
    dest = _settings(tmp_path / "dst")
    plan_id = "plan-fromui00001"
    ui_plan = {
        "plan_id": plan_id,
        "name": "收益增强验证 · 成分滚动模型",
        "status": "draft",
        "closed": False,
        "created_at": "2026-09-11T00:00:00+00:00",
        "updated_at": "2026-09-11T00:00:00+00:00",
        "items": [
            {
                "item_id": "item-fromui0001",
                "sort_order": 1,
                "selected": True,
                "name": "未开始",
                "config": {"name": "未开始"},
                "status": "pending",
                "run_id": None,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/results/index":
            return httpx.Response(200, json={"runs": [], "deleted": []})
        if path == "/hello":
            return httpx.Response(
                200,
                json={"service": "quantlab-lan", "machine_id": "5ff7b173", "ui_port": 8765, "host": "192.168.1.39"},
            )
        if path.startswith("/plans/"):
            return httpx.Response(404, json={"detail": "not found"})
        if path == "/api/backtest-plans":
            return httpx.Response(200, json={"items": [ui_plan]})
        if path == f"/api/backtest-plans/{plan_id}":
            return httpx.Response(200, json=ui_plan)
        return httpx.Response(404)

    remote = HttpLanSource(
        "http://192.168.1.39:8766",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    stats = pull_results(dest, remote)
    assert plan_id in stats["pulled_plans"]
    copied = dest.runtime_root / "results" / "_plans" / f"{plan_id}.json"
    assert copied.is_file()
    assert json.loads(copied.read_text(encoding="utf-8"))["name"] == "收益增强验证 · 成分滚动模型"


def test_pull_results_copies_plan_snapshot_even_when_run_already_exists(tmp_path: Path) -> None:
    from quantlab.services.result_sync import PLANS_DIR_NAME

    dest = _settings(tmp_path / "dst")
    source = _settings(tmp_path / "src")
    run_id = "20260910-120000-6611"
    for settings in (dest, source):
        folder = settings.runtime_root / "results" / run_id
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps({"run_id": run_id, "status": "completed", "config": {"name": "已有"}}),
            encoding="utf-8",
        )
    plan_id = "plan-frompeer001"
    plan_dir = source.runtime_root / "results" / PLANS_DIR_NAME
    plan_dir.mkdir(parents=True)
    payload = {"schema": 1, "plan_id": plan_id, "name": "对端计划", "status": "completed", "items": []}
    (plan_dir / f"{plan_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    stats = pull_results(dest, LocalLanSource(source))
    assert plan_id in stats["pulled_plans"]
    copied = dest.runtime_root / "results" / PLANS_DIR_NAME / f"{plan_id}.json"
    assert copied.is_file()
    assert json.loads(copied.read_text(encoding="utf-8"))["name"] == "对端计划"
    again = pull_results(dest, LocalLanSource(source))
    assert again["pulled_plans"] == []
    assert again["pulled_runs"] == []


def test_pull_results_skips_source_status_when_local_run_already_finished(tmp_path: Path) -> None:
    dest = _settings(tmp_path / "dst")
    source = _settings(tmp_path / "src")
    done_id = "20260910-120000-7711"
    for settings in (dest, source):
        folder = settings.runtime_root / "results" / done_id
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps({"run_id": done_id, "status": "completed", "config": {"name": "已有"}}),
            encoding="utf-8",
        )
        (folder / "metrics.json").write_text("{}", encoding="utf-8")
    inner = LocalLanSource(source)
    reads: list[str] = []

    class CountingSource:
        def results_index(self):
            return inner.results_index()

        def result_tree(self, run_id):
            reads.append(f"tree:{run_id}")
            return inner.result_tree(run_id)

        def read_result_file(self, run_id, rel):
            reads.append(f"file:{run_id}:{rel}")
            return inner.read_result_file(run_id, rel)

        def read_deleted(self, run_id):
            return inner.read_deleted(run_id)

        def read_plan(self, plan_id):
            return inner.read_plan(plan_id)

    stats = pull_results(dest, CountingSource())
    assert stats["pulled_runs"] == []
    assert not any(item.startswith("file:") or item.startswith("tree:") for item in reads)


def test_coordinate_results_stops_after_unchanged_plan_round(tmp_path: Path) -> None:
    from quantlab.services.lan_sync import coordinate_results_sync
    from quantlab.services.result_sync import PLANS_DIR_NAME

    left = _settings(tmp_path / "a")
    right = _settings(tmp_path / "b")
    load_machine_identity(left.runtime_root)
    load_machine_identity(right.runtime_root)
    run_id = "20260910-120000-8811"
    plan_id = "plan-alreadyhere01"
    payload = json.dumps({"schema": 1, "plan_id": plan_id, "name": "已有计划", "status": "completed", "items": []})
    for settings in (left, right):
        folder = settings.runtime_root / "results" / run_id
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps({"run_id": run_id, "status": "completed", "config": {"name": "已有"}}),
            encoding="utf-8",
        )
        plans = settings.runtime_root / "results" / PLANS_DIR_NAME
        plans.mkdir(parents=True)
        (plans / f"{plan_id}.json").write_text(payload, encoding="utf-8")
    asks = {"n": 0}

    def ask(peer, sources):
        asks["n"] += 1
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
    assert stats["rounds"] == 1
    assert stats["pulled_runs"] == []
    assert stats["pulled_plans"] == []
    assert asks["n"] == 1


def test_sync_progress_survives_reset_via_file(tmp_path: Path) -> None:
    left = _settings(tmp_path / "p")
    progress = SyncProgress()
    bind_sync_progress(left, progress)
    progress.start("results", message="正在同步回测产物…")
    progress.update(done=3, total=10)
    progress.finish(message="回测产物已同步")
    sync_progress().reset()
    restored = bind_sync_progress(left)
    snap = restored.snapshot()
    assert snap["status"] == "completed"
    assert snap["kind"] == "results"
    assert snap["percent"] == 100
    assert snap["message"] == "回测产物已同步"
