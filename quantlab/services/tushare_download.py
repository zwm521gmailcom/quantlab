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
from datetime import datetime
from pathlib import Path
from typing import Any
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

    def download_suspend_d(self, start_date: str, end_date: str) -> dict[str, Any]:
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")
        output = self.settings.raw_root / "suspend_d" / "suspend_d.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for month_start, month_end in self._month_ranges(start_date, end_date):
            rows.extend(self._call("suspend_d", {"start_date": month_start, "end_date": month_end}))
        if not rows and existing.empty:
            raise ValueError("tushare returned no suspend_d rows")
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
        if not frame.empty:
            keys = [column for column in ("ts_code", "trade_date", "suspend_type") if column in frame.columns]
            if keys:
                frame = frame.drop_duplicates(keys, keep="last")
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        return {"rows": len(rows), "target": "raw/suspend_d/suspend_d.parquet", "file_name": output.name}

    def refresh_stock_basic(self) -> dict[str, Any]:
        target_dir = self.settings.raw_root / "stock_basic"
        target_dir.mkdir(parents=True, exist_ok=True)
        totals = {}
        for status in ("L", "D", "P"):
            rows = self._call("stock_basic", {"list_status": status})
            if rows:
                output = target_dir / f"stock_basic_{status}.parquet"
                pq.write_table(pa.Table.from_pylist(rows), output)
                totals[status] = len(rows)
        return {"rows": totals}

    def download_interface_dates(self, api_name: str, interface_dir: str, dates: list[str], pause: float = 0.35, max_retries: int = 3) -> dict[str, Any]:
        target_dir = self.settings.raw_root / interface_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        done, failed, total = 0, [], 0
        for date in dates:
            output = target_dir / f"{date}.parquet"
            if output.exists():
                continue
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

