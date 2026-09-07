from playwright.sync_api import Page, expect
import pytest


def test_rule_backtest_page_shows_trend_params(
    browser_server: str, page: Page
) -> None:
    response = page.goto(f"{browser_server}/backtests/rules")
    assert response is not None and response.ok

    expect(page.locator("#signal-source")).to_have_count(0)
    expect(page.locator("#up-pct-60")).to_have_count(0)
    expect(page.locator("#up-pct-20")).to_have_count(0)
    expect(page.locator("#date-from")).to_be_visible()
    expect(page.locator("#date-to")).to_be_visible()
    expect(page.locator("#rule-strategy")).to_have_value("wiki_trend_follow")
    expect(page.locator("p.form-note strong")).to_contain_text("Wiki 多指标趋势跟踪")
    expect(page.locator("p.form-note")).to_contain_text("当时")
    expect(page.locator("p.form-note")).to_contain_text("下一交易日开盘")

    payload = page.evaluate("() => window.configValue()")
    assert payload["kind"] == "rule_signal"
    assert payload["rule_strategy_id"] == "wiki_trend_follow"
    assert payload["account_mode"] == "target_weight_exits"
    assert payload["strategy_entity_id"] is None
    assert payload["strategy_version_id"] is None
    assert payload["open_when_benchmark_gt_ma200"] is False
    assert payload.get("factor_versions") in ([], None)
    assert payload.get("model") in ({}, None)
    assert payload["stop_loss"] == pytest.approx(0.10)
    assert payload["take_profit"] == pytest.approx(0.25)
    assert payload["max_hold_days"] == 45
    assert payload["rebalance_every"] == 5
    assert payload["sell_fee_rate"] == pytest.approx(0.0005)
    assert payload["stamp_tax_rate"] == pytest.approx(0.001)
    assert payload["slippage"] == pytest.approx(0.0005)
    assert payload["sell_fee_rate"] + payload["stamp_tax_rate"] == pytest.approx(0.0015)
    params = payload.get("strategy_params") or payload.get("params") or {}
    assert "up_pct_60" not in params
    assert "up_pct_20" not in params
    assert params["rsi_low"] == pytest.approx(40)
    assert params["rsi_high"] == pytest.approx(72)
    assert payload["test"]["date_from"]
    assert payload["test"]["date_to"]

    save = page.get_by_role("button", name="保存配置")
    expect(save).to_be_enabled()
    with page.expect_response(
        lambda resp: "/api/backtests/drafts" in resp.url or "/api/backtests/validate" in resp.url,
        timeout=20000,
    ) as pending:
        save.click()
    resp = pending.value
    body = resp.text()
    assert resp.status != 500, body
    assert resp.status in {200, 201, 400} or "成分" in body
    if resp.status >= 400:
        assert "成分" in body


def test_backtest_new_has_no_rule_strategy_source(
    browser_server: str, page: Page
) -> None:
    response = page.goto(f"{browser_server}/backtests/new", wait_until="networkidle")
    assert response is not None and response.ok
    expect(page.locator("#signal-source")).to_have_count(0)
    expect(page.get_by_role("option", name="规则策略")).to_have_count(0)
    expect(page.locator("#up-pct-60")).to_have_count(0)
    expect(page.locator("#model")).to_be_visible()
