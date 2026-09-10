from playwright.sync_api import Page, expect


def test_merged_factor_page_replaces_old_library_shell(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    response = page.goto(f"{browser_server}/factors")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="因子数据")).to_be_visible()
    expect(page.locator("#factor-table")).to_be_visible()
    expect(page.locator("#factor-name-search")).to_be_visible()
    expect(page.locator("#factor-category-filter")).to_be_visible()
    expect(page.locator("#factor-table")).to_contain_text("IC 均值")
    assert page.locator("#factor-library-table").count() == 0
    assert errors == []
