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
  {key: "", label: "", sortable: false},
  {key: "name", label: "任务", type: "text"},
  {key: "factors", label: "因子", type: "text"},
  {key: "window", label: "训练 / 回测", type: "text"},
  {key: "model", label: "模型", type: "text"},
  {key: "status", label: "状态", type: "text"},
  {key: "return", label: "收益率", type: "number"},
  {key: "max_drawdown", label: "最大回撤", type: "number"},
  {key: "run", label: "运行", type: "text"},
];
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

function planLabel(plan) {
  const status = plan.closed ? "完结" : (STATUS[plan.status] || plan.status);
  return `${plan.name}（${status} · ${plan.items.length} 笔）`;
}

function syncButtons() {
  const running = current?.status === "running";
  const closed = Boolean(current?.closed);
  $("start").disabled = !current || running || closed || !(current.items || []).some((item) => item.selected && ["pending", "failed", "skipped"].includes(item.status));
  $("stop").disabled = !running;
  $("select-all").disabled = !current || running || closed || !(current.items || []).length;
  $("delete-items").disabled = !current || running || closed || !(current.items || []).some((item) => item.selected);
  $("close-plan").disabled = !current || running || closed;
  $("delete-plan").disabled = !current || running || (current.items || []).length > 0;
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
  if (!column || !key) return;
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

function metricCell(item, key) {
  const metric = item.metrics?.[key];
  const cell = document.createElement("td");
  const display = metric?.display || "—";
  cell.className = "archive-num";
  cell.textContent = display;
  if (display === "—" || metric?.value == null || metric?.value === "") {
    cell.classList.add("is-empty");
    return cell;
  }
  const number = Number(metric.value);
  if (!Number.isNaN(number) && number !== 0) cell.classList.add(number < 0 ? "is-neg" : "is-pos");
  return cell;
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

function renderTable() {
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
  const running = current.items.filter((item) => item.status === "running").length;
  const pending = current.items.filter((item) => item.status === "pending").length;
  const done = current.items.filter((item) => item.status === "completed").length;
  $("plan-state").textContent = `${current.name} · ${current.plan_id} · ${current.closed ? "完结" : (STATUS[current.status] || current.status)} · ${current.items.length} 笔，已完成 ${done}，待运行 ${pending}${running ? "，正在跑 1 笔" : ""}`;
  const table = document.createElement("table");
  table.className = "archive-grid plan-grid";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  PLAN_COLUMNS.forEach((column) => {
    const th = document.createElement("th");
    if (!column.key) {
      const input = document.createElement("input");
      input.id = "plan-select-all";
      input.type = "checkbox";
      input.setAttribute("aria-label", "全选任务");
      th.append(input);
    } else {
      th.className = "plan-sortable";
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
  const pageSize = QuantLabPager.readSize(PLAN_PAGE_KEY, QuantLabPager.DEFAULT_SIZES, 20);
  const items = sortedItems(current.items || []);
  const pages = QuantLabPager.pagesFor(items.length, pageSize);
  planPage = QuantLabPager.clampPage(planPage, pages);
  QuantLabPager.slice(items, planPage, pageSize).forEach((item) => {
    const row = document.createElement("tr");
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
    const name = document.createElement("td");
    name.innerHTML = `<div class="archive-run"><strong class="archive-name"></strong><span class="archive-meta"></span></div>`;
    name.querySelector(".archive-name").textContent = item.name;
    name.querySelector(".archive-meta").textContent = item.item_id;
    const factors = document.createElement("td");
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
    const windowCell = document.createElement("td");
    windowCell.className = "archive-window";
    windowCell.innerHTML = `<div></div><div></div>`;
    windowCell.children[0].textContent = item.summary?.train || "—";
    windowCell.children[1].textContent = item.summary?.test || "—";
    const model = document.createElement("td");
    model.className = "archive-window";
    model.innerHTML = `<div></div><div></div>`;
    model.children[0].textContent = item.summary?.model_name || item.summary?.kind || "—";
    model.children[1].textContent = item.summary?.walk_forward === "rolling" ? "定长回看" : "一次训练";
    const status = document.createElement("td");
    const mark = document.createElement("span");
    mark.className = `archive-status status-${item.status}`;
    mark.textContent = STATUS[item.status] || item.status;
    status.append(mark);
    if (item.error_message) {
      const err = document.createElement("div");
      err.className = "archive-meta";
      err.textContent = item.error_message;
      status.append(err);
    }
    if (
      ["failed", "skipped", "completed"].includes(item.status)
      && current.status !== "running"
      && !current.closed
    ) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn plan-retry";
      retry.textContent = "重算";
      retry.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        startPlan([item.item_id]);
      });
      status.append(retry);
    }
    const run = document.createElement("td");
    if (item.run_id) {
      const link = document.createElement("a");
      link.href = `/backtests/runs/${encodeURIComponent(item.run_id)}`;
      link.textContent = item.run_id;
      run.append(link);
    } else {
      run.textContent = "尚未运行";
    }
    row.append(check, name, factors, windowCell, model, status, metricCell(item, "return"), metricCell(item, "max_drawdown"), run);
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
    const items = current.items || [];
    const selectedCount = items.filter((item) => item.selected).length;
    headerCheck.disabled = current.status === "running" || Boolean(current.closed) || !items.length;
    headerCheck.checked = items.length > 0 && selectedCount === items.length;
    headerCheck.indeterminate = selectedCount > 0 && selectedCount < items.length;
    headerCheck.addEventListener("change", async () => {
      if (!current || !items.length) return;
      const flag = headerCheck.checked;
      const selected = {};
      items.forEach((item) => { selected[item.item_id] = flag; });
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
  const failed = current.items.filter((item) => item.status === "failed").length;
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
}

function render() {
  renderSelect();
  renderTable();
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
      current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}`);
      const index = plans.findIndex((plan) => plan.plan_id === current.plan_id);
      if (index >= 0) plans[index] = current;
      render();
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
    const index = plans.findIndex((plan) => plan.plan_id === current.plan_id);
    if (index >= 0) plans[index] = current;
    render();
    startPoll();
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
}

async function load() {
  try {
    const payload = await request("/api/backtest-plans");
    plans = payload.items || [];
    const wanted = new URLSearchParams(window.location.search).get("plan_id") || current?.plan_id;
    current = plans.find((plan) => plan.plan_id === wanted) || plans[0] || null;
    render();
    if (current?.status === "running") startPoll();
  } catch (error) {
    $("plan-state").textContent = error.message;
    setBanner(error.message, true);
  }
}

$("plan-select").addEventListener("change", () => {
  current = plans.find((plan) => plan.plan_id === $("plan-select").value) || null;
  planPage = 1;
  const url = new URL(window.location.href);
  if (current) url.searchParams.set("plan_id", current.plan_id);
  else url.searchParams.delete("plan_id");
  history.replaceState(history.state, "", `${url.pathname}${url.search}`);
  render();
  if (current?.status === "running") startPoll();
  else stopPoll();
});

$("select-all").addEventListener("click", async () => {
  if (!current) return;
  const selected = {};
  current.items.forEach((item) => { selected[item.item_id] = true; });
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
    const index = plans.findIndex((plan) => plan.plan_id === current.plan_id);
    if (index >= 0) plans[index] = current;
    render();
    setBanner(`已删除 ${itemIds.length} 笔任务。`);
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

$("start").addEventListener("click", async () => {
  if (!current) return;
  const itemIds = current.items.filter((item) => item.selected).map((item) => item.item_id);
  await startPlan(itemIds);
});

$("stop").addEventListener("click", async () => {
  if (!current) return;
  $("stop").disabled = true;
  try {
    current = await request(`/api/backtest-plans/${encodeURIComponent(current.plan_id)}/stop`, {method: "POST"});
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
    const index = plans.findIndex((plan) => plan.plan_id === current.plan_id);
    if (index >= 0) plans[index] = current;
    render();
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

$("delete-plan").addEventListener("click", async () => {
  if (!current || (current.items || []).length) return;
  const name = current.name;
  const planId = current.plan_id;
  if (!window.confirm(`确定删除「${name}」？只有没有任务的计划可以删除。`)) return;
  $("delete-plan").disabled = true;
  try {
    await request(`/api/backtest-plans/${encodeURIComponent(planId)}`, {method: "DELETE"});
    const url = new URL(window.location.href);
    url.searchParams.delete("plan_id");
    history.replaceState(history.state, "", `${url.pathname}${url.search}`);
    current = null;
    await load();
    setBanner(`已删除「${name}」。`);
  } catch (error) {
    setBanner(error.message, true);
    syncButtons();
  }
});

load();
