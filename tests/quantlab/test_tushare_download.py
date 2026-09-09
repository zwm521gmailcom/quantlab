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
    svc.download_index_weight("000300.SH", "20240801", "20991231")
    assert calls
    assert max(c["end_date"] for c in calls) <= "20240915"
    assert calls[-1]["start_date"] == "20240901"
    assert calls[-1]["end_date"] == "20240915"


def test_data_center_raw_dialog_has_index_weight_download_button():
    js = Path("quantlab/web/assets/data/datasets.js").read_text()
    assert "下载成分权重" in js
    assert "appendIndexWeightDownload" in js
    assert "index_weight_{指数代码}" in js or "index_weight_000300_SH" in js


def test_data_center_raw_dialog_has_suspend_d_download_button():
    js = Path("quantlab/web/assets/data/datasets.js").read_text()
    api_src = "".join(p.read_text(encoding="utf-8") for p in Path("quantlab/api").rglob("*.py"))
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
