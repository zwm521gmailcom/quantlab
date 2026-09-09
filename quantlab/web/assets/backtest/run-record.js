const id = decodeURIComponent(location.pathname.split("/").pop());
const q = (x) => document.getElementById(x);
const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[c]));
const metricLabels = {
  return: "累计收益",
  annual_return: "年化收益",
  sharpe: "夏普",
  sortino: "索提诺",
  calmar: "卡玛",
  max_drawdown: "最大回撤",
  max_loss_streak: "最长连亏",
  win_rate: "胜率",
  benchmark_return: "基准收益",
  excess_return: "超额收益",
  turnover: "日均换手",
  capital_usage: "日均资金占用",
  rank_ic: "Rank IC",
  ndcg_at_10: "NDCG@10",
  bullish_days: "市场多头天数",
  signal_rows: "信号条数",
  flat_days: "完全空仓天数",
  membership_asof: "成分 as-of",
};

function formatRuleRawMetric(key, value) {
  if (key === "membership_asof") {
    if (value === "monthly") return "当时月度成分";
    if (value === "latest") return "最新一期（冻结）";
    return String(value);
  }
  return String(value);
}

function ruleRawMetricHtml(raw) {
  if (!raw || typeof raw !== "object") return "";
  return [
    ["bullish_days", "市场多头天数"],
    ["signal_rows", "信号条数"],
    ["flat_days", "完全空仓天数"],
    ["membership_asof", "成分 as-of"],
  ].filter(([key]) => raw[key] != null && raw[key] !== "").map(([key, label]) => {
    return `<span class="metric"><b>${esc(label)}</b> ${esc(formatRuleRawMetric(key, raw[key]))}</span>`;
  }).join("");
}

function chartTime(date) {
  const text = String(date || "");
  return text.length === 8 ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : text;
}

function addLine(chart, color) {
  if (typeof chart.addLineSeries === "function") return chart.addLineSeries({color, lineWidth: 2});
  return chart.addSeries(window.LightweightCharts.LineSeries, {color, lineWidth: 2});
}

function bindChartLegend(legend, entries) {
  if (!legend) return;
  legend.replaceChildren();
  if (!entries.length) {
    legend.hidden = true;
    return;
  }
  legend.hidden = false;
  entries.forEach((entry) => {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "equity-legend-toggle";
    toggle.setAttribute("aria-pressed", "true");
    toggle.setAttribute("aria-label", "隐藏 " + entry.label);
    toggle.title = "点击隐藏";
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = entry.color;
    toggle.append(swatch, document.createTextNode(entry.label));
    toggle.addEventListener("click", () => {
      const visible = toggle.getAttribute("aria-pressed") !== "false";
      if (entry.series && typeof entry.series.setData === "function") {
        entry.series.setData(visible ? [] : entry.points);
      }
      toggle.setAttribute("aria-pressed", String(!visible));
      toggle.setAttribute("aria-label", (visible ? "显示 " : "隐藏 ") + entry.label);
      toggle.title = visible ? "点击显示" : "点击隐藏";
    });
    legend.append(toggle);
  });
}

function chartTheme() {
  return (window.qlTheme || {chartColors: () => ({bg: "#f3f4f0", text: "#1e2a24", grid: "#d2d7cf", border: "#c3c9c0"})}).chartColors();
}

function renderEquity(raw, benchmarkLabel) {
  const el = q("equity-chart");
  if (!el) return;
  const curve = (raw && raw.equity_curve) || [];
  const legend = q("equity-legend");
  if (!curve.length || typeof window.LightweightCharts === "undefined") {
    el.textContent = "未生成曲线";
    if (legend) legend.hidden = true;
    return;
  }
  el.textContent = "";
  const colors = chartTheme();
  const chart = window.LightweightCharts.createChart(el, {
    autoSize: true,
    height: 280,
    layout: {
      background: {type: window.LightweightCharts.ColorType.Solid, color: colors.bg},
      textColor: colors.text,
      fontSize: 12,
    },
    grid: {vertLines: {color: colors.grid}, horzLines: {color: colors.grid}},
    rightPriceScale: {borderColor: colors.border, scaleMargins: {top: 0.08, bottom: 0.08}},
    timeScale: {borderColor: colors.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN", dateFormat: "yyyy-MM-dd"},
    handleScroll: false,
    handleScale: false,
  });
  window.addEventListener("ql-theme-change", () => {
    const next = chartTheme();
    chart.applyOptions({
      layout: {background: {type: window.LightweightCharts.ColorType.Solid, color: next.bg}, textColor: next.text},
      grid: {vertLines: {color: next.grid}, horzLines: {color: next.grid}},
      rightPriceScale: {borderColor: next.border},
      timeScale: {borderColor: next.border},
    });
  });
  const strategy = addLine(chart, "#c4622d");
  const strategyPoints = curve.map((row) => ({time: chartTime(row.date), value: Number(row.equity)}));
  strategy.setData(strategyPoints);
  const legendEntries = [{series: strategy, points: strategyPoints, label: "组合", color: "#c4622d"}];
  const bench = (raw && raw.benchmark_curve) || [];
  if (bench.length) {
    const benchSeries = addLine(chart, "#2f6a4a");
    const benchPoints = bench.map((row) => ({time: chartTime(row.date), value: Number(row.equity)}));
    benchSeries.setData(benchPoints);
    legendEntries.push({series: benchSeries, points: benchPoints, label: benchmarkLabel || "基准", color: "#2f6a4a"});
  }
  bindChartLegend(legend, legendEntries);
  chart.timeScale().fitContent();
}

const SEGMENT_COLORS = ["#6b4c9a", "#3d7ea6", "#4d8f6a", "#c4a035", "#c4622d"];

function formatSegmentReturn(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return (number * 100).toFixed(2) + "%";
}

function renderSegmentDimension(raw, key, chartId, legendId, tableId, emptyText) {
  const el = q(chartId);
  const legend = q(legendId);
  const tableHost = q(tableId);
  if (!el) return;
  const dimension = raw && raw.segment_curves && raw.segment_curves[key];
  const buckets = (dimension && dimension.buckets) || [];
  const ready = dimension && dimension.status === "available" && buckets.some((item) => (item.equity_curve || []).length);
  if (!ready || typeof window.LightweightCharts === "undefined") {
    el.textContent = emptyText;
    if (legend) legend.hidden = true;
    if (tableHost) tableHost.replaceChildren();
    return;
  }
  el.textContent = "";
  const colors = chartTheme();
  const chart = window.LightweightCharts.createChart(el, {
    autoSize: true,
    height: 260,
    layout: {
      background: {type: window.LightweightCharts.ColorType.Solid, color: colors.bg},
      textColor: colors.text,
      fontSize: 12,
    },
    grid: {vertLines: {color: colors.grid}, horzLines: {color: colors.grid}},
    rightPriceScale: {borderColor: colors.border, scaleMargins: {top: 0.08, bottom: 0.08}},
    timeScale: {borderColor: colors.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN", dateFormat: "yyyy-MM-dd"},
    handleScroll: false,
    handleScale: false,
  });
  const legendEntries = [];
  buckets.forEach((item, index) => {
    const curve = item.equity_curve || [];
    if (!curve.length) return;
    const color = SEGMENT_COLORS[index % SEGMENT_COLORS.length];
    const series = addLine(chart, color);
    const points = curve.map((row) => ({time: chartTime(row.date), value: Number(row.equity)}));
    series.setData(points);
    legendEntries.push({series, points, label: item.label || ("Q" + (index + 1)), color});
  });
  bindChartLegend(legend, legendEntries);
  chart.timeScale().fitContent();
  if (tableHost) {
    const table = document.createElement("table");
    table.className = "fold-table";
    table.innerHTML = "<thead><tr><th>分档</th><th>累计收益</th><th>成交笔数</th></tr></thead>";
    const body = document.createElement("tbody");
    buckets.forEach((item) => {
      const tr = document.createElement("tr");
      [item.label || "—", formatSegmentReturn(item.total_return), item.trade_count == null ? "—" : String(item.trade_count)].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      });
      body.append(tr);
    });
    table.append(body);
    tableHost.replaceChildren(table);
  }
}

function renderSegmentCurves(raw) {
  renderSegmentDimension(
    raw,
    "by_float_market_cap",
    "cap-equity-chart",
    "cap-equity-legend",
    "cap-equity-table",
    "这次没有市值分层净值。新回测会按当天流通市值分成五档，档内用同样的 Top N。",
  );
  renderSegmentDimension(
    raw,
    "by_turn",
    "turn-equity-chart",
    "turn-equity-legend",
    "turn-equity-table",
    "这次没有换手分层净值。新回测会按当天换手分成五档，档内用同样的 Top N。",
  );
}

function traceHtml(list) {
  return list.map(([ok, label, detail]) => (
    `<div class="trace-item ${ok ? "ok" : "warn"}"><b>${ok ? "✓" : "○"} ${esc(label)}</b><small>${esc(detail)}</small></div>`
  )).join("");
}

function isJsonBlock(value) {
  return value !== null && typeof value === "object";
}

function appendJsonFold(parent, title, value, preId) {
  const details = document.createElement("details");
  details.className = "fold";
  const summary = document.createElement("summary");
  summary.textContent = title;
  const pre = document.createElement("pre");
  pre.className = "config-block";
  if (preId) pre.id = preId;
  pre.textContent = JSON.stringify(value ?? null, null, 2);
  details.append(summary, pre);
  parent.append(details);
}

const JSON_FOLD_LABELS = {
  hyperparameters: "超参",
  train: "训练",
  test: "测试",
  factor_versions: "因子",
  model: "模型",
  buy_fee: "买入费率",
  sell_fee: "卖出费率",
  label: "标签",
  missing_policy: "缺失规则",
  filter: "过滤",
};
const JSON_FOLD_ORDER = ["hyperparameters", "train", "test", "factor_versions", "model", "buy_fee", "sell_fee", "missing_policy", "label", "filter"];

function walkForwardLabel(value) {
  if (value === "lookback" || value === "rolling" || value === "monthly") return "定长回看";
  return "一次训练";
}

function isRuleSignal(c) {
  return c.kind === "rule_signal" || Boolean(c.rule_strategy_id);
}

function ruleStrategyLabel(id) {
  if (id === "wiki_trend_follow") return "Wiki 多指标趋势跟踪";
  return id || "—";
}

function formatRankMetric(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(4) : String(value);
}

function renderMonthlyRanking(d) {
  const target = q("monthly-ranking");
  if (!target) return;
  const raw = d.metrics_raw || {};
  const rows = raw.monthly_ranking || (raw.model && raw.model.monthly_ranking) || [];
  target.replaceChildren();
  if (!rows.length) {
    target.textContent = "这次没有每月 Rank IC / NDCG@10。新回测会写进结果汇总。";
    return;
  }
  const table = document.createElement("table");
  table.className = "fold-table";
  table.innerHTML = "<thead><tr><th>月份</th><th>交易日</th><th>Rank IC</th><th>NDCG@10</th></tr></thead>";
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    [row.month || "—", row.days == null ? "—" : String(row.days), formatRankMetric(row.rank_ic), formatRankMetric(row.ndcg_at_10)].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
  target.append(table);
}

function formatYearMetric(value, percent) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return percent ? (number * 100).toFixed(2) + "%" : number.toFixed(4);
}

const STEP_LABELS = {
  snapshot_validation: "快照校验",
  model_training: "模型训练",
  prediction: "预测打分",
  positions: "生成仓位",
  execution: "撮合成交",
  metrics: "指标汇总",
};
function renderStepTiming(d) {
  const total = q("timing-total");
  const target = q("step-timing");
  if (!target) return;
  const timing = d.timing || {};
  if (total) total.textContent = "总耗时：" + (timing.duration_display || "—");
  const rows = d.dag || [];
  target.replaceChildren();
  if (!rows.length) {
    target.textContent = "这次没有记下各模块时间。";
    return;
  }
  const table = document.createElement("table");
  table.className = "fold-table";
  table.innerHTML = "<thead><tr><th>模块</th><th>耗时</th></tr></thead>";
  const body = document.createElement("tbody");
  rows.forEach((step) => {
    const tr = document.createElement("tr");
    [
      step.step_label || STEP_LABELS[step.step_name] || step.step_name || "—",
      step.duration_display || "—",
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
  target.append(table);
}

function renderAnnualSummary(d) {
  const target = q("annual-summary");
  if (!target) return;
  const raw = d.metrics_raw || {};
  const rows = Array.isArray(raw.annual_summary) ? raw.annual_summary : [];
  target.replaceChildren();
  if (!rows.length) {
    target.textContent = "—";
    return;
  }
  const table = document.createElement("table");
  table.className = "fold-table";
  table.innerHTML = "<thead><tr><th>年份</th><th>收益</th><th>最大回撤</th></tr></thead>";
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    [row.year || "—", formatYearMetric(row.return, true), formatYearMetric(row.max_drawdown, true)].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
  target.append(table);
}

async function load() {
  const r = await fetch("/api/backtests/runs/" + encodeURIComponent(id));
  const d = await r.json();
  if (!r.ok) {
    q("identity").textContent = d.message || "加载失败";
    return;
  }
  q("title").textContent = d.name + " · 运行记录";
  const fail = d.status === "failed" ? ((d.failure && d.failure.reason) || d.error_message || "回测没有跑完") : "";
  const totalText = d.timing && d.timing.duration_display && d.timing.duration_display !== "—" ? " · 总耗时 " + d.timing.duration_display : "";
  q("identity").textContent = d.run_id + " · " + d.status_name + " · 创建 " + d.created_at + (d.finished_at ? " · 完成 " + d.finished_at : "") + totalText;
  const c = d.configuration || {};
  q("summary").innerHTML = (fail ? `<p class="state error">${esc(fail)}</p>` : "")
    + Object.entries(d.metrics).map(([k, v]) => `<span class="metric"><b>${esc(metricLabels[k] || k)}</b> ${esc(v.display)}</span>`).join("")
    + ruleRawMetricHtml(d.metrics_raw)
    + `<p>基准：${esc(d.benchmark)} · 成交：${esc(d.metrics_raw && d.metrics_raw.trade_count != null ? d.metrics_raw.trade_count : "—")}</p>`;
  try {
    renderEquity(d.metrics_raw, d.benchmark);
  } catch (error) {
    q("equity-chart").textContent = "未生成曲线";
  }
  try {
    renderSegmentCurves(d.metrics_raw);
  } catch (error) {
    const cap = q("cap-equity-chart");
    const turn = q("turn-equity-chart");
    if (cap) cap.textContent = "这次没有市值分层净值。";
    if (turn) turn.textContent = "这次没有换手分层净值。";
  }
  const isRule = isRuleSignal(c);
  const snapshot = [
    ["运行名称", d.name],
    ["数据快照", c.dataset_id + " · " + c.dataset_version_id],
    ["股票范围", c.stock_scope || "—"],
    ["训练区间", ((c.train && c.train.date_from) || "—") + " 至 " + ((c.train && c.train.date_to) || "—")],
    ["测试区间", ((c.test && c.test.date_from) || "—") + " 至 " + ((c.test && c.test.date_to) || "—")],
    ["因子组合", (c.factor_versions || []).map((f) => f.factor_id + ":" + f.version_id).join("，") || "—"],
  ];
  if (isRule) {
    snapshot.splice(3, 0, ["信号来源", "规则策略"], ["规则策略", ruleStrategyLabel(c.rule_strategy_id)]);
    snapshot.push(
      ["Top N / 权重", (c.top_n != null ? c.top_n : "—") + " / " + (c.weighting || "—")],
      ["调仓间隔", c.rebalance_every != null ? c.rebalance_every + " 个交易日" : "—"],
      ["买入 / 卖出", (c.buy_price || "—") + " / " + (c.sell_price || "—")],
      ["训练方式", "规则信号（不训练）"],
      ["基准", c.benchmark || "—"],
      ["配置哈希", c.content_hash || "—"],
    );
  } else {
    snapshot.push(
      ["模型", ((c.model && c.model.entity_id) || "—") + " · " + ((c.model && c.model.version_id) || "—")],
      ["Top N / 权重", (c.top_n != null ? c.top_n : "—") + " / " + (c.weighting || "—")],
      ["调仓间隔", c.rebalance_every != null ? c.rebalance_every + " 个交易日" : "—"],
      ["买入 / 卖出", (c.buy_price || "—") + " / " + (c.sell_price || "—")],
      ["训练方式", walkForwardLabel(c.walk_forward || (c.hyperparameters && c.hyperparameters.walk_forward))],
      ["基准", c.benchmark || "—"],
      ["配置哈希", c.content_hash || "—"],
    );
  }
  const walkSnap = String(c.walk_forward || (c.hyperparameters && c.hyperparameters.walk_forward) || "once").toLowerCase();
  if (!isRule && (walkSnap === "lookback" || walkSnap === "rolling" || walkSnap === "monthly")) {
    snapshot.splice(-2, 0,
      ["回看月数", c.train_lookback_months || c.train_period_months || "—"],
      ["回测周期", c.test_period_months || "—"],
    );
  }
  const raw = d.metrics_raw || {};
  if (raw.bullish_days != null) snapshot.push(["市场多头天数", String(raw.bullish_days)]);
  if (raw.signal_rows != null) snapshot.push(["信号条数", String(raw.signal_rows)]);
  if (raw.flat_days != null) snapshot.push(["完全空仓天数", String(raw.flat_days)]);
  if (raw.membership_asof != null) snapshot.push(["成分 as-of", formatRuleRawMetric("membership_asof", raw.membership_asof)]);
  q("config-snapshot").innerHTML = traceHtml(snapshot.map(([label, value]) => [true, label, value]));
  renderMonthlyRanking(d);
  renderAnnualSummary(d);
  renderStepTiming(d);
  const folds = q("config-folds");
  folds.replaceChildren();
  appendJsonFold(folds, "运行配置", d.configuration, "configuration");
  const seen = new Set();
  for (const key of JSON_FOLD_ORDER) {
    if (!isJsonBlock(c[key])) continue;
    appendJsonFold(folds, JSON_FOLD_LABELS[key] || key, c[key]);
    seen.add(key);
  }
  for (const [key, value] of Object.entries(c)) {
    if (seen.has(key) || !isJsonBlock(value)) continue;
    appendJsonFold(folds, JSON_FOLD_LABELS[key] || key, value);
  }
  appendJsonFold(folds, "运行信息", {failure: d.failure, logs: d.logs}, "logs");
  const factorCount = (c.factor_versions || []).length;
  const dagRows = d.dag || [];
  const dagRecorded = dagRows.length >= 6 && dagRows.every((s) => s.status && s.status !== "pending");
  const trace = [
    ["配置已冻结", Boolean(c.content_hash), "内容哈希 " + ((c.content_hash || "缺失").slice(0, 18) + "…")],
    ["数据与因子版本", Boolean(c.dataset_id && c.dataset_version_id && factorCount), c.dataset_id + " · " + (c.dataset_version_id || "—") + "；" + factorCount + " 个因子版本"],
    ["训练 / 测试隔离", Boolean(c.train && c.test && String(c.train.date_to) < String(c.test.date_from)), "训练至 " + ((c.train && c.train.date_to) || "—") + "，测试自 " + ((c.test && c.test.date_from) || "—")],
    ["DAG 已记录", dagRecorded, "共 " + dagRows.length + " 步"],
    ["失败保留错误", !(d.status === "failed" && !d.failure && !d.error_message), d.status === "failed" ? "失败原因已保留" : "当前状态 " + esc(d.status_name)],
    ["Artifact 哈希", Boolean(d.artifacts && d.artifacts.every((a) => a.content_hash)), (d.artifacts || []).length + " 个 Artifact"],
  ];
  q("traceability").innerHTML = traceHtml(trace);
  q("run-directory").textContent = [d.results_root || "尚未生成结果目录", (d.artifacts || []).map((a) => "└─ " + a.original_name + " · " + (a.size_bytes || 0) + " bytes")].flat().join("\n");
  q("artifacts").innerHTML = (d.artifacts || []).map((a) => `<div><a href="${esc("/api/artifacts/" + a.artifact_id + "/download")}">${esc(a.display_name)}</a> · ${esc(a.original_name)} · ${esc(a.size_bytes)} bytes</div>`).join("") || "尚未生成";
  q("copy").onclick = async () => {
    const x = await fetch(d.actions.copy_config_url, {method: "POST"});
    const y = await x.json();
    if (x.ok && y.redirect_url) {
      window.location.assign(y.redirect_url);
      return;
    }
    q("message").textContent = x.ok ? "已复制为回测草稿：" + y.draft_id : (y.message || "复制失败");
  };
  if (d.status === "failed") {
    q("retry").hidden = false;
    q("retry").onclick = async () => {
      q("retry").disabled = true;
      q("message").textContent = "正在重新回测…";
      const x = await fetch((d.actions && d.actions.execute_url) || ("/api/backtests/" + encodeURIComponent(id) + "/execute"), {method: "POST"});
      const y = await x.json();
      if (x.ok) location.reload();
      else {
        q("retry").disabled = false;
        q("message").textContent = y.message || "重新回测失败";
      }
    };
  }
  let confirmingDelete = false;
  q("remove").onclick = async () => {
    if (!confirmingDelete) {
      confirmingDelete = true;
      q("remove").textContent = "确认删除";
      return;
    }
    q("remove").disabled = true;
    q("message").textContent = "正在删除…";
    const x = await fetch((d.actions && d.actions.delete_url) || ("/api/backtests/runs/" + encodeURIComponent(id)), {method: "DELETE"});
    if (x.status === 204 || x.ok) {
      location.href = "/backtests/runs";
      return;
    }
    const y = await x.json().catch(() => ({}));
    confirmingDelete = false;
    q("remove").disabled = false;
    q("remove").textContent = "删除";
    q("message").textContent = y.message || "删除失败";
  };
}
load();
