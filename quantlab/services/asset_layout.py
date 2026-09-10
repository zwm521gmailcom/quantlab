"""Planned on-disk layout by asset class. Does not create or move files."""
from __future__ import annotations

from typing import Any

ASSET_CLASSES: tuple[tuple[str, str], ...] = (
    ("cn_a", "A股"),
    ("hk", "港股"),
    ("us", "美股"),
    ("crypto", "数字货币"),
)
ASSET_CLASS_LABELS = {code: label for code, label in ASSET_CLASSES}
LEGACY_ASSET_CLASS = "cn_a"


def normalize_asset_class(code: object | None = None) -> str:
    text = "" if code is None else str(code).strip()
    if not text:
        return LEGACY_ASSET_CLASS
    if text not in ASSET_CLASS_LABELS:
        raise ValueError("asset_class is invalid")
    return text


def asset_class_fields(code: object | None = None) -> dict[str, str]:
    normalized = normalize_asset_class(code)
    return {
        "asset_class": normalized,
        "asset_class_label": ASSET_CLASS_LABELS[normalized],
    }


PATH_ROLES: tuple[tuple[str, str], ...] = (
    ("canonical", "标准宽表"),
    ("derived", "旁路因子"),
    ("raw", "原始下载"),
    ("factors_runtime", "本机因子产物"),
)
DIRECTORY_PLAN_LEAD = "A股沿用现有路径，不搬家。港股、美股、数字货币以后按下面落盘。因子也带同一套分类。"
DIRECTORY_PLAN_RULES = (
    "A股沿用现有路径，不搬家。",
    "因子必须自带资产分类；唯一键是（资产分类, 因子ID, 版本）。",
    "计算和回测不得混用不同资产分类。",
    "港股、美股、数字货币以后落在 data/{分类}/ 下，不要写入现有 A 股 parquet。",
    "局域网只同步 data/，不同步 quantlab_runtime/factors/。",
)


def _join(root: str, *parts: str, directory: bool = False) -> str:
    chunks = [str(root).replace("\\", "/").rstrip("/")]
    for part in parts:
        text = str(part).replace("\\", "/").strip("/")
        if text:
            chunks.append(text)
    path = "/".join(chunk for chunk in chunks if chunk not in {"", "."})
    if not path:
        path = "."
    if directory and path != ".":
        return f"{path}/"
    return path


def _class_paths(code: str, *, data_root: str, raw_root: str, runtime_root: str) -> dict[str, str]:
    if code == LEGACY_ASSET_CLASS:
        return {
            "canonical": _join(data_root, "canonical.parquet"),
            "derived": _join(data_root, "derived", directory=True),
            "raw": _join(raw_root, directory=True),
            "factors_runtime": _join(runtime_root, "factors", directory=True),
        }
    return {
        "canonical": _join(data_root, code, "canonical.parquet"),
        "derived": _join(data_root, code, "derived", directory=True),
        "raw": _join(data_root, code, "raw", directory=True),
        "factors_runtime": _join(runtime_root, "factors", code, directory=True),
    }


def directory_plan(*, data_root: str, raw_root: str, runtime_root: str) -> dict[str, Any]:
    classes = []
    for code, label in ASSET_CLASSES:
        current = code == LEGACY_ASSET_CLASS
        item: dict[str, Any] = {
            "code": code,
            "label": label,
            "current": current,
            "status": "现行" if current else "尚未使用",
            "paths": _class_paths(code, data_root=data_root, raw_root=raw_root, runtime_root=runtime_root),
        }
        if current:
            item["note"] = "现有因子文件可平铺在本机因子产物目录；新文件可用 cn_a/ 子目录。"
        classes.append(item)
    return {
        "lead": DIRECTORY_PLAN_LEAD,
        "path_roles": [{"key": key, "label": label} for key, label in PATH_ROLES],
        "asset_classes": classes,
        "rules": list(DIRECTORY_PLAN_RULES),
    }
