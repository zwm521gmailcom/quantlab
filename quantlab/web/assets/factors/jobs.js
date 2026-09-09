function researchText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function jobStatusLabel(status) {
  return {completed: "已完成", failed: "失败", queued: "排队中", running: "运行中"}[status] || status || "未知";
}

function jobListDate(value) {
  const text = String(value || "").trim();
  const compact = text.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`;
  return text || "—";
}

function jobListTime(value) {
  const text = String(value || "");
  const match = text.match(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : (text || "—");
}

function jobListWindows(windows) {
  if (!Array.isArray(windows) || !windows.length) return "—";
  return windows.join("、");
}

function jobListCell(text, className) {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = text;
  return cell;
}

function metricText(metrics, key) {
  const validation = (metrics || {}).validation || {};
  if (key === "coverage") return validation.coverage == null ? "—" : `${(Number(validation.coverage) * 100).toFixed(1)}%`;
  if (key === "rank_ic") return validation.rank_ic == null ? "—" : Number(validation.rank_ic).toFixed(4);
  return "—";
}

function loadFactorJobsPage() {
  const error = document.getElementById("factor-jobs-error");
  const result = document.getElementById("factor-jobs-result");
  const listView = document.getElementById("factor-jobs-list-view");
  const detailView = document.getElementById("factor-jobs-detail-view");
  const empty = document.getElementById("factor-jobs-empty");
  const list = document.getElementById("factor-jobs-list");
  if (!listView || !detailView) return;
  const runId = new URLSearchParams(window.location.search).get("run_id");
  let jobListPage = 1;
  let jobDetailPage = 1;
  let jobDetailItems = [];
  const jobDetailSelected = new Set();

  function jobListPageSize() {
    return tablePageSize("quantlab-factor-jobs-page-size", 50);
  }

  function jobItemsPageSize() {
    return tablePageSize("quantlab-factor-job-items-page-size", 20);
  }

  function jobItemSelectable(item) {
    const kept = Boolean(item.kept_in_task);
    const unevaluable = String(item.reason || "").startsWith("evaluation_error");
    return kept && !unevaluable && !item.enabled;
  }

  function showError(message) {
    error.textContent = message;
    error.classList.remove("hidden");
  }

  async function loadList() {
    error.classList.add("hidden");
    result.classList.add("hidden");
    listView.classList.remove("hidden");
    detailView.classList.add("hidden");
    list.replaceChildren();
    empty.classList.add("hidden");
    try {
      const pageSize = jobListPageSize();
      const pager = tablePager();
      const response = await fetch(`/api/factor-jobs?page=${jobListPage}&page_size=${pageSize}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      const items = payload.items || [];
      const pages = pager ? pager.pagesFor(payload.total || 0, pageSize) : Math.max(1, payload.pages || 1);
      if (pager && jobListPage > pages && (payload.total || 0) > 0) {
        jobListPage = pages;
        return loadList();
      }
      empty.classList.toggle("hidden", (payload.total || items.length) !== 0);
      bindTablePager(document.getElementById("factor-jobs-list-pagination"), {
        page: payload.page || jobListPage,
        pages,
        pageSize,
        total: payload.total || 0,
        storageKey: "quantlab-factor-jobs-page-size",
        onPage: (next) => { jobListPage = next; loadList(); },
        onPageSize: () => { jobListPage = 1; loadList(); },
      });
      if (!items.length) return;
      const table = document.createElement("table");
      table.className = "factor-job-grid";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      ["任务", "状态", "市场", "区间", "字段", "变换", "窗口", "可勾选", "未达标", "已入库", "创建"].forEach((label) => {
        const th = document.createElement("th");
        th.textContent = label;
        headRow.append(th);
      });
      head.append(headRow);
      table.append(head);
      const body = document.createElement("tbody");
      items.forEach((job) => {
        const row = document.createElement("tr");
        const href = job.detail_url || `/research/factor-jobs?run_id=${encodeURIComponent(job.run_id)}`;
        const task = document.createElement("td");
        const block = document.createElement("div");
        block.className = "factor-job-run";
        const link = document.createElement("a");
        link.href = href;
        link.textContent = job.name || "自动因子挖掘";
        const id = document.createElement("div");
        id.className = "factor-job-id";
        id.textContent = job.run_id || "";
        block.append(link, id);
        task.append(block);
        row.append(task);
        const status = document.createElement("td");
        const statusEl = document.createElement("span");
        statusEl.className = "factor-job-status status-" + (job.status || "");
        statusEl.textContent = jobStatusLabel(job.status);
        status.append(statusEl);
        row.append(status);
        row.append(jobListCell(job.market_label || "全部市场"));
        row.append(jobListCell(`${jobListDate(job.date_from)} – ${jobListDate(job.date_to)}`, "factor-job-window"));
        row.append(jobListCell(job.field_label || "—", "factor-job-field"));
        row.append(jobListCell(job.operator_label || "—"));
        row.append(jobListCell(jobListWindows(job.windows)));
        row.append(jobListCell(String(job.kept_count || 0), "factor-job-num"));
        row.append(jobListCell(String(job.rejected_count || 0), "factor-job-num"));
        row.append(jobListCell(String(job.enabled_count || 0), "factor-job-num"));
        row.append(jobListCell(jobListTime(job.created_at), "factor-job-created"));
        body.append(row);
      });
      table.append(body);
      list.append(table);
    } catch (errorValue) {
      showError(`任务列表加载失败：${errorValue.message}`);
    }
  }

  function renderJobItems() {
    const box = document.getElementById("factor-jobs-items");
    const pagerHost = document.getElementById("factor-jobs-items-pagination");
    box.replaceChildren();
    if (!jobDetailItems.length) {
      researchText(box, "div", "state", "这个任务还没有候选公式。");
      if (pagerHost) {
        pagerHost.replaceChildren();
        pagerHost.hidden = true;
      }
      return;
    }
    const pager = tablePager();
    const pageSize = jobItemsPageSize();
    const pages = pager ? pager.pagesFor(jobDetailItems.length, pageSize) : 1;
    jobDetailPage = pager ? pager.clampPage(jobDetailPage, pages) : 1;
    const visible = pager ? pager.slice(jobDetailItems, jobDetailPage, pageSize) : jobDetailItems;
    visible.forEach((item) => {
      const row = document.createElement("label");
      const kept = Boolean(item.kept_in_task);
      row.className = kept ? "factor-job-item" : "factor-job-item rejected";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = item.candidate_id;
      checkbox.dataset.kept = kept ? "1" : "0";
      const unevaluable = String(item.reason || "").startsWith("evaluation_error");
      const alreadyIn = Boolean(item.enabled);
      checkbox.disabled = alreadyIn || unevaluable || !kept;
      checkbox.checked = alreadyIn || jobDetailSelected.has(item.candidate_id);
      checkbox.addEventListener("change", () => {
        if (checkbox.disabled) return;
        if (checkbox.checked) jobDetailSelected.add(item.candidate_id);
        else jobDetailSelected.delete(item.candidate_id);
      });
      row.append(checkbox);
      const body = document.createElement("div");
      researchText(body, "div", "factor-job-item-title", item.formula_label || item.formula || "未命名公式");
      researchText(body, "div", "factor-job-item-formula", item.formula || "");
      const meta = document.createElement("div");
      meta.className = "factor-job-item-meta";
      const keepTag = document.createElement("span");
      keepTag.className = item.enabled ? "factor-job-tag ok" : (kept ? "factor-job-tag" : "factor-job-tag warn");
      keepTag.textContent = item.enabled ? "已入库" : (kept ? "可入库" : "未达标");
      meta.append(keepTag);
      meta.append(document.createTextNode(`有效值占比 ${metricText(item.period_metrics, "coverage")} · 排序相关性 ${metricText(item.period_metrics, "rank_ic")} · ${item.reason_label || item.reason || ""}`));
      body.append(meta);
      row.append(body);
      box.append(row);
    });
    if (pagerHost) pagerHost.hidden = false;
    bindTablePager(pagerHost, {
      page: jobDetailPage,
      pages,
      pageSize,
      total: jobDetailItems.length,
      storageKey: "quantlab-factor-job-items-page-size",
      onPage: (next) => { jobDetailPage = next; renderJobItems(); },
      onPageSize: () => { jobDetailPage = 1; renderJobItems(); },
    });
  }

  async function loadDetail(id) {
    error.classList.add("hidden");
    result.classList.add("hidden");
    listView.classList.add("hidden");
    detailView.classList.remove("hidden");
    const box = document.getElementById("factor-jobs-items");
    box.replaceChildren();
    try {
      const response = await fetch(`/api/factor-jobs/${encodeURIComponent(id)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      document.getElementById("factor-jobs-detail-title").textContent = `${payload.job?.name || "自动因子挖掘"} · ${jobStatusLabel(payload.job?.status)}`;
      document.getElementById("factor-jobs-config").textContent = payload.config_summary || "";
      jobDetailItems = payload.items || [];
      jobDetailSelected.clear();
      jobDetailItems.forEach((item) => {
        if (jobItemSelectable(item)) jobDetailSelected.add(item.candidate_id);
      });
      jobDetailPage = 1;
      renderJobItems();
    } catch (errorValue) {
      showError(`任务详情加载失败：${errorValue.message}`);
    }
  }

  document.getElementById("factor-jobs-select-kept")?.addEventListener("click", () => {
    error.classList.add("hidden");
    result.classList.add("hidden");
    const selectable = jobDetailItems.filter(jobItemSelectable);
    if (!selectable.length) {
      showError("没有还能勾选的公式。未达标的不能入库；已经入库的也不用再勾。");
      return;
    }
    selectable.forEach((item) => jobDetailSelected.add(item.candidate_id));
    renderJobItems();
    result.textContent = `已勾选 ${selectable.length} 条可入库公式。再点「入库并计算分析」才会写入并算 IC。`;
    result.classList.remove("hidden");
  });

  document.getElementById("factor-jobs-enable")?.addEventListener("click", async () => {
    error.classList.add("hidden");
    const enableButton = document.getElementById("factor-jobs-enable");
    const checked = [...jobDetailSelected];
    if (!checked.length) {
      showError("请先勾选要放进因子库的公式。");
      return;
    }
    if (enableButton) {
      enableButton.disabled = true;
      enableButton.textContent = "正在入库并计算分析…";
    }
    try {
      const response = await fetch(`/api/factor-jobs/${encodeURIComponent(runId)}/enable`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({candidate_ids: checked}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      result.textContent = `已入库 ${payload.enabled_count} 条并完成分析。可在因子数据页查看 IC。`;
      result.classList.remove("hidden");
      await loadDetail(runId);
    } catch (errorValue) {
      showError(`启用失败：${errorValue.message}`);
    } finally {
      if (enableButton) {
        enableButton.disabled = false;
        enableButton.textContent = "入库并计算分析";
      }
    }
  });

  if (runId) loadDetail(runId);
  else loadList();
}
