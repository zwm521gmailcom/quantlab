from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from quantlab.api.errors import _raise_kline_error
from quantlab.services.kline import DEFAULT_KLINE_VERSION

router = APIRouter(tags=["kline"])


def _kline_params(
    *,
    ts_code: str | None,
    date_from: str | None,
    date_to: str | None,
    version_id: str,
    mode: Literal["raw", "hfq"],
    fields: str | None,
) -> dict[str, object]:
    return {
        "symbols": ts_code,
        "date_from": date_from,
        "date_to": date_to,
        "version_id": version_id,
        "mode": mode,
        "fields": fields,
    }


@router.get("/api/kline/query")
def kline_query(
    request: Request,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query(DEFAULT_KLINE_VERSION),
    mode: Literal["raw", "hfq"] = "raw",
    fields: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    max_rows: int = Query(5000, ge=1, le=5000),
    downsample: int | None = Query(None, ge=2, le=200),
    tail: bool = Query(False),
) -> dict[str, object]:
    try:
        return request.app.state.kline_service.query(
            **_kline_params(
                ts_code=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
                mode=mode,
                fields=fields,
            ),
            page=page,
            page_size=page_size,
            max_rows=max_rows,
            downsample=downsample,
            tail=tail,
        )
    except ValueError as error:
        _raise_kline_error(error)
    raise AssertionError("unreachable")


@router.get("/api/kline/summary")
def kline_summary(
    request: Request,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query(DEFAULT_KLINE_VERSION),
) -> dict[str, object]:
    try:
        return request.app.state.kline_service.summary(
            symbols=ts_code,
            date_from=date_from,
            date_to=date_to,
            version_id=version_id,
        )
    except ValueError as error:
        _raise_kline_error(error)
    raise AssertionError("unreachable")


@router.get("/api/kline/quality")
def kline_quality(
    request: Request,
    version_id: str = Query(DEFAULT_KLINE_VERSION),
) -> dict[str, object]:
    try:
        return request.app.state.kline_service.quality(version_id=version_id)
    except ValueError as error:
        _raise_kline_error(error)
    raise AssertionError("unreachable")


@router.get("/api/kline/export.csv")
def kline_export(
    request: Request,
    ts_code: str | None = Query(None, alias="ts_code"),
    date_from: str | None = None,
    date_to: str | None = None,
    version_id: str = Query(DEFAULT_KLINE_VERSION),
    mode: Literal["raw", "hfq"] = "raw",
    fields: str | None = None,
    max_rows: int = Query(5000, ge=1, le=5000),
) -> Response:
    try:
        csv_text = request.app.state.kline_service.csv_text(
            **_kline_params(
                ts_code=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
                mode=mode,
                fields=fields,
            ),
            max_rows=max_rows,
        )
    except ValueError as error:
        _raise_kline_error(error)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kline-export.csv"},
    )
