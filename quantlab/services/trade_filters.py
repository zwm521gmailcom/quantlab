"""开仓过滤表达式解析与求值。"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_OPEN_LIMIT_EXPR = "open < up_limit AND open > down_limit"
DERIVED_FIELDS = ("sma200", "sma_200", "ma200")
FIELD_ALIASES = {"sma200": "sma_200", "ma200": "sma_200"}
FILL_FIELDS = {"hfq_open", "up_limit", "open", "down_limit"}

_TOKEN = re.compile(
    r"""
    \s*
    (
        >=|<=|==|!=|〉|〈|》|《|>|<|
        \(|\)|
        AND|OR|NOT|
        [A-Za-z_][A-Za-z0-9_]*|
        \d+(?:\.\d+)?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_OPS = {
    ">": ">",
    "<": "<",
    ">=": ">=",
    "<=": "<=",
    "==": "==",
    "!=": "!=",
    "》": ">",
    "《": "<",
    "〉": ">",
    "〈": "<",
}
_BOOL = {"AND": "AND", "OR": "OR", "NOT": "NOT"}


def tokenize(text: str) -> list[str]:
    raw = str(text or "").replace("＝", "==").strip()
    if not raw:
        raise ValueError("过滤表达式不能为空")
    tokens: list[str] = []
    pos = 0
    while pos < len(raw):
        if raw[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(raw, pos)
        if not match:
            raise ValueError(f"无法解析过滤表达式：{raw}")
        tokens.append(match.group(1))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def get(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("过滤表达式不完整")
        self.index += 1
        return token

    def parse(self) -> Any:
        tree = self.parse_or()
        if self.peek() is not None:
            raise ValueError("无法解析过滤表达式")
        return tree

    def parse_or(self) -> Any:
        left = self.parse_and()
        while (self.peek() or "").upper() == "OR":
            self.get()
            left = ("or", left, self.parse_and())
        return left

    def parse_and(self) -> Any:
        left = self.parse_not()
        while (self.peek() or "").upper() == "AND":
            self.get()
            left = ("and", left, self.parse_not())
        return left

    def parse_not(self) -> Any:
        if (self.peek() or "").upper() == "NOT":
            self.get()
            return ("not", self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self) -> Any:
        left = self.parse_primary()
        token = self.peek()
        if token in _OPS:
            op = _OPS[self.get()]
            right = self.parse_primary()
            return ("cmp", op, left, right)
        return left

    def parse_primary(self) -> Any:
        token = self.get()
        if token == "(":
            tree = self.parse_or()
            if self.get() != ")":
                raise ValueError("过滤表达式括号不配对")
            return tree
        upper = token.upper()
        if upper in _BOOL:
            raise ValueError("无法解析过滤表达式")
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return ("num", float(token))
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            return ("name", token)
        raise ValueError("无法解析过滤表达式")


def _parse_expr_impl(text: str) -> Any:
    return _Parser(tokenize(text)).parse()


def expression_fields(tree: Any) -> set[str]:
    kind = tree[0]
    if kind == "name":
        return {tree[1]}
    if kind == "num":
        return set()
    if kind == "not":
        return expression_fields(tree[1])
    if kind == "cmp":
        return expression_fields(tree[2]) | expression_fields(tree[3])
    if kind in {"and", "or"}:
        return expression_fields(tree[1]) | expression_fields(tree[2])
    return set()


def _resolve_name(name: str) -> str:
    return FIELD_ALIASES.get(name, name)


def _value(node: Any, row: dict[str, Any]) -> float | None:
    if node[0] == "num":
        return float(node[1])
    if node[0] != "name":
        return None
    column = _resolve_name(node[1])
    if column not in row and node[1] in row:
        column = node[1]
    raw = row.get(column)
    if raw is None:
        raw = row.get(node[1])
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _eval_tree(tree: Any, row: dict[str, Any]) -> bool:
    kind = tree[0]
    if kind == "not":
        return not _eval_tree(tree[1], row)
    if kind == "and":
        return _eval_tree(tree[1], row) and _eval_tree(tree[2], row)
    if kind == "or":
        return _eval_tree(tree[1], row) or _eval_tree(tree[2], row)
    if kind == "cmp":
        left = _value(tree[2], row)
        right = _value(tree[3], row)
        if left is None or right is None:
            return False
        op = tree[1]
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        return False
    if kind in {"name", "num"}:
        value = _value(tree, row) if kind == "name" else float(tree[1])
        return bool(value)
    return False


def needs_sma200(expressions: list[str]) -> bool:
    names: set[str] = set()
    for text in expressions:
        if not str(text or "").strip():
            continue
        names |= expression_fields(parse_expr(text))
    return bool(names & set(DERIVED_FIELDS))


def split_open_filters(expressions: list[str]) -> tuple[list[str], list[str]]:
    signal: list[str] = []
    fill: list[str] = []
    for text in expressions:
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        names = {_resolve_name(name) for name in expression_fields(parse_expr(cleaned))}
        names |= {name for name in expression_fields(parse_expr(cleaned))}
        fill_names = {_resolve_name(name) for name in FILL_FIELDS} | set(FILL_FIELDS)
        if names and names <= fill_names:
            fill.append(cleaned)
        else:
            signal.append(cleaned)
    return signal, fill


def normalize_trade_filters(
    raw: Any,
    *,
    skip_open_limit: bool,
    allowed_fields: set[str],
) -> dict[str, list[str]]:
    allowed = set(allowed_fields) | set(DERIVED_FIELDS)
    if isinstance(raw, dict) and isinstance(raw.get("open"), list):
        expressions = [str(item).strip() for item in raw["open"] if str(item).strip()]
    elif skip_open_limit:
        expressions = [DEFAULT_OPEN_LIMIT_EXPR]
    else:
        expressions = []
    for text in expressions:
        tree = parse_expr(text)
        unknown = sorted(
            name
            for name in expression_fields(tree)
            if name not in allowed and _resolve_name(name) not in allowed
        )
        if unknown:
            raise ValueError(f"过滤表达式里没有字段 {unknown[0]}，请从表字段里选")
    return {"open": expressions}


def open_expressions(config: dict[str, Any]) -> list[str]:
    block = config.get("trade_filters")
    if isinstance(block, dict) and isinstance(block.get("open"), list):
        return [str(item).strip() for item in block["open"] if str(item).strip()]
    if bool(config.get("skip_open_limit", True)):
        return [DEFAULT_OPEN_LIMIT_EXPR]
    return []


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
