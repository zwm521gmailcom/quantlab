from fastapi import APIRouter, Request

from quantlab.api.errors import _json_body, _strategy_error

router = APIRouter(tags=["strategies"])


@router.get("/api/strategies")
def strategy_list(request: Request) -> dict[str, object]:
    return request.app.state.strategy_center_service.list_strategies()


@router.post("/api/strategies", status_code=201)
async def strategy_create(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.create_strategy(str(body.get("entity_id", "")), str(body.get("name", "")))  # type: ignore[union-attr]
    except (AttributeError, ValueError) as error:
        _strategy_error(ValueError(str(error)))
    raise AssertionError("unreachable")


@router.get("/api/strategies/{entity_id}/versions/{version_id}")
def strategy_version_detail(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    item = request.app.state.strategy_center_service.get_strategy_version(entity_id, version_id)
    if item is None:
        _strategy_error(ValueError("strategy version not found"), 404)
    return item


@router.post("/api/strategies/{entity_id}/versions", status_code=201)
async def strategy_version_create(entity_id: str, request: Request) -> dict[str, object]:
    body = await _json_body(request)
    try:
        return request.app.state.strategy_center_service.create_strategy_version(entity_id, body)  # type: ignore[arg-type]
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.post("/api/strategies/{entity_id}/versions/{version_id}/publish")
def strategy_version_publish(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.strategy_center_service.publish_strategy_version(entity_id, version_id)
    except ValueError as error:
        _strategy_error(error)
    raise AssertionError("unreachable")


@router.post("/api/strategies/{entity_id}/versions/{version_id}/copy", status_code=201)
def strategy_version_copy(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.strategy_center_service.copy_strategy_version(entity_id, version_id)
    except ValueError as error:
        _strategy_error(error, 404 if "not found" in str(error) else 400)
    raise AssertionError("unreachable")


@router.get("/api/strategies/{entity_id}/versions/{left}/diff/{right}")
def strategy_version_diff(request: Request, entity_id: str, left: str, right: str) -> dict[str, object]:
    try:
        return request.app.state.strategy_center_service.diff_strategy_versions(entity_id, left, right)
    except ValueError as error:
        _strategy_error(error, 404)
    raise AssertionError("unreachable")


@router.post("/api/strategies/{entity_id}/versions/{version_id}/backtest-draft")
def strategy_to_backtest_draft(request: Request, entity_id: str, version_id: str) -> dict[str, object]:
    try:
        return request.app.state.strategy_center_service.to_backtest_draft(entity_id, version_id)
    except ValueError as error:
        _strategy_error(error, 404)
    raise AssertionError("unreachable")
