let klinePage = 1;
let klinePreferLast = true;
const KLINE_PAGE_KEY = "quantlab-kline-page-size";
function klineParams({downsample = null, pageSize = null, tail = false, page = 1, maxRows = 500} = {}) {
  const params = new URLSearchParams();
  const symbol = document.getElementById("kline-symbol").value.trim();
  const dateFrom = document.getElementById("kline-date-from").value;
  const dateTo = document.getElementById("kline-date-to").value;
  const mode = document.getElementById("kline-mode").value;
  if (symbol) params.set("ts_code", symbol);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  params.set("version_id", "current");
  params.set("mode", mode);
  params.set("page", String(page));
  params.set("page_size", tail ? String(pageSize || 10) : pageSize ? String(pageSize) : downsample ? "500" : "100");
  params.set("max_rows", String(maxRows));
  if (downsample) params.set("downsample", String(downsample));
  if (tail) params.set("tail", "1");
  return params;
}

function appendKlineText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function klineStatus(item, mode) {
  const flags = [];
  if (Number(item.st_status) !== 0) flags.push("ST");
  const close = Number(item[`${mode}_close`]);
  const up = Number(item[`${mode}_up_limit`]);
  const down = Number(item[`${mode}_down_limit`]);
  if (!Number.isFinite(close)) flags.push("无价格");
  if (Number.isFinite(close) && Number.isFinite(up) && close >= up) flags.push("涨停");
  if (Number.isFinite(close) && Number.isFinite(down) && close <= down) flags.push("跌停");
  return flags.length ? flags.join(" · ") : "正常";
}

const KLINE_FIELD_EXPLANATIONS = {
  "trade_date": ["交易日期", "格式 YYYYMMDD，为交易所交易日历上的日期。"],
  "ts_code": ["股票代码", "6 位数字加交易所后缀：000001.SZ 为深交所、600000.SH 为上交所。"],
  "vol": ["成交量", "该交易日该股票的总成交量。"],
  "amount": ["成交额", "该交易日该股票的总成交金额。"],
  "adj_factor": ["复权因子", "除权除息后的复权比例。后复权价 = 原始价 × adj_factor。"],
  "st_status": ["ST 状态", "0 = 正常；1 = ST；2 = *ST。"],
  "is_suspended": ["停牌标记", "1 = 当日停牌；0 = 正常交易。"],
  "eligible": ["可入池", "1 = 可进入交易/研究池；0 = 被排除（如停牌或退市整理等）。"],
  "exchange": ["交易所", "SSE = 上交所；SZSE = 深交所。"],
  "list_date": ["上市日期", "股票上市日期，格式 YYYYMMDD。"],
  "delist_date": ["退市日期", "已退市股票的退市日期；未退市通常为空或远期日期。"],
  "pe_ttm": ["市盈率 TTM", "按最近四个季度滚动计算的市盈率，来自 daily_basic（倍）。"],
  "total_mv": ["总市值（原始）", "Tushare daily_basic 原始单位：万元。"],
  "circ_mv": ["流通市值（原始）", "Tushare daily_basic 原始单位：万元。"],
  "dv_ttm": ["股息率 TTM（原始）", "Tushare daily_basic 原始单位：百分比（如 2.5 表示 2.5%）。"],
  "turnover_rate": ["换手率（原始）", "Tushare daily_basic 原始单位：百分比（如 1.8 表示 1.8%）。"],
  "total_market_cap": ["总市值（元）", "标准换算口径：total_mv × 10000，单位为元。"],
  "float_market_cap": ["流通市值（元）", "标准换算口径：circ_mv × 10000，单位为元。"],
  "dividend_yield_ratio": ["股息率（小数）", "标准换算口径：dv_ttm ÷ 100，如 0.025 表示 2.5%。"],
  "turn": ["换手率因子", "日换手率，Tushare 原值：百分比（如 2.5 表示 2.5%），供因子与回测使用。"],
  "raw_open": ["原始开盘价", "未复权开盘价（元），与 canonical 的 open 字段一致。"],
  "raw_high": ["原始最高价", "未复权最高价（元）。"],
  "raw_low": ["原始最低价", "未复权最低价（元）。"],
  "raw_close": ["原始收盘价", "未复权收盘价（元）。"],
  "raw_up_limit": ["原始涨停价", "未复权涨停价（元）。"],
  "raw_down_limit": ["原始跌停价", "未复权跌停价（元）。"],
  "hfq_open": ["后复权开盘价", "后复权开盘价（元）= 原始开盘价 × adj_factor；正式研究口径。"],
  "hfq_high": ["后复权最高价", "后复权最高价（元）= 原始最高价 × adj_factor。"],
  "hfq_low": ["后复权最低价", "后复权最低价（元）= 原始最低价 × adj_factor。"],
  "hfq_close": ["后复权收盘价", "后复权收盘价（元）= 原始收盘价 × adj_factor；因子计算使用该价格。"],
  "hfq_up_limit": ["后复权涨停价", "后复权涨停价（元）= 原始涨停价 × adj_factor。"],
  "hfq_down_limit": ["后复权跌停价", "后复权跌停价（元）= 原始跌停价 × adj_factor。"],
  "状态": ["自动状态", "根据该行数据判断：ST（st_status ≠ 0）、无价格、涨停/跌停，正常显示“正常”。"],
};

function renderKlineTable(payload) {
  const table = document.getElementById("kline-table");
  const empty = document.getElementById("kline-empty");
  table.replaceChildren();
  empty.classList.toggle("hidden", payload.items.length !== 0);
  if (!payload.items.length) return;
  const grid = document.createElement("div");
  grid.className = "kline-grid";
  grid.setAttribute("role", "rowgroup");
  const header = appendKlineText(grid, "div", "kline-row kline-header", "");
  header.setAttribute("role", "row");
  payload.fields.forEach((field) => {
    const cell = appendKlineText(header, "span", "factor-column-header", field);
    const explanation = KLINE_FIELD_EXPLANATIONS[field];
    if (explanation) cell.append(infoDot(field, explanation));
  });
  const statusHeader = appendKlineText(header, "span", "factor-column-header", "状态");
  statusHeader.append(infoDot("状态", KLINE_FIELD_EXPLANATIONS["状态"]));
  payload.items.forEach((item) => {
    const row = appendKlineText(grid, "div", "kline-row", "");
    row.setAttribute("role", "row");
    payload.fields.forEach((field) => {
      const value = item[field];
      const text = typeof value === "number" ? value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : value;
      appendKlineText(row, "span", null, text);
    });
    appendKlineText(row, "span", null, klineStatus(item, payload.mode));
  });
  grid.style.gridTemplateColumns = `repeat(${payload.fields.length + 1}, max-content)`;
  table.append(grid);
}

let klineChartInstance = null;

function klineBusinessDate(value) {
  const text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  return null;
}

function renderKlineChart(payload) {
  const container = document.getElementById("kline-chart");
  if (klineChartInstance) {
    try { klineChartInstance.remove(); } catch (_error) { /* already disposed */ }
    klineChartInstance = null;
  }
  container.replaceChildren();
  const mode = payload.mode;
  const rows = payload.items
    .filter((item) => item.ts_code && item.trade_date && ["open", "high", "low", "close"].every((part) => Number.isFinite(Number(item[`${mode}_${part}`]))))
    .sort((left, right) => String(left.trade_date).localeCompare(String(right.trade_date)));
  if (!rows.length) {
    appendKlineText(container, "p", "state", "没有可绘制的完整价格记录。");
    return;
  }
  const symbols = Array.from(new Set(rows.map((item) => item.ts_code)));
  const chartSymbol = symbols[0];
  const candles = rows.filter((item) => item.ts_code === chartSymbol).map((item) => {
    const time = klineBusinessDate(item.trade_date);
    if (!time) return null;
    return {
      time,
      open: Number(item[`${mode}_open`]),
      high: Number(item[`${mode}_high`]),
      low: Number(item[`${mode}_low`]),
      close: Number(item[`${mode}_close`]),
    };
  }).filter(Boolean);
  if (!candles.length) {
    appendKlineText(container, "p", "state", "没有可绘制的完整价格记录。");
    return;
  }
  if (typeof window.LightweightCharts === "undefined") {
    appendKlineText(container, "p", "state", "TradingView Lightweight Charts 资源未加载，图表不可用。");
    return;
  }
  const theme = qlChartColors();
  const chart = window.LightweightCharts.createChart(container, {
    autoSize: true,
    height: 430,
    layout: {
      background: {type: window.LightweightCharts.ColorType.Solid, color: theme.bg},
      textColor: theme.text,
      fontSize: 11,
    },
    grid: {vertLines: {color: theme.grid}, horzLines: {color: theme.grid}},
    rightPriceScale: {borderColor: theme.border},
    timeScale: {borderColor: theme.border, timeVisible: false, secondsVisible: false},
    crosshair: {mode: 0},
    localization: {locale: "zh-CN"},
  });
  klineChartInstance = chart;
  const up = qlCss("--color-danger", "#9b2c2c");
  const down = qlCss("--color-ok", "#2f6a4a");
  const candleSeries = chart.addCandlestickSeries({
    upColor: up,
    downColor: down,
    borderUpColor: up,
    borderDownColor: down,
    wickUpColor: up,
    wickDownColor: down,
  });
  candleSeries.setData(candles);
  chart.priceScale("right").applyOptions({scaleMargins: {top: 0.08, bottom: 0.22}});
  const volumes = rows.filter((item) => item.ts_code === chartSymbol && item.vol != null).map((item) => {
    const candle = candles.find((entry) => entry.time === klineBusinessDate(item.trade_date));
    if (!candle) return null;
    return {
      time: candle.time,
      value: Number(item.vol),
      color: candle.close >= candle.open ? "rgba(155,44,44,.35)" : "rgba(47,106,74,.35)",
    };
  }).filter(Boolean);
  if (volumes.length) {
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: {type: "volume"},
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({scaleMargins: {top: 0.8, bottom: 0}});
    volumeSeries.setData(volumes);
  }
  if (symbols.length > 1) {
    const note = appendKlineText(container, "p", "kline-chart-note", `图表展示 ${chartSymbol}；当前共 ${symbols.length} 只标的，其余标的请在数据表中查看。`);
    container.insertBefore(note, container.firstChild);
  }
  const legend = document.createElement("div");
  legend.className = "kline-chart-legend";
  const modeLabel = payload.mode === "raw" ? "原始" : "后复权";
  legend.textContent = `${chartSymbol} · 日线 · ${modeLabel}`;
  container.insertBefore(legend, container.firstChild);
  chart.timeScale().fitContent();
}

function renderKlineQuality(quality) {
  const container = document.getElementById("kline-quality");
  container.replaceChildren();
  appendKlineText(container, "strong", `quality-${quality.status}`, quality.status);
  appendKlineText(container, "span", null, ` ${quality.date_min ?? "—"} 至 ${quality.date_max ?? "—"} · ${quality.row_count} 行`);
  Object.entries(quality.checks).forEach(([name, value]) => {
    appendKlineText(container, "p", null, `${name}: ${value}`);
  });
}

function klineTablePageSize() {
  return tablePageSize(KLINE_PAGE_KEY, 10);
}

async function fetchKlinePayload(params) {
  const response = await fetch(`/api/kline/query?${params}`);
  if (!response.ok) throw new Error("标准行情宽表 API 请求失败");
  return response.json();
}

function bindKlinePager(payload) {
  const pager = tablePager();
  const pageSize = klineTablePageSize();
  const pages = pager ? pager.pagesFor(payload.total || 0, pageSize) : 1;
  bindTablePager(document.getElementById("kline-pagination"), {
    page: klinePage,
    pages,
    pageSize,
    total: payload.total || 0,
    storageKey: KLINE_PAGE_KEY,
    onPage: (next) => { klinePreferLast = false; klinePage = next; loadKlineTable(); },
    onPageSize: () => { klinePreferLast = true; loadKlineTable(); },
  });
}

async function loadKlineTable() {
  const pager = tablePager();
  const pageSize = klineTablePageSize();
  let page = klinePreferLast ? 1 : klinePage;
  let table = await fetchKlinePayload(klineParams({pageSize, page, maxRows: 5000}));
  const pages = pager ? pager.pagesFor(table.total || 0, pageSize) : 1;
  if (klinePreferLast) {
    klinePage = pages;
    if (page !== pages) table = await fetchKlinePayload(klineParams({pageSize, page: pages, maxRows: 5000}));
    klinePreferLast = false;
  } else if (pager) {
    klinePage = pager.clampPage(klinePage, pages);
    if (klinePage !== page) table = await fetchKlinePayload(klineParams({pageSize, page: klinePage, maxRows: 5000}));
  }
  renderKlineTable(table);
  bindKlinePager(table);
}

async function loadKline() {
  const error = document.getElementById("kline-error");
  error.classList.add("hidden");
  klinePreferLast = true;
  const chartParams = klineParams({pageSize: 500});
  const qualityParams = new URLSearchParams({version_id: "current"});
  try {
    const [chartResponse, qualityResponse] = await Promise.all([
      fetch(`/api/kline/query?${chartParams}`),
      fetch(`/api/kline/quality?${qualityParams}`),
    ]);
    if (![chartResponse, qualityResponse].every((response) => response.ok)) throw new Error("标准行情宽表 API 请求失败");
    const [chart, quality] = await Promise.all([chartResponse.json(), qualityResponse.json()]);
    renderKlineChart(chart);
    renderKlineQuality(quality);
    await loadKlineTable();
  } catch (errorValue) {
    error.textContent = `标准行情宽表加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

function exportKline() {
  const params = klineParams();
  params.delete("page"); params.delete("page_size"); params.delete("downsample");
  params.delete("tail");
  window.location.assign(`/api/kline/export.csv?${params}`);
}

async function loadKlineVersions() {
  const queryDateFrom = new URLSearchParams(window.location.search).get("date_from");
  const queryDateTo = new URLSearchParams(window.location.search).get("date_to");
  const response = await fetch("/api/datasets/ds_canonical_market/versions");
  if (!response.ok) return;
  const versions = await response.json();
  const dateToInput = document.getElementById("kline-date-to");
  const dateFromInput = document.getElementById("kline-date-from");
  if (queryDateTo) dateToInput.value = queryDateTo;
  if (queryDateFrom) dateFromInput.value = queryDateFrom;
  if (!dateFromInput.value || !dateToInput.value) {
    const latest = versions.find((item) => item.quality_status === "passed" && item.date_max) || versions[0];
    const maxDate = latest && latest.date_max ? String(latest.date_max) : "";
    if (/^\d{8}$/.test(maxDate)) {
      if (!dateToInput.value) dateToInput.value = `${maxDate.slice(0, 4)}-${maxDate.slice(4, 6)}-${maxDate.slice(6, 8)}`;
      if (!dateFromInput.value) dateFromInput.value = `${maxDate.slice(0, 4)}-01-01`;
    }
  }
}
