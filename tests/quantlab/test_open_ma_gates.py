from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quantlab.services.backtest_job import resolve_open_dates
from quantlab.services.backtest_workbench import normalize_open_ma_gates
from quantlab.services.offline_rl.fingerprint import config_fingerprint


def test_normalize_open_ma_gates_dedupes_and_rejects_junk():
    assert normalize_open_ma_gates(None) == []
    assert normalize_open_ma_gates([]) == []
    assert normalize_open_ma_gates(
        [
            {"code": "000001.sh", "window": 30},
            {"code": "000001.SH", "window": 30},
            {"code": "000300.SH", "window": 10},
        ]
    ) == [
        {"code": "000001.SH", "window": 30},
        {"code": "000300.SH", "window": 10},
    ]
    with pytest.raises(ValueError, match="列表"):
        normalize_open_ma_gates({"code": "000001.SH", "window": 30})
    with pytest.raises(ValueError, match="指数代码"):
        normalize_open_ma_gates([{"code": "上证", "window": 30}])
    with pytest.raises(ValueError, match="窗口"):
        normalize_open_ma_gates([{"code": "000001.SH", "window": 1}])


def _write_index(raw_root: Path, code: str, dates: list[str], closes: list[float]) -> None:
    slug = code.replace(".", "_")
    path = raw_root / "index_daily" / f"index_daily_{slug}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"ts_code": [code] * len(dates), "trade_date": dates, "close": closes}),
        path,
    )


def test_resolve_open_dates_intersects_year_line_and_short_ma(tmp_path: Path) -> None:
    dates = [f"2020010{i}" for i in range(1, 7)]
    raw = tmp_path / "raw"
    _write_index(raw, "000300.SH", dates, [10, 11, 12, 5, 6, 7])
    _write_index(raw, "000001.SH", dates, [10, 11, 12, 13, 14, 4])
    config = {
        "benchmark": "000300.SH",
        "open_when_benchmark_gt_ma200": False,
        "open_ma_gates": [
            {"code": "000300.SH", "window": 2},
            {"code": "000001.SH", "window": 2},
        ],
        "test": {"date_from": "2020-01-01", "date_to": "2020-01-06"},
    }
    allowed = resolve_open_dates(None, config, raw_root=raw)
    assert allowed == {"20200102", "20200103", "20200105"}


def test_resolve_open_dates_missing_index_raises(tmp_path: Path) -> None:
    config = {
        "open_when_benchmark_gt_ma200": False,
        "open_ma_gates": [{"code": "000001.SH", "window": 30}],
        "test": {"date_from": "2020-01-02", "date_to": "2020-12-31"},
    }
    with pytest.raises(ValueError, match="000001.SH"):
        resolve_open_dates(None, config, raw_root=tmp_path / "raw")


def test_fingerprint_includes_open_ma_gates() -> None:
    base = {
        "factor_versions": [{"field": "sleeve_c150_st_w2dv03"}],
        "top_n": 4,
        "weighting": "equal",
        "holding_days": 2,
        "rebalance_every": 2,
        "open_when_benchmark_gt_ma200": True,
        "test": {"filter": {"expressions": []}},
        "train": {"filter": {"expressions": []}},
        "model": {"kind": "factor_rank"},
    }
    with_gate = dict(base, open_ma_gates=[{"code": "000001.SH", "window": 30}])
    assert config_fingerprint(base) != config_fingerprint(with_gate)
    swapped = dict(base, open_ma_gates=[{"window": 30, "code": "000001.SH"}])
    assert config_fingerprint(with_gate) == config_fingerprint(swapped)
