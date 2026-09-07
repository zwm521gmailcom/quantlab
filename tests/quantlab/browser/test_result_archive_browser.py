from playwright.sync_api import Page, expect


def test_result_archive_has_filterable_table_without_summary_sidebar(page: Page, browser_server: str) -> None:
    page.goto(browser_server + "/backtests/runs")
    expect(page.get_by_role("heading", name="结果档案")).to_be_visible()
    expect(page.locator("#archive-root")).to_be_visible()
    expect(page.locator("#query")).to_be_visible()
    expect(page.locator("#archive-table")).to_be_visible()
    expect(page.locator("#archive-table thead")).to_contain_text("回测")
    expect(page.locator("#archive-table thead")).to_contain_text("累计收益")
    expect(page.locator("#archive-table th")).to_have_count(10)
    expect(page.locator("text=清单统计")).to_have_count(0)
    expect(page.locator("text=查看方式")).to_have_count(0)
    expect(page.get_by_role("button", name="删除").first).to_be_visible()
