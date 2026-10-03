from playwright.sync_api import Page, expect


PICKS = {
    "candidate_count": 2,
    "picks": [
        {
            "name": "alpha",
            "test_annual_return": 0.2,
            "test_benchmark_annual_return": 0.1,
            "test_information_ratio": 0.8,
            "test_max_drawdown": -0.15,
        },
        {
            "name": "beta",
            "test_annual_return": 0.35,
            "test_benchmark_annual_return": 0.12,
            "test_information_ratio": 1.4,
            "test_max_drawdown": -0.22,
        },
    ],
}


def test_overview_renders_real_metrics_alerts_and_three_run_detail_links(
    browser_server: str, page: Page
) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.route("**/api/qlib/picks", lambda route: route.fulfill(status=200, json=PICKS))

    page.goto(browser_server)

    for chart_id in ("chart-ir", "chart-excess", "chart-scatter", "chart-drawdown"):
        expect(page.locator(f"#{chart_id} svg")).to_be_visible()
    expect(page.locator("#chart-scatter title").filter(has_text="alpha")).to_have_count(1)
    assert console_errors == []


def test_overview_shows_api_failure_without_fake_content(browser_server: str, page: Page) -> None:
    page.route(
        "**/api/qlib/picks",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error_code":"INTERNAL_SERVER_ERROR","message":"internal server error"}',
        ),
    )

    page.goto(browser_server)

    page.locator("#error-state").wait_for(state="visible")
    assert page.locator("#error-state").is_visible()
    assert "服务连接失败：HTTP 500" in page.locator("#error-state").inner_text()
    assert page.locator("#chart-ir svg").count() == 0


def test_overview_renders_persisted_values_as_text_without_executing_markup(
    browser_server: str, page: Page
) -> None:
    malicious = '<img src="x" onerror="window.__xss = true">'
    payload = {
        "candidate_count": 1,
        "picks": [
            {
                "name": malicious,
                "test_annual_return": 0.2,
                "test_benchmark_annual_return": 0.05,
                "test_information_ratio": 0.6,
                "test_max_drawdown": -0.1,
            }
        ],
    }
    page.route("**/api/qlib/picks", lambda route: route.fulfill(status=200, json=payload))

    page.goto(browser_server)

    assert malicious in page.locator("#chart-scatter").text_content()
    assert page.locator("#chart-scatter img").count() == 0
    assert page.evaluate("window.__xss") is None
