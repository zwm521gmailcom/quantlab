let factorSamplePage = 1;
const FACTOR_SAMPLE_PAGE_KEY = "quantlab-factor-sample-page-size";
function appendFactorText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function factorParams() {
  const params = new URLSearchParams();
  const factor = document.getElementById("factor-filter")?.value || "";
  const dateFrom = document.getElementById("factor-date-from")?.value || "";
  const dateTo = document.getElementById("factor-date-to")?.value || "";
  if (factor) params.set("factor", factor);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  params.set("version_id", "v1");
  return params;
}

function populateFactorCategoryFilter(items) {
  const root = document.getElementById("factor-category-filter");
  const dropdown = root && root.querySelector(".multi-select-dropdown");
  if (!root || !dropdown || root.dataset.filled === "1") return;
  const categories = [...new Set(items.map((item) => String(item.category || "未分类").trim() || "未分类"))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
  categories.forEach((category) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "factor-category";
    input.value = category;
    input.checked = true;
    input.dataset.label = category;
    label.append(input, document.createTextNode(` ${category}`));
    dropdown.append(label);
  });
  root.dataset.filled = "1";
  bindMultiSelect(root);
  dropdown.addEventListener("change", () => {
    factorCatalogPage = 1;
    renderFactorCatalog(factorCatalogAll);
  });
}

function factorHeaderCell(label, explanationLines) {
  const cell = document.createElement("span");
  cell.className = "factor-column-header";
  cell.append(label);
  if (explanationLines && explanationLines.length) {
    cell.append(infoDot(label, explanationLines));
  }
  return cell;
}

let factorCatalogAll = [];
let factorCatalogPage = 1;
const FACTOR_PAGE_SIZES = [50, 100, 200, 500];
const FACTOR_PAGE_SIZE_KEY = "quantlab-factor-page-size";

function factorPageSizeSelects() {
  return [...document.querySelectorAll("[data-factor-page-size], #factor-page-size")];
}

function factorCatalogPageSize() {
  const select = factorPageSizeSelects()[0];
  const raw = Number(select && select.value);
  if (FACTOR_PAGE_SIZES.includes(raw)) return raw;
  try {
    const stored = Number(localStorage.getItem(FACTOR_PAGE_SIZE_KEY));
    if (FACTOR_PAGE_SIZES.includes(stored)) return stored;
  } catch (_error) {}
  return 50;
}

function makeFactorPageSizeSelect() {
  const label = document.createElement("label");
  label.className = "factor-page-size";
  label.append("每页最大显示数量 ");
  const select = document.createElement("select");
  select.setAttribute("data-factor-page-size", "1");
  select.setAttribute("aria-label", "每页最大显示数量");
  FACTOR_PAGE_SIZES.forEach((size) => {
    const option = document.createElement("option");
    option.value = String(size);
    option.textContent = String(size);
    select.append(option);
  });
  label.append(select);
  return {label, select};
}

function ensureFactorPageSizeControl() {
  const hosts = [
    document.querySelector("#factor-catalog-view .data-toolbar"),
    document.querySelector("#factor-catalog-view .factor-pagination"),
  ].filter(Boolean);
  hosts.forEach((host) => {
    if (host.querySelector("[data-factor-page-size], #factor-page-size")) return;
    const {label} = makeFactorPageSizeSelect();
    host.append(label);
  });
  const selects = factorPageSizeSelects();
  const value = String(factorCatalogPageSize());
  selects.forEach((select) => {
    select.value = value;
    if (select.dataset.bound) return;
    select.addEventListener("change", () => {
      try { localStorage.setItem(FACTOR_PAGE_SIZE_KEY, select.value); } catch (_error) {}
      factorCatalogPage = 1;
      factorPageSizeSelects().forEach((other) => { other.value = select.value; });
      renderFactorCatalog(factorCatalogAll);
    });
    select.dataset.bound = "1";
  });
  return selects[0];
}

function renderFactorCatalog(items) {
  factorCatalogAll = items;
  ensureFactorPageSizeControl();
  const table = document.getElementById("factor-table");
  const empty = document.getElementById("factor-empty");
  const query = (document.getElementById("factor-name-search")?.value || "").trim().toLowerCase();
  const assetClass = document.getElementById("factor-asset-class")?.value || "";
  const categoryBoxes = [...document.querySelectorAll("#factor-category-filter input[name='factor-category']")];
  const selectedCategories = new Set(categoryBoxes.filter((box) => box.checked).map((box) => box.value));
  const filtered = items.filter((item) => {
    if (assetClass && String(item.asset_class || "cn_a") !== assetClass) return false;
    if (categoryBoxes.length && !selectedCategories.has(String(item.category || "未分类").trim() || "未分类")) return false;
    if (!query) return true;
    return String(item.name || "").toLowerCase().includes(query) || String(item.factor_id || "").toLowerCase().includes(query);
  });
  const totalFiltered = filtered.length;
  const pageSize = factorCatalogPageSize();
  const pages = Math.max(1, Math.ceil(totalFiltered / pageSize));
  factorCatalogPage = Math.min(factorCatalogPage, pages);
  items = filtered.slice((factorCatalogPage - 1) * pageSize, factorCatalogPage * pageSize);
  const prev = document.getElementById("factor-page-prev");
  const next = document.getElementById("factor-page-next");
  const label = document.getElementById("factor-page-label");
  if (prev) prev.disabled = factorCatalogPage <= 1;
  if (next) next.disabled = factorCatalogPage >= pages;
  if (label) label.textContent = `第 ${factorCatalogPage} / ${pages} 页 · 共 ${totalFiltered} 个`;
  table.replaceChildren();
  empty.classList.toggle("hidden", totalFiltered !== 0);
  if (!totalFiltered) return;
  const header = appendFactorText(table, "div", "factor-row factor-header", "");
  appendFactorText(header, "span", null, "因子代码");
  appendFactorText(header, "span", null, "因子名称");
  appendFactorText(header, "span", null, "资产分类");
  appendFactorText(header, "span", null, "分类");
  appendFactorText(header, "span", null, "方向");
  header.append(factorHeaderCell("覆盖率", ["覆盖率 = 最近一次已完成计算中，因子非空行数 ÷ 计算周期内可用于计算的总行数。", "缺失行 = 计算周期内因子为空、或窗口不足的可计算行数。", "数据来自 canonical 标准行情宽表按计算任务现算；无已完成计算时显示“—”。"]));
  header.append(factorHeaderCell("IC 均值", ["最近一次真实计算中，全部有效交易日的每日截面 RankIC 平均值。", "IC = 当日所有股票因子排序与未来收益标签排序的秩相关。", "未计算时显示“未诊断”。"]));
  header.append(factorHeaderCell("IC 正值比例", ["IC > 0 的交易日占有效交易日的比例。", "越高说明因子方向越稳定。"]));
  header.append(factorHeaderCell("IC 稳定性", ["每日 IC 的标准差。", "标准差越小，因子预测能力越稳定。"]));
  header.append(factorHeaderCell("最近计算", ["最近一次真实计算的流水号、年份与有效交易日数。"]));
  appendFactorText(header, "span", null, "状态");
  items.forEach((item) => {
    const row = appendFactorText(table, "div", "factor-row", "");
    appendFactorText(row, "span", "factor-code", item.factor_id);
    const factorCell = appendFactorText(row, "span", "factor-name-cell", "");
    const name = appendFactorText(factorCell, "a", "factor-name", item.name);
    name.href = `/factors/${encodeURIComponent(item.factor_id)}`;
    factorCell.append(infoDot("因子说明", [`${item.factor_id} ${item.name}`, item.formula, ...(item.formula_explanation || [])].filter(Boolean)));
    appendFactorText(row, "span", "factor-asset-class", item.asset_class_label || "A股");
    appendFactorText(row, "span", "factor-category", item.category || "未分类");
    appendFactorText(row, "span", null, `${item.direction_label} ${item.direction === "negative" ? "↓" : "↑"}`);
    const coverageValue = item.latest_calculation && item.latest_calculation.status === "completed" && item.latest_calculation.coverage != null ? item.latest_calculation.coverage : item.coverage;
    appendFactorText(row, "span", "coverage-cell", coverageValue == null ? "—" : `${(coverageValue * 100).toFixed(2)}%`);
    const latest = item.latest_calculation && item.latest_calculation.status === "completed" ? item.latest_calculation : null;
    if (latest) {
      appendFactorText(row, "span", "ic-value", latest.ic_mean == null ? "—" : Number(latest.ic_mean).toFixed(4));
      appendFactorText(row, "span", "ic-value", latest.ic_positive_ratio == null ? "—" : `${(Number(latest.ic_positive_ratio) * 100).toFixed(1)}%`);
      appendFactorText(row, "span", "ic-value", latest.ic_std == null ? "—" : Number(latest.ic_std).toFixed(4));
      appendFactorText(row, "span", "factor-calc-cell", `${latest.serial_no} · ${String(latest.date_from).slice(0, 4)}—${String(latest.date_to).slice(0, 4)} · ${latest.effective_days ?? 0} 日`);
    } else {
      appendFactorText(row, "span", "muted", "未诊断");
      appendFactorText(row, "span", "muted", "未诊断");
      appendFactorText(row, "span", "muted", "未诊断");
      appendFactorText(row, "span", "muted", "未诊断");
    }
    appendFactorText(row, "span", `quality-${item.quality_status}`, item.status === "published" ? "已发布" : item.status);
  });
}

async function fetchFactorCatalog() {
  const response = await fetch("/api/factor-data/catalog");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function populateFactorFilter(items) {
  const select = document.getElementById("factor-filter");
  if (!select || select.options.length > 1) return;
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.factor_id;
    option.textContent = `${item.name} · ${item.factor_id}`;
    select.appendChild(option);
  });
}

async function loadFactors() {
  const error = document.getElementById("factor-error");
  try {
    const items = await fetchFactorCatalog();
    populateFactorFilter(items);
    populateFactorCategoryFilter(items);
    renderFactorCatalog(items);
    document.getElementById("factor-name-search")?.addEventListener("input", () => { factorCatalogPage = 1; renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-asset-class")?.addEventListener("change", () => { factorCatalogPage = 1; renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-page-prev")?.addEventListener("click", () => { factorCatalogPage = Math.max(1, factorCatalogPage - 1); renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-page-next")?.addEventListener("click", () => { factorCatalogPage += 1; renderFactorCatalog(factorCatalogAll); });
  } catch (errorValue) {
    error.textContent = `因子目录加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

async function queryFactorSample() {
  const error = document.getElementById("factor-error");
  const factor = document.getElementById("factor-filter").value;
  if (!factor) return;
  try {
    const base = factorParams();
    base.set("factor", factor);
    base.set("page", String(factorSamplePage));
    base.set("page_size", String(factorSamplePageSize()));
    base.set("max_rows", "5000");
    const [detailResponse, summaryResponse, sampleResponse] = await Promise.all([
      fetch(`/api/factor-data/factors/${encodeURIComponent(factor)}`),
      fetch(`/api/factor-data/summary?factor=${encodeURIComponent(factor)}&${base.toString()}`),
      fetch(`/api/factor-data/query?${base.toString()}`),
    ]);
    if (![detailResponse, summaryResponse, sampleResponse].every((response) => response.ok)) throw new Error("因子样本请求失败");
    renderFactorDetail(await detailResponse.json(), await summaryResponse.json(), await sampleResponse.json());
  } catch (errorValue) {
    error.textContent = `因子样本加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

function exportFactorSample() {
  const factor = document.getElementById("factor-filter").value;
  if (!factor) return;
  const params = factorParams();
  params.set("factor", factor);
  params.set("max_rows", "5000");
  window.location.assign(`/api/factor-data/export.csv?${params.toString()}`);
}
