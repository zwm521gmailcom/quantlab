from __future__ import annotations

from playwright.sync_api import Page, expect


def test_factor_mining_page_exposes_config_run_and_audit_sections(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/research/factor-mining")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="自动挖掘因子")).to_be_visible()
    expect(page.locator("#factor-mining-form")).to_be_visible()
    expect(page.get_by_role("button", name="开始挖掘")).to_be_visible()
    expect(page.get_by_role("heading", name="运行状态")).to_have_count(0)
    expect(page.get_by_role("heading", name="候选结果")).to_have_count(0)
    expect(page.get_by_role("heading", name="完整血缘")).to_have_count(0)
    assert "自动发布" not in page.content()
    assert "转为草稿" not in page.content()
