from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/overview")
def overview(request: Request) -> dict[str, object]:
    return request.app.state.overview_service.get_overview()
