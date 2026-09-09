from pathlib import Path

from fastapi import APIRouter, Request

from quantlab.api.errors import _json_body, _strategy_error

router = APIRouter(tags=["models"])


@router.get("/api/models")
def model_list(request: Request) -> dict[str, object]:
    return request.app.state.strategy_center_service.list_models()


@router.post("/api/models", status_code=201)
async def model_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.create_model(str(body.get("entity_id", "")), str(body.get("name", "")))  # type: ignore[union-attr]
    except (AttributeError, ValueError) as error:
        _strategy_error(ValueError(str(error)))
    raise AssertionError("unreachable")


@router.get("/api/models/kinds")
def model_kinds(request: Request) -> dict[str, object]:
    return request.app.state.model_training_service.list_kinds()


@router.post("/api/models/design", status_code=201)
async def model_design(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.model_training_service.create_design(body)  # type: ignore[arg-type]
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.post("/api/models/catalog")
def model_catalog_ensure(request: Request) -> dict[str, object]:
    try:
        return request.app.state.model_training_service.ensure_catalog()
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.get("/api/models/{entity_id}/versions/{version_id}")
def model_version_detail(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    item = request.app.state.strategy_center_service.get_model_version(entity_id, version_id)
    if item is None:
        _strategy_error(ValueError("model version not found"), 404)
    return item


@router.post("/api/models/{entity_id}/versions", status_code=201)
async def model_version_create(entity_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.create_model_version(entity_id, body)  # type: ignore[arg-type]
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.post("/api/models/{entity_id}/versions/{version_id}/publish")
def model_version_publish(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.strategy_center_service.publish_model_version(entity_id, version_id)
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.get("/api/models/runs")
def model_training_runs(request: Request) -> list[dict[str, object]]:
    return request.app.state.strategy_center_service.list_training_runs()


@router.get("/api/models/runs/{run_id}")
def model_training_run_detail(request: Request, run_id: str) -> dict[str, object]:
    item = request.app.state.strategy_center_service.get_training_run(run_id)
    if item is None:
        _strategy_error(ValueError("training run not found"), 404)
    return item


@router.post("/api/models/{entity_id}/versions/{version_id}/training-runs", status_code=201)
async def model_training_run_create(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        config = body.get("config", body) if isinstance(body, dict) else {}
        return request.app.state.strategy_center_service.create_training_run(entity_id, version_id, config)  # type: ignore[arg-type]
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.post("/api/models/runs/{run_id}/status")
async def model_training_run_status(run_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.transition_training_run(run_id, str(body.get("status", "")))  # type: ignore[union-attr]
    except (AttributeError, ValueError) as error:
        _strategy_error(ValueError(str(error)))
    raise AssertionError("unreachable")


@router.post("/api/models/runs/{run_id}/artifacts")
async def model_training_run_artifact(run_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.attach_training_artifact(run_id, Path(str(body.get("path", ""))), str(body.get("display_name", "模型Artifact")))  # type: ignore[union-attr]
    except (AttributeError, ValueError, FileNotFoundError) as error:
        _strategy_error(ValueError(str(error)))
    raise AssertionError("unreachable")
