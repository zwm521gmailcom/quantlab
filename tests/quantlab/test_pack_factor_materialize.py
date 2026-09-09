from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK
from quantlab.services.canonical_pack_factors import (
    attach_pack_factor_columns,
    default_sidecar_path,
    materialize_canonical_pack_factors,
)


def _tiny_canonical(path: Path, days: int = 5) -> None:
    codes = ["000001.SZ", "600000.SH"]
    rows: dict[str, list] = {
        "ts_code": [],
        "trade_date": [],
        "hfq_open": [],
        "hfq_high": [],
        "hfq_low": [],
        "hfq_close": [],
        "amount": [],
        "vol": [],
        "pe_ttm": [],
        "float_market_cap": [],
        "total_market_cap": [],
        "dividend_yield_ratio": [],
        "turn": [],
    }
    for day in range(days):
        date = f"201901{day + 1:02d}"
        for index, code in enumerate(codes):
            close = 10.0 + day + index
            rows["ts_code"].append(code)
            rows["trade_date"].append(date)
            rows["hfq_open"].append(close - 0.2)
            rows["hfq_high"].append(close + 0.3)
            rows["hfq_low"].append(close - 0.4)
            rows["hfq_close"].append(close)
            rows["amount"].append(1000.0 * (day + 1))
            rows["vol"].append(100.0 * (day + 1))
            rows["pe_ttm"].append(8.0 + index)
            rows["float_market_cap"].append(1_000_000.0 * (index + 1))
            rows["total_market_cap"].append(2_000_000.0 * (index + 1))
            rows["dividend_yield_ratio"].append(0.02 * (index + 1))
            rows["turn"].append(1.5 + index)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows), path)


def test_materialize_writes_all_pack_fields_without_changing_canonical(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=5)
    before = canonical.read_bytes()

    result = materialize_canonical_pack_factors(canonical)

    sidecar = Path(result["path"])
    assert sidecar == default_sidecar_path(canonical)
    assert sidecar.is_file()
    assert canonical.read_bytes() == before
    names = set(pq.ParquetFile(sidecar).schema_arrow.names)
    assert {"ts_code", "trade_date"} <= names
    assert {item.field for item in CANONICAL_FACTOR_PACK} <= names
    table = pq.read_table(sidecar).to_pandas()
    market = pq.read_table(canonical, columns=["ts_code", "trade_date", "hfq_close"]).to_pandas()
    merged = table.merge(market, on=["ts_code", "trade_date"])
    first = merged.loc[merged["ts_code"] == "000001.SZ"].sort_values("trade_date")
    assert pd.isna(first["momentum_1"].iloc[0])
    expected = first["hfq_close"].iloc[1] / first["hfq_close"].iloc[0] - 1
    assert abs(first["momentum_1"].iloc[1] - expected) < 1e-9
    assert len(table) == 10


def test_attach_joins_sidecar_values_onto_short_backtest_window(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=5)
    materialize_canonical_pack_factors(canonical)
    frame = pq.read_table(canonical).to_pandas().iloc[-2:].copy()
    frame["instrument"] = frame["ts_code"]
    frame["date"] = frame["trade_date"]
    attached = attach_pack_factor_columns(
        frame,
        [{"factor_id": "factor_momentum_1", "field": "momentum_1", "version_id": "v1"}],
        default_sidecar_path(canonical),
    )
    assert "momentum_1" in attached.columns
    assert attached["momentum_1"].notna().all()
