from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response

from quantlab.api.errors import _error_payload, _json_body, _raise_factor_library_error

router = APIRouter(tags=["factors"])


@router.get("/api/factors")
def factor_library_list(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    asset_class: str | None = None,
    source: str | None = None,
    lifecycle: str | None = None,
    quality: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return request.app.state.factor_repository.list(
            query=q, category=category, asset_class=asset_class, source=source, lifecycle=lifecycle,
            quality=quality, page=page, page_size=page_size,
        )
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factors/{entity_id}/{version_id}")
def factor_library_detail(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        item = request.app.state.factor_repository.get(entity_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error)
    if item is None:
        _raise_factor_library_error(ValueError("FactorVersion not found"), status_code=404)
    return item


@router.get("/api/factors/{factor_id}/versions/{version_id}")
def factor_version_detail(request: Request, factor_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_detail_service.get(factor_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.post("/api/factor-drafts/preview")
async def factor_draft_preview(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_manual_service.preview(body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factor-drafts/fields")
def factor_draft_fields(request: Request, dataset_id: str, dataset_version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_manual_service.fields(dataset_id, dataset_version_id)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factor-drafts", status_code=201)
async def factor_draft_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_manual_service.create_draft(body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factor-drafts/{entity_id}/{version_id}/diagnose")
def factor_draft_diagnose(
    request: Request,
    entity_id: str,
    version_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, object]:
    try:
        return request.app.state.factor_manual_service.diagnose(
            entity_id, version_id, date_from=date_from, date_to=date_to
        )
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.patch("/api/factor-drafts/{entity_id}/{version_id}")
async def factor_draft_update(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_manual_service.update_draft(entity_id, version_id, body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.post("/api/factor-drafts/{entity_id}/{version_id}/publish")
def factor_draft_publish(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_manual_service.publish(entity_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.patch("/api/factors/{factor_id}")
def factor_rename(request: Request, factor_id: str, body: dict[str, object] = Body(...)) -> dict[str, object]:
    name = body.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail=_error_payload("FACTOR_LIBRARY_INVALID", "请填写新名称。"))
    try:
        return request.app.state.factor_repository.rename(factor_id, name.strip())
    except ValueError as error:
        message = str(error)
        status_code = 404 if "not found" in message.lower() or "找不到" in message else 400
        _raise_factor_library_error(error, status_code=status_code)
    raise AssertionError("unreachable")


@router.post("/api/factors/{factor_id}/versions/{version_id}/copy", status_code=201)
def factor_version_copy(request: Request, factor_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_detail_service.copy_version(factor_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.post("/api/factors/{factor_id}/versions/{version_id}/diagnose")
def factor_version_diagnose(request: Request, factor_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_detail_service.diagnose(factor_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.post("/api/factors", status_code=201)
async def factor_library_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_repository.import_definition(body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/import", status_code=201)
async def factor_library_import(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_repository.import_definition(body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.patch("/api/factors/{entity_id}/{version_id}")
async def factor_library_update(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_repository.update_draft(entity_id, version_id, body)  # type: ignore[arg-type]
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/diagnose")
async def factor_library_diagnose(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_repository.diagnose(body.get("items") if isinstance(body, dict) else body)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/quality-diagnose")
async def factor_library_quality_diagnose(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_repository.diagnose(body.get("items") if isinstance(body, dict) else body)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/publish")
async def factor_library_publish_many(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        items = body.get("items") if isinstance(body, dict) else body
        return request.app.state.factor_repository.publish_many(items)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/deprecate")
async def factor_library_deprecate_many(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        items = body.get("items") if isinstance(body, dict) else body
        return request.app.state.factor_repository.deprecate_many(items)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/{entity_id}/{version_id}/publish")
def factor_library_publish(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_repository.publish(entity_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factors/{entity_id}/{version_id}/deprecate")
def factor_library_deprecate(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_repository.deprecate(entity_id, version_id)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.delete("/api/factors/{entity_id}/{version_id}", status_code=204)
def factor_library_delete(request: Request, entity_id: str, version_id: str) -> Response:
    try:
        request.app.state.factor_repository.delete(entity_id, version_id)
    except ValueError as error:
        message = str(error)
        if "not found" in message.lower() or "找不到" in message:
            status_code = 404
        elif "不能删" in message:
            status_code = 409
        else:
            status_code = 400
        _raise_factor_library_error(error, status_code=status_code)
    return Response(status_code=204)
