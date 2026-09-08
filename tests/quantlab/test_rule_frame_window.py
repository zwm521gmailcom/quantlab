"""Rule backtest should slice Parquet by date instead of reading the whole file."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.services.rule_backtest import _read_frame


def test_read_frame_loads_only_the_requested_date_window(tmp_path) -> None:
    path = tmp_path / "canonical.parquet"
    dates = ["20180102", "20190102", "20200102", "20210104"]
    pq.write_table(
        pa.table(
            {
                "trade_date": dates,
                "ts_code": ["000001.SZ"] * len(dates),
                "hfq_close": [10.0, 11.0, 12.0, 13.0],
                "hfq_open": [10.0, 11.0, 12.0, 13.0],
                "amount": [80_000_000.0] * len(dates),
            }
        ),
        path,
    )

    frame = _read_frame(path, date_from="20200102", date_to="20200102", warmup_calendar_days=30)
    loaded = set(pd.Series(frame["date"]).map(lambda value: pd.Timestamp(value).strftime("%Y%m%d")))
    assert "20200102" in loaded
    assert "20180102" not in loaded
    assert "20210104" not in loaded
