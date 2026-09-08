from pathlib import Path


def _page(name: str) -> str:
    return Path("quantlab/web/pages", name).read_text()


def test_shared_shell_contains_all_navigation_entries_and_states() -> None:
    html = Path("quantlab/web/pages/index.html").read_text()
    for label in ("研究总览", "数据中心", "因子研究", "模型中心", "回测中心", "结果档案", "设置"):
        assert label in html
    assert 'id="loading-state"' in html
    assert 'id="empty-state"' in html
    assert 'id="error-state"' in html


def test_model_page_uses_shared_shell_and_empty_state() -> None:
    html = _page("models.html")
    assert 'class="shell"' in html
    assert 'aria-label="主导航"' in html
    assert "模型中心" in html
    assert 'id="empty-state"' in html
    assert 'id="model-list"' in html
    assert "拖动卡片可调整顺序" in html


def test_backtest_new_uses_formal_workbench_structure() -> None:
    html = _page("backtest_workbench_formal.html")
    assert 'class="shell"' in html
    assert 'aria-current="page"' in html
    for label in (
        "回测名称", "股票范围", "中国A股（SH/SZ）", "沪市（SH）", "深市（SZ）", "因子组合", "模型选择",
        "训练样本", "回测样本", "盘前过滤", "训练区间", "回测区间", "Top N", "等权", "调仓间隔",
        "未复权开盘价", "未复权收盘价", "买入费率", "卖出费率", "印花税", "最低费用", "滑点",
        "基准", "保存配置", "运行状态", "开始回测", "回测强行停止", "结果档案",
    ):
        assert label in html
    assert "运行前检查" not in html
    assert "等待校验" not in html
    assert 'id="dataset"' in html
    assert 'id="train-dataset"' not in html
    assert 'id="test-dataset"' not in html
    assert "样本过滤" in html
    assert 'id="pretrade-stock-list"' in html
    assert 'id="pretrade-benchmark-list"' in html
    assert 'id="open-filter-list"' in html
    assert 'id="random-seed"' in html
    assert 'id="test-usage-note"' in html
    assert 'id="acknowledge-test-reuse"' not in html
    assert "确认再次使用这段 test" not in html
    assert 'id="train-close-gt-ma200"' not in html
    assert 'id="val-close-gt-ma200"' not in html
    assert 'id="test-close-gt-ma200"' not in html
    assert "同一张标准行情宽表带上这些因子后，下面训练、回测各切一份" in html


def test_narrow_pages_constrain_wide_content_inside_the_viewport() -> None:
    css = Path("quantlab/web/assets/app.css").read_text()
    assert "main, .card { min-width: 0; }" in css
    assert ".factor-table, .factor-library-table { max-width: 100%; }" in css
    assert ".config-block { max-width: 100%; }" in css


def test_plan_grid_keeps_checkbox_column_narrow() -> None:
    css = Path("quantlab/web/assets/app.css").read_text()
    assert ".archive-grid.plan-grid th:nth-child(1)" in css
    assert "width: 40px; min-width: 40px; max-width: 40px;" in css
    html = _page("backtest_plan.html")
    assert "app.css?v=20260909plan7" in html
    assert 'class="main plan-page"' in html
    assert ".main.plan-page" in css
    assert "max-width: none" in css
    assert 'id="plan-state"' in html
    assert 'table.className = "archive-grid plan-grid"' in html
    assert 'factorList.className = "plan-factors"' in html
    assert "model.children[1].textContent" in html
    assert "lightgbm_tree ·" not in html
    assert ".plan-factors { display: flex; flex-wrap: wrap;" in css
    assert ".plan-factors span" in css


def test_factor_jobs_list_renders_basic_fields_as_a_table() -> None:
    js = Path("quantlab/web/assets/app.js").read_text()
    html = _page("factor_jobs.html")
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'id="factor-jobs-list"' in html
    assert 'class="factor-job-table"' in html
    assert 'table.className = "factor-job-grid"' in js
    assert '["任务", "状态", "市场", "区间", "字段", "变换", "窗口", "可勾选", "未达标", "已入库", "创建"]' in js
    assert ".factor-job-table { max-width: 100%; }" in css
    assert ".factor-job-grid" in css
    assert "factor-job-card" not in js


def test_research_and_settings_pages_share_the_complete_primary_navigation() -> None:
    nav = Path("quantlab/web/assets/nav.js").read_text()
    for href, label in (
        ("/", "研究总览"),
        ("/data", "数据中心"),
        ("/kline", "标准行情宽表"),
        ("/factors", "因子研究"),
        ("/factors/new/manual", "手动建立因子"),
        ("/research/factor-mining", "自动挖掘因子"),
        ("/research/factor-jobs", "因子计算任务"),
        ("/models", "模型中心"),
        ("/backtests/new", "回测中心"),
        ("/backtests/plan", "回测计划"),
        ("/backtests/rules", "规则回测"),
        ("/backtests/runs", "结果档案"),
        ("/settings", "设置"),
    ):
        assert href in nav
        assert label in nav
    assert "策略中心" not in nav
    for name in ("factor_manual.html", "factor_mining.html", "settings.html", "result_archive.html"):
        html = _page(name)
        assert 'class="shell"' in html
        assert '<script src="/assets/nav.js' in html


def test_settings_form_has_responsive_layout_hooks() -> None:
    html = _page("settings.html")
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'id="settings-form"' in html
    assert 'class="settings-actions"' in html
    assert 'id="compute-form"' in html
    assert 'id="bucket-workers"' in html
    assert "#settings-form" in css
    assert ".settings-actions" in css
    assert ".settings-help" in css
    assert "minmax(0, 1fr)" in css


def test_all_primary_pages_declare_the_grouped_sidebar_contract() -> None:
    pages = Path("quantlab/web/pages").glob("*.html")
    checked = 0
    for page in pages:
        html = page.read_text()
        if '<nav aria-label="主导航">' not in html:
            continue
        checked += 1
        assert '<script src="/assets/nav.js' in html
    assert checked >= 10


def test_grouped_sidebar_asset_declares_data_and_factor_children() -> None:
    nav = Path("quantlab/web/assets/nav.js").read_text()
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'className = item.children ? "nav-group"' in nav
    assert 'className = child ? "nav-subitem"' in nav
    assert '{ href: "/kline", label: "标准行情宽表", active: exact("/kline") }' in nav
    assert 'href: "/data/factors", label: "因子数据"' not in nav
    assert 'href: "/factors",\n          label: "因子研究"' in nav
    assert 'label: "因子数据"' not in nav
    assert '{ href: "/factors/new/manual", label: "手动建立因子", active:' in nav
    assert '{ href: "/research/factor-mining", label: "自动挖掘因子", active: under("/research/factor-mining") }' in nav
    assert '{ href: "/research/factor-jobs", label: "因子计算任务", active: under("/research/factor-jobs") }' in nav
    assert ".nav-children" in css
    assert ".nav-subitem" in css
    assert ".nav-parent-active" in css


def test_manual_factor_child_route_initializes_the_same_page_logic() -> None:
    app = Path("quantlab/web/assets/app.js").read_text()
    assert 'window.location.pathname === "/factors/new/manual"' in app


def test_backtest_workbench_matches_the_formal_design_sections() -> None:
    html = _page("backtest_workbench_formal.html")
    for marker in (
        'class="top"', 'class="root"', 'class="banner"', 'class="layout"',
        'data-panel-id="actions"', 'id="start"', 'id="stop-backtest"', 'id="save"', 'id="preview"',
        "运行身份", "因子组合", "训练样本", "回测样本", "盘前过滤", "模型与信号", "仓位", "交易规则",
        "开始回测", "保存配置", "运行 ID",
    ):
        assert marker in html
    assert 'class="panel run-panel"' not in html
    assert "运行前检查" not in html
    top = html.split('class="top"', 1)[1].split('class="root"', 1)[0]
    assert 'id="start"' not in top
    assert 'id="save-template"' not in html
    assert "保存为模板" not in html
    assert 'id="save-template"' not in top
    assert 'href="/backtests/runs">结果档案' not in top
    model_panel = html.split("模型与信号", 1)[1].split("仓位", 1)[0]
    assert 'class="form"' in model_panel
    assert 'class="field wide"' in model_panel
    assert 'id="dataset"' in html
    assert 'id="train-dataset"' not in html and 'id="test-dataset"' not in html
    assert 'id="train-scope"' in html and 'id="test-scope"' in html
    assert 'id="val-scope"' not in html and "验证样本" not in html
    assert 'id="scope"' not in html
    assert "数据、因子和区间" not in html
    for tag in ("模块 1", "模块 2", "模块 3", "模块 4", "模块 5", "模块 6", "模块 7", "模块 8", "模块 9"):
        assert tag in html
    assert html.index("模块 1") < html.index("模块 2") < html.index("模块 3") < html.index("模块 4") < html.index("模块 5") < html.index("模块 6") < html.index("模块 7") < html.index("模块 8") < html.index("模块 9")
    assert html.index(">盘前过滤<") < html.index(">交易规则<")
    assert html.index(">仓位<") < html.index(">盘前过滤<")
    assert html.index(">交易规则<") < html.index(">运行<")
    assert html.index(">运行<") < html.index(">运行状态<")
    css = Path("quantlab/web/assets/app.css").read_text()
    assert "display: contents" in css
    assert "#bt-roll-fields:not(.hidden)" in css


def test_backtest_workbench_save_keeps_draft_id_in_url() -> None:
    html = _page("backtest_workbench_formal.html")
    assert "function rememberDraft" in html
    assert 'searchParams.set("draft_id"' in html
    assert "history.replaceState" in html
    assert "quantlab-backtest-last-draft-id" in html
    assert "showOutput(JSON.stringify(payload.config" not in html
    assert 'showStatus("配置已保存为草稿，尚未生成运行 ID")' in html


def test_backtest_workbench_panels_can_be_dragged_to_reorder() -> None:
    html = _page("backtest_workbench_formal.html")
    css = Path("quantlab/web/assets/app.css").read_text()
    for panel_id in ("identity", "factors", "train", "test", "model", "position", "pretrade", "execution", "actions", "run-status"):
        assert f'data-panel-id="{panel_id}"' in html
    assert "columns[0]?.append(panel)" not in html
    assert "panel.parentElement?.append(panel)" in html
    assert 'class="layout-col"' in html
    assert 'class="panel-drag-handle"' in html
    assert 'PANEL_ORDER_KEY = "quantlab-backtest-panel-order"' in html
    assert "function bindWorkbenchPanelDrag" in html
    assert "function applyPanelOrder" in html
    assert ".panel-drag-handle { cursor: grab;" in css
    assert ".layout .panel.dragging" in css


def test_backtest_output_can_collapse_then_hide() -> None:
    html = _page("backtest_workbench_formal.html")
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'id="output-wrap"' in html
    assert 'id="output-collapse"' in html
    assert 'id="output-hide"' in html
    assert ">缩小<" in html
    assert ">隐藏<" in html
    assert "function showOutput" in html
    assert "showOutput(JSON.stringify" in html
    assert "#output.collapsed { max-height:" in css


def test_backtest_run_status_module_tracks_steps_and_log() -> None:
    html = _page("backtest_workbench_formal.html")
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'data-panel-id="run-status"' in html
    assert 'id="run-steps"' in html
    assert 'id="run-log"' in html
    assert 'id="run-record-link"' in html
    for label in ("快照校验", "模型训练", "预测打分", "生成仓位", "撮合成交", "指标汇总"):
        assert label in html
    assert "function pollRunStatus" in html
    assert "function applyRunStatus" in html
    assert "/status" in html
    assert "训练完成，进入回测" in html
    assert "window.location.assign(`/backtests/runs/" not in html
    assert ".run-steps li[data-status=\"running\"]" in css
    assert ".run-log" in css


def test_data_center_page_shows_only_raw_table_without_quality_panels() -> None:
    html = _page("data.html")
    assert 'id="quality-overview"' not in html
    assert 'id="dataset-detail"' not in html
    assert 'class="data-quality-area"' not in html
    assert 'id="factor-table"' not in html


def test_factor_workflow_pages_share_back_path_and_step_state() -> None:
    app = Path("quantlab/web/assets/app.js").read_text()
    manual = _page("factor_manual.html")
    mining = _page("factor_mining.html")
    for html in (manual, mining):
        assert 'class="page-back"' in html
        assert 'href="/factors"' in html
        assert 'class="workflow-steps"' in html
    assert 'id="manual-factor-steps"' in manual
    assert 'id="canonical-factor-pack"' in manual
    assert "计算验证并入库" in manual
    assert 'id="canonical-factor-pack-list"' in manual
    assert "function loadCanonicalFactorPack" in app
    assert 'id="factor-mining-steps"' in mining
    assert "function setWorkflowStep" in app
    for heading in ("基本信息", "公式与数据", "计算口径", "点时约束"):
        assert heading in manual
    for heading in ("数据范围", "时间切分", "公式怎么拼", "筛选与规模"):
        assert heading in mining
    assert 'id="mining-field-groups" class="mining-field-groups"' in mining
    assert "因子库里的因子按交易日 + 股票代码对齐到宽表" in mining
    assert "宽表暂无此列，不可用" not in app
    assert 'appendMiningPicker(box, "因子库"' in app
    css = Path("quantlab/web/assets/app.css").read_text()
    assert '.factor-form-fields input:not([type="checkbox"]):not([type="radio"])' in css
    assert "html.dark .multi-select-toggle" in css
    assert "html.dark .field-picker" in css


def test_grouped_sidebar_uses_explicit_routes_and_weak_parent_state() -> None:
    nav = Path("quantlab/web/assets/nav.js").read_text()
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'pathname.startsWith("/factors/") && !pathname.startsWith("/factors/new")' in nav
    assert 'pathname === "/factors/new" || pathname === "/factors/new/manual"' in nav
    assert "child.active?.(pathname)" in nav
    assert "parent.classList.add(\"nav-parent-active\")" in nav
    assert ".nav-group > a.nav-parent-active" in css
    assert ".nav-group > a[aria-current=\"page\"]" in css


def test_sidebar_declares_autohide_toggle() -> None:
    nav = Path("quantlab/web/assets/nav.js").read_text()
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'NAV_HIDE_KEY = "nav-autohide"' in nav
    assert "自动隐藏导航" in nav
    assert "nav-autohide-toggle" in nav
    assert "holdCollapse" in nav
    assert "nav-expanded" in nav
    assert "--nav-width: 184px" in css
    assert "grid-template-columns: var(--nav-width) 1fr" in css
    assert "html.nav-autohide .shell" in css
    assert "html.nav-autohide .shell > aside.nav-expanded" in css
    assert "html.nav-autohide aside {" not in css
    assert ".nav-autohide-toggle" in css


def test_hidden_utility_overrides_flex_toolbars() -> None:
    css = Path("quantlab/web/assets/app.css").read_text()
    assert ".hidden { display: none !important; }" in css
    assert "button.info-dot" in css and "min-height: 0" in css
    assert "main button:not([class])" in css
    assert "html.dark main button:not([class])" in css
    assert "html.dark main button {" not in css


def test_model_list_places_three_cards_per_row() -> None:
    css = Path("quantlab/web/assets/app.css").read_text()
    assert "#model-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; align-items: start; }" in css


def test_model_list_cards_can_be_dragged_to_reorder() -> None:
    html = _page("models.html")
    js = Path("quantlab/web/assets/app.js").read_text()
    css = Path("quantlab/web/assets/app.css").read_text()
    assert "拖动卡片可调整顺序" in html
    assert 'MODEL_CARD_ORDER_KEY = "model-card-order"' in js
    assert "function applyModelCardOrder" in js
    assert "function bindModelCardDrag" in js
    assert "card.draggable = true" in js
    assert ".model-card { cursor: grab;" in css or "cursor: grab" in css



def test_run_record_page_includes_traceability_and_directory_sections() -> None:
    html = _page("backtest_run_record.html")
    for marker in (
        'id="config-snapshot"',
        'id="traceability"',
        'id="run-directory"',
        "可追溯性检查",
        'class="record-meta"',
        "复制时只带入参数、因子组合和数据引用",
    ):
        assert marker in html


def test_overview_page_declares_performance_and_health_regions() -> None:
    html = _page("index.html")
    app = Path("quantlab/web/assets/app.js").read_text()
    assert 'id="recent-performance"' in html
    assert 'id="data-health"' in html
    assert 'getElementById("recent-performance")' in app
    assert 'getElementById("data-health")' in app


def test_kline_page_bundles_tradingview_lightweight_charts() -> None:
    html = _page("kline.html")
    app = Path("quantlab/web/assets/app.js").read_text()
    vendor = Path("quantlab/web/assets/vendor/lightweight-charts.standalone.production.js")
    assert vendor.is_file()
    assert "lightweight-charts.standalone.production.js" in html
    assert "window.LightweightCharts.createChart" in app
    assert "addCandlestickSeries" in app


def test_factor_data_page_declares_ic_annotations_and_calculation_history() -> None:
    html = _page("factors.html")
    app = Path("quantlab/web/assets/app.js").read_text()
    css = Path("quantlab/web/assets/app.css").read_text()
    assert 'id="factor-calculation-history"' in html
    assert 'id="factor-page-size"' in html
    assert "每页最大显示数量" in html
    assert 'function factorCatalogPageSize' in app
    assert "FACTOR_PAGE_SIZES = [50, 100, 200, 500]" in app
    assert ".factor-page-size" in css
    assert "factor-column-header" in app
    assert "function openFactorInfoPopover" in app
    assert "factor-formula-expression" in app
    assert "function renderFactorCalculationHistory" in app
    assert "/api/factor-calculations?factor_id=" in app
    assert "factor-calculation-row" in css
    assert "分层 IC · 流通市值" in app
    assert "小盘 IC" in app
    assert "重新运行分析" in app
    assert "原始因子值分布" in app
    assert "factor-ic-bucket-bars" in css
    assert "factor-normal-overlay" in css
    assert "bindMultiSelect" in app
    assert "loadFactorJobsPage" in app


def test_model_version_page_lists_human_fields_not_raw_json() -> None:
    html = _page("model_version_detail.html")
    js = Path("quantlab/web/assets/app.js").read_text()
    assert 'class="shell"' in html
    assert "<script src=\"/assets/app.js" in html
    assert "<script src=\"/assets/nav.js" in html
    assert "JSON.stringify" not in html
    assert "function loadModelVersionPage" in js
    assert "/api/models/" in js
    for label in ("种类", "训练方式", "回看月数", "版本状态"):
        assert label in js
    assert 'window.location.pathname.startsWith("/models/")' in js
    assert "查看版本" in js


def test_model_run_page_lists_fold_and_train_window() -> None:
    html = _page("model_run.html")
    js = Path("quantlab/web/assets/app.js").read_text()
    assert 'class="shell"' in html
    assert "<script src=\"/assets/app.js" in html
    assert "JSON.stringify" not in html
    assert "function loadModelRunPage" in js
    assert "/api/models/runs/" in js
    for label in ("哪一折", "训练区间", "训练行数", "训练方式"):
        assert label in js
    assert 'window.location.pathname.startsWith("/models/runs/")' in js


def test_backtest_run_record_omits_training_folds_table() -> None:
    html = _page("backtest_run_record.html")
    assert 'id="train-folds"' not in html
    assert "训练折" not in html
    assert "哪一折" not in html
    assert 'id="monthly-ranking"' in html
    assert "每月 Rank IC" in html
    assert "训练方式" in html
    assert "按流通市值分层" in html
    assert "按换手分层" in html
    assert 'id="cap-equity-chart"' in html
    assert 'id="turn-equity-chart"' in html
    assert "function renderSegmentCurves" in html
    assert "function bindChartLegend" in html
    assert "equity-legend-toggle" in html
    assert 'aria-pressed' in html


def test_backtest_run_record_lists_module_durations() -> None:
    html = _page("backtest_run_record.html")
    assert "模块耗时" in html
    assert 'id="step-timing"' in html
    assert 'id="timing-total"' in html
    assert "function renderStepTiming" in html
    assert "duration_display" in html
    assert "<thead><tr><th>模块</th><th>耗时</th></tr></thead>" in html
    assert "<th>开始</th>" not in html
    assert "<th>结束</th>" not in html
    assert "formatClock" not in html


def test_run_record_keeps_walk_forward_in_config_snapshot() -> None:
    html = _page("backtest_run_record.html")
    app = Path("quantlab/web/assets/app.js").read_text()
    assert "train_period_months" in html
    assert "回测周期" in html
    assert "function walkForwardLabel" in html
    assert "model.walk_forward && model.walk_forward.folds" not in html
    assert "model.walk_forward && model.walk_forward.folds" in app
    assert "train_period_months" in app


def test_backtest_workbench_clamps_test_period_to_lookback() -> None:
    html = Path("quantlab/web/pages/backtest_workbench_formal.html").read_text()
    assert "function clampTestPeriodToLookback" in html
    assert 'q("bt-test-period")' in html
    assert "clampTestPeriodToLookback()" in html


def test_backtest_workbench_restores_walk_forward_from_copied_config() -> None:
    html = Path("quantlab/web/pages/backtest_workbench_formal.html").read_text()
    assert "configPayload.walk_forward" in html
    assert "configPayload.train_period_months" in html
    assert "configPayload.test_period_months" in html
    assert "params.walk_forward != null" in html
    assert "max_bins（分箱数）" in html
    assert "NDCG discount_base（名次折扣）" in html
    assert "NDCG eval_at（评估只看前 N）" in html


def test_backtest_run_record_copy_opens_draft() -> None:
    html = _page("backtest_run_record.html")
    assert "window.location.assign(y.redirect_url)" in html
    assert 'walkSnap === "lookback"' in html


def test_result_archive_filters_use_model_not_strategy_labels() -> None:
    html = _page("result_archive.html")
    assert "搜索名称/ID/模型/因子" in html
    assert "<label>模型" in html
    assert "策略 / 因子" not in html
    assert "模型 / 因子" in html
    assert "未登记策略" not in html
    assert "未登记模型" in html
    assert "策略中心" not in html


def test_nav_exposes_rule_backtest_page() -> None:
    nav = Path("quantlab/web/assets/nav.js").read_text()
    assert '{ href: "/backtests/plan", label: "回测计划", active:' in nav
    assert '{ href: "/backtests/rules", label: "规则回测", active: exact("/backtests/rules") }' in nav
    assert "规则回测" in nav


def test_backtest_new_does_not_embed_rule_strategy() -> None:
    html = _page("backtest_workbench_formal.html")
    assert 'id="signal-source"' not in html
    assert 'value="rule_signal"' not in html
    assert "wiki_trend_follow" not in html
    assert "up_pct_60" not in html
    assert "规则策略" not in html


def test_backtest_rules_page_emits_required_fields() -> None:
    html = _page("backtest_rules.html")
    assert 'class="shell"' in html
    assert "<script src=\"/assets/nav.js" in html
    assert 'id="signal-source"' not in html
    assert "训练区间" not in html
    assert "因子组合" not in html
    assert "wiki_trend_follow" in html
    assert "Wiki 多指标趋势跟踪" in html
    assert 'kind: "rule_signal"' in html
    assert 'account_mode: "target_weight_exits"' in html
    assert "strategy_entity_id: null" in html
    assert "strategy_version_id: null" in html
    assert "open_when_benchmark_gt_ma200: false" in html
    assert "rule_strategy_id" in html
    assert "strategy_params" in html
    assert 'id="stop-loss"' in html
    assert 'id="take-profit"' in html
    assert 'id="max-hold-days"' in html
    assert 'id="up-pct-20"' not in html
    assert 'id="up-pct-60"' not in html
    assert "up_pct_20" not in html
    assert "up_pct_60" not in html
    assert "当时" in html
    assert "下一交易日开盘" in html
    assert "月度成分" in html or "不做市场择时" in html
    assert 'id="rsi-low"' in html
    assert 'id="rsi-high"' in html
    assert 'id="date-from"' in html
    assert 'id="stop-backtest"' in html
    assert "回测强行停止" in html
    assert 'id="date-to"' in html
    assert 'id="sellFee"' in html
    assert 'value="0.0005"' in html
    assert 'id="rebalance"' in html
    assert 'value="5"' in html
    assert 'id="stampTax"' in html
    assert 'value="0.001"' in html


def test_backtest_run_record_shows_rule_signal_metrics() -> None:
    html = _page("backtest_run_record.html")
    assert "市场多头天数" in html
    assert "信号条数" in html
    assert "完全空仓天数" in html
    assert "当时月度成分" in html
    assert "最新一期（冻结）" in html
    assert "bullish_days" in html
    assert "signal_rows" in html
    assert "flat_days" in html
    assert "membership_asof" in html
    assert "sortino" in html
    assert "calmar" in html
    assert "max_loss_streak" in html
    assert "annual-summary" in html or "年度拆解" in html


def test_backtest_run_record_rule_signal_config_snapshot() -> None:
    html = _page("backtest_run_record.html")
    assert "信号来源" in html
    assert "规则策略" in html
    assert 'c.kind === "rule_signal"' in html or "isRuleSignal" in html
    assert "规则信号（不训练）" in html
    assert "Wiki 多指标趋势跟踪" in html
