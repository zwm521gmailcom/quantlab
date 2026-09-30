from __future__ import annotations

from playwright.sync_api import Page, expect


def test_legacy_factor_research_routes_redirect_to_factor_workflows(browser_server: str, page: Page) -> None:
    page.goto(f"{browser_server}/research/factors")
    expect(page).to_have_url(f"{browser_server}/factors")
    expect(page.get_by_role("heading", name="因子数据")).to_be_visible()

    page.goto(f"{browser_server}/research/factors/manual")
    expect(page).to_have_url(f"{browser_server}/factors/new/manual")
    expect(page.get_by_role("heading", name="手动建立因子")).to_be_visible()

    page.goto(f"{browser_server}/research/factors/auto")
    expect(page).to_have_url(f"{browser_server}/qlib")
    expect(page.get_by_role("heading", name="Qlib 挖因子")).to_be_visible()
