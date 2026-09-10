"""UDP discovery, LAN HTTP sidecar, and 04:00 market push. Started only by `quantlab serve`."""
from __future__ import annotations

import logging
import socket
import threading
from datetime import date, datetime
from typing import Any

import uvicorn

from quantlab.api.lan_app import create_lan_app
from quantlab.services.lan_peers import BEACON_INTERVAL_SECONDS, LAN_PORT, PeerRegistry, beacon_payload, parse_beacon, self_peer
from quantlab.services.lan_sync import coordinate_market_sync
from quantlab.services.machine_identity import load_machine_identity

LOGGER = logging.getLogger("quantlab.lan")


def should_run_market_sync_0400(enabled: bool, now: datetime, last_run_date: date | None) -> bool:
    if not enabled:
        return False
    if now.hour != 4:
        return False
    return last_run_date != now.date()


def start_lan_sidecar(app: Any) -> None:
    settings = app.state.settings
    registry = PeerRegistry()
    app.state.peer_registry = registry
    stop = threading.Event()
    app.state.lan_stop = stop
    lan_app = create_lan_app(settings, app.state.database)
    threading.Thread(target=_udp_listen, args=(registry, stop), name="quantlab-lan-udp", daemon=True).start()
    threading.Thread(target=_udp_beacon, args=(settings, registry, stop), name="quantlab-lan-beacon", daemon=True).start()
    threading.Thread(target=_lan_http, args=(lan_app,), name="quantlab-lan-http", daemon=True).start()
    threading.Thread(target=_market_scheduler, args=(app, stop), name="quantlab-lan-0400", daemon=True).start()


def _udp_listen(registry: PeerRegistry, stop: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", LAN_PORT))
        sock.settimeout(1.0)
        while not stop.is_set():
            try:
                raw, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    break
                LOGGER.warning("LAN UDP listen failed", exc_info=True)
                break
            peer = parse_beacon(raw, addr[0])
            if peer is not None:
                registry.note(peer)
    except OSError:
        LOGGER.warning("cannot bind UDP %s; LAN discovery will not receive peers", LAN_PORT, exc_info=True)
    finally:
        sock.close()


def _udp_beacon(settings: Any, registry: PeerRegistry, stop: threading.Event) -> None:
    machine = load_machine_identity(settings.runtime_root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        while not stop.is_set():
            peer = self_peer(settings.runtime_root)
            if peer.machine_id:
                registry.note(peer)
            payload = beacon_payload(
                str(machine.get("machine_id") or peer.machine_id),
                int(machine.get("serial_prefix") or peer.serial_prefix),
                host=peer.host,
                hostname=peer.hostname,
            )
            try:
                sock.sendto(payload, ("255.255.255.255", LAN_PORT))
            except OSError:
                LOGGER.debug("LAN beacon send failed", exc_info=True)
            stop.wait(BEACON_INTERVAL_SECONDS)
    finally:
        sock.close()


def _lan_http(lan_app: Any) -> None:
    try:
        uvicorn.run(lan_app, host="0.0.0.0", port=LAN_PORT, log_level="warning")
    except OSError:
        LOGGER.warning("cannot bind TCP %s; other machines cannot pull files from this host", LAN_PORT, exc_info=True)


def _market_scheduler(app: Any, stop: threading.Event) -> None:
    last_run_date: date | None = None
    while not stop.wait(30):
        try:
            enabled = bool(app.state.settings_service.public().get("lan", {}).get("market_sync_at_0400"))
            now = datetime.now()
            if not should_run_market_sync_0400(enabled, now, last_run_date):
                continue
            settings = app.state.settings
            registry = app.state.peer_registry
            machine = load_machine_identity(settings.runtime_root)
            self_id = str(machine.get("machine_id") or "")
            peer = self_peer(settings.runtime_root)
            if peer.machine_id:
                registry.note(peer)
            coordinate_market_sync(
                settings,
                registry.online(self_id=self_id),
                self_id,
                self_id=self_id,
            )
            last_run_date = now.date()
        except Exception:
            LOGGER.warning("04:00 market sync failed", exc_info=True)
