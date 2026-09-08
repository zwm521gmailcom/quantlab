from playwright.sync_api import Page, expect


def test_backtest_new_shows_pretrade_formulas_and_seed(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/backtests/new", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator("#pretrade-stock-list .filter-expr-input").first).to_be_visible()
    expect(page.locator("#pretrade-benchmark-list")).to_be_visible()
    expect(page.locator("#open-filter-list")).to_be_visible()
    expect(page.locator("#random-seed")).to_have_value("123")
    expect(page.locator("#test-usage-note")).to_be_visible()
    expect(page.locator("#acknowledge-test-reuse")).to_have_count(0)
    expect(page.locator("#train-close-gt-ma200")).to_have_count(0)
    stock_exprs = page.locator("#pretrade-stock-list .filter-expr-input").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )
    assert "st_status == 0" in stock_exprs
    assert "is_suspended == 0" in stock_exprs
    first = page.locator("#pretrade-stock-list .filter-expr-row").first
    first.locator("select.filter-expr-fields").select_option("sma200")
    first.get_by_role("button", name="插入字段").click()
    assert "sma200" in first.locator(".filter-expr-input").input_value()
    payload = page.evaluate("() => window.configValue ? window.configValue() : null")
    if payload:
        assert payload["pretrade_filters"]["stock"]
        assert payload["hyperparameters"]["random_seed"] == 123


def test_backtest_run_status_module_updates_steps_and_log(browser_server: str, page: Page) -> None:
    response = page.goto(f"{browser_server}/backtests/new", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator('[data-panel-id="run-status"]')).to_be_visible()
    expect(page.locator("#run-steps li")).to_have_count(6)
    expect(page.locator("#run-log")).to_contain_text("尚未开始运行")
    page.evaluate(
        """() => window.applyRunStatus({
          run_id: "demo-run",
          status: "running",
          steps: [
            {step_name: "snapshot_validation", status: "completed"},
            {step_name: "model_training", status: "running"},
            {step_name: "prediction", status: "pending"},
            {step_name: "positions", status: "pending"},
            {step_name: "execution", status: "pending"},
            {step_name: "metrics", status: "pending"}
          ]
        })"""
    )
    expect(page.locator('#run-steps [data-step="model_training"]')).to_have_attribute("data-status", "running")
    expect(page.locator("#run-log")).to_contain_text("进入模型训练")
    page.evaluate(
        """() => window.applyRunStatus({
          run_id: "demo-run",
          status: "running",
          steps: [
            {step_name: "snapshot_validation", status: "completed"},
            {step_name: "model_training", status: "completed"},
            {step_name: "prediction", status: "running"},
            {step_name: "positions", status: "pending"},
            {step_name: "execution", status: "pending"},
            {step_name: "metrics", status: "pending"}
          ]
        })"""
    )
    expect(page.locator("#run-log")).to_contain_text("训练完成，进入回测")
    expect(page.locator("#run-log")).to_contain_text("进入预测打分")
    page.evaluate(
        """() => window.applyRunStatus({
          run_id: "demo-run",
          status: "completed",
          steps: [
            {step_name: "snapshot_validation", status: "completed"},
            {step_name: "model_training", status: "completed"},
            {step_name: "prediction", status: "completed"},
            {step_name: "positions", status: "completed"},
            {step_name: "execution", status: "completed"},
            {step_name: "metrics", status: "completed"}
          ]
        })"""
    )
    expect(page.locator("#run-record-link")).to_be_visible()
    expect(page.locator("#run-record-link")).to_have_attribute("href", "/backtests/runs/demo-run")
    expect(page.locator("#status")).to_contain_text("运行完成")
