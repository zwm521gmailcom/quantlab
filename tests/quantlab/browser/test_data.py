from playwright.sync_api import Page, expect


def test_data_page_renders_real_catalog_and_safe_aliases(browser_server: str, page: Page) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

    response = page.goto(f"{browser_server}/data")

    assert response is not None and response.ok
    assert page.get_by_role("heading", name="数据中心").is_visible()
    page.locator("#dataset-table").get_by_text("daily", exact=True).first.wait_for()
    assert page.locator("#dataset-table").get_by_text("daily", exact=True).first.is_visible()
    assert page.locator("#dataset-table").get_by_text("adj_factor").first.is_visible()
    assert "七因子研究宽表" not in page.locator("#dataset-table").inner_text()
    assert "后复权加 ST 标准行情" not in page.locator("#dataset-table").inner_text()
    assert page.locator(".path-alias").all_inner_texts()
    assert "/Volumes/T2/vnpy" not in page.content()
    assert page.locator("#dataset-table img").count() == 0
    page.locator(".dataset-name-link").first.click()
    expect(page.locator("#raw-files-dialog")).to_be_visible()
    expect(page.locator("#raw-files-dialog .raw-files-header")).to_contain_text("建立时间")
    assert page.locator("#raw-files-dialog .raw-files-row:not(.raw-files-header)").count() <= 20
    assert console_errors == []


def test_data_page_filters_real_catalog_without_fake_rows(browser_server: str, page: Page) -> None:
    page.goto(f"{browser_server}/data")

    page.locator("#dataset-query").fill("does-not-exist")
    page.get_by_role("button", name="筛选").click()

    page.locator("#dataset-empty").wait_for(state="visible")
    assert page.locator("#dataset-empty").is_visible()
    assert page.locator("#dataset-table .dataset-row:not(.dataset-header)").count() == 0
    assert page.locator("#dataset-table .dataset-header").count() == 1
