function klineBusinessDate(value) {
  const text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  return null;
}

let activeFactorRunId = null;
let currentFactorItem = null;
let currentFactorSample = {items: [], fields: []};
let currentFactorHistory = [];

function factorConditionalPayload(latest) {
  if (!latest) return null;
  const payload = latest.conditional_ic && Object.keys(latest.conditional_ic).length
    ? latest.conditional_ic
    : latest.summary && latest.summary.conditional_ic;
  if (!payload || typeof payload !== "object") return null;
  if (!payload.by_float_market_cap && !payload.by_turn) return null;
  return payload;
}

function conditionalCapIc(latest, bucket) {
  const payload = factorConditionalPayload(latest);
  const buckets = payload && payload.by_float_market_cap && payload.by_float_market_cap.buckets || [];
  const match = buckets.find((item) => Number(item.bucket) === bucket);
  return match ? match.ic_mean : null;
}


async function runFactorAnalysis(item, previous = null) {
  const error = document.getElementById("factor-error");
  const button = document.getElementById("factor-run-analysis");
  if (!item) return;
  const dateFrom = document.getElementById("factor-date-from")?.value.trim() || previous?.date_from || "";
  const dateTo = document.getElementById("factor-date-to")?.value.trim() || previous?.date_to || "";
  const body = {
    factor_id: item.factor_id,
    version_id: item.factor_version_id || item.version_id || "v1",
  };
  if (dateFrom) body.date_from = dateFrom;
  if (dateTo) body.date_to = dateTo;
  let idleLabel = "运行分析";
  if (button) {
    idleLabel = button.textContent || idleLabel;
    button.disabled = true;
    button.textContent = "正在计算分析…";
  }
  error.classList.add("hidden");
  try {
    const response = await fetch("/api/factor-calculations", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || payload.detail?.message || `HTTP ${response.status}`);
    await loadFactorDetail(item.factor_id);
  } catch (errorValue) {
    error.textContent = `分析失败：${errorValue.message}`;
    error.classList.remove("hidden");
    if (button) {
      button.disabled = false;
      button.textContent = idleLabel;
    }
  }
}

function renderFactorDetail(item, summary, sample, calculation = null) {
  currentFactorItem = item;
  currentFactorSample = sample;
  const detail = document.getElementById("factor-detail");
  detail.replaceChildren();
  const titleRow = document.createElement("div");
  titleRow.className = "factor-detail-title-row";
  const backLink = document.createElement("a");
  backLink.href = "/factors";
  backLink.className = "factor-detail-back";
  backLink.textContent = "← 因子数据";
  const heading = document.createElement("h3");
  heading.textContent = `${item.name} · ${item.factor_id}`;
  titleRow.append(backLink, heading);
  const latestForButton = calculation && calculation.status ? calculation : item.latest_calculation;
  const completedForButton = latestForButton && latestForButton.status === "completed" ? latestForButton : null;
  const runButton = document.createElement("button");
  runButton.type = "button";
  runButton.id = "factor-run-analysis";
  runButton.className = "factor-run-analysis";
  runButton.textContent = completedForButton ? "重新运行分析" : "运行分析";
  runButton.addEventListener("click", () => runFactorAnalysis(item, latestForButton));
  titleRow.append(runButton);
  detail.append(titleRow);
  const formulaBlock = appendFactorText(detail, "div", "factor-formula-block", "");
  appendFactorText(formulaBlock, "h4", null, "公式");
  appendFactorText(formulaBlock, "pre", "factor-formula-expression", item.formula);
  (item.formula_explanation || []).forEach((line) => {
    if (line === (item.formula_explanation || [])[0]) appendFactorText(formulaBlock, "h4", null, "公式中文解释");
    appendFactorText(formulaBlock, "p", null, line);
  });
  appendFactorText(detail, "p", "factor-detail-meta", `因子版本 ${item.factor_version_id} · 数据版本 canonical/${item.dataset_version_id} · 方向 ${item.direction_label} · 频率 ${item.frequency}`);
  appendFactorText(detail, "p", "factor-storage", "因子数据文件：data/canonical.parquet（标准行情只读，因子按计算任务生成，不再依赖 features.parquet）");
  appendFactorText(detail, "p", "factor-detail-meta", `缺失规则：${item.missing_policy}；点时规则：${item.pit_policy}`);
  const summaryBox = document.getElementById("factor-summary");
  summaryBox.replaceChildren();
  const latest = calculation && calculation.status ? calculation : item.latest_calculation;
  const completed = latest && latest.status === "completed" ? latest : null;
  const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  const pct2 = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
  const four = (value) => value == null ? "—" : Number(value).toFixed(4);
  const groups = [
    {
      title: "数据质量",
      metrics: [
        ["计算行数", completed && completed.row_count != null ? new Intl.NumberFormat("zh-CN").format(completed.row_count) : "未诊断"],
        ["缺失行数", completed && completed.missing_rows != null ? new Intl.NumberFormat("zh-CN").format(completed.missing_rows) : "未诊断"],
        ["计算覆盖率", completed && completed.coverage != null ? pct2(completed.coverage) : "未诊断"],
        ["有效交易日", completed && completed.effective_days != null ? completed.effective_days : "未诊断"],
        ["计算周期", completed ? `${completed.date_from} — ${completed.date_to}` : "未诊断"],
        ["计算流水号", completed ? completed.serial_no : "未诊断"],
      ],
    },
    {
      title: "有效性指标",
      metrics: [
        ["IC 均值", completed ? four(completed.ic_mean) : "未诊断"],
        ["IC 正值比例", completed ? pct(completed.ic_positive_ratio) : "未诊断"],
        ["IC 标准差", completed ? four(completed.ic_std) : "未诊断"],
        ["ICIR", completed ? four(completed.icir) : "未诊断"],
        ["小盘 IC", completed ? four(conditionalCapIc(completed, 1)) : "未诊断"],
        ["大盘 IC", completed ? four(conditionalCapIc(completed, 5)) : "未诊断"],
      ],
    },
    {
      title: "分组表现",
      metrics: [
        ["Top-Bottom 价差", completed ? four(completed.top_bottom_spread) : "未诊断"],
        ["单调性", completed ? four(completed.monotonicity) : "未诊断"],
        ["换手率均值", completed ? four(completed.turnover_mean) : "未诊断"],
        ["方向", `${item.direction_label} ${item.direction === "negative" ? "↓" : "↑"}`],
      ],
    },
  ];
  if (completed) {
    const quantiles = completed.quantiles || {};
    groups.push({
      title: "分布特征",
      metrics: [
        ["均值", four(completed.factor_mean)],
        ["标准差", four(completed.factor_std)],
        ["最小值", four(completed.factor_min)],
        ["最大值", four(completed.factor_max)],
        ["P1 / P5", `${four(quantiles.p1)} / ${four(quantiles.p5)}`],
        ["P25 / P50", `${four(quantiles.p25)} / ${four(quantiles.p50)}`],
        ["P75 / P95 / P99", `${four(quantiles.p75)} / ${four(quantiles.p95)} / ${four(quantiles.p99)}`],
        ["偏度", four(completed.factor_skew)],
        ["峰度", four(completed.factor_kurtosis)],
      ],
    });
  }
  groups.forEach((group) => {
    const heading = document.createElement("h4");
    heading.textContent = group.title;
    summaryBox.append(heading);
    const grid = document.createElement("div");
    grid.className = "factor-metric-grid";
    group.metrics.forEach(([label, value]) => {
      const card = appendFactorText(grid, "div", "factor-metric", "");
      appendFactorText(card, "strong", null, value);
      appendFactorText(card, "span", null, label);
    });
    summaryBox.append(grid);
  });
    renderFactorDailyIc(completed);
  renderFactorSampleTable(sample);
}

function factorSamplePageSize() {
  return tablePageSize(FACTOR_SAMPLE_PAGE_KEY, 10);
}

function bindFactorSamplePager(sample) {
  const pager = tablePager();
  const host = document.getElementById("factor-sample-pagination");
  if (!pager || !host) return;
  const pageSize = factorSamplePageSize();
  const total = sample.total || 0;
  bindTablePager(host, {
    page: sample.page || factorSamplePage,
    pages: pager.pagesFor(total, pageSize),
    pageSize,
    total,
    storageKey: FACTOR_SAMPLE_PAGE_KEY,
    onPage: (next) => { factorSamplePage = next; reloadFactorSample(); },
    onPageSize: () => { factorSamplePage = 1; reloadFactorSample(); },
  });
}

function renderFactorSampleTable(sample) {
  const table = document.getElementById("factor-sample");
  if (!table) return;
  table.replaceChildren();
  const sampleRows = sample.items || [];
  if (sampleRows.length) {
    table.style.setProperty("--factor-col-count", String(Math.max((sample.fields || []).length, 1)));
    const header = appendFactorText(table, "div", "factor-row factor-header", "");
    (sample.fields || []).forEach((field) => appendFactorText(header, "span", null, field));
    sampleRows.forEach((item) => {
      const row = appendFactorText(table, "div", "factor-row", "");
      (sample.fields || []).forEach((field) => appendFactorText(row, "span", null, item[field]));
    });
  }
  bindFactorSamplePager(sample || {});
}

async function fetchFactorSample(factorId, latestRun) {
  const pageSize = factorSamplePageSize();
  const query = factorParams();
  query.set("factor", factorId);
  query.set("page", String(factorSamplePage));
  query.set("page_size", String(pageSize));
  query.set("max_rows", "5000");
  if (!query.get("date_from")) query.set("date_from", latestRun?.date_from || "2024-01-01");
  if (!query.get("date_to")) query.set("date_to", latestRun?.date_to || "2024-12-31");
  const sampleResponse = await fetch(`/api/factor-data/query?${query.toString()}`);
  if (!sampleResponse.ok) return {items: [], fields: [], total: 0, page: factorSamplePage};
  return sampleResponse.json();
}

async function reloadFactorSample() {
  const factorId = currentFactorItem && currentFactorItem.factor_id;
  if (!factorId) return;
  const latest = (currentFactorHistory || []).find((entry) => entry.status === "completed") || null;
  try {
    const sample = await fetchFactorSample(factorId, latest);
    currentFactorSample = sample;
    renderFactorSampleTable(sample);
  } catch (_error) {}
}

let factorIcChartInstance = null;

function renderFactorChartSeries(block, chartData, color, title) {
  if (typeof window.LightweightCharts === "undefined") return;
  const theme = qlChartColors();
  const chart = window.LightweightCharts.createChart(block, {
    autoSize: true,
    height: 220,
    layout: {background: {type: window.LightweightCharts.ColorType.Solid, color: theme.bg}, textColor: theme.text, fontSize: 11},
    grid: {vertLines: {color: theme.grid}, horzLines: {color: theme.grid}},
    rightPriceScale: {borderColor: theme.border},
    timeScale: {borderColor: theme.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN"},
    handleScroll: false,
    handleScale: false,
  });
  const heading = document.createElement("div");
  heading.className = "factor-chart-title";
  heading.textContent = title;
  block.append(heading);
  const series = chart.addLineSeries({color: color, lineWidth: 2, priceFormat: {type: "price", precision: 4}});
  series.setData(chartData.filter((item) => item.time));
  series.createPriceLine({price: 0, color: theme.border, lineWidth: 1, lineStyle: window.LightweightCharts.LineStyle.Dashed, axisLabelVisible: false});
  chart.timeScale().fitContent();
  chart.applyOptions({rightPriceScale: {scaleMargins: {top: 0.1, bottom: 0.1}}});
  return chart;
}


function factorChartHeading(title) {
  const heading = document.createElement("div");
  heading.className = "factor-chart-title";
  heading.append(title);
  return heading;
}

const FACTOR_CONDITIONAL_COLORS = ["#e0b48a", "#c4622d", "#8c5340", "#4d6b5c", "#2f6a4a"];
const FACTOR_CONDITIONAL_CHARTS = [
  {
    key: "by_float_market_cap",
    title: "分层 IC · 流通市值",
    missing: "行情宽表没有流通市值字段，无法分层。",
    thin: "有效样本不足以按市值分层（每档至少 3 只）。",
  },
  {
    key: "by_turn",
    title: "分层 IC · 换手率",
    missing: "行情宽表没有换手率字段，无法分层。",
    thin: "有效样本不足以按换手分层（每档至少 3 只）。",
  },
];

function renderFactorConditionalBars(container, title, buckets) {
  const block = document.createElement("div");
  block.className = "factor-chart-block factor-ic-buckets";
  block.append(factorChartHeading(title));
  const values = buckets.map((item) => item.ic_mean).filter((value) => value != null).map(Number);
  const maxAbs = Math.max(...values.map(Math.abs), 1e-6);
  const row = document.createElement("div");
  row.className = "factor-ic-bucket-bars";
  buckets.forEach((bucket) => {
    const cell = document.createElement("div");
    cell.className = "factor-ic-bucket";
    const track = document.createElement("div");
    track.className = "factor-ic-bucket-track";
    const zero = document.createElement("div");
    zero.className = "factor-ic-bucket-zero";
    track.append(zero);
    if (bucket.ic_mean != null) {
      const bar = document.createElement("div");
      const ic = Number(bucket.ic_mean);
      bar.className = `factor-ic-bucket-bar ${ic >= 0 ? "positive" : "negative"}`;
      bar.style.height = `${Math.max(2, Math.round((Math.abs(ic) / maxAbs) * 50))}%`;
      bar.title = `${bucket.label} · IC ${ic.toFixed(4)} · ${bucket.effective_days || 0} 日`;
      track.append(bar);
    }
    const label = document.createElement("span");
    label.className = "factor-ic-bucket-label";
    label.textContent = bucket.label || `Q${bucket.bucket}`;
    const value = document.createElement("span");
    value.className = "factor-ic-bucket-value";
    value.textContent = bucket.ic_mean == null ? "—" : Number(bucket.ic_mean).toFixed(4);
    cell.append(track, label, value);
    row.append(cell);
  });
  block.append(row);
  container.append(block);
}

function renderFactorConditionalLines(container, title, buckets) {
  if (typeof window.LightweightCharts === "undefined") return;
  const hasSeries = buckets.some((bucket) => (bucket.daily_ic || []).length);
  if (!hasSeries) return;
  const block = document.createElement("div");
  block.className = "factor-chart-block factor-ic-lines";
  const chartHost = document.createElement("div");
  chartHost.className = "factor-ic-line-host";
  block.append(factorChartHeading(title + " · 每日曲线"));
  const legend = document.createElement("div");
  legend.className = "factor-ic-bucket-legend";
  block.append(chartHost, legend);
  container.append(block);
  const _fc = qlChartColors();
  const chart = window.LightweightCharts.createChart(chartHost, {
    autoSize: true,
    height: 220,
    layout: {background: {type: window.LightweightCharts.ColorType.Solid, color: _fc.bg}, textColor: _fc.text, fontSize: 11},
    grid: {vertLines: {color: _fc.grid}, horzLines: {color: _fc.grid}},
    rightPriceScale: {borderColor: _fc.border},
    timeScale: {borderColor: _fc.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN", dateFormat: "yyyy-MM-dd"},
    handleScroll: false,
    handleScale: false,
  });
  buckets.forEach((bucket, index) => {
    const points = (bucket.daily_ic || [])
      .map((point) => ({time: klineBusinessDate(point.date), value: point.ic}))
      .filter((point) => point.time != null && Number.isFinite(point.value));
    if (!points.length) return;
    const series = chart.addLineSeries({
      color: FACTOR_CONDITIONAL_COLORS[index] || qlCss("--color-primary", "#c4622d"),
      lineWidth: 2,
      priceFormat: {type: "price", precision: 4},
    });
    series.setData(points);
    if (index === 0) series.createPriceLine({price: 0, color: "#c8d0dd", lineWidth: 1, lineStyle: window.LightweightCharts.LineStyle.Dashed, axisLabelVisible: false});
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "factor-ic-bucket-toggle";
    toggle.setAttribute("aria-pressed", "true");
    toggle.title = `隐藏 ${bucket.label || `Q${bucket.bucket}`}`;
    const swatch = document.createElement("span");
    swatch.className = "factor-ic-swatch";
    swatch.style.background = FACTOR_CONDITIONAL_COLORS[index] || qlCss("--color-primary", "#c4622d");
    toggle.append(swatch, bucket.label || `Q${bucket.bucket}`);
    toggle.addEventListener("click", () => {
      const visible = toggle.getAttribute("aria-pressed") !== "false";
      series.setData(visible ? [] : points);
      toggle.setAttribute("aria-pressed", String(!visible));
      toggle.title = `${visible ? "显示" : "隐藏"} ${bucket.label || `Q${bucket.bucket}`}`;
    });
    legend.append(toggle);
  });
  chart.timeScale().fitContent();
}

function renderFactorConditionalIc(container, latest) {
  const payload = factorConditionalPayload(latest);
  if (!payload) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "此任务未含分层 IC，重新运行分析后显示。";
    container.append(empty);
    return;
  }
  FACTOR_CONDITIONAL_CHARTS.forEach((spec) => {
    const dimension = payload[spec.key];
    if (!dimension) return;
    if (dimension.status === "missing_field") {
      const note = document.createElement("p");
      note.className = "state";
      note.textContent = spec.missing;
      container.append(note);
      return;
    }
    if (dimension.status !== "available") {
      const note = document.createElement("p");
      note.className = "state";
      note.textContent = spec.thin;
      container.append(note);
      return;
    }
    const buckets = dimension.buckets || [];
    renderFactorConditionalBars(container, spec.title, buckets);
    renderFactorConditionalLines(container, spec.title, buckets);
  });
}

function formatFactorChartNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const abs = Math.abs(number);
  if (abs >= 1e6 || (abs > 0 && abs < 1e-3)) return number.toExponential(2);
  if (abs >= 100) return number.toFixed(1);
  return number.toFixed(4);
}

function renderFactorHistogramOverlay(histogram, maxCount, height) {
  const hasExpected = histogram.some((item) => Number.isFinite(Number(item.expected)));
  if (!hasExpected) return null;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("factor-normal-overlay");
  svg.setAttribute("viewBox", `0 0 ${histogram.length} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", histogram.map((item, index) => {
    const expected = Math.max(0, Number(item.expected) || 0);
    const y = height - (expected / maxCount) * (height - 10);
    return `${index ? "L" : "M"}${(index + 0.5).toFixed(2)} ${y.toFixed(2)}`;
  }).join(" "));
  svg.append(path);
  return svg;
}

function renderFactorDailyIc(latest) {
  const container = document.getElementById("factor-charts");
  if (!container) return;
  container.replaceChildren();
  if (!latest || !latest.summary || typeof window.LightweightCharts === "undefined") {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "图表资源未加载或尚未运行计算。";
    container.append(empty);
    return;
  }
  const icPoints = latest.summary.daily_ic || [];
  const spreadPoints = latest.summary.daily_top_bottom || [];
  if (icPoints.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block";
    container.append(block);
    const chart = renderFactorChartSeries(block, icPoints.map((point) => ({time: klineBusinessDate(point.date), value: point.ic})), qlCss("--color-primary", "#c4622d"), "每日 IC 曲线");
    if (chart) factorIcChartInstance = chart;
  }
  if (spreadPoints.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block";
    container.append(block);
    renderFactorChartSeries(block, spreadPoints.map((point) => ({time: klineBusinessDate(point.date), value: point.spread})), qlCss("--color-danger", "#9b2c2c"), "Top-Bottom 每日价差");
  }
  renderFactorConditionalIc(container, latest);
  const histogram = latest.histogram || [];
  if (histogram.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block factor-histogram";
    const histogramRange = latest.histogram_range || latest.summary?.histogram_range || {};
    const logScale = histogramRange.scale === "log";
    const maxCount = Math.max(
      ...histogram.map((item) => item.count || 0),
      ...histogram.map((item) => Number(item.expected) || 0),
      1,
    );
    const yAxis = document.createElement("div");
    yAxis.className = "factor-histogram-y";
    [maxCount, Math.round(maxCount / 2), 0].forEach((value) => {
      const label = document.createElement("span");
      label.textContent = Math.round(value);
      yAxis.append(label);
    });
    const bars = document.createElement("div");
    bars.className = "factor-histogram-bars";
    histogram.forEach((item) => {
      const bar = document.createElement("div");
      bar.className = "factor-histogram-bar";
      bar.style.height = `${Math.max(2, Math.round((item.count / maxCount) * 140))}px`;
      const expected = Number.isFinite(Number(item.expected)) ? ` · 正态期望 ${Math.round(Number(item.expected))}` : "";
      bar.title = `${formatFactorChartNumber(item.left)} ~ ${formatFactorChartNumber(item.right)} · ${item.count} 行${expected}`;
      bars.append(bar);
    });
    const overlay = renderFactorHistogramOverlay(histogram, maxCount, 150);
    if (overlay) bars.append(overlay);
    const xAxis = document.createElement("div");
    xAxis.className = "factor-histogram-x";
    const minLabel = document.createElement("span");
    minLabel.textContent = formatFactorChartNumber(histogram[0].left);
    const midLabel = document.createElement("span");
    midLabel.textContent = formatFactorChartNumber(histogram[Math.floor(histogram.length / 2)].left);
    const maxLabel = document.createElement("span");
    maxLabel.textContent = formatFactorChartNumber(histogram[histogram.length - 1].right);
    xAxis.append(minLabel, midLabel, maxLabel);
    const rangeNote = document.createElement("span");
    rangeNote.className = "factor-histogram-range-note";
    const scaleLabel = logScale ? "对数" : "线性";
    if (histogramRange.basis === "p1_p99") {
      rangeNote.textContent = `显示 P1–P99 · ${scaleLabel} · 两端极值：${histogramRange.lower_tail_count || 0} / ${histogramRange.upper_tail_count || 0}`;
    } else {
      rangeNote.textContent = `显示全部原始范围 · ${scaleLabel}`;
    }
    block.append(factorChartHeading(logScale ? "原始因子值分布（对数分桶）" : "原始因子值分布"), rangeNote, yAxis, bars, xAxis);
    container.append(block);
  }
  if (!container.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "尚无每日 IC 序列，运行计算后在此显示。";
    container.append(empty);
  }
}

function renderFactorCalculationHistory(items) {
  const container = document.getElementById("factor-calculation-history");
  if (!container) return;
  container.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = "计算历史";
  container.append(heading);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "尚未运行真实计算；运行后每条记录的周期、时间、指标与错误都会留在这里。";
    container.append(empty);
    return;
  }
  const table = document.createElement("div");
  table.className = "factor-calculation-table";
  table.setAttribute("role", "table");
  const header = appendFactorText(table, "div", "factor-row factor-header factor-calculation-row", "");
  ["任务（流水号 · 周期）", "开始 → 完成", "覆盖 / 缺失", "IC 均值 / 正值 / 稳定性", "状态 / 说明"].forEach((label) => appendFactorText(header, "span", null, label));
  items.forEach((item) => {
    const row = appendFactorText(table, "div", "factor-calculation-row", "");
    row.classList.toggle("factor-calculation-row-active", item.calculation_id === activeFactorRunId);
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-label", `切换到 ${item.serial_no}`);
    appendFactorText(row, "span", null, `${item.serial_no}
${String(item.date_from).slice(0, 4)}-${String(item.date_from).slice(4, 6)}-${String(item.date_from).slice(6, 8)}\n${String(item.date_to).slice(0, 4)}-${String(item.date_to).slice(4, 6)}-${String(item.date_to).slice(6, 8)}`);
    const startRaw = String(item.started_at || "");
    const endRaw = item.finished_at ? String(item.finished_at) : "";
    const startDate = startRaw.slice(0, 10);
    const startTime = startRaw.slice(11, 16);
    const endDate = endRaw.slice(0, 10);
    const endTime = endRaw.slice(11, 16);
    const timeText = startDate === endDate && endDate ? `${startDate} ${startTime} → ${endTime}` : `${startDate} ${startTime} → ${endDate || "—"} ${endTime || "—"}`;
    appendFactorText(row, "span", null, timeText);
    appendFactorText(row, "span", null, `${item.coverage == null ? "—" : (item.coverage * 100).toFixed(2) + "%"} / ${item.missing_rows ?? "—"}`);
    appendFactorText(row, "span", null, `${item.ic_mean == null ? "—" : Number(item.ic_mean).toFixed(4)}\n${item.ic_positive_ratio == null ? "—" : (Number(item.ic_positive_ratio) * 100).toFixed(1) + "%"}\n${item.ic_std == null ? "—" : Number(item.ic_std).toFixed(4)}`);
    appendFactorText(row, "span", null, `${item.status === "completed" ? "已完成" : item.status === "failed" ? "失败" : "计算中"} · ${item.error_message || `${item.effective_days ?? 0} 日`}`);
    row.addEventListener("click", () => {
      activeFactorRunId = item.calculation_id;
      renderFactorDetail(currentFactorItem, null, currentFactorSample, item);
      renderFactorCalculationHistory(currentFactorHistory);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        row.click();
      }
    });
  });
  container.append(table);
}

async function loadFactorDetail(factorId) {
  const error = document.getElementById("factor-error");
  factorSamplePage = 1;
  try {
    const [detailResponse, calculationResponse] = await Promise.all([
      fetch(`/api/factor-data/factors/${encodeURIComponent(factorId)}`),
      fetch(`/api/factor-calculations?factor_id=${encodeURIComponent(factorId)}`),
    ]);
    if (![detailResponse, calculationResponse].every((response) => response.ok)) throw new Error("因子详情请求失败");
    const item = await detailResponse.json();
    const historyItems = (await calculationResponse.json()).items || [];
    const latestRun = historyItems.find((entry) => entry.status === "completed") || null;
    let sample = {items: [], fields: [], total: 0};
    try {
      sample = await fetchFactorSample(factorId, latestRun);
    } catch (_error) {
      // Manual/derived factors may not have a physical feature column yet.
    }
    currentFactorHistory = historyItems;
    if (activeFactorRunId === null && latestRun) activeFactorRunId = latestRun.calculation_id;
    renderFactorDetail(item, null, sample, latestRun);
    renderFactorCalculationHistory(historyItems);
  } catch (errorValue) {
    error.textContent = `因子详情加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}
