"""Small Tushare Pro downloader for raw interface files."""

from __future__ import annotations

import calendar
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.config import Settings


_TUSHARE_API = "https://api.tushare.pro"
logger = logging.getLogger(__name__)

# Official limits: https://tushare.pro/document/1?doc_id=290
# Strict: use table values as-is (no headroom discount).
TUSHARE_FREQ_DOC = "https://tushare.pro/document/1?doc_id=290"
TUSHARE_FREQ_TIERS: tuple[dict[str, Any], ...] = (
    {
        "min_points": 120,
        "tier": 120,
        "calls_per_minute": 50,
        "daily_limit_per_api": 8000,
        "note": "非复权日线等；其他接口可能无法调取",
    },
    {
        "min_points": 2000,
        "tier": 2000,
        "calls_per_minute": 200,
        "daily_limit_per_api": 100_000,
        "note": "可参考各接口文档积分要求；100000次/个API/天",
    },
    {
        "min_points": 5000,
        "tier": 5000,
        "calls_per_minute": 500,
        "daily_limit_per_api": None,
        "note": "常规数据无日总量上限",
    },
    {
        "min_points": 10000,
        "tier": 10000,
        "calls_per_minute": 500,
        "daily_limit_per_api": None,
        "note": "常规无日上限；特色数据 300次/分",
    },
    {
        "min_points": 15000,
        "tier": 15000,
        "calls_per_minute": 500,
        "daily_limit_per_api": None,
        "note": "特色数据无总量限制",
    },
)
_DEFAULT_MAX_CALLS_PER_MINUTE = 50  # safest until points are configured; 2000积分档实测硬顶 200/min
_RATE_LIMIT_PATTERN = re.compile(r"频率超限|每分钟最多访问")
_MAX_RATE_LIMIT_RETRIES = 5


@dataclass(frozen=True)
class TushareQuota:
    points: int | None
    tier: int
    calls_per_minute: int
    daily_limit_per_api: int | None
    configured: bool
    doc: str = TUSHARE_FREQ_DOC


def resolve_tushare_quota(points: int | None) -> TushareQuota:
    """Map saved points to doc_id=290 frequency tier (floor match)."""
    if points is None:
        tier = TUSHARE_FREQ_TIERS[0]
        return TushareQuota(
            points=None,
            tier=int(tier["tier"]),
            calls_per_minute=int(tier["calls_per_minute"]),
            daily_limit_per_api=tier["daily_limit_per_api"],
            configured=False,
        )
    value = int(points)
    if value < 0:
        raise ValueError("tushare points must be >= 0")
    chosen = TUSHARE_FREQ_TIERS[0]
    for tier in TUSHARE_FREQ_TIERS:
        if value >= int(tier["min_points"]):
            chosen = tier
    return TushareQuota(
        points=value,
        tier=int(chosen["tier"]),
        calls_per_minute=int(chosen["calls_per_minute"]),
        daily_limit_per_api=chosen["daily_limit_per_api"],
        configured=True,
    )


def points_config_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "config" / "tushare_points.json"


def load_tushare_points(runtime_root: Path) -> int | None:
    path = points_config_path(runtime_root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("points") is None:
        return None
    try:
        return int(value["points"])
    except (TypeError, ValueError):
        return None


def quota_public(runtime_root: Path) -> dict[str, Any]:
    points = load_tushare_points(runtime_root)
    quota = resolve_tushare_quota(points)
    return {
        "configured": quota.configured,
        "points": quota.points,
        "tier": quota.tier,
        "calls_per_minute": quota.calls_per_minute,
        "daily_limit_per_api": quota.daily_limit_per_api,
        "doc": quota.doc,
        "tiers": list(TUSHARE_FREQ_TIERS),
    }


class TushareRateLimiter:
    """Sliding-window limiter aligned with Tushare Pro doc_id=290."""

    def __init__(self, max_calls_per_minute: int = _DEFAULT_MAX_CALLS_PER_MINUTE) -> None:
        if max_calls_per_minute < 1:
            raise ValueError("max_calls_per_minute must be positive")
        self.max_calls_per_minute = max_calls_per_minute
        self.min_interval_seconds = 60.0 / max_calls_per_minute
        self._window: deque[float] = deque()
        self._last_call_at: float | None = None

    def configure(self, max_calls_per_minute: int) -> None:
        if max_calls_per_minute < 1:
            raise ValueError("max_calls_per_minute must be positive")
        self.max_calls_per_minute = max_calls_per_minute
        self.min_interval_seconds = 60.0 / max_calls_per_minute

    def wait(self) -> None:
        now = time.monotonic()
        while self._window and now - self._window[0] >= 60.0:
            self._window.popleft()
        if len(self._window) >= self.max_calls_per_minute:
            sleep_for = 60.0 - (now - self._window[0]) + 0.05
            logger.info(
                "Tushare pacing: %d calls in last 60s (limit %d/min per doc_id=290), sleeping %.2fs",
                len(self._window),
                self.max_calls_per_minute,
                sleep_for,
            )
            time.sleep(max(sleep_for, 0.0))
            now = time.monotonic()
            while self._window and now - self._window[0] >= 60.0:
                self._window.popleft()
        if self._last_call_at is not None:
            elapsed = now - self._last_call_at
            if elapsed < self.min_interval_seconds:
                gap = self.min_interval_seconds - elapsed
                logger.info(
                    "Tushare pacing: spacing calls by %.3fs (limit %d/min)",
                    gap,
                    self.max_calls_per_minute,
                )
                time.sleep(gap)
                now = time.monotonic()
        self._window.append(now)
        self._last_call_at = now


class TushareDailyBudget:
    """Per-API daily call budget from doc_id=290 (None = unlimited)."""

    def __init__(self, path: Path, daily_limit_per_api: int | None) -> None:
        self.path = path
        self.daily_limit_per_api = daily_limit_per_api
        self._lock = threading.Lock()

    def configure(self, daily_limit_per_api: int | None) -> None:
        with self._lock:
            self.daily_limit_per_api = daily_limit_per_api

    def _today(self) -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    def _read(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("date") == self._today():
                    counts = value.get("apis") or {}
                    if isinstance(counts, dict):
                        return {"date": self._today(), "apis": {str(k): int(v) for k, v in counts.items()}}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        return {"date": self._today(), "apis": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def consume(self, api_name: str) -> None:
        if self.daily_limit_per_api is None:
            return
        with self._lock:
            data = self._read()
            key = str(api_name)
            used = int(data["apis"].get(key, 0))
            if used >= int(self.daily_limit_per_api):
                raise ValueError(
                    f"tushare daily limit reached for {key}: {used}/{self.daily_limit_per_api} "
                    f"(doc_id=290 tier limit); resume tomorrow"
                )
            data["apis"][key] = used + 1
            self._write(data)


def _is_rate_limit_error(message: str) -> bool:
    return bool(_RATE_LIMIT_PATTERN.search(message))


class TushareDownloadService:
    def __init__(self, settings: Settings, *, rate_limiter: TushareRateLimiter | None = None) -> None:
        self.settings = settings
        self._enforce_quota = rate_limiter is None
        self._rate_limiter = rate_limiter if rate_limiter is not None else TushareRateLimiter()
        budget_path = Path(settings.runtime_root) / "config" / "tushare_daily_budget.json"
        self._daily_budget = TushareDailyBudget(budget_path, daily_limit_per_api=8000)
        self._backfill_guard = threading.Lock()
        self._backfill_running = False
        self._backfill_cancel = threading.Event()
        self._backfill_jobs: dict[str, dict[str, Any]] = {}
        if self._enforce_quota:
            self.apply_saved_quota()

    def apply_saved_quota(self) -> TushareQuota:
        quota = resolve_tushare_quota(load_tushare_points(self.settings.runtime_root))
        self._rate_limiter.configure(quota.calls_per_minute)
        self._daily_budget.configure(quota.daily_limit_per_api)
        logger.info(
            "Tushare quota applied: points=%s tier=%s %d/min daily_per_api=%s (%s)",
            quota.points,
            quota.tier,
            quota.calls_per_minute,
            quota.daily_limit_per_api if quota.daily_limit_per_api is not None else "unlimited",
            quota.doc,
        )
        return quota

    def _token(self) -> str:
        token_path = self.settings.runtime_root / "config/tushare_token.json"
        if not token_path.is_file():
            raise ValueError("tushare api key is not configured")
        try:
            value = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("tushare api key is not configured") from error
        token = str(value.get("token") or "").strip()
        if not token:
            raise ValueError("tushare api key is not configured")
        return token

    def _call(self, api_name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = {
            "api_name": api_name,
            "token": self._token(),
            "params": params,
            "fields": "",
        }
        request = urllib.request.Request(
            _TUSHARE_API,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: ValueError | None = None
        for attempt in range(1, _MAX_RATE_LIMIT_RETRIES + 1):
            self._rate_limiter.wait()
            if self._enforce_quota:
                self._daily_budget.consume(api_name)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                raise ValueError(f"tushare http error: {error.code}") from error
            if body.get("code") == 0:
                data = body.get("data") or {}
                fields = data.get("fields") or []
                items = data.get("items") or []
                return [dict(zip(fields, row, strict=True)) for row in items]
            message = str(body.get("msg") or "unknown")
            last_error = ValueError(f"tushare error: {message}")
            if not _is_rate_limit_error(message) or attempt >= _MAX_RATE_LIMIT_RETRIES:
                raise last_error
            backoff = min(2 ** (attempt - 1), 30)
            logger.warning(
                "Tushare rate limit on %s (attempt %d/%d): %s; retry in %ds",
                api_name,
                attempt,
                _MAX_RATE_LIMIT_RETRIES,
                message,
                backoff,
            )
            time.sleep(backoff)
        raise last_error or ValueError("tushare error: unknown")

    _INDEX_WEIGHT_CODE_ALIASES = {
        "000300.SH": "000300.SH",
        "000905.SH": "000905.SH",
        "399300.SZ": "000300.SH",
    }

    def _index_weight_api_code(self, index_code: str) -> str:
        mapped = self._INDEX_WEIGHT_CODE_ALIASES.get(index_code, index_code)
        return mapped

    def _today_yyyymmdd(self) -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")

    def _month_ranges(self, start_date: str, end_date: str) -> list[tuple[str, str]]:
        start = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date, "%Y%m%d")
        ranges: list[tuple[str, str]] = []
        cursor = start.replace(day=1)
        while cursor <= end:
            last_day = calendar.monthrange(cursor.year, cursor.month)[1]
            month_start = cursor.replace(day=1)
            month_end = cursor.replace(day=last_day)
            range_start = max(start, month_start)
            range_end = min(end, month_end)
            if range_start <= range_end:
                ranges.append((range_start.strftime("%Y%m%d"), range_end.strftime("%Y%m%d")))
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1, day=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1, day=1)
        return ranges

    # Empirically index_weight responses truncate around 7000 rows (newest kept).
    _INDEX_WEIGHT_ROW_CAP = 7000

    def _index_weight_chunk_ranges(self, start_date: str, end_date: str, cons_hint: int) -> list[tuple[str, str]]:
        """Chunk by months sized from constituent hint so each call stays under row cap."""
        months = self._month_ranges(start_date, end_date)
        if not months:
            return []
        cons = max(int(cons_hint or 1), 1)
        # leave headroom: cap*0.9 / cons
        months_per_call = max(1, int((self._INDEX_WEIGHT_ROW_CAP * 0.9) // cons))
        chunks: list[tuple[str, str]] = []
        for i in range(0, len(months), months_per_call):
            part = months[i : i + months_per_call]
            chunks.append((part[0][0], part[-1][1]))
        return chunks

    def download_index_weight(self, index_code: str, start_date: str, end_date: str) -> dict[str, Any]:
        api_code = self._index_weight_api_code(index_code)
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        file_stem = api_code.replace(".", "_")
        output = self.settings.raw_root / "index_weight" / f"index_weight_{file_stem}.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        # Probe latest month for constituent count → adaptive window (much fewer calls than 1-month).
        months = self._month_ranges(start_date, end_date)
        cons_hint = 300
        if months:
            probe = self._call(
                "index_weight",
                {"index_code": api_code, "start_date": months[-1][0], "end_date": months[-1][1]},
            )
            if probe:
                cons_hint = max(len(probe), 1)
        rows: list[dict[str, Any]] = []
        chunks = self._index_weight_chunk_ranges(start_date, end_date, cons_hint)
        calls = 0
        for chunk_start, chunk_end in chunks:
            chunk_rows = self._call(
                "index_weight",
                {"index_code": api_code, "start_date": chunk_start, "end_date": chunk_end},
            )
            calls += 1
            # If still hitting row cap, split this chunk month-by-month.
            if len(chunk_rows) >= self._INDEX_WEIGHT_ROW_CAP:
                logger.warning(
                    "index_weight row cap hit for %s %s-%s (rows=%d cons_hint=%d); fallback monthly",
                    api_code,
                    chunk_start,
                    chunk_end,
                    len(chunk_rows),
                    cons_hint,
                )
                chunk_rows = []
                for month_start, month_end in self._month_ranges(chunk_start, chunk_end):
                    chunk_rows.extend(
                        self._call(
                            "index_weight",
                            {"index_code": api_code, "start_date": month_start, "end_date": month_end},
                        )
                    )
                    calls += 1
            rows.extend(chunk_rows)
        if not rows and existing.empty:
            raise ValueError(f"tushare returned no index_weight rows for {index_code}")
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).drop_duplicates(
            ["index_code", "con_code", "trade_date"], keep="last"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        relative = f"raw/index_weight/index_weight_{file_stem}.parquet"
        result: dict[str, Any] = {
            "rows": len(rows),
            "target": relative,
            "file_name": output.name,
            "cons_hint": cons_hint,
            "chunks": len(chunks),
            "calls": calls,
        }
        if index_code != api_code:
            result["mapped_from"] = index_code
            result["note"] = f"请求代码 {index_code} 已映射为 {api_code}"
            logger.info("index_weight code mapped: %s -> %s", index_code, api_code)
        return result

    def download_index_basic(self) -> dict[str, Any]:
        stopped = self._consume_backfill_stop()
        if stopped:
            return {"rows": 0, "stopped": stopped, "target": "raw/index_basic/index_basic.parquet"}
        rows = self._call("index_basic", {})
        if not rows:
            raise ValueError("tushare returned no index_basic rows")
        target = self.settings.raw_root / "index_basic"
        target.mkdir(parents=True, exist_ok=True)
        output = target / "index_basic.parquet"
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, output)
        return {"rows": len(rows), "target": "raw/index_basic/index_basic.parquet", "file_name": output.name}

    def download_date_interface(self, api_name: str, trade_date: str, interface_dir: str, overwrite: bool = False) -> dict[str, Any]:
        target_dir = self.settings.raw_root / interface_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / f"{trade_date}.parquet"
        if output.exists() and not overwrite:
            return {"skipped": True, "file_name": output.name, "rows": 0}
        rows = self._call(api_name, {"trade_date": trade_date})
        if rows:
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, output)
        else:
            raise ValueError(f"tushare returned no rows for {api_name} {trade_date}")
        return {"rows": len(rows), "file_name": output.name, "skipped": False}

    def refresh_trade_cal(self, start_date: str, end_date: str) -> dict[str, Any]:
        rows = self._call("trade_cal", {"exchange": "SSE", "start_date": start_date, "end_date": end_date})
        output = self.settings.raw_root / "trade_cal/calendar.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).drop_duplicates(["exchange", "cal_date"], keep="last")
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        return {"rows": len(rows), "target": "raw/trade_cal/calendar.parquet"}

    def refresh_index_daily(self, ts_code: str, start_date: str, end_date: str) -> dict[str, Any]:
        code = str(ts_code or "").strip().upper().replace("-", ".")
        if not code:
            raise ValueError("ts_code is required")
        rows = self._call("index_daily", {"ts_code": code, "start_date": start_date, "end_date": end_date})
        slug = code.replace(".", "_")
        relative = f"raw/index_daily/index_daily_{slug}.parquet"
        output = self.settings.raw_root / "index_daily" / f"index_daily_{slug}.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
        if not frame.empty:
            keys = [column for column in ("ts_code", "trade_date") if column in frame.columns]
            if keys:
                frame = frame.drop_duplicates(keys, keep="last")
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        return {"rows": len(rows), "target": relative}

    # doc_id=170: 单次最大 6000 行，超出是静默截断。打包预算留出余量。
    _MONEYFLOW_ROW_CAP = 6000
    _MONEYFLOW_PACK_BUDGET = 5800

    def _moneyflow_windows(self, dates: list[str], row_hints: dict[str, int]) -> list[list[str]]:
        """Pack adjacent trade dates only while estimated rows stay under the doc cap."""
        windows: list[list[str]] = []
        current: list[str] = []
        used = 0
        for date in dates:
            hint = row_hints.get(date)
            cost = int(hint) if hint is not None else self._MONEYFLOW_PACK_BUDGET
            if cost < 1:
                cost = 1
            if current and used + cost > self._MONEYFLOW_PACK_BUDGET:
                windows.append(current)
                current = []
                used = 0
            current.append(date)
            used += cost
        if current:
            windows.append(current)
        return windows

    def _calendar_covers(self, start_date: str, end_date: str) -> bool:
        path = self.settings.raw_root / "trade_cal" / "calendar.parquet"
        if not path.is_file():
            return False
        try:
            dates = [str(value) for value in pq.read_table(path, columns=["cal_date"]).column("cal_date").to_pylist()]
        except (OSError, KeyError):
            return False
        return bool(dates) and min(dates) <= start_date and max(dates) >= end_date

    def _sse_open_dates(self, start_date: str, end_date: str) -> list[str]:
        if not self._calendar_covers(start_date, end_date):
            self.refresh_trade_cal(start_date, end_date)
        path = self.settings.raw_root / "trade_cal" / "calendar.parquet"
        frame = pq.read_table(path, columns=["exchange", "cal_date", "is_open"]).to_pandas()
        mask = (frame["exchange"].astype(str) == "SSE") & (frame["is_open"].astype(int) == 1)
        dates = sorted(str(value) for value in frame.loc[mask, "cal_date"] if start_date <= str(value) <= end_date)
        return dates

    def _moneyflow_row_hints(self, dates: list[str]) -> dict[str, int]:
        daily = self.settings.raw_root / "daily"
        hints: dict[str, int] = {}
        if not daily.is_dir():
            return hints
        for date in dates:
            path = daily / f"{date}.parquet"
            if not path.is_file():
                continue
            try:
                hints[date] = int(pq.ParquetFile(path).metadata.num_rows)
            except OSError:
                continue
        return hints

    def download_moneyflow(self, start_date: str, end_date: str) -> dict[str, Any]:
        start_date = str(start_date or "").strip()
        end_date = str(end_date or "").strip()
        if not re.fullmatch(r"\d{8}", start_date) or not re.fullmatch(r"\d{8}", end_date):
            raise ValueError("start_date、end_date 必须是 YYYYMMDD")
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")
        if self._enforce_quota:
            self.apply_saved_quota()
        open_dates = self._sse_open_dates(start_date, end_date)
        target = self.settings.raw_root / "moneyflow"
        skipped = 0
        runs: list[list[str]] = []
        current: list[str] = []
        for date in open_dates:
            if (target / f"{date}.parquet").is_file():
                skipped += 1
                if current:
                    runs.append(current)
                    current = []
                continue
            current.append(date)
        if current:
            runs.append(current)
        missing = [date for run in runs for date in run]
        windows: list[list[str]] = []
        hints = self._moneyflow_row_hints(missing)
        for run in runs:
            windows.extend(self._moneyflow_windows(run, hints))
        target.mkdir(parents=True, exist_ok=True)
        calls = 0
        written_rows = 0
        files = 0
        stopped: str | None = None

        def write_day(day: str, rows: list[dict[str, Any]]) -> None:
            nonlocal written_rows, files
            frame = pd.DataFrame(rows)
            keys = [column for column in ("ts_code", "trade_date") if column in frame.columns]
            if keys:
                frame = frame.drop_duplicates(keys, keep="last")
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), target / f"{day}.parquet")
            written_rows += len(frame)
            files += 1

        def fetch(params: dict[str, Any]) -> list[dict[str, Any]] | None:
            nonlocal calls, stopped
            reason = self._consume_backfill_stop()
            if reason:
                stopped = reason
                return None
            try:
                rows = self._call("moneyflow", params)
            except ValueError as error:
                if "daily limit" in str(error):
                    stopped = str(error)
                    return None
                raise
            calls += 1
            return rows

        def fetch_day(day: str) -> list[dict[str, Any]] | None:
            rows = fetch({"trade_date": day})
            if rows is None:
                return None
            if len(rows) >= self._MONEYFLOW_ROW_CAP:
                raise ValueError(f"moneyflow {day} 达到单次 6000 行上限（doc_id=170），拒绝写入不完整文件")
            return rows

        for window in windows:
            if stopped:
                break
            if len(window) == 1:
                rows = fetch_day(window[0])
                if rows is None:
                    break
                if rows:
                    write_day(window[0], rows)
                continue
            rows = fetch({"start_date": window[0], "end_date": window[-1]})
            if rows is None:
                break
            by_date: dict[str, list[dict[str, Any]]] = {day: [] for day in window}
            truncated = len(rows) >= self._MONEYFLOW_ROW_CAP
            if not truncated:
                for row in rows:
                    day = str(row.get("trade_date") or "")
                    if day in by_date:
                        by_date[day].append(row)
                if any(not by_date[day] for day in window):
                    truncated = True
            if truncated:
                logger.warning(
                    "moneyflow window %s-%s hit the 6000 row cap or dropped a date; refetch by trade_date",
                    window[0],
                    window[-1],
                )
                for day in window:
                    if stopped:
                        break
                    day_rows = fetch_day(day)
                    if day_rows is None:
                        break
                    if day_rows:
                        write_day(day, day_rows)
                continue
            for day in window:
                if by_date[day]:
                    write_day(day, by_date[day])
        if written_rows == 0 and skipped == 0 and not stopped:
            raise ValueError("tushare returned no moneyflow rows")
        result: dict[str, Any] = {
            "rows": written_rows,
            "target": "raw/moneyflow",
            "calls": calls,
            "skipped": skipped,
            "files": files,
        }
        if stopped:
            result["stopped"] = stopped
        return result

    def _period_end_dates(self, open_dates: list[str], freq: str) -> list[str]:
        if freq not in {"week", "month"}:
            raise ValueError("freq 必须是 week 或 month")
        groups: dict[tuple[int, int], str] = {}
        for day in open_dates:
            current = datetime.strptime(day, "%Y%m%d")
            key = current.isocalendar()[:2] if freq == "week" else (current.year, current.month)
            previous = groups.get(key)
            if previous is None or day > previous:
                groups[key] = day
        return [groups[key] for key in sorted(groups)]

    def download_stk_week_month_adj(self, start_date: str, end_date: str, freq: str | None = None) -> dict[str, Any]:
        start_date = str(start_date or "").strip()
        end_date = str(end_date or "").strip()
        if not re.fullmatch(r"\d{8}", start_date) or not re.fullmatch(r"\d{8}", end_date):
            raise ValueError("start_date、end_date 必须是 YYYYMMDD")
        freqs = ["week", "month"] if not freq else [str(freq)]
        if any(item not in {"week", "month"} for item in freqs):
            raise ValueError("freq 必须是 week 或 month")
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")
        if self._enforce_quota:
            self.apply_saved_quota()
        open_dates = self._sse_open_dates(start_date, end_date)
        target = self.settings.raw_root / "stk_week_month_adj"
        target.mkdir(parents=True, exist_ok=True)
        calls = 0
        written_rows = 0
        files = 0
        skipped = 0
        stopped: str | None = None

        def write_file(name: str, rows: list[dict[str, Any]]) -> None:
            nonlocal written_rows, files
            frame = pd.DataFrame(rows)
            keys = [column for column in ("ts_code", "trade_date", "freq") if column in frame.columns]
            if keys:
                frame = frame.drop_duplicates(keys, keep="last")
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), target / name)
            written_rows += len(frame)
            files += 1

        def fetch(params: dict[str, Any]) -> list[dict[str, Any]] | None:
            nonlocal calls, stopped
            reason = self._consume_backfill_stop()
            if reason:
                stopped = reason
                return None
            try:
                rows = self._call("stk_week_month_adj", params)
            except ValueError as error:
                if "daily limit" in str(error):
                    stopped = str(error)
                    return None
                raise
            calls += 1
            return rows

        for item in freqs:
            if stopped:
                break
            ends = self._period_end_dates(open_dates, item)
            missing: list[str] = []
            for day in ends:
                if (target / f"{item}_{day}.parquet").is_file():
                    skipped += 1
                else:
                    missing.append(day)
            if not missing:
                continue
            hints = self._moneyflow_row_hints(missing)
            windows = self._moneyflow_windows(missing, hints)

            def fetch_one(day: str, freq_name: str = item) -> list[dict[str, Any]] | None:
                rows = fetch({"trade_date": day, "freq": freq_name})
                if rows is None:
                    return None
                if len(rows) >= self._MONEYFLOW_ROW_CAP:
                    raise ValueError(
                        f"stk_week_month_adj {freq_name} {day} 达到单次 6000 行上限（doc_id=365），拒绝写入不完整文件"
                    )
                return rows

            for window in windows:
                if stopped:
                    break
                if len(window) == 1:
                    rows = fetch_one(window[0])
                    if rows is None:
                        break
                    if rows:
                        write_file(f"{item}_{window[0]}.parquet", rows)
                    continue
                rows = fetch({"start_date": window[0], "end_date": window[-1], "freq": item})
                if rows is None:
                    break
                by_date: dict[str, list[dict[str, Any]]] = {day: [] for day in window}
                truncated = len(rows) >= self._MONEYFLOW_ROW_CAP
                if not truncated:
                    for row in rows:
                        day = str(row.get("trade_date") or "")
                        if day in by_date:
                            by_date[day].append(row)
                    if any(not by_date[day] for day in window):
                        truncated = True
                if truncated:
                    logger.warning(
                        "stk_week_month_adj %s %s-%s hit the 6000 row cap; refetch one period at a time",
                        item,
                        window[0],
                        window[-1],
                    )
                    for day in window:
                        if stopped:
                            break
                        day_rows = fetch_one(day)
                        if day_rows is None:
                            break
                        if day_rows:
                            write_file(f"{item}_{day}.parquet", day_rows)
                    continue
                for day in window:
                    if by_date[day]:
                        write_file(f"{item}_{day}.parquet", by_date[day])
        if written_rows == 0 and skipped == 0 and not stopped:
            raise ValueError("tushare returned no stk_week_month_adj rows")
        result: dict[str, Any] = {
            "rows": written_rows,
            "target": "raw/stk_week_month_adj",
            "calls": calls,
            "skipped": skipped,
            "files": files,
            "freqs": freqs,
        }
        if stopped:
            result["stopped"] = stopped
        return result

    def download_suspend_d(self, start_date: str, end_date: str) -> dict[str, Any]:
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")
        output = self.settings.raw_root / "suspend_d" / "suspend_d.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        rows: list[dict[str, Any]] = []
        stopped = self._consume_backfill_stop()
        if stopped is None:
            for month_start, month_end in self._month_ranges(start_date, end_date):
                stopped = self._consume_backfill_stop()
                if stopped:
                    break
                rows.extend(self._call("suspend_d", {"start_date": month_start, "end_date": month_end}))
        if stopped and not rows:
            return {"rows": 0, "target": "raw/suspend_d/suspend_d.parquet", "stopped": stopped}
        if not rows and existing.empty:
            raise ValueError("tushare returned no suspend_d rows")
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
        if not frame.empty:
            keys = [column for column in ("ts_code", "trade_date", "suspend_type") if column in frame.columns]
            if keys:
                frame = frame.drop_duplicates(keys, keep="last")
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        result: dict[str, Any] = {"rows": len(rows), "target": "raw/suspend_d/suspend_d.parquet", "file_name": output.name}
        if stopped:
            result["stopped"] = stopped
        return result

    def refresh_stock_basic(self) -> dict[str, Any]:
        target_dir = self.settings.raw_root / "stock_basic"
        target_dir.mkdir(parents=True, exist_ok=True)
        totals = {}
        stopped = None
        for status in ("L", "D", "P"):
            stopped = self._consume_backfill_stop()
            if stopped:
                break
            rows = self._call("stock_basic", {"list_status": status})
            if rows:
                output = target_dir / f"stock_basic_{status}.parquet"
                pq.write_table(pa.Table.from_pylist(rows), output)
                totals[status] = len(rows)
        result: dict[str, Any] = {"rows": totals}
        if stopped:
            result["stopped"] = stopped
        return result

    _DATE_FILE_APIS = ("daily", "daily_basic", "adj_factor", "stk_limit")

    def _stored_sse_open_dates(self) -> list[str]:
        path = self.settings.raw_root / "trade_cal" / "calendar.parquet"
        if not path.is_file():
            return []
        frame = pq.read_table(path, columns=["exchange", "cal_date", "is_open"]).to_pandas()
        mask = (frame["exchange"].astype(str) == "SSE") & (frame["is_open"].astype(int) == 1)
        return sorted(str(value) for value in frame.loc[mask, "cal_date"])

    def backfill_cutoff(self, now: datetime) -> str:
        """Last usable SSE session. Before 16:00 Shanghai, today is excluded."""
        local = now.astimezone(ZoneInfo("Asia/Shanghai"))
        today = local.strftime("%Y%m%d")
        dates = self._stored_sse_open_dates()
        if local.hour < 16:
            dates = [day for day in dates if day < today]
        else:
            dates = [day for day in dates if day <= today]
        if not dates:
            raise ValueError("没有可用的截止交易日")
        return dates[-1]

    def submit_backfill(self, interface: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Start one background backfill. A second call is rejected until it finishes."""
        if interface not in self._known_backfill_interfaces():
            raise ValueError(f"未知接口 {interface}")
        moment = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        job_id = uuid4().hex[:12]
        with self._backfill_guard:
            if self._backfill_running:
                raise ValueError("已有补数在进行")
            self._backfill_cancel.clear()
            self._backfill_running = True
            self._backfill_jobs[job_id] = {
                "job_id": job_id,
                "interface": interface,
                "status": "running",
                "cancel_requested": False,
                "processed": 0,
                "total": 0,
                "filled": 0,
                "failed_dates": [],
                "error": "",
                "summary": "",
            }
        thread = threading.Thread(
            target=self._run_backfill_job,
            args=(job_id, interface, moment),
            name=f"quantlab-backfill-{interface}",
            daemon=True,
        )
        thread.start()
        return {"job_id": job_id, "interface": interface, "status": "running"}

    def backfill_job(self, job_id: str) -> dict[str, Any]:
        with self._backfill_guard:
            job = self._backfill_jobs.get(job_id)
            if job is None:
                raise ValueError("补数任务不存在")
            return dict(job)

    def active_backfill(self) -> dict[str, Any]:
        with self._backfill_guard:
            for job in self._backfill_jobs.values():
                if job.get("status") == "running":
                    return dict(job)
        return {"status": "idle"}

    def stop_backfill(self, job_id: str) -> dict[str, Any]:
        """Ask the running backfill to finish its current write, then exit."""
        with self._backfill_guard:
            job = self._backfill_jobs.get(job_id)
            if job is None:
                raise ValueError("补数任务不存在")
            if job.get("status") != "running":
                raise ValueError("这个补数没有在跑")
            job["cancel_requested"] = True
            self._backfill_cancel.set()
            return dict(job)

    def _consume_backfill_stop(self) -> str | None:
        if self._backfill_cancel.is_set():
            return "已停止"
        return None

    def _run_backfill_job(self, job_id: str, interface: str, now: datetime) -> None:
        try:
            result = self._backfill_one(interface, now)
            status = "stopped" if str(result.get("stopped") or "") == "已停止" else "succeeded"
            self._write_backfill_job(job_id, status=status, **self._backfill_view(result))
        except Exception as error:
            self._write_backfill_job(job_id, status="failed", error=str(error))
        finally:
            with self._backfill_guard:
                self._backfill_running = False
                self._backfill_cancel.clear()

    def _write_backfill_job(self, job_id: str, **fields: Any) -> None:
        with self._backfill_guard:
            job = self._backfill_jobs.get(job_id)
            if job is not None:
                job.update(fields)

    @staticmethod
    def _backfill_view(result: dict[str, Any]) -> dict[str, Any]:
        failed = [str(day) for day in result.get("failed") or []]
        filled_dates = result.get("filled")
        rows = result.get("rows")
        if isinstance(filled_dates, list):
            filled = int(result.get("done", len(filled_dates)) or 0)
            total = len(filled_dates)
            processed = filled + len(failed)
            summary = "已是最新" if filled == 0 and not failed else f"补入 {filled} 个交易日"
        elif isinstance(rows, dict):
            filled = sum(int(value) for value in rows.values())
            processed = total = len(rows)
            summary = f"刷新 {filled} 行"
        elif isinstance(rows, int):
            filled = rows
            processed = total = int(result.get("calls") or result.get("files") or (1 if filled else 0))
            summary = "已是最新" if filled == 0 and int(result.get("calls") or 0) == 0 else f"刷新 {filled} 行"
        else:
            filled = int(result.get("calls") or 0)
            processed = total = filled
            summary = "已是最新" if str(result.get("status") or "") == "已是最新" or filled == 0 else f"补入 {filled} 个交易日"
        if str(result.get("status") or "") == "已是最新":
            summary = "已是最新"
            filled = 0
        stopped = str(result.get("stopped") or "")
        if stopped == "已停止":
            summary = "已停止" if filled == 0 else f"已停止，已补 {filled} 笔"
        return {
            "processed": processed,
            "total": total,
            "filled": filled,
            "failed_dates": failed,
            "error": "" if stopped == "已停止" else stopped,
            "summary": summary,
        }

    def backfill(self, interface: str, *, now: datetime) -> dict[str, Any]:
        """Fill one raw interface forward. Does not extend history before existing files."""
        with self._backfill_guard:
            if self._backfill_running:
                raise ValueError("已有补数在进行")
            self._backfill_cancel.clear()
            self._backfill_running = True
        try:
            return self._backfill_one(interface, now)
        finally:
            with self._backfill_guard:
                self._backfill_running = False

    def _known_backfill_interfaces(self) -> set[str]:
        return {
            *self._DATE_FILE_APIS,
            "moneyflow",
            "suspend_d",
            "index_daily",
            "index_weight",
            "stk_week_month_adj",
            "stock_basic",
            "index_basic",
            "trade_cal",
        }

    def _backfill_one(self, interface: str, now: datetime) -> dict[str, Any]:
        if interface not in self._known_backfill_interfaces():
            raise ValueError(f"未知接口 {interface}")
        cutoff = self.backfill_cutoff(now)
        if interface in self._DATE_FILE_APIS:
            return self._backfill_date_files(interface, cutoff)
        if interface == "moneyflow":
            return self._backfill_moneyflow(cutoff)
        if interface == "suspend_d":
            return self._backfill_suspend(cutoff)
        if interface == "index_daily":
            return self._backfill_index_daily(cutoff)
        if interface == "index_weight":
            return self._backfill_index_weight(cutoff)
        if interface == "stk_week_month_adj":
            return self._backfill_week_month(cutoff)
        if interface == "stock_basic":
            return self.refresh_stock_basic()
        if interface == "index_basic":
            return self.download_index_basic()
        return self._backfill_trade_cal(cutoff)

    def _file_dates(self, folder: Path) -> list[str]:
        found: list[str] = []
        if not folder.is_dir():
            return found
        for path in folder.glob("*.parquet"):
            if re.fullmatch(r"\d{8}", path.stem):
                found.append(path.stem)
        return sorted(found)

    def _open_after(self, start: str, cutoff: str) -> list[str]:
        if start >= cutoff:
            return []
        return [day for day in self._sse_open_dates(start, cutoff) if day > start]

    def _backfill_date_files(self, api_name: str, cutoff: str) -> dict[str, Any]:
        folder = self.settings.raw_root / api_name
        have = self._file_dates(folder)
        if not have:
            return {"interface": api_name, "calls": 0, "status": "已是最新", "filled": []}
        missing = self._open_after(have[-1], cutoff)
        if not missing:
            return {"interface": api_name, "calls": 0, "status": "已是最新", "filled": []}
        result = self.download_interface_dates(api_name, api_name, missing, pause=0)
        result["interface"] = api_name
        done = int(result.get("done") or 0)
        result["filled"] = missing[:done] if result.get("stopped") else missing
        return result

    def _backfill_moneyflow(self, cutoff: str) -> dict[str, Any]:
        have = self._file_dates(self.settings.raw_root / "moneyflow")
        if not have:
            return {"interface": "moneyflow", "calls": 0, "status": "已是最新"}
        missing = self._open_after(have[-1], cutoff)
        if not missing:
            return {"interface": "moneyflow", "calls": 0, "status": "已是最新"}
        result = self.download_moneyflow(missing[0], cutoff)
        result["interface"] = "moneyflow"
        return result

    def _max_column_date(self, path: Path, column: str) -> str | None:
        if not path.is_file():
            return None
        try:
            values = [str(value) for value in pq.read_table(path, columns=[column]).column(column).to_pylist() if value]
        except (OSError, KeyError):
            return None
        return max(values) if values else None

    def _backfill_suspend(self, cutoff: str) -> dict[str, Any]:
        path = self.settings.raw_root / "suspend_d" / "suspend_d.parquet"
        latest = self._max_column_date(path, "trade_date")
        if latest is None or latest >= cutoff:
            return {"interface": "suspend_d", "calls": 0, "status": "已是最新"}
        missing = self._open_after(latest, cutoff)
        if not missing:
            return {"interface": "suspend_d", "calls": 0, "status": "已是最新"}
        result = self.download_suspend_d(missing[0], cutoff)
        result["interface"] = "suspend_d"
        return result

    def _index_code_from_stem(self, prefix: str, stem: str) -> str | None:
        if not stem.startswith(prefix):
            return None
        body = stem[len(prefix) :]
        if "_" not in body:
            return None
        left, right = body.rsplit("_", 1)
        if not left or not right:
            return None
        return f"{left}.{right}"

    def _backfill_index_daily(self, cutoff: str) -> dict[str, Any]:
        folder = self.settings.raw_root / "index_daily"
        calls = 0
        if not folder.is_dir():
            return {"interface": "index_daily", "calls": 0, "status": "已是最新"}
        for path in sorted(folder.glob("index_daily_*.parquet")):
            code = self._index_code_from_stem("index_daily_", path.stem)
            latest = self._max_column_date(path, "trade_date")
            if code is None or latest is None or latest >= cutoff:
                continue
            missing = self._open_after(latest, cutoff)
            if not missing:
                continue
            stopped = self._consume_backfill_stop()
            if stopped:
                return {"interface": "index_daily", "calls": calls, "stopped": stopped, "status": "已停止"}
            self.refresh_index_daily(code, missing[0], cutoff)
            calls += 1
        return {"interface": "index_daily", "calls": calls, "status": "已是最新" if calls == 0 else "已补"}

    def _completed_month_end(self, cutoff: str) -> str | None:
        stored = self._stored_sse_open_dates()
        dates = [day for day in stored if day <= cutoff]
        if not dates:
            return None
        by_month: dict[str, list[str]] = {}
        for day in dates:
            by_month.setdefault(day[:6], []).append(day)
        ends = [max(days) for days in by_month.values()]
        cutoff_month = cutoff[:6]
        if any(day > cutoff and day.startswith(cutoff_month) for day in stored):
            ends = [day for day in ends if not day.startswith(cutoff_month)]
        return max(ends) if ends else None

    def _backfill_index_weight(self, cutoff: str) -> dict[str, Any]:
        folder = self.settings.raw_root / "index_weight"
        target = self._completed_month_end(cutoff)
        calls = 0
        if target is None or not folder.is_dir():
            return {"interface": "index_weight", "calls": 0, "status": "已是最新", "target_date": target}
        for path in sorted(folder.glob("index_weight_*.parquet")):
            code = self._index_code_from_stem("index_weight_", path.stem)
            latest = self._max_column_date(path, "trade_date")
            if code is None or latest is None or latest >= target:
                continue
            start = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
            stopped = self._consume_backfill_stop()
            if stopped:
                return {
                    "interface": "index_weight",
                    "calls": calls,
                    "stopped": stopped,
                    "status": "已停止",
                    "target_date": target,
                }
            self.download_index_weight(code, start, target)
            calls += 1
        return {"interface": "index_weight", "calls": calls, "status": "已是最新" if calls == 0 else "已补", "target_date": target}

    def _prefixed_dates(self, folder: Path, prefix: str) -> list[str]:
        found: list[str] = []
        if not folder.is_dir():
            return found
        for path in folder.glob(f"{prefix}_*.parquet"):
            day = path.stem[len(prefix) + 1 :]
            if re.fullmatch(r"\d{8}", day):
                found.append(day)
        return sorted(found)

    def _backfill_week_month(self, cutoff: str) -> dict[str, Any]:
        folder = self.settings.raw_root / "stk_week_month_adj"
        weeks = self._prefixed_dates(folder, "week")
        months = self._prefixed_dates(folder, "month")
        if not weeks and not months:
            return {"interface": "stk_week_month_adj", "calls": 0, "status": "已是最新"}
        anchor = min(weeks[-1] if weeks else cutoff, months[-1] if months else cutoff)
        missing = self._open_after(anchor, cutoff)
        if not missing:
            return {"interface": "stk_week_month_adj", "calls": 0, "status": "已是最新"}
        result = self.download_stk_week_month_adj(missing[0], cutoff)
        result["interface"] = "stk_week_month_adj"
        return result

    def _backfill_trade_cal(self, cutoff: str) -> dict[str, Any]:
        year_end = cutoff[:4] + "1231"
        if self._calendar_covers(cutoff, year_end):
            return {"interface": "trade_cal", "calls": 0, "status": "已是最新"}
        stopped = self._consume_backfill_stop()
        if stopped:
            return {"interface": "trade_cal", "calls": 0, "stopped": stopped, "status": "已停止"}
        result = self.refresh_trade_cal(cutoff, year_end)
        result["interface"] = "trade_cal"
        return result

    def download_interface_dates(self, api_name: str, interface_dir: str, dates: list[str], pause: float = 0.35, max_retries: int = 3) -> dict[str, Any]:
        target_dir = self.settings.raw_root / interface_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        done, failed, total = 0, [], 0
        for date in dates:
            output = target_dir / f"{date}.parquet"
            if output.exists():
                continue
            stopped = self._consume_backfill_stop()
            if stopped:
                return {"done": done, "failed": failed, "rows": total, "stopped": stopped}
            for attempt in range(1, max_retries + 1):
                try:
                    result = self.download_date_interface(api_name, date, interface_dir)
                    total += result.get("rows", 0)
                    done += 1
                    break
                except Exception:
                    if attempt == max_retries:
                        failed.append(date)
                    time.sleep(1 + attempt)
            time.sleep(pause)
        return {"done": done, "failed": failed, "rows": total}

