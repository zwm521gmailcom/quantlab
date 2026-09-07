from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from quantlab.config import Settings
from quantlab.services.catalog import snapshot_authoritative_data


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def browser_server(tmp_path_factory: pytest.TempPathFactory) -> str:
    port = _free_port()
    project_root = tmp_path_factory.mktemp("quantlab-browser")
    worktree_root = Path(__file__).resolve().parents[3]
    data_root = worktree_root / "data"
    calibration_root = worktree_root / "data/calibration"
    raw_root = worktree_root / "data/raw"
    runtime_root = project_root / "quantlab_runtime"
    settings = Settings(
        project_root=project_root,
        data_root=data_root,
        calibration_root=calibration_root,
        raw_root=raw_root,
        runtime_root=runtime_root,
    )
    baseline_before = settings.baselines_root / "before.json"
    snapshot_authoritative_data(settings, baseline_before)
    before_payload = json.loads(baseline_before.read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment["QUANTLAB_PROJECT_ROOT"] = str(project_root)
    environment["QUANTLAB_DATA_ROOT"] = str(data_root)
    environment["QUANTLAB_CALIBRATION_ROOT"] = str(calibration_root)
    environment["QUANTLAB_RAW_ROOT"] = str(raw_root)
    environment["QUANTLAB_RUNTIME_ROOT"] = str(runtime_root)
    init_command = [
        sys.executable,
        "-m",
        "quantlab.cli",
        "init-db",
        "--project-root",
        str(project_root),
        "--data-root",
        str(data_root),
        "--calibration-root",
        str(calibration_root),
        "--runtime-root",
        str(runtime_root),
    ]
    subprocess.run(
        init_command,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "quantlab.api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    import urllib.request

    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        raise RuntimeError(f"QuantLab server did not start: {stdout!r} {stderr!r}")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=5)
        baseline_after = settings.baselines_root / "after.json"
        snapshot_authoritative_data(settings, baseline_after)
        after_payload = json.loads(baseline_after.read_text(encoding="utf-8"))
        assert before_payload["roots"] == after_payload["roots"]
        assert before_payload["files"] == after_payload["files"]


@pytest.fixture
def page() -> Page:
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch()
        current_page = browser.new_page()
        yield current_page
        browser.close()
