from __future__ import annotations

from playwright.sync_api import Page


def test_manual_factor_page_exposes_safe_authoring_controls(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/factors/new")
    assert response is not None and response.ok
    page.get_by_role("heading", name="手动建立因子").wait_for()
    assert page.locator("#manual-factor-form").is_visible()
    assert page.get_by_role("button", name="校验并预览").is_visible()
    assert page.get_by_role("button", name="保存草稿").is_visible()
    assert "eval(" not in page.content()
