from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "quantlab"}
