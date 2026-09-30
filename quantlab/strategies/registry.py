"""Registry of built-in rule strategies."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quantlab.strategies.volume_week_screen import DEFAULTS as VOLUME_WEEK_DEFAULTS
from quantlab.strategies.wiki_trend_follow import DEFAULTS as WIKI_DEFAULTS

_MODULE_PATH = Path(__file__).resolve().parent / "wiki_trend_follow.py"
_VOLUME_WEEK_PATH = Path(__file__).resolve().parent / "volume_week_screen.py"


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
    "volume_week_screen": {
        "module": "quantlab.strategies.volume_week_screen",
        "title": "周量翻倍·周涨幅从小到大",
        "defaults": VOLUME_WEEK_DEFAULTS,
        "source_hash": _source_hash(_VOLUME_WEEK_PATH),
    },
}
