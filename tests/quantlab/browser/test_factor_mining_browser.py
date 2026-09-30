from __future__ import annotations

from playwright.sync_api import Page, expect


def test_retired_factor_mining_page_opens_qlib(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/research/factor-mining")
    assert response is not None and response.ok
    expect(page).to_have_url(f"{browser_server}/qlib")
    expect(page.get_by_role("heading", name="Qlib 挖因子")).to_be_visible()
    expect(page.locator("#factor-mining-form")).to_have_count(0)
    expect(page.get_by_role("link", name="自动挖掘因子")).to_have_count(0)
