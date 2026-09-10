from playwright.sync_api import Page, expect
import json


def test_merged_factor_page_serves_unified_catalog_without_old_library_actions(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    response = page.goto(f"{browser_server}/factors")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="因子数据")).to_be_visible()
    expect(page.locator("#factor-table")).to_be_visible()
    expect(page.locator("#factor-name-search")).to_be_visible()
    expect(page.locator("#factor-category-filter")).to_be_visible()
    assert page.locator("#factor-symbol").count() == 0
    page.wait_for_timeout(300)
    assert page.locator("#factor-library-actions").count() == 0
    assert page.locator("#factor-library-diagnose").count() == 0
    expect(page.locator("#factor-table .factor-header")).to_contain_text("操作")
    expect(page.locator("#factor-table .factor-row-actions button").filter(has_text="改名").first).to_be_visible()
    expect(page.locator("#factor-table .factor-row-actions button").filter(has_text="删除").first).to_be_visible()
    toolbar = page.locator("#factor-catalog-view .data-toolbar")
    expect(toolbar.get_by_role("button", name="查询样本")).to_have_count(0)
    expect(toolbar.get_by_role("button", name="下载受限样本")).to_have_count(0)
    expect(toolbar.get_by_role("link", name="手动建立因子")).to_have_count(0)
    expect(toolbar.get_by_role("link", name="自动挖掘因子")).to_have_count(0)
    expect(toolbar.get_by_role("link", name="因子计算任务")).to_have_count(0)
    expect(page.locator("aside").get_by_role("link", name="手动建立因子")).to_be_visible()
    expect(page.locator("aside").get_by_role("link", name="自动挖掘因子")).to_be_visible()
    expect(page.locator("aside").get_by_role("link", name="因子计算任务")).to_be_visible()
    assert errors == []


def test_factor_catalog_can_rename_a_row_and_rejects_builtin_delete(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error" and "409" not in message.text
        else None,
    )
    response = page.goto(f"{browser_server}/factors")
    assert response is not None and response.ok
    row = page.locator("#factor-table .factor-row").filter(has_text="momentum_5").first
    row.wait_for()
    original = row.locator("a.factor-name").inner_text()
    try:
        row.get_by_role("button", name="改名").click()
        editor = row.locator("input.factor-name-edit")
        editor.fill("动量改名测试")
        editor.press("Enter")
        expect(row.locator("a.factor-name")).to_have_text("动量改名测试")
        page.reload()
        row = page.locator("#factor-table .factor-row").filter(has_text="momentum_5").first
        expect(row.locator("a.factor-name")).to_have_text("动量改名测试")
        row.get_by_role("button", name="删除").click()
        expect(row.get_by_role("button", name="确认删除")).to_be_visible()
        row.get_by_role("button", name="确认删除").click()
        expect(page.locator("#factor-error")).to_contain_text("系统自带")
        expect(row.locator(".factor-row-error")).to_contain_text("系统自带")
        expect(row.get_by_role("button", name="删除")).to_be_visible()
        expect(row).to_be_visible()
    finally:
        page.request.patch(
            f"{browser_server}/api/factors/factor_momentum_5",
            data=json.dumps({"name": original}),
            headers={"content-type": "application/json"},
        )
    assert errors == []


def test_factor_catalog_filters_by_category_single_and_all(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    response = page.goto(f"{browser_server}/factors")
    assert response is not None and response.ok
    rows = page.locator("#factor-table .factor-row:not(.factor-header)")
    rows.first.wait_for()
    baseline = rows.count()
    page.locator("#factor-category-filter .multi-select-toggle").click()
    options = page.locator("#factor-category-filter input[name='factor-category']")
    expect(options.first).to_be_visible()
    category = options.first.input_value()
    page.locator("#factor-category-filter input[data-role='all']").uncheck()
    options.first.check()
    expect(rows.first).to_be_visible()
    for index in range(rows.count()):
        expect(rows.nth(index).locator(".factor-category")).to_have_text(category)
    page.locator("#factor-category-filter input[data-role='all']").check()
    expect(rows).to_have_count(baseline)
    assert errors == []
