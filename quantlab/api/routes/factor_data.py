from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from quantlab.api.errors import _error_payload, _json_body, _raise_factor_error, _raise_factor_library_error
from quantlab.services.factor_data import MAX_ROWS as FACTOR_MAX_ROWS

router = APIRouter(tags=["factor-data"])


@router.get("/api/factor-data/catalog")
def factor_catalog(request: Request) -> list[dict[str, object]]:
    try:
        items = request.app.state.factor_data_service.catalog()
        latest_by_id = request.app.state.factor_calculation_service.catalog_latest()
        for item in items:
            entity = str(item.get("factor_entity_id") or "")
            factor_id = str(item.get("factor_id") or "")
            item["latest_calculation"] = (
                latest_by_id.get(entity)
                or latest_by_id.get(factor_id)
                or latest_by_id.get(f"factor_{factor_id}")
            )
        return items
    except ValueError as error:
        _raise_factor_error(error)
    raise AssertionError("unreachable")


@router.post("/api/factor-calculations", status_code=201)
async def factor_calculation_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail=_error_payload("FACTOR_CALCULATION_INVALID", "request body is required"))
    try:
        return request.app.state.factor_calculation_service.run(
            str(body.get("factor_id", "")),
            version_id=str(body.get("version_id") or "v1"),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
            markets=body.get("markets"),
        )
    except ValueError as error:
        status_code = 404 if any(token in str(error) for token in ("not found", "unknown factor", "找不到")) else 400
        raise HTTPException(status_code=status_code, detail=_error_payload("FACTOR_CALCULATION_INVALID", str(error))) from error


@router.get("/api/factor-calculations")
def factor_calculation_list(
    request: Request,
    factor_id: str | None = None,
    version_id: str | None = None,
) -> dict[str, object]:
    return request.app.state.factor_calculation_service.list(factor_id=factor_id, version_id=version_id)


@router.get("/api/factor-calculations/latest/{factor_id}")
def factor_calculation_latest(request: Request, factor_id: str) -> dict[str, object]:
    item = request.app.state.factor_calculation_service.latest(factor_id=factor_id)
    if item is None:
        raise HTTPException(status_code=404, detail=_error_payload("FACTOR_CALCULATION_NOT_FOUND", "factor calculation not found", entity_id=factor_id))
    return item


@router.get("/api/factor-calculations/{calculation_id}")
def factor_calculation_detail(request: Request, calculation_id: int) -> dict[str, object]:
    item = request.app.state.factor_calculation_service.get(calculation_id)
    if item is None:
        raise HTTPException(status_code=404, detail=_error_payload("FACTOR_CALCULATION_NOT_FOUND", "factor calculation not found"))
    return item


@router.get("/api/factor-data/factors/{factor_id}")
def factor_detail(request: Request, factor_id: str) -> dict[str, object]:
    try:
        items = request.app.state.factor_data_service.catalog()
        item = next((entry for entry in items if entry["factor_id"] == factor_id), None)
        if item is None:
            raise ValueError(f"unknown factor: {factor_id}")
        item["latest_calculation"] = request.app.state.factor_calculation_service.latest(factor_id=factor_id)
        return item
    except ValueError as error:
        _raise_factor_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factor-data/query")
def factor_query(
    request: Request,
    factor: str,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query("v1"),
    fields: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    max_rows: int = Query(5000, ge=1, le=FACTOR_MAX_ROWS),
) -> dict[str, object]:
    try:
        return request.app.state.factor_data_service.query(
            factor=factor,
            symbols=ts_code,
            date_from=date_from,
            date_to=date_to,
            version_id=version_id,
            fields=fields,
            page=page,
            page_size=page_size,
            max_rows=max_rows,
        )
    except ValueError as error:
        _raise_factor_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factor-data/summary")
def factor_summary(
    request: Request,
    factor: str,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query("v1"),
) -> dict[str, object]:
    try:
        return request.app.state.factor_data_service.summary(
            factor=factor,
            symbols=ts_code,
            date_from=date_from,
            date_to=date_to,
            version_id=version_id,
        )
    except ValueError as error:
        _raise_factor_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factor-data/quality")
def factor_quality(request: Request, version_id: str = Query("v1")) -> dict[str, object]:
    try:
        return request.app.state.factor_data_service.quality(version_id=version_id)
    except ValueError as error:
        _raise_factor_error(error)
    raise AssertionError("unreachable")


@router.get("/api/factor-data/export.csv")
def factor_export(
    request: Request,
    factor: str,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query("v1"),
    max_rows: int = Query(5000, ge=1, le=FACTOR_MAX_ROWS),
) -> Response:
    try:
        content = request.app.state.factor_data_service.csv_text(
            factor=factor,
            symbols=ts_code,
            date_from=date_from,
            date_to=date_to,
            version_id=version_id,
            max_rows=max_rows,
        )
    except ValueError as error:
        _raise_factor_error(error)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={factor}-sample.csv"},
    )


@router.get("/api/factor-packs/canonical")
def factor_pack_canonical(request: Request) -> dict[str, object]:
    return request.app.state.factor_pack_service.catalog()


@router.post("/api/factor-packs/canonical/ingest")
async def factor_pack_ingest(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    if not isinstance(body, dict):
        body = {}
    try:
        field = body.get("field")
        return request.app.state.factor_pack_service.ingest(
            dataset_id=str(body.get("dataset_id") or "ds_canonical_market"),
            dataset_version_id=str(body.get("dataset_version_id") or "current"),
            field=None if field in (None, "") else str(field),
        )
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")
