"""The local GPT-OSS 120B proposes a hypothesis and a small batch of factors.

A repeated name or formula is skipped. Every other formula is validated and
tested on its own LightGBM TopkDropout book. It is kept when its own test
annual return beats its own benchmark and its test information ratio is positive.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.config import Settings
from quantlab.services.qlib_export import EXTRA_FIELDS, convert_frame, load_source_frame, to_iso_date, to_qlib_symbol
from quantlab.services.qlib_strategy import run_factor_strategy

MAX_LOOPS = 1000
AGENT_TIMEOUT = 360
AGENT_ATTEMPTS = 3
MAX_FORMULA_LENGTH = 400
DEFAULT_LOOPS = 3
DEFAULT_PATIENCE = 2
LOCAL_MODEL = "gpt-oss-120b"
DEFAULT_MODEL = LOCAL_MODEL
LOCAL_PROMPT_RECENT = 24
LOCAL_CATALOG_LINES = 48
FACTOR_BATCH = 4
ALLOWED_MODELS = (LOCAL_MODEL,)
LLAMA_CHAT_URL = os.environ.get("QUANTLAB_LLAMA_URL", "http://127.0.0.1:8080/v1/chat/completions")
LLAMA_API_KEY = os.environ.get("QUANTLAB_LLAMA_API_KEY", "local")
LLAMA_READY_SECONDS = int(os.environ.get("QUANTLAB_LLAMA_READY_SECONDS", "180"))
_FIELDS = {"open", "high", "low", "close", "volume", *EXTRA_FIELDS}
_FUNCS = {"Ref", "Mean", "Std"}


def _llama_origin() -> str:
    marker = "/v1/"
    if marker in LLAMA_CHAT_URL:
        return LLAMA_CHAT_URL.split(marker, 1)[0]
    return LLAMA_CHAT_URL.rstrip("/")


def _local_model_down(message: str) -> bool:
    text = message.lower()
    return "本机模型失败" in message or "connection refused" in text or "errno 111" in text


def local_gpt_ready() -> bool:
    """True when llama-server is answering as GPT-OSS 120B."""
    request = urllib.request.Request(_llama_origin() + "/v1/models", method="GET")
    if LLAMA_API_KEY:
        request.add_header("Authorization", f"Bearer {LLAMA_API_KEY}")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    models = body.get("data") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return False
    return any(str(item.get("id") or "") == LOCAL_MODEL for item in models if isinstance(item, dict))


def ensure_local_gpt(timeout: float | None = None, cancel: threading.Event | None = None) -> None:
    """Start the user llama-server unit when GPT-OSS 120B is not answering."""
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已停止")
    if local_gpt_ready():
        return
    wait = LLAMA_READY_SECONDS if timeout is None else timeout
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "llama-server.service"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    detail = ""
    if active.stdout.strip() != "active":
        subprocess.run(
            ["systemctl", "--user", "reset-failed", "llama-server.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        started = subprocess.run(
            ["systemctl", "--user", "start", "llama-server.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        detail = (started.stderr or started.stdout or "").strip()
        if started.returncode != 0 and detail:
            raise RuntimeError(f"本机 GPT-OSS 120B 没有拉起：{detail[:300]}")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("已停止")
        if local_gpt_ready():
            return
        time.sleep(2)
    extra = f"：{detail[:200]}" if detail else ""
    raise RuntimeError(f"本机 GPT-OSS 120B 没有在 {int(wait)} 秒内起来{extra}")


def stop_local_gpt() -> None:
    """Stop the user llama-server unit so a mining run cannot keep calling it."""
    stopped = subprocess.run(
        ["systemctl", "--user", "stop", "llama-server.service"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    detail = (stopped.stderr or stopped.stdout or "").strip()
    if stopped.returncode != 0:
        raise RuntimeError(f"本机模型没有关掉：{detail[:300]}")


def _agent_binary() -> str:
    found = shutil.which("agent")
    if found:
        return found
    home = Path.home() / ".local" / "bin" / "agent"
    if home.is_file():
        return str(home)
    return "agent"


class CursorCliAgent:
    """One read-only Cursor agent call. It cannot edit the repository."""

    def __init__(self, binary: str | None = None, timeout: int = AGENT_TIMEOUT) -> None:
        self.binary = binary or _agent_binary()
        self.timeout = timeout

    def run(self, prompt: str, *, model: str, workspace: Path) -> str:
        workspace.mkdir(parents=True, exist_ok=True)
        command = [
            self.binary,
            "-p",
            "--mode",
            "ask",
            "--output-format",
            "text",
            "--model",
            model,
            "--trust",
            "--workspace",
            str(workspace),
            prompt,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Grok 超时（{self.timeout} 秒）") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "agent 失败").strip()
            raise RuntimeError(detail[:500])
        return completed.stdout


class LlamaServerAgent:
    """One completion from the local llama-server. Used for gpt-oss-120b."""

    def __init__(self, url: str | None = None, timeout: int = AGENT_TIMEOUT) -> None:
        self.url = url or LLAMA_CHAT_URL
        self.timeout = timeout

    def run(self, prompt: str, *, model: str, workspace: Path) -> str:
        del workspace
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 4096,
        }
        headers = {"Content-Type": "application/json"}
        if LLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as error:
            raise RuntimeError(f"本机模型超时（{self.timeout} 秒）") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"本机模型失败：{error}") from error
        message = body["choices"][0]["message"]
        content = str(message.get("content") or "").strip()
        if not content:
            content = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        if not content:
            raise RuntimeError("本机模型没有返回正文")
        return content


class RoutingAgent:
    """Grok stays on the Cursor agent. gpt-oss-120b goes to the local llama-server."""

    def __init__(self, cursor: CursorCliAgent | None = None, local: LlamaServerAgent | None = None) -> None:
        self.cursor = cursor or CursorCliAgent()
        self.local = local or LlamaServerAgent()

    def run(self, prompt: str, *, model: str, workspace: Path) -> str:
        runner = self.local if model == LOCAL_MODEL else self.cursor
        return runner.run(prompt, model=model, workspace=workspace)


def extract_proposal(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if "```" in cleaned:
        fenced = cleaned.split("```", 2)[1]
        cleaned = fenced[4:] if fenced.startswith("json") else fenced
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON 不是对象")
    name = str(payload.get("name") or "").strip()
    formula = str(payload.get("formula") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not name or not formula:
        raise ValueError("JSON 缺少 name 或 formula")
    return {"name": name, "formula": formula, "reason": reason}


def extract_factor_batch(text: str) -> dict[str, object]:
    """A hypothesis plus up to four factors. A single name/formula object still counts as one factor."""
    cleaned = text.strip()
    if "```" in cleaned:
        fenced = cleaned.split("```", 2)[1]
        cleaned = fenced[4:] if fenced.startswith("json") else fenced
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON 不是对象")
    hypothesis = str(payload.get("hypothesis") or "").strip()
    raw = payload.get("factors")
    factors: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            formula = str(item.get("formula") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if name and formula:
                factors.append({"name": name, "formula": formula, "reason": reason})
    if not factors and payload.get("name") and payload.get("formula"):
        factors.append(extract_proposal(text))
    if not factors:
        raise ValueError("JSON 缺少 factors")
    return {"hypothesis": hypothesis, "factors": factors[:FACTOR_BATCH]}


def _max_abs_daily_corr(
    factor: pd.Series,
    library: pd.DataFrame,
    dates: pd.Series,
    *,
    min_names: int = 8,
) -> float | None:
    """Largest mean cross-sectional correlation between one factor and the library."""
    if library is None or library.empty:
        return None
    values = np.column_stack(
        [
            pd.to_numeric(factor, errors="coerce").to_numpy(dtype=np.float64),
            library.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64),
        ]
    )
    codes, _ = pd.factorize(dates.to_numpy(), sort=False)
    order = np.argsort(codes, kind="mergesort")
    values = values[order]
    codes = codes[order]
    cuts = np.flatnonzero(np.diff(codes)) + 1
    bounds = np.r_[0, cuts, len(codes)]
    totals = np.zeros(values.shape[1] - 1, dtype=np.float64)
    counts = np.zeros(values.shape[1] - 1, dtype=np.float64)
    for start, end in zip(bounds[:-1], bounds[1:]):
        block = values[start:end]
        x = block[:, 0]
        ready = np.isfinite(x)
        if int(ready.sum()) < min_names:
            continue
        x = x[ready]
        y = block[ready, 1:]
        x_center = x - x.mean()
        x_energy = float(np.dot(x_center, x_center))
        if x_energy <= 0:
            continue
        finite = np.isfinite(y)
        dense = finite.all(axis=0)
        if dense.any():
            block_y = y[:, dense]
            center = block_y - block_y.mean(axis=0)
            numer = x_center @ center
            denom = np.sqrt((center**2).sum(axis=0) * x_energy)
            corr = np.divide(numer, denom, out=np.zeros(int(dense.sum())), where=denom > 0)
            totals[np.flatnonzero(dense)] += corr
            counts[np.flatnonzero(dense)] += 1
        for column in np.flatnonzero(~dense):
            mask = finite[:, column]
            if int(mask.sum()) < min_names:
                continue
            xs = x[mask]
            ys = y[mask, column]
            xs = xs - xs.mean()
            ys = ys - ys.mean()
            denom = np.sqrt(np.dot(xs, xs) * np.dot(ys, ys))
            if denom <= 0:
                continue
            totals[column] += float(np.dot(xs, ys) / denom)
            counts[column] += 1
    if not np.any(counts):
        return None
    means = np.divide(totals, counts, out=np.zeros_like(totals), where=counts > 0)
    return float(np.nanmax(np.abs(means)))


def _book_ir(book: dict[str, object]) -> float | None:
    if book.get("error"):
        return None
    value = book.get("test_information_ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:
        return None
    return number


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _rolling(series: pd.Series, window: int, kind: str) -> pd.Series:
    grouped = series.groupby(level="instrument", sort=False)
    if kind == "shift":
        return grouped.shift(window)
    rolled = grouped.rolling(window, min_periods=window)
    values = rolled.mean() if kind == "mean" else rolled.std()
    if isinstance(values.index, pd.MultiIndex) and values.index.nlevels == series.index.nlevels + 1:
        values = values.droplevel(0)
    return values.reindex(series.index)


def evaluate_formula(formula: str, panel: pd.DataFrame) -> pd.Series:
    import ast

    tree = ast.parse(formula, mode="eval")

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            walk(node.body)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
            walk(node.left)
            walk(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
            walk(node.operand)
            return
        if isinstance(node, ast.Name) and node.id in _FIELDS:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            if len(node.args) != 2 or node.keywords:
                raise ValueError("函数只接受序列和窗口两个参数")
            walk(node.args[0])
            window = node.args[1]
            if not isinstance(window, ast.Constant) or not isinstance(window.value, int):
                raise ValueError("窗口必须是整数")
            if window.value < 1 or window.value > 120:
                raise ValueError("窗口要在 1 到 120 之间")
            return
        raise ValueError("公式含有不允许的写法")

    walk(tree)

    def ev(node: ast.AST) -> pd.Series | float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Name):
            return panel[node.id]
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            value = ev(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = ev(node.left)
            right = ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        assert isinstance(node, ast.Call)
        series = ev(node.args[0])
        if not isinstance(series, pd.Series):
            raise ValueError("函数的第一个参数必须是序列")
        window = int(node.args[1].value)  # type: ignore[attr-defined]
        kind = {"Ref": "shift", "Mean": "mean", "Std": "std"}[node.func.id]  # type: ignore[attr-defined]
        return _rolling(series, window, kind)

    result = ev(tree)
    if not isinstance(result, pd.Series):
        raise ValueError("公式没有得到序列")
    return result.replace([np.inf, -np.inf], np.nan)


def mean_rank_ic(
    factor: pd.Series,
    future: pd.Series,
    dates: pd.Series,
    *,
    min_names: int = 5,
) -> float | None:
    frame = pd.DataFrame(
        {
            "factor": np.asarray(factor, dtype="float64"),
            "future": np.asarray(future, dtype="float64"),
            "date": np.asarray(dates),
        }
    ).dropna()
    if frame.empty:
        return None
    scores: list[float] = []
    for _, day in frame.groupby("date", sort=False):
        if len(day) < min_names:
            continue
        score = day["factor"].corr(day["future"], method="spearman")
        if pd.notna(score):
            scores.append(float(score))
    if not scores:
        return None
    return float(np.mean(scores))


def _formula_key(formula: str) -> str:
    return "".join(str(formula).split())


def _name_key(name: str) -> str:
    return str(name).strip().casefold()


def _formula_fields_text() -> str:
    return "、".join(["open", "high", "low", "close", "volume", *EXTRA_FIELDS])


def _successful_rows(history: list[dict[str, object]]) -> list[dict[str, object]]:
    """Formulas the model may see. Failures and formulas that missed their own test stay out."""
    rows: list[dict[str, object]] = []
    for item in history:
        if not isinstance(item, dict) or item.get("error") or item.get("accepted") is False:
            continue
        if not str(item.get("formula") or "").strip():
            continue
        rows.append(item)
    return rows


def _prompt(history: list[dict[str, object]], rejected: list[dict[str, str]] | None = None) -> str:
    history = _successful_rows(history)
    if not history:
        shown = "还没有上一轮。"
    else:
        lines = []
        for item in history:
            lines.append(
                f"- {item.get('name')}: {item.get('formula')}；"
                f"验证 IC {item.get('valid_ic')}；错误 {item.get('error') or '无'}"
            )
        shown = "\n".join(lines)
    rejected_text = ""
    if rejected:
        lines = [f"- {item.get('name')}: {item.get('formula')}" for item in rejected]
        rejected_text = "下面这些刚被拒绝，名字或公式已经有了，换一个：\n" + "\n".join(lines) + "\n"
    return (
        "你是量化因子研究员。只回复一个 JSON 对象，不要解释，不要用 markdown。\n"
        "字段：name（英文短名）、formula、reason（一句中文）。\n"
        f"formula 只能使用 {_formula_fields_text()}、加减乘除、括号，"
        "以及 Ref(序列, 整数)、Mean(序列, 整数)、Std(序列, 整数)。\n"
        "窗口是 1 到 120 的整数。名字和公式都不能和已经出现过的相同。\n"
        "下面只有验证段的 Spearman IC。样本末段的分数不会写在这里。\n"
        f"{shown}\n"
        f"{rejected_text}"
    )


def formula_family(formula: str) -> str:
    """Collapse Ref/Mean/Std windows so a later window tweak stays in the same family."""
    names = ("Ref(", "Mean(", "Std(")
    result: list[str] = []
    index = 0
    while index < len(formula):
        name = next((item for item in names if formula.startswith(item, index)), "")
        if not name:
            result.append(formula[index])
            index += 1
            continue
        open_at = index + len(name) - 1
        depth = 0
        close_at = open_at
        while close_at < len(formula):
            char = formula[close_at]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            close_at += 1
        if close_at >= len(formula) or formula[close_at] != ")":
            result.append(formula[index])
            index += 1
            continue
        inner = formula[open_at + 1 : close_at]
        inner = formula_family(inner)
        depth = 0
        comma = -1
        for offset, char in enumerate(inner):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                comma = offset
        window = inner[comma + 1 :].strip() if comma >= 0 else ""
        if window.isdigit():
            inner = inner[: comma + 1] + "W"
        result.append(f"{name}{inner})")
        index = close_at + 1
    return "".join(result)


def _pick_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:
        return None
    return number


def _screen_book(row: dict[str, object]) -> dict[str, object]:
    """The factor's own TopkDropout book. The Alpha158 joint book is a different information ratio."""
    own = row.get("own_test")
    if isinstance(own, dict):
        return own
    if row.get("rescore_status"):
        return {}
    strategy = row.get("strategy")
    return strategy if isinstance(strategy, dict) else {}


def screen_factor_picks(plans: list[dict[str, object]]) -> dict[str, object]:
    """Keep one factor per window-family when its own test annual beats that row's benchmark and IR is positive."""
    candidates: list[dict[str, object]] = []
    for plan in plans:
        rounds = plan.get("rounds") or []
        if not isinstance(rounds, list):
            continue
        for index, row in enumerate(rounds):
            if not isinstance(row, dict) or _mining_failure(row):
                continue
            strategy = _screen_book(row)
            if strategy.get("error"):
                continue
            annual = _pick_number(strategy.get("test_annual_return"))
            benchmark = _pick_number(strategy.get("test_benchmark_annual_return"))
            ir = _pick_number(strategy.get("test_information_ratio"))
            formula = str(row.get("formula") or "")
            if annual is None or benchmark is None or ir is None or not formula:
                continue
            if annual <= benchmark or ir <= 0:
                continue
            candidates.append(
                {
                    "plan_id": plan.get("plan_id"),
                    "model": row.get("model") or plan.get("model") or "",
                    "round": index + 1,
                    "max_loops": plan.get("max_loops"),
                    "created_at": str(row.get("created_at") or ""),
                    "name": row.get("name") or "",
                    "reason": row.get("reason") or "",
                    "formula": formula,
                    "family": "".join(formula_family(formula).split()),
                    "valid_ic": row.get("valid_ic"),
                    "test_ic": row.get("test_ic"),
                    "test_annual_return": annual,
                    "test_benchmark_annual_return": benchmark,
                    "test_max_drawdown": strategy.get("test_max_drawdown"),
                    "test_information_ratio": ir,
                    "detail_url": strategy.get("detail_url") or "",
                }
            )
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in candidates:
        grouped.setdefault(str(item["family"]), []).append(item)
    picks: list[dict[str, object]] = []
    for items in grouped.values():
        items.sort(key=lambda item: (-float(item["test_information_ratio"]), str(item["created_at"]), str(item["plan_id"]), int(item["round"])))
        chosen = dict(items[0])
        chosen["family_size"] = len(items)
        picks.append(chosen)
    picks.sort(key=lambda item: (-float(item["test_information_ratio"]), str(item["created_at"])))
    return {"candidate_count": len(candidates), "picks": picks}


def _admission_decision(error: object) -> bool:
    """A rescore decline is a status, not a broken formula."""
    text = str(error or "")
    return "没有更好" in text or "需要换方向" in text or "截面相关达到" in text


def _mining_failure(row: object) -> bool:
    return isinstance(row, dict) and bool(row.get("error")) and not _admission_decision(row.get("error"))


def _own_test_ready(row: dict[str, object]) -> bool:
    """The runs table treats a factor as scored only when its own test book has numbers."""
    book = row.get("own_test")
    if not isinstance(book, dict) or book.get("error"):
        return False
    for key in ("test_annual_return", "test_benchmark_annual_return", "test_information_ratio"):
        value = book.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if value != value:
            return False
    return True


def _status_error(row: object) -> bool:
    """A row the runs table labels 错误. Admission notes, blanks, and pending books stay."""
    if not isinstance(row, dict):
        return False
    if _mining_failure(row):
        return True
    if not str(row.get("name") or "").strip() and not str(row.get("formula") or "").strip():
        return False
    if _own_test_ready(row):
        return False
    strategy = row.get("strategy")
    return isinstance(strategy, dict) and bool(str(strategy.get("error") or "").strip())


def _interruption_count(plan: dict[str, object]) -> int:
    """Rows the runs table labels 中断, including a model stop reason with no stored event."""
    events = [item for item in plan.get("interruptions") or [] if isinstance(item, dict) and item.get("message")]
    if events:
        return len(events)
    if "模型" in str(plan.get("stop_reason") or ""):
        return 1
    return 0


def _counted_factor(row: object) -> bool:
    """A kept factor fills the plan quota. A formula that missed its own test does not."""
    return (
        isinstance(row, dict)
        and not row.get("error")
        and row.get("accepted") is not False
        and bool(str(row.get("name") or "").strip())
        and bool(str(row.get("formula") or "").strip())
    )


def _mined_factor_count(rounds: list[object]) -> int:
    """Factors that were actually saved. Failed calls and rejected formulas do not count."""
    return sum(1 for row in rounds if _counted_factor(row))


def _valid_ir(row: dict[str, object]) -> float | None:
    value = row.get("valid_information_ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:
        return None
    return number


def _family_key(formula: str) -> str:
    return "".join(formula_family(formula).split())


def _family_index(rows: list[object]) -> tuple[set[str], dict[str, float]]:
    """Counted formulas occupy a family. Only a numeric validation IR can be beaten."""
    seen: set[str] = set()
    best: dict[str, float] = {}
    for row in rows:
        if not _counted_factor(row):
            continue
        assert isinstance(row, dict)
        family = _family_key(str(row.get("formula") or ""))
        seen.add(family)
        ir = _valid_ir(row)
        if ir is None:
            continue
        current = best.get(family)
        if current is None or ir > current:
            best[family] = ir
    return seen, best


def _family_allows(family: str, ir: float, seen: set[str], best: dict[str, float]) -> bool:
    if family not in seen:
        return True
    incumbent = best.get(family)
    if incumbent is None:
        return False
    return ir > incumbent


def _validation_passes(strategy: dict[str, object]) -> bool:
    if strategy.get("error"):
        return False
    annual = strategy.get("test_annual_return")
    benchmark = strategy.get("test_benchmark_annual_return")
    ir = strategy.get("test_information_ratio")
    numbers = (annual, benchmark, ir)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numbers):
        return False
    annual_f, benchmark_f, ir_f = (float(annual), float(benchmark), float(ir))  # type: ignore[arg-type]
    if annual_f != annual_f or benchmark_f != benchmark_f or ir_f != ir_f:
        return False
    return annual_f > benchmark_f and ir_f > 0


def _remember_family(family: str, ir: float, seen: set[str], best: dict[str, float]) -> None:
    seen.add(family)
    current = best.get(family)
    if current is None or ir > current:
        best[family] = ir


def _catalog_lines(history: list[dict[str, object]]) -> list[str]:
    """One line per formula already mined. Whitespace differences stay one line."""
    lines: list[str] = []
    seen: set[str] = set()
    for item in history:
        formula = str(item.get("formula") or "").strip()
        key = _formula_key(formula)
        if not key or key in seen:
            continue
        seen.add(key)
        name = str(item.get("name") or "").strip()
        lines.append(f"{name} {formula}" if name else formula)
    return lines


def _book_numbers(result: dict[str, object]) -> dict[str, object]:
    """Keep the factor's own TopkDropout figures. Curves stay out of the mining row."""
    if result.get("error"):
        return {"error": str(result["error"])}
    return {
        "annual_return": result.get("test_annual_return"),
        "benchmark_annual_return": result.get("test_benchmark_annual_return"),
        "max_drawdown": result.get("test_max_drawdown"),
        "information_ratio": result.get("test_information_ratio"),
        "test_annual_return": result.get("test_annual_return"),
        "test_benchmark_annual_return": result.get("test_benchmark_annual_return"),
        "test_max_drawdown": result.get("test_max_drawdown"),
        "test_information_ratio": result.get("test_information_ratio"),
    }


def _record_own_books(row: dict[str, object], valid_book: dict[str, object], test_book: dict[str, object]) -> None:
    """Archive this formula's own validation book and test book on the mining row."""
    row["own_valid"] = _book_numbers(valid_book)
    row["own_test"] = _book_numbers(test_book)
    row.pop("baseline_valid_information_ratio", None)
    valid_error = str(valid_book.get("error") or "")
    test_error = str(test_book.get("error") or "")
    if not valid_error:
        row["valid_annual_return"] = valid_book.get("test_annual_return")
        row["valid_information_ratio"] = valid_book.get("test_information_ratio")
    else:
        row["valid_annual_return"] = None
        row["valid_information_ratio"] = None
    if valid_error or test_error:
        row["error"] = valid_error or test_error
        row["accepted"] = False
        return
    row["error"] = ""
    row["accepted"] = _validation_passes(test_book)


def _feedback_text(feedback: object) -> str:
    if not isinstance(feedback, dict) or not feedback:
        return "还没有上一轮回测。"
    lines: list[str] = []
    if feedback.get("hypothesis"):
        lines.append(f"假设：{feedback.get('hypothesis')}")
    for item in feedback.get("scored") or []:
        if not isinstance(item, dict):
            continue
        mark = "测试段过线" if item.get("accepted") else "测试段未过线"
        lines.append(
            f"{item.get('name')} 验证信息比率 {item.get('valid_information_ratio')}，"
            f"测试信息比率 {item.get('test_information_ratio')}，{mark}。"
        )
    return "\n".join(lines) if lines else "还没有上一轮回测。"


def _prompt_for_model(
    history: list[dict[str, object]],
    rejected: list[dict[str, str]] | None,
    model: str,
    feedback: object = None,
) -> str:
    """Ask for one hypothesis and a short batch. Failures and test-segment scores stay out."""
    if model != LOCAL_MODEL:
        return _prompt(history, rejected)
    succeeded = _successful_rows(history)
    ranked = sorted(
        enumerate(succeeded),
        key=lambda pair: (_valid_ir(pair[1]) if _valid_ir(pair[1]) is not None else float("-inf"), pair[0]),
        reverse=True,
    )
    catalog = _catalog_lines([item for _, item in ranked[:LOCAL_CATALOG_LINES]])
    recent = succeeded[-LOCAL_PROMPT_RECENT:]
    recent_lines = [
        f"- {item.get('name')}: {item.get('formula')}；验证 IC {item.get('valid_ic')}"
        + (f"；验证段信息比率 {item.get('valid_information_ratio')}" if _valid_ir(item) is not None else "")
        for item in recent
    ]
    rejected_text = ""
    if rejected:
        lines = [f"- {item.get('name')}: {item.get('formula')}" for item in rejected]
        rejected_text = "下面这些名字或公式已经有了，换一个：\n" + "\n".join(lines) + "\n"
    recent_text = "\n".join(recent_lines) if recent_lines else "还没有留下的新因子。"
    return (
        "你是量化因子研究员。只回复一个 JSON 对象，不要解释，不要用 markdown。\n"
        f"字段：hypothesis（一句中文的经济假设）、factors（1 到 {FACTOR_BATCH} 个对象，每个含 name、formula、reason）。\n"
        f"formula 只能使用 {_formula_fields_text()}、加减乘除、括号，"
        "以及 Ref(序列, 整数)、Mean(序列, 整数)、Std(序列, 整数)。\n"
        "窗口是 1 到 120 的整数。\n"
        "每个新公式单独做验证和测试，不和 Alpha158 合成一个模型。\n"
        "验证段和测试段各自训练该因子的 LightGBM。持仓是当天分数最高的 50 只，掉出这 50 只当天卖出。\n"
        "测试年化高于该因子自己的基准、并且测试信息比率为正，这一条才留下。\n"
        "上一轮回测：\n"
        + _feedback_text(feedback)
        + "\n已经留下的公式，新的 name 和 formula 不能与其中任何一条相同，去掉空格后相同也算重复。\n"
        + "\n".join(catalog)
        + "\n最近留下的公式：\n"
        + recent_text
        + "\n"
        + rejected_text
    )


_RESCORE: dict[str, object] = {}


def _init_rescore_worker(threads: int) -> None:
    """Load the score panel once in each rescore process."""
    service = QlibFactorLoopService(Settings())
    panel = service._score_panel()
    close = panel["close"]
    future = close.groupby(level="instrument", sort=False).shift(-1) / close - 1
    _RESCORE["panel"] = panel
    _RESCORE["close"] = close
    _RESCORE["future"] = future
    _RESCORE["dates"] = pd.Series(panel.index.get_level_values("date"), index=panel.index)
    _RESCORE["instruments"] = pd.Series(panel.index.get_level_values("instrument"), index=panel.index)
    _RESCORE["threads"] = threads


def _rescore_saved_job(job: dict[str, object]) -> dict[str, object]:
    """Refit one saved formula and rebuild its validation book and test book."""
    try:
        factor = evaluate_formula(str(job["formula"]), _RESCORE["panel"])  # type: ignore[arg-type]
        common = {
            "prices": _RESCORE["close"],
            "train_end": str(job["train_end"]),
            "valid_end": str(job["valid_end"]),
            "num_threads": int(_RESCORE["threads"]),  # type: ignore[arg-type]
        }
        valid_book = run_factor_strategy(
            factor,
            _RESCORE["future"],  # type: ignore[arg-type]
            _RESCORE["dates"],  # type: ignore[arg-type]
            _RESCORE["instruments"],  # type: ignore[arg-type]
            book="valid",
            **common,  # type: ignore[arg-type]
        )
        test_book = run_factor_strategy(
            factor,
            _RESCORE["future"],  # type: ignore[arg-type]
            _RESCORE["dates"],  # type: ignore[arg-type]
            _RESCORE["instruments"],  # type: ignore[arg-type]
            book="test",
            **common,  # type: ignore[arg-type]
        )
        for key in ("equity_curve", "benchmark_curve", "trades"):
            valid_book.pop(key, None)
            if not job.get("archive"):
                test_book.pop(key, None)
        return {"plan_id": job["plan_id"], "index": job["index"], "valid": valid_book, "test": test_book}
    except Exception as error:  # noqa: BLE001 — one formula must not stop the rescore
        return {"plan_id": job["plan_id"], "index": job["index"], "error": str(error)}


class QlibFactorLoopService:
    def __init__(self, settings: Settings, agent: CursorCliAgent | LlamaServerAgent | RoutingAgent | None = None) -> None:
        self.settings = settings
        self.agent = agent or RoutingAgent()
        self._lock = threading.Lock()
        self._convert_lock = threading.Lock()
        self._runners: dict[str, threading.Thread] = {}
        self._generation: dict[str, int] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._archive_inflight: set[str] = set()
        self._panel_cache: tuple[float, pd.DataFrame] | None = None

    @property
    def qlib_root(self) -> Path:
        return self.settings.data_root / "qlib"

    @property
    def qlib_dir(self) -> Path:
        return self.qlib_root / "cn_data"

    @property
    def plans_dir(self) -> Path:
        return self.settings.runtime_root / "qlib" / "plans"

    def status(self) -> dict[str, object]:
        with self._lock:
            self._reap_orphans()
        source = self.settings.data_root / "canonical.parquet"
        manifest_path = self.qlib_root / "manifest.json"
        manifest: dict[str, object] = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plans = [self._public(self._read_plan(path)) for path in self._plan_paths()]
        return {
            "source_path": str(source),
            "source_ready": source.is_file(),
            "conversion": manifest,
            "plans": plans,
        }

    def picks(self) -> dict[str, object]:
        plans = [self._read_plan(path) for path in self._plan_paths()]
        return screen_factor_picks(plans)

    def start_convert(self) -> dict[str, object]:
        source = self.settings.data_root / "canonical.parquet"
        if not source.is_file():
            raise ValueError("找不到 canonical.parquet")
        with self._convert_lock:
            current = self.status()["conversion"]
            if isinstance(current, dict) and current.get("status") == "running":
                raise ValueError("转换已在进行")
            self._write_manifest({"status": "running"})
        thread = threading.Thread(target=self._convert, daemon=True)
        thread.start()
        return self.status()

    def _convert(self) -> None:
        try:
            frame = load_source_frame(self.settings.data_root / "canonical.parquet")
            membership = self._load_membership()
            summary = convert_frame(frame, self.qlib_dir, membership=membership, raw_root=self.settings.raw_root)
            summary["status"] = "completed"
            self._write_manifest(summary)
        except Exception as error:  # noqa: BLE001 — surface the job error on the page
            self._write_manifest({"status": "failed", "error": str(error)})

    def save_plan(self, body: dict[str, object]) -> dict[str, object]:
        max_loops = int(body.get("max_loops") or DEFAULT_LOOPS)
        raw_patience = body.get("patience") if "patience" in body else None
        patience = max_loops if raw_patience in (None, "") else int(raw_patience)
        model = str(body.get("model") or DEFAULT_MODEL)
        train_end = str(body.get("train_end") or "2022-12-31")
        valid_end = str(body.get("valid_end") or "2024-12-31")
        if max_loops < 1 or max_loops > MAX_LOOPS:
            raise ValueError(f"挖因子数量要在 1 到 {MAX_LOOPS} 之间")
        if patience < 1 or patience > max_loops:
            raise ValueError("提前停止的轮数要在 1 和循环次数之间")
        if model not in ALLOWED_MODELS:
            raise ValueError("模型不在允许的列表里")
        if to_iso_date(train_end) != train_end or to_iso_date(valid_end) != valid_end:
            raise ValueError("日期要用 YYYY-MM-DD")
        if train_end >= valid_end:
            raise ValueError("训练结束日要早于验证结束日")
        plan = {
            "plan_id": "qlib-" + uuid.uuid4().hex[:12],
            "created_at": _now_text(),
            "status": "configured",
            "max_loops": max_loops,
            "patience": patience,
            "model": model,
            "train_end": train_end,
            "valid_end": valid_end,
            "agent_calls": 0,
            "rounds": [],
            "stop_reason": "",
            "stop_requested": False,
        }
        self._write_plan(plan)
        return self._public(plan)

    def start(self, plan_id: str) -> dict[str, object]:
        with self._lock:
            self._reap_orphans()
            for path in self._plan_paths():
                other = self._read_plan(path)
                other_id = str(other.get("plan_id") or "")
                if other.get("status") == "running" and other_id != plan_id and self._live(other_id):
                    raise ValueError("已有循环在跑")
            if self._live(plan_id):
                raise ValueError("循环已在跑")
            plan = self._load(plan_id)
            if str(plan.get("model") or "") not in ALLOWED_MODELS:
                raise ValueError("只能使用本机模型 GPT-OSS 120B")
            manifest_path = self.qlib_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            if manifest.get("status") != "completed":
                raise ValueError("先转换数据，再开始循环")
            if _mined_factor_count(plan.get("rounds") or []) >= int(plan["max_loops"]):
                raise ValueError("这个任务已经挖满")
            generation = self._generation.get(plan_id, 0) + 1
            self._generation[plan_id] = generation
            cancel = threading.Event()
            self._cancels[plan_id] = cancel
            plan["status"] = "running"
            plan["stop_requested"] = False
            plan["stop_reason"] = ""
            self._write_plan(plan, force=True)
            thread = threading.Thread(target=self._run_safe, args=(plan_id, generation, cancel), daemon=True)
            self._runners[plan_id] = thread
            thread.start()
        return self._public(self._load(plan_id))

    def stop(self, plan_id: str) -> dict[str, object]:
        with self._lock:
            plan = self._load(plan_id)
            if plan.get("status") in {"stopped", "completed"}:
                self._reap_orphans()
                return self._public(self._load(plan_id))
            if plan.get("status") != "running":
                raise ValueError("这个循环没有在跑")
            cancel = self._cancels.get(plan_id)
            if cancel is not None:
                cancel.set()
            plan["stop_requested"] = True
            plan["status"] = "stopped"
            plan["stop_reason"] = "已停止"
            self._write_plan(plan, force=True)
            model = str(plan.get("model") or "")
            self._reap_orphans()
        if model == LOCAL_MODEL:
            stop_local_gpt()
        return self._public(self._load(plan_id))

    def purge_errors(self, plan_id: str) -> dict[str, object]:
        """Drop rows labeled 错误 or 中断. 过线, 未过线, and 待计算 stay."""
        with self._lock:
            if self._live(plan_id):
                raise ValueError("循环还在跑，先停止再删除错误和中断")
            plan = self._load(plan_id)
            if plan.get("status") == "running":
                raise ValueError("循环还在跑，先停止再删除错误和中断")
            rounds = plan.get("rounds") or []
            if not isinstance(rounds, list):
                rounds = []
            kept = [row for row in rounds if not _status_error(row)]
            removed = len(rounds) - len(kept) + _interruption_count(plan)
            plan["rounds"] = kept
            plan["interruptions"] = []
            if "模型" in str(plan.get("stop_reason") or ""):
                plan["stop_reason"] = "已停止" if plan.get("status") == "stopped" else ""
            self._write_plan(plan, force=True)
        public = self._public(self._load(plan_id))
        public["removed"] = removed
        public["kept"] = len(kept)
        return public

    def delete_plan(self, plan_id: str) -> dict[str, object]:
        """Remove one stopped plan file. A live run and every other plan stay."""
        with self._lock:
            if self._live(plan_id):
                raise ValueError("循环还在跑，先停止再删除计划")
            plan = self._load(plan_id)
            if plan.get("status") == "running":
                raise ValueError("循环还在跑，先停止再删除计划")
            removed = len(plan.get("rounds") or [])
            path = self._plan_path(plan_id)
            path.unlink()
            temporary = path.with_suffix(".json.next")
            if temporary.is_file():
                temporary.unlink()
            self._runners.pop(plan_id, None)
            self._cancels.pop(plan_id, None)
            self._generation.pop(plan_id, None)
        return {"plan_id": plan_id, "deleted": True, "rounds": removed}

    def get(self, plan_id: str) -> dict[str, object]:
        return self._public(self._load(plan_id))

    def _archive_run_exists(self, database: object, run_id: str) -> bool:
        with database.connect() as connection:  # type: ignore[attr-defined]
            row = connection.execute("SELECT 1 FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        return row is not None

    def _write_archive_files(self, folder: Path, metrics: dict[str, object], curve: list[object], trades: list[object]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
        (folder / "equity_curve.json").write_text(json.dumps(curve, ensure_ascii=False), encoding="utf-8")
        (folder / "trades.json").write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")

    def _refresh_archive_files(self, database: object, run_id: str, folder: Path) -> None:
        import hashlib

        with database.transaction() as connection:  # type: ignore[attr-defined]
            for role, name in (("metrics", "metrics.json"), ("equity_curve", "equity_curve.json"), ("trades", "trades.json")):
                path = folder / name
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                connection.execute(
                    "UPDATE artifacts SET content_hash=?, size_bytes=? WHERE run_id=? AND artifact_role=?",
                    (f"sha256:{digest}", path.stat().st_size, run_id, role),
                )

    def _store_strategy(self, plan: dict[str, object], round_row: dict[str, object], strategy: dict[str, object]) -> None:
        stored = dict(strategy)
        if not stored.get("error"):
            try:
                published = self._publish_archive(plan, round_row, stored)
                stored["archive_run_id"] = published["run_id"]
                stored["detail_url"] = f"/backtests/runs/{published['run_id']}"
            except Exception as error:  # noqa: BLE001 — keep the factor score if the archive write fails
                stored["archive_error"] = str(error)
        stored.pop("equity_curve", None)
        stored.pop("benchmark_curve", None)
        stored.pop("trades", None)
        round_row["strategy"] = stored

    def _publish_own_test(
        self,
        plan: dict[str, object],
        round_row: dict[str, object],
        valid_book: dict[str, object],
        test_book: dict[str, object],
    ) -> None:
        """Write the factor's own test book into the result archive. Validation numbers ride along."""
        if test_book.get("error"):
            return
        archived = dict(test_book)
        archived["book"] = "test"
        if valid_book.get("error"):
            archived["valid_annual_return"] = None
            archived["valid_information_ratio"] = None
        else:
            archived["valid_annual_return"] = valid_book.get("test_annual_return")
            archived["valid_information_ratio"] = valid_book.get("test_information_ratio")
        self._store_strategy(plan, round_row, archived)
        strategy = round_row.get("strategy")
        own_test = round_row.get("own_test")
        if isinstance(strategy, dict) and isinstance(own_test, dict):
            if strategy.get("detail_url"):
                own_test["detail_url"] = strategy["detail_url"]
            if strategy.get("archive_run_id"):
                own_test["archive_run_id"] = strategy["archive_run_id"]

    def _publish_archive(
        self,
        plan: dict[str, object],
        round_row: dict[str, object],
        strategy: dict[str, object],
    ) -> dict[str, str]:
        from datetime import datetime, timezone

        from quantlab.repositories.artifacts import ArtifactRepository
        from quantlab.repositories.database import Database
        from quantlab.services.backtest_job import _attach_extra_performance_metrics
        from quantlab.services.backtest_summary import refresh_backtest_summary
        from quantlab.services.run_identity import RunIdentity

        database = Database(self.settings.database_path)
        database.initialize()
        curve = list(strategy.get("equity_curve") or [])
        bench = list(strategy.get("benchmark_curve") or [])
        if len(curve) < 2:
            raise ValueError("没有可写入档案的权益曲线")
        name = str(round_row.get("name") or "qlib")
        formula = str(round_row.get("formula") or "")
        total_return = strategy.get("test_return")
        benchmark_return = strategy.get("test_benchmark_return")
        excess = None
        if isinstance(total_return, float) and isinstance(benchmark_return, float):
            excess = total_return - benchmark_return
        metrics: dict[str, object] = {
            "return": total_return,
            "annual_return": strategy.get("test_annual_return"),
            "sharpe": strategy.get("test_sharpe"),
            "max_drawdown": strategy.get("test_max_drawdown"),
            "benchmark_return": benchmark_return,
            "excess_return": excess,
            "turnover": strategy.get("test_turnover"),
            "capital_usage": strategy.get("test_capital_usage"),
            "rank_ic": round_row.get("test_ic"),
            "information_ratio": strategy.get("test_information_ratio"),
            "valid_annual_return": strategy.get("valid_annual_return"),
            "valid_information_ratio": strategy.get("valid_information_ratio"),
            "win_rate": strategy.get("win_rate"),
            "ndcg_at_10": None,
            "trade_count": int(strategy.get("trade_count") or 0),
            "equity_curve": curve,
            "benchmark_curve": bench,
        }
        metrics = _attach_extra_performance_metrics(metrics, curve)
        segment = "验证段" if strategy.get("book") == "valid" else "测试段"
        config = {
            "name": f"Qlib {name} · TopkDropout",
            "note": (
                f"{formula}。LightGBM 打分，持有当天分数最高的 {strategy.get('topk')} 只，掉出这个名单当天卖出。"
                "分数相同的已持有股票优先留下。"
                f"{segment}按次日收盘到收盘。年化收益和信息比率都写在这份档案里。"
                "成交按 100 万本金，价格是当日收盘，买入 5bp、卖出 15bp。"
                "盈亏是持有期间的权重收益减去这两笔费用，全部成交的盈亏加总等于累计收益。期末仍持有是浮动盈亏。"
            ),
            "kind": "qlib_topk_dropout",
            "benchmark": "有分数股票等权",
            "test": {"date_from": curve[0]["date"], "date_to": curve[-1]["date"]},
            "top_n": strategy.get("topk"),
            "rebalance_every": 1,
            "model": {"name": "LightGBM", "kind": "qlib_topk_dropout"},
            "factor_versions": [{"field": name, "factor": formula}],
            "qlib_plan_id": plan.get("plan_id"),
        }
        trades = list(strategy.get("trades") or [])
        existing = str(strategy.get("archive_run_id") or "")
        if existing and self._archive_run_exists(database, existing):
            folder = self.settings.runtime_root / "results" / existing
            self._write_archive_files(folder, metrics, curve, trades)
            self._refresh_archive_files(database, existing, folder)
            payload = json.dumps(metrics, ensure_ascii=False)
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE backtest_runs SET config_json=?, metrics_json=? WHERE run_id=?",
                    (json.dumps(config, ensure_ascii=False), payload, existing),
                )
                refresh_backtest_summary(connection, existing)
            return {"run_id": existing}
        run_id = RunIdentity(database, self.settings.runtime_root).next_id()
        database.register_run(run_id, "backtest")
        folder = self.settings.runtime_root / "results" / run_id
        self._write_archive_files(folder, metrics, curve, trades)
        artifacts = ArtifactRepository(self.settings, database)
        artifacts.register(run_id=run_id, path=folder / "metrics.json", display_name="回测指标", artifact_role="metrics")
        artifacts.register(run_id=run_id, path=folder / "equity_curve.json", display_name="权益曲线", artifact_role="equity_curve")
        artifacts.register(run_id=run_id, path=folder / "trades.json", display_name="成交明细", artifact_role="trades")
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = json.dumps(metrics, ensure_ascii=False)
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO backtest_runs(run_id, status, strategy_entity_id, strategy_version_id, "
                "dataset_id, dataset_version_id, config_json, metrics_json) VALUES (?, 'queued', NULL, NULL, NULL, NULL, ?, ?)",
                (run_id, json.dumps(config, ensure_ascii=False), payload),
            )
            connection.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
            connection.execute(
                "UPDATE backtest_runs SET status='completed', metrics_json=? WHERE run_id=?",
                (payload, run_id),
            )
            connection.execute("UPDATE run_registry SET finished_at=? WHERE run_id=?", (finished, run_id))
            refresh_backtest_summary(connection, run_id)
        return {"run_id": run_id}

    def publish_own_archive(self, plan_id: str, round_index: int) -> dict[str, object]:
        """Write this round's own test book into a new result archive. The old joint archive stays."""
        key = f"{plan_id}:{round_index}"
        with self._lock:
            if self._live(plan_id):
                raise ValueError("循环还在跑，先停止再写入回测")
            plan = self._load(plan_id)
            if plan.get("status") == "running":
                raise ValueError("循环还在跑，先停止再写入回测")
            rounds = plan.get("rounds") or []
            if not isinstance(rounds, list) or round_index < 0 or round_index >= len(rounds) or not isinstance(rounds[round_index], dict):
                raise ValueError("没有这一轮")
            row = rounds[round_index]
            own = row.get("own_test") if isinstance(row.get("own_test"), dict) else {}
            ready = str(own.get("detail_url") or "")
            archive_id = str(own.get("archive_run_id") or "")
            if ready and archive_id:
                from quantlab.repositories.database import Database

                if self._archive_run_exists(Database(self.settings.database_path), archive_id):
                    return {"plan_id": plan_id, "round": round_index + 1, "detail_url": ready}
            formula = str(row.get("formula") or "").strip()
            if not formula:
                raise ValueError("这一轮没有公式")
            train_end = str(plan["train_end"])
            valid_end = str(plan["valid_end"])
            if key in self._archive_inflight:
                raise ValueError("这一轮的回测正在写入")
            self._archive_inflight.add(key)
        try:
            panel = self._score_panel()
            close = panel["close"]
            future = close.groupby(level="instrument", sort=False).shift(-1) / close - 1
            dates = pd.Series(panel.index.get_level_values("date"), index=panel.index)
            instruments = pd.Series(panel.index.get_level_values("instrument"), index=panel.index)
            factor = evaluate_formula(formula, panel)
            valid_book = run_factor_strategy(
                factor,
                future,
                dates,
                instruments,
                prices=close,
                train_end=train_end,
                valid_end=valid_end,
                book="valid",
                num_threads=1,
            )
            test_book = run_factor_strategy(
                factor,
                future,
                dates,
                instruments,
                prices=close,
                train_end=train_end,
                valid_end=valid_end,
                book="test",
                num_threads=1,
            )
            if test_book.get("error"):
                raise ValueError(str(test_book["error"]))
            with self._lock:
                if self._live(plan_id):
                    raise ValueError("循环还在跑，先停止再写入回测")
                plan = self._load(plan_id)
                if plan.get("status") == "running":
                    raise ValueError("循环还在跑，先停止再写入回测")
                current = plan["rounds"][round_index]
                if not isinstance(current, dict):
                    raise ValueError("没有这一轮")
                own = current.get("own_test") if isinstance(current.get("own_test"), dict) else {}
                if own.get("detail_url") and own.get("archive_run_id"):
                    return {"plan_id": plan_id, "round": round_index + 1, "detail_url": str(own["detail_url"])}
                _record_own_books(current, valid_book, test_book)
                self._publish_own_test(plan, current, valid_book, test_book)
                self._write_plan(plan, force=True)
                stored = current.get("own_test") if isinstance(current.get("own_test"), dict) else {}
                detail_url = str(stored.get("detail_url") or "")
                if not detail_url:
                    strategy = current.get("strategy") if isinstance(current.get("strategy"), dict) else {}
                    raise ValueError(str(strategy.get("archive_error") or "没有写入结果档案"))
            return {"plan_id": plan_id, "round": round_index + 1, "detail_url": detail_url}
        finally:
            with self._lock:
                self._archive_inflight.discard(key)

    def fill_strategy(self, plan_id: str) -> dict[str, object]:
        """Score an existing formula into TopkDropout. Does not call Grok."""
        plan = self._load(plan_id)
        panel = self._score_panel()
        close = panel["close"]
        future = close.groupby(level="instrument", sort=False).shift(-1) / close - 1
        dates = pd.Series(panel.index.get_level_values("date"), index=panel.index)
        instruments = pd.Series(panel.index.get_level_values("instrument"), index=panel.index)
        for row in plan["rounds"]:
            if row.get("error") or not row.get("formula"):
                continue
            factor = evaluate_formula(str(row["formula"]), panel)
            previous = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
            train_end = str(plan["train_end"])
            valid_end = str(plan["valid_end"])
            valid_book = run_factor_strategy(
                factor,
                future,
                dates,
                instruments,
                prices=close,
                train_end=train_end,
                valid_end=valid_end,
                book="valid",
            )
            test_book = run_factor_strategy(
                factor,
                future,
                dates,
                instruments,
                prices=close,
                train_end=train_end,
                valid_end=valid_end,
                book="test",
            )
            valid_mask = (dates > plan["train_end"]) & (dates <= plan["valid_end"])
            test_mask = dates > plan["valid_end"]
            row["valid_ic"] = mean_rank_ic(factor[valid_mask], future[valid_mask], dates[valid_mask])
            row["test_ic"] = mean_rank_ic(factor[test_mask], future[test_mask], dates[test_mask])
            _record_own_books(row, valid_book, test_book)
            if previous.get("archive_run_id") and not test_book.get("error"):
                test_book = dict(test_book)
                test_book["archive_run_id"] = previous["archive_run_id"]
            self._publish_own_test(plan, row, valid_book, test_book)
        self._write_plan(plan)
        return self._public(plan)

    def rescore_saved_books(self, *, workers: int = 8, threads: int = 1) -> dict[str, object]:
        """Rebuild every saved formula's validation book and test book. Does not ask for new formulas."""
        done_path = self.settings.runtime_root / "qlib" / "rescore_top50.done"
        done_path.parent.mkdir(parents=True, exist_ok=True)
        done = set(done_path.read_text(encoding="utf-8").split()) if done_path.is_file() else set()
        plans: dict[str, dict[str, object]] = {}
        jobs: list[dict[str, object]] = []
        for path in self._plan_paths():
            plan = self._read_plan(path)
            plan_id = str(plan["plan_id"])
            plans[plan_id] = plan
            for index, row in enumerate(plan.get("rounds") or []):
                if not isinstance(row, dict):
                    continue
                formula = str(row.get("formula") or "").strip()
                if not formula or _mining_failure(row):
                    continue
                key = f"{plan_id}:{index}"
                if key in done:
                    continue
                own = row.get("own_test") if isinstance(row.get("own_test"), dict) else {}
                strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
                archive = str(own.get("archive_run_id") or strategy.get("archive_run_id") or "")
                jobs.append(
                    {
                        "plan_id": plan_id,
                        "index": index,
                        "formula": formula,
                        "train_end": str(plan["train_end"]),
                        "valid_end": str(plan["valid_end"]),
                        "archive": bool(archive),
                    }
                )
        finished = 0
        failed: list[dict[str, object]] = []
        if jobs:
            context = multiprocessing.get_context("spawn")
            with context.Pool(workers, initializer=_init_rescore_worker, initargs=(threads,)) as pool:
                for result in pool.imap_unordered(_rescore_saved_job, jobs, chunksize=1):
                    plan_id = str(result["plan_id"])
                    index = int(result["index"])
                    plan = plans[plan_id]
                    row = plan["rounds"][index]
                    assert isinstance(row, dict)
                    key = f"{plan_id}:{index}"
                    if result.get("error") and "valid" not in result:
                        failed.append({"plan_id": plan_id, "index": index, "error": result["error"]})
                        print(f"rescore fail {key} {result['error']}", flush=True)
                        continue
                    self._apply_rescored_books(plan, row, result["valid"], result["test"])
                    self._write_plan(plan, force=True)
                    with done_path.open("a", encoding="utf-8") as handle:
                        handle.write(key + "\n")
                    finished += 1
                    if finished % 25 == 0 or finished == len(jobs):
                        print(f"rescore {finished}/{len(jobs)}", flush=True)
        return {"jobs": len(jobs), "finished": finished, "failed": failed}

    def _apply_rescored_books(
        self,
        plan: dict[str, object],
        row: dict[str, object],
        valid_book: dict[str, object],
        test_book: dict[str, object],
    ) -> None:
        """Write the new books onto the mining row and refresh an archive that already exists."""
        own = row.get("own_test") if isinstance(row.get("own_test"), dict) else {}
        strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
        detail_url = str(own.get("detail_url") or strategy.get("detail_url") or "")
        archive_id = str(own.get("archive_run_id") or strategy.get("archive_run_id") or "")
        _record_own_books(row, valid_book, test_book)
        stored = row.get("own_test")
        if isinstance(stored, dict):
            if detail_url:
                stored["detail_url"] = detail_url
            if archive_id:
                stored["archive_run_id"] = archive_id
        row["holding_rule"] = "top50"
        if not archive_id or test_book.get("error"):
            return
        refreshed = dict(test_book)
        refreshed["archive_run_id"] = archive_id
        self._publish_own_test(plan, row, valid_book, refreshed)

    def _known_factors(self, plan_id: str) -> tuple[set[str], set[str], list[dict[str, object]]]:
        formulas: set[str] = set()
        names: set[str] = set()
        prior: list[dict[str, object]] = []
        for path in self._plan_paths():
            stored = self._read_plan(path)
            same_plan = stored.get("plan_id") == plan_id
            for row in stored.get("rounds") or []:
                formula = _formula_key(str(row.get("formula") or ""))
                name = _name_key(str(row.get("name") or ""))
                if formula:
                    formulas.add(formula)
                if name and not row.get("error"):
                    names.add(name)
                if same_plan or row.get("error") or row.get("accepted") is False or not formula:
                    continue
                prior.append(
                    {
                        "name": row.get("name"),
                        "formula": row.get("formula"),
                        "valid_ic": row.get("valid_ic"),
                        "valid_information_ratio": row.get("valid_information_ratio"),
                        "error": "",
                    }
                )
        return formulas, names, prior

    def execute(
        self,
        plan_id: str,
        agent: CursorCliAgent | LlamaServerAgent | RoutingAgent | None = None,
        *,
        generation: int | None = None,
        cancel: threading.Event | None = None,
    ) -> dict[str, object]:
        """Run the capped loop on the calling thread. Used by the background start and tests."""
        runner = agent or self.agent
        plan = self._load(plan_id)
        panel = self._score_panel()
        close = panel["close"]
        future = close.groupby(level="instrument", sort=False).shift(-1) / close - 1
        dates = pd.Series(panel.index.get_level_values("date"), index=panel.index)
        instruments = pd.Series(panel.index.get_level_values("instrument"), index=panel.index)
        seen_formulas, seen_names, prior_history = self._known_factors(plan_id)
        target = int(plan["max_loops"])
        while _mined_factor_count(plan["rounds"]) < target:
            stopped = self._aborted(plan_id, generation, cancel)
            if stopped is not None:
                return stopped
            history = prior_history + [
                {
                    "name": item.get("name"),
                    "formula": item.get("formula"),
                    "valid_ic": item.get("valid_ic"),
                    "valid_information_ratio": item.get("valid_information_ratio"),
                    "error": item.get("error"),
                }
                for item in plan["rounds"]
            ]
            reply = None
            last_error = ""
            rejections: list[dict[str, str]] = []
            saved = False
            formula_error: dict[str, object] | None = None
            for _attempt in range(AGENT_ATTEMPTS):
                try:
                    reply = runner.run(
                        _prompt_for_model(history, rejections, str(plan["model"]), plan.get("last_feedback")),
                        model=str(plan["model"]),
                        workspace=self._agent_workspace(),
                    )
                except Exception as error:  # noqa: BLE001 — timeout retries this round, then the loop goes on
                    plan["agent_calls"] = int(plan["agent_calls"]) + 1
                    last_error = str(error)
                    reply = None
                    if not self._owns(plan_id, generation):
                        return {"plan_id": plan_id, "status": "superseded"}
                    self._write_plan(plan)
                    stopped = self._aborted(plan_id, generation, cancel)
                    if stopped is not None:
                        return stopped
                    if _local_model_down(last_error) and str(plan.get("model") or "") == LOCAL_MODEL:
                        self._remember_model_gap(plan, last_error, generation)
                        try:
                            ensure_local_gpt(cancel=cancel)
                        except Exception as restart_error:  # noqa: BLE001 — keep mining after the model is back
                            self._remember_model_gap(plan, str(restart_error), generation)
                        stopped = self._aborted(plan_id, generation, cancel)
                        if stopped is not None:
                            return stopped
                        time.sleep(5)
                    continue
                plan["agent_calls"] = int(plan["agent_calls"]) + 1
                try:
                    batch = extract_factor_batch(reply)
                except Exception as error:  # noqa: BLE001 — a bad reply is one recorded row, then the loop goes on
                    formula_error = {
                        "error": str(error),
                        "valid_ic": None,
                        "test_ic": None,
                        "formula": "",
                        "name": "",
                        "reason": "",
                        "hypothesis": "",
                        "created_at": _now_text(),
                        "model": str(plan["model"]),
                    }
                    break
                hypothesis = str(batch["hypothesis"])
                batch_rows: list[dict[str, object]] = []
                feedback_rows: list[dict[str, object]] = []
                accepted_this_batch = 0
                room = target - _mined_factor_count(plan["rounds"])
                train_end = str(plan["train_end"])
                valid_end = str(plan["valid_end"])
                for proposal in batch["factors"]:
                    assert isinstance(proposal, dict)
                    round_row = {
                        "error": "",
                        "valid_ic": None,
                        "test_ic": None,
                        "created_at": _now_text(),
                        "model": str(plan["model"]),
                        "hypothesis": hypothesis,
                        "accepted": False,
                    }
                    try:
                        formula = str(proposal["formula"])
                        name = _name_key(str(proposal["name"]))
                        round_row.update(proposal)
                        round_row["hypothesis"] = hypothesis
                        if len(formula) > MAX_FORMULA_LENGTH:
                            raise ValueError("公式过长")
                        if not name:
                            raise ValueError("缺少因子名")
                        key = _formula_key(formula)
                        if key in seen_formulas or name in seen_names:
                            rejections.append({"name": str(proposal["name"]), "formula": formula})
                            continue
                        if accepted_this_batch >= room:
                            round_row["error"] = "本轮数量已经够了"
                            batch_rows.append(round_row)
                            continue
                        seen_formulas.add(key)
                        seen_names.add(name)
                        factor = evaluate_formula(formula, panel)
                        valid_mask = (dates > plan["train_end"]) & (dates <= plan["valid_end"])
                        test_mask = dates > plan["valid_end"]
                        round_row["valid_ic"] = mean_rank_ic(factor[valid_mask], future[valid_mask], dates[valid_mask])
                        round_row["test_ic"] = mean_rank_ic(factor[test_mask], future[test_mask], dates[test_mask])
                        valid_book = run_factor_strategy(
                            factor,
                            future,
                            dates,
                            instruments,
                            prices=close,
                            train_end=train_end,
                            valid_end=valid_end,
                            book="valid",
                        )
                        test_book = run_factor_strategy(
                            factor,
                            future,
                            dates,
                            instruments,
                            prices=close,
                            train_end=train_end,
                            valid_end=valid_end,
                            book="test",
                        )
                        _record_own_books(round_row, valid_book, test_book)
                        self._publish_own_test(plan, round_row, valid_book, test_book)
                        if round_row.get("accepted"):
                            accepted_this_batch += 1
                        feedback_rows.append(
                            {
                                "name": round_row.get("name"),
                                "accepted": bool(round_row.get("accepted")),
                                "valid_information_ratio": round_row.get("valid_information_ratio"),
                                "test_information_ratio": _book_ir(test_book),
                            }
                        )
                        batch_rows.append(round_row)
                    except Exception as error:  # noqa: BLE001 — one bad formula does not discard the rest of the batch
                        round_row["error"] = str(error)
                        round_row["formula"] = round_row.get("formula") or ""
                        batch_rows.append(round_row)
                if rejections and not batch_rows:
                    continue
                plan["last_feedback"] = {"hypothesis": hypothesis, "scored": feedback_rows}
                plan["rounds"].extend(batch_rows)
                saved = True
                break
            if saved:
                pass
            elif formula_error is not None:
                plan["rounds"].append(formula_error)
            elif rejections and reply is not None:
                if not self._owns(plan_id, generation):
                    return {"plan_id": plan_id, "status": "superseded"}
                self._write_plan(plan)
                continue
            else:
                stopped = self._aborted(plan_id, generation, cancel)
                if stopped is not None:
                    return stopped
                if _local_model_down(last_error) and str(plan.get("model") or "") == LOCAL_MODEL:
                    self._remember_model_gap(plan, last_error, generation)
                    try:
                        ensure_local_gpt(cancel=cancel)
                    except Exception as restart_error:  # noqa: BLE001 — keep mining after the model is back
                        self._remember_model_gap(plan, str(restart_error), generation)
                    stopped = self._aborted(plan_id, generation, cancel)
                    if stopped is not None:
                        return stopped
                    time.sleep(5)
                    continue
                plan = self._load(plan_id)
                plan["rounds"].append(
                    {
                        "error": last_error,
                        "valid_ic": None,
                        "test_ic": None,
                        "formula": "",
                        "name": "",
                        "reason": "",
                        "created_at": _now_text(),
                        "model": str(plan["model"]),
                    }
                )
            if not self._owns(plan_id, generation):
                return {"plan_id": plan_id, "status": "superseded"}
            self._write_plan(plan)
        if not self._owns(plan_id, generation):
            return {"plan_id": plan_id, "status": "superseded"}
        stopped = self._aborted(plan_id, generation, cancel)
        if stopped is not None:
            return stopped
        plan = self._load(plan_id)
        plan["status"] = "completed"
        plan["stop_reason"] = "已挖满指定数量"
        plan["stop_requested"] = False
        self._write_plan(plan, force=True)
        return self._public(plan)

    def _remember_model_gap(self, plan: dict[str, object], detail: str = "", generation: int | None = None) -> None:
        """Note a model outage without ending the run or counting it as a factor."""
        if not self._owns(str(plan.get("plan_id") or ""), generation):
            return
        reason = "本机模型中断，正在重新拉起"
        if detail:
            reason = f"{reason}：{detail[:180]}"
        events = [item for item in plan.get("interruptions") or [] if isinstance(item, dict)]
        if events and events[-1].get("message") == reason:
            return
        events.append({"created_at": _now_text(), "message": reason})
        plan["interruptions"] = events
        self._write_plan(plan)

    def _owns(self, plan_id: str, generation: int | None) -> bool:
        if generation is None:
            return True
        return self._generation.get(plan_id) == generation

    def _live(self, plan_id: str) -> bool:
        thread = self._runners.get(plan_id)
        if thread is None:
            return False
        if thread.is_alive():
            return True
        return thread.ident is None

    def _reap_orphans(self) -> None:
        """A restarted process has no mining thread. Do not leave those plans looking active."""
        for path in self._plan_paths():
            plan = self._read_plan(path)
            if plan.get("status") != "running":
                continue
            plan_id = str(plan.get("plan_id") or "")
            if self._live(plan_id):
                continue
            plan["status"] = "stopped"
            plan["stop_requested"] = True
            plan["stop_reason"] = "已停止"
            self._write_plan(plan, force=True)

    def _aborted(self, plan_id: str, generation: int | None, cancel: threading.Event | None) -> dict[str, object] | None:
        if not self._owns(plan_id, generation):
            return {"plan_id": plan_id, "status": "superseded"}
        plan = self._load(plan_id)
        if not plan.get("stop_requested") and not (cancel is not None and cancel.is_set()):
            return None
        return self._finish_if_stopped(plan_id)

    def _finish_if_stopped(self, plan_id: str) -> dict[str, object] | None:
        plan = self._load(plan_id)
        if not plan.get("stop_requested"):
            plan["stop_requested"] = True
        plan["status"] = "stopped"
        plan["stop_reason"] = "已停止"
        self._write_plan(plan, force=True)
        return self._public(plan)

    def _run_safe(self, plan_id: str, generation: int, cancel: threading.Event) -> None:
        try:
            self.execute(plan_id, generation=generation, cancel=cancel)
        except Exception as error:  # noqa: BLE001
            if not self._owns(plan_id, generation):
                return
            plan = self._load(plan_id)
            if plan.get("stop_requested") or cancel.is_set():
                plan["status"] = "stopped"
                plan["stop_requested"] = True
                plan["stop_reason"] = "已停止"
            else:
                plan["status"] = "failed"
                plan["stop_reason"] = str(error)
            self._write_plan(plan, force=True)
        finally:
            if self._runners.get(plan_id) is threading.current_thread():
                self._runners.pop(plan_id, None)

    def _score_panel(self) -> pd.DataFrame:
        path = self.qlib_root / "panel.parquet"
        if not path.is_file():
            raise ValueError("还没有转换后的行情面板")
        mtime = path.stat().st_mtime
        if self._panel_cache is not None and self._panel_cache[0] == mtime:
            return self._panel_cache[1]
        frame = pd.read_parquet(path)
        csi300 = self.qlib_dir / "instruments" / "csi300.txt"
        if csi300.is_file():
            symbols = {line.split("\t", 1)[0] for line in csi300.read_text(encoding="utf-8").splitlines() if line}
            frame = frame[frame["symbol"].isin(symbols)]
        frame = frame.rename(columns={"symbol": "instrument"})
        frame = frame.set_index(["instrument", "date"]).sort_index()
        self._panel_cache = (mtime, frame)
        return frame

    def _load_membership(self) -> pd.DataFrame | None:
        path = self._membership_path()
        if path is None:
            return None
        table = pd.read_parquet(path)
        code_col = "con_code" if "con_code" in table.columns else "ts_code"
        if code_col not in table.columns or "trade_date" not in table.columns:
            return None
        out = pd.DataFrame(
            {
                "symbol": table[code_col].map(to_qlib_symbol),
                "date": table["trade_date"].map(to_iso_date),
            }
        )
        out = out[(out["symbol"] != "") & (out["date"] != "")]
        return out.drop_duplicates()

    def _membership_path(self) -> Path | None:
        name = "index_weight_000300_SH.parquet"
        roots = (
            self.settings.data_root,
            self.settings.raw_root,
            self.settings.data_root / "raw",
            self.settings.raw_root / "index_weight",
        )
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None

    def _agent_workspace(self) -> Path:
        return self.settings.runtime_root / "qlib" / "agent_workspace"

    def _write_manifest(self, payload: dict[str, object]) -> None:
        path = self.qlib_root / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _plan_paths(self) -> list[Path]:
        if not self.plans_dir.is_dir():
            return []
        return sorted(self.plans_dir.glob("qlib-*.json"), reverse=True)

    def _plan_path(self, plan_id: str) -> Path:
        if not plan_id.startswith("qlib-") or "/" in plan_id:
            raise ValueError("计划编号无效")
        return self.plans_dir / f"{plan_id}.json"

    def _read_plan(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _load(self, plan_id: str) -> dict[str, object]:
        path = self._plan_path(plan_id)
        if not path.is_file():
            raise ValueError("找不到这个循环计划")
        return self._read_plan(path)

    def _write_plan(self, plan: dict[str, object], *, force: bool = False) -> None:
        path = self._plan_path(str(plan["plan_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and not force:
            current = self._read_plan(path)
            if current.get("stop_requested"):
                plan["stop_requested"] = True
            if current.get("status") in {"stopped", "completed"} and plan.get("status") == "running":
                plan["status"] = current["status"]
                plan["stop_reason"] = current.get("stop_reason") or plan.get("stop_reason") or ""
        temporary = path.with_suffix(".json.next")
        temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _public(plan: dict[str, object]) -> dict[str, object]:
        return {
            "plan_id": plan["plan_id"],
            "created_at": plan.get("created_at", ""),
            "status": plan["status"],
            "max_loops": plan["max_loops"],
            "patience": plan["patience"],
            "model": plan["model"],
            "train_end": plan["train_end"],
            "valid_end": plan["valid_end"],
            "agent_calls": plan.get("agent_calls", 0),
            "stop_reason": plan.get("stop_reason", ""),
            "interruptions": [item for item in plan.get("interruptions") or [] if isinstance(item, dict)],
            "rounds": plan.get("rounds", []),
        }
