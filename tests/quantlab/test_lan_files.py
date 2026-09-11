from pathlib import Path

import pytest

from quantlab.services.lan_files import (
    classify_data_rel,
    filter_data_tree,
    iter_rel_files,
    normalize_data_categories,
    safe_under,
)


def test_safe_under_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "ok.parquet").write_bytes(b"a")
    assert safe_under(root, "ok.parquet").is_file()
    with pytest.raises(ValueError):
        safe_under(root, "../secret")
    with pytest.raises(ValueError):
        safe_under(root, "/etc/passwd")


def test_iter_rel_files_lists_nested(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "a.parquet").write_bytes(b"aa")
    (root / ".DS_Store").write_bytes(b"x")
    items = iter_rel_files(root)
    assert [item["rel"] for item in items] == ["raw/a.parquet"]
    assert items[0]["size"] == 2


def test_classify_data_rel_covers_asset_prefixed_roles() -> None:
    assert classify_data_rel("canonical.parquet") == "canonical"
    assert classify_data_rel("hk/canonical.parquet") == "canonical"
    assert classify_data_rel("derived/pack.parquet") == "derived"
    assert classify_data_rel("crypto/derived/x.parquet") == "derived"
    assert classify_data_rel("raw/daily/a.parquet") == "raw"
    assert classify_data_rel("source_tables/foo.parquet") == "source_tables"
    assert classify_data_rel("calibration/x.parquet") is None
    assert normalize_data_categories(["raw", "canonical"]) == ("canonical", "raw")
    with pytest.raises(ValueError):
        normalize_data_categories([])
    filtered = filter_data_tree(
        [{"rel": "canonical.parquet"}, {"rel": "raw/a.parquet"}, {"rel": "keep.parquet"}],
        ["canonical"],
    )
    assert [item["rel"] for item in filtered] == ["canonical.parquet"]
