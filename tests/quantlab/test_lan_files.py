from pathlib import Path

import pytest

from quantlab.services.lan_files import iter_rel_files, safe_under


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
