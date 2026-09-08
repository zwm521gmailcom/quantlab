"""Load the last compiled module while the .py source is missing."""

from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_pyc = Path(__file__).resolve().parent / "_recovered_pyc" / "trade_filters.pyc"
_code = SourcelessFileLoader(__name__, str(_pyc)).get_code(__name__)
if _code is None:
    raise ImportError(f"无法从字节码恢复：{_pyc}")
exec(_code, globals())

import re

_parse_expr_impl = parse_expr


def _normalize_bool_ops(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return raw
    normalized = re.sub(r"&&", " AND ", raw)
    normalized = re.sub(r"\|\|", " OR ", normalized)
    normalized = re.sub(r"&", " AND ", normalized)
    normalized = re.sub(r"\|", " OR ", normalized)
    return normalized


def parse_expr(text: str):
    return _parse_expr_impl(_normalize_bool_ops(text))


_parsed_open_filters: dict[str, object] = {}


def eval_open_filters(expressions, row):
    if expressions is None:
        return True
    for text in expressions:
        raw = str(text or "").strip()
        if not raw:
            continue
        tree = _parsed_open_filters.get(raw)
        if tree is None:
            tree = parse_expr(raw)
            _parsed_open_filters[raw] = tree
        if not _eval_tree(tree, row):
            return False
    return True
