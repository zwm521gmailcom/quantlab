import json

import numpy as np
import pandas as pd

from quantlab.config import Settings
from quantlab.services.qlib_factor_loop import (
    LOCAL_CATALOG_LINES,
    QlibFactorLoopService,
    _max_abs_daily_corr,
    _mined_factor_count,
    _prompt_for_model,
    extract_factor_batch,
)
from quantlab.services.qlib_strategy import _score_masks, lightgbm_scores


def _settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        calibration_root=tmp_path / "calibration",
        runtime_root=tmp_path / "runtime",
        raw_root=tmp_path / "data" / "raw",
    )


def _plan(plan_id: str, rounds: list[dict], max_loops: int = 5) -> dict:
    return {
        "plan_id": plan_id,
        "created_at": "2026-10-01T10:00:00+08:00",
        "status": "running",
        "max_loops": max_loops,
        "patience": max_loops,
        "model": "gpt-oss-120b",
        "train_end": "2022-12-31",
        "valid_end": "2024-12-31",
        "agent_calls": 0,
        "rounds": rounds,
        "stop_reason": "",
        "stop_requested": False,
    }


def _counted(name: str, formula: str, ir: float | None = None) -> dict:
    row = {"name": name, "formula": formula, "error": "", "valid_ic": 0.01, "test_ic": 0.01}
    if ir is not None:
        row["valid_information_ratio"] = ir
        row["valid_annual_return"] = 0.1
    return row


def _panel() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [["000001.SZ"], ["2020-01-02", "2020-01-03"]],
        names=["instrument", "date"],
    )
    return pd.DataFrame({"close": [1.0, 1.1]}, index=index)


class _Script:
    def __init__(self, payloads: list[str], stop_at: int | None = None, service=None, plan_id: str = ""):
        self.payloads = payloads
        self.stop_at = stop_at
        self.service = service
        self.plan_id = plan_id
        self.calls = 0

    def run(self, prompt, *, model, workspace):
        self.calls += 1
        if self.stop_at is not None and self.calls == self.stop_at and self.service is not None:
            plan = self.service._load(self.plan_id)
            plan["stop_requested"] = True
            self.service._write_plan(plan)
        return self.payloads[min(self.calls - 1, len(self.payloads) - 1)]


def _proposal(name: str, formula: str) -> str:
    return json.dumps({"name": name, "formula": formula, "reason": "测试"}, ensure_ascii=False)


def _book(annual: float, benchmark: float, ir: float, *, error: str = "") -> dict:
    if error:
        return {"error": error, "strategy": "TopkDropout", "model": "LightGBM"}
    return {
        "test_annual_return": annual,
        "test_benchmark_annual_return": benchmark,
        "test_information_ratio": ir,
        "test_max_drawdown": -0.1,
        "strategy": "TopkDropout",
        "model": "LightGBM",
    }


def test_default_scores_stay_after_valid_end_and_keep_early_stopping(monkeypatch):
    import lightgbm as lgb

    seen = []
    real = lgb.early_stopping

    def wrapped(rounds, verbose=False):
        seen.append(rounds)
        return real(rounds, verbose=verbose)

    monkeypatch.setattr(lgb, "early_stopping", wrapped)
    days = pd.bdate_range("2020-01-01", periods=70)
    train_end = str(days[39].date())
    valid_end = str(days[54].date())
    dates = []
    values = []
    future = []
    for index, day in enumerate(days):
        for side in (0.0, 1.0):
            dates.append(str(day.date()))
            values.append(float(index) + side)
            future.append(0.001 * ((index % 5) - 2) + side * 0.0001)
    factor = pd.Series(values)
    scored = lightgbm_scores(
        factor,
        pd.Series(future),
        pd.Series(dates),
        train_end=train_end,
        valid_end=valid_end,
    )
    got = set(pd.Series(dates)[scored.notna()])
    assert got
    assert all(day > valid_end for day in got)
    assert seen == [10]


def test_validation_training_rows_stay_inside_train_and_scores_stay_in_valid():
    days = [f"2020-01-{day:02d}" for day in range(1, 32)]
    dates = pd.Series(days)
    train_end = "2020-01-10"
    valid_end = "2020-01-20"
    train, valid, score = _score_masks(dates, train_end, valid_end, score_after=train_end, score_end=valid_end)
    assert set(dates[train]) <= set(dates[dates <= train_end])
    assert dates[train].max() <= train_end
    assert set(dates[score]) <= set(dates[(dates > train_end) & (dates <= valid_end)])
    assert not set(dates[train]) & set(dates[score])
    assert valid.any()


def _patch_books(monkeypatch, books: list[str], queued: list[float | str]) -> None:
    def fake(*args, book="test", **kwargs):
        books.append(book)
        item = queued.pop(0)
        if isinstance(item, str):
            return _book(0, 0, 0, error=item)
        return _book(0.2, 0.05, item)

    monkeypatch.setattr("quantlab.services.qlib_factor_loop.run_factor_strategy", fake)


def test_a_factor_that_misses_its_own_test_is_recorded_and_does_not_count(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan_id = "qlib-validfail01"
    service._write_plan(_plan(plan_id, [_counted("old", "close")]))
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    books: list[str] = []
    _patch_books(monkeypatch, books, [0.4, -0.2])
    before = _mined_factor_count(service._load(plan_id)["rounds"])
    agent = _Script([_proposal("new", "volume")], stop_at=1, service=service, plan_id=plan_id)
    service.execute(plan_id, agent=agent)
    stored = service._load(plan_id)
    saved = stored["rounds"][-1]
    assert _mined_factor_count(stored["rounds"]) == before
    assert saved["error"] == ""
    assert saved["accepted"] is False
    assert saved["own_valid"]["information_ratio"] == 0.4
    assert saved["own_test"]["test_information_ratio"] == -0.2
    assert "test_ic" in saved
    assert books == ["valid", "test"]
    assert stored["status"] == "stopped"


def test_passing_validation_counts_and_keeps_the_test_book(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan_id = "qlib-validpass01"
    service._write_plan(_plan(plan_id, [_counted("old", "close")], max_loops=2))
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    books: list[str] = []
    _patch_books(monkeypatch, books, [0.4, 0.55])
    service.execute(plan_id, agent=_Script([_proposal("new", "volume")]))
    stored = service._load(plan_id)
    assert _mined_factor_count(stored["rounds"]) == 2
    saved = stored["rounds"][-1]
    assert saved["error"] == ""
    assert saved["accepted"] is True
    assert saved["valid_information_ratio"] == 0.4
    assert saved["own_valid"]["annual_return"] == 0.2
    assert saved["own_test"]["test_information_ratio"] == 0.55
    assert "test_ic" in saved
    assert saved["strategy"]["test_information_ratio"] == 0.55
    assert books == ["valid", "test"]
    assert stored["status"] == "completed"


def test_strategy_error_does_not_count(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan_id = "qlib-straterr001"
    service._write_plan(_plan(plan_id, [_counted("old", "close")]))
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    _patch_books(monkeypatch, [], ["训练样本不够", "训练样本不够"])
    agent = _Script([_proposal("new", "volume")], stop_at=1, service=service, plan_id=plan_id)
    service.execute(plan_id, agent=agent)
    stored = service._load(plan_id)
    assert _mined_factor_count(stored["rounds"]) == 1
    assert stored["rounds"][-1]["error"] == "训练样本不够"


def test_a_new_formula_is_scored_on_its_own_even_when_correlation_is_high(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan_id = "qlib-corr000001"
    service._write_plan(_plan(plan_id, [_counted("base", "close")], max_loops=5))
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    books: list[str] = []
    _patch_books(monkeypatch, books, [0.2, -0.4])
    agent = _Script([_proposal("w20", "Mean(close, 20)")], stop_at=1, service=service, plan_id=plan_id)
    service.execute(plan_id, agent=agent)
    stored = service._load(plan_id)
    saved = stored["rounds"][-1]
    assert saved["error"] == ""
    assert saved["accepted"] is False
    assert saved["own_valid"]["information_ratio"] == 0.2
    assert saved["own_test"]["test_information_ratio"] == -0.4
    assert _mined_factor_count(stored["rounds"]) == 1
    assert books == ["valid", "test"]
    assert stored["last_feedback"]["scored"][0]["name"] == "w20"


def test_a_batch_keeps_each_factor_on_its_own_book(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan_id = "qlib-batch00001"
    service._write_plan(_plan(plan_id, [], max_loops=5))
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    books: list[str] = []
    _patch_books(monkeypatch, books, [0.11, 0.4, 0.22, 0.8])
    payload = json.dumps(
        {
            "hypothesis": "放量后还会延续",
            "factors": [
                {"name": "alpha", "formula": "close", "reason": "甲"},
                {"name": "beta", "formula": "volume", "reason": "乙"},
            ],
        },
        ensure_ascii=False,
    )
    agent = _Script([payload], stop_at=1, service=service, plan_id=plan_id)
    service.execute(plan_id, agent=agent)
    stored = service._load(plan_id)
    assert [row["name"] for row in stored["rounds"]] == ["alpha", "beta"]
    assert all(row["accepted"] for row in stored["rounds"])
    assert stored["rounds"][0]["hypothesis"] == "放量后还会延续"
    assert stored["rounds"][0]["strategy"]["test_information_ratio"] == 0.4
    assert stored["rounds"][1]["strategy"]["test_information_ratio"] == 0.8
    assert stored["rounds"][0]["own_valid"]["information_ratio"] == 0.11
    assert stored["rounds"][1]["own_valid"]["information_ratio"] == 0.22
    assert books == ["valid", "test", "valid", "test"]
    assert stored["last_feedback"]["scored"][0]["accepted"] is True
    assert stored["last_feedback"]["scored"][1]["test_information_ratio"] == 0.8


def test_prompt_ranks_counted_formulas_and_hides_failures_and_test_fields():
    history = [
        {"name": f"bad{index}", "formula": f"Ref(close, -{index + 1})", "error": "窗口必须是整数", "valid_ic": None}
        for index in range(60)
    ]
    history.extend(
        [
            {"name": "low", "formula": "Mean(close, 1)", "error": "", "valid_ic": 0.01, "valid_information_ratio": 0.1, "test_information_ratio": 9},
            {"name": "mid", "formula": "Mean(close, 2)", "error": "", "valid_ic": 0.02, "valid_information_ratio": 0.2},
            {"name": "high", "formula": "Mean(close, 3)", "error": "", "valid_ic": 0.03, "valid_information_ratio": 0.9},
        ]
    )
    prompt = _prompt_for_model(history, [{"name": "x", "formula": "Mean(close, 8)", "valid_information_ratio": 0.2}], "gpt-oss-120b")
    assert "Mean(close, 1)" in prompt
    assert "Mean(close, 3)" in prompt
    assert "Ref(close, -1)" not in prompt
    assert prompt.index("Mean(close, 3)") < prompt.index("Mean(close, 1)")
    assert "test_information_ratio" not in prompt
    assert "test_annual_return" not in prompt
    assert "hypothesis" in prompt
    assert "单独做验证和测试" in prompt
    assert "测试年化高于该因子自己的基准" in prompt
    assert "0.99" not in prompt
    wide = [
        {"name": f"f{index}", "formula": f"close+{index}", "error": "", "valid_ic": 0.01, "valid_information_ratio": index / 100}
        for index in range(80)
    ]
    wide_prompt = _prompt_for_model(wide, None, "gpt-oss-120b")
    assert "f79 close+79" in wide_prompt
    assert f"f{80 - LOCAL_CATALOG_LINES} close+{80 - LOCAL_CATALOG_LINES}" in wide_prompt
    assert "f0 close+0" not in wide_prompt
    assert "f31 close+31" not in wide_prompt


def test_alpha158_matches_the_qlib_column_count_and_kmid():
    from quantlab.services.qlib_alpha158 import alpha158_frame

    dates = pd.bdate_range("2020-01-01", periods=70).strftime("%Y-%m-%d")
    index = pd.MultiIndex.from_product([["000001.SZ"], list(dates)], names=["instrument", "date"])
    close = 10 + np.arange(len(dates), dtype=float)
    frame = pd.DataFrame(
        {
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": np.full(len(dates), 1000.0),
        },
        index=index,
    )
    got = alpha158_frame(frame)
    assert got.shape[1] == 158
    assert got["KMID"].iloc[0] == np.float64((close[0] - (close[0] - 1)) / (close[0] - 1))
    assert got["ROC5"].iloc[5] == close[0] / close[5]
    assert got["MA5"].iloc[4] == close[:5].mean() / close[4]


def test_copy_correlation_reaches_the_drop_line():
    names = [f"s{i}" for i in range(10)]
    index = pd.MultiIndex.from_product([names, ["2020-01-02", "2020-01-03"]], names=["instrument", "date"])
    values = np.tile(np.linspace(-1, 1, 10), 2)
    factor = pd.Series(values, index=index)
    library = pd.DataFrame({"KMID": values}, index=index)
    dates = pd.Series(index.get_level_values("date"), index=index)
    assert _max_abs_daily_corr(factor, library, dates) > 0.99


def test_extract_factor_batch_keeps_a_hypothesis_and_four_factors():
    payload = {
        "hypothesis": "高换手之后会回落",
        "factors": [{"name": f"n{i}", "formula": f"close+{i}", "reason": "测试"} for i in range(6)],
    }
    batch = extract_factor_batch(json.dumps(payload, ensure_ascii=False))
    assert batch["hypothesis"] == "高换手之后会回落"
    assert len(batch["factors"]) == 4
    single = extract_factor_batch(_proposal("one", "close"))
    assert single["factors"][0]["formula"] == "close"


def test_runs_page_shows_validation_book_columns():
    from pathlib import Path

    html = Path("quantlab/web/pages/qlib_runs.html").read_text(encoding="utf-8")
    js = Path("quantlab/web/assets/qlib/runs.js").read_text(encoding="utf-8")
    order = [
        'data-sort="valid_ic"',
        'data-sort="valid_annual"',
        'data-sort="valid_ir"',
        'data-sort="test_ic"',
        'data-sort="test_annual"',
        'data-sort="test_drawdown"',
        'data-sort="test_ir"',
    ]
    positions = [html.index(token) for token in order]
    assert positions == sorted(positions)
    assert "验证 IC" in html
    assert "测试回撤" in html
    assert "测试信息比率" in html
    assert "own_valid" in js
    assert "own_test" in js
    assert "待计算" in js
    assert "过线" in js
    assert "查看回测" in js
    assert "/own-archive" in js


def test_weight_changes_sell_before_buy_in_book_order() -> None:
    from quantlab.services.qlib_strategy import _record_weight_trades

    prices = pd.Series({"SZ000002": 10.0, "SH600001": 20.0, "SH600519": 30.0})
    previous = {"SZ000002": 0.5, "SH600001": 0.5}
    current = {"SH600519": 0.5, "SH600001": 0.5}
    episodes = {
        "SZ000002": {
            "buy_date": "2025-01-02",
            "buy_price": 10.0,
            "buy_amount": 100.0,
            "buy_fee": 1.0,
            "shares": 10.0,
            "hold_pnl": 5.0,
            "weight": 0.5,
        },
        "SH600001": {
            "buy_date": "2025-01-02",
            "buy_price": 20.0,
            "buy_amount": 100.0,
            "buy_fee": 1.0,
            "shares": 5.0,
            "hold_pnl": 0.0,
            "weight": 0.5,
        },
    }
    trades: list[dict] = []
    _record_weight_trades(episodes, trades, previous, current, prices, 1000.0, "2025-01-03")
    assert [(row["reason"], row["instrument"]) for row in trades] == [("平仓", "SZ000002")]
    assert trades[0]["buy_date"] == "2025-01-02"
    assert trades[0]["sell_date"] == "2025-01-03"
    opened: list[dict] = []
    episodes: dict = {}
    _record_weight_trades(episodes, opened, {}, {"SH600519": 0.5, "SH600001": 0.5}, prices, 1000.0, "2025-01-02")
    assert opened == []
    assert list(episodes) == ["SH600519", "SH600001"]


def test_publish_own_archive_writes_the_factor_test_book_and_keeps_the_old_run(tmp_path, monkeypatch):
    service = QlibFactorLoopService(_settings(tmp_path))
    plan = _plan("qlib-archiv0001", [])
    plan["status"] = "stopped"
    plan["rounds"] = [
        {
            "name": "amp",
            "formula": "close",
            "error": "",
            "own_test": {
                "test_annual_return": 0.32,
                "test_benchmark_annual_return": 0.12,
                "test_information_ratio": 1.9,
                "test_max_drawdown": -0.15,
            },
            "strategy": {"detail_url": "/backtests/runs/old-joint", "archive_run_id": "old-joint"},
        }
    ]
    service._write_plan(plan)
    monkeypatch.setattr(service, "_score_panel", _panel)
    monkeypatch.setattr("quantlab.services.qlib_factor_loop.evaluate_formula", lambda formula, panel: panel["close"])
    calls: list[str] = []

    def fake(*args, book="test", **kwargs):
        calls.append(book)
        annual = 0.32 if book == "test" else -0.05
        ir = 1.9 if book == "test" else -1.5
        result = _book(annual, 0.12, ir)
        result["equity_curve"] = [{"date": "2025-01-02", "equity": 1.0}, {"date": "2025-01-03", "equity": 1.01}]
        result["benchmark_curve"] = result["equity_curve"]
        result["trades"] = []
        result["test_return"] = 0.4
        result["test_benchmark_return"] = 0.2
        result["topk"] = 50
        result["n_drop"] = 5
        return result

    monkeypatch.setattr("quantlab.services.qlib_factor_loop.run_factor_strategy", fake)
    published = service.publish_own_archive("qlib-archiv0001", 0)
    assert published["detail_url"].startswith("/backtests/runs/")
    assert "old-joint" not in published["detail_url"]
    assert calls == ["valid", "test"]
    stored = service._load("qlib-archiv0001")
    assert stored["rounds"][0]["own_test"]["detail_url"] == published["detail_url"]
    assert stored["rounds"][0]["own_test"]["test_information_ratio"] == 1.9
    again = service.publish_own_archive("qlib-archiv0001", 0)
    assert again["detail_url"] == published["detail_url"]
    assert calls == ["valid", "test"]
    running = _plan("qlib-archiv0002", [{"name": "x", "formula": "close", "error": ""}])
    service._write_plan(running)
    try:
        service.publish_own_archive("qlib-archiv0002", 0)
    except ValueError as error:
        assert "先停止" in str(error)
    else:
        raise AssertionError("a running plan should not publish an archive")
