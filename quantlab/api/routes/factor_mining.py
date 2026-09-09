from fastapi import APIRouter, HTTPException, Query, Request

from quantlab.api.errors import _json_body

router = APIRouter(tags=["factor-mining"])


@router.post("/api/factor-mining/runs", status_code=201)
async def factor_mining_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_mining_service.run(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error


@router.post("/api/factor-mining/jobs", status_code=201)
async def factor_mining_job_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_mining_service.create_job(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error


@router.post("/api/factor-mining/jobs/{run_id}/checkpoint")
async def factor_mining_job_checkpoint(run_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.factor_mining_service.checkpoint(run_id, body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.post("/api/factor-mining/jobs/{run_id}/cancel")
def factor_mining_job_cancel(request: Request, run_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.cancel(run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.post("/api/factor-mining/jobs/{run_id}/resume", status_code=201)
def factor_mining_job_resume(request: Request, run_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.resume(run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.get("/api/factor-jobs")
def factor_jobs_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.list_jobs(page=page, page_size=page_size)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"error_code": "FACTOR_JOB_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error


@router.get("/api/factor-jobs/{run_id}")
def factor_job_detail(request: Request, run_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.job_detail(run_id)
    except ValueError as error:
        status_code = 404 if "not found" in str(error) else 400
        raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_JOB_NOT_FOUND" if status_code == 404 else "FACTOR_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.post("/api/factor-jobs/{run_id}/enable")
async def factor_job_enable(run_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    candidate_ids = body.get("candidate_ids") if isinstance(body, dict) else None
    try:
        return request.app.state.factor_mining_service.enable(run_id, candidate_ids)
    except ValueError as error:
        status_code = 404 if "not found" in str(error) else 400
        raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.get("/api/factor-mining/runs/{run_id}")
def factor_mining_detail(request: Request, run_id: str) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.detail(run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail={"error_code": "FACTOR_MINING_NOT_FOUND", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.get("/api/factor-mining/runs/{run_id}/candidates/{dedupe_key}")
def factor_mining_candidate_detail(request: Request, run_id: str, dedupe_key: str) -> dict[str, object]:
    try:
        detail = request.app.state.factor_mining_service.detail(run_id)
        for candidate in detail["candidates"]:
            if candidate.get("dedupe_key") == dedupe_key:
                return candidate
        raise ValueError("candidate not found or was rejected")
    except ValueError as error:
        raise HTTPException(status_code=404, detail={"error_code": "FACTOR_MINING_CANDIDATE_NOT_FOUND", "message": str(error), "entity_id": run_id, "details": {}}) from error


@router.post("/api/factor-mining/runs/{run_id}/candidates/{dedupe_key}/draft")
def factor_mining_candidate_draft(request: Request, run_id: str, dedupe_key: str) -> dict[str, object]:
    try:
        return request.app.state.factor_mining_service.candidate_draft(run_id, dedupe_key)
    except ValueError as error:
        status_code = 404 if "not found" in str(error) else 400
        raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_MINING_CANDIDATE_NOT_FOUND" if status_code == 404 else "FACTOR_MINING_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error
