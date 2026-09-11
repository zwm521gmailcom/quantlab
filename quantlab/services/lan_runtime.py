"""UDP discovery, LAN HTTP sidecar, and 04:00 market push. Started only by `quantlab serve`."""
from __future__ import annotations

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

import httpx
import uvicorn

from quantlab.api.lan_app import create_lan_app
from quantlab.services.lan_peers import (
    BEACON_INTERVAL_SECONDS,
    HELLO_PROBE_TIMEOUT,
    PROBE_INTERVAL_SECONDS,
    SUBNET_PROBE_EVERY,
    PeerRegistry,
    beacon_payload,
    beacon_targets,
    local_lan_ip,
    parse_beacon,
    parse_hello,
    probe_ips_from_arp,
    read_arp_table,
    same_subnet_hosts,
    self_peer,
)
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
    threading.Thread(target=_udp_listen, args=(settings, registry, stop), name="quantlab-lan-udp", daemon=True).start()
    threading.Thread(target=_udp_beacon, args=(settings, registry, stop), name="quantlab-lan-beacon", daemon=True).start()
    threading.Thread(target=_http_probe, args=(settings, registry, stop), name="quantlab-lan-probe", daemon=True).start()
    threading.Thread(target=_lan_http, args=(lan_app, int(settings.lan_port)), name="quantlab-lan-http", daemon=True).start()
    threading.Thread(target=_market_scheduler, args=(app, stop), name="quantlab-lan-0400", daemon=True).start()


def _udp_listen(settings: Any, registry: PeerRegistry, stop: threading.Event) -> None:
    lan_port = int(settings.lan_port)
    asset = str(settings.asset)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", lan_port))
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
            if peer is not None and peer.asset == asset:
                registry.note(peer)
    except OSError:
        LOGGER.warning("cannot bind UDP %s; LAN discovery will not receive peers", lan_port, exc_info=True)
    finally:
        sock.close()


def _udp_beacon(settings: Any, registry: PeerRegistry, stop: threading.Event) -> None:
    machine = load_machine_identity(settings.runtime_root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lan_port = int(settings.lan_port)
    bound_host = ""
    try:
        while not stop.is_set():
            peer = self_peer(
                settings.runtime_root,
                ui_port=settings.port,
                lan_port=settings.lan_port,
                asset=str(settings.asset),
            )
            if peer.machine_id:
                registry.note(peer)
            if peer.host and peer.host != bound_host and not peer.host.startswith("127."):
                try:
                    sock.bind((peer.host, 0))
                    bound_host = peer.host
                except OSError:
                    LOGGER.debug("LAN beacon bind %s failed", peer.host, exc_info=True)
            payload = beacon_payload(
                str(machine.get("machine_id") or peer.machine_id),
                int(machine.get("serial_prefix") or peer.serial_prefix),
                host=peer.host,
                hostname=peer.hostname,
                ui_port=int(settings.port),
                sync_port=lan_port,
                asset=str(settings.asset),
            )
            for dest in beacon_targets(peer.host):
                try:
                    sock.sendto(payload, (dest, lan_port))
                except OSError:
                    LOGGER.debug("LAN beacon send to %s failed", dest, exc_info=True)
            stop.wait(BEACON_INTERVAL_SECONDS)
    finally:
        sock.close()


def _http_probe(settings: Any, registry: PeerRegistry, stop: threading.Event) -> None:
    machine = load_machine_identity(settings.runtime_root)
    self_id = str(machine.get("machine_id") or "")
    lan_port = int(settings.lan_port)
    asset = str(settings.asset)
    cycle = 0
    while not stop.is_set():
        try:
            self_ip = local_lan_ip()
            arp_hosts = probe_ips_from_arp(read_arp_table(), self_ip)
            if arp_hosts:
                _probe_hello_hosts(registry, arp_hosts, self_id=self_id, lan_port=lan_port, asset=asset)
            if cycle % SUBNET_PROBE_EVERY == 0:
                known = set(arp_hosts)
                rest = [host for host in same_subnet_hosts(self_ip, self_ip=self_ip) if host not in known]
                if rest:
                    _probe_hello_hosts(registry, rest, self_id=self_id, lan_port=lan_port, asset=asset)
        except Exception:
            LOGGER.debug("LAN hello probe failed", exc_info=True)
        cycle += 1
        stop.wait(PROBE_INTERVAL_SECONDS)


def _probe_hello_hosts(
    registry: PeerRegistry,
    hosts: list[str],
    *,
    self_id: str = "",
    lan_port: int,
    asset: str,
) -> None:
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=8)
    with httpx.Client(timeout=HELLO_PROBE_TIMEOUT, limits=limits) as client:

        def one(host: str) -> Any:
            try:
                response = client.get(f"http://{host}:{lan_port}/hello")
                response.raise_for_status()
                return parse_hello(response.json(), host)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = [pool.submit(one, host) for host in hosts]
            for future in as_completed(futures):
                peer = future.result()
                if peer is None:
                    continue
                if self_id and peer.machine_id == self_id:
                    continue
                if peer.asset != asset:
                    continue
                registry.note(peer)


def _lan_http(lan_app: Any, lan_port: int) -> None:
    try:
        uvicorn.run(lan_app, host="0.0.0.0", port=lan_port, log_level="warning")
    except OSError:
        LOGGER.warning("cannot bind TCP %s; other machines cannot pull files from this host", lan_port, exc_info=True)


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
            peer = self_peer(
                settings.runtime_root,
                ui_port=settings.port,
                lan_port=settings.lan_port,
                asset=str(settings.asset),
            )
            if peer.machine_id:
                registry.note(peer)
            lan = app.state.settings_service.public().get("lan") or {}
            coordinate_market_sync(
                settings,
                registry.online(self_id=self_id, asset=str(settings.asset)),
                self_id,
                self_id=self_id,
                categories=lan.get("market_sync_categories"),
            )
            last_run_date = now.date()
        except Exception:
            LOGGER.warning("04:00 market sync failed", exc_info=True)
