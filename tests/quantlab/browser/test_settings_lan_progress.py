from playwright.sync_api import Page, expect


def test_settings_lan_sync_shows_total_progress_and_percent(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/settings")
    assert response is not None and response.ok
    results = page.locator("#lan-progress-results")
    market = page.locator("#lan-progress-market")
    expect(results).to_contain_text("总进度")
    expect(results).to_contain_text("0 / 0")
    expect(results).to_contain_text("0%")
    expect(market).to_contain_text("总进度")
    expect(market).to_contain_text("0%")
    peer = page.locator("[data-peer-progress]")
    expect(peer).to_be_visible()
    expect(peer).to_contain_text("总进度")
    expect(peer).to_contain_text("0%")
    page.get_by_role("button", name="开始同步行情").click()
    expect(market).to_contain_text("100%")
    progress = page.request.get(f"{browser_server}/api/lan/sync/progress").json()
    assert progress["percent"] == 100
    assert progress["done"] == progress["total"]
