import pandas as pd

from quantlab.services.qlib_export import EXTRA_FIELDS, convert_frame
from quantlab.services.qlib_factor_loop import _prompt_for_model, evaluate_formula


def _market() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20240102", "20240103"],
            "hfq_open": [10.0, 11.0],
            "hfq_high": [10.5, 11.5],
            "hfq_low": [9.5, 10.5],
            "hfq_close": [10.2, 11.2],
            "open": [10.0, 11.0],
            "high": [10.5, 11.5],
            "low": [9.5, 10.5],
            "close": [10.0, 11.0],
            "vol": [100.0, 110.0],
        }
    )


def _write_raw(root, folder: str, day: str, row: dict) -> None:
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_parquet(path / f"{day}.parquet", index=False)


def test_convert_joins_daily_basic_and_moneyflow(tmp_path):
    raw = tmp_path / "raw"
    _write_raw(
        raw,
        "daily_basic",
        "20240102",
        {"ts_code": "000001.SZ", "trade_date": "20240102", "close": 9.0, "turnover_rate": 1.5, "pe_ttm": 8.0, "circ_mv": 100.0},
    )
    _write_raw(
        raw,
        "daily_basic",
        "20240103",
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240103",
            "close": 9.1,
            "turnover_rate": 2.5,
            "pe": 7.0,
            "pb": 1.2,
            "pe_ttm": 8.5,
            "circ_mv": 101.0,
        },
    )
    _write_raw(
        raw,
        "moneyflow",
        "20240102",
        {"ts_code": "000001.SZ", "trade_date": "20240102", "net_mf_amount": 30.0, "buy_elg_vol": 4.0},
    )
    summary = convert_frame(_market(), tmp_path / "qlib" / "cn_data", raw_root=raw)
    panel = pd.read_parquet(summary["panel_path"])
    assert "turnover_rate" in summary["fields"]
    assert "net_mf_amount" in summary["fields"]
    assert list(panel["turnover_rate"]) == [1.5, 2.5]
    assert panel["pe"].isna().iloc[0]
    assert panel["pe"].iloc[1] == 7.0
    assert panel["pb"].iloc[1] == 1.2
    assert panel["net_mf_amount"].iloc[0] == 30.0
    assert panel["net_mf_amount"].isna().iloc[1]
    assert panel["close"].iloc[0] == 10.2
    assert not (tmp_path / "qlib" / "cn_data" / "features" / "sz000001" / "turnover_rate.day.bin").exists()
    assert (tmp_path / "qlib" / "cn_data" / "features" / "sz000001" / "close.day.bin").exists()


def test_formula_can_use_joined_fields():
    index = pd.MultiIndex.from_tuples(
        [("SZ000001", "2024-01-02"), ("SZ000001", "2024-01-03")],
        names=["instrument", "date"],
    )
    panel = pd.DataFrame(
        {"turnover_rate": [1.0, 2.0], "net_mf_amount": [5.0, 7.0]},
        index=index,
    )
    values = evaluate_formula("Mean(turnover_rate, 2) + net_mf_amount", panel)
    assert values.iloc[0] != values.iloc[0]
    assert values.iloc[1] == 8.5
    try:
        evaluate_formula("amount", panel)
    except ValueError as error:
        assert "不允许" in str(error)
    else:
        raise AssertionError("amount is not a formula field")


def test_prompt_lists_the_extra_fields():
    prompt = _prompt_for_model([], None, "gpt-oss-120b")
    assert "turnover_rate" in prompt
    assert "net_mf_amount" in prompt
    assert set(EXTRA_FIELDS) <= set(prompt.replace("、", " ").split())
