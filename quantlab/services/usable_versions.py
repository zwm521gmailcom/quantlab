"""Curated, non-overfit backtest versions shown from the plan page.

Admission is deliberate. A high full-sample return is not enough.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "web/assets/backtest/usable-versions.json"
_MICRO = re.compile(r"MA1(?!50(?:\b|\+)|70(?:\b|\+))[0-9]{2}|MA14[0-9]|MA16[0-9]")
_FORBIDDEN = ("Top3", "top3", "滑点2", "2bp", "种子", "num_leaves", "叶子14", "叶子15")


def load_catalog(path: Path | None = None) -> dict:
    target = path or CATALOG_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def rejection_reason(item: dict) -> str | None:
    run_id = str(item.get("run_id") or "").strip()
    title = str(item.get("title") or "").strip()
    why = str(item.get("why") or "").strip()
    admitted = str(item.get("admitted") or "").strip()
    blob = " ".join([title, why, admitted, str(item.get("role") or "")])
    if not run_id:
        return "缺少 run_id"
    if not title or not why:
        return "缺少标题或收录理由"
    if _MICRO.search(blob):
        return "含微窗，不收录"
    for token in _FORBIDDEN:
        if token in blob:
            return f"含禁止项 {token}"
    frozen_before_search = "搜索开始前" in admitted or "搜索前已冻结" in why
    holdout_not_used = "2024–2026" in why and ("未参与" in why or "只报告" in why)
    if not frozen_before_search and not holdout_not_used:
        return "必须是搜索前已冻结，或写明 2024–2026 未参与挑选"
    return None


def admit(item: dict, path: Path | None = None) -> dict:
    reason = rejection_reason(item)
    if reason:
        raise ValueError(reason)
    target = path or CATALOG_PATH
    catalog = load_catalog(target)
    items = list(catalog.get("items") or [])
    if any(str(row.get("run_id")) == str(item["run_id"]) for row in items):
        return catalog
    items.append(
        {
            "run_id": str(item["run_id"]).strip(),
            "title": str(item["title"]).strip(),
            "role": str(item.get("role") or "研究底座").strip(),
            "admitted": str(item.get("admitted") or "").strip(),
            "why": str(item["why"]).strip(),
        }
    )
    catalog["items"] = items
    target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return catalog
