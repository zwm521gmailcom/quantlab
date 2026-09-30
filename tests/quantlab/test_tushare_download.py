from pathlib import Path

from quantlab.config import Settings


def _settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=tmp_path / "data" / "raw",
    )


def test_catalog_lists_index_weight_doc_96():
    from quantlab.services.catalog import DatasetCatalog

    assert DatasetCatalog._RAW_INTERFACE_DOCS["index_weight"] == 96
    assert DatasetCatalog._RAW_INTERFACE_NAMES["index_weight"] == "指数成分和权重"


def test_catalog_lists_suspend_d_doc_214():
    from quantlab.services.catalog import DatasetCatalog

    assert DatasetCatalog._RAW_INTERFACE_DOCS["suspend_d"] == 214
    assert DatasetCatalog._RAW_INTERFACE_NAMES["suspend_d"] == "每日停复牌信息"


def test_download_index_weight_writes_monthly_rows(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(
        svc,
        "_call",
        lambda api, params: [
            {
                "index_code": params["index_code"],
                "con_code": "000001.SZ",
                "trade_date": "20180903",
                "weight": 0.86,
            }
        ],
    )
    out = svc.download_index_weight("000300.SH", "20180901", "20180930")
    assert out["rows"] == 1
    assert "index_weight_000300_SH.parquet" in out["target"]


def test_download_index_weight_alias_writes_canonical_parquet(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(
        svc,
        "_call",
        lambda api, params: [
            {
                "index_code": params["index_code"],
                "con_code": "000001.SZ",
                "trade_date": "20180903",
                "weight": 0.86,
            }
        ],
    )
    out = svc.download_index_weight("399300.SZ", "20180901", "20180930")
    assert out["target"] == "raw/index_weight/index_weight_000300_SH.parquet"
    assert out["mapped_from"] == "399300.SZ"
    assert "399300.SZ" in out["note"]


def test_download_index_weight_clamps_future_end_date(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    calls: list[dict] = []

    def fake_call(api, params):
        calls.append(dict(params))
        return [
            {
                "index_code": params["index_code"],
                "con_code": "000001.SZ",
                "trade_date": params["end_date"],
                "weight": 0.86,
            }
        ]

    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(svc, "_call", fake_call)
    monkeypatch.setattr(svc, "_today_yyyymmdd", lambda: "20240915")
    out = svc.download_index_weight("000300.SH", "20240801", "20991231")
    assert calls
    assert max(c["end_date"] for c in calls) <= "20240915"
    assert any(c["end_date"] == "20240915" for c in calls)
    assert out["calls"] >= 1


def test_index_weight_chunk_ranges_grow_with_small_constituents(tmp_path):
    from quantlab.services.tushare_download import TushareDownloadService

    svc = TushareDownloadService(_settings(tmp_path))
    # ~50 cons → many months per call
    chunks = svc._index_weight_chunk_ranges("20161010", "20260831", cons_hint=50)
    assert len(chunks) < 20
    # ~500 cons → fewer months per call, more chunks
    dense = svc._index_weight_chunk_ranges("20161010", "20260831", cons_hint=500)
    assert len(dense) > len(chunks)


def test_data_center_raw_dialog_has_index_weight_download_button():
    js = Path("quantlab/web/assets/data/datasets.js").read_text()
    assert "下载成分权重" in js
    assert "appendIndexWeightDownload" in js
    assert "index_weight_{指数代码}" in js or "index_weight_000300_SH" in js


def test_data_center_raw_dialog_has_suspend_d_download_button():
    js = Path("quantlab/web/assets/data/datasets.js").read_text()
    api_src = "".join(
        p.read_text(encoding="utf-8")
        for p in Path("quantlab/api").rglob("*.py")
        if not p.name.startswith("._")
    )
    assert "下载停复牌" in js
    assert "appendSuspendDDownload" in js
    assert "/api/raw/download/suspend_d" in js
    assert "suspend_d.parquet" in js
    assert "/api/raw/download/suspend_d" in api_src


def test_download_suspend_d_writes_monthly_rows_and_clamps_future_end(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    calls: list[dict] = []

    def fake_call(api, params):
        assert api == "suspend_d"
        calls.append(dict(params))
        return [
            {
                "ts_code": "000001.SZ",
                "trade_date": params["end_date"],
                "suspend_type": "S",
                "suspend_timing": None,
            }
        ]

    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(svc, "_call", fake_call)
    monkeypatch.setattr(svc, "_today_yyyymmdd", lambda: "20240915")
    out = svc.download_suspend_d("20240801", "20991231")
    assert out["target"] == "raw/suspend_d/suspend_d.parquet"
    assert out["rows"] == 2
    assert calls[0]["start_date"] == "20240801"
    assert calls[-1]["end_date"] == "20240915"
    assert (tmp_path / "data" / "raw" / "suspend_d" / "suspend_d.parquet").is_file()


def test_raw_items_lists_suspend_d_before_download(tmp_path):
    from quantlab.config import Settings
    from quantlab.repositories.database import Database
    from quantlab.services.catalog import DatasetCatalog

    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=raw_root,
    )
    database = Database(settings.database_path)
    database.initialize()
    catalog = DatasetCatalog(settings, database)
    item = next(row for row in catalog.raw_items() if row["name"] == "suspend_d")
    assert item["name_cn"] == "每日停复牌信息"
    assert item["path_alias"] == "raw/suspend_d"
    assert item["file_count"] == 0
    assert item["quality_status"] == "warning"
    assert item["tushare_url"] == "https://tushare.pro/document/2?doc_id=214"
    assert item["asset_class"] == "cn_a"
    assert item["asset_class_label"] == "A股"


def test_raw_items_lists_index_weight_before_download(tmp_path):
    from quantlab.config import Settings
    from quantlab.repositories.database import Database
    from quantlab.services.catalog import DatasetCatalog

    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=raw_root,
    )
    database = Database(settings.database_path)
    database.initialize()
    catalog = DatasetCatalog(settings, database)
    items = catalog.raw_items()
    index_weight = next(item for item in items if item["name"] == "index_weight")
    assert index_weight["name_cn"] == "指数成分和权重"
    assert index_weight["path_alias"] == "raw/index_weight"
    assert index_weight["file_count"] == 0
    assert index_weight["quality_status"] == "warning"


def test_raw_interface_files_allows_empty_index_weight(tmp_path):
    from quantlab.config import Settings
    from quantlab.repositories.database import Database
    from quantlab.services.catalog import DatasetCatalog

    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=raw_root,
    )
    database = Database(settings.database_path)
    database.initialize()
    catalog = DatasetCatalog(settings, database)
    payload = catalog.raw_interface_files("index_weight")
    assert payload["total"] == 0
    assert payload["items"] == []


def test_resolve_tushare_quota_follows_doc290():
    from quantlab.services.tushare_download import resolve_tushare_quota

    unset = resolve_tushare_quota(None)
    assert unset.configured is False
    assert unset.calls_per_minute == 50
    assert unset.daily_limit_per_api == 8000
    low = resolve_tushare_quota(120)
    assert low.tier == 120
    assert low.calls_per_minute == 50
    mid = resolve_tushare_quota(2000)
    assert mid.tier == 2000
    assert mid.calls_per_minute == 200
    assert mid.daily_limit_per_api == 100_000
    high = resolve_tushare_quota(5000)
    assert high.calls_per_minute == 500
    assert high.daily_limit_per_api is None


def test_daily_budget_stops_at_doc290_limit(tmp_path):
    from quantlab.services.tushare_download import TushareDailyBudget

    path = tmp_path / "budget.json"
    budget = TushareDailyBudget(path, daily_limit_per_api=2)
    budget.consume("index_weight")
    budget.consume("index_weight")
    try:
        budget.consume("index_weight")
        assert False, "expected daily limit error"
    except ValueError as error:
        assert "daily limit" in str(error)
    budget.consume("index_daily")


def test_download_service_applies_saved_points_quota(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    settings = _settings(tmp_path)
    (settings.runtime_root / "config").mkdir(parents=True, exist_ok=True)
    (settings.runtime_root / "config/tushare_points.json").write_text('{"points": 2000}', encoding="utf-8")
    svc = TushareDownloadService(settings)
    assert svc._rate_limiter.max_calls_per_minute == 200
    assert svc._daily_budget.daily_limit_per_api == 100_000


def test_rate_limiter_enforces_sliding_window(monkeypatch):
    from quantlab.services.tushare_download import TushareRateLimiter

    sleeps: list[float] = []
    clock = [10.0]

    monkeypatch.setattr("quantlab.services.tushare_download.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("quantlab.services.tushare_download.time.monotonic", lambda: clock[0])

    limiter = TushareRateLimiter(max_calls_per_minute=2)
    limiter.min_interval_seconds = 0
    limiter.wait()
    limiter.wait()
    limiter.wait()

    assert sleeps
    assert sleeps[-1] >= 50.0


def test_call_retries_on_rate_limit_message(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService, TushareRateLimiter

    monkeypatch.setattr("quantlab.services.tushare_download.time.sleep", lambda _seconds: None)
    responses = [
        {"code": 1, "msg": "抱歉，您访问接口(index_weight)频率超限(200次/分钟)"},
        {
            "code": 0,
            "data": {
                "fields": ["index_code", "con_code", "trade_date", "weight"],
                "items": [["000300.SH", "000001.SZ", "20180903", 0.86]],
            },
        },
    ]

    def fake_urlopen(_request, timeout=60):
        body = responses.pop(0)

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                import json

                return json.dumps(body).encode("utf-8")

        return _Resp()

    monkeypatch.setattr("quantlab.services.tushare_download.urllib.request.urlopen", fake_urlopen)
    svc = TushareDownloadService(_settings(tmp_path), rate_limiter=TushareRateLimiter(max_calls_per_minute=500))
    monkeypatch.setattr(svc, "_token", lambda: "test-token")
    rows = svc._call("index_weight", {"index_code": "000300.SH", "start_date": "20180901", "end_date": "20180930"})
    assert rows[0]["con_code"] == "000001.SZ"


def test_refresh_index_daily_writes_per_code_parquet(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    svc = TushareDownloadService(_settings(tmp_path))
    monkeypatch.setattr(
        svc,
        "_call",
        lambda api, params: [
            {
                "ts_code": params["ts_code"],
                "trade_date": "20180102",
                "close": 3400.1,
            }
        ],
    )
    out = svc.refresh_index_daily("000001.SH", "20180101", "20180131")
    assert out["target"] == "raw/index_daily/index_daily_000001_SH.parquet"
    assert (tmp_path / "data/raw/index_daily/index_daily_000001_SH.parquet").is_file()
    assert not (tmp_path / "data/raw/index_daily/index_daily_000300_SH.parquet").exists()
    csi = svc.refresh_index_daily("000300.SH", "20180101", "20180131")
    assert csi["target"] == "raw/index_daily/index_daily_000300_SH.parquet"


from datetime import datetime
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _calendar(tmp_path: Path, opens: list[str]) -> None:
    _write(
        tmp_path / "data/raw/trade_cal/calendar.parquet",
        [{"exchange": "SSE", "cal_date": day, "is_open": 1} for day in opens],
    )


def _shanghai(hour: int, day: str = "20260930") -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), hour, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


_TAIL_OPENS = ["20161010", "20161011", "20260921", "20260922", "20260928", "20260929", "20260930", "20261231"]


def _record(svc, monkeypatch, rows_for):
    calls: list[tuple[str, dict]] = []

    def _call(api, params):
        calls.append((api, dict(params)))
        return rows_for(api, params)

    monkeypatch.setattr(svc, "_call", _call)
    monkeypatch.setattr("quantlab.services.tushare_download.time.sleep", lambda _seconds: None)
    return calls


def test_backfill_cutoff_excludes_today_before_1600_and_includes_it_after(tmp_path):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    svc = TushareDownloadService(_settings(tmp_path))
    assert svc.backfill_cutoff(_shanghai(14)) == "20260929"
    assert svc.backfill_cutoff(_shanghai(16)) == "20260930"


def _date_file_case(tmp_path, monkeypatch, api: str):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    raw = tmp_path / "data/raw" / api
    _write(raw / "20161010.parquet", [{"ts_code": "000001.SZ", "trade_date": "20161010"}])
    _write(raw / "20260921.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260921"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda name, params: [{"ts_code": "000001.SZ", "trade_date": params["trade_date"]}],
    )
    result = svc.backfill(api, now=_shanghai(14))
    assert [params["trade_date"] for name, params in calls if name == api] == ["20260922", "20260928", "20260929"]
    assert {name for name, _params in calls} == {api}
    assert (raw / "20260922.parquet").is_file()
    assert not (raw / "20161011.parquet").exists()
    assert not (raw / "20260930.parquet").exists()
    assert result["interface"] == api


def test_daily_backfill_continues_only_after_the_latest_file(tmp_path, monkeypatch):
    _date_file_case(tmp_path, monkeypatch, "daily")


def test_daily_basic_backfill_uses_its_own_api_and_directory(tmp_path, monkeypatch):
    _date_file_case(tmp_path, monkeypatch, "daily_basic")
    assert not (tmp_path / "data/raw/daily").exists()


def test_adj_factor_backfill_uses_its_own_api_and_directory(tmp_path, monkeypatch):
    _date_file_case(tmp_path, monkeypatch, "adj_factor")


def test_stk_limit_backfill_uses_its_own_api_and_directory(tmp_path, monkeypatch):
    _date_file_case(tmp_path, monkeypatch, "stk_limit")


def test_moneyflow_backfill_packs_adjacent_gaps_splits_on_existing_days_and_rejects_cap(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    opens = ["20260921", "20260922", "20260923", "20260924", "20261231"]
    _calendar(tmp_path, opens)
    raw = tmp_path / "data/raw"
    for day in ("20260922", "20260923"):
        _write(raw / "daily" / f"{day}.parquet", [{"ts_code": f"{index:06d}.SZ", "trade_date": day} for index in range(10)])
    _write(raw / "moneyflow" / "20260921.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260921"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [
            {"ts_code": "000001.SZ", "trade_date": day}
            for day in ([params["trade_date"]] if "trade_date" in params else ["20260922", "20260923"])
        ],
    )
    svc.backfill("moneyflow", now=_shanghai(10, "20260924"))
    assert calls == [("moneyflow", {"start_date": "20260922", "end_date": "20260923"})]

    _write(raw / "moneyflow" / "20260922.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260922"}])
    (raw / "moneyflow" / "20260921.parquet").unlink()
    (raw / "moneyflow" / "20260923.parquet").unlink()
    calls.clear()
    svc.download_moneyflow("20260921", "20260923")
    assert ("moneyflow", {"trade_date": "20260921"}) in calls
    assert ("moneyflow", {"trade_date": "20260923"}) in calls
    assert not any(params.get("start_date") == "20260921" and params.get("end_date") == "20260923" for _api, params in calls)

    def capped(_api, params):
        calls.append(("moneyflow", dict(params)))
        return [{"ts_code": f"{index:06d}.SZ", "trade_date": params.get("trade_date", "20260924")} for index in range(6000)]

    monkeypatch.setattr(svc, "_call", capped)
    calls.clear()
    try:
        svc.download_moneyflow("20260924", "20260924")
    except ValueError as error:
        assert "6000" in str(error)
    else:
        raise AssertionError("truncated moneyflow should be refused")
    assert not (raw / "moneyflow" / "20260924.parquet").exists()


def test_suspend_d_backfill_requests_only_months_after_the_stored_date(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, ["20260921", "20260922", "20260929", "20261231"])
    path = tmp_path / "data/raw/suspend_d/suspend_d.parquet"
    _write(path, [{"ts_code": "000001.SZ", "trade_date": "20260921", "suspend_type": "S"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [{"ts_code": "000001.SZ", "trade_date": "20260922", "suspend_type": "S"}],
    )
    svc.backfill("suspend_d", now=_shanghai(14))
    assert calls == [("suspend_d", {"start_date": "20260922", "end_date": "20260929"})]
    stored = pq.read_table(path).to_pylist()
    assert sum(1 for row in stored if row["suspend_type"] == "S" and row["trade_date"] == "20260922") == 1


def test_index_daily_backfill_skips_current_files_and_does_not_create_indexes(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    folder = tmp_path / "data/raw/index_daily"
    _write(folder / "index_daily_000300_SH.parquet", [{"ts_code": "000300.SH", "trade_date": "20260921"}])
    _write(folder / "index_daily_000905_SH.parquet", [{"ts_code": "000905.SH", "trade_date": "20260929"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [{"ts_code": params["ts_code"], "trade_date": "20260922", "close": 1}],
    )
    result = svc.backfill("index_daily", now=_shanghai(14))
    assert result["calls"] == 1
    assert calls == [("index_daily", {"ts_code": "000300.SH", "start_date": "20260922", "end_date": "20260929"})]
    assert not (folder / "index_daily_000852_SH.parquet").exists()


def test_index_weight_backfill_maps_alias_skips_current_month_end_and_splits_at_row_cap(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, ["20260731", "20260831", "20260929", "20260930", "20261231"])
    folder = tmp_path / "data/raw/index_weight"
    _write(folder / "index_weight_000905_SH.parquet", [{"index_code": "000905.SH", "con_code": "000001.SZ", "trade_date": "20260831"}])
    _write(folder / "index_weight_399300_SZ.parquet", [{"index_code": "399300.SZ", "con_code": "000001.SZ", "trade_date": "20260731"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [{"index_code": params["index_code"], "con_code": "000001.SZ", "trade_date": "20260831", "weight": 1}],
    )
    result = svc.backfill("index_weight", now=_shanghai(14))
    assert result["target_date"] == "20260831"
    assert result["calls"] == 1
    assert {params["index_code"] for _api, params in calls} == {"000300.SH"}
    assert (folder / "index_weight_000300_SH.parquet").is_file()

    fat = [{"index_code": "000300.SH", "con_code": f"{index:06d}.SZ", "trade_date": "20260831"} for index in range(7000)]
    recorded = []

    def capture(_api, params):
        recorded.append(dict(params))
        return list(fat)

    monkeypatch.setattr(svc, "_call", capture)
    svc.download_index_weight("000300.SH", "20260801", "20260831")
    assert len(recorded) >= 3
    assert all(item["index_code"] == "000300.SH" for item in recorded)


def test_stk_week_month_adj_backfill_requests_both_frequencies(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, ["20260831", "20260918", "20260929", "20261231"])
    folder = tmp_path / "data/raw/stk_week_month_adj"
    _write(folder / "week_20260918.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260918", "freq": "week"}])
    _write(folder / "month_20260831.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260831", "freq": "month"}])
    _write(tmp_path / "data/raw/daily/20260929.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260929"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [{"ts_code": "000001.SZ", "trade_date": params.get("trade_date", "20260929"), "freq": params["freq"]}],
    )
    svc.backfill("stk_week_month_adj", now=_shanghai(14))
    freqs = {params["freq"] for _api, params in calls}
    assert freqs == {"week", "month"}
    assert (folder / "week_20260929.parquet").is_file()
    assert (folder / "month_20260929.parquet").is_file()
    calls.clear()
    again = svc.backfill("stk_week_month_adj", now=_shanghai(14))
    assert again["calls"] == 0
    assert calls == []


def test_stock_basic_backfill_refreshes_three_statuses_without_empty_overwrite(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    path = tmp_path / "data/raw/stock_basic/stock_basic_D.parquet"
    _write(path, [{"ts_code": "OLD.SZ", "list_status": "D"}])
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(
        svc,
        monkeypatch,
        lambda _api, params: [] if params["list_status"] == "D" else [{"ts_code": "000001.SZ", "list_status": params["list_status"]}],
    )
    svc.backfill("stock_basic", now=_shanghai(14))
    assert [params["list_status"] for _api, params in calls] == ["L", "D", "P"]
    assert pq.read_table(path).to_pylist() == [{"ts_code": "OLD.SZ", "list_status": "D"}]
    assert (tmp_path / "data/raw/stock_basic/stock_basic_L.parquet").is_file()


def test_index_basic_backfill_does_not_replace_file_with_empty_response(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    path = tmp_path / "data/raw/index_basic/index_basic.parquet"
    _write(path, [{"ts_code": "000300.SH", "name": "沪深300"}])
    before = path.read_bytes()
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(svc, monkeypatch, lambda _api, _params: [])
    try:
        svc.backfill("index_basic", now=_shanghai(14))
    except ValueError as error:
        assert "no index_basic" in str(error)
    else:
        raise AssertionError("empty index_basic should fail")
    assert calls == [("index_basic", {})]
    assert path.read_bytes() == before


def test_trade_cal_backfill_skips_when_year_is_covered_and_requests_sse_otherwise(tmp_path, monkeypatch):
    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    svc = TushareDownloadService(_settings(tmp_path))
    calls = _record(svc, monkeypatch, lambda _api, params: [{"exchange": "SSE", "cal_date": "20260930", "is_open": 1}])
    covered = svc.backfill("trade_cal", now=_shanghai(14))
    assert covered["calls"] == 0
    assert calls == []

    short = tmp_path / "short"
    _calendar(short, ["20260901"])
    # _calendar writes under tmp_path; rebuild the short root by using a dedicated settings tree.
    from quantlab.config import Settings

    settings = Settings(
        project_root=short,
        data_root=short / "data",
        calibration_root=short / "calibration",
        runtime_root=short / "runtime",
        raw_root=short / "data" / "raw",
    )
    _write(
        short / "data/raw/trade_cal/calendar.parquet",
        [{"exchange": "SSE", "cal_date": "20260901", "is_open": 1}],
    )
    other = TushareDownloadService(settings)
    calls = _record(other, monkeypatch, lambda _api, params: [{"exchange": params["exchange"], "cal_date": "20261231", "is_open": 0}])
    other.backfill("trade_cal", now=_shanghai(14))
    assert calls[0][0] == "trade_cal"
    assert calls[0][1]["exchange"] == "SSE"
    assert calls[0][1]["end_date"] == "20261231"


def test_second_backfill_is_rejected_while_one_is_running(tmp_path, monkeypatch):
    import threading

    from quantlab.services.tushare_download import TushareDownloadService

    _calendar(tmp_path, _TAIL_OPENS)
    _write(tmp_path / "data/raw/daily/20260921.parquet", [{"ts_code": "000001.SZ", "trade_date": "20260921"}])
    svc = TushareDownloadService(_settings(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def _call(_api, params):
        started.set()
        assert release.wait(3)
        return [{"ts_code": "000001.SZ", "trade_date": params["trade_date"]}]

    monkeypatch.setattr(svc, "_call", _call)
    monkeypatch.setattr("quantlab.services.tushare_download.time.sleep", lambda _seconds: None)
    box: dict[str, object] = {}

    def run():
        try:
            svc.backfill("daily", now=_shanghai(14))
        except Exception as error:  # noqa: BLE001
            box["error"] = error

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(3)
    try:
        svc.backfill("adj_factor", now=_shanghai(14))
    except ValueError as error:
        assert "已有补数在进行" in str(error)
    else:
        raise AssertionError("overlapping backfill should be rejected")
    release.set()
    worker.join(3)
    assert "error" not in box


def test_submit_backfill_reports_job_and_rejects_unknown_or_overlap(tmp_path, monkeypatch):
    import threading
    import time

    from quantlab.services.tushare_download import TushareDownloadService

    svc = TushareDownloadService(_settings(tmp_path))
    entered = threading.Event()
    release = threading.Event()

    def _one(interface, _now):
        entered.set()
        assert release.wait(3)
        return {"interface": interface, "done": 2, "failed": ["20260929"], "filled": ["20260922", "20260928", "20260929"], "rows": 4}

    monkeypatch.setattr(svc, "_backfill_one", _one)
    try:
        svc.submit_backfill("not-an-interface", now=_shanghai(14))
    except ValueError as error:
        assert "未知接口" in str(error)
    else:
        raise AssertionError("unknown interface should not start a job")
    assert svc._backfill_jobs == {}

    first = svc.submit_backfill("daily", now=_shanghai(14))
    assert first["status"] == "running"
    assert entered.wait(3)
    try:
        svc.submit_backfill("daily_basic", now=_shanghai(14))
    except ValueError as error:
        assert "已有补数在进行" in str(error)
    else:
        raise AssertionError("overlapping backfill should be rejected")
    release.set()
    job = {}
    for _ in range(50):
        job = svc.backfill_job(first["job_id"])
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "succeeded"
    assert job["summary"] == "补入 2 个交易日"
    assert job["failed_dates"] == ["20260929"]
    assert job["processed"] == 3
    try:
        svc.backfill_job("missing")
    except ValueError as error:
        assert "不存在" in str(error)
    else:
        raise AssertionError("missing job should be rejected")
