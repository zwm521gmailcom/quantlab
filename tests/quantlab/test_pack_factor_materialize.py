from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK
from quantlab.services.canonical_pack_factors import (
    COMPOSITE_FIELDS,
    attach_pack_factor_columns,
    default_composite_sidecar_path,
    default_sidecar_path,
    materialize_canonical_pack_factors,
    materialize_composite_pack_factors,
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


def test_composite_sidecar_attaches_sum_of_existing_ranks(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=5)
    materialize_canonical_pack_factors(canonical)
    written = materialize_composite_pack_factors(default_sidecar_path(canonical))
    assert Path(written["path"]) == default_composite_sidecar_path(canonical)
    frame = pq.read_table(canonical).to_pandas().copy()
    frame["instrument"] = frame["ts_code"]
    frame["date"] = frame["trade_date"]
    attached = attach_pack_factor_columns(
        frame,
        [
            {
                "factor_id": "factor_sleeve_mv_div",
                "field": "sleeve_mv_div",
                "version_id": "v1",
            }
        ],
        default_sidecar_path(canonical),
    )
    assert "sleeve_mv_div" in attached.columns
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "total_mv_cs_rank", "div_yield_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, left_on=["ts_code", "trade_date"], right_on=["ts_code", "trade_date"])
    expected = merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"]
    assert (merged["sleeve_mv_div"] - expected).abs().max() < 1e-9
    composite_names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert set(COMPOSITE_FIELDS) <= composite_names


def test_composite_adds_momentum_cross_section_to_size_rank(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=15)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    frame = pq.read_table(canonical).to_pandas().copy()
    frame["instrument"] = frame["ts_code"]
    frame["date"] = frame["trade_date"]
    attached = attach_pack_factor_columns(
        frame,
        [
            {
                "factor_id": "factor_sleeve_mom_mv",
                "field": "sleeve_mom_mv",
                "version_id": "v1",
            }
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "momentum_10", "total_mv_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    mom_rank = merged["momentum_10"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    expected = mom_rank + merged["total_mv_cs_rank"]
    delta = (merged["sleeve_mom_mv"] - expected).abs()
    comparable = expected.notna() & merged["sleeve_mom_mv"].notna()
    assert comparable.any()
    assert delta[comparable].max() < 1e-9


def test_composite_price60_and_float_variants_match_source_ranks(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {"factor_id": "factor_sleeve_price60_mv_div", "field": "sleeve_price60_mv_div", "version_id": "v1"},
            {"factor_id": "factor_sleeve_price_float_div", "field": "sleeve_price_float_div", "version_id": "v1"},
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_60",
            "close_ts_rank_20",
            "total_mv_cs_rank",
            "float_mv_cs_rank",
            "div_yield_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    expected60 = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"]
    expected_float = merged["close_ts_rank_20"] + merged["float_mv_cs_rank"] + merged["div_yield_cs_rank"]
    delta60 = (merged["sleeve_price60_mv_div"] - expected60).abs()
    delta_float = (merged["sleeve_price_float_div"] - expected_float).abs()
    comparable60 = expected60.notna() & merged["sleeve_price60_mv_div"].notna()
    comparable_float = expected_float.notna() & merged["sleeve_price_float_div"].notna()
    assert comparable60.any()
    assert comparable_float.any()
    assert delta60[comparable60].max() < 1e-9
    assert delta_float[comparable_float].max() < 1e-9


def test_composite_inverts_pe_turnover_and_size_ranks(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=25)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {
                "factor_id": "factor_sleeve_price_mv_div_cheap",
                "field": "sleeve_price_mv_div_cheap",
                "version_id": "v1",
            },
            {
                "factor_id": "factor_sleeve_price_mv_div_quiet",
                "field": "sleeve_price_mv_div_quiet",
                "version_id": "v1",
            },
            {
                "factor_id": "factor_sleeve_price_small_div",
                "field": "sleeve_price_small_div",
                "version_id": "v1",
            },
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_20",
            "total_mv_cs_rank",
            "div_yield_cs_rank",
            "pe_ttm_cs_rank",
            "turn_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    expected_cheap = (
        merged["close_ts_rank_20"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + (1.0 - merged["pe_ttm_cs_rank"])
    )
    expected_quiet = (
        merged["close_ts_rank_20"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + (1.0 - merged["turn_cs_rank"])
    )
    expected_small = (
        merged["close_ts_rank_20"]
        + (1.0 - merged["total_mv_cs_rank"])
        + merged["div_yield_cs_rank"]
    )
    for actual, expected in (
        (merged["sleeve_price_mv_div_cheap"], expected_cheap),
        (merged["sleeve_price_mv_div_quiet"], expected_quiet),
        (merged["sleeve_price_small_div"], expected_small),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9


def test_composite_price60_ablation_drops_div_or_size_or_adds_cheap(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {"factor_id": "factor_sleeve_price60_mv", "field": "sleeve_price60_mv", "version_id": "v1"},
            {"factor_id": "factor_sleeve_price60_div", "field": "sleeve_price60_div", "version_id": "v1"},
            {
                "factor_id": "factor_sleeve_price60_mv_div_cheap",
                "field": "sleeve_price60_mv_div_cheap",
                "version_id": "v1",
            },
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_60",
            "total_mv_cs_rank",
            "div_yield_cs_rank",
            "pe_ttm_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    expected_mv = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"]
    expected_div = merged["close_ts_rank_60"] + merged["div_yield_cs_rank"]
    expected_cheap = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + (1.0 - merged["pe_ttm_cs_rank"])
    )
    for actual, expected in (
        (merged["sleeve_price60_mv"], expected_mv),
        (merged["sleeve_price60_div"], expected_div),
        (merged["sleeve_price60_mv_div_cheap"], expected_cheap),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9


def test_composite_price60_quiet_and_cheap_quiet(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {
                "factor_id": "factor_sleeve_price60_mv_div_quiet",
                "field": "sleeve_price60_mv_div_quiet",
                "version_id": "v1",
            },
            {
                "factor_id": "factor_sleeve_price60_mv_div_cheap_quiet",
                "field": "sleeve_price60_mv_div_cheap_quiet",
                "version_id": "v1",
            },
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_60",
            "total_mv_cs_rank",
            "div_yield_cs_rank",
            "pe_ttm_cs_rank",
            "turn_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    expected_quiet = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + (1.0 - merged["turn_cs_rank"])
    )
    expected_both = expected_quiet + (1.0 - merged["pe_ttm_cs_rank"])
    for actual, expected in (
        (merged["sleeve_price60_mv_div_quiet"], expected_quiet),
        (merged["sleeve_price60_mv_div_cheap_quiet"], expected_both),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9


def test_composite_price60_dividend_weights_keep_same_universe(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {"factor_id": "factor_sleeve_price60_mv_div0", "field": "sleeve_price60_mv_div0", "version_id": "v1"},
            {"factor_id": "factor_sleeve_price60_mv_div25", "field": "sleeve_price60_mv_div25", "version_id": "v1"},
            {"factor_id": "factor_sleeve_price60_mv_div50", "field": "sleeve_price60_mv_div50", "version_id": "v1"},
            {"factor_id": "factor_sleeve_price60_mv_div", "field": "sleeve_price60_mv_div", "version_id": "v1"},
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "total_mv_cs_rank", "div_yield_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    universe = merged["sleeve_price60_mv_div"].notna()
    assert universe.equals(merged["sleeve_price60_mv_div0"].notna())
    assert universe.equals(merged["sleeve_price60_mv_div25"].notna())
    assert universe.equals(merged["sleeve_price60_mv_div50"].notna())
    expected0 = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + 0.0 * merged["div_yield_cs_rank"]
    expected25 = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + 0.25 * merged["div_yield_cs_rank"]
    expected50 = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + 0.5 * merged["div_yield_cs_rank"]
    for actual, expected in (
        (merged["sleeve_price60_mv_div0"], expected0),
        (merged["sleeve_price60_mv_div25"], expected25),
        (merged["sleeve_price60_mv_div50"], expected50),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9


def test_composite_zscore60_and_bias60_use_cross_section_ranks(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [
            {"factor_id": "factor_sleeve_zscore60_mv_div", "field": "sleeve_zscore60_mv_div", "version_id": "v1"},
            {"factor_id": "factor_sleeve_bias60_mv_div", "field": "sleeve_bias60_mv_div", "version_id": "v1"},
        ],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_zscore_60",
            "close_bias_60",
            "total_mv_cs_rank",
            "div_yield_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    z_rank = merged["close_zscore_60"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    b_rank = merged["close_bias_60"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    expected_z = z_rank + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"]
    expected_b = b_rank + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"]
    for actual, expected in (
        (merged["sleeve_zscore60_mv_div"], expected_z),
        (merged["sleeve_bias60_mv_div"], expected_b),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9


def test_composite_price60_cheap_weights_match_linear_combo(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import PRICE60_CHEAP_WEIGHTS

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    sample = PRICE60_CHEAP_WEIGHTS[0]
    field, _name, price_w, size_w, div_w, cheap_w = sample
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        [{"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}],
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "total_mv_cs_rank", "div_yield_cs_rank", "pe_ttm_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    expected = (
        price_w * merged["close_ts_rank_60"]
        + size_w * merged["total_mv_cs_rank"]
        + div_w * merged["div_yield_cs_rank"]
        + cheap_w * (1.0 - merged["pe_ttm_cs_rank"])
    )
    comparable = expected.notna() & merged[field].notna()
    assert comparable.any()
    assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert {item[0] for item in PRICE60_CHEAP_WEIGHTS} <= names
