from __future__ import annotations

import json
import re

from playwright.sync_api import Page, Route, expect


def test_save_config_survives_refresh(browser_server: str, page: Page) -> None:
    saved: dict[str, object] = {}

    def handle_drafts(route: Route) -> None:
        request = route.request
        if request.method == "POST" and request.url.rstrip("/").endswith("/api/backtests/drafts"):
            body = request.post_data_json or {}
            saved["config"] = body.get("config") or {}
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {"draft_id": "draft-keep", "revision": 1, "config": saved["config"]},
                    ensure_ascii=False,
                ),
            )
            return
        if request.method == "GET" and "/api/backtests/drafts/" in request.url:
            config = saved.get("config") or {"name": "模板回测甲"}
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "draft_id": "draft-keep",
                        "revision": 1,
                        "complete": True,
                        "config": config,
                    },
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    page.route("**/api/backtests/drafts**", handle_drafts)
    response = page.goto(f"{browser_server}/backtests/new", wait_until="networkidle")
    assert response is not None and response.ok
    page.evaluate(
        """() => {
          const name = document.getElementById("name");
          const button = document.getElementById("save");
          if (name) name.value = "模板回测甲";
          if (button) button.disabled = false;
        }"""
    )
    page.locator("#save").click()
    expect(page).to_have_url(re.compile(r"draft_id=draft-keep"))
    expect(page.locator("#name")).to_have_value("模板回测甲")
    expect(page.locator("#save-template")).to_have_count(0)
    expect(page.locator("#status")).to_contain_text("配置已保存为草稿")
    expect(page.locator("#output-wrap")).to_have_class(re.compile(r"\bhidden\b"))
    page.reload(wait_until="networkidle")
    expect(page.locator("#name")).to_have_value("模板回测甲")
