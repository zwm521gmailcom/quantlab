from typing import Literal

from fastapi import APIRouter, Query, Request

from quantlab.api.errors import _raise_research_error

router = APIRouter(tags=["research-runs"])


@router.get("/api/research-runs")
def research_run_list(
    request: Request,
    q: str | None = None,
    research_type: Literal["manual", "automatic"] | None = None,
    status: Literal["queued", "running", "completed", "failed"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return request.app.state.research_run_repository.list(
            query=q, research_type=research_type, status=status,
            page=page, page_size=page_size,
        )
    except ValueError as error:
        _raise_research_error(error)
    raise AssertionError("unreachable")


@router.post("/api/research-runs", status_code=201)
async def research_run_create(request: Request) -> dict[str, object]:
    body = await request.json()
    if not isinstance(body, dict):
        _raise_research_error(ValueError("research request body must be an object"))
    try:
        return request.app.state.research_run_repository.create(
            name=body.get("name", ""),
            research_type=body.get("research_type", ""),
            config=body.get("config", {}),
        )
    except ValueError as error:
        _raise_research_error(error)
    raise AssertionError("unreachable")


@router.get("/api/research-runs/{run_id}")
def research_run_detail(request: Request, run_id: str) -> dict[str, object]:
    result = request.app.state.research_run_repository.get(run_id)
    if result is None:
        _raise_research_error(ValueError("research run not found"))
    return result


@router.post("/api/research-runs/{run_id}/copy-config", status_code=200)
async def research_run_copy(run_id: str, request: Request) -> dict[str, object]:
    body = await request.json()
    if not isinstance(body, dict):
        _raise_research_error(ValueError("copy-config request body must be an object"))
    try:
        return request.app.state.research_run_repository.copy_config(run_id, name=body.get("name"))
    except ValueError as error:
        _raise_research_error(error)
    raise AssertionError("unreachable")


@router.post("/api/research-runs/{run_id}/status")
async def research_run_status(run_id: str, request: Request) -> dict[str, object]:
    body = await request.json()
    try:
        return request.app.state.research_run_repository.transition(run_id, str(body.get("status", "")))
    except ValueError as error:
        _raise_research_error(error)
    raise AssertionError("unreachable")
