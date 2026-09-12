const $ = (id) => document.getElementById(id);
const ARCHIVE_PAGE_KEY = "quantlab-archive-page-size";
let page = 1;
let pageSize = QuantLabPager.readSize(ARCHIVE_PAGE_KEY, QuantLabPager.DEFAULT_SIZES, 10);
let sortKey = "created_at";
let sortDir = "desc";
const COLUMNS = [
  {key: "created_at", label: "回测", type: "date"},
  {key: "strategy", label: "模型 / 因子", type: "text"},
  {key: "date_from", label: "测试区间", type: "date"},
  {key: "return", label: "累计收益", type: "number"},
  {key: "annual_return", label: "年化", type: "number"},
  {key: "sharpe", label: "夏普", type: "number"},
  {key: "max_drawdown", label: "最大回撤", type: "number"},
  {key: "win_rate", label: "胜率", type: "number"},
  {key: "benchmark", label: "基准", type: "text"},
  {key: "", label: "操作"},
];

function params() {
  const query = new URLSearchParams({page, page_size: pageSize, sort: sortKey, order: sortDir});
  for (const [key, id] of [["q", "query"], ["status", "status"], ["strategy", "strategy"], ["date_from", "dateFrom"], ["date_to", "dateTo"]]) {
    if ($(id).value) query.set(key, $(id).value);
  }
  const exportQuery = new URLSearchParams(query);
  exportQuery.delete("page");
  exportQuery.delete("page_size");
  $("export").href = "/api/backtests/runs.csv?" + exportQuery.toString();
  return query;
}

function fmtTime(value) {
  const text = String(value || "");
  const match = text.match(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : (text || "—");
}

function metricCell(item, key) {
  const metric = item.metrics?.[key];
  const cell = document.createElement("td");
  const display = metric?.display || "未生成";
  cell.className = "archive-num";
  cell.textContent = display;
  if (display === "未生成" || metric?.value == null || metric?.value === "") {
    cell.classList.add("is-empty");
    return cell;
  }
  const number = Number(metric.value);
  if (!Number.isNaN(number) && number !== 0) cell.classList.add(number < 0 ? "is-neg" : "is-pos");
  return cell;
}

function resetArchiveDelete(button) {
  if (!button) return;
  button.dataset.confirming = "";
  button.textContent = "删除";
  button.disabled = false;
  button.parentElement?.querySelector("[data-role='delete-cancel']")?.remove();
}

async function deleteArchiveRun(item, button) {
  if (button.dataset.confirming !== "1") {
    document.querySelectorAll(".archive-actions button.danger").forEach(resetArchiveDelete);
    button.dataset.confirming = "1";
    button.textContent = "确认删除";
    $("archive-state").textContent = "同步后其他机器也会删除这条。";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.dataset.role = "delete-cancel";
    cancel.textContent = "取消";
    cancel.onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      resetArchiveDelete(button);
    };
    button.after(cancel);
    return;
  }
  button.disabled = true;
  $("archive-state").textContent = "正在删除 " + item.run_id + "…";
  const result = await fetch(item.delete_url || ("/api/backtests/runs/" + encodeURIComponent(item.run_id)), {method: "DELETE"});
  if (result.status === 204 || result.ok) {
    load();
    return;
  }
  const payload = await result.json().catch(() => ({}));
  resetArchiveDelete(button);
  $("archive-state").textContent = payload.message || "删除失败";
}

function textCell(text, className) {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = text;
  return cell;
}

function setSort(key) {
  const column = COLUMNS.find((item) => item.key === key);
  if (!column || !column.key) return;
  const first = column.type === "text" ? "asc" : "desc";
  const second = first === "desc" ? "asc" : "desc";
  if (sortKey !== key) {
    sortKey = key;
    sortDir = first;
  } else if (sortDir === first) {
    sortDir = second;
  } else {
    sortKey = "created_at";
    sortDir = "desc";
  }
  page = 1;
  load();
}

async function load() {
  $("archive-state").textContent = "正在加载…";
  const response = await fetch("/api/backtests/runs?" + params());
  const data = await response.json();
  if (!response.ok) {
    $("archive-state").textContent = data.message || "加载失败";
    return;
  }
  $("archive-root").textContent = data.results_root || "运行时结果目录";
  $("archive-state").textContent = data.total + " 条结果";

  const wrap = $("archive-table");
  wrap.replaceChildren();
  const table = document.createElement("table");
  table.className = "archive-grid";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  COLUMNS.forEach((column) => {
    const th = document.createElement("th");
    if (!column.key) {
      th.textContent = column.label;
      headRow.append(th);
      return;
    }
    th.classList.add("archive-sortable");
    th.dataset.sort = column.key;
    th.setAttribute("aria-sort", sortKey === column.key ? (sortDir === "asc" ? "ascending" : "descending") : "none");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = column.label;
    button.setAttribute("title", "按" + column.label + "排序");
    button.addEventListener("click", () => setSort(column.key));
    th.append(button);
    th.addEventListener("click", (event) => {
      if (event.target === button) return;
      setSort(column.key);
    });
    headRow.append(th);
  });
  head.append(headRow);
  table.append(head);
  const body = document.createElement("tbody");

  if (!data.items?.length) {
    const empty = document.createElement("tr");
    empty.className = "archive-empty-row";
    const cell = document.createElement("td");
    cell.colSpan = COLUMNS.length;
    cell.textContent = "还没有回测结果。到回测中心配置后点「开始回测」。";
    empty.append(cell);
    body.append(empty);
  }

  for (const item of data.items || []) {
    const row = document.createElement("tr");
    row.dataset.status = item.status || "";

    const run = document.createElement("td");
    const block = document.createElement("div");
    block.className = "archive-run";
    const top = document.createElement("div");
    top.className = "archive-run-top";
    const link = document.createElement("a");
    link.href = item.detail_url;
    link.textContent = item.run_id;
    const status = document.createElement("span");
    status.className = "archive-status status-" + (item.status || "");
    status.textContent = item.status_name || item.status || "未知";
    top.append(link, status);
    const name = document.createElement("div");
    name.className = "archive-name";
    name.textContent = item.name || "未命名回测";
    name.title = name.textContent;
    const meta = document.createElement("div");
    meta.className = "archive-meta";
    meta.textContent = "创建 " + fmtTime(item.created_at);
    block.append(top, name, meta);
    run.append(block);
    row.append(run);

    const strategy = document.createElement("td");
    const stack = document.createElement("div");
    stack.className = "archive-stack";
    const strategyName = document.createElement("div");
    strategyName.textContent = item.strategy?.name || "未登记模型";
    const factors = document.createElement("small");
    const factorText = (item.factors || []).filter(Boolean).join("、");
    factors.textContent = factorText || "未选因子";
    factors.title = factors.textContent;
    stack.append(strategyName, factors);
    strategy.append(stack);
    row.append(strategy);

    const windowCell = document.createElement("td");
    windowCell.className = "archive-window";
    const fromLine = document.createElement("div");
    fromLine.textContent = item.test_window?.date_from || "—";
    const toLine = document.createElement("div");
    toLine.textContent = item.test_window?.date_to || "—";
    windowCell.append(fromLine, toLine);
    row.append(windowCell);
    for (const key of ["return", "annual_return", "sharpe", "max_drawdown", "win_rate"]) {
      row.append(metricCell(item, key));
    }
    row.append(textCell(item.benchmark || "—", "archive-bench"));

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "archive-actions";
    if (item.status === "failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重新回测";
      retry.onclick = async () => {
        retry.disabled = true;
        retry.textContent = "正在回测…";
        $("archive-state").textContent = "正在重新回测 " + item.run_id + "…";
        const result = await fetch(item.execute_url || ("/api/backtests/" + encodeURIComponent(item.run_id) + "/execute"), {method: "POST"});
        const payload = await result.json().catch(() => ({}));
        if (result.ok) location.href = item.detail_url;
        else {
          retry.disabled = false;
          retry.textContent = "重新回测";
          $("archive-state").textContent = payload.message || "重新回测失败";
        }
      };
      actions.append(retry);
    }
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "复制配置";
    copy.onclick = async () => {
      const result = await fetch(item.copy_url, {method: "POST"});
      const payload = await result.json();
      if (result.ok) location.href = payload.redirect_url;
      else $("archive-state").textContent = payload.message || "复制失败";
    };
    actions.append(copy);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "删除";
    remove.onclick = () => deleteArchiveRun(item, remove);
    actions.append(remove);
    actionsCell.append(actions);
    row.append(actionsCell);
    body.append(row);
  }

  table.append(body);
  wrap.append(table);
  const pages = QuantLabPager.pagesFor(data.total || 0, pageSize);
  page = QuantLabPager.clampPage(data.page || page, pages);
  if (page !== data.page && (data.total || 0) > 0) {
    load();
    return;
  }
  QuantLabPager.mount($("archive-pagination"), {
    page,
    pages,
    pageSize,
    total: data.total || 0,
    storageKey: ARCHIVE_PAGE_KEY,
    onPage: (next) => { page = next; load(); },
    onPageSize: (size) => { pageSize = size; page = 1; load(); },
  });
}

$("refresh").onclick = () => { page = 1; load(); };
load();
