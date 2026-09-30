from quantlab.config import Settings
from quantlab.services.qlib_factor_loop import QlibFactorLoopService


def _settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=tmp_path / "data" / "raw",
    )


def _plan(plan_id: str, status: str, rounds: int, max_loops: int = 3) -> dict:
    return {
        "plan_id": plan_id,
        "created_at": "2026-09-30 15:00:00",
        "status": status,
        "max_loops": max_loops,
        "patience": max_loops,
        "model": "gpt-oss-120b",
        "train_end": "2022-12-31",
        "valid_end": "2024-12-31",
        "agent_calls": rounds,
        "rounds": [{"error": "", "formula": f"close{index}", "name": f"f{index}"} for index in range(rounds)],
        "stop_reason": "已停止" if status == "stopped" else "",
        "stop_requested": status == "stopped",
    }


def test_stop_requests_exit_and_stops_the_local_model(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.stop_local_gpt", lambda: calls.append("stop"))
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-running0001", "running", 1))
    service.stop("qlib-running0001")
    stored = service._load("qlib-running0001")
    assert stored["stop_requested"] is True
    assert stored["status"] == "running"
    assert calls == ["stop"]
    finished = service._finish_if_stopped("qlib-running0001")
    assert finished is not None
    assert finished["status"] == "stopped"
    assert finished["stop_reason"] == "已停止"


def test_stop_idle_plan_does_not_stop_the_model(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.stop_local_gpt", lambda: calls.append("stop"))
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-stopped0001", "stopped", 1))
    try:
        service.stop("qlib-stopped0001")
    except ValueError as error:
        assert "没有在跑" in str(error)
    else:
        raise AssertionError("idle plan should not stop the model")
    assert calls == []


def test_start_rejects_a_second_loop_while_one_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.stop_local_gpt", lambda: None)
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-running0001", "running", 1))
    service._write_plan(_plan("qlib-stopped0001", "stopped", 1))
    try:
        service.start("qlib-stopped0001")
    except ValueError as error:
        assert "已有循环在跑" in str(error)
    else:
        raise AssertionError("a second loop should be rejected")
    assert service._load("qlib-stopped0001")["status"] == "stopped"


def test_qlib_page_has_resume_picker_and_disables_start_while_mining() -> None:
    from pathlib import Path

    html = Path("quantlab/web/pages/qlib.html").read_text(encoding="utf-8")
    js = Path("quantlab/web/assets/qlib/page.js").read_text(encoding="utf-8")
    assert 'id="qlib-resume"' in html
    assert 'id="qlib-continue"' in html
    assert html.index('id="qlib-stop"') < html.index('id="qlib-resume"')
    assert 'plan.status === "running" || plan.status === "configured"' in js
    assert 'id="qlib-mining"' in html
    assert "startButton.disabled = !conversionReady || running" in js
    assert "/stop" in js
    assert "qlib-continue" in js
