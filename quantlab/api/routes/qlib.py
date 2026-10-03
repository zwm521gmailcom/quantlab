from fastapi import APIRouter, HTTPException, Request

from quantlab.api.errors import _json_body

router = APIRouter(tags=["qlib"])


def _invalid(error: ValueError, code: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error_code": code, "message": str(error), "entity_id": None, "details": {}},
    )


@router.get("/api/qlib/status")
def qlib_status(request: Request) -> dict[str, object]:
    return request.app.state.qlib_factor_loop_service.status()


@router.get("/api/qlib/picks")
def qlib_picks(request: Request) -> dict[str, object]:
    return request.app.state.qlib_factor_loop_service.picks()


@router.post("/api/qlib/convert", status_code=202)
def qlib_convert(request: Request) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.start_convert()
    except ValueError as error:
        raise _invalid(error, "QLIB_CONVERT_INVALID") from error


@router.post("/api/qlib/plans", status_code=201)
async def qlib_save_plan(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.qlib_factor_loop_service.save_plan(body)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.get("/api/qlib/plans/{plan_id}")
def qlib_plan(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.get(plan_id)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.post("/api/qlib/plans/{plan_id}/start", status_code=202)
def qlib_start(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.start(plan_id)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.post("/api/qlib/plans/{plan_id}/stop")
def qlib_stop(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.stop(plan_id)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.post("/api/qlib/plans/{plan_id}/rounds/{round_index}/own-archive")
def qlib_own_archive(request: Request, plan_id: str, round_index: int) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.publish_own_archive(plan_id, round_index)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.post("/api/qlib/plans/{plan_id}/purge-errors")
def qlib_purge_errors(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.purge_errors(plan_id)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error


@router.post("/api/qlib/plans/{plan_id}/delete")
def qlib_delete_plan(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.qlib_factor_loop_service.delete_plan(plan_id)
    except ValueError as error:
        raise _invalid(error, "QLIB_PLAN_INVALID") from error
