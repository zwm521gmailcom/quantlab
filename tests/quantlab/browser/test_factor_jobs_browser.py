import json

from playwright.sync_api import Page, expect


def test_factor_jobs_page_shows_empty_state_before_any_mining_run(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/research/factor-jobs")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="因子计算任务")).to_be_visible()
    expect(page.locator("#factor-jobs-empty")).to_be_visible()
    expect(page.locator("#factor-jobs-empty").get_by_role("link", name="自动挖掘因子")).to_be_visible()
    expect(page.get_by_role("button", name="入库并计算分析")).to_be_hidden()


def test_factor_jobs_nav_is_present_on_mining_page(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/research/factor-mining")
    assert response is not None and response.ok
    expect(page.get_by_role("link", name="因子计算任务")).to_be_visible()
    assert "转为草稿" not in page.content()


def test_select_kept_checks_importable_and_skips_rejected(browser_server: str, page: Page) -> None:
    payload = {
        "job": {"name": "自动因子挖掘", "status": "completed", "run_id": "run-1"},
        "config_summary": "测试任务",
        "items": [
            {
                "candidate_id": "keep-1",
                "kept_in_task": True,
                "enabled": False,
                "formula": "hfq_close",
                "formula_label": "后复权收盘",
                "reason": "validation_thresholds_passed",
                "reason_label": "验证期达标，可勾选入库",
                "period_metrics": {"validation": {"coverage": 1, "rank_ic": 0.1}},
            },
            {
                "candidate_id": "skip-1",
                "kept_in_task": False,
                "enabled": False,
                "formula": "hfq_close.rolling_mean(5)",
                "formula_label": "未达标公式",
                "reason": "correlated_with_existing_candidate",
                "reason_label": "与已有候选高度相关，任务内已去重",
                "period_metrics": {"validation": {"coverage": 1, "rank_ic": 0.1}},
            },
        ],
    }
    page.route("**/api/factor-jobs/run-1", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    response = page.goto(f"{browser_server}/research/factor-jobs?run_id=run-1")
    assert response is not None and response.ok
    keep = page.locator("#factor-jobs-items input[value='keep-1']")
    skip = page.locator("#factor-jobs-items input[value='skip-1']")
    expect(keep).to_be_checked()
    expect(skip).to_be_disabled()
    keep.uncheck()
    expect(keep).not_to_be_checked()
    page.get_by_role("button", name="勾选可入库项").click()
    expect(keep).to_be_checked()
    expect(skip).not_to_be_checked()
    expect(page.locator("#factor-jobs-result")).to_contain_text("已勾选 1 条")
