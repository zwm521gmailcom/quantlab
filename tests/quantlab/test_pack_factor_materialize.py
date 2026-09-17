from pathlib import Path

from datetime import date, timedelta

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


def _tiny_canonical(path: Path, days: int = 5, codes: list[str] | None = None) -> None:
    codes = list(codes or ["000001.SZ", "600000.SH"])
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
    start = date(2018, 1, 2)
    for day in range(days):
        current = start + timedelta(days=day)
        date_text = current.strftime("%Y%m%d")
        for index, code in enumerate(codes):
            close = 10.0 + day + index
            rows["ts_code"].append(code)
            rows["trade_date"].append(date_text)
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

    sidecar = Path(canonical.parent / result["path"]).resolve()
    assert sidecar == default_sidecar_path(canonical)
    assert not Path(result["path"]).is_absolute()
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


def test_materialize_overnight_cs_rank_matches_cross_section_of_overnight_ret(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=5)
    materialize_canonical_pack_factors(canonical)
    table = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "overnight_ret", "overnight_ret_cs_rank"],
    ).to_pandas()
    expected = table.groupby("trade_date", sort=False)["overnight_ret"].rank(pct=True)
    comparable = table["overnight_ret"].notna() & table["overnight_ret_cs_rank"].notna()
    assert comparable.any()
    assert (table["overnight_ret_cs_rank"] - expected).abs()[comparable].max() < 1e-9


def test_materialize_momentum_10_cs_rank_matches_cross_section(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=12)
    materialize_canonical_pack_factors(canonical)
    table = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "momentum_10", "momentum_10_cs_rank"],
    ).to_pandas()
    expected = table.groupby("trade_date", sort=False)["momentum_10"].rank(pct=True)
    comparable = table["momentum_10"].notna() & table["momentum_10_cs_rank"].notna()
    assert comparable.any()
    assert (table["momentum_10_cs_rank"] - expected).abs()[comparable].max() < 1e-9


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
    assert not Path(written["path"]).is_absolute()
    assert (canonical.parent / written["path"]).resolve() == default_composite_sidecar_path(canonical)
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
    refs = [{"factor_id": f"factor_{field}", "field": field, "version_id": "v1"} for field, *_ in PRICE60_CHEAP_WEIGHTS]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "total_mv_cs_rank", "div_yield_cs_rank", "pe_ttm_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    for field, _name, price_w, size_w, div_w, cheap_w in PRICE60_CHEAP_WEIGHTS:
        expected = (
            price_w * merged["close_ts_rank_60"]
            + size_w * merged["total_mv_cs_rank"]
            + div_w * merged["div_yield_cs_rank"]
            + cheap_w * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert {item[0] for item in PRICE60_CHEAP_WEIGHTS} <= names


def test_composite_c075_momentum_and_size_blends_match_linear_combo(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C075_MOM_BLENDS,
        C075_PRICE_BLENDS,
        C075_SIZE_BLENDS,
        C150_DIV_BLENDS,
        C150_QUIET_BLENDS,
        C150_SIZE_BLENDS,
        D075_CHEAP_BLENDS,
    )

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field, *_ in (
            *C075_MOM_BLENDS,
            *C075_SIZE_BLENDS,
            *C075_PRICE_BLENDS,
            *D075_CHEAP_BLENDS,
            *C150_SIZE_BLENDS,
            *C150_DIV_BLENDS,
            *C150_QUIET_BLENDS,
        )
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
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
            "momentum_10",
            "turn_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    mom_rank = merged["momentum_10"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base = merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 0.75 * cheap
    for field, _name, mom_w in C075_MOM_BLENDS:
        expected = base + mom_w * mom_rank
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, size_w in C075_SIZE_BLENDS:
        expected = (
            merged["close_ts_rank_60"]
            + size_w * merged["total_mv_cs_rank"]
            + merged["div_yield_cs_rank"]
            + 0.75 * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, price_w in C075_PRICE_BLENDS:
        expected = (
            price_w * merged["close_ts_rank_60"]
            + merged["total_mv_cs_rank"]
            + merged["div_yield_cs_rank"]
            + 0.75 * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, cheap_w in D075_CHEAP_BLENDS:
        expected = (
            merged["close_ts_rank_60"]
            + merged["total_mv_cs_rank"]
            + 0.75 * merged["div_yield_cs_rank"]
            + cheap_w * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    for field, _name, size_w in C150_SIZE_BLENDS:
        expected = (
            merged["close_ts_rank_60"]
            + size_w * merged["total_mv_cs_rank"]
            + merged["div_yield_cs_rank"]
            + 1.5 * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, div_w in C150_DIV_BLENDS:
        expected = (
            merged["close_ts_rank_60"]
            + merged["total_mv_cs_rank"]
            + div_w * merged["div_yield_cs_rank"]
            + 1.5 * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    quiet = 1.0 - merged["turn_cs_rank"]
    for field, _name, quiet_w in C150_QUIET_BLENDS:
        expected = base_c150 + quiet_w * quiet
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9


def test_composite_c150_price_and_overlay_blends_match_linear_combo(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_AMOUNT_BLENDS,
        C150_LOCATION_BLENDS,
        C150_OVERNIGHT_BLENDS,
        C150_PRICE_BLENDS,
        C150_RANGE_BLENDS,
        C150_SHAPE_BLENDS,
    )

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70)
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field, *_ in (
            *C150_PRICE_BLENDS,
            *C150_RANGE_BLENDS,
            *C150_AMOUNT_BLENDS,
            *C150_OVERNIGHT_BLENDS,
            *C150_LOCATION_BLENDS,
            *C150_SHAPE_BLENDS,
        )
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
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
            "intraday_range",
            "amount_cs_rank",
            "overnight_ret",
            "close_location",
            "close_zscore_60",
            "close_bias_60",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    rng = merged["intraday_range"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    overnight = merged["overnight_ret"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    loc = merged["close_location"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    z_rank = merged["close_zscore_60"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    b_rank = merged["close_bias_60"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    for field, _name, price_w in C150_PRICE_BLENDS:
        expected = (
            price_w * merged["close_ts_rank_60"]
            + merged["total_mv_cs_rank"]
            + merged["div_yield_cs_rank"]
            + 1.5 * cheap
        )
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, weight in C150_RANGE_BLENDS:
        expected = base_c150 + weight * (1.0 - rng)
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, weight in C150_AMOUNT_BLENDS:
        expected = base_c150 + weight * merged["amount_cs_rank"]
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, weight in C150_OVERNIGHT_BLENDS:
        expected = base_c150 + weight * overnight
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    for field, _name, weight in C150_LOCATION_BLENDS:
        expected = base_c150 + weight * loc
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    expected_z = z_rank + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 1.5 * cheap
    expected_b = b_rank + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 1.5 * cheap
    for field, expected in (("sleeve_c150_z", expected_z), ("sleeve_c150_b", expected_b)):
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert {item[0] for item in (*C150_PRICE_BLENDS, *C150_RANGE_BLENDS, *C150_AMOUNT_BLENDS, *C150_OVERNIGHT_BLENDS, *C150_LOCATION_BLENDS, *C150_SHAPE_BLENDS)} <= names


def test_composite_c150_lookback_float_az_and_board_masks(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_AZ_BLENDS,
        C150_BOARD_BLENDS,
        C150_FLOAT,
        C150_P20,
        _board_drop_mask,
    )

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=70, codes=["000001.SZ", "600000.SH", "688001.SH", "300001.SZ"])
    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field, *_ in (C150_P20, C150_FLOAT, *C150_AZ_BLENDS, *C150_BOARD_BLENDS)
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_20",
            "close_ts_rank_60",
            "total_mv_cs_rank",
            "float_mv_cs_rank",
            "div_yield_cs_rank",
            "pe_ttm_cs_rank",
            "amount_zscore_20",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    expected_p20 = (
        merged["close_ts_rank_20"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    expected_float = (
        merged["close_ts_rank_60"]
        + merged["float_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    az_rank = merged["amount_zscore_20"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    for actual, expected in (
        (merged[C150_P20[0]], expected_p20),
        (merged[C150_FLOAT[0]], expected_float),
    ):
        comparable = expected.notna() & actual.notna()
        assert comparable.any()
        assert (actual - expected).abs()[comparable].max() < 1e-9
    for field, _name, weight in C150_AZ_BLENDS:
        expected = base_c150 + weight * az_rank
        comparable = expected.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - expected).abs()[comparable].max() < 1e-9
    drop = _board_drop_mask(merged["ts_code"], "main")
    assert drop.loc[merged["ts_code"] == "688001.SH"].all()
    assert drop.loc[merged["ts_code"] == "300001.SZ"].all()
    assert (~drop.loc[merged["ts_code"] == "000001.SZ"]).all()
    for field, _name, kind in C150_BOARD_BLENDS:
        keep = ~_board_drop_mask(merged["ts_code"], kind)
        comparable = keep & base_c150.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - base_c150).abs()[comparable].max() < 1e-9
        assert merged.loc[~keep, field].isna().all()
    names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert {C150_P20[0], C150_FLOAT[0], *(item[0] for item in C150_AZ_BLENDS), *(item[0] for item in C150_BOARD_BLENDS)} <= names


def test_composite_c150_lookback_ablation_volume_and_ma_masks(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_AZPOS,
        C150_D075,
        C150_HV_BLENDS,
        C150_MA_BLENDS,
        C150_P000,
        C150_P120,
        C150_P252,
        C150_S000,
    )

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=260, codes=["000001.SZ", "600000.SH", "000002.SZ"])
    # make the third name fall so 20/60-day bias can go negative
    table = pq.read_table(canonical).to_pandas()
    falling = table["ts_code"] == "000002.SZ"
    order = table.loc[falling].sort_values("trade_date")
    n = int(falling.sum())
    table.loc[order.index, "hfq_close"] = 80.0 - 0.2 * pd.Series(range(n), index=order.index)
    table.loc[order.index, "hfq_open"] = table.loc[order.index, "hfq_close"] - 0.2
    table.loc[order.index, "hfq_high"] = table.loc[order.index, "hfq_close"] + 0.3
    table.loc[order.index, "hfq_low"] = table.loc[order.index, "hfq_close"] - 0.4
    table.loc[order.index, "amount"] = 8000.0 - 20.0 * pd.Series(range(n), index=order.index)
    table.loc[order.index, "vol"] = 400.0 - pd.Series(range(n), index=order.index)
    pq.write_table(pa.Table.from_pandas(table, preserve_index=False), canonical)

    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field, *_ in (
            C150_P000,
            C150_S000,
            C150_D075,
            C150_P120,
            C150_P252,
            C150_AZPOS,
            *C150_HV_BLENDS,
            *C150_MA_BLENDS,
        )
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=[
            "ts_code",
            "trade_date",
            "close_ts_rank_60",
            "close_ts_rank_120",
            "close_ts_rank_252",
            "total_mv_cs_rank",
            "div_yield_cs_rank",
            "pe_ttm_cs_rank",
            "vol_mean_20",
            "amount_zscore_20",
            "close_bias_20",
            "close_bias_60",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    vol_rank = merged["vol_mean_20"].groupby(merged["trade_date"], sort=False).rank(pct=True)
    keep_az = (merged["amount_zscore_20"] > 0).fillna(False)
    expected = {
        C150_P000[0]: merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 1.5 * cheap,
        C150_S000[0]: merged["close_ts_rank_60"] + merged["div_yield_cs_rank"] + 1.5 * cheap,
        C150_D075[0]: (
            merged["close_ts_rank_60"]
            + merged["total_mv_cs_rank"]
            + 0.75 * merged["div_yield_cs_rank"]
            + 1.5 * cheap
        ),
        C150_P120[0]: merged["close_ts_rank_120"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap,
        C150_P252[0]: merged["close_ts_rank_252"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap,
    }
    for field, series in expected.items():
        comparable = series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    for field, _name, weight in C150_HV_BLENDS:
        series = base_c150 + weight * vol_rank
        comparable = series.notna() & merged[field].notna()
        assert comparable.any()
        assert (merged[field] - series).abs()[comparable].max() < 1e-9
    assert merged.loc[keep_az & base_c150.notna(), C150_AZPOS[0]].notna().any()
    assert (merged.loc[keep_az, C150_AZPOS[0]] - base_c150.loc[keep_az]).abs().max() < 1e-9
    assert merged.loc[~keep_az, C150_AZPOS[0]].isna().all()
    for field, _name, kind in C150_MA_BLENDS:
        bias = merged["close_bias_20" if kind == "bias20" else "close_bias_60"]
        keep = (bias > 0).fillna(False)
        comparable = keep & base_c150.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - base_c150).abs()[comparable].max() < 1e-9, field
        assert merged.loc[~keep, field].isna().all(), field
        assert (~keep).any(), field
    names = set(pq.ParquetFile(default_composite_sidecar_path(canonical)).schema_arrow.names)
    assert {
        C150_P000[0],
        C150_S000[0],
        C150_D075[0],
        C150_P120[0],
        C150_P252[0],
        C150_AZPOS[0],
        *(item[0] for item in C150_HV_BLENDS),
        *(item[0] for item in C150_MA_BLENDS),
    } <= names


def test_composite_c150_reversal_fade_trend_and_inst_blends(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_CSI500,
        C150_CSI800_POOL_FIELDS,
        C150_CSI800_S5,
        C150_CSI800_STABLE,
        C150_CSI800_W,
        C150_CSI800_WINV,
        C150_FADE_GAP,
        C150_INST,
        C150_LOWLOC,
        C150_REV_MOM,
        C150_RM60,
        C150_TREND_BLENDS,
        C150_UNIVERSE,
        _cs_rank,
    )

    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=80, codes=["000001.SZ", "600000.SH", "000002.SZ"])
    table = pq.read_table(canonical).to_pandas()
    falling = table["ts_code"] == "000002.SZ"
    order = table.loc[falling].sort_values("trade_date")
    n = int(falling.sum())
    table.loc[order.index, "hfq_close"] = 80.0 - 0.2 * pd.Series(range(n), index=order.index)
    table.loc[order.index, "hfq_open"] = table.loc[order.index, "hfq_close"] + 0.8
    table.loc[order.index, "turn"] = 4.0 + 0.02 * pd.Series(range(n), index=order.index)
    table.loc[order.index, "vol"] = 50.0 + pd.Series(range(n), index=order.index)
    pq.write_table(pa.Table.from_pandas(table, preserve_index=False), canonical)

    materialize_canonical_pack_factors(canonical)
    materialize_composite_pack_factors(default_sidecar_path(canonical))
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field, *_ in (
            *C150_REV_MOM,
            *C150_FADE_GAP,
            *C150_TREND_BLENDS,
            C150_INST,
            C150_RM60,
            C150_LOWLOC,
            *C150_UNIVERSE,
            C150_CSI500,
            *C150_CSI800_W,
            C150_CSI800_WINV,
            C150_CSI800_STABLE,
            C150_CSI800_S5,
            *((field,) for field in C150_CSI800_POOL_FIELDS),
        )
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
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
            "momentum_10",
            "momentum_60",
            "overnight_ret",
            "close_bias_60",
            "vol_mean_20",
            "turn_cs_rank",
            "close_location",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    mom = _cs_rank(merged["momentum_10"], merged["trade_date"])
    mom60 = _cs_rank(merged["momentum_60"], merged["trade_date"])
    overnight = _cs_rank(merged["overnight_ret"], merged["trade_date"])
    bias60 = _cs_rank(merged["close_bias_60"], merged["trade_date"])
    vol = _cs_rank(merged["vol_mean_20"], merged["trade_date"])
    quiet = 1.0 - merged["turn_cs_rank"]
    loc = _cs_rank(merged["close_location"], merged["trade_date"])
    for field, _name, weight in C150_REV_MOM:
        series = base_c150 + weight * (1.0 - mom)
        comparable = series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    for field, _name, weight in C150_FADE_GAP:
        series = base_c150 + weight * (1.0 - overnight)
        comparable = series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    for field, _name, weight in C150_TREND_BLENDS:
        series = base_c150 + weight * bias60
        comparable = series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    inst = base_c150 + 0.25 * vol + 0.25 * quiet
    comparable = inst.notna() & merged[C150_INST[0]].notna()
    assert comparable.any()
    assert (merged[C150_INST[0]] - inst).abs()[comparable].max() < 1e-9
    rm60 = base_c150 + 0.25 * (1.0 - mom60)
    comparable = rm60.notna() & merged[C150_RM60[0]].notna()
    assert comparable.any()
    assert (merged[C150_RM60[0]] - rm60).abs()[comparable].max() < 1e-9
    lowloc = base_c150 + 0.25 * (1.0 - loc)
    comparable = lowloc.notna() & merged[C150_LOWLOC[0]].notna()
    assert comparable.any()
    assert (merged[C150_LOWLOC[0]] - lowloc).abs()[comparable].max() < 1e-9
    # 缺权重文件时成分掩码必须退化成脊柱，避免现有测试目录把分数打成全 NaN。
    for field, _name, _kind in C150_UNIVERSE:
        comparable = base_c150.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - base_c150).abs()[comparable].max() < 1e-9, field
    for field in (
        C150_CSI500[0],
        *(item[0] for item in C150_CSI800_W),
        C150_CSI800_WINV[0],
        C150_CSI800_STABLE[0],
        C150_CSI800_S5[0],
        *C150_CSI800_POOL_FIELDS,
    ):
        comparable = base_c150.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - base_c150).abs()[comparable].max() < 1e-9, field


def test_composite_c150_index_universe_masks_when_weight_files_exist(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_CSI500,
        C150_CSI800_S5,
        C150_CSI800_STABLE,
        C150_CSI800_W,
        C150_CSI800_WINV,
        C150_UNIVERSE,
        _cs_rank,
    )

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ"]
    _tiny_canonical(canonical, days=70, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "000001.SZ", "600000.SH"],
                "trade_date": ["20180102", "20180305", "20180305"],
                "weight": [4.0, 4.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH", "000905.SH"],
                "con_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20180102", "20180305"],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field in (
            *(item[0] for item in C150_UNIVERSE),
            C150_CSI500[0],
            *(item[0] for item in C150_CSI800_W),
            C150_CSI800_WINV[0],
            C150_CSI800_STABLE[0],
            C150_CSI800_S5[0],
        )
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "total_mv_cs_rank", "div_yield_cs_rank", "pe_ttm_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * (1.0 - merged["pe_ttm_cs_rank"])
    )
    hs300 = C150_UNIVERSE[0][0]
    csi800 = C150_UNIVERSE[1][0]
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    before = compact == "20180304"
    after = compact == "20180305"
    in_800 = merged["ts_code"].isin(["000001.SZ", "600000.SH"])
    # 60 日分位暖机之后，20180304 仍只有 000001；20180305 起 600000 才进沪深300。
    assert merged.loc[before & (merged["ts_code"] == "000001.SZ"), hs300].notna().all()
    assert merged.loc[before & (merged["ts_code"] == "600000.SH"), hs300].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), hs300].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), hs300].notna().all()
    assert (merged.loc[after & (merged["ts_code"] == "600000.SH"), hs300]
            - base_c150.loc[after & (merged["ts_code"] == "600000.SH")]).abs().max() < 1e-9
    assert merged.loc[merged["ts_code"] == "000002.SZ", hs300].isna().all()
    assert merged.loc[in_800, csi800].notna().any()
    assert (merged.loc[in_800 & base_c150.notna(), csi800] - base_c150.loc[in_800 & base_c150.notna()]).abs().max() < 1e-9
    assert merged.loc[~in_800, csi800].isna().all()
    assert merged.loc[merged["ts_code"] == "600000.SH", C150_CSI500[0]].notna().any()
    assert merged.loc[merged["ts_code"] != "600000.SH", C150_CSI500[0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_CSI800_STABLE[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_CSI800_STABLE[0]].notna().all()
    assert merged.loc[merged["ts_code"] == "000002.SZ", C150_CSI800_STABLE[0]].isna().all()
    only_500 = before & (merged["ts_code"] == "600000.SH")
    only_300 = before & (merged["ts_code"] == "000001.SZ")
    assert (merged.loc[only_500, C150_CSI800_S5[0]] - (base_c150.loc[only_500] + 0.25)).abs().max() < 1e-9
    assert (merged.loc[only_300, C150_CSI800_S5[0]] - base_c150.loc[only_300]).abs().max() < 1e-9
    idx_w = merged["ts_code"].map({"000001.SZ": 4.0, "600000.SH": 1.0})
    idx_w = idx_w.where(in_800)
    weight_rank = _cs_rank(idx_w, merged["trade_date"])
    for field, _name, weight in C150_CSI800_W:
        series = base_c150.where(in_800) + weight * weight_rank
        comparable = after & in_800 & series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    winv = base_c150.where(in_800) + 0.25 * (1.0 - weight_rank)
    comparable = after & in_800 & winv.notna()
    assert (merged.loc[comparable, C150_CSI800_WINV[0]] - winv.loc[comparable]).abs().max() < 1e-9


def test_composite_csi800_stable_pool_ranks_weight_floor_and_tenure(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_CSI800_POOL_FIELDS,
        C150_CSI800_STABLE,
        C150_CSPE,
        C150_ST_CSALL,
        C150_ST_CSDV,
        C150_ST_CSPE,
        C150_ST_CSPE0,
        C150_ST_S000,
        C150_ST_T,
        C150_ST_W005,
        C150_ST_W010,
        _cs_rank_where,
    )

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ"]
    _tiny_canonical(canonical, days=90, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH"] * 3,
                "con_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20180131", "20180228", "20180330"],
                "weight": [4.0, 4.0, 4.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH"] * 3,
                "con_code": ["600000.SH", "600000.SH", "000002.SZ"],
                "trade_date": ["20180228", "20180330", "20180330"],
                "weight": [0.07, 0.07, 0.20],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    refs = [{"factor_id": f"factor_{field}", "field": field, "version_id": "v1"} for field in C150_CSI800_POOL_FIELDS]
    refs.append(
        {
            "factor_id": f"factor_{C150_CSI800_STABLE[0]}",
            "field": C150_CSI800_STABLE[0],
            "version_id": "v1",
        }
    )
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "total_mv_cs_rank", "div_yield_cs_rank", "pe_ttm_cs_rank"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    after = compact == "20180331"
    in_800 = merged["ts_code"].isin(["000001.SZ", "600000.SH", "000002.SZ"])
    st_mask = after & merged["ts_code"].isin(["000001.SZ", "600000.SH"])
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    base_c150 = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    )
    cheap_800 = 1.0 - _cs_rank_where(merged["pe_ttm_cs_rank"], merged["trade_date"], in_800)
    cheap_st = 1.0 - _cs_rank_where(merged["pe_ttm_cs_rank"], merged["trade_date"], st_mask)
    div_st = _cs_rank_where(merged["div_yield_cs_rank"], merged["trade_date"], st_mask)
    mv_st = _cs_rank_where(merged["total_mv_cs_rank"], merged["trade_date"], st_mask)
    st_base = base_c150.where(st_mask)
    expected = {
        C150_CSPE[0]: (merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 1.5 * cheap_800).where(in_800),
        C150_ST_CSPE[0]: (merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + merged["div_yield_cs_rank"] + 1.5 * cheap_st).where(st_mask),
        C150_ST_CSDV[0]: (merged["close_ts_rank_60"] + merged["total_mv_cs_rank"] + div_st + 1.5 * cheap_st).where(st_mask),
        C150_ST_CSALL[0]: (merged["close_ts_rank_60"] + mv_st + div_st + 1.5 * cheap_st).where(st_mask),
        C150_ST_S000[0]: (merged["close_ts_rank_60"] + merged["div_yield_cs_rank"] + 1.5 * cheap).where(st_mask),
        C150_ST_CSPE0[0]: (merged["close_ts_rank_60"] + merged["div_yield_cs_rank"] + 1.5 * cheap_st).where(st_mask),
    }
    for field, series in expected.items():
        comparable = after & series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    assert merged.loc[after & (merged["ts_code"] == "000002.SZ"), C150_CSI800_STABLE[0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000002.SZ"), C150_CSPE[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_W005[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_W005[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_W010[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_W010[0]].isna().all()
    tenure_rank = pd.Series(index=merged.index, dtype="float64")
    tenure_rank.loc[after & (merged["ts_code"] == "000001.SZ")] = 1.0
    tenure_rank.loc[after & (merged["ts_code"] == "600000.SH")] = 0.5
    for field, _name, weight in C150_ST_T:
        series = st_base + weight * tenure_rank
        comparable = after & st_mask & series.notna() & merged[field].notna()
        assert comparable.any(), field
        assert (merged[field] - series).abs()[comparable].max() < 1e-9, field
    long_tenured = float(merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_T[1][0]].iloc[0])
    short_tenured = float(merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_T[1][0]].iloc[0])
    assert long_tenured > short_tenured


def test_composite_csi800_weight_ladder_asymmetric_and_two_snap_floor(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import (
        C150_CSI800_STABLE,
        C150_ST_A510,
        C150_ST_A605,
        C150_ST_A_LADDER,
        C150_ST_A_W2,
        C150_ST_HQ,
        C150_ST_W2,
        C150_ST_W_NEW,
        C150_ST_WP,
        C150_ST_W005,
        C150_ST_W010,
        C150_ST_W2M0,
        C150_ST_W2L80,
        C150_ST_W2L90,
        C150_ST_W2L50,
        C150_ST_W2M,
        C150_ST_W2R,
        C150_ST_W2T,
        C150_ST_W2_TILT,
        C150_ST_W2_MICRO,
        C150_ST_W2_LEG,
        C150_ST_W2_PEAK,
        C150_ST_DV03_TILT,
        C150_ST_DV03_CSRANK,
        C150_ST_QT005_TILT,
        C150_ST_FADE_QT005,
        C150_ST_DV03_SHAPE,
        _cs_quantile_where,
    )

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ", "600001.SH"]
    _tiny_canonical(canonical, days=90, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH"] * 5,
                "con_code": ["000001.SZ", "000001.SZ", "000001.SZ", "600001.SH", "600001.SH"],
                "trade_date": ["20180131", "20180228", "20180330", "20180228", "20180330"],
                "weight": [4.0, 4.0, 4.0, 0.068, 0.068],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH"] * 4,
                "con_code": ["600000.SH", "000002.SZ", "600000.SH", "000002.SZ"],
                "trade_date": ["20180228", "20180228", "20180330", "20180330"],
                "weight": [0.04, 0.02, 0.07, 0.02],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    fields = [
        C150_CSI800_STABLE[0],
        C150_ST_W005[0],
        C150_ST_W010[0],
        C150_ST_W2[0],
        C150_ST_A510[0],
        C150_ST_A605[0],
        *(item[0] for item in C150_ST_W_NEW),
        *(item[0] for item in C150_ST_WP),
        *(item[0] for item in C150_ST_A_LADDER),
        *(item[0] for item in C150_ST_A_W2),
        *(item[0] for item in C150_ST_HQ),
        C150_ST_W2M0[0],
        C150_ST_W2L80[0],
        C150_ST_W2L90[0],
        C150_ST_W2L50[0],
        *(item[0] for item in C150_ST_W2M),
        *(item[0] for item in C150_ST_W2R),
        *(item[0] for item in C150_ST_W2T),
        *(item[0] for item in C150_ST_W2_TILT),
        *(item[0] for item in C150_ST_W2_MICRO),
        *(item[0] for item in C150_ST_W2_LEG),
        *(item[0] for item in C150_ST_W2_PEAK),
        *(item[0] for item in C150_ST_DV03_TILT),
        *(item[0] for item in C150_ST_DV03_CSRANK),
        *(item[0] for item in C150_ST_QT005_TILT),
        *(item[0] for item in C150_ST_FADE_QT005),
        *(item[0] for item in C150_ST_DV03_SHAPE),
    ]
    refs = [{"factor_id": f"factor_{field}", "field": field, "version_id": "v1"} for field in fields]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
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
            "vol_mean_20_cs_rank",
            "close_bias_20_cs_rank",
            "momentum_10_cs_rank",
        ],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    after = compact == "20180331"
    st_mask = after & merged["ts_code"].isin(codes)
    cheap = 1.0 - merged["pe_ttm_cs_rank"]
    st_base = (
        merged["close_ts_rank_60"]
        + merged["total_mv_cs_rank"]
        + merged["div_yield_cs_rank"]
        + 1.5 * cheap
    ).where(st_mask)
    idx_w = merged["ts_code"].map({"000001.SZ": 4.0, "600000.SH": 0.07, "000002.SZ": 0.02, "600001.SH": 0.068})
    prev_w = merged["ts_code"].map({"000001.SZ": 4.0, "600000.SH": 0.04, "000002.SZ": 0.02, "600001.SH": 0.068})
    hs300 = merged["ts_code"].isin(["000001.SZ", "600001.SH"])
    assert merged.loc[after & (merged["ts_code"] == "000002.SZ"), C150_CSI800_STABLE[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "000002.SZ"), C150_ST_W005[0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_W005[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_W_NEW[4][0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_W_NEW[4][0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_W2[0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_W2[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_A510[0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_A605[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600001.SH"), C150_ST_A605[0]].notna().all()
    assert merged.loc[after & (merged["ts_code"] == "600001.SH"), C150_ST_A_LADDER[0][0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "600000.SH"), C150_ST_A_W2[0][0]].isna().all()
    assert merged.loc[after & (merged["ts_code"] == "000001.SZ"), C150_ST_A_W2[0][0]].notna().all()
    for field, _name, thr in C150_ST_W_NEW:
        series = st_base.where(idx_w >= thr)
        comparable = after & st_mask & (series.notna() | merged[field].notna())
        assert ((merged[field].isna() & series.isna()) | ((merged[field] - series).abs() < 1e-9)).loc[after & st_mask].all(), field
    two_snap = st_base.where((idx_w >= 0.05) & (prev_w >= 0.05))
    comparable = after & st_mask
    assert ((merged[C150_ST_W2[0]] - two_snap).abs() < 1e-9).loc[comparable & two_snap.notna()].all()
    assert merged.loc[comparable & two_snap.isna(), C150_ST_W2[0]].isna().all()
    a510 = st_base.where((hs300 & (idx_w >= 0.05)) | (~hs300 & (idx_w >= 0.10)))
    a605 = st_base.where((hs300 & (idx_w >= 0.06)) | (~hs300 & (idx_w >= 0.05)))
    assert ((merged[C150_ST_A510[0]] - a510).abs() < 1e-9).loc[comparable & a510.notna()].all()
    assert merged.loc[comparable & a510.isna(), C150_ST_A510[0]].isna().all()
    assert ((merged[C150_ST_A605[0]] - a605).abs() < 1e-9).loc[comparable & a605.notna()].all()
    for field, _name, thr300, thr500 in C150_ST_A_LADDER:
        series = st_base.where((hs300 & (idx_w >= thr300)) | (~hs300 & (idx_w >= thr500)))
        assert ((merged[field] - series).abs() < 1e-9).loc[comparable & series.notna()].all(), field
        assert merged.loc[comparable & series.isna(), field].isna().all(), field
    for field, _name, thr300, thr500 in C150_ST_A_W2:
        series = st_base.where(
            ((hs300 & (idx_w >= thr300)) | (~hs300 & (idx_w >= thr500))) & (prev_w >= 0.05)
        )
        assert ((merged[field] - series).abs() < 1e-9).loc[comparable & series.notna()].all(), field
        assert merged.loc[comparable & series.isna(), field].isna().all(), field
    for field, _name, q in C150_ST_HQ:
        cut = _cs_quantile_where(idx_w, merged["trade_date"], hs300, q)
        series = st_base.where((hs300 & (idx_w >= cut)) | (~hs300 & (idx_w >= 0.05)))
        kept = comparable & series.notna()
        assert kept.any(), field
        assert (merged[field] - series).abs()[kept].max() < 1e-9, field
        assert merged.loc[comparable & series.isna(), field].isna().all(), field
    extras = [
        C150_ST_W2M0[0],
        C150_ST_W2L80[0],
        C150_ST_W2L90[0],
        C150_ST_W2L50[0],
        *(item[0] for item in C150_ST_W2M),
        *(item[0] for item in C150_ST_W2R),
        *(item[0] for item in C150_ST_W2T),
    ]
    a605w2 = merged[C150_ST_A_W2[0][0]]
    for field in extras:
        kept = comparable & merged[field].notna()
        assert merged.loc[kept, field].notna().all(), field
        assert a605w2.loc[kept].notna().all(), field
        assert ((merged[field] - a605w2).abs()[kept] < 1e-9).all(), field
        assert merged.loc[comparable & a605w2.isna(), field].isna().all(), field
    for item in (
        *C150_ST_W2_TILT,
        *C150_ST_W2_MICRO,
        *C150_ST_W2_LEG,
        *C150_ST_W2_PEAK,
        *C150_ST_DV03_TILT,
        *C150_ST_DV03_CSRANK,
        *C150_ST_QT005_TILT,
        *C150_ST_FADE_QT005,
        *C150_ST_DV03_SHAPE,
    ):
        field = item[0]
        kept = comparable & merged[field].notna()
        assert a605w2.loc[kept].notna().all(), field
        assert merged.loc[comparable & a605w2.isna(), field].isna().all(), field
    for field, _name, q in C150_ST_WP:
        cut = _cs_quantile_where(idx_w, merged["trade_date"], st_mask, q)
        series = st_base.where(idx_w >= cut)
        kept = comparable & series.notna()
        assert kept.any(), field
        assert (merged[field] - series).abs()[kept].max() < 1e-9, field
        assert merged.loc[comparable & series.isna(), field].isna().all(), field
    dv03 = merged["sleeve_c150_st_w2dv03"]
    expected_csrank = {
        C150_ST_DV03_CSRANK[0][0]: dv03 + 0.25 * (1.0 - merged["vol_mean_20_cs_rank"]),
        C150_ST_DV03_CSRANK[1][0]: dv03 + 0.25 * merged["momentum_10_cs_rank"],
        C150_ST_DV03_CSRANK[2][0]: dv03 + 0.25 * (1.0 - merged["close_bias_20_cs_rank"]),
    }
    for field, series in expected_csrank.items():
        kept = comparable & dv03.notna() & merged[field].notna()
        assert kept.any(), field
        assert (merged[field] - series).abs()[kept].max() < 1e-9, field
        assert merged.loc[comparable & dv03.isna(), field].isna().all(), field


def test_materialize_momentum_120_matches_pct_change(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=130)
    materialize_canonical_pack_factors(canonical)
    table = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "momentum_120"],
    ).to_pandas()
    market = pq.read_table(canonical, columns=["ts_code", "trade_date", "hfq_close"]).to_pandas()
    first = table.merge(market, on=["ts_code", "trade_date"])
    first = first.loc[first["ts_code"] == "000001.SZ"].sort_values("trade_date").reset_index(drop=True)
    assert pd.isna(first["momentum_120"].iloc[119])
    expected = first["hfq_close"].iloc[120] / first["hfq_close"].iloc[0] - 1
    assert abs(first["momentum_120"].iloc[120] - expected) < 1e-9


def test_composite_rs_sleeves_mask_to_consecutive_csi800(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import C150_CSI800_STABLE, RS120_ST, RS60_ST

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ"]
    _tiny_canonical(canonical, days=130, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "000001.SZ", "600000.SH"],
                "trade_date": ["20180102", "20180305", "20180305"],
                "weight": [4.0, 4.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH", "000905.SH"],
                "con_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20180102", "20180305"],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field in (RS60_ST[0], RS120_ST[0], C150_CSI800_STABLE[0])
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "momentum_60", "momentum_120"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    after = compact >= "20180305"
    assert merged.loc[merged["ts_code"] == "000002.SZ", RS60_ST[0]].isna().all()
    assert merged.loc[merged["ts_code"] == "000002.SZ", RS120_ST[0]].isna().all()
    member = after & merged["ts_code"].isin(["000001.SZ", "600000.SH"])
    comparable = member & merged["momentum_60"].notna()
    assert comparable.any()
    assert (merged.loc[comparable, RS60_ST[0]] - merged.loc[comparable, "momentum_60"]).abs().max() < 1e-9
    both = comparable & merged[C150_CSI800_STABLE[0]].notna()
    assert both.any()
    assert (merged.loc[both, RS60_ST[0]] - merged.loc[both, C150_CSI800_STABLE[0]]).abs().min() > 1e-6
    warmed = member & merged["momentum_120"].notna()
    assert warmed.any()
    assert (merged.loc[warmed, RS120_ST[0]] - merged.loc[warmed, "momentum_120"]).abs().max() < 1e-9


def test_composite_brk_sleeves_mask_to_consecutive_csi800(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import BRK120_ST, BRK60_ST, RS60_ST

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ"]
    _tiny_canonical(canonical, days=130, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "000001.SZ", "600000.SH"],
                "trade_date": ["20180102", "20180305", "20180305"],
                "weight": [4.0, 4.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH", "000905.SH"],
                "con_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20180102", "20180305"],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field in (BRK60_ST[0], BRK120_ST[0], RS60_ST[0])
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "close_ts_rank_60", "close_ts_rank_120", "momentum_60"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    after = compact >= "20180305"
    assert merged.loc[merged["ts_code"] == "000002.SZ", BRK60_ST[0]].isna().all()
    assert merged.loc[merged["ts_code"] == "000002.SZ", BRK120_ST[0]].isna().all()
    member = after & merged["ts_code"].isin(["000001.SZ", "600000.SH"])
    comparable = member & merged["close_ts_rank_60"].notna()
    assert comparable.any()
    assert (merged.loc[comparable, BRK60_ST[0]] - merged.loc[comparable, "close_ts_rank_60"]).abs().max() < 1e-9
    warmed = member & merged["close_ts_rank_120"].notna()
    assert warmed.any()
    assert (merged.loc[warmed, BRK120_ST[0]] - merged.loc[warmed, "close_ts_rank_120"]).abs().max() < 1e-9
    both = comparable & merged[RS60_ST[0]].notna()
    assert both.any()
    brk_vs_rs = (merged.loc[both, BRK60_ST[0]] - merged.loc[both, RS60_ST[0]]).abs()
    assert float(brk_vs_rs.max()) > 1e-6


def test_materialize_volatility_20_matches_return_std(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.parquet"
    _tiny_canonical(canonical, days=30)
    materialize_canonical_pack_factors(canonical)
    table = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "volatility_20"],
    ).to_pandas()
    market = pq.read_table(canonical, columns=["ts_code", "trade_date", "hfq_close"]).to_pandas()
    first = table.merge(market, on=["ts_code", "trade_date"])
    first = first.loc[first["ts_code"] == "000001.SZ"].sort_values("trade_date").reset_index(drop=True)
    rets = first["hfq_close"].pct_change()
    expected = rets.rolling(20).std()
    assert pd.isna(first["volatility_20"].iloc[19])
    assert abs(float(first["volatility_20"].iloc[20] - expected.iloc[20])) < 1e-9


def test_composite_quality_sleeves_mask_to_consecutive_csi800(tmp_path: Path) -> None:
    from quantlab.services.canonical_pack_factors import QMIX_ST, QTURN_ST, QVOL_ST, RS60_ST

    canonical = tmp_path / "canonical.parquet"
    codes = ["000001.SZ", "600000.SH", "000002.SZ"]
    _tiny_canonical(canonical, days=80, codes=codes)
    materialize_canonical_pack_factors(canonical)
    weight_dir = tmp_path / "weights"
    weight_dir.mkdir()
    pq.write_table(
        pa.table(
            {
                "index_code": ["000300.SH", "000300.SH", "000300.SH"],
                "con_code": ["000001.SZ", "000001.SZ", "600000.SH"],
                "trade_date": ["20180102", "20180305", "20180305"],
                "weight": [4.0, 4.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index_code": ["000905.SH", "000905.SH"],
                "con_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20180102", "20180305"],
                "weight": [1.0, 1.0],
            }
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    materialize_composite_pack_factors(default_sidecar_path(canonical), index_weight_dir=weight_dir)
    refs = [
        {"factor_id": f"factor_{field}", "field": field, "version_id": "v1"}
        for field in (QTURN_ST[0], QVOL_ST[0], QMIX_ST[0], RS60_ST[0])
    ]
    attached = attach_pack_factor_columns(
        pq.read_table(canonical).to_pandas().assign(
            instrument=lambda frame: frame["ts_code"],
            date=lambda frame: frame["trade_date"],
        ),
        refs,
        default_sidecar_path(canonical),
    )
    pack = pq.read_table(
        default_sidecar_path(canonical),
        columns=["ts_code", "trade_date", "turn_cs_rank", "volatility_20"],
    ).to_pandas()
    merged = attached.merge(pack, on=["ts_code", "trade_date"])
    compact = merged["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    after = compact >= "20180305"
    for field in (QTURN_ST[0], QVOL_ST[0], QMIX_ST[0]):
        assert merged.loc[merged["ts_code"] == "000002.SZ", field].isna().all()
    member = after & merged["ts_code"].isin(["000001.SZ", "600000.SH"])
    comparable = member & merged["turn_cs_rank"].notna()
    assert comparable.any()
    expected_qturn = 1.0 - merged.loc[comparable, "turn_cs_rank"]
    assert (merged.loc[comparable, QTURN_ST[0]] - expected_qturn).abs().max() < 1e-9
    vol_ok = member & merged["volatility_20"].notna()
    assert vol_ok.any()
    assert merged.loc[vol_ok, QVOL_ST[0]].notna().all()
    mix_ok = comparable & merged[QMIX_ST[0]].notna() & merged[QTURN_ST[0]].notna() & merged[QVOL_ST[0]].notna()
    assert mix_ok.any()
    expected_mix = merged.loc[mix_ok, QTURN_ST[0]] + merged.loc[mix_ok, QVOL_ST[0]]
    assert (merged.loc[mix_ok, QMIX_ST[0]] - expected_mix).abs().max() < 1e-9
    both = comparable & merged[RS60_ST[0]].notna()
    assert both.any()
    assert float((merged.loc[both, QTURN_ST[0]] - merged.loc[both, RS60_ST[0]]).abs().max()) > 1e-6

