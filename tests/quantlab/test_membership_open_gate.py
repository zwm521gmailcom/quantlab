from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quantlab.services.backtest_job import resolve_membership_open_allow
from quantlab.services.offline_rl.fingerprint import config_fingerprint
from quantlab.services.portfolio import run_portfolio


def _write_weights(raw: Path, index_code: str, members: list[str], snap: str = "20191231") -> None:
    slug = index_code.replace(".", "_")
    path = raw / "index_weight" / f"index_weight_{slug}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "index_code": [index_code] * len(members),
                "con_code": members,
                "trade_date": [snap] * len(members),
                "weight": [1.0] * len(members),
            }
        ),
        path,
    )


def _write_index(raw: Path, code: str, dates: list[str], closes: list[float]) -> None:
    slug = code.replace(".", "_")
    path = raw / "index_daily" / f"index_daily_{slug}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"ts_code": [code] * len(dates), "trade_date": dates, "close": closes}),
        path,
    )


def _config(**extra):
    payload = {
        "open_gate_by_membership": True,
        "membership_ma_window": 2,
        "open_when_benchmark_gt_ma200": True,
        "test": {"date_from": "2020-01-01", "date_to": "2020-01-06"},
    }
    payload.update(extra)
    return payload


def test_membership_gate_splits_300_and_500(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    dates = [f"2020010{i}" for i in range(1, 7)]
    _write_weights(raw, "000300.SH", ["AAA.SZ"])
    _write_weights(raw, "000905.SH", ["BBB.SZ"])
    _write_index(raw, "000300.SH", dates, [10, 11, 12, 5, 6, 7])
    _write_index(raw, "000905.SH", dates, [10, 11, 12, 13, 14, 4])
    allow = resolve_membership_open_allow(None, _config(), raw_root=raw)
    assert allow is not None
    by_day = {day: set(names) for day, names in allow.items()}
    assert by_day["20200102"] == {"AAA.SZ", "BBB.SZ"}
    assert by_day["20200103"] == {"AAA.SZ", "BBB.SZ"}
    assert by_day["20200104"] == {"BBB.SZ"}
    assert by_day["20200105"] == {"AAA.SZ", "BBB.SZ"}
    assert by_day["20200106"] == {"AAA.SZ"}


def test_membership_overlap_follows_csi300(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    dates = [f"2020010{i}" for i in range(1, 7)]
    _write_weights(raw, "000300.SH", ["AAA.SZ"])
    _write_weights(raw, "000905.SH", ["AAA.SZ"])
    _write_index(raw, "000300.SH", dates, [10, 11, 12, 5, 6, 7])
    _write_index(raw, "000905.SH", dates, [10, 11, 12, 13, 14, 4])
    allow = resolve_membership_open_allow(None, _config(), raw_root=raw)
    by_day = {day: set(names) for day, names in allow.items()}
    assert "AAA.SZ" in by_day["20200106"]
    assert "AAA.SZ" not in by_day.get("20200104", [])


def test_membership_gate_missing_csi500_daily_raises(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_weights(raw, "000300.SH", ["AAA.SZ"])
    _write_weights(raw, "000905.SH", ["BBB.SZ"])
    dates = [f"2020010{i}" for i in range(1, 7)]
    _write_index(raw, "000300.SH", dates, [10, 11, 12, 13, 14, 15])
    with pytest.raises(ValueError, match="000905.SH"):
        resolve_membership_open_allow(None, _config(), raw_root=raw)


def test_fingerprint_includes_membership_flag() -> None:
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
    flagged = dict(base, open_gate_by_membership=True)
    assert config_fingerprint(base) != config_fingerprint(flagged)


def test_portfolio_filters_names_by_membership_allow() -> None:
    dates = ["20200102", "20200103", "20200106"]
    rows = []
    for date in dates:
        for name, close in (("AAA.SZ", 10.0), ("BBB.SZ", 11.0)):
            rows.append(
                {
                    "date": date,
                    "instrument": name,
                    "open": close,
                    "close": close,
                    "up_limit": close + 2,
                    "down_limit": close - 2,
                    "suspended": False,
                    "score": 1.0 if name == "AAA.SZ" else 2.0,
                }
            )
    frame = pd.DataFrame(rows)
    predictions = frame[["date", "instrument", "score"]].copy()
    config = {
        "test": {"date_from": "20200102", "date_to": "20200106"},
        "top_n": 1,
        "holding_days": 1,
        "rebalance_every": 1,
        "initial_capital": 1_000_000,
        "buy_price": "open",
        "sell_price": "close",
        "open_gate_by_membership": True,
        "membership_open_allow": {"20200102": ["AAA.SZ"]},
        "buy_fee_rate": 0,
        "sell_fee_rate": 0,
        "stamp_tax_rate": 0,
        "slippage": 0,
        "lot_size": 100,
        "buy_fee_minimum": 5,
        "sell_fee_minimum": 5,
        "unfilled_policy": "keep_cash",
        "weighting": "equal",
    }
    trades, _curve = run_portfolio(frame, predictions, config)
    filled = [t for t in trades if t.get("status") == "filled"]
    assert filled
    assert all(t["instrument"] == "AAA.SZ" for t in filled)
    assert all(str(t.get("signal_date") or t.get("buy_date")).replace("-", "")[:8] != "20200103" or t["instrument"] == "AAA.SZ" for t in filled)
    bought_on = {str(t.get("signal_date") or t.get("buy_date")).replace("-", "")[:8] for t in filled}
    assert "20200102" in bought_on
    assert "20200103" not in bought_on
