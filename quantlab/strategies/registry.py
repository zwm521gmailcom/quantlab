"""Registry of built-in rule strategies."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quantlab.strategies.wiki_trend_follow import DEFAULTS as WIKI_DEFAULTS

_MODULE_PATH = Path(__file__).resolve().parent / "wiki_trend_follow.py"


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


RULE_STRATEGIES: dict[str, dict] = {
    "wiki_trend_follow": {
        "module": "quantlab.strategies.wiki_trend_follow",
        "title": "多指标趋势跟踪",
        "defaults": WIKI_DEFAULTS,
        "source_hash": _source_hash(_MODULE_PATH),
    },
}
