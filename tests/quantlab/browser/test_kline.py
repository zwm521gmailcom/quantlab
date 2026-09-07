from playwright.sync_api import Page


def test_kline_page_uses_real_bounded_query_and_shows_status(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    response = page.goto(f"{browser_server}/kline?ts_code=000001.SZ")

    assert response is not None and response.ok
    assert page.get_by_role("heading", name="标准行情宽表").is_visible()
    page.locator("#kline-table").get_by_text("000001.SZ").first.wait_for()
    assert page.locator("#kline-table").get_by_text("raw_close").count() == 1
    assert page.locator("#kline-chart canvas").count() >= 1
    assert page.locator("#kline-chart .kline-chart-legend").is_visible()
    assert page.locator("#kline-quality").get_by_text("passed").is_visible()
    assert "后复权" in page.locator("#kline-chart").inner_text()
    assert errors == []
