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


def test_copied_draft_keeps_pretrade_formulas_out_of_train_and_test(browser_server: str, page: Page) -> None:
    shared = [
        "st_status == 0",
        "is_suspended == 0",
        "close > low",
        "close != up_limit AND close != down_limit",
    ]

    def handle_draft(route: Route) -> None:
        if route.request.method == "GET" and "/api/backtests/drafts/" in route.request.url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "draft_id": "draft-copy",
                        "revision": 1,
                        "complete": True,
                        "config": {
                            "name": "复制拆过滤",
                            "kind": "factor_rank",
                            "pretrade_filters": {"stock": shared, "benchmark": []},
                            "train": {
                                "date_from": "2019-01-02",
                                "date_to": "2019-12-31",
                                "filter": {"expressions": [*shared, "hfq_close > sma200"]},
                            },
                            "test": {
                                "date_from": "2026-01-05",
                                "date_to": "2026-08-31",
                                "filter": {"expressions": [*shared, "st_status==0 & close>low"]},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    page.route("**/api/backtests/drafts/**", handle_draft)
    response = page.goto(f"{browser_server}/backtests/new?draft_id=draft-copy", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator("#name")).to_have_value("复制拆过滤")
    pretrade = page.locator("#pretrade-stock-list .filter-expr-input").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )
    train = page.locator("#train-filter-list .filter-expr-input").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )
    test = page.locator("#test-filter-list .filter-expr-input").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )
    assert "st_status == 0" in pretrade
    assert "st_status == 0" not in train
    assert "st_status == 0" not in test
    assert train == ["hfq_close > sma200"]
    assert test == ["st_status==0 & close>low"]
