function cell(text, className) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text;
  return td;
}

const INDEX_NAME = {
  "000300.SH": "沪深300",
  "000905.SH": "中证500",
  "000906.SH": "中证800",
  "000852.SH": "中证1000",
  "932000.CSI": "中证2000",
  "000985.CSI": "中证全指",
};

const UNIVERSE_OPTIONS = [
  {label: "中证800", codes: ["000300.SH", "000905.SH"]},
  {label: "沪深300", codes: ["000300.SH"]},
  {label: "中证500", codes: ["000905.SH"]},
  {label: "中证800", codes: ["000906.SH"]},
  {label: "中证1000", codes: ["000852.SH"]},
  {label: "中证2000", codes: ["932000.CSI"]},
  {label: "中证全指", codes: ["000985.CSI"]},
];

function factorFields(config) {
  const fields = [];
  ((config && config.factor_versions) || []).forEach((item) => {
    if (!item || typeof item !== "object") return;
    const field = String(item.field || item.factor_id || "").replace(/^factor_/, "").trim();
    if (field) fields.push(field);
  });
  return fields;
}

function spanText(block) {
  const from = (block && block.date_from) || "";
  const to = (block && block.date_to) || "";
  const text = `${from} — ${to}`.replace(/^ — | — $/g, "").trim();
  return text || "—";
}

function walkLabel(config) {
  const hp = (config && config.hyperparameters) || {};
  const value = (config && config.walk_forward) || hp.walk_forward;
  if (value === "lookback" || value === "rolling" || value === "monthly") return "定长回看";
  return "一次训练";
}

function universeText(config) {
  const raw = config && config.universe_index_codes;
  if (Array.isArray(raw) && raw.length === 0) return "全A";
  const wanted = [];
  const seen = new Set();
  (Array.isArray(raw) ? raw : String(raw || "").split(/[,;，]/)).forEach((item) => {
    const code = String(item || "").trim().toUpperCase().replace(/-/g, ".");
    if (!code || seen.has(code)) return;
    seen.add(code);
    wanted.push(code);
  });
  if (!wanted.length) return "";
  const remaining = new Set(wanted);
  const labels = [];
  UNIVERSE_OPTIONS.forEach((option) => {
    if (option.codes.some((code) => !remaining.has(code))) return;
    labels.push(option.label);
    option.codes.forEach((code) => remaining.delete(code));
  });
  remaining.forEach((code) => labels.push(INDEX_NAME[code] || code));
  return labels.join(" + ");
}

function gateText(config) {
  const gates = Array.isArray(config && config.open_ma_gates) ? config.open_ma_gates : [];
  if (!gates.length) {
    return config && config.open_when_benchmark_gt_ma200 ? "沪深300 MA200" : "";
  }
  const grouped = [];
  gates.forEach((gate) => {
    const code = String((gate && gate.code) || "").trim();
    const last = grouped[grouped.length - 1];
    if (last && last.code === code) last.windows.push(gate && gate.window);
    else grouped.push({code, windows: [gate && gate.window]});
  });
  return grouped.map((item) => {
    const name = INDEX_NAME[item.code] || item.code || "基准";
    const windows = item.windows.filter((value) => value != null).map((value) => `MA${value}`).join("+");
    return windows ? `${name} ${windows}` : name;
  }).join(" · ");
}

function slipText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return `${Math.round(number * 10000)}bp`;
}

function metaLine(parent, text) {
  if (!text) return;
  const line = document.createElement("div");
  line.className = "archive-meta";
  line.textContent = text;
  parent.append(line);
}

function fillFactors(td, config) {
  td.className = "plan-col-factors";
  const list = document.createElement("div");
  list.className = "plan-factors";
  const fields = factorFields(config);
  if (!fields.length) list.textContent = "—";
  fields.forEach((field) => {
    const chip = document.createElement("span");
    chip.textContent = field;
    list.append(chip);
  });
  td.append(list);
}

function fillWindow(td, config) {
  td.className = "archive-window plan-col-window";
  const train = document.createElement("div");
  train.textContent = spanText(config.train);
  const test = document.createElement("div");
  test.className = "archive-meta";
  test.textContent = spanText(config.test);
  td.append(train, test);
  const bits = [];
  const lookback = config.train_lookback_months || config.train_period_months;
  if (walkLabel(config) === "定长回看" && lookback) bits.push(`回看 ${lookback} 月`);
  if (config.test_period_months) bits.push(`周期 ${config.test_period_months} 月`);
  const universe = universeText(config);
  if (universe) bits.push(universe);
  metaLine(td, bits.join(" · "));
}

function fillModel(td, config) {
  td.className = "archive-window plan-col-model";
  const model = config.model && typeof config.model === "object" ? config.model : {};
  const rule = config.kind === "rule_signal" || Boolean(config.rule_strategy_id);
  const name = document.createElement("div");
  name.textContent = rule
    ? (config.rule_strategy_id || "规则策略")
    : (model.name || model.entity_id || config.kind || "—");
  td.append(name);
  const hp = config.hyperparameters || {};
  const spec = [rule ? "不训练" : walkLabel(config)];
  if (!rule && hp.number_of_trees != null) spec.push(`${hp.number_of_trees} 棵`);
  if (!rule && hp.num_leaves != null) spec.push(`${hp.num_leaves} 叶`);
  metaLine(td, spec.join(" · "));
  const book = [];
  if (config.top_n != null) book.push(`Top${config.top_n}`);
  if (config.holding_days != null) book.push(`持有 ${config.holding_days}`);
  if (config.rebalance_every != null) book.push(`调仓 ${config.rebalance_every}`);
  if (config.weighting === "equal" || config.weighting === "equal_weight") book.push("等权");
  else if (config.weighting === "score") book.push("打分");
  else if (config.weighting) book.push(String(config.weighting));
  const slip = slipText(config.slippage);
  if (slip) book.push(`滑点 ${slip}`);
  metaLine(td, book.join(" · "));
  metaLine(td, gateText(config));
}

async function loadUsable() {
  const host = document.getElementById("usable-versions");
  if (!host) return;
  const rules = document.getElementById("usable-rules");
  const body = document.getElementById("usable-body");
  const catalog = await fetch("/assets/backtest/usable-versions.json?v=20261003usable2").then((r) => r.json());
  if (rules) {
    rules.replaceChildren();
    (catalog.rules || []).forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line;
      rules.append(li);
    });
  }
  if (!body) return;
  body.replaceChildren();
  const items = catalog.items || [];
  const runs = await Promise.all(items.map(async (item) => {
    try {
      const response = await fetch(`/api/backtests/runs/${encodeURIComponent(item.run_id)}`);
      if (!response.ok) return null;
      return await response.json();
    } catch (error) {
      return null;
    }
  }));
  const visible = items.flatMap((item, index) => (runs[index] ? [[item, runs[index]]] : []));
  if (!visible.length) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "没有可固定的回测。";
    row.append(td);
    body.append(row);
    return;
  }
  visible.forEach(([item, run]) => {
    const metrics = run.metrics || {};
    const config = run.configuration || {};
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.className = "plan-col-name";
    const link = document.createElement("a");
    link.href = `/backtests/runs/${encodeURIComponent(item.run_id)}`;
    link.textContent = item.title;
    const meta = document.createElement("div");
    meta.className = "archive-meta";
    meta.textContent = `${item.role} · ${item.run_id}`;
    const why = document.createElement("div");
    why.className = "plan-item-note";
    why.textContent = item.why;
    name.append(link, meta, why);
    const factors = document.createElement("td");
    fillFactors(factors, config);
    const windowCell = document.createElement("td");
    fillWindow(windowCell, config);
    const model = document.createElement("td");
    fillModel(model, config);
    row.append(
      name,
      factors,
      windowCell,
      model,
      cell(item.admitted || "—", "plan-col-admitted"),
      cell((metrics.return && metrics.return.display) || "—", "archive-num plan-col-return"),
      cell((metrics.max_drawdown && metrics.max_drawdown.display) || "—", "archive-num plan-col-drawdown"),
    );
    body.append(row);
  });
}

loadUsable();
