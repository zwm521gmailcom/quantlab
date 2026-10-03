const SVG_NS = "http://www.w3.org/2000/svg";
const CHART_W = 640;
const CHART_H = 280;
const PAD = { left: 48, right: 16, top: 16, bottom: 32 };

async function loadOverview() {
  const loading = document.getElementById("loading-state");
  const error = document.getElementById("error-state");
  const connectionSummary = document.getElementById("dataset-summary");
  try {
    const [picksResponse, chartsResponse] = await Promise.all([
      fetch("/api/qlib/picks"),
      fetch("/api/overview/charts"),
    ]);
    if (!picksResponse.ok) throw new Error(`HTTP ${picksResponse.status}`);
    if (!chartsResponse.ok) throw new Error(`HTTP ${chartsResponse.status}`);
    const payload = await picksResponse.json();
    const charts = await chartsResponse.json();
    const picks = Array.isArray(payload.picks) ? payload.picks : [];
    drawDashboard(picks, charts);
    connectionSummary.textContent = "quantlab 服务已连接";
    connectionSummary.classList.remove("hidden");
    loading.classList.add("hidden");
  } catch (errorValue) {
    loading.classList.add("hidden");
    error.textContent = `服务连接失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

const KIND_LABEL = {
  qlib_topk_dropout: "挖因子 Top50",
  qlib_lgb_multi: "多因子",
  qlib_lgb_regression: "单因子",
  lightgbm_tree: "LightGBM",
  factor_rank: "因子排序",
  ridge_linear: "岭回归",
  huber: "Huber",
  xgboost_tree: "XGBoost",
  random_forest: "随机森林",
  elastic_net: "弹性网络",
  lasso: "Lasso",
  rule_signal: "规则",
  unknown: "未标明",
};

const QUALITY_LABEL = {
  dataset_version: "数据集",
  factor_version: "因子",
  model_version: "模型",
  strategy_version: "策略",
  passed: "通过",
  failed: "失败",
  needs_review: "待复核",
  warning: "警告",
};

function drawDashboard(picks, charts) {
  const rows = picks.map((pick) => {
    const annual = finiteNumber(pick.test_annual_return);
    const benchmark = finiteNumber(pick.test_benchmark_annual_return);
    return {
      name: String(pick.name || ""),
      ir: finiteNumber(pick.test_information_ratio),
      excess: annual == null || benchmark == null ? null : annual - benchmark,
      drawdown: finiteNumber(pick.test_max_drawdown),
      testIc: finiteNumber(pick.test_ic),
      validIc: finiteNumber(pick.valid_ic),
      benchmark,
    };
  });
  drawHistogram(document.getElementById("chart-ir"), rows.map((row) => row.ir), {
    kind: "number",
    label: "测试信息比率分布",
  });
  drawHistogram(document.getElementById("chart-excess"), rows.map((row) => row.excess), {
    kind: "pct",
    label: "超额年化分布",
  });
  drawHistogram(document.getElementById("chart-drawdown"), rows.map((row) => row.drawdown), {
    kind: "pct",
    label: "最大回撤分布",
  });
  drawHistogram(document.getElementById("chart-test-ic"), rows.map((row) => row.testIc), {
    kind: "number",
    label: "测试 IC 分布",
  });
  drawHistogram(document.getElementById("chart-benchmark"), rows.map((row) => row.benchmark), {
    kind: "pct",
    label: "基准年化分布",
  });
  drawScatter(document.getElementById("chart-scatter"), rows, {
    x: "ir",
    y: "excess",
    xKind: "number",
    yKind: "pct",
    label: "信息比率与超额年化",
    title: (row) => `${row.name} 信息比率 ${row.ir.toFixed(2)} 超额年化 ${formatTick(row.excess, "pct")}`,
  });
  drawScatter(document.getElementById("chart-ic-pair"), rows, {
    x: "validIc",
    y: "testIc",
    xKind: "number",
    yKind: "number",
    label: "验证 IC 与测试 IC",
    title: (row) => `${row.name} 验证 ${row.validIc.toFixed(3)} 测试 ${row.testIc.toFixed(3)}`,
    zero: true,
  });
  drawSystem(charts || {});
}

function drawSystem(charts) {
  const kinds = Array.isArray(charts.kind_counts) ? charts.kind_counts : [];
  drawCategories(document.getElementById("chart-kinds"), kinds.map((item) => ({
    name: KIND_LABEL[item.kind] || item.kind || "未标明",
    count: Number(item.count) || 0,
  })), "回测类型数量");
  const status = charts.status_counts || {};
  drawCategories(document.getElementById("chart-status"), [
    { name: "已完成", count: Number(status.completed) || 0 },
    { name: "失败", count: Number(status.failed) || 0 },
    { name: "运行中", count: Number(status.running) || 0 },
    { name: "排队", count: Number(status.queued) || 0 },
  ].filter((item) => item.count > 0), "回测完成情况");
  const annual = charts.annual || {};
  const drawdown = charts.drawdown || {};
  drawPrepared(document.getElementById("chart-annual-topk"), annual.qlib_topk_dropout, { kind: "pct", label: "挖因子账本年化" });
  drawPrepared(document.getElementById("chart-annual-multi"), annual.qlib_lgb_multi, { kind: "pct", label: "多因子回测年化" });
  drawPrepared(document.getElementById("chart-annual-single"), annual.qlib_lgb_regression, { kind: "pct", label: "单因子回测年化" });
  drawPrepared(document.getElementById("chart-annual-lgb"), annual.lightgbm_tree, { kind: "pct", label: "LightGBM 年化" });
  drawPrepared(document.getElementById("chart-dd-topk"), drawdown.qlib_topk_dropout, { kind: "pct", label: "挖因子账本回撤" });
  drawPrepared(document.getElementById("chart-dd-multi"), drawdown.qlib_lgb_multi, { kind: "pct", label: "多因子回测回撤" });
  drawPrepared(document.getElementById("chart-factor-ic"), charts.factor_ic, { kind: "number", label: "因子计算 IC" });
  drawPrepared(document.getElementById("chart-coverage"), charts.factor_coverage, { kind: "pct", label: "因子覆盖率" });
  const quality = Array.isArray(charts.quality) ? charts.quality : [];
  drawCategories(document.getElementById("chart-quality"), quality.map((item) => ({
    name: `${QUALITY_LABEL[item.source] || item.source} · ${QUALITY_LABEL[item.status] || item.status}`,
    count: Number(item.count) || 0,
  })), "版本质量数量");
  const datasets = Array.isArray(charts.datasets) ? charts.datasets : [];
  drawCategories(document.getElementById("chart-datasets"), datasets.map((item) => ({
    name: item.id === "ds_canonical_market" ? "标准行情" : item.id === "dataset_source_tables" ? "原始表" : (item.id || "数据集"),
    count: Number(item.rows) || 0,
    title: `${item.date_min || ""}–${item.date_max || ""} ${item.quality_status || ""}`.trim(),
  })), "数据集行数", "compact");
}

function svgNode(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function chartFrame(host, label) {
  host.replaceChildren();
  const svg = svgNode("svg", {
    viewBox: `0 0 ${CHART_W} ${CHART_H}`,
    role: "img",
    "aria-label": label,
  });
  svg.classList.add("dash-chart-svg");
  host.appendChild(svg);
  return svg;
}

function formatTick(value, kind) {
  if (kind === "pct") return `${Math.round(value * 100)}%`;
  if (kind === "compact") {
    const abs = Math.abs(value);
    if (abs >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
    if (abs >= 1e6) return `${Math.round(value / 1e4)}万`;
    return String(Math.round(value));
  }
  const digits = Math.abs(value) >= 10 ? 0 : Math.abs(value) >= 1 ? 1 : 2;
  return value.toFixed(digits);
}

function histogram(values, bins) {
  const finite = values.filter((value) => value != null);
  if (!finite.length) return { bins: [], min: 0, max: 1 };
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (min === max) {
    min -= Math.abs(min) * 0.05 || 0.5;
    max += Math.abs(max) * 0.05 || 0.5;
  }
  const width = (max - min) / bins;
  const counts = Array.from({ length: bins }, () => 0);
  finite.forEach((value) => {
    let index = Math.floor((value - min) / width);
    if (index >= bins) index = bins - 1;
    if (index < 0) index = 0;
    counts[index] += 1;
  });
  return {
    min,
    max,
    bins: counts.map((count, index) => ({
      count,
      from: min + index * width,
      to: min + (index + 1) * width,
    })),
  };
}

function paintHistogram(host, series, options) {
  const svg = chartFrame(host, options.label);
  const plotX = PAD.left;
  const plotY = PAD.top;
  const plotW = CHART_W - PAD.left - PAD.right;
  const plotH = CHART_H - PAD.top - PAD.bottom;
  const maxCount = Math.max(1, ...series.bins.map((bin) => bin.count));
  svg.appendChild(svgNode("line", {
    x1: plotX, y1: plotY, x2: plotX, y2: plotY + plotH, class: "dash-chart-axis",
  }));
  svg.appendChild(svgNode("line", {
    x1: plotX, y1: plotY + plotH, x2: plotX + plotW, y2: plotY + plotH, class: "dash-chart-axis",
  }));
  [0, maxCount].forEach((tick) => {
    const y = plotY + plotH - (tick / maxCount) * plotH;
    const label = svgNode("text", { x: plotX - 8, y: y + 4, "text-anchor": "end", class: "dash-chart-label" });
    label.textContent = String(tick);
    svg.appendChild(label);
  });
  const gap = series.bins.length > 1 ? 3 : 0;
  const barW = Math.max(1, (plotW - gap * (series.bins.length - 1)) / series.bins.length);
  series.bins.forEach((bin, index) => {
    const height = (bin.count / maxCount) * plotH;
    const rect = svgNode("rect", {
      x: plotX + index * (barW + gap),
      y: plotY + plotH - height,
      width: barW,
      height: Math.max(height, bin.count ? 1 : 0),
      class: "dash-chart-bar",
    });
    const title = svgNode("title", {});
    title.textContent = `${formatTick(bin.from, options.kind)} 到 ${formatTick(bin.to, options.kind)}：${bin.count}`;
    rect.appendChild(title);
    svg.appendChild(rect);
  });
  [series.min, (series.min + series.max) / 2, series.max].forEach((tick, index) => {
    const x = plotX + (index / 2) * plotW;
    const label = svgNode("text", {
      x,
      y: CHART_H - 8,
      "text-anchor": index === 0 ? "start" : index === 2 ? "end" : "middle",
      class: "dash-chart-label",
    });
    label.textContent = formatTick(tick, options.kind);
    svg.appendChild(label);
  });
}

function drawPrepared(host, spec, options) {
  if (!host) return;
  if (!spec || !Array.isArray(spec.counts) || !spec.counts.length) {
    chartFrame(host, options.label);
    return;
  }
  const width = (Number(spec.end) - Number(spec.start)) / spec.counts.length;
  paintHistogram(host, {
    min: Number(spec.start),
    max: Number(spec.end),
    bins: spec.counts.map((count, index) => ({
      count: Number(count) || 0,
      from: Number(spec.start) + index * width,
      to: Number(spec.start) + (index + 1) * width,
    })),
  }, options);
}

function drawHistogram(host, values, options) {
  if (!host) return;
  paintHistogram(host, histogram(values, 16), options);
}

function drawScatter(host, rows, options) {
  if (!host) return;
  const svg = chartFrame(host, options.label);
  const points = rows.filter((row) => row[options.x] != null && row[options.y] != null);
  const plotX = PAD.left;
  const plotY = PAD.top;
  const plotW = CHART_W - PAD.left - PAD.right;
  const plotH = CHART_H - PAD.top - PAD.bottom;
  svg.appendChild(svgNode("line", {
    x1: plotX, y1: plotY, x2: plotX, y2: plotY + plotH, class: "dash-chart-axis",
  }));
  svg.appendChild(svgNode("line", {
    x1: plotX, y1: plotY + plotH, x2: plotX + plotW, y2: plotY + plotH, class: "dash-chart-axis",
  }));
  if (!points.length) return;
  const xValues = points.map((row) => row[options.x]);
  const yValues = points.map((row) => row[options.y]);
  let xMin = Math.min(...xValues);
  let xMax = Math.max(...xValues);
  let yMin = options.zero ? Math.min(0, ...yValues) : Math.min(...yValues);
  let yMax = Math.max(...yValues);
  if (xMin === xMax) xMax = xMin + 1;
  if (yMin === yMax) yMax = yMin + 0.01;
  const xPad = (xMax - xMin) * 0.06;
  const yPad = (yMax - yMin) * 0.08;
  xMin -= xPad;
  xMax += xPad;
  yMin -= yPad;
  yMax += yPad;
  const xOf = (value) => plotX + ((value - xMin) / (xMax - xMin)) * plotW;
  const yOf = (value) => plotY + plotH - ((value - yMin) / (yMax - yMin)) * plotH;
  if (yMin < 0 && yMax > 0) {
    svg.appendChild(svgNode("line", {
      x1: plotX, y1: yOf(0), x2: plotX + plotW, y2: yOf(0), class: "dash-chart-zero",
    }));
  }
  points.forEach((row) => {
    const dot = svgNode("circle", {
      cx: xOf(row[options.x]),
      cy: yOf(row[options.y]),
      r: 3.5,
      class: "dash-chart-dot",
    });
    const title = svgNode("title", {});
    title.textContent = options.title(row);
    dot.appendChild(title);
    svg.appendChild(dot);
  });
  [xMin, (xMin + xMax) / 2, xMax].forEach((tick, index) => {
    const label = svgNode("text", {
      x: xOf(tick),
      y: CHART_H - 8,
      "text-anchor": index === 0 ? "start" : index === 2 ? "end" : "middle",
      class: "dash-chart-label",
    });
    label.textContent = formatTick(tick, options.xKind);
    svg.appendChild(label);
  });
  [yMin, yMax].forEach((tick) => {
    const label = svgNode("text", { x: plotX - 8, y: yOf(tick) + 4, "text-anchor": "end", class: "dash-chart-label" });
    label.textContent = formatTick(tick, options.yKind);
    svg.appendChild(label);
  });
}

function drawCategories(host, items, label, valueKind) {
  if (!host) return;
  const rows = items.filter((item) => item && item.name);
  const height = Math.max(CHART_H, 36 + rows.length * 28);
  host.replaceChildren();
  const svg = svgNode("svg", { viewBox: `0 0 ${CHART_W} ${height}`, role: "img", "aria-label": label });
  svg.classList.add("dash-chart-svg");
  host.appendChild(svg);
  if (!rows.length) return;
  const plotX = 148;
  const plotW = CHART_W - plotX - 64;
  const maxCount = Math.max(1, ...rows.map((item) => item.count));
  rows.forEach((item, index) => {
    const y = 16 + index * 28;
    const name = svgNode("text", { x: plotX - 8, y: y + 14, "text-anchor": "end", class: "dash-chart-label" });
    name.textContent = item.name;
    svg.appendChild(name);
    const width = (item.count / maxCount) * plotW;
    const rect = svgNode("rect", {
      x: plotX, y, width: Math.max(width, item.count ? 1 : 0), height: 18, class: "dash-chart-bar",
    });
    const title = svgNode("title", {});
    title.textContent = item.title || `${item.name} ${formatTick(item.count, valueKind || "compact")}`;
    rect.appendChild(title);
    svg.appendChild(rect);
    const count = svgNode("text", {
      x: plotX + Math.max(width, item.count ? 1 : 0) + 6,
      y: y + 14,
      class: "dash-chart-label",
    });
    count.textContent = formatTick(item.count, valueKind || "compact");
    svg.appendChild(count);
  });
}
