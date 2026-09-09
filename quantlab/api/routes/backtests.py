from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from quantlab.api.errors import _error_payload, _json_body, _plan_error, _raise_factor_library_error
from quantlab.services.backtest_control import run_isolated, stop_run

router = APIRouter(tags=["backtests"])


@router.post("/api/backtest-drafts")
async def backtest_draft_create(request: Request, response: Response) -> dict[str, object]:
    body = await _json_body(request)
    try:
        if isinstance(body, dict):
            items = body.get("factor_version_ids")
            draft_id = body.get("draft_id")
            if draft_id is not None:
                return request.app.state.factor_detail_service.update_backtest_draft(
                    str(draft_id), items, expected_revision=body.get("revision")
                )
        else:
            items = body
        response.status_code = 201
        return request.app.state.factor_detail_service.create_backtest_draft(items)
    except ValueError as error:
        _raise_factor_library_error(error)
    raise AssertionError("unreachable")


@router.get("/api/backtest-drafts/{draft_id}")
def backtest_draft_detail(request: Request, draft_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_detail_service.get_backtest_draft(draft_id)
    except ValueError as error:
        _raise_factor_library_error(error, status_code=404)


@router.get("/api/backtest-plans")
def backtest_plan_list(request: Request, open_only: bool = Query(False, alias="open")) -> dict[str, object]:
    return request.app.state.backtest_plan_service.list_plans(open_only=open_only)


@router.post("/api/backtest-plans", status_code=201)
async def backtest_plan_create(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise ValueError("plan body is required")
        return request.app.state.backtest_plan_service.create(body)
    except (RuntimeError, ValueError) as error:
        _plan_error(error)
    raise AssertionError("unreachable")


@router.get("/api/backtest-plans/{plan_id}")
def backtest_plan_detail(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.backtest_plan_service.get(plan_id)
    except ValueError as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtest-plans/{plan_id}/items", status_code=201)
async def backtest_plan_add_item(plan_id: str, request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise ValueError("item body is required")
        return request.app.state.backtest_plan_service.add_item(plan_id, body)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.patch("/api/backtest-plans/{plan_id}/items")
async def backtest_plan_select_items(plan_id: str, request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        if not isinstance(body, dict) or not isinstance(body.get("selected"), dict):
            raise ValueError("selected is required")
        return request.app.state.backtest_plan_service.set_selected(plan_id, body["selected"])
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtest-plans/{plan_id}/items/delete")
async def backtest_plan_delete_items(plan_id: str, request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
    except Exception:
        body = {}
    try:
        item_ids = body.get("item_ids") if isinstance(body, dict) else None
        if item_ids is not None and not isinstance(item_ids, list):
            raise ValueError("item_ids must be a list")
        return request.app.state.backtest_plan_service.delete_items(plan_id, item_ids=item_ids)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.delete("/api/backtest-plans/{plan_id}/items/{item_id}")
def backtest_plan_delete_item(request: Request, plan_id: str, item_id: str) -> dict[str, object]:
    try:
        return request.app.state.backtest_plan_service.delete_item(plan_id, item_id)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtest-plans/{plan_id}/start", status_code=202)
async def backtest_plan_start(plan_id: str, request: Request) -> dict[str, object]:
    body: object = {}
    try:
        body = await _json_body(request)
    except Exception:
        body = {}
    try:
        item_ids = body.get("item_ids") if isinstance(body, dict) else None
        if item_ids is not None and not isinstance(item_ids, list):
            raise ValueError("item_ids must be a list")
        return request.app.state.backtest_plan_service.start(plan_id, item_ids=item_ids)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtest-plans/{plan_id}/stop")
def backtest_plan_stop(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.backtest_plan_service.stop(plan_id)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtest-plans/{plan_id}/close")
def backtest_plan_close(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.backtest_plan_service.close(plan_id)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.delete("/api/backtest-plans/{plan_id}")
def backtest_plan_delete(request: Request, plan_id: str) -> dict[str, object]:
    try:
        return request.app.state.backtest_plan_service.delete(plan_id)
    except (RuntimeError, ValueError) as error:
        _plan_error(error, plan_id)
    raise AssertionError("unreachable")


@router.post("/api/backtests/validate")
async def backtest_validate(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        return request.app.state.backtest_workbench_service.validate(body)  # type: ignore[arg-type]
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_CONFIG_INVALID", str(error))) from error


@router.post("/api/backtests/preview")
async def backtest_preview(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        return request.app.state.backtest_workbench_service.preview(body)  # type: ignore[arg-type]
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_CONFIG_INVALID", str(error))) from error


@router.post("/api/backtests", status_code=201)
async def backtest_create(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        return request.app.state.backtest_workbench_service.submit(body)  # type: ignore[arg-type]
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_SUBMIT_INVALID", str(error))) from error


@router.post("/api/backtests/drafts", status_code=201)
async def backtest_draft_save(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
            raise ValueError("config is required")
        return request.app.state.backtest_workbench_service.save_draft(
            body["config"],  # type: ignore[arg-type]
            draft_id=body.get("draft_id"),
            expected_revision=body.get("expected_revision"),
        )
    except ValueError as error:
        status = 404 if "draft not found" in str(error) else 400
        raise HTTPException(status_code=status, detail=_error_payload("BACKTEST_DRAFT_INVALID", str(error))) from error


@router.get("/api/backtests/drafts/{draft_id}")
def backtest_workbench_draft_detail(request: Request, draft_id: str) -> dict[str, object]:
    draft = request.app.state.backtest_workbench_service.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_DRAFT_NOT_FOUND", "backtest draft not found", entity_id=draft_id))
    return draft


@router.get("/api/backtests/runs")
def backtest_archive_list(
    request: Request,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
    status: str | None = None, strategy: str | None = None, q: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    sort: str = "created_at", order: Literal["asc", "desc"] = "desc",
) -> dict[str, object]:
    try:
        return request.app.state.result_archive_service.list(page=page, page_size=page_size, status=status, strategy=strategy, query=q, date_from=date_from, date_to=date_to, sort=sort, order=order)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_ARCHIVE_INVALID", str(error))) from error


@router.get("/api/backtests/runs.csv")
def backtest_archive_export(
    request: Request,
    status: str | None = None, strategy: str | None = None, q: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    sort: str = "created_at", order: Literal["asc", "desc"] = "desc",
) -> Response:
    try:
        content = request.app.state.result_archive_service.csv_text(status=status, strategy=strategy, query=q, date_from=date_from, date_to=date_to, sort=sort, order=order)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_ARCHIVE_INVALID", str(error))) from error
    return Response(content=content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=backtest-results.csv"})


@router.get("/api/backtests/runs/{run_id}")
def backtest_archive_detail(request: Request, run_id: str) -> dict[str, object]:
    result = request.app.state.result_archive_service.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
    return result


@router.post("/api/backtests/runs/{run_id}/copy-config", status_code=201)
def backtest_archive_copy(request: Request, run_id: str) -> dict[str, object]:
    try:
        return request.app.state.result_archive_service.copy_config(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", str(error), entity_id=run_id)) from error


@router.delete("/api/backtests/runs/{run_id}", status_code=204)
def backtest_archive_delete(request: Request, run_id: str) -> Response:
    try:
        request.app.state.result_archive_service.delete(run_id)
    except ValueError as error:
        message = str(error)
        status_code = 404 if "找不到" in message or "not found" in message.lower() else 400
        code = "BACKTEST_NOT_FOUND" if status_code == 404 else "BACKTEST_ARCHIVE_INVALID"
        raise HTTPException(status_code=status_code, detail=_error_payload(code, message, entity_id=run_id)) from error
    return Response(status_code=204)


@router.get("/api/backtests/{run_id}")
def backtest_detail(request: Request, run_id: str) -> dict[str, object]:
    result = request.app.state.backtest_workbench_service.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
    return result


@router.post("/api/backtests/{run_id}/execute")
def backtest_execute(request: Request, run_id: str) -> dict[str, object]:
    try:
        result = run_isolated(request.app.state.settings, request.app.state.backtest_job_service, run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_EXECUTION_INVALID", str(error), entity_id=run_id)) from error
    if result is None:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
    return result


@router.post("/api/backtests/{run_id}/stop")
def backtest_stop(request: Request, run_id: str) -> dict[str, object]:
    try:
        return stop_run(request.app.state.backtest_job_service, run_id)
    except ValueError as error:
        message = str(error)
        missing = "not found" in message.lower() or "找不到" in message
        status_code = 404 if missing else 400
        code = "BACKTEST_NOT_FOUND" if missing else "BACKTEST_STOP_INVALID"
        raise HTTPException(status_code=status_code, detail=_error_payload(code, message, entity_id=run_id)) from error


@router.get("/api/backtests/{run_id}/status")
def backtest_status(request: Request, run_id: str) -> dict[str, object]:
    result = request.app.state.backtest_job_service.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
    return result
    raise AssertionError("unreachable")
