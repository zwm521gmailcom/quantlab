from playwright.sync_api import Page


def test_factor_page_shows_versioned_catalog_without_selection_sidebar(
    browser_server: str, page: Page
) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

    response = page.goto(f"{browser_server}/data/factors")

    assert response is not None and response.ok
    assert page.get_by_role("heading", name="因子数据").is_visible()
    momentum_row = page.locator("#factor-table .factor-row").filter(has_text="momentum_5").first
    momentum_row.wait_for()
    assert "五日动量" in momentum_row.inner_text()
    assert page.locator("#factor-table .factor-header").get_by_text("资产分类").is_visible()
    assert "A股" in momentum_row.inner_text()
    assert page.locator("#factor-asset-class").is_visible()
    assert page.locator("#factor-table .factor-row").count() == 8
    assert "hfq_close[t]" not in page.locator("#factor-table").inner_text()
    assert page.locator("#factor-table").get_by_text("100.00%").count() >= 1
    assert "当前选择" not in page.content()
    assert "innerHTML" not in page.content()
    assert errors == []


def test_factor_detail_shows_quality_summary_and_sample(browser_server: str, page: Page) -> None:
    summary_requests: list[str] = []
    page.on("request", lambda request: summary_requests.append(request.url) if "/api/factor-data/summary" in request.url else None)
    response = page.goto(f"{browser_server}/data/factors/momentum_5")

    assert response is not None and response.ok
    assert page.get_by_role("heading", name="因子数据").is_visible()
    page.locator("#factor-detail").get_by_text("五日动量 · momentum_5").wait_for()
    assert page.locator("#factor-summary").get_by_text("注册覆盖率", exact=True).is_visible()
    assert summary_requests == []
    assert page.locator("#factor-sample").get_by_text("momentum_5").count() == 1
    assert page.locator("#factor-detail").get_by_text("公式中文解释").is_visible()
    assert page.locator("#factor-detail .factor-formula-expression").is_visible()
    assert "5 observations" in page.locator("#factor-detail .factor-formula-expression").inner_text()
