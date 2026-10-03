const $ = (id) => document.getElementById(id);
const STATUS = {
  draft: "未开始",
  running: "运行中",
  completed: "已完成",
  stopped: "已停止",
  closed: "完结",
  pending: "待运行",
  queued: "排队",
  failed: "失败",
  skipped: "已跳过",
};
const PLAN_COLUMNS = [
  {key: "", label: "", col: "check", sortable: false},
  {key: "name", label: "任务", type: "text", col: "name"},
  {key: "factors", label: "因子", type: "text", col: "factors"},
  {key: "retry", label: "重算", col: "retry", sortable: false},
  {key: "window", label: "训练 / 回测", type: "text", col: "window"},
  {key: "model", label: "模型", type: "text", col: "model"},
  {key: "status", label: "状态", type: "text", col: "status"},
  {key: "return", label: "收益率", type: "number", col: "return"},
  {key: "max_drawdown", label: "最大回撤", type: "number", col: "drawdown"},
  {key: "run", label: "运行", type: "text", col: "run"},
];

function itemNote(item) {
  return String(item.config?.note || item.summary?.note || "").trim();
}
const PLAN_PAGE_KEY = "quantlab-plan-page-size";
let plans = [];
let current = null;
let pollTimer = 0;
let sortKey = "";
let sortDir = "desc";
let planPage = 1;

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || payload.detail?.message || `HTTP ${response.status}`);
  return payload;
}

function setBanner(text, error = false) {
  $("banner-text").textContent = text;
  $("banner").classList.toggle("banner-error", error);
}

function planCount(plan) {
  if (Array.isArray(plan?.items)) return plan.items.length;
  return Number(plan?.item_count) || 0;
}

function planLabel(plan) {
  const status = plan.closed ? "完结" : (STATUS[plan.status] || plan.status);
  return `${plan.name}（${status} · ${planCount(plan)} 笔）`;
}

function rememberPlan(plan) {
  if (!plan?.plan_id) return;
  const summary = {
    plan_id: plan.plan_id,
    name: plan.name,
    status: plan.status,
    closed: plan.closed,
    created_at: plan.created_at,
    updated_at: plan.updated_at,
    item_count: planCount(plan),
  };
  const index = plans.findIndex((item) => item.plan_id === plan.plan_id);
  if (index >= 0) plans[index] = {...plans[index], ...summary};
  else plans.unshift(summary);
}

function syncButtons() {
  const running = current?.status === "running";
  const closed = Boolean(current?.closed);
  $("start").disabled = !current || running || closed || !(current.items || []).some((item) => item.selected && ["pending", "failed", "skipped"].includes(item.status));
  $("stop").disabled = !running;
  $("select-all").disabled = !current || running || closed || !(current.items || []).length;
  $("delete-items").disabled = !current || running || closed || !(current.items || []).some((item) => item.selected);
  $("close-plan").disabled = !current || running || closed;
  $("delete-plan").disabled = !current || running;
}

function metricValue(item, key) {
  const value = item.metrics?.[key]?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sortValue(item, key) {
  if (key === "name") return item.name || "";
  if (key === "factors") return (item.summary?.factors || []).join(" ");
  if (key === "window") return `${item.summary?.train || ""} ${item.summary?.test || ""}`;
  if (key === "model") return `${item.summary?.model_name || item.summary?.kind || ""} ${item.summary?.walk_forward || ""}`;
  if (key === "status") return item.status || "";
  if (key === "return" || key === "max_drawdown") return metricValue(item, key);
  if (key === "run") return item.run_id || "";
  return "";
}

function sortedItems(items) {
  if (!sortKey) return items.slice();
  const dir = sortDir === "asc" ? 1 : -1;
  return items.slice().sort((left, right) => {
    const leftValue = sortValue(left, sortKey);
    const rightValue = sortValue(right, sortKey);
    const leftEmpty = leftValue == null || leftValue === "";
    const rightEmpty = rightValue == null || rightValue === "";
    if (leftEmpty && rightEmpty) return 0;
    if (leftEmpty) return 1;
    if (rightEmpty) return -1;
    if (typeof leftValue === "number" && typeof rightValue === "number") return (leftValue - rightValue) * dir;
    return String(leftValue).localeCompare(String(rightValue), "zh") * dir;
  });
}

function setSort(key) {
  const column = PLAN_COLUMNS.find((item) => item.key === key);
  if (!column || !key || column.sortable === false) return;
  const numeric = column.type === "number";
  if (sortKey !== key) {
    sortKey = key;
    sortDir = numeric ? "desc" : "asc";
  } else if ((numeric && sortDir === "desc") || (!numeric && sortDir === "asc")) {
    sortDir = sortDir === "asc" ? "desc" : "asc";
  } else {
    sortKey = "";
  }
  renderTable();
}

function fillMetricCell(cell, item, key, col) {
  const metric = item.metrics?.[key];
  const display = metric?.display || "—";
  cell.className = `archive-num plan-col-${col}`;
  cell.classList.remove("is-empty", "is-neg", "is-pos");
  cell.textContent = display;
  if (display === "—" || metric?.value == null || metric?.value === "") {
    cell.classList.add("is-empty");
    return;
  }
  const number = Number(metric.value);
  if (!Number.isNaN(number) && number !== 0) cell.classList.add(number < 0 ? "is-neg" : "is-pos");
}

function metricCell(item, key, col) {
  const cell = document.createElement("td");
  fillMetricCell(cell, item, key, col);
  return cell;
}

function fillStatusCell(cell, item) {
  cell.className = "plan-col-status";
  cell.replaceChildren();
  const mark = document.createElement("span");
  mark.className = `archive-status status-${item.status}`;
  mark.textContent = STATUS[item.status] || item.status;
  cell.append(mark);
  if (item.error_message) {
    const err = document.createElement("div");
    err.className = "archive-meta";
    err.textContent = item.error_message;
    cell.append(err);
  }
}

function fillRunCell(cell, item) {
  cell.className = "plan-col-run";
  cell.replaceChildren();
  if (item.run_id) {
    const link = document.createElement("a");
    link.href = `/backtests/runs/${encodeURIComponent(item.run_id)}`;
    link.textContent = item.run_id;
    cell.append(link);
  } else {
    cell.textContent = "尚未运行";
  }
}

function fillRetryCell(cell, item) {
  cell.className = "plan-col-retry";
  cell.replaceChildren();
  if (
    ["failed", "skipped", "completed"].includes(item.status)
    && current.status !== "running"
    && !current.closed
  ) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "plan-retry";
    retry.textContent = "重算";
    retry.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      startPlan([item.item_id]);
    });
    cell.append(retry);
  } else {
    cell.textContent = "—";
  }
}

function pageView() {
  const pageSize = QuantLabPager.readSize(PLAN_PAGE_KEY, QuantLabPager.DEFAULT_SIZES, 20);
  const items = sortedItems(current?.items || []);
  const pages = QuantLabPager.pagesFor(items.length, pageSize);
  planPage = QuantLabPager.clampPage(planPage, pages);
  return {
    pageSize,
    items,
    pages,
    pageItems: QuantLabPager.slice(items, planPage, pageSize),
  };
}

function updatePlanChrome() {
  if (!current) {
    $("plan-state").textContent = "还没有回测计划。";
    syncButtons();
    return;
  }
  const planItems = current.items || [];
  const running = planItems.filter((item) => item.status === "running").length;
  const queued = planItems.filter((item) => item.status === "queued").length;
  const pending = planItems.filter((item) => item.status === "pending").length;
  const done = planItems.filter((item) => item.status === "completed").length;
  const failed = planItems.filter((item) => item.status === "failed").length;
  const admission = current.admission;
  const admissionNote = admission
    ? `，读数据 ${admission.loading}${admission.load_limit == null ? "" : `/${admission.load_limit}`} 笔，上限 ${admission.limit} 笔`
    : "";
  $("plan-state").textContent = `${current.name} · ${current.plan_id} · ${current.closed ? "完结" : (STATUS[current.status] || current.status)} · ${planItems.length} 笔，已完成 ${done}，待运行 ${pending}${queued ? `，排队 ${queued}` : ""}${running ? `，正在跑 ${running} 笔` : ""}${admissionNote}`;
  setBanner(
    current.closed
      ? "这份计划已完结。不能再加入任务或开始，回测中心下拉框也不会再列出它。"
      : current.status === "running"
      ? "正在按勾选顺序串行运行。可以离开这个页面，运算仍会继续。"
      : failed
        ? `这份计划有 ${failed} 笔失败。可点该行的「重算」，不必全选重跑。`
      : current.status === "completed"
        ? "这份计划已跑完。可点该行的「重算」只重跑一笔，不必全选。"
        : "勾选要跑的任务，点全选再点开始或删除任务。不会并行开多笔回测。",
    false
  );
  syncButtons();
  const headerCheck = $("plan-select-all");
  if (headerCheck) {
    const selectedCount = planItems.filter((item) => item.selected).length;
    headerCheck.disabled = current.status === "running" || Boolean(current.closed) || !planItems.length;
    headerCheck.checked = planItems.length > 0 && selectedCount === planItems.length;
    headerCheck.indeterminate = selectedCount > 0 && selectedCount < planItems.length;
  }
}

function updateSelectLabels() {
  const select = $("plan-select");
  if (!select) return;
  plans.forEach((plan) => {
    const option = [...select.options].find((item) => item.value === plan.plan_id);
    if (option) option.textContent = planLabel(plan);
  });
  if (current) select.value = current.plan_id;
}

function patchTable() {
  if (!current) return false;
  const wrap = $("plan-table");
  const table = wrap?.querySelector("table.plan-grid");
  const tbody = table?.querySelector("tbody");
  if (!tbody) return false;
  const {pageItems} = pageView();
  const rows = [...tbody.querySelectorAll("tr")];
  if (rows.length !== pageItems.length) return false;
  for (let index = 0; index < pageItems.length; index += 1) {
    if (rows[index].dataset.itemId !== pageItems[index].item_id) return false;
  }
  const locked = current.status === "running" || Boolean(current.closed);
  pageItems.forEach((item, index) => {
    const row = rows[index];
    const check = row.querySelector('input[type="checkbox"]');
    if (check) {
      check.checked = Boolean(item.selected);
      check.disabled = locked;
    }
    fillRetryCell(row.querySelector(".plan-col-retry"), item);
    fillStatusCell(row.querySelector(".plan-col-status"), item);
    fillMetricCell(row.querySelector(".plan-col-return"), item, "return", "return");
    fillMetricCell(row.querySelector(".plan-col-drawdown"), item, "max_drawdown", "drawdown");
    fillRunCell(row.querySelector(".plan-col-run"), item);
  });
  updatePlanChrome();
  return true;
}

function renderSelect() {
  const select = $("plan-select");
  select.replaceChildren();
  if (!plans.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无计划";
    select.append(option);
    return;
  }
  plans.forEach((plan) => {
    const option = document.createElement("option");
    option.value = plan.plan_id;
    option.textContent = planLabel(plan);
    select.append(option);
  });
  if (current) select.value = current.plan_id;
}

function renderTable({soft = false} = {}) {
  if (soft && patchTable()) return;
  const wrap = $("plan-table");
  const pagerHost = $("plan-pagination");
  wrap.replaceChildren();
  if (!current) {
    $("plan-state").textContent = "还没有回测计划。";
    if (pagerHost) {
      pagerHost.replaceChildren();
      pagerHost.hidden = true;
    }
    syncButtons();
    return;
  }
  if (pagerHost) pagerHost.hidden = false;
  updatePlanChrome();
  const table = document.createElement("table");
  table.className = "archive-grid plan-grid";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  PLAN_COLUMNS.forEach((column) => {
    const th = document.createElement("th");
    th.className = `plan-col-${column.col}`;
    if (column.sortable === false) {
      if (!column.key) {
        const input = document.createElement("input");
        input.id = "plan-select-all";
        input.type = "checkbox";
        input.setAttribute("aria-label", "全选任务");
        th.append(input);
      } else {
        th.textContent = column.label;
      }
    } else {
      th.classList.add("plan-sortable");
      th.dataset.sort = column.key;
      th.setAttribute("aria-sort", sortKey === column.key ? (sortDir === "asc" ? "ascending" : "descending") : "none");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = column.label;
      button.addEventListener("click", () => setSort(column.key));
      th.append(button);
      th.addEventListener("click", (event) => {
        if (event.target === button) return;
        setSort(column.key);
      });
    }
    headRow.append(th);
  });
  head.append(headRow);
  table.append(head);
  const body = document.createElement("tbody");
  const {pageSize, items, pages, pageItems} = pageView();
  pageItems.forEach((item) => {
    const row = document.createElement("tr");
    row.dataset.itemId = item.item_id;
    const check = document.createElement("td");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(item.selected);
    input.disabled = current.status === "running" || Boolean(current.closed);
    input.setAttribute("aria-label", `选择 ${item.name}`);
    input.addEventListener("change", async () => {
      try {
        current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/items`, {
          method: "PATCH",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({selected: {[item.item_id]: input.checked}}),
        });
        render();
      } catch (error) {
        setBanner(error.message, true);
      }
    });
    check.append(input);
    check.className = "plan-col-check";
    const name = document.createElement("td");
    name.className = "plan-col-name";
    name.innerHTML = `<div class="archive-run"><strong class="archive-name"></strong><div class="archive-meta"></div><div class="plan-item-note"></div></div>`;
    name.querySelector(".archive-name").textContent = item.name;
    name.querySelector(".archive-meta").textContent = item.item_id;
    const noteText = itemNote(item);
    const noteEl = name.querySelector(".plan-item-note");
    if (noteText) {
      noteEl.textContent = noteText;
      noteEl.title = noteText;
    } else {
      noteEl.remove();
    }
    const factors = document.createElement("td");
    factors.className = "plan-col-factors";
    const factorList = document.createElement("div");
    factorList.className = "plan-factors";
    const fields = item.summary?.factors || [];
    if (!fields.length) {
      factorList.textContent = "—";
    } else {
      fields.forEach((field) => {
        const chip = document.createElement("span");
        chip.textContent = field;
        factorList.append(chip);
      });
    }
    factors.append(factorList);
    const retryCell = document.createElement("td");
    fillRetryCell(retryCell, item);
    const windowCell = document.createElement("td");
    windowCell.className = "archive-window plan-col-window";
    windowCell.innerHTML = `<div></div><div class="archive-meta"></div>`;
    windowCell.children[0].textContent = item.summary?.train || "—";
    windowCell.children[1].textContent = item.summary?.test || "—";
    const model = document.createElement("td");
    model.className = "archive-window plan-col-model";
    model.innerHTML = `<div></div><div class="archive-meta"></div>`;
    model.children[0].textContent = item.summary?.model_name || item.summary?.kind || "—";
    model.children[1].textContent = item.summary?.walk_forward === "rolling" ? "定长回看" : "一次训练";
    const status = document.createElement("td");
    fillStatusCell(status, item);
    const run = document.createElement("td");
    fillRunCell(run, item);
    row.append(
      check,
      name,
      factors,
      retryCell,
      windowCell,
      model,
      status,
      metricCell(item, "return", "return"),
      metricCell(item, "max_drawdown", "drawdown"),
      run,
    );
    body.append(row);
  });
  table.append(body);
  wrap.append(table);
  QuantLabPager.mount(pagerHost, {
    page: planPage,
    pages,
    pageSize,
    total: items.length,
    storageKey: PLAN_PAGE_KEY,
    onPage: (next) => { planPage = next; renderTable(); },
    onPageSize: () => { planPage = 1; renderTable(); },
  });
  const headerCheck = $("plan-select-all");
  if (headerCheck) {
    const planItems = current.items || [];
    const selectedCount = planItems.filter((item) => item.selected).length;
    headerCheck.disabled = current.status === "running" || Boolean(current.closed) || !planItems.length;
    headerCheck.checked = planItems.length > 0 && selectedCount === planItems.length;
    headerCheck.indeterminate = selectedCount > 0 && selectedCount < planItems.length;
    headerCheck.addEventListener("change", async () => {
      if (!current || !planItems.length) return;
      const flag = headerCheck.checked;
      const selected = {};
      planItems.forEach((item) => { selected[item.item_id] = flag; });
      try {
        current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/items`, {
          method: "PATCH",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({selected}),
        });
        render();
        setBanner(flag ? "已全选。可点开始运行，或点删除任务去掉这些条目。" : "已取消全选。");
      } catch (error) {
        setBanner(error.message, true);
      }
    });
  }
}

function render({soft = false} = {}) {
  if (soft) updateSelectLabels();
  else renderSelect();
  renderTable({soft});
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = 0;
  }
}

function startPoll() {
  stopPoll();
  pollTimer = window.setInterval(async () => {
    if (!current) return;
    try {
      await loadPlan(current.plan_id);
      render({soft: true});
      if (current.status !== "running") stopPoll();
    } catch (error) {
      setBanner(error.message, true);
    }
  }, 2000);
}

async function startPlan(itemIds) {
  if (!current) return;
  $("start").disabled = true;
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/start`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({item_ids: itemIds}),
    });
    rememberPlan(current);
    render();
    startPoll();
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
}

let loadSeq = 0;

async function loadPlan(planId) {
  const seq = ++loadSeq;
  if (!planId) {
    current = null;
    return null;
  }
  const payload = await request(`/api/backtest-plans/${encodeURIComponent(planId)}`);
  if (seq !== loadSeq) return current;
  current = payload;
  rememberPlan(current);
  return current;
}

async function load() {
  try {
    const payload = await request("/api/backtest-plans");
    plans = payload.items || [];
    const wanted = new URLSearchParams(window.location.search).get("plan_id") || current?.plan_id;
    const summary = plans.find((plan) => plan.plan_id === wanted) || plans[0] || null;
    await loadPlan(summary?.plan_id || "");
    render();
    if (current?.status === "running") startPoll();
  } catch (error) {
    $("plan-state").textContent = error.message;
    setBanner(error.message, true);
  }
}

$("plan-select").addEventListener("change", async () => {
  const planId = $("plan-select").value;
  planPage = 1;
  const url = new URL(window.location.href);
  if (planId) url.searchParams.set("plan_id", planId);
  else url.searchParams.delete("plan_id");
  history.replaceState(history.state, "", `${url.pathname}${url.search}`);
  $("plan-state").textContent = "正在加载…";
  try {
    await loadPlan(planId);
    render();
    if (current?.status === "running") startPoll();
    else stopPoll();
  } catch (error) {
    setBanner(error.message, true);
  }
});

$("select-all").addEventListener("click", async () => {
  if (!current) return;
  const selected = {};
  (current.items || []).forEach((item) => { selected[item.item_id] = true; });
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/items`, {
      method: "PATCH",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({selected}),
    });
    render();
    setBanner("已全选。可点开始运行，或点删除任务去掉这些条目。");
  } catch (error) {
    setBanner(error.message, true);
  }
});

$("delete-items").addEventListener("click", async () => {
  if (!current) return;
  const itemIds = (current.items || []).filter((item) => item.selected).map((item) => item.item_id);
  if (!itemIds.length) return;
  if (!window.confirm(`确定删除已选的 ${itemIds.length} 笔任务？此操作不能恢复。`)) return;
  $("delete-items").disabled = true;
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/items/delete`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({item_ids: itemIds}),
    });
    rememberPlan(current);
    render();
    setBanner(`已删除 ${itemIds.length} 笔任务。`);
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

$("start").addEventListener("click", async () => {
  if (!current) return;
  const itemIds = (current.items || []).filter((item) => item.selected).map((item) => item.item_id);
  await startPlan(itemIds);
});

$("stop").addEventListener("click", async () => {
  if (!current) return;
  $("stop").disabled = true;
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/stop`, {method: "POST"});
    rememberPlan(current);
    render();
    startPoll();
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

function openPlanDialog() {
  $("plan-name").value = "";
  $("plan-dialog").classList.remove("hidden");
  $("plan-name").focus();
}

function closePlanDialog() {
  $("plan-dialog").classList.add("hidden");
}

$("new-plan").addEventListener("click", openPlanDialog);
$("plan-dialog-cancel").addEventListener("click", closePlanDialog);
$("plan-dialog").addEventListener("click", (event) => {
  if (event.target === $("plan-dialog")) closePlanDialog();
});
$("plan-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    $("plan-dialog-save").click();
  }
  if (event.key === "Escape") closePlanDialog();
});
$("plan-dialog-save").addEventListener("click", async () => {
  const name = $("plan-name").value.trim();
  if (!name) {
    setBanner("请填写计划名称。", true);
    return;
  }
  $("plan-dialog-save").disabled = true;
  try {
    current = await request("/api/backtest-plans", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({name}),
    });
    closePlanDialog();
    const url = new URL(window.location.href);
    url.searchParams.set("plan_id", current.plan_id);
    history.replaceState(history.state, "", `${url.pathname}${url.search}`);
    await load();
    setBanner(`已新建「${current.name}」，计划 ID：${current.plan_id}。`);
  } catch (error) {
    setBanner(error.message, true);
  } finally {
    $("plan-dialog-save").disabled = false;
  }
});

$("close-plan").addEventListener("click", async () => {
  if (!current || current.closed) return;
  $("close-plan").disabled = true;
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/close`, {method: "POST"});
    rememberPlan(current);
    render();
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

$("delete-plan").addEventListener("click", async () => {
  if (!current || current.status === "running") return;
  const name = current.name;
  const planId = current.plan_id;
  const taskCount = (current.items || []).length;
  const warning = taskCount
    ? `确定删除「${name}」？其中 ${taskCount} 个任务，以及已经跑出的回测和产物都会一起删除。`
    : `确定删除「${name}」？`;
  if (!window.confirm(warning)) return;
  $("delete-plan").disabled = true;
  try {
    const result = await request(`/api/backtest-plans/${encodeURIComponent(planId)}`, {method: "DELETE"});
    const removed = Array.isArray(result.deleted_run_ids) ? result.deleted_run_ids.length : 0;
    const url = new URL(window.location.href);
    url.searchParams.delete("plan_id");
    history.replaceState(history.state, "", `${url.pathname}${url.search}`);
    current = null;
    await load();
    setBanner(removed ? `已删除「${name}」，并清掉 ${removed} 笔回测和产物。` : `已删除「${name}」。`);
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

load();
