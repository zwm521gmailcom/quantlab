"""LAN-only QuantLab file service. No UI, no SQLite port, no secrets."""
from __future__ import annotations

import socket

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from quantlab.config import Settings
from quantlab.domain.identifiers import validate_run_id
from quantlab.repositories.database import Database
from quantlab.services.lan_files import iter_rel_files, safe_under
from quantlab.services.lan_peers import local_lan_ip, validate_lan_host, validate_lan_port
from quantlab.services.lan_sync import (
    DELETED_DIR,
    HTTP_TIMEOUT,
    HttpLanSource,
    local_deleted_ids,
    local_plan_ids,
    local_result_status,
    local_run_ids,
    pull_market,
    pull_results,
)
from quantlab.services.machine_identity import load_machine_identity
from quantlab.services.result_sync import PLANS_DIR_NAME, export_plan_snapshots, sync_result_catalog, validate_plan_snapshot_id


def _hello(settings: Settings) -> dict[str, object]:
    machine = load_machine_identity(settings.runtime_root)
    return {
        "service": "quantlab-lan",
        "machine_id": machine.get("machine_id") or "",
        "serial_prefix": int(machine.get("serial_prefix") or 10),
        "hostname": socket.gethostname(),
        "host": local_lan_ip(),
        "sync_port": int(settings.lan_port),
        "ui_port": int(settings.port),
        "asset": str(settings.asset),
        "lan_port": int(settings.lan_port),
    }


def create_lan_app(settings: Settings, database: Database | None = None) -> FastAPI:
    app = FastAPI(title="QuantLab LAN sync", version="0.1.0")
    app.state.settings = settings
    app.state.database = database

    @app.get("/hello")
    def hello() -> dict[str, object]:
        return _hello(settings)

    @app.get("/results/index")
    def results_index() -> dict[str, object]:
        if database is not None:
            export_plan_snapshots(settings, database)
        root = settings.runtime_root / "results"
        runs = sorted(local_run_ids(root))
        return {
            "runs": runs,
            "deleted": sorted(local_deleted_ids(root)),
            "plans": sorted(local_plan_ids(root)),
            "run_status": {run_id: local_result_status(root / run_id) for run_id in runs},
        }

    @app.get("/results/{run_id}/tree")
    def result_tree(run_id: str) -> list[dict[str, object]]:
        try:
            run_id = validate_run_id(run_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return iter_rel_files(settings.runtime_root / "results" / run_id)

    @app.get("/results/{run_id}/file")
    def result_file(run_id: str, rel: str = Query(...)) -> Response:
        try:
            run_id = validate_run_id(run_id)
            path = safe_under(settings.runtime_root / "results" / run_id, rel)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    @app.get("/deleted/{run_id}")
    def deleted_marker(run_id: str) -> Response:
        try:
            run_id = validate_run_id(run_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        path = settings.runtime_root / "results" / DELETED_DIR / f"{run_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=path.read_bytes(), media_type="application/json")

    @app.get("/plans/{plan_id}")
    def plan_snapshot(plan_id: str) -> Response:
        try:
            plan_id = validate_plan_snapshot_id(plan_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        path = settings.runtime_root / "results" / PLANS_DIR_NAME / f"{plan_id}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=path.read_bytes(), media_type="application/json")

    @app.get("/data/tree")
    def data_tree() -> list[dict[str, object]]:
        return iter_rel_files(settings.data_root)

    @app.get("/data/file")
    def data_file(rel: str = Query(...)) -> Response:
        try:
            path = safe_under(settings.data_root, rel)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    @app.post("/pull-market")
    async def pull_market_from(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        body = payload if isinstance(payload, dict) else {}
        try:
            host = validate_lan_host(body.get("source_host"))
            port = validate_lan_port(body.get("source_port"))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            stats = pull_market(settings, HttpLanSource(f"http://{host}:{port}", client=client))
        return {"ok": True, **stats}

    @app.post("/pull-results")
    async def pull_results_from(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        body = payload if isinstance(payload, dict) else {}
        raw_sources = body.get("sources") or []
        if not isinstance(raw_sources, list):
            raise HTTPException(status_code=400, detail="sources must be a list")
        pulled_runs: list[str] = []
        pulled_deleted: list[str] = []
        pulled_plans: list[str] = []
        errors: list[str] = []
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                try:
                    host = validate_lan_host(item.get("host"))
                    port = validate_lan_port(item.get("port"))
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                try:
                    stats = pull_results(settings, HttpLanSource(f"http://{host}:{port}", client=client))
                except Exception as error:
                    errors.append(str(error))
                    continue
                pulled_runs.extend(stats["pulled_runs"])
                pulled_deleted.extend(stats["pulled_deleted"])
                pulled_plans.extend(stats.get("pulled_plans") or [])
        if database is not None:
            sync_result_catalog(settings, database)
        return {
            "ok": not errors,
            "pulled_runs": pulled_runs,
            "pulled_deleted": pulled_deleted,
            "pulled_plans": pulled_plans,
            "errors": errors,
        }

    return app
