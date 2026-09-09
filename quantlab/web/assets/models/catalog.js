function setModelPageMessage(id, message, isError) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
  node.classList.toggle("error", Boolean(isError && message));
}

let modelKindCatalog = {};
let kindFormDrafts = {};
let lastKind = "";

function snapshotKindForm(kind) {
  if (!kind) return;
  kindFormDrafts[kind] = {
    name: document.getElementById("model-name")?.value || "",
    hyperparameters: modelHyperparamsFromForm(kind),
  };
}

function applyKindForm(kind) {
  if (!kind) return;
  const spec = modelKindCatalog[kind] || {};
  const draft = kindFormDrafts[kind];
  const name = document.getElementById("model-name");
  if (name) name.value = (draft?.name || spec.name || "");
  applyKindDefaults({kind, hyperparameters: draft?.hyperparameters || spec.hyperparameters || {}});
  syncKindFields(kind);
  syncTrainLookbackField();
  syncModelCenterBacktestLink();
}

function modelCenterBacktestUrl(modelRef) {
  const kind = document.getElementById("model-kind")?.value || "lightgbm_tree";
  const query = new URLSearchParams();
  query.set("kind", kind);
  query.set("hp", JSON.stringify(modelHyperparamsFromForm(kind)));
  if (modelRef) query.set("model", modelRef);
  return `/backtests/new?${query.toString()}`;
}

function syncModelCenterBacktestLink(modelRef) {
  const link = document.getElementById("model-to-backtest");
  if (link) link.href = modelCenterBacktestUrl(modelRef);
}

function syncKindFields(kind, root = document) {
  const current = kind || "lightgbm_tree";
  root.querySelectorAll("[data-kinds]").forEach((node) => {
    const kinds = String(node.dataset.kinds || "").split(/\s+/).filter(Boolean);
    node.classList.toggle("hidden", !kinds.includes(current));
  });
}

function syncTrainLookbackField() {
  const walk = document.getElementById("model-walk-forward")?.value || "once";
  const input = document.getElementById("model-lookback-months");
  if (input) input.disabled = walk !== "lookback";
}

function trainProtocolFromForm() {
  const walk_forward = document.getElementById("model-walk-forward")?.value || "once";
  const params = {walk_forward};
  if (walk_forward === "lookback") {
    params.train_lookback_months = Number(document.getElementById("model-lookback-months")?.value || 12);
  }
  return params;
}

function walkForwardText(value) {
  if (value === "lookback" || value === "rolling" || value === "monthly") return "定长回看";
  return "一次训练";
}

function trainProtocolRows(params) {
  const walk = params.walk_forward || "once";
  const rows = [["训练方式", walkForwardText(walk), "walk_forward"]];
  if (walk === "lookback" || walk === "rolling" || walk === "monthly") {
    rows.push(["回看月数", params.train_lookback_months ?? params.train_period_months ?? 12, "train_lookback_months"]);
  }
  return rows;
}

function setInputValue(id, value) {
  const node = document.getElementById(id);
  if (node && value != null) node.value = value;
}

function applyKindDefaults(spec) {
  if (!spec) return;
  const kind = spec.kind || "";
  const params = spec.hyperparameters || {};
  if (kind && kind !== "factor_rank") {
    const walk = String(params.walk_forward || "once").toLowerCase();
    setInputValue("model-walk-forward", walk === "once" || walk === "" ? "once" : "lookback");
    setInputValue("model-lookback-months", params.train_lookback_months ?? params.train_period_months ?? 12);
  }
  if (kind === "lightgbm_tree") {
    setInputValue("model-trees", params.number_of_trees);
    setInputValue("model-bins", params.max_bins);
    setInputValue("model-leaves", params.num_leaves);
    setInputValue("model-min-samples", params.min_child_samples);
    setInputValue("model-learning-rate", params.learning_rate);
    setInputValue("model-label-gain", params.label_gain || "linear_0_19");
    setInputValue("model-ndcg-metric", params.metric || "ndcg");
    setInputValue("model-ndcg-eval-at", params.ndcg_eval_at ?? 10);
    setInputValue("model-ndcg-discount-base", params.ndcg_discount_base ?? 1);
    return;
  }
  if (kind === "xgboost_tree") {
    setInputValue("model-xgb-trees", params.number_of_trees);
    setInputValue("model-xgb-bins", params.max_bins);
    setInputValue("model-xgb-max-depth", params.max_depth);
    setInputValue("model-xgb-min-samples", params.min_child_samples);
    setInputValue("model-xgb-learning-rate", params.learning_rate);
    setInputValue("model-label-gain", params.label_gain || "linear_0_19");
    setInputValue("model-ndcg-metric", params.metric || "ndcg");
    setInputValue("model-ndcg-eval-at", params.ndcg_eval_at ?? 10);
    return;
  }
  if (kind === "random_forest") {
    setInputValue("model-forest-trees", params.number_of_trees);
    setInputValue("model-max-depth", params.max_depth);
    setInputValue("model-forest-min-samples", params.min_child_samples);
    return;
  }
  if (kind === "ridge_linear" || kind === "lasso" || kind === "elastic_net" || kind === "huber") {
    setInputValue("model-alpha", params.alpha);
  }
  if (kind === "elastic_net") setInputValue("model-l1-ratio", params.l1_ratio);
  if (kind === "huber") setInputValue("model-epsilon", params.epsilon);
}

function modelHyperparamsFromForm(kind) {
  const current = kind || document.getElementById("model-kind")?.value || "lightgbm_tree";
  const protocol = current === "factor_rank" ? {} : trainProtocolFromForm();
  if (current === "lightgbm_tree") {
    return {
      number_of_trees: Number(document.getElementById("model-trees")?.value || 5),
      max_bins: Number(document.getElementById("model-bins")?.value || 511),
      num_leaves: Number(document.getElementById("model-leaves")?.value || 30),
      min_child_samples: Number(document.getElementById("model-min-samples")?.value || 1000),
      learning_rate: Number(document.getElementById("model-learning-rate")?.value || 0.1),
      label_gain: document.getElementById("model-label-gain")?.value || "linear_0_19",
      metric: document.getElementById("model-ndcg-metric")?.value || "ndcg",
      ndcg_eval_at: Number(document.getElementById("model-ndcg-eval-at")?.value || 10),
      ndcg_discount_base: Number(document.getElementById("model-ndcg-discount-base")?.value || 1),
      ...protocol,
    };
  }
  if (current === "xgboost_tree") {
    return {
      number_of_trees: Number(document.getElementById("model-xgb-trees")?.value || 5),
      max_bins: Number(document.getElementById("model-xgb-bins")?.value || 256),
      max_depth: Number(document.getElementById("model-xgb-max-depth")?.value || 6),
      min_child_samples: Number(document.getElementById("model-xgb-min-samples")?.value || 1),
      learning_rate: Number(document.getElementById("model-xgb-learning-rate")?.value || 0.1),
      label_gain: document.getElementById("model-label-gain")?.value || "linear_0_19",
      metric: document.getElementById("model-ndcg-metric")?.value || "ndcg",
      ndcg_eval_at: Number(document.getElementById("model-ndcg-eval-at")?.value || 10),
      ...protocol,
    };
  }
  if (current === "random_forest") {
    return {
      number_of_trees: Number(document.getElementById("model-forest-trees")?.value || 20),
      max_depth: Number(document.getElementById("model-max-depth")?.value || 8),
      min_child_samples: Number(document.getElementById("model-forest-min-samples")?.value || 20),
      ...protocol,
    };
  }
  if (current === "ridge_linear" || current === "lasso") {
    return {alpha: Number(document.getElementById("model-alpha")?.value || (current === "lasso" ? 0.001 : 1)), ...protocol};
  }
  if (current === "elastic_net") {
    return {
      alpha: Number(document.getElementById("model-alpha")?.value || 0.001),
      l1_ratio: Number(document.getElementById("model-l1-ratio")?.value || 0.5),
      ...protocol,
    };
  }
  if (current === "huber") {
    return {
      alpha: Number(document.getElementById("model-alpha")?.value || 0.0001),
      epsilon: Number(document.getElementById("model-epsilon")?.value || 1.35),
      ...protocol,
    };
  }
  return protocol;
}

function labelGainText(value) {
  return value === "exponential" ? "指数默认" : "线性 0–19";
}

function ndcgMetricText(value) {
  return value === "none" ? "关闭" : "开启 NDCG";
}

function modelParamHelp(kind, key) {
  const shared = {
    number_of_trees: [
      "一共长多少棵树来投票打分。",
      "树多一点通常更稳，但也更慢，还更容易把训练里的噪音记住。现在默认很少，是为了先跑得快。",
    ],
    max_bins: [
      "连续的因子值会先被切成一格一格，再给树看。",
      "格子越多越细，训练更慢。LightGBM 最多 511 格，XGBoost 最多 256 格。",
    ],
    num_leaves: [
      "每棵树最多切成多少个小格子。",
      "格子越多，规则越细，也越容易把训练里的巧合记住。",
    ],
    min_child_samples: [
      "一片叶子里至少要摊上这么多条样本，少了就不让再往下切。",
      "数字越大，树越粗、越稳；数字太小，容易为了几只股票长出奇怪的分叉。",
    ],
    learning_rate: [
      "每加一棵树，只听它几成意见。",
      "数字小，学得慢但稳；数字大，学得快，也更容易走偏。",
    ],
    max_depth: [
      "一棵树最多连问几层「是不是比某个数大」。",
      "问得越深，规则越细，也越容易过拟合。",
    ],
    metric: [
      "训练时要不要额外看一眼：排在前面的股票，是不是真的更该排前面。",
      "关掉以后就不盯这个分数了，但排序训练还在做。",
    ],
    label_gain: [
      "告诉模型「排第一」比「排第十」重要多少。",
      "线性 0–19 是名次一档加一分，大家差得比较匀；指数默认会特别照顾最前面那几名。",
    ],
    ndcg_eval_at: [
      "算上面那个分数时，只看排名最靠前的这么多只。",
      "默认 10，跟回测里每次拿前 10 只对得上。",
    ],
    ndcg_discount_base: [
      "越往后的名次，分数打几折。这是 LightGBM 专用的。",
      "填 1 是常用默认；数字越大，越不那么盯着「必须拿第一」。",
    ],
    alpha: [
      "给系数加一根松紧带，不让模型把某一个因子看得特别重。",
      "数字越大越平滑，也可能学得不够。",
    ],
    l1_ratio: [
      "弹性网络里，有多少力气用来把没用的因子直接压成 0。",
      "越靠近 1 越像 LASSO，越靠近 0 越像 Ridge。",
    ],
    epsilon: [
      "某只股票收益离谱到什么程度，才不当普通误差、改用更迟钝的算法。",
      "数字越大，越能容忍那些极端股票。",
    ],
    none_ols: ["这种模型没有额外旋钮。回测时会用你选的全部因子直接做一次线性拟合。"],
    none_rank: ["不训练。回测时直接按你选的那个因子值给股票排队，因子大的排前面。"],
    walk_forward: [
      "回测时模型怎么用训练数据。这是模型的默认训练方式。",
      "一次训练：整个训练区间只拟合一次。定长回看：每个月只用最近 N 个月的数据重训。回测页选模型后会带出，改的是这一次，不写回模型中心。",
    ],
    train_lookback_months: [
      "定长回看时，每个月训练用最近多少个月的数据。",
      "默认 12 个月。只有选了定长回看才会用到这个数字。",
    ],
  };
  const byKind = {
    lightgbm_tree: {
      min_child_samples: [
        "每个叶子里至少要摊上这么多条股票日。",
        "设大一点，树就不会为了几只股票专门切一刀，模型更粗、也更稳。",
      ],
    },
    xgboost_tree: {
      min_child_samples: [
        "一个分叉里的样本太少就不让再切。",
        "用来防止树切得太碎，记住一些碰巧出现的情况。",
      ],
      max_bins: [
        "连续的因子值会先被切成一格一格，再给树看。",
        "格子越多越细，训练更慢。XGBoost 这边最多 256 格。",
      ],
    },
    random_forest: {
      number_of_trees: [
        "随机森林靠很多棵树一起投票。",
        "树太少会比较飘，树太多会更慢。默认 20 棵，比排序树的默认值多一些。",
      ],
      min_child_samples: [
        "每片叶子至少要有这么多条样本。",
        "太小会切得很细，训练看起来很好，换一段时间就不灵。",
      ],
    },
  };
  return (byKind[kind] && byKind[kind][key]) || shared[key] || [];
}

function bindModelParamHelpIcons(root = document) {
  root.querySelectorAll("[data-param]").forEach((field) => {
    if (field.dataset.helpBound) return;
    const key = field.dataset.param;
    const kinds = String(field.closest("[data-kinds]")?.dataset.kinds || "").split(/\s+/).filter(Boolean);
    const kind = kinds[0] || document.getElementById("model-kind")?.value || "";
    const lines = modelParamHelp(kind, key);
    if (!lines.length) return;
    const name = field.querySelector("label") || field;
    const title = name.textContent.trim() || key;
    name.append(infoDot(title, lines));
    field.dataset.helpBound = "1";
  });
}

function kindParamRows(kind, params) {
  const protocol = kind === "factor_rank" ? [] : trainProtocolRows(params);
  if (kind === "lightgbm_tree") {
    return [
      ["树数量", params.number_of_trees, "number_of_trees"],
      ["max_bins（分箱数）", params.max_bins, "max_bins"],
      ["叶节点", params.num_leaves, "num_leaves"],
      ["最小样本", params.min_child_samples, "min_child_samples"],
      ["学习率", params.learning_rate, "learning_rate"],
      ["NDCG 评估", ndcgMetricText(params.metric), "metric"],
      ["NDCG 增益", labelGainText(params.label_gain), "label_gain"],
      ["NDCG eval_at（评估只看前 N）", params.ndcg_eval_at ?? 10, "ndcg_eval_at"],
      ["NDCG discount_base（名次折扣）", params.ndcg_discount_base ?? 1, "ndcg_discount_base"],
      ...protocol,
    ];
  }
  if (kind === "xgboost_tree") {
    return [
      ["树数量", params.number_of_trees, "number_of_trees"],
      ["max_bins（分箱数）", params.max_bins, "max_bins"],
      ["最大深度", params.max_depth, "max_depth"],
      ["最小样本", params.min_child_samples, "min_child_samples"],
      ["学习率", params.learning_rate, "learning_rate"],
      ["NDCG 评估", ndcgMetricText(params.metric), "metric"],
      ["NDCG 增益", labelGainText(params.label_gain), "label_gain"],
      ["NDCG eval_at（评估只看前 N）", params.ndcg_eval_at ?? 10, "ndcg_eval_at"],
      ...protocol,
    ];
  }
  if (kind === "random_forest") {
    return [
      ["树数量", params.number_of_trees, "number_of_trees"],
      ["最大深度", params.max_depth, "max_depth"],
      ["最小样本", params.min_child_samples, "min_child_samples"],
      ...protocol,
    ];
  }
  if (kind === "ridge_linear" || kind === "lasso") {
    return [["正则强度 α", params.alpha, "alpha"], ...protocol];
  }
  if (kind === "elastic_net") {
    return [
      ["正则强度 α", params.alpha, "alpha"],
      ["L1 比例", params.l1_ratio, "l1_ratio"],
      ...protocol,
    ];
  }
  if (kind === "huber") {
    return [
      ["正则强度 α", params.alpha, "alpha"],
      ["异常阈值 ε", params.epsilon, "epsilon"],
      ...protocol,
    ];
  }
  if (kind === "ols") return protocol;
  if (kind === "factor_rank") return [["无可改参数", "不训练", "none_rank"]];
  return protocol;
}

async function fetchModelKindCatalog() {
  const payload = await fetch("/api/models/kinds").then((response) => {
    if (!response.ok) throw new Error("模型种类加载失败");
    return response.json();
  });
  const items = payload.items || [];
  modelKindCatalog = Object.fromEntries(items.map((item) => [item.kind, item]));
  return items;
}

async function loadModelKinds() {
  const select = document.getElementById("model-kind");
  if (!select) return;
  const items = await fetchModelKindCatalog();
  if (!items.length) return;
  const previous = select.value;
  select.replaceChildren();
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.kind;
    option.textContent = item.name;
    select.append(option);
  });
  if (previous && modelKindCatalog[previous]) select.value = previous;
  if (!select.dataset.bound) {
    select.addEventListener("change", () => {
      snapshotKindForm(lastKind);
      lastKind = select.value;
      applyKindForm(lastKind);
    });
    select.dataset.bound = "1";
  }
  lastKind = select.value;
  applyKindForm(select.value);
}

const MODEL_CARD_ORDER_KEY = "model-card-order";

function loadModelCardOrder() {
  try {
    const raw = JSON.parse(localStorage.getItem(MODEL_CARD_ORDER_KEY) || "[]");
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch (_error) {
    return [];
  }
}

function saveModelCardOrder(kinds) {
  localStorage.setItem(MODEL_CARD_ORDER_KEY, JSON.stringify(kinds));
}

function applyModelCardOrder(published) {
  const order = loadModelCardOrder();
  if (!order.length) return published;
  const rank = new Map(order.map((kind, index) => [kind, index]));
  return [...published].sort((a, b) => {
    const left = rank.has(a.kind) ? rank.get(a.kind) : Number.POSITIVE_INFINITY;
    const right = rank.has(b.kind) ? rank.get(b.kind) : Number.POSITIVE_INFINITY;
    return left - right;
  });
}

function bindModelCardDrag(list) {
  if (!list.dataset.dragBound) {
    list.addEventListener("dragover", (event) => event.preventDefault());
    list.addEventListener("drop", (event) => {
      event.preventDefault();
      const kinds = [...list.querySelectorAll(".model-card")].map((el) => el.dataset.kind).filter(Boolean);
      saveModelCardOrder(kinds);
    });
    list.dataset.dragBound = "1";
  }
  list.querySelectorAll(".model-card").forEach((card) => {
    card.draggable = true;
    card.addEventListener("dragstart", (event) => {
      card.classList.add("dragging");
      event.dataTransfer.setData("text/plain", card.dataset.kind || "");
      event.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (event) => {
      event.preventDefault();
      const dragging = list.querySelector(".model-card.dragging");
      if (!dragging || dragging === card) return;
      const rect = card.getBoundingClientRect();
      const after = event.clientX > rect.left + rect.width / 2;
      list.insertBefore(dragging, after ? card.nextSibling : card);
    });
    card.addEventListener("drop", (event) => {
      event.preventDefault();
      const kinds = [...list.querySelectorAll(".model-card")].map((el) => el.dataset.kind).filter(Boolean);
      saveModelCardOrder(kinds);
    });
  });
}

function renderModelList(items) {
  const list = document.getElementById("model-list");
  const empty = document.getElementById("empty-state");
  const loading = document.getElementById("loading-state");
  loading?.classList.add("hidden");
  if (!list) return;
  list.replaceChildren();
  const seen = new Set();
  const published = [];
  (items || []).forEach((item) => {
    const latest = (item.versions || []).filter((version) => version.status === "published").at(-1);
    if (!latest) return;
    const kind = latest.config?.kind || item.entity_id;
    if (seen.has(kind)) return;
    seen.add(kind);
    published.push({item, latest, kind});
  });
  empty?.classList.toggle("hidden", published.length !== 0);
  applyModelCardOrder(published).forEach(({item, latest, kind}) => {
    const card = document.createElement("article");
    card.className = "model-card";
    card.dataset.kind = kind;
    const title = document.createElement("strong");
    title.textContent = item.name;
    const params = latest?.config?.hyperparameters || {};
    const meta = document.createElement("dl");
    meta.className = "model-params";
    kindParamRows(kind, params).forEach(([label, value, helpKey]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      dt.append(label);
      const help = modelParamHelp(kind, helpKey || "");
      if (help.length) dt.append(infoDot(label, help));
      const dd = document.createElement("dd");
      dd.textContent = value == null || value === "" ? "—" : String(value);
      row.append(dt, dd);
      meta.append(row);
    });
    const link = document.createElement("a");
    const query = new URLSearchParams();
    query.set("kind", kind);
    query.set("model", `${item.entity_id}::${latest?.version_id || "v1"}`);
    query.set("hp", JSON.stringify(params));
    link.href = `/backtests/new?${query.toString()}`;
    link.textContent = "在回测中心调用";
    link.draggable = false;
    const versionLink = document.createElement("a");
    versionLink.href = `/models/${encodeURIComponent(item.entity_id)}/versions/${encodeURIComponent(latest?.version_id || "v1")}`;
    versionLink.textContent = "查看版本";
    versionLink.draggable = false;
    const actions = document.createElement("div");
    actions.className = "model-card-actions";
    actions.append(versionLink, link);
    card.append(title, meta, actions);
    list.append(card);
  });
  bindModelCardDrag(list);
}

async function refreshModelList() {
  const response = await fetch("/api/models");
  if (!response.ok) throw new Error("模型列表加载失败");
  const payload = await response.json();
  renderModelList(payload.items || []);
}

async function saveModelDesign() {
  const button = document.getElementById("model-save");
  setModelPageMessage("model-error", "");
  setModelPageMessage("model-result", "正在保存…");
  if (button) {
    button.disabled = true;
    button.textContent = "保存中…";
  }
  try {
    const response = await fetch("/api/models/design", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        kind: document.getElementById("model-kind")?.value || "lightgbm_tree",
        name: document.getElementById("model-name")?.value || "树模型（LightGBM）",
        hyperparameters: modelHyperparamsFromForm(document.getElementById("model-kind")?.value),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "保存失败");
    const kind = payload.design?.kind || document.getElementById("model-kind")?.value || "lightgbm_tree";
    const name = document.getElementById("model-name")?.value || payload.design?.name || "";
    const hyperparameters = payload.design?.hyperparameters || modelHyperparamsFromForm(kind);
    kindFormDrafts[kind] = {name, hyperparameters};
    modelKindCatalog[kind] = {...(modelKindCatalog[kind] || {}), kind, name, hyperparameters};
    setModelPageMessage("model-result", "已保存这种模型的默认参数。回测里只会看到这一份。");
    const link = document.createElement("a");
    const entity = payload.model?.entity_id;
    const version = payload.version?.version_id;
    const modelRef = entity && version ? `${entity}::${version}` : "";
    link.href = modelCenterBacktestUrl(modelRef);
    link.textContent = "打开回测中心";
    document.getElementById("model-result")?.append(" ", link);
    try {
      await fetchModelKindCatalog();
    } catch (_error) {
      /* 刚才保存的值已经写进表单和目录，刷新失败不影响这次保存。 */
    }
    await refreshModelList();
  } catch (errorValue) {
    setModelPageMessage("model-result", "");
    setModelPageMessage("model-error", `保存失败：${errorValue.message}`, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "保存默认参数";
    }
  }
}

async function loadModelsPage() {
  bindModelParamHelpIcons();
  try {
    await fetch("/api/models/catalog", {method: "POST"}).then(async (catalog) => {
      if (catalog.ok) return;
      const payload = await catalog.json().catch(() => ({}));
      const message = payload.message || payload.detail?.message || "";
      if (!message.includes("标准行情宽表")) throw new Error(message || "自动登记模型种类失败");
    });
    await loadModelKinds();
    await refreshModelList();
    syncModelCenterBacktestLink();
    const form = document.getElementById("model-kind")?.closest("section");
    if (form && !form.dataset.backtestLinkBound) {
      form.addEventListener("input", () => syncModelCenterBacktestLink());
      form.dataset.backtestLinkBound = "1";
    }
    const walkSelect = document.getElementById("model-walk-forward");
    if (walkSelect && !walkSelect.dataset.lookbackBound) {
      walkSelect.addEventListener("change", () => {
        syncTrainLookbackField();
        syncModelCenterBacktestLink();
      });
      walkSelect.dataset.lookbackBound = "1";
    }
    document.getElementById("model-save")?.addEventListener("click", saveModelDesign);
  } catch (errorValue) {
    document.getElementById("loading-state")?.classList.add("hidden");
    document.getElementById("error-state")?.classList.remove("hidden");
    setModelPageMessage("model-error", errorValue.message, true);
  }
}

function modelStatusText(status) {
  if (status === "published") return "已发布";
  if (status === "draft") return "草稿";
  if (status === "archived") return "已归档";
  if (status === "queued") return "排队中";
  if (status === "running") return "运行中";
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  return status || "—";
}

function formatTrainWindow(start, end) {
  if (!start && !end) return "—";
  return `${start || "—"} 至 ${end || "—"}`;
}

function collectTrainFolds(source) {
  const config = source?.config || {};
  const metrics = source?.metrics_raw || source?.metrics || {};
  const model = (metrics && metrics.model) || {};
  const nested = model.walk_forward && model.walk_forward.folds;
  const folds = metrics.folds || config.folds || nested || model.folds || [];
  if (Array.isArray(folds) && folds.length) return folds;
  const window = config.train_window || {};
  const train = config.train || {};
  const start = config.train_start || model.train_start || window.start || train.date_from;
  const end = config.train_end || model.train_end || window.end || train.date_to;
  const rows = config.train_rows ?? model.train_rows;
  const month = config.month || model.month;
  if (start || end || rows != null || month) {
    return [{
      month: month || "1",
      train_start: start,
      train_end: end,
      train_rows: rows,
      predict_from: config.predict_from || model.predict_from,
      predict_to: config.predict_to || model.predict_to,
    }];
  }
  return [];
}

function renderDefinitionList(parent, rows) {
  if (!parent) return;
  parent.replaceChildren();
  const meta = document.createElement("dl");
  meta.className = "model-params";
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value == null || value === "" ? "—" : String(value);
    row.append(dt, dd);
    meta.append(row);
  });
  parent.append(meta);
}

function renderTrainFoldsTable(target, folds) {
  if (!target) return;
  target.replaceChildren();
  target.classList.remove("muted");
  if (!folds.length) {
    target.classList.add("muted");
    target.textContent = "这次没有单独记下折次。训练发生在回测里时，折表会出现在对应回测运行记录。";
    return;
  }
  const table = document.createElement("table");
  table.className = "fold-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["哪一折", "训练区间", "训练行数", "预测区间"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.append(th);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  folds.forEach((fold, index) => {
    const tr = document.createElement("tr");
    [
      fold.month || fold.fold || String(index + 1),
      formatTrainWindow(fold.train_start, fold.train_end),
      fold.train_rows == null || fold.train_rows === "" ? "—" : String(fold.train_rows),
      formatTrainWindow(fold.predict_from, fold.predict_to),
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(head, body);
  target.append(table);
}
