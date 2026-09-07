"""Small Tushare Pro downloader for raw interface files."""

from __future__ import annotations

import calendar
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.config import Settings


_TUSHARE_API = "https://api.tushare.pro"
logger = logging.getLogger(__name__)

# Official per-minute limits: https://tushare.pro/document/1?doc_id=290
# 120 points → 50/min; 2000+ → 200/min; 5000+ → 500/min.
# index_weight requires 2000 points; error text cites 200/min (doc_id=108).
_TUSHARE_TIER_LIMITS: dict[str, int] = {
    "120": 50,
    "2000": 200,
    "5000": 500,
}
_DEFAULT_TIER = "2000"
_DEFAULT_MAX_CALLS_PER_MINUTE = 180  # 200/min official, 10% headroom
_RATE_LIMIT_PATTERN = re.compile(r"频率超限|每分钟最多访问")
_MAX_RATE_LIMIT_RETRIES = 5


class TushareRateLimiter:
    """Sliding-window limiter aligned with Tushare Pro doc_id=290."""

    def __init__(self, max_calls_per_minute: int = _DEFAULT_MAX_CALLS_PER_MINUTE) -> None:
        if max_calls_per_minute < 1:
            raise ValueError("max_calls_per_minute must be positive")
        self.max_calls_per_minute = max_calls_per_minute
        self.min_interval_seconds = 60.0 / max_calls_per_minute
        self._window: deque[float] = deque()
        self._last_call_at: float | None = None

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


_GLOBAL_RATE_LIMITER = TushareRateLimiter()


def _is_rate_limit_error(message: str) -> bool:
    return bool(_RATE_LIMIT_PATTERN.search(message))


class TushareDownloadService:
    def __init__(self, settings: Settings, *, rate_limiter: TushareRateLimiter | None = None) -> None:
        self.settings = settings
        self._rate_limiter = rate_limiter or _GLOBAL_RATE_LIMITER

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

    def download_index_weight(self, index_code: str, start_date: str, end_date: str) -> dict[str, Any]:
        api_code = self._index_weight_api_code(index_code)
        today = self._today_yyyymmdd()
        if end_date > today:
            end_date = today
        file_stem = api_code.replace(".", "_")
        output = self.settings.raw_root / "index_weight" / f"index_weight_{file_stem}.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for month_start, month_end in self._month_ranges(start_date, end_date):
            rows.extend(
                self._call(
                    "index_weight",
                    {"index_code": api_code, "start_date": month_start, "end_date": month_end},
                )
            )
        if not rows and existing.empty:
            raise ValueError(f"tushare returned no index_weight rows for {index_code}")
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).drop_duplicates(
            ["index_code", "con_code", "trade_date"], keep="last"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        relative = f"raw/index_weight/index_weight_{file_stem}.parquet"
        result: dict[str, Any] = {"rows": len(rows), "target": relative, "file_name": output.name}
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
        rows = self._call("index_daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        output = self.settings.raw_root / "index_daily/index_daily_000300_SH.parquet"
        existing = pq.read_table(output).to_pandas() if output.exists() else pd.DataFrame()
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="last")
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output)
        return {"rows": len(rows), "target": "raw/index_daily/index_daily_000300_SH.parquet"}

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

