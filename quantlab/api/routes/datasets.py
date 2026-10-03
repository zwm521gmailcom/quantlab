from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from quantlab.api.errors import _error_payload
from quantlab.services.canonical_market import apply_suspend_d

router = APIRouter(tags=["datasets"])


@router.get("/api/datasets")
def datasets(
    request: Request,
    category: str | None = None,
    status: str | None = None,
    quality_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, object]:
    return request.app.state.dataset_catalog.filtered_items(
        category=category, status=status, quality_status=quality_status,
        date_from=date_from, date_to=date_to, query=q, page=page, page_size=page_size,
    )


@router.post("/api/raw/download/index_basic")
def raw_download_index_basic(request: Request) -> dict[str, object]:
    try:
        return request.app.state.tushare_download_service.download_index_basic()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error


@router.post("/api/raw/download/index_weight")
def raw_download_index_weight(request: Request, body: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        index_code = str(body.get("index_code") or "").strip()
        start_date = str(body.get("start_date") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        if not index_code or not start_date or not end_date:
            raise ValueError("index_code、start_date、end_date 均为必填")
        return request.app.state.tushare_download_service.download_index_weight(index_code, start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error


@router.post("/api/raw/download/stk_week_month_adj")
def raw_download_stk_week_month_adj(request: Request, body: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        start_date = str(body.get("start_date") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        freq = str(body.get("freq") or "").strip() or None
        if not start_date or not end_date:
            raise ValueError("start_date、end_date 均为必填")
        return request.app.state.tushare_download_service.download_stk_week_month_adj(start_date, end_date, freq)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error


@router.post("/api/raw/download/moneyflow")
def raw_download_moneyflow(request: Request, body: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        start_date = str(body.get("start_date") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        if not start_date or not end_date:
            raise ValueError("start_date、end_date 均为必填")
        return request.app.state.tushare_download_service.download_moneyflow(start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error


@router.post("/api/raw/download/suspend_d")
def raw_download_suspend_d(request: Request, body: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        start_date = str(body.get("start_date") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        if not start_date or not end_date:
            raise ValueError("start_date、end_date 均为必填")
        return request.app.state.tushare_download_service.download_suspend_d(start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error


@router.post("/api/raw/apply/suspend_d")
def raw_apply_suspend_d(request: Request) -> dict[str, object]:
    try:
        with request.app.state.database.connect() as connection:
            row = connection.execute(
                "SELECT path FROM dataset_versions "
                "WHERE entity_id='ds_canonical_market' AND version_id='current'"
            ).fetchone()
        canonical = (
            request.app.state.settings.resolve_user_path(row["path"])
            if row and row["path"]
            else request.app.state.settings.data_root / "canonical.parquet"
        )
        suspend = request.app.state.settings.raw_root / "suspend_d" / "suspend_d.parquet"
        return apply_suspend_d(canonical_path=canonical, suspend_path=suspend)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=_error_payload("CANONICAL_APPLY_FAILED", str(error)),
        ) from error


@router.get("/api/raw/{interface_name}/files")
def raw_interface_files(
    request: Request,
    interface_name: str,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, object]:
    try:
        return request.app.state.dataset_catalog.raw_interface_files(interface_name=interface_name, limit=limit)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=_error_payload("RAW_INTERFACE_NOT_FOUND", str(error))) from error


@router.get("/api/datasets/raw")
def raw_datasets(
    request: Request,
    category: str | None = None,
    asset_class: str | None = None,
    status: str | None = None,
    quality_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, object]:
    raw = request.app.state.dataset_catalog.raw_items()
    items = []
    query = (q or "").strip().lower()
    wanted_asset = (asset_class or "").strip()
    for item in raw:
        if category and item.get("category") != category:
            continue
        if wanted_asset and item.get("asset_class") != wanted_asset:
            continue
        if quality_status and item.get("quality_status") != quality_status:
            continue
        if date_from and item.get("date_max") and item["date_max"] < date_from:
            continue
        if date_to and item.get("date_min") and item["date_min"] > date_to:
            continue
        if query and query not in str(item.get("name", "")).lower() and query not in str(item.get("entity_id", "")).lower():
            continue
        items.append(item)
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": total, "page": page, "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size)}


@router.post("/api/datasets/raw/{interface}/backfill")
def raw_backfill(request: Request, interface: str) -> dict[str, object]:
    try:
        return request.app.state.tushare_download_service.submit_backfill(interface)
    except ValueError as error:
        message = str(error)
        busy = message == "已有补数在进行"
        raise HTTPException(
            status_code=409 if busy else 400,
            detail=_error_payload("RAW_BACKFILL_BUSY" if busy else "RAW_BACKFILL_INVALID", message),
        ) from error


@router.get("/api/datasets/raw/backfill/active")
def raw_backfill_active(request: Request) -> dict[str, object]:
    return request.app.state.tushare_download_service.active_backfill()


@router.get("/api/datasets/raw/backfill/{job_id}")
def raw_backfill_job(request: Request, job_id: str) -> dict[str, object]:
    try:
        return request.app.state.tushare_download_service.backfill_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=_error_payload("RAW_BACKFILL_NOT_FOUND", str(error))) from error


@router.post("/api/datasets/raw/backfill/{job_id}/stop")
def raw_backfill_stop(request: Request, job_id: str) -> dict[str, object]:
    try:
        return request.app.state.tushare_download_service.stop_backfill(job_id)
    except ValueError as error:
        message = str(error)
        missing = "不存在" in message
        raise HTTPException(
            status_code=404 if missing else 409,
            detail=_error_payload("RAW_BACKFILL_NOT_FOUND" if missing else "RAW_BACKFILL_IDLE", message),
        ) from error


@router.get("/api/datasets/quality-alerts")
def dataset_quality_alerts(request: Request) -> list[dict[str, object]]:
    return request.app.state.dataset_catalog.quality_alerts()


@router.get("/api/datasets/quality-summary")
def dataset_quality_summary(request: Request) -> dict[str, object]:
    return request.app.state.dataset_catalog.quality_summary()


@router.post("/api/datasets/rescan")
def rescan_datasets(request: Request) -> dict[str, object]:
    return request.app.state.dataset_catalog.rescan()


@router.get("/api/datasets/scan-audits")
def dataset_scan_audits(request: Request) -> list[dict[str, object]]:
    return request.app.state.dataset_catalog.scan_audits()


@router.get("/api/datasets/{entity_id}")
def dataset(request: Request, entity_id: str) -> dict[str, object]:
    item = request.app.state.dataset_catalog.get_public(entity_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "DATASET_NOT_FOUND", "message": "dataset not found", "entity_id": entity_id, "details": {}},
        )
    return item


@router.get("/api/datasets/{entity_id}/versions")
def dataset_versions(request: Request, entity_id: str) -> list[dict[str, object]]:
    versions = request.app.state.dataset_catalog.versions(entity_id)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "DATASET_NOT_FOUND", "message": "dataset not found", "entity_id": entity_id, "details": {}},
        )
    return versions


@router.get("/api/datasets/{entity_id}/versions/{version_id}")
def dataset_version(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    item = request.app.state.dataset_catalog.get_public(entity_id, version_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "DATASET_VERSION_NOT_FOUND", "message": "dataset version not found", "entity_id": entity_id, "details": {"version_id": version_id}},
        )
    return item


@router.get("/api/artifacts/{artifact_id}/download")
def artifact_download(request: Request, artifact_id: str) -> FileResponse:
    path = request.app.state.artifact_repository.get_download_path(artifact_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "ARTIFACT_NOT_FOUND", "message": "artifact not found", "entity_id": artifact_id, "details": {}},
        )
    return FileResponse(path, filename=path.name)


@router.get("/api/artifacts/{artifact_id}")
def artifact(request: Request, artifact_id: str) -> dict[str, object]:
    item = request.app.state.artifact_repository.get(artifact_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "ARTIFACT_NOT_FOUND", "message": "artifact not found", "entity_id": artifact_id, "details": {}},
        )
    return item
