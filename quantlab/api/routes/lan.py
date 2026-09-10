from fastapi import APIRouter, HTTPException, Request

from quantlab.api.errors import _error_payload
from quantlab.services.lan_peers import LAN_PORT, PeerRegistry, local_lan_ip, self_peer
from quantlab.services.lan_sync import SyncBusyError, coordinate_market_sync, coordinate_results_sync
from quantlab.services.machine_identity import load_machine_identity

router = APIRouter(tags=["lan"])


def _registry(request: Request) -> PeerRegistry:
    registry = getattr(request.app.state, "peer_registry", None)
    if registry is None:
        registry = PeerRegistry()
        request.app.state.peer_registry = registry
    return registry


def _busy() -> HTTPException:
    return HTTPException(status_code=409, detail=_error_payload("LAN_SYNC_BUSY", "sync already running"))


@router.get("/api/lan/peers")
def list_peers(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    registry = _registry(request)
    machine = load_machine_identity(settings.runtime_root)
    peer = self_peer(settings.runtime_root)
    if peer.machine_id:
        registry.note(peer)
    return {"lan_port": LAN_PORT, "peers": registry.online(self_id=str(machine.get("machine_id") or ""))}


@router.post("/api/lan/sync/results")
def sync_results_api(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    registry = _registry(request)
    machine = load_machine_identity(settings.runtime_root)
    peer = self_peer(settings.runtime_root)
    if peer.machine_id:
        registry.note(peer)
    try:
        return coordinate_results_sync(
            settings,
            request.app.state.database,
            registry.online(self_id=str(machine.get("machine_id") or "")),
            self_host=local_lan_ip(),
            self_port=LAN_PORT,
        )
    except SyncBusyError as error:
        raise _busy() from error


@router.post("/api/lan/sync/market")
async def sync_market_api(request: Request) -> dict[str, object]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    payload = body if isinstance(body, dict) else {}
    settings = request.app.state.settings
    registry = _registry(request)
    machine = load_machine_identity(settings.runtime_root)
    self_id = str(machine.get("machine_id") or "")
    peer = self_peer(settings.runtime_root)
    if peer.machine_id:
        registry.note(peer)
    source_id = str(payload.get("machine_id") or "").strip() or self_id
    peers = registry.online(self_id=self_id)
    try:
        return coordinate_market_sync(settings, peers, source_id, self_id=self_id)
    except SyncBusyError as error:
        raise _busy() from error
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=_error_payload("LAN_PEER_NOT_FOUND", str(error)),
        ) from error
