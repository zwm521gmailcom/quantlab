from fastapi import APIRouter, Request

from quantlab import __version__

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "quantlab",
        "version": __version__,
        "asset": str(settings.asset),
        "asset_label": settings.asset_label,
        "port": int(settings.port),
        "lan_port": int(settings.lan_port),
    }
