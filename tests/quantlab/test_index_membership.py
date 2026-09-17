from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import pytest

from quantlab.services.index_membership import (
    amount_unit,
    amount_yuan,
    filter_index_universe_asof,
    filter_listed_universe,
    is_main_sme_chinext,
    latest_members,
    load_index_weight,
    members_on,
    membership_coverage_error,
    parse_universe_index_codes,
)


def test_parse_universe_index_codes_defaults_and_normalizes():
    assert parse_universe_index_codes(None) == ("000300.SH", "000905.SH")
    assert parse_universe_index_codes("") == ("000300.SH", "000905.SH")
    assert parse_universe_index_codes("000852.sh") == ("000852.SH",)
    assert parse_universe_index_codes(["000906.SH", " 000852.SH "]) == ("000906.SH", "000852.SH")
    assert parse_universe_index_codes(["000300.SH", "000300.SH", "000905.SH"]) == ("000300.SH", "000905.SH")


def test_membership_coverage_error_reports_missing_weight(tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "index_weight").mkdir(parents=True)
    assert "000852.SH" in (membership_coverage_error(raw, ["000852.SH"]) or "")


def test_members_on_uses_last_month_not_future():
    weights = pd.DataFrame({
        "index_code": ["000300.SH", "000300.SH"],
        "con_code": ["AAA.SZ", "BBB.SZ"],
        "trade_date": ["20180903", "20181008"],
        "weight": [1.0, 1.0],
    })
    assert members_on(weights, "000300.SH", "20180920") == {"AAA.SZ"}
    assert members_on(weights, "000300.SH", "20181008") == {"BBB.SZ"}


def test_members_on_missing_index_is_empty_not_all_a():
    assert members_on(pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"]), "000300.SH", "20180903") == set()


def test_latest_members_ignores_older_months():
    weights = pd.DataFrame({
        "index_code": ["000300.SH", "000300.SH", "000905.SH"],
        "con_code": ["OLD.SZ", "NEW.SZ", "FIVE.SZ"],
        "trade_date": ["20180903", "20240131", "20231231"],
        "weight": [1.0, 1.0, 1.0],
    })
    assert latest_members(weights, "000300.SH") == {"NEW.SZ"}
    assert latest_members(weights, "000905.SH") == {"FIVE.SZ"}
    assert members_on(weights, "000300.SH", "20180920") == {"OLD.SZ"}
    assert latest_members(pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"]), "000300.SH") == set()


def test_board_excludes_star_and_bj():
    assert is_main_sme_chinext("600000.SH")
    assert is_main_sme_chinext("300001.SZ")
    assert is_main_sme_chinext("301001.SZ")
    assert not is_main_sme_chinext("688001.SH")
    assert not is_main_sme_chinext("830001.BJ")


def test_load_index_weight_reads_parquet_files(tmp_path):
    weight_dir = tmp_path / "index_weight"
    weight_dir.mkdir()
    rows_300 = [
        {"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "20180903", "weight": 0.5},
    ]
    rows_500 = [
        {"index_code": "000905.SH", "con_code": "600000.SH", "trade_date": "20180903", "weight": 0.3},
    ]
    pq.write_table(pa.Table.from_pylist(rows_300), weight_dir / "index_weight_000300_SH.parquet")
    pq.write_table(pa.Table.from_pylist(rows_500), weight_dir / "index_weight_000905_SH.parquet")

    df = load_index_weight(tmp_path)
    assert len(df) == 2
    assert set(df.columns) == {"index_code", "con_code", "trade_date", "weight"}


def test_load_index_weight_missing_returns_empty_columns(tmp_path):
    df = load_index_weight(tmp_path)
    assert list(df.columns) == ["index_code", "con_code", "trade_date", "weight"]
    assert df.empty


def test_load_index_weight_reads_only_requested_index_files(tmp_path):
    weight_dir = tmp_path / "index_weight"
    weight_dir.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [{"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "20180903", "weight": 0.5}]
        ),
        weight_dir / "index_weight_000300_SH.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [{"index_code": "000905.SH", "con_code": "600000.SH", "trade_date": "20180903", "weight": 0.3}]
        ),
        weight_dir / "index_weight_000905_SH.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [{"index_code": "000001.SH", "con_code": "999999.SH", "trade_date": "20180903", "weight": 1.0}]
        ),
        weight_dir / "index_weight_000001_SH.parquet",
    )

    df = load_index_weight(tmp_path, ["000300.SH", "000905.SH"])
    assert set(df["index_code"].astype(str)) == {"000300.SH", "000905.SH"}
    assert "000001.SH" not in set(df["index_code"].astype(str))
    assert len(df) == 2


def test_amount_yuan_converts_thousand_yuan():
    series = pd.Series([50000.0, 60000.0, 70000.0])
    result = amount_yuan(series)
    assert result.tolist() == [50_000_000.0, 60_000_000.0, 70_000_000.0]
    assert result.attrs["unit"] == "thousand_yuan"


def test_amount_yuan_keeps_yuan_when_median_large():
    series = pd.Series([50_000_000.0, 60_000_000.0])
    result = amount_yuan(series)
    assert result.tolist() == [50_000_000.0, 60_000_000.0]
    assert result.attrs["unit"] == "yuan"


def test_amount_unit_uses_column_median_not_a_single_row():
    series = pd.Series([50_000.0, 60_000.0, 20_000_000.0])
    assert amount_unit(series) == "thousand_yuan"
    converted = amount_yuan(series)
    assert converted.iloc[2] == pytest.approx(20_000_000_000.0)
    forced = amount_yuan(pd.Series([20_000_000.0]), unit="thousand_yuan")
    assert forced.iloc[0] == pytest.approx(20_000_000_000.0)


def test_filter_listed_universe_drops_ineligible_and_delisted():
    frame = pd.DataFrame(
        {
            "date": ["20240102", "20240102", "20240103", "20240103"],
            "instrument": ["AAA.SZ", "BBB.SZ", "AAA.SZ", "BBB.SZ"],
            "eligible": [True, False, True, True],
            "list_date": ["20200101", "20200101", "20200101", "20200101"],
            "delist_date": ["", "", "", "20240103"],
        }
    )
    kept = filter_listed_universe(frame)
    assert set(zip(kept["date"], kept["instrument"], strict=True)) == {
        ("20240102", "AAA.SZ"),
        ("20240103", "AAA.SZ"),
    }


def test_filter_listed_universe_requires_listing_columns():
    frame = pd.DataFrame({"date": ["20240102"], "instrument": ["AAA.SZ"], "close": [10.0]})
    with pytest.raises(ValueError, match="list_date|delist_date|eligible"):
        filter_listed_universe(frame)


def test_filter_index_universe_asof_drops_future_constituents():
    frame = pd.DataFrame(
        {
            "date": ["20180920", "20180920", "20181008", "20181008"],
            "instrument": ["AAA.SZ", "BBB.SZ", "AAA.SZ", "BBB.SZ"],
            "close": [1.0, 1.0, 1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000300.SH"],
            "con_code": ["AAA.SZ", "BBB.SZ"],
            "trade_date": ["20180903", "20181008"],
            "weight": [1.0, 1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH",))
    assert set(zip(kept["date"], kept["instrument"], strict=True)) == {
        ("20180920", "AAA.SZ"),
        ("20181008", "BBB.SZ"),
    }


def test_filter_index_universe_asof_unions_two_indices():
    frame = pd.DataFrame(
        {
            "date": ["20180920", "20180920", "20180920"],
            "instrument": ["AAA.SZ", "BBB.SZ", "CCC.SZ"],
            "close": [1.0, 1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000905.SH"],
            "con_code": ["AAA.SZ", "BBB.SZ"],
            "trade_date": ["20180903", "20180903"],
            "weight": [1.0, 1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH", "000905.SH"))
    assert set(kept["instrument"]) == {"AAA.SZ", "BBB.SZ"}


def test_filter_index_universe_asof_drops_days_before_first_snapshot():
    frame = pd.DataFrame(
        {
            "date": ["20180801", "20180920"],
            "instrument": ["AAA.SZ", "AAA.SZ"],
            "close": [1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["AAA.SZ"],
            "trade_date": ["20180903"],
            "weight": [1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH",))
    assert list(zip(kept["date"], kept["instrument"], strict=True)) == [("20180920", "AAA.SZ")]


def test_filter_index_universe_asof_accepts_dashed_dates():
    frame = pd.DataFrame(
        {
            "date": ["2018-09-20", "2018-09-20"],
            "instrument": ["AAA.SZ", "BBB.SZ"],
            "close": [1.0, 1.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["AAA.SZ"],
            "trade_date": ["20180903"],
            "weight": [1.0],
        }
    )
    kept = filter_index_universe_asof(frame, weights, ("000300.SH",))
    assert list(kept["instrument"]) == ["AAA.SZ"]
