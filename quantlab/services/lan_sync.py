"""Copy result and market files between QuantLab trees. No SQLite merge."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.repositories.database import Database
from quantlab.services.lan_files import iter_rel_files, safe_under
from quantlab.services.lan_peers import LAN_PORT, validate_lan_host, validate_lan_port
from quantlab.services.result_sync import (
    PLANS_DIR_NAME,
    sync_result_catalog,
    validate_plan_snapshot_id,
)

MAX_SYNC_ROUNDS = 8
DELETED_DIR = "_deleted"
HTTP_TIMEOUT = 300.0
_FINISHED_STATUSES = {"completed", "failed"}
_SYNC_LOCK = threading.Lock()
_PROGRESS_FILE = "lan_sync_progress.json"


class SyncBusyError(RuntimeError):
    """Another LAN file sync is already running on this process."""


class SyncProgress:
    """Thread-safe snapshot for settings page: 总进度 done/total and percent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self._idle()
        self._persist_path: Path | None = None

    def _idle(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "kind": None,
            "source_machine_id": "",
            "done": 0,
            "total": 0,
            "percent": 0,
            "detail": "",
            "message": "",
            "error": "",
        }

    def bind(self, path: Path) -> None:
        self._persist_path = Path(path)
        self._restore()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def reset(self) -> None:
        with self._lock:
            self._state = self._idle()

    def start(self, kind: str, *, message: str = "", source_machine_id: str = "") -> None:
        with self._lock:
            self._state = {
                "status": "running",
                "kind": str(kind),
                "source_machine_id": str(source_machine_id or ""),
                "done": 0,
                "total": 0,
                "percent": 0,
                "detail": "",
                "message": str(message or ""),
                "error": "",
            }
        self._persist()

    def add_total(self, extra: int) -> None:
        with self._lock:
            self._state["total"] = int(self._state["total"]) + max(0, int(extra))
            self._recompute()
        self._persist()

    def update(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        detail: str | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            if done is not None:
                self._state["done"] = max(0, int(done))
            if total is not None:
                self._state["total"] = max(0, int(total))
            if detail is not None:
                self._state["detail"] = str(detail)
            if message is not None:
                self._state["message"] = str(message)
            self._recompute()
        self._persist()

    def tick(self, *, detail: str = "") -> None:
        with self._lock:
            self._state["done"] = int(self._state["done"]) + 1
            if detail:
                self._state["detail"] = str(detail)
            self._recompute()
        self._persist()

    def finish(self, *, message: str = "", error: str = "") -> None:
        with self._lock:
            failed = bool(error)
            self._state["status"] = "failed" if failed else "completed"
            self._state["error"] = str(error or "")
            if message:
                self._state["message"] = str(message)
            if not failed:
                total = int(self._state["total"])
                done = int(self._state["done"])
                if total and done < total:
                    self._state["done"] = total
                if total <= 0:
                    self._state["percent"] = 100
                else:
                    self._recompute()
            else:
                self._recompute()
        self._persist()

    def _recompute(self) -> None:
        total = int(self._state["total"])
        done = int(self._state["done"])
        if total <= 0:
            self._state["percent"] = 0
            return
        self._state["percent"] = min(100, int(round(100.0 * done / total)))

    def _persist(self) -> None:
        path = self._persist_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    def _restore(self) -> None:
        path = self._persist_path
        if path is None or not path.is_file():
            return
        with self._lock:
            if str(self._state.get("status") or "idle") != "idle":
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(data, dict):
                return
            state = self._idle()
            for key in state:
                if key in data:
                    state[key] = data[key]
            if str(state.get("status") or "") == "running":
                state["status"] = "failed"
                state["error"] = "sync interrupted"
                state["message"] = "同步中断，请再点一次"
            self._state = state


_PROGRESS = SyncProgress()


def sync_progress() -> SyncProgress:
    return _PROGRESS


def bind_sync_progress(settings: Settings, progress: SyncProgress | None = None) -> SyncProgress:
    tracker = progress or sync_progress()
    tracker.bind(Path(settings.runtime_root) / "config" / _PROGRESS_FILE)
    return tracker


class LanSource(Protocol):
    def results_index(self) -> dict[str, list[str]]: ...
    def result_tree(self, run_id: str) -> list[dict[str, Any]]: ...
    def read_result_file(self, run_id: str, rel: str) -> bytes: ...
    def read_deleted(self, run_id: str) -> bytes | None: ...
    def read_plan(self, plan_id: str) -> bytes | None: ...
    def data_tree(self) -> list[dict[str, Any]]: ...
    def read_data_file(self, rel: str) -> bytes: ...


class LocalLanSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _results(self) -> Path:
        return self.settings.runtime_root / "results"

    def results_index(self) -> dict[str, list[str]]:
        root = self._results()
        return {
            "runs": sorted(local_run_ids(root)),
            "deleted": sorted(local_deleted_ids(root)),
            "plans": sorted(local_plan_ids(root)),
        }

    def result_tree(self, run_id: str) -> list[dict[str, Any]]:
        return iter_rel_files(self._results() / validate_run_id(run_id))

    def read_result_file(self, run_id: str, rel: str) -> bytes:
        path = safe_under(self._results() / validate_run_id(run_id), rel)
        return path.read_bytes()

    def read_deleted(self, run_id: str) -> bytes | None:
        path = self._results() / DELETED_DIR / f"{validate_run_id(run_id)}.json"
        if not path.is_file():
            return None
        return path.read_bytes()

    def read_plan(self, plan_id: str) -> bytes | None:
        path = self._results() / PLANS_DIR_NAME / f"{validate_plan_snapshot_id(plan_id)}.json"
        if not path.is_file():
            return None
        return path.read_bytes()

    def data_tree(self) -> list[dict[str, Any]]:
        return iter_rel_files(self.settings.data_root)

    def read_data_file(self, rel: str) -> bytes:
        return safe_under(self.settings.data_root, rel).read_bytes()


class HttpLanSource:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base = str(base_url).rstrip("/")
        self.client = client or httpx.Client(timeout=HTTP_TIMEOUT)

    def results_index(self) -> dict[str, list[str]]:
        response = self.client.get(f"{self.base}/results/index")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"runs": [], "deleted": []}

    def result_tree(self, run_id: str) -> list[dict[str, Any]]:
        response = self.client.get(f"{self.base}/results/{validate_run_id(run_id)}/tree")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def read_result_file(self, run_id: str, rel: str) -> bytes:
        response = self.client.get(f"{self.base}/results/{validate_run_id(run_id)}/file", params={"rel": rel})
        response.raise_for_status()
        return response.content

    def read_deleted(self, run_id: str) -> bytes | None:
        response = self.client.get(f"{self.base}/deleted/{validate_run_id(run_id)}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def read_plan(self, plan_id: str) -> bytes | None:
        response = self.client.get(f"{self.base}/plans/{validate_plan_snapshot_id(plan_id)}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def data_tree(self) -> list[dict[str, Any]]:
        response = self.client.get(f"{self.base}/data/tree")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def read_data_file(self, rel: str) -> bytes:
        response = self.client.get(f"{self.base}/data/file", params={"rel": rel})
        response.raise_for_status()
        return response.content


def local_run_ids(results_root: Path) -> set[str]:
    root = Path(results_root)
    if not root.is_dir():
        return set()
    found: set[str] = set()
    for path in root.iterdir():
        if not path.is_dir() or path.name == DELETED_DIR:
            continue
        try:
            found.add(validate_run_id(path.name))
        except ValueError:
            continue
    return found


def local_deleted_ids(results_root: Path) -> set[str]:
    root = Path(results_root) / DELETED_DIR
    if not root.is_dir():
        return set()
    found: set[str] = set()
    for path in root.glob("*.json"):
        try:
            found.add(validate_run_id(path.stem))
        except ValueError:
            continue
    return found


def local_plan_ids(results_root: Path) -> set[str]:
    root = Path(results_root) / PLANS_DIR_NAME
    if not root.is_dir():
        return set()
    found: set[str] = set()
    for path in root.glob("*.json"):
        try:
            found.add(validate_plan_snapshot_id(path.stem))
        except ValueError:
            continue
    return found


def _manifest_status(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("status") or "").strip()


def local_result_status(folder: Path) -> str:
    path = Path(folder) / "run.json"
    if not path.is_file():
        return ""
    try:
        return _manifest_status(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return ""


def source_result_status(source: LanSource, run_id: str) -> str:
    try:
        raw = source.read_result_file(run_id, "run.json")
        return _manifest_status(json.loads(raw.decode("utf-8")))
    except Exception:
        return ""


def pull_results(
    settings: Settings, source: LanSource, progress: SyncProgress | None = None
) -> dict[str, list[str]]:
    results = settings.require_write_path(settings.runtime_root / "results")
    results.mkdir(parents=True, exist_ok=True)
    index = source.results_index()
    pulled_runs: list[str] = []
    pulled_deleted: list[str] = []
    have_deleted = local_deleted_ids(results)
    pending_runs: list[tuple[str, list[dict[str, Any]]]] = []
    for run_id in index.get("runs") or []:
        try:
            run_id = validate_run_id(str(run_id))
        except ValueError:
            continue
        if source_result_status(source, run_id) not in _FINISHED_STATUSES:
            continue
        folder = results / run_id
        if local_result_status(folder) in _FINISHED_STATUSES:
            continue
        pending_runs.append((run_id, source.result_tree(run_id)))
    pending_deleted: list[str] = []
    for run_id in index.get("deleted") or []:
        try:
            run_id = validate_run_id(str(run_id))
        except ValueError:
            continue
        if run_id in have_deleted:
            continue
        pending_deleted.append(run_id)
    units = sum(len(tree) for _, tree in pending_runs) + len(pending_deleted)
    units += sum(1 for _, tree in pending_runs if not tree)
    if progress is not None:
        progress.add_total(units)
    for run_id, tree in pending_runs:
        folder = results / run_id
        folder.mkdir(parents=True, exist_ok=True)
        if not tree:
            if progress is not None:
                progress.tick(detail=run_id)
            pulled_runs.append(run_id)
            continue
        for item in tree:
            rel = str(item.get("rel") or "")
            if not rel:
                if progress is not None:
                    progress.tick(detail=run_id)
                continue
            dest = safe_under(folder, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_result_file(run_id, rel))
            if progress is not None:
                progress.tick(detail=f"{run_id}/{rel}")
        pulled_runs.append(run_id)
    deleted_root = results / DELETED_DIR
    for run_id in pending_deleted:
        payload = source.read_deleted(run_id)
        if not payload:
            if progress is not None:
                progress.tick(detail=f"{DELETED_DIR}/{run_id}")
            continue
        deleted_root.mkdir(parents=True, exist_ok=True)
        (deleted_root / f"{run_id}.json").write_bytes(payload)
        pulled_deleted.append(run_id)
        have_deleted.add(run_id)
        if progress is not None:
            progress.tick(detail=f"{DELETED_DIR}/{run_id}")
    pulled_plans = _pull_plan_snapshots(results, source, index.get("plans") or [], progress=progress)
    return {"pulled_runs": pulled_runs, "pulled_deleted": pulled_deleted, "pulled_plans": pulled_plans}


def _pull_plan_snapshots(
    results: Path,
    source: LanSource,
    plan_ids: list[Any],
    progress: SyncProgress | None = None,
) -> list[str]:
    read_plan = getattr(source, "read_plan", None)
    if not callable(read_plan):
        return []
    wanted: list[str] = []
    for raw in plan_ids:
        try:
            wanted.append(validate_plan_snapshot_id(str(raw)))
        except ValueError:
            continue
    if progress is not None:
        progress.add_total(len(wanted))
    pulled: list[str] = []
    root = results / PLANS_DIR_NAME
    for plan_id in wanted:
        payload = read_plan(plan_id)
        if not payload:
            if progress is not None:
                progress.tick(detail=f"{PLANS_DIR_NAME}/{plan_id}")
            continue
        root.mkdir(parents=True, exist_ok=True)
        dest = safe_under(root, f"{plan_id}.json")
        dest.write_bytes(payload)
        pulled.append(plan_id)
        if progress is not None:
            progress.tick(detail=f"{PLANS_DIR_NAME}/{plan_id}")
    return pulled


def pull_market(
    settings: Settings, source: LanSource, progress: SyncProgress | None = None
) -> dict[str, int]:
    root = Path(settings.data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    copied = 0
    tree = source.data_tree()
    if progress is not None:
        progress.add_total(len(tree))
    for item in tree:
        rel = str(item.get("rel") or "")
        if not rel:
            if progress is not None:
                progress.tick()
            continue
        dest = safe_under(root, rel)
        incoming = source.read_data_file(rel)
        if dest.is_file() and dest.read_bytes() == incoming:
            if progress is not None:
                progress.tick(detail=rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(incoming)
        copied += 1
        if progress is not None:
            progress.tick(detail=rel)
    return {"copied": copied}


def sync_results(settings: Settings, database: Database, sources: list[LanSource], *, rounds: int = MAX_SYNC_ROUNDS) -> dict[str, Any]:
    pulled_runs: list[str] = []
    pulled_deleted: list[str] = []
    used = 0
    for round_no in range(1, max(1, int(rounds)) + 1):
        used = round_no
        changed = False
        for source in sources:
            stats = pull_results(settings, source)
            if stats["pulled_runs"] or stats["pulled_deleted"]:
                changed = True
                pulled_runs.extend(stats["pulled_runs"])
                pulled_deleted.extend(stats["pulled_deleted"])
        if not changed:
            break
    sync_result_catalog(settings, database)
    return {"rounds": used, "pulled_runs": pulled_runs, "pulled_deleted": pulled_deleted}


def _http_base(host: str, port: int) -> str:
    return f"http://{validate_lan_host(host)}:{validate_lan_port(port)}"


def ask_peer_pull_results(peer: dict[str, Any], sources: list[dict[str, Any]], *, client: httpx.Client | None = None) -> dict[str, Any]:
    owns = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        response = http.post(
            f"{_http_base(peer['host'], peer['port'])}/pull-results",
            json={"sources": sources},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    finally:
        if owns:
            http.close()


def ask_peer_pull_market(
    peer: dict[str, Any], source_host: str, source_port: int, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    owns = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        response = http.post(
            f"{_http_base(peer['host'], peer['port'])}/pull-market",
            json={"source_host": validate_lan_host(source_host), "source_port": validate_lan_port(source_port)},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    finally:
        if owns:
            http.close()


def _acquire_sync() -> None:
    if not _SYNC_LOCK.acquire(blocking=False):
        raise SyncBusyError("sync already running")


def coordinate_results_sync(
    settings: Settings,
    database: Database,
    peers: list[dict[str, Any]],
    *,
    self_host: str,
    self_port: int = LAN_PORT,
    rounds: int = MAX_SYNC_ROUNDS,
    source_for: Callable[[str, int], LanSource] | None = None,
    ask_peer: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] | None = None,
    progress: SyncProgress | None = None,
) -> dict[str, Any]:
    _acquire_sync()
    tracker = bind_sync_progress(settings, progress)
    try:
        tracker.start("results", message="正在同步回测产物…")
        remote = [peer for peer in peers if not peer.get("self")]
        make_source = source_for or (lambda host, port: HttpLanSource(_http_base(host, port)))
        ask = ask_peer or ask_peer_pull_results
        pulled_runs: list[str] = []
        pulled_deleted: list[str] = []
        errors: list[str] = []
        used = 0
        for round_no in range(1, max(1, int(rounds)) + 1):
            used = round_no
            changed = False
            tracker.update(message=f"正在同步回测产物…第 {round_no} 轮")
            for peer in remote:
                try:
                    stats = pull_results(settings, make_source(str(peer["host"]), int(peer["port"])), progress=tracker)
                except Exception as error:
                    errors.append(str(error))
                    continue
                if stats["pulled_runs"] or stats["pulled_deleted"]:
                    changed = True
                    pulled_runs.extend(stats["pulled_runs"])
                    pulled_deleted.extend(stats["pulled_deleted"])
            if remote:
                tracker.add_total(len(remote))
            for peer in remote:
                others = [{"host": self_host, "port": int(self_port)}] + [
                    {"host": other["host"], "port": int(other["port"])}
                    for other in remote
                    if other["machine_id"] != peer["machine_id"]
                ]
                if not others:
                    tracker.tick(detail=str(peer.get("hostname") or peer.get("machine_id") or ""))
                    continue
                try:
                    remote_stats = ask(peer, others)
                except Exception as error:
                    errors.append(str(error))
                    tracker.tick(detail=str(peer.get("hostname") or peer.get("machine_id") or ""))
                    continue
                tracker.tick(detail=str(peer.get("hostname") or peer.get("machine_id") or ""))
                if remote_stats.get("pulled_runs") or remote_stats.get("pulled_deleted"):
                    changed = True
            if not changed:
                break
        tracker.update(message="正在写入本机回测目录…")
        sync_result_catalog(settings, database)
        tracker.finish(message="回测产物已同步")
        return {
            "rounds": used,
            "pulled_runs": pulled_runs,
            "pulled_deleted": pulled_deleted,
            "errors": errors,
        }
    except Exception as error:
        tracker.finish(error=str(error), message="同步回测产物失败")
        raise
    finally:
        _SYNC_LOCK.release()


def coordinate_market_sync(
    settings: Settings,
    peers: list[dict[str, Any]],
    source_machine_id: str,
    *,
    self_id: str,
    source_for: Callable[[str, int], LanSource] | None = None,
    ask_peer: Callable[[dict[str, Any], str, int], dict[str, Any]] | None = None,
    progress: SyncProgress | None = None,
) -> dict[str, Any]:
    source_id = str(source_machine_id or "").strip()
    source = next((peer for peer in peers if peer.get("machine_id") == source_id), None)
    if source is None:
        raise ValueError("source machine is not online")
    _acquire_sync()
    tracker = bind_sync_progress(settings, progress)
    try:
        tracker.start("market", message="正在同步行情…", source_machine_id=source_id)
        make_source = source_for or (lambda host, port: HttpLanSource(_http_base(host, port)))
        ask = ask_peer or ask_peer_pull_market
        local = {"copied": 0}
        if source_id != str(self_id or ""):
            local = pull_market(settings, make_source(str(source["host"]), int(source["port"])), progress=tracker)
        remote: list[dict[str, Any]] = []
        targets = [peer for peer in peers if not peer.get("self") and peer.get("machine_id") != source_id]
        if targets:
            tracker.add_total(len(targets))
            tracker.update(message="正在通知其他机器拉取行情…")
        for peer in targets:
            try:
                stats = ask(peer, str(source["host"]), int(source["port"]))
                remote.append({"machine_id": peer["machine_id"], "ok": True, **stats})
            except Exception as error:
                remote.append({"machine_id": peer["machine_id"], "ok": False, "error": str(error)})
            tracker.tick(detail=str(peer.get("hostname") or peer.get("machine_id") or ""))
        tracker.finish(message="行情已同步")
        return {"source_machine_id": source_id, "local": local, "peers": remote}
    except Exception as error:
        tracker.finish(error=str(error), message="同步行情失败")
        raise
    finally:
        _SYNC_LOCK.release()
