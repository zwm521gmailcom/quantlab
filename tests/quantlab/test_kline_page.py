from pathlib import Path


PAGE = Path(__file__).parents[2] / "quantlab/web/pages/kline.html"
SCRIPT = Path(__file__).parents[2] / "quantlab/web/assets/data/kline.js"


def test_kline_page_has_controls_chart_quality_and_safe_rendering() -> None:
    page = PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    for element_id in (
        "kline-symbol",
        "kline-date-from",
        "kline-date-to",
        "kline-mode",
        "kline-table",
        "kline-pagination",
        "kline-chart",
        "kline-quality",
        "kline-export",
    ):
        assert f'id="{element_id}"' in page
    assert "/api/kline/query" in script
    assert "/api/kline/summary" not in script
    assert "/api/kline/quality" in script
    assert "qfq" not in page
    assert "前复权" not in script
    assert "qfq" not in script
    assert "innerHTML" not in script
    assert 'id="kline-version"' not in page
    assert 'value="000001.SZ"' in page
    assert 'class="kline-layout"' in page
    assert 'class="kline-quality-panel"' in page
