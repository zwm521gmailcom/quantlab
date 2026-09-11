"""Path-safe file listings for LAN sync (no database, no secrets)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_SKIP_NAMES = {".DS_Store", ".gitkeep"}
_SKIP_DIRS = {"__pycache__", ".git"}
DATA_SYNC_CATEGORIES = ("canonical", "derived", "raw", "source_tables")
DATA_SYNC_CATEGORY_LABELS = {
    "canonical": "标准宽表",
    "derived": "旁路因子",
    "raw": "原始 raw",
    "source_tables": "来源整理表",
}
_NESTED_ASSETS = {"hk", "us", "crypto"}


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


def classify_data_rel(rel: object) -> str | None:
    parts = Path(str(rel or "").replace("\\", "/")).parts
    if parts and parts[0] in _NESTED_ASSETS:
        parts = parts[1:]
    if not parts:
        return None
    head = parts[0]
    if head in {"derived", "raw", "source_tables"}:
        return head
    if len(parts) == 1 and head.endswith(".parquet") and head.startswith("canonical"):
        return "canonical"
    return None


def normalize_data_categories(raw: object = None) -> tuple[str, ...]:
    if raw is None:
        return DATA_SYNC_CATEGORIES
    if isinstance(raw, str):
        parts = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        parts = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise ValueError("至少选择一类数据再同步。")
    if not parts:
        raise ValueError("至少选择一类数据再同步。")
    unknown = [item for item in parts if item not in DATA_SYNC_CATEGORIES]
    if unknown:
        raise ValueError("同步类别无效")
    wanted = set(parts)
    return tuple(item for item in DATA_SYNC_CATEGORIES if item in wanted)


def filter_data_tree(items: list[dict[str, Any]], categories: object = None) -> list[dict[str, Any]]:
    wanted = set(normalize_data_categories(categories))
    return [item for item in items if classify_data_rel(item.get("rel")) in wanted]
