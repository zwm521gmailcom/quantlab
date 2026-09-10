import re

from playwright.sync_api import Page


def test_foundation_shell_loads_navigation_and_real_health(browser_server: str, page: Page) -> None:
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    response = page.goto(browser_server)
    assert response is not None and response.ok
    assert page.get_by_role("link", name="模型中心").is_visible()
    assert page.locator("aside").get_by_role("link", name=re.compile("数据中心")).is_visible()
    assert page.locator("#empty-state").is_visible()
    assert "quantlab 服务已连接" in page.locator("#dataset-summary").inner_text()
    datasets_response = page.request.get(f"{browser_server}/api/datasets")
    assert datasets_response.ok
    datasets = datasets_response.json()["items"]
    canonical = next(item for item in datasets if item["entity_id"] == "ds_canonical_market")
    assert canonical["row_count"] == 10_282_666
    assert canonical["quality_status"] == "passed"
    assert "/Volumes/" not in datasets_response.text()
    assert "/Users/" not in datasets_response.text()
    page.reload()
    assert page.get_by_role("heading", name="研究总览").is_visible()
    refreshed = page.request.get(f"{browser_server}/api/datasets").json()["items"]
    refreshed_canonical = next(
        item for item in refreshed if item["entity_id"] == "ds_canonical_market"
    )
    assert refreshed_canonical == canonical
    not_found = page.request.get(f"{browser_server}/api/datasets/not-found")
    assert not_found.status == 404
    assert not_found.json() == {
        "error_code": "DATASET_NOT_FOUND",
        "message": "dataset not found",
        "entity_id": "not-found",
        "details": {},
    }
    assert "/Volumes/" not in page.content()
    assert "/Users/" not in page.content()
    assert console_errors == []
