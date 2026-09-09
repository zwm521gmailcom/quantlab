from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _write_canonical(path: Path) -> None:
    table = pa.table(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
            "trade_date": ["20240102", "20240103", "20240102"],
            "close": [10.0, 10.5, 20.0],
            "is_suspended": [0, 0, 0],
            "eligible": [1, 1, 1],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _write_suspend(path: Path) -> None:
    table = pa.table(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
            "trade_date": ["20240102", "20240103", "20240102"],
            "suspend_type": ["S", "R", "S"],
            "suspend_timing": [None, None, "09:30-10:00"],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def test_apply_suspend_d_marks_s_rows_and_clears_eligible(tmp_path: Path) -> None:
    from quantlab.services.canonical_market import apply_suspend_d

    canonical = tmp_path / "canonical.parquet"
    suspend = tmp_path / "suspend_d.parquet"
    _write_canonical(canonical)
    _write_suspend(suspend)

    result = apply_suspend_d(canonical_path=canonical, suspend_path=suspend)
    table = pq.read_table(canonical).to_pydict()
    rows = {
        (code, date): (suspended, eligible)
        for code, date, suspended, eligible in zip(
            table["ts_code"], table["trade_date"], table["is_suspended"], table["eligible"], strict=True
        )
    }
    assert rows[("000001.SZ", "20240102")] == (1, 0)
    assert rows[("000001.SZ", "20240103")] == (0, 1)
    assert rows[("000002.SZ", "20240102")] == (1, 0)
    assert result["marked"] == 2
    assert result["rows"] == 3


def test_apply_suspend_d_requires_files(tmp_path: Path) -> None:
    from quantlab.services.canonical_market import apply_suspend_d

    canonical = tmp_path / "canonical.parquet"
    _write_canonical(canonical)
    try:
        apply_suspend_d(canonical_path=canonical, suspend_path=tmp_path / "missing.parquet")
    except ValueError as error:
        assert "suspend_d" in str(error)
    else:
        raise AssertionError("expected missing suspend_d to fail")


def test_data_center_can_merge_suspend_d_into_canonical() -> None:
    js = Path("quantlab/web/assets/data/datasets.js").read_text(encoding="utf-8")
    api_src = "".join(p.read_text(encoding="utf-8") for p in Path("quantlab/api").rglob("*.py"))
    assert "合并到宽表" in js
    assert "/api/raw/apply/suspend_d" in js
    assert "/api/raw/apply/suspend_d" in api_src
