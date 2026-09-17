from fastapi import APIRouter, HTTPException, Request

from quantlab.api.errors import _error_payload, _json_body

router = APIRouter(tags=["settings"])


@router.get("/api/settings/raw-root")
def raw_root_get(request: Request) -> dict[str, object]:
    return {"raw_root": request.app.state.settings_service.raw_path()}


@router.post("/api/settings/raw-root")
async def raw_root_update(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    if not isinstance(body, dict) or not body.get("path"):
        raise HTTPException(status_code=400, detail=_error_payload("RAW_ROOT_INVALID", "path is required"))
    try:
        return request.app.state.settings_service.update_raw_root(str(body["path"]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("RAW_ROOT_INVALID", str(error))) from error


@router.get("/api/settings/tushare-token")
def tushare_token_status(request: Request) -> dict[str, object]:
    return {"configured": request.app.state.settings_service.token_configured()}


@router.post("/api/settings/tushare-token")
async def tushare_token_update(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_TOKEN_INVALID", "token is required"))
    token = str(body.get("token") or "").strip()
    return request.app.state.settings_service.update_tushare_token(token)


@router.get("/api/settings/tushare-points")
def tushare_points_status(request: Request) -> dict[str, object]:
    return request.app.state.settings_service.tushare_points_public()


@router.post("/api/settings/tushare-points")
async def tushare_points_update(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_POINTS_INVALID", "points is required"))
    raw = body.get("points", None)
    try:
        if raw is None or str(raw).strip() == "":
            result = request.app.state.settings_service.update_tushare_points(None)
        else:
            result = request.app.state.settings_service.update_tushare_points(int(raw))
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail=_error_payload("TUSHARE_POINTS_INVALID", str(error)),
        ) from error
    download = getattr(request.app.state, "tushare_download_service", None)
    if download is not None and hasattr(download, "apply_saved_quota"):
        download.apply_saved_quota()
    return result


@router.get("/api/settings")
def get_settings(request: Request) -> dict[str, object]:
    return request.app.state.settings_service.public()


@router.put("/api/settings")
async def update_settings(request: Request) -> dict[str, object]:
    try:
        body = await request.json()
        return request.app.state.settings_service.update(body if isinstance(body, dict) else {})
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "SETTINGS_INVALID", "message": str(error), "details": {}}) from error


@router.post("/api/settings/reset")
def reset_settings(request: Request) -> dict[str, object]:
    return request.app.state.settings_service.reset()


@router.post("/api/settings/test-connection")
def test_settings_connection(request: Request) -> dict[str, object]:
    value = request.app.state.settings_service.public()
    return {"ok": True, "host": value["environment"]["host"], "roots": value["paths"]}


@router.post("/api/settings/scan")
def scan_settings(request: Request) -> dict[str, object]:
    return request.app.state.settings_service.scan()
