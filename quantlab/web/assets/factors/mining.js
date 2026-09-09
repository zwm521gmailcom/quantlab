function miningBody() {
  const fields = [...document.querySelectorAll("input[name='mining-field']:checked")].map((cb) => cb.value);
  const operators = [...document.querySelectorAll("input[name='mining-operator']:checked")].map((cb) => cb.value);
  const windows = [...document.querySelectorAll("input[name='mining-window']:checked")].map((cb) => Number(cb.value)).filter((value) => Number.isFinite(value));
  return {config: {dataset_id: document.getElementById("mining-dataset").value.trim(), dataset_version_id: document.getElementById("mining-version").value.trim(), markets: selectedMarkets("mining-market"), date_from: miningDate("mining-from"), train_end: miningDate("mining-train-end"), validation_end: miningDate("mining-validation-end"), date_to: miningDate("mining-to"), fields, operators, windows, seed: Number(document.getElementById("mining-seed").value), max_candidates: Number(document.getElementById("mining-max-candidates").value), max_depth: Number(document.getElementById("mining-max-depth").value), min_validation_coverage: Number(document.getElementById("mining-min-coverage").value), min_validation_rank_ic: Number(document.getElementById("mining-min-ic").value)}};
}

const MINING_SKIP_FIELDS = new Set(["date", "instrument", "ts_code", "trade_date", "label", "future_return", "future_return_1pct", "future_return_99pct", "clipped_return", "binned_return"]);
const MINING_FIELD_LABELS = {
  hfq_open: "后复权开盘", hfq_high: "后复权最高", hfq_low: "后复权最低", hfq_close: "后复权收盘",
  hfq_up_limit: "后复权涨停价", hfq_down_limit: "后复权跌停价",
  open: "原始开盘", high: "原始最高", low: "原始最低", close: "原始收盘",
  vol: "成交量", amount: "成交额", adj_factor: "复权因子",
  pe_ttm: "市盈率", total_mv: "总市值", circ_mv: "流通市值", dv_ttm: "股息率",
  turnover_rate: "换手率", total_market_cap: "总市值", float_market_cap: "流通市值",
  dividend_yield_ratio: "股息率", turn: "换手率",
  up_limit: "涨停价", down_limit: "跌停价", is_suspended: "停牌", eligible: "可交易",
  st_status: "ST 状态", exchange: "交易所", list_date: "上市日", delist_date: "退市日",
};

function appendMiningPicker(parent, title, items) {
  const fieldset = document.createElement("fieldset");
  fieldset.className = "field-picker";
  const legend = document.createElement("legend");
  legend.textContent = title;
  fieldset.append(legend);
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "form-note";
    empty.textContent = "暂无";
    fieldset.append(empty);
  }
  items.forEach((item) => {
    const label = document.createElement("label");
    if (item.disabled) label.className = "disabled";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "mining-field";
    input.value = item.value;
    input.disabled = Boolean(item.disabled);
    label.append(input, document.createTextNode(` ${item.label}`));
    if (item.note) label.title = item.note;
    fieldset.append(label);
  });
  parent.append(fieldset);
}

async function loadMiningFieldChoices() {
  const box = document.getElementById("mining-field-groups");
  if (!box) return;
  const datasetId = document.getElementById("mining-dataset").value.trim();
  const versionId = document.getElementById("mining-version").value.trim();
  try {
    const [fieldRes, factorRes] = await Promise.all([
      fetch(`/api/factor-drafts/fields?dataset_id=${encodeURIComponent(datasetId)}&dataset_version_id=${encodeURIComponent(versionId)}`),
      fetch("/api/factors?page_size=100"),
    ]);
    const fieldPayload = await fieldRes.json();
    const factorPayload = await factorRes.json();
    if (!fieldRes.ok) throw new Error(fieldPayload.message || `HTTP ${fieldRes.status}`);
    if (!factorRes.ok) throw new Error(factorPayload.message || `HTTP ${factorRes.status}`);
    const tableFields = (fieldPayload.fields || []).filter((name) => !MINING_SKIP_FIELDS.has(name));
    const seenFactors = new Set();
    const libraryFactors = [];
    (factorPayload.items || []).forEach((item) => {
      const id = item.factor_entity_id || item.entity_id;
      if (!id || seenFactors.has(id) || MINING_SKIP_FIELDS.has(id)) return;
      seenFactors.add(id);
      libraryFactors.push({value: id, label: `${item.name || id}（${id}）`});
    });
    const leftoverTable = tableFields
      .filter((name) => !seenFactors.has(name))
      .map((name) => ({value: name, label: `${MINING_FIELD_LABELS[name] || name}（${name}）`}));
    box.replaceChildren();
    if (libraryFactors.length) appendMiningPicker(box, "因子库", libraryFactors);
    appendMiningPicker(box, "标准行情宽表（尚未登记为因子的列）", leftoverTable);
  } catch (errorValue) {
    box.replaceChildren();
    const state = document.createElement("div");
    state.className = "state error";
    state.textContent = `可选字段加载失败：${errorValue.message}`;
    box.append(state);
  }
}

function loadFactorMiningPage() {
  const form = document.getElementById("factor-mining-form");
  const error = document.getElementById("factor-mining-error");
  const result = document.getElementById("factor-mining-result");
  const submit = document.getElementById("mining-submit");
  bindMarketMultiSelect("mining-market");
  ["mining-op-level", "mining-op-rolling", "mining-op-relative", "mining-op-smooth", "mining-op-cross", "mining-windows"].forEach(bindMultiSelect);
  alignDatasetVersionInput("mining-dataset", "mining-version");
  loadMiningFieldChoices();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    result.classList.add("hidden");
    result.replaceChildren();
    const operators = [...document.querySelectorAll("input[name='mining-operator']:checked")];
    if (!operators.length) {
      error.textContent = "至少选择一种变换方式。";
      error.classList.remove("hidden");
      return;
    }
    if (![...document.querySelectorAll("input[name='mining-field']:checked")].length) {
      error.textContent = "至少选择一个参与拼公式的字段。";
      error.classList.remove("hidden");
      return;
    }
    if (![...document.querySelectorAll("input[name='mining-window']:checked")].length) {
      error.textContent = "至少选择一个回看天数。";
      error.classList.remove("hidden");
      return;
    }
    if (!selectedMarkets("mining-market").length) {
      error.textContent = "至少选择一个市场。";
      error.classList.remove("hidden");
      return;
    }
    result.textContent = "挖掘运行中…当前会同步跑完全部候选，完成后会跳到因子计算任务。";
    result.classList.remove("hidden");
    submit.disabled = true;
    setWorkflowStep("factor-mining-steps", "run");
    try {
      const response = await fetch("/api/factor-mining/runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(miningBody())});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || payload.detail || `HTTP ${response.status}`);
      setWorkflowStep("factor-mining-steps", "review");
      const runId = payload.research_run?.run_id;
      const jobHref = runId ? `/research/factor-jobs?run_id=${encodeURIComponent(runId)}` : "/research/factor-jobs";
      result.replaceChildren();
      result.append(document.createTextNode(`完成：留下 ${(payload.candidates || []).length} 条可勾选公式，淘汰 ${(payload.rejections || []).length} 条。去任务里勾选后才会进因子库。 `));
      const jobLink = document.createElement("a");
      jobLink.href = jobHref;
      jobLink.textContent = "打开因子计算任务";
      result.append(jobLink);
      result.classList.remove("hidden");
      window.location.href = jobHref;
    } catch (errorValue) {
      result.classList.add("hidden");
      error.textContent = `自动挖掘失败：${errorValue.message}`;
      error.classList.remove("hidden");
      setWorkflowStep("factor-mining-steps", "run");
    } finally {
      submit.disabled = false;
    }
  });
}
