"""Path-safe file listings for LAN sync (no database, no secrets)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_SKIP_NAMES = {".DS_Store", ".gitkeep"}
_SKIP_DIRS = {"__pycache__", ".git"}


def safe_under(root: Path, rel: str) -> Path:
    text = str(rel or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("~"):
        raise ValueError("path is outside allowed root")
    parts = Path(text).parts
    if ".." in parts:
        raise ValueError("path is outside allowed root")
    origin = Path(root).resolve()
    target = (origin / text).resolve()
    if target != origin and origin not in target.parents:
        raise ValueError("path is outside allowed root")
    return target


def iter_rel_files(root: Path) -> list[dict[str, Any]]:
    origin = Path(root)
    if not origin.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in origin.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _SKIP_NAMES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(origin).parts):
            continue
        stat = path.stat()
        items.append({"rel": path.relative_to(origin).as_posix(), "size": int(stat.st_size), "mtime": int(stat.st_mtime)})
    items.sort(key=lambda item: str(item["rel"]))
    return items
