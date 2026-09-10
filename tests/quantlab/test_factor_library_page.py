from pathlib import Path


ROOT = Path(__file__).parents[2]
FACTOR_PAGE = ROOT / "quantlab" / "web" / "pages" / "factors.html"
OLD_LIBRARY_PAGE = ROOT / "quantlab" / "web" / "pages" / "factor_library.html"
OLD_DETAIL_PAGE = ROOT / "quantlab" / "web" / "pages" / "factor_version_detail.html"
SCRIPT = ROOT / "quantlab" / "web" / "assets" / "factors" / "catalog.js"


def test_old_factor_library_page_files_were_removed() -> None:
    assert not OLD_LIBRARY_PAGE.exists()
    assert not OLD_DETAIL_PAGE.exists()


def test_merged_factor_page_uses_safe_dom_rendering() -> None:
    page = FACTOR_PAGE.read_text()
    script = SCRIPT.read_text()
    assert 'id="factor-table"' in page
    assert 'id="factor-name-search"' in page
    assert 'id="factor-category-filter"' in page
    assert "因子分类" in page
    assert 'id="factor-asset-class"' in page
    assert "资产分类" in page
    assert 'id="factor-symbol"' not in page
    assert "document.createElement" in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "populateFactorCategoryFilter" in script
    assert 'id="factor-query"' not in page
    assert 'id="factor-export"' not in page
    assert 'href="/factors/new/manual"' not in page
    assert 'href="/research/factor-mining"' not in page
    assert 'href="/research/factor-jobs"' not in page
