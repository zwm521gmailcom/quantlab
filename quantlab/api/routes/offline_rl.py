from fastapi import APIRouter, HTTPException, Query, Request

from quantlab.api.errors import _error_payload, _json_body
from quantlab.services.offline_rl.experiment import OfflineRlExperimentService

router = APIRouter(tags=["offline-rl"])


def _service(request: Request) -> OfflineRlExperimentService:
    service = getattr(request.app.state, "offline_rl_service", None)
    if service is None:
        service = OfflineRlExperimentService(
            request.app.state.settings,
            request.app.state.database,
        )
        request.app.state.offline_rl_service = service
    return service


def _busy() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=_error_payload("OFFLINE_RL_BUSY", "offline rl experiment already running"),
    )


@router.get("/api/offline-rl/preview")
def offline_rl_preview(
    request: Request,
    k: int = Query(8),
    window: int = Query(20),
) -> dict[str, object]:
    return _service(request).preview(k=k, window=window)


@router.post("/api/offline-rl/run")
async def offline_rl_run(request: Request) -> dict[str, object]:
    try:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return _service(request).run(
            k=int(body.get("k", 8)),
            window=int(body.get("window", 20)),
            gamma=float(body.get("gamma", 0.99)),
            iterations=int(body.get("iterations", 10)),
        )
    except RuntimeError as error:
        if str(error) == "busy":
            raise _busy() from error
        raise
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=_error_payload("OFFLINE_RL_INVALID", str(error)),
        ) from error


@router.get("/api/offline-rl/progress")
def offline_rl_progress(request: Request) -> dict[str, object]:
    return _service(request).progress()
