import json

from playwright.sync_api import Page, expect


def _conditional_ic_run() -> dict:
    cap_labels = {1: "Q1 小盘", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 大盘"}
    turn_labels = {1: "Q1 低换手", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 高换手"}

    def buckets(labels: dict[int, str]) -> list[dict]:
        return [
            {
                "bucket": index,
                "label": labels[index],
                "ic_mean": 0.01 * index,
                "ic_positive_ratio": 0.6,
                "effective_days": 2,
                "daily_ic": [
                    {"date": "20240102", "ic": 0.01 * index, "count": 30},
                    {"date": "20240104", "ic": 0.02 * index, "count": 30},
                ],
            }
            for index in range(1, 6)
        ]

    conditional_ic = {
        "by_float_market_cap": {"field": "float_market_cap", "status": "available", "buckets": buckets(cap_labels)},
        "by_turn": {"field": "turn", "status": "available", "buckets": buckets(turn_labels)},
    }
    summary = {
        "label": "t+1 open -> t+2 close",
        "daily_ic": [{"date": "20240102", "ic": 0.05, "count": 150}, {"date": "20240104", "ic": 0.04, "count": 150}],
        "daily_top_bottom": [{"date": "20240102", "spread": 0.01}, {"date": "20240104", "spread": 0.02}],
        "conditional_ic": conditional_ic,
        "histogram": [
            {"left": -0.10, "right": 0.00, "count": 30, "expected": 25},
            {"left": 0.00, "right": 0.10, "count": 80, "expected": 70},
            {"left": 0.10, "right": 0.20, "count": 40, "expected": 35},
        ],
        "histogram_range": {
            "basis": "p1_p99",
            "scale": "linear",
            "lower": -0.10,
            "upper": 0.20,
            "lower_tail_count": 2,
            "upper_tail_count": 3,
            "fit_mean": 0.05,
            "fit_std": 0.05,
        },
    }
    return {
        "calculation_id": 99,
        "serial_no": "#99",
        "status": "completed",
        "date_from": "20240102",
        "date_to": "20240104",
        "started_at": "2024-01-04T00:00:00+00:00",
        "finished_at": "2024-01-04T00:00:01+00:00",
        "row_count": 300,
        "missing_rows": 0,
        "coverage": 1.0,
        "effective_days": 2,
        "ic_mean": 0.045,
        "ic_positive_ratio": 1.0,
        "ic_std": 0.01,
        "icir": 4.5,
        "top_bottom_spread": 0.015,
        "monotonicity": 0.8,
        "turnover_mean": 0.2,
        "factor_mean": 0.05,
        "factor_std": 0.05,
        "conditional_ic": conditional_ic,
        "summary": summary,
        "histogram": summary["histogram"],
        "histogram_range": summary["histogram_range"],
        "quantiles": {},
    }


def test_merged_factor_detail_serves_unified_factor_data_page(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    response = page.goto(f"{browser_server}/factors/momentum_5")
    assert response is not None and response.ok
    expect(page.get_by_role("heading", name="因子数据")).to_be_visible()
    expect(page.locator("#factor-detail")).to_contain_text("五日动量 · momentum_5")
    expect(page.locator("#factor-detail")).to_contain_text("公式中文解释")
    expect(page.locator("#factor-summary")).to_contain_text("有效性指标")
    expect(page.locator("#factor-charts")).to_be_visible()
    assert errors == []


def test_factor_detail_renders_conditional_ic_charts(browser_server: str, page: Page) -> None:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    payload = {"items": [_conditional_ic_run()], "count": 1}
    sample_payload = {
        "fields": ["date", "instrument", "momentum_5"],
        "items": [
            {"date": f"2024010{index}", "instrument": "000001.SZ", "momentum_5": f"0.0{index}"}
            for index in range(1, 16)
        ],
    }

    def handle_calculations(route) -> None:
        route.fulfill(status=200, content_type="application/json; charset=utf-8", body=json.dumps(payload, ensure_ascii=False))

    def handle_sample(route) -> None:
        route.fulfill(status=200, content_type="application/json; charset=utf-8", body=json.dumps(sample_payload, ensure_ascii=False))

    page.route("**/api/factor-data/query**", handle_sample)
    page.route("**/api/factor-calculations**", handle_calculations)
    response = page.goto(f"{browser_server}/factors/momentum_5")
    assert response is not None and response.ok
    expect(page.locator("#factor-summary")).to_contain_text("小盘 IC")
    expect(page.locator("#factor-summary")).to_contain_text("大盘 IC")
    expect(page.locator("#factor-charts")).to_contain_text("分层 IC · 流通市值")
    expect(page.locator("#factor-charts")).to_contain_text("Q1 小盘")
    expect(page.locator("#factor-charts")).to_contain_text("分层 IC · 换手率")
    cap_chart = page.locator(".factor-ic-lines").filter(has_text="流通市值").first
    turn_chart = page.locator(".factor-ic-lines").filter(has_text="换手率").first
    cap_q1 = cap_chart.get_by_role("button", name="Q1 小盘")
    turn_q5 = turn_chart.get_by_role("button", name="Q5 高换手")
    expect(cap_q1).to_have_attribute("aria-pressed", "true")
    expect(turn_q5).to_have_attribute("aria-pressed", "true")
    cap_q1.click()
    turn_q5.click()
    expect(cap_q1).to_have_attribute("aria-pressed", "false")
    expect(turn_q5).to_have_attribute("aria-pressed", "false")
    cap_q1.click()
    expect(cap_q1).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".factor-histogram")).to_contain_text("原始因子值分布")
    expect(page.locator(".factor-histogram")).to_contain_text("显示 P1–P99")
    expect(page.locator(".factor-histogram")).to_contain_text("线性")
    expect(page.locator(".factor-histogram")).not_to_contain_text("全部原始范围")
    expect(page.locator(".factor-histogram .factor-normal-overlay path")).to_have_count(1)
    expect(page.locator(".factor-normal-reference")).to_have_count(0)
    expect(page.locator("#factor-charts")).not_to_contain_text("正态分布参考")
    expect(page.locator("#factor-run-analysis")).to_have_text("重新运行分析")
    expect(page.locator("#factor-run-analysis")).to_be_enabled()
    expect(page.locator("#factor-sample .factor-row:not(.factor-header)")).to_have_count(10)
    expect(page.locator(".factor-sample-note")).to_contain_text("仅展示 10 行")
    assert errors == []
