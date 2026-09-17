from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.backtest_job import attach_sma, prepare_research_frame, research_frame_date_span
from quantlab.services.index_membership import filter_index_universe_asof


def test_stock_sma_matches_before_and_after_universe_filter():
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200103", "20200106"] * 2,
            "instrument": ["KEEP.SZ"] * 3 + ["DROP.SZ"] * 3,
            "hfq_close": [10.0, 11.0, 12.0, 20.0, 21.0, 22.0],
        }
    )
    full = attach_sma(frame.copy(), window=2)
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"] * 2,
            "con_code": ["KEEP.SZ", "KEEP.SZ"],
            "trade_date": ["20191231", "20191231"],
            "weight": [1.0, 1.0],
        }
    )
    pit = filter_index_universe_asof(frame.copy(), weights, ("000300.SH",))
    after = attach_sma(pit, window=2)
    keep_full = full.loc[full["instrument"] == "KEEP.SZ", "sma_2"].reset_index(drop=True)
    keep_after = after.loc[after["instrument"] == "KEEP.SZ", "sma_2"].reset_index(drop=True)
    pd.testing.assert_series_equal(keep_full, keep_after, check_names=False)


def test_prepare_research_frame_filters_before_attaching_sma(tmp_path, monkeypatch):
    calls: list[tuple[str, int]] = []
    frame = pd.DataFrame(
        {
            "date": ["20200102", "20200102"],
            "instrument": ["KEEP.SZ", "DROP.SZ"],
            "hfq_close": [10.0, 20.0],
            "close": [10.0, 20.0],
        }
    )
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "con_code": ["KEEP.SZ"],
            "trade_date": ["20191231"],
            "weight": [1.0],
        }
    )
    raw = tmp_path / "raw"
    (raw / "index_weight").mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(weights.to_dict("records")),
        raw / "index_weight" / "index_weight_000300_SH.parquet",
    )

    import quantlab.services.backtest_job as job

    def track_attach(frame_in, config):
        calls.append(("sma", int(len(frame_in))))
        return frame_in

    monkeypatch.setattr(job, "_attach_config_sma", track_attach)
    config = {
        "universe_index_codes": ["000300.SH"],
        "factor_versions": [],
        "train": {"filter": {"expressions": ["hfq_close > sma200"]}},
        "test": {"filter": {"expressions": ["hfq_close > sma200"]}},
    }
    out = prepare_research_frame(frame, config, raw, sidecar_path=tmp_path / "missing.parquet")
    assert list(out["instrument"]) == ["KEEP.SZ"]
    assert calls and calls[0][0] == "sma"
    assert calls[0][1] == 1


def test_factor_rank_date_span_uses_test_window_only():
    config = {
        "kind": "factor_rank",
        "train": {"date_from": "2017-01-03", "date_to": "2017-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2026-08-31"},
    }
    assert research_frame_date_span(config) == ("20240102", "20260831")


def test_tree_model_date_span_covers_train_and_test():
    config = {
        "kind": "lightgbm_tree",
        "train": {"date_from": "2017-01-03", "date_to": "2017-12-29"},
        "test": {"date_from": "2024-01-02", "date_to": "2026-08-31"},
    }
    assert research_frame_date_span(config) == ("20170103", "20260831")
