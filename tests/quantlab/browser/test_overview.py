from playwright.sync_api import Page, expect


OVERVIEW = {
    "summary": {"dataset_count": 2, "published_factor_count": 3, "strategy_count": 1},
    "run_status_counts": {"completed": 2, "running": 1, "failed": 1},
    "recent_runs": [
        {
            "run_type": "research",
            "run_id": "20260902-120000-0001",
            "name": "研究运行",
            "status": "completed",
            "created_at": "2026-09-02T12:00:00Z",
            "finished_at": "2026-09-02T12:01:00Z",
            "detail_url": "/research/runs/20260902-120000-0001",
        },
        {
            "run_type": "model_training",
            "run_id": "20260902-120001-0002",
            "name": "模型训练",
            "status": "running",
            "created_at": "2026-09-02T12:00:01Z",
            "finished_at": None,
            "detail_url": "/models/runs/20260902-120001-0002",
        },
        {
            "run_type": "backtest",
            "run_id": "20260902-120002-0003",
            "name": "回测",
            "status": "failed",
            "created_at": "2026-09-02T12:00:02Z",
            "finished_at": "2026-09-02T12:03:00Z",
            "detail_url": "/backtests/runs/20260902-120002-0003",
        },
    ],
    "quality_alerts": [
        {
            "entity_type": "dataset_version",
            "entity_id": "d1",
            "version_id": "v1",
            "quality_status": "warning",
            "message": "质量状态为 warning",
        }
    ],
}


def test_overview_renders_real_metrics_alerts_and_three_run_detail_links(
    browser_server: str, page: Page
) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.route("**/api/overview", lambda route: route.fulfill(status=200, json=OVERVIEW))

    page.goto(browser_server)

    expect(page.locator("#overview-summary")).to_contain_text("2")
    expect(page.locator("#quality-alerts").get_by_text("warning")).to_be_visible()
    expect(page.locator("a[href='/research/runs/20260902-120000-0001']")).to_be_visible()
    expect(page.locator("a[href='/models/runs/20260902-120001-0002']")).to_be_visible()
    expect(page.locator("a[href='/backtests/runs/20260902-120002-0003']")).to_be_visible()
    assert console_errors == []


def test_overview_shows_api_failure_without_fake_content(browser_server: str, page: Page) -> None:
    page.route(
        "**/api/overview",
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
    assert page.locator("#recent-runs").inner_text() == ""


def test_overview_renders_persisted_values_as_text_without_executing_markup(
    browser_server: str, page: Page
) -> None:
    malicious = '<img src="x" onerror="window.__xss = true">'
    payload = {
        "summary": {"dataset_count": 0, "published_factor_count": 0, "strategy_count": 0},
        "run_status_counts": {},
        "recent_runs": [
            {
                "run_type": "research",
                "run_id": "20260902-120000-0001",
                "name": malicious,
                "status": "completed",
                "created_at": "2026-09-02T12:00:00Z",
                "finished_at": None,
                "detail_url": "/research/runs/20260902-120000-0001",
            }
        ],
        "quality_alerts": [
            {
                "entity_type": "dataset_version",
                "entity_id": "d1",
                "version_id": "v1",
                "quality_status": "warning",
                "message": malicious,
            }
        ],
    }
    page.route("**/api/overview", lambda route: route.fulfill(status=200, json=payload))

    page.goto(browser_server)

    assert page.locator("#recent-runs").text_content() == malicious + "research · completed"
    assert page.locator("#quality-alerts").text_content() == malicious
    assert page.locator("#recent-runs img").count() == 0
    assert page.locator("#quality-alerts img").count() == 0
    assert page.evaluate("window.__xss") is None
