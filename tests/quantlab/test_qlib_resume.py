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
    assert stored["status"] == "stopped"
    assert stored["stop_reason"] == "已停止"
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
    stopped = service.stop("qlib-stopped0001")
    assert stopped["status"] == "stopped"
    assert calls == []


def test_start_rejects_a_second_loop_while_one_is_running(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr("quantlab.services.qlib_factor_loop.stop_local_gpt", lambda: None)
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-running0001", "running", 1))
    service._write_plan(_plan("qlib-stopped0001", "stopped", 1))
    hold = threading.Event()
    worker = threading.Thread(target=hold.wait)
    worker.start()
    service._runners["qlib-running0001"] = worker
    try:
        service.start("qlib-stopped0001")
    except ValueError as error:
        assert "已有循环在跑" in str(error)
    else:
        raise AssertionError("a second loop should be rejected")
    finally:
        hold.set()
        worker.join()
    assert service._load("qlib-stopped0001")["status"] == "stopped"
    assert service._load("qlib-running0001")["status"] == "running"


def test_model_outage_stops_without_recording_failed_factors(tmp_path, monkeypatch):
    import pandas as pd

    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-down0000001", "running", 1, max_loops=5))
    index = pd.MultiIndex.from_product([["000001.SZ"], ["20200101", "20200102"]], names=["instrument", "date"])
    monkeypatch.setattr(service, "_score_panel", lambda: pd.DataFrame({"close": [1.0, 1.1]}, index=index))

    class Down:
        def run(self, prompt, *, model, workspace):
            raise RuntimeError("本机模型失败：<urlopen error [Errno 111] Connection refused>")

    def unavailable(timeout=None, cancel=None):
        raise RuntimeError("本机 GPT-OSS 120B 没有拉起：failed")

    def stop_on_wait(_seconds):
        plan = service._load("qlib-down0000001")
        plan["stop_requested"] = True
        service._write_plan(plan)

    monkeypatch.setattr("quantlab.services.qlib_factor_loop.ensure_local_gpt", unavailable)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.time.sleep", stop_on_wait)
    service.execute("qlib-down0000001", agent=Down())
    stored = service._load("qlib-down0000001")
    assert stored["status"] == "stopped"
    assert stored["stop_reason"] == "已停止"
    assert len(stored["rounds"]) == 1
    assert stored["rounds"][0]["name"] == "f0"
    assert stored["interruptions"]
    assert "没有拉起" in stored["interruptions"][-1]["message"]


def test_failed_call_does_not_clear_a_stop_or_restart_the_model(tmp_path, monkeypatch):
    import pandas as pd

    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-stoprace001", "running", 1, max_loops=5))
    index = pd.MultiIndex.from_product([["000001.SZ"], ["20200101", "20200102"]], names=["instrument", "date"])
    monkeypatch.setattr(service, "_score_panel", lambda: pd.DataFrame({"close": [1.0, 1.1]}, index=index))
    starts = []

    class Down:
        def run(self, prompt, *, model, workspace):
            plan = service._load("qlib-stoprace001")
            plan["stop_requested"] = True
            service._write_plan(plan)
            raise RuntimeError("本机模型失败：<urlopen error [Errno 111] Connection refused>")

    monkeypatch.setattr("quantlab.services.qlib_factor_loop.ensure_local_gpt", lambda: starts.append("start"))
    service.execute("qlib-stoprace001", agent=Down())
    stored = service._load("qlib-stoprace001")
    assert stored["status"] == "stopped"
    assert stored["stop_requested"] is True
    assert starts == []
    assert len(stored["rounds"]) == 1


def test_write_plan_keeps_stop_until_forced(tmp_path):
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-stopkeep001", "running", 1))
    plan = service._load("qlib-stopkeep001")
    plan["stop_requested"] = True
    service._write_plan(plan)
    stale = service._load("qlib-stopkeep001")
    stale["stop_requested"] = False
    stale["agent_calls"] = 9
    service._write_plan(stale)
    assert service._load("qlib-stopkeep001")["stop_requested"] is True
    stale["stop_requested"] = False
    service._write_plan(stale, force=True)
    assert service._load("qlib-stopkeep001")["stop_requested"] is False


def _manifest(tmp_path) -> None:
    folder = tmp_path / "data" / "qlib"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text('{"status":"completed"}', encoding="utf-8")


def test_orphan_running_plan_stops_and_can_start_again(tmp_path, monkeypatch):
    import threading

    calls = []
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.stop_local_gpt", lambda: calls.append("stop"))
    service = QlibFactorLoopService(_settings(tmp_path))
    service._write_plan(_plan("qlib-orphan00001", "running", 1, max_loops=5))
    reaped = service.status()
    orphan = next(plan for plan in reaped["plans"] if plan["plan_id"] == "qlib-orphan00001")
    assert orphan["status"] == "stopped"
    assert orphan["stop_reason"] == "已停止"
    assert calls == []

    _manifest(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def fake_execute(plan_id, agent=None, *, generation=None, cancel=None):
        started.set()
        release.wait(2)
        if cancel is not None and cancel.is_set():
            plan = service._load(plan_id)
            plan["status"] = "stopped"
            plan["stop_requested"] = True
            plan["stop_reason"] = "已停止"
            service._write_plan(plan, force=True)

    monkeypatch.setattr(service, "execute", fake_execute)
    first = service.start("qlib-orphan00001")
    assert first["status"] == "running"
    assert started.wait(2)
    stopped = service.stop("qlib-orphan00001")
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "已停止"
    release.set()
    service._runners["qlib-orphan00001"].join(2)
    again = threading.Event()

    def fake_again(plan_id, agent=None, *, generation=None, cancel=None):
        again.set()

    monkeypatch.setattr(service, "execute", fake_again)
    second = service.start("qlib-orphan00001")
    assert second["status"] == "running"
    assert again.wait(2)
    service.stop("qlib-orphan00001")
    thread = service._runners.get("qlib-orphan00001")
    if thread is not None:
        thread.join(2)
    assert service._load("qlib-orphan00001")["status"] == "stopped"
    assert calls


def test_purge_errors_keeps_successful_factors_and_refuses_a_live_run(tmp_path):
    import threading

    service = QlibFactorLoopService(_settings(tmp_path))
    plan = _plan("qlib-stopped0001", "stopped", 2, max_loops=1000)
    plan["rounds"].append({"name": "bad", "formula": "Ref(close, -1)", "error": "窗口必须是整数"})
    plan["interruptions"] = [{"message": "本机模型中断"}]
    service._write_plan(plan)
    purged = service.purge_errors("qlib-stopped0001")
    assert purged["removed"] == 1
    assert purged["kept"] == 2
    stored = service._load("qlib-stopped0001")
    assert stored["status"] == "stopped"
    assert stored["stop_reason"] == "已停止"
    assert [row["name"] for row in stored["rounds"]] == ["f0", "f1"]
    assert stored["interruptions"] == [{"message": "本机模型中断"}]
    running = _plan("qlib-running0001", "running", 1)
    running["rounds"].append({"name": "bad", "formula": "Ref(close, -1)", "error": "窗口必须是整数"})
    service._write_plan(running)
    hold = threading.Event()
    worker = threading.Thread(target=hold.wait)
    worker.start()
    service._runners["qlib-running0001"] = worker
    try:
        service.purge_errors("qlib-running0001")
    except ValueError as error:
        assert "先停止" in str(error)
    else:
        raise AssertionError("a live run should keep its error rows")
    finally:
        hold.set()
        worker.join()
    assert len(service._load("qlib-running0001")["rounds"]) == 2


def test_runs_page_can_purge_error_rows() -> None:
    from pathlib import Path

    html = Path("quantlab/web/pages/qlib_runs.html").read_text(encoding="utf-8")
    js = Path("quantlab/web/assets/qlib/runs.js").read_text(encoding="utf-8")
    assert 'id="qlib-purge-errors"' in html
    assert "/purge-errors" in js
    assert "成功的" in js


def test_local_prompt_shows_only_the_recent_catalog() -> None:
    from quantlab.services.qlib_factor_loop import LOCAL_CATALOG_LINES, _prompt_for_model

    history = [{"name": f"f{index}", "formula": f"close+{index}", "valid_ic": 0.01, "error": ""} for index in range(80)]
    prompt = _prompt_for_model(history, None, "gpt-oss-120b")
    assert "f0 close+0" not in prompt
    assert f"f{80 - LOCAL_CATALOG_LINES} close+{80 - LOCAL_CATALOG_LINES}" in prompt
    assert "f79 close+79" in prompt


def test_local_prompt_hides_failed_formulas_like_a_new_plan() -> None:
    from quantlab.services.qlib_factor_loop import _prompt_for_model

    history = [{"name": f"ok{index}", "formula": f"Mean(close, {index + 1})", "valid_ic": 0.01, "error": ""} for index in range(3)]
    history.extend(
        {"name": f"bad{index}", "formula": f"Ref(close, -{index + 1})", "valid_ic": None, "error": "窗口必须是整数"}
        for index in range(60)
    )
    prompt = _prompt_for_model(history, None, "gpt-oss-120b")
    assert "Mean(close, 1)" in prompt
    assert "Ref(close, -1)" not in prompt
    assert "窗口必须是整数" not in prompt


def test_qlib_page_has_resume_picker_and_disables_start_while_mining() -> None:
    from pathlib import Path

    html = Path("quantlab/web/pages/qlib.html").read_text(encoding="utf-8")
    js = Path("quantlab/web/assets/qlib/page.js").read_text(encoding="utf-8")
    assert 'id="qlib-resume"' in html
    assert 'id="qlib-continue"' in html
    assert html.index('id="qlib-stop"') < html.index('id="qlib-resume"')
    assert "resumeLabel" in js
    assert "created_at" in js
    assert 'id="qlib-mining"' in html
    assert "startButton.disabled = !conversionReady || running" in js
    assert "/stop" in js
    assert "qlib-continue" in js
