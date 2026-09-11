"""In-memory LAN peer list fed by UDP beacons."""
from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quantlab.config import DEFAULT_LAN_PORT, DEFAULT_UI_PORT, normalize_asset
from quantlab.services.machine_identity import load_machine_identity

LAN_PORT = DEFAULT_LAN_PORT
UI_PORT = DEFAULT_UI_PORT
PEER_TTL_SECONDS = 15.0
BEACON_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class LanPeer:
    machine_id: str
    hostname: str
    host: str
    port: int
    serial_prefix: int
    last_seen: float
    ui_port: int = UI_PORT
    asset: str = "a_share"


def validate_lan_host(host: object) -> str:
    text = str(host or "").strip()
    if not text or len(text) > 253:
        raise ValueError("invalid host")
    if any(char in text for char in "/\\:@?#"):
        raise ValueError("invalid host")
    if ".." in text or text.startswith("."):
        raise ValueError("invalid host")
    return text


def validate_lan_port(port: object) -> int:
    try:
        value = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("invalid port") from error
    if not 1 <= value <= 65535:
        raise ValueError("invalid port")
    return value


def _is_rfc1918(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if first == 10:
        return True
    if first == 192 and second == 168:
        return True
    return first == 172 and 16 <= second <= 31


def prefer_lan_ip(candidates: list[str]) -> str:
    unique: list[str] = []
    for ip in candidates:
        text = str(ip or "").strip()
        if not text or text in unique:
            continue
        unique.append(text)
    private = [ip for ip in unique if _is_rfc1918(ip)]
    if private:
        private.sort(key=lambda ip: (0 if ip.startswith("192.168.") else 1, ip))
        return private[0]
    usable = [
        ip
        for ip in unique
        if not ip.startswith("127.") and not ip.startswith("198.18.") and not ip.startswith("198.19.")
    ]
    if usable:
        return usable[0]
    return "127.0.0.1"


def _interface_ipv4() -> list[str]:
    found: list[str] = []
    for command in (["ifconfig"], ["ip", "-4", "-o", "addr"]):
        try:
            raw = subprocess.check_output(command, timeout=1, text=True, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        for match in re.finditer(r"inet(?: addr:)?\s*(\d+\.\d+\.\d+\.\d+)", raw):
            ip = match.group(1)
            if ip not in found:
                found.append(ip)
        if found:
            return found
    return found


def local_lan_ip() -> str:
    found = _interface_ipv4()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(0.3)
    try:
        probe.connect(("8.8.8.8", 80))
        found.append(str(probe.getsockname()[0] or ""))
    except OSError:
        pass
    finally:
        probe.close()
    return prefer_lan_ip(found)


def parse_beacon(raw: bytes, host: str, *, now: float | None = None) -> LanPeer | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("service") != "quantlab":
        return None
    machine_id = str(payload.get("machine_id") or "").strip()
    if not machine_id:
        return None
    try:
        port = int(payload.get("sync_port") or LAN_PORT)
        prefix = int(payload.get("serial_prefix") or 10)
        ui_port = int(payload.get("ui_port") or UI_PORT)
        asset = normalize_asset(payload.get("asset") or "a_share")
    except (TypeError, ValueError):
        return None
    announced = str(payload.get("host") or host or "").strip() or host
    try:
        announced = validate_lan_host(announced)
        port = validate_lan_port(port)
        ui_port = validate_lan_port(ui_port)
    except ValueError:
        return None
    return LanPeer(
        machine_id=machine_id,
        hostname=str(payload.get("hostname") or announced),
        host=announced,
        port=port,
        serial_prefix=prefix,
        last_seen=float(now if now is not None else time.time()),
        ui_port=ui_port,
        asset=asset,
    )


def beacon_payload(
    machine_id: str,
    serial_prefix: int,
    *,
    host: str | None = None,
    hostname: str | None = None,
    ui_port: int = UI_PORT,
    sync_port: int = LAN_PORT,
    asset: str = "a_share",
) -> bytes:
    return json.dumps(
        {
            "schema": 2,
            "service": "quantlab",
            "machine_id": machine_id,
            "serial_prefix": int(serial_prefix),
            "sync_port": int(sync_port),
            "ui_port": int(ui_port),
            "asset": normalize_asset(asset),
            "host": host or local_lan_ip(),
            "hostname": hostname or socket.gethostname(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class PeerRegistry:
    def __init__(self, *, ttl: float = PEER_TTL_SECONDS) -> None:
        self.ttl = float(ttl)
        self._peers: dict[str, LanPeer] = {}

    def note(self, peer: LanPeer) -> None:
        self._peers[peer.machine_id] = peer

    def online(self, *, now: float | None = None, self_id: str = "", asset: str | None = None) -> list[dict[str, Any]]:
        current = float(now if now is not None else time.time())
        wanted = normalize_asset(asset) if asset else None
        items = []
        for peer in self._peers.values():
            if current - peer.last_seen > self.ttl:
                continue
            if wanted is not None and peer.asset != wanted:
                continue
            items.append(
                {
                    "machine_id": peer.machine_id,
                    "hostname": peer.hostname,
                    "host": peer.host,
                    "port": peer.port,
                    "ui_port": peer.ui_port,
                    "ui_url": f"http://{peer.host}:{peer.ui_port}/",
                    "asset": peer.asset,
                    "serial_prefix": peer.serial_prefix,
                    "self": bool(self_id) and peer.machine_id == self_id,
                    "age_s": round(current - peer.last_seen, 1),
                }
            )
        items.sort(key=lambda item: (not item["self"], str(item["hostname"]), str(item["machine_id"])))
        return items

    def find(self, machine_id: str, *, now: float | None = None) -> LanPeer | None:
        current = float(now if now is not None else time.time())
        peer = self._peers.get(str(machine_id or ""))
        if peer is None or current - peer.last_seen > self.ttl:
            return None
        return peer


def self_peer(
    runtime_root: Path | None,
    *,
    ui_port: int | None = None,
    lan_port: int | None = None,
    asset: str = "a_share",
    now: float | None = None,
) -> LanPeer:
    machine = load_machine_identity(runtime_root)
    host = local_lan_ip()
    return LanPeer(
        machine_id=str(machine.get("machine_id") or ""),
        hostname=socket.gethostname(),
        host=host,
        port=int(lan_port or LAN_PORT),
        serial_prefix=int(machine.get("serial_prefix") or 10),
        last_seen=float(now if now is not None else time.time()),
        ui_port=int(ui_port or UI_PORT),
        asset=normalize_asset(asset),
    )
