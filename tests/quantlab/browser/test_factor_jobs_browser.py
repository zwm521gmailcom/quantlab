from playwright.sync_api import Page, expect


def test_retired_factor_jobs_page_opens_factor_library(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/research/factor-jobs")
    assert response is not None and response.ok
    expect(page).to_have_url(f"{browser_server}/factors")
    expect(page.get_by_role("heading", name="因子数据")).to_be_visible()
    expect(page.get_by_role("heading", name="因子计算任务")).to_have_count(0)


def test_factor_jobs_link_is_absent_from_qlib_nav(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/qlib")
    assert response is not None and response.ok
    expect(page.get_by_role("link", name="因子计算任务")).to_have_count(0)
    expect(page.get_by_role("link", name="Qlib 挖因子")).to_be_visible()
