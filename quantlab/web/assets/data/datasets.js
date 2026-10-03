let datasetPage = 1;
const DATASET_PAGE_KEY = "quantlab-dataset-page-size";
const datasetQualityById = new Map();

async function loadDatasets() {
  const table = document.getElementById("dataset-table");
  const empty = document.getElementById("dataset-empty");
  const error = document.getElementById("data-error-state");
  const result = document.getElementById("rescan-result");
  const appendText = (parent, tagName, className, value) => {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = value == null ? "" : value;
    parent.appendChild(element);
    return element;
  };
  const params = new URLSearchParams();
  const query = document.getElementById("dataset-query").value.trim();
  const category = document.getElementById("dataset-category").value;
  const assetSelect = document.getElementById("dataset-asset-class");
  const assetClass = assetSelect ? assetSelect.value : "";
  const quality = document.getElementById("dataset-quality").value;
  const pageSize = tablePageSize(DATASET_PAGE_KEY, 50);
  const pager = tablePager();
  if (query) params.set("q", query);
  if (category) params.set("category", category);
  if (assetClass) params.set("asset_class", assetClass);
  if (quality) params.set("quality_status", quality);
  params.set("page", String(datasetPage));
  params.set("page_size", String(pageSize));
  try {
    const response = await fetch(`/api/datasets/raw?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const pages = pager ? pager.pagesFor(payload.total || 0, pageSize) : Math.max(1, payload.pages || 1);
    if (pager && datasetPage > pages && (payload.total || 0) > 0) {
      datasetPage = pages;
      return loadDatasets();
    }
    table.replaceChildren();
    empty.classList.toggle("hidden", (payload.total || payload.items.length) !== 0);
    const header = appendText(table, "div", "dataset-row dataset-header", "");
    header.setAttribute("role", "row");
    appendText(header, "span", null, "数据名称");
    appendText(header, "span", null, "中文名称");
    appendText(header, "span", null, "资产分类");
    appendText(header, "span", null, "类型 / 版本");
    appendText(header, "span", null, "文件 / 日期范围");
    appendText(header, "span", null, "总行数");
    appendText(header, "span", null, "总大小");
    appendText(header, "span", null, "质量");
    appendText(header, "span", null, "Tushare 接口");
    appendText(header, "span", null, "补数据");
    payload.items.forEach((item) => {
      const row = appendText(table, "div", "dataset-row", "");
      row.setAttribute("role", "row");
      const nameCell = appendText(row, "span", "dataset-name-cell", "");
      const nameButton = document.createElement("button");
      nameButton.type = "button";
      nameButton.className = "dataset-name-link";
      nameButton.textContent = item.name;
      nameButton.addEventListener("click", () => showRawInterfaceFiles(item.entity_id, item.name_cn || item.name, item.path_alias));
      nameCell.append(nameButton);
      appendText(row, "span", "dataset-cn-name", item.name_cn || item.name);
      appendText(row, "span", "dataset-asset-class", item.asset_class_label || "A股");
      appendText(row, "span", null, `${item.category} · ${item.version_id}`);
      const count = item.file_count != null ? `${item.file_count} 个文件` : (item.row_count == null ? "—" : `${new Intl.NumberFormat("zh-CN").format(item.row_count)} 行`);
      appendText(row, "span", null, `${count} · ${item.date_min ?? "—"} 至 ${item.date_max ?? "—"}`);
      appendText(row, "span", null, item.row_count == null ? "—" : new Intl.NumberFormat("zh-CN").format(item.row_count));
      appendText(row, "span", null, formatFileSize(item.total_size_bytes));
      appendText(row, "span", `quality-${item.quality_status}`, item.quality_status);
      const linkCell = appendText(row, "span", null, "");
      const link = document.createElement("a");
      link.href = item.tushare_url || "https://tushare.pro/document/2";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "接口文档 ↗";
      link.className = "raw-doc-link";
      linkCell.append(link);
      const actionCell = appendText(row, "span", "dataset-backfill", "");
      const backfillButton = document.createElement("button");
      backfillButton.type = "button";
      backfillButton.dataset.backfill = item.name;
      backfillButton.textContent = "补数据";
      backfillButton.addEventListener("click", () => startRawBackfill(item.name));
      const stopButton = document.createElement("button");
      stopButton.type = "button";
      stopButton.className = "backfill-stop";
      stopButton.dataset.backfillStop = item.name;
      stopButton.textContent = "停止";
      stopButton.hidden = true;
      stopButton.addEventListener("click", () => stopRawBackfill());
      const note = document.createElement("small");
      note.className = "backfill-note";
      actionCell.append(backfillButton, stopButton, note);
    });
    applyBackfillButtons();
    await restoreRawBackfill();
    bindTablePager(document.getElementById("dataset-pagination"), {
      page: payload.page || datasetPage,
      pages,
      pageSize,
      total: payload.total || 0,
      storageKey: DATASET_PAGE_KEY,
      onPage: (next) => { datasetPage = next; loadDatasets(); },
      onPageSize: () => { datasetPage = 1; loadDatasets(); },
    });
  } catch (errorValue) {
    error.textContent = `数据目录加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
  result.classList.add("hidden");
}

let backfillState = null;
let backfillTimer = null;

function applyBackfillButtons() {
  const running = Boolean(backfillState && backfillState.phase === "running");
  document.querySelectorAll("[data-backfill]").forEach((button) => {
    button.disabled = running;
    button.textContent = running && button.dataset.backfill === backfillState.interface ? "补数中" : "补数据";
    const note = button.parentElement && button.parentElement.querySelector(".backfill-note");
    if (note) note.textContent = backfillState && button.dataset.backfill === backfillState.interface ? backfillState.note : "";
  });
  document.querySelectorAll("[data-backfill-stop]").forEach((button) => {
    const active = running && button.dataset.backfillStop === backfillState.interface;
    button.hidden = !active;
    button.disabled = !active || Boolean(backfillState && backfillState.stopping);
    button.textContent = backfillState && backfillState.stopping ? "停止中" : "停止";
  });
}

function backfillErrorMessage(payload, status) {
  if (payload && payload.message) return payload.message;
  if (payload && payload.detail && payload.detail.message) return payload.detail.message;
  return `HTTP ${status}`;
}

async function startRawBackfill(interfaceName) {
  if (backfillState && backfillState.phase === "running") return;
  backfillState = { interface: interfaceName, phase: "running", note: "", jobId: "" };
  applyBackfillButtons();
  try {
    const response = await fetch(`/api/datasets/raw/${encodeURIComponent(interfaceName)}/backfill`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      backfillState = { interface: interfaceName, phase: "done", note: backfillErrorMessage(payload, response.status), jobId: "" };
      applyBackfillButtons();
      return;
    }
    backfillState.jobId = payload.job_id;
    if (backfillTimer) clearInterval(backfillTimer);
    backfillTimer = setInterval(() => { pollRawBackfill(); }, 2000);
    await pollRawBackfill();
  } catch (errorValue) {
    backfillState = { interface: interfaceName, phase: "done", note: errorValue.message, jobId: "" };
    applyBackfillButtons();
  }
}

async function pollRawBackfill() {
  if (!backfillState || backfillState.phase !== "running" || !backfillState.jobId) return;
  let response;
  try {
    response = await fetch(`/api/datasets/raw/backfill/${encodeURIComponent(backfillState.jobId)}`);
  } catch (errorValue) {
    backfillState.note = "连接中断，仍在查询";
    applyBackfillButtons();
    return;
  }
  const job = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (backfillTimer) clearInterval(backfillTimer);
    backfillTimer = null;
    backfillState = { interface: backfillState.interface, phase: "done", note: backfillErrorMessage(job, response.status), jobId: "" };
    applyBackfillButtons();
    return;
  }
  if (job.status === "running") {
    if (job.cancel_requested) {
      backfillState.stopping = true;
      backfillState.note = "正在停止，当前这一笔写完后结束";
    }
    applyBackfillButtons();
    return;
  }
  if (backfillTimer) clearInterval(backfillTimer);
  backfillTimer = null;
  const failed = Array.isArray(job.failed_dates) && job.failed_dates.length ? `，失败 ${job.failed_dates.join("、")}` : "";
  let note = job.status === "failed" ? (job.error || "补数失败") : `${job.summary || "已停止"}${failed}`;
  if (job.status !== "failed" && job.error) note = note ? `${note}；${job.error}` : job.error;
  const interfaceName = backfillState.interface;
  backfillState = { interface: interfaceName, phase: "done", note, jobId: job.job_id || "" };
  if (job.status === "succeeded" || job.status === "stopped") await loadDatasets();
  else applyBackfillButtons();
}

async function stopRawBackfill() {
  if (!backfillState || backfillState.phase !== "running" || !backfillState.jobId || backfillState.stopping) return;
  backfillState.stopping = true;
  backfillState.note = "正在停止，当前这一笔写完后结束";
  applyBackfillButtons();
  try {
    const response = await fetch(`/api/datasets/raw/backfill/${encodeURIComponent(backfillState.jobId)}/stop`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      backfillState.stopping = false;
      backfillState.note = backfillErrorMessage(payload, response.status);
      applyBackfillButtons();
    }
  } catch (errorValue) {
    backfillState.stopping = false;
    backfillState.note = errorValue.message;
    applyBackfillButtons();
  }
}

async function restoreRawBackfill() {
  if (backfillState && backfillState.phase === "running" && backfillState.jobId) {
    applyBackfillButtons();
    return;
  }
  try {
    const response = await fetch("/api/datasets/raw/backfill/active");
    if (!response.ok) return;
    const job = await response.json();
    if (!job || job.status !== "running" || !job.job_id) return;
    backfillState = {
      interface: job.interface,
      phase: "running",
      stopping: Boolean(job.cancel_requested),
      note: job.cancel_requested ? "正在停止，当前这一笔写完后结束" : "",
      jobId: job.job_id,
    };
    if (backfillTimer) clearInterval(backfillTimer);
    backfillTimer = setInterval(() => { pollRawBackfill(); }, 2000);
    applyBackfillButtons();
  } catch (_errorValue) {
    /* 页面仍可手动补数 */
  }
}


function formatFileSize(bytes) {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value >= 100 ? 0 : 2)} ${units[unit]}`;
}

function todayYyyymmdd() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}${month}${day}`;
}

function appendIndexWeightDownload(panel, entityId, displayName) {
  const actions = document.createElement("div");
  actions.className = "raw-files-actions";
  const hint = document.createElement("p");
  hint.className = "form-note";
  hint.textContent = "落盘规则：raw/index_weight/index_weight_{指数代码}.parquet（如 000300.SH → index_weight_000300_SH.parquet）。规则回测需同时下载 000300.SH 与 000905.SH。";
  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "btn primary";
  downloadButton.textContent = "下载成分权重";
  downloadButton.addEventListener("click", async () => {
    const indexCode = window.prompt("指数代码（如 000300.SH）", "000300.SH");
    if (!indexCode) return;
    const startDate = window.prompt("开始日期 YYYYMMDD", "20180101");
    if (!startDate) return;
    const endDate = window.prompt("结束日期 YYYYMMDD", todayYyyymmdd());
    if (!endDate) return;
    downloadButton.disabled = true;
    downloadButton.textContent = "正在下载…";
    try {
      const downloadResponse = await fetch("/api/raw/download/index_weight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index_code: indexCode.trim(), start_date: startDate.trim(), end_date: endDate.trim() }),
      });
      const downloadPayload = await downloadResponse.json();
      if (!downloadResponse.ok) {
        window.alert(downloadPayload.message || downloadPayload.detail?.message || "下载失败");
        return;
      }
      window.alert(`下载完成：${downloadPayload.rows} 行，${downloadPayload.target}`);
      document.getElementById("raw-files-dialog")?.remove();
      showRawInterfaceFiles(entityId, displayName);
    } catch (errorValue) {
      window.alert(`下载失败：${errorValue.message}`);
    } finally {
      downloadButton.disabled = false;
      downloadButton.textContent = "下载成分权重";
    }
  });
  actions.append(hint, downloadButton);
  panel.append(actions);
}

function appendStkWeekMonthAdjDownload(panel, entityId, displayName) {
  const actions = document.createElement("div");
  actions.className = "raw-files-actions";
  const hint = document.createElement("p");
  hint.className = "form-note";
  hint.textContent = "接口 stk_week_month_adj（doc 365）。周线、月线一起下，写入 raw/stk_week_month_adj/week_YYYYMMDD.parquet 和 month_YYYYMMDD.parquet。只拉每周、每月最后一个交易日。单次最多 6000 行，跨周会被截断。频次和日总量按设置里的 Tushare 积分档；已有文件跳过，可重复点继续。";
  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "btn primary";
  downloadButton.textContent = "下载周月线";
  downloadButton.addEventListener("click", async () => {
    const startDate = window.prompt("开始日期 YYYYMMDD", "19900101");
    if (!startDate) return;
    const endDate = window.prompt("结束日期 YYYYMMDD", todayYyyymmdd());
    if (!endDate) return;
    downloadButton.disabled = true;
    downloadButton.textContent = "正在下载…";
    try {
      const downloadResponse = await fetch("/api/raw/download/stk_week_month_adj", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_date: startDate.trim(), end_date: endDate.trim() }),
      });
      const downloadPayload = await downloadResponse.json();
      if (!downloadResponse.ok) {
        window.alert(downloadPayload.message || downloadPayload.detail?.message || "下载失败");
        return;
      }
      const skipped = downloadPayload.skipped || 0;
      const summary = `写入 ${downloadPayload.rows} 行，跳过 ${skipped} 个已有周期，${downloadPayload.calls || 0} 次调用，${downloadPayload.target}`;
      window.alert(downloadPayload.stopped ? `${summary}。已按设置中的日上限停止：${downloadPayload.stopped}` : `下载完成：${summary}`);
      document.getElementById("raw-files-dialog")?.remove();
      showRawInterfaceFiles(entityId, displayName);
    } catch (errorValue) {
      window.alert(`下载失败：${errorValue.message}`);
    } finally {
      downloadButton.disabled = false;
      downloadButton.textContent = "下载周月线";
    }
  });
  actions.append(hint, downloadButton);
  panel.append(actions);
}

function appendMoneyflowDownload(panel, entityId, displayName) {
  const actions = document.createElement("div");
  actions.className = "raw-files-actions";
  const hint = document.createElement("p");
  hint.className = "form-note";
  hint.textContent = "接口 moneyflow（doc 170）。按交易日写入 raw/moneyflow/YYYYMMDD.parquet。单次最多 6000 行，跨日会被截断，所以只在估算行数低于上限时合并相邻交易日。频次和日总量按设置里的 Tushare 积分档；已有文件跳过，可重复点继续。";
  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "btn primary";
  downloadButton.textContent = "下载资金流向";
  downloadButton.addEventListener("click", async () => {
    const startDate = window.prompt("开始日期 YYYYMMDD", "20100101");
    if (!startDate) return;
    const endDate = window.prompt("结束日期 YYYYMMDD", todayYyyymmdd());
    if (!endDate) return;
    downloadButton.disabled = true;
    downloadButton.textContent = "正在下载…";
    try {
      const downloadResponse = await fetch("/api/raw/download/moneyflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_date: startDate.trim(), end_date: endDate.trim() }),
      });
      const downloadPayload = await downloadResponse.json();
      if (!downloadResponse.ok) {
        window.alert(downloadPayload.message || downloadPayload.detail?.message || "下载失败");
        return;
      }
      const skipped = downloadPayload.skipped || 0;
      const summary = `写入 ${downloadPayload.rows} 行，跳过 ${skipped} 个已有交易日，${downloadPayload.calls || 0} 次调用，${downloadPayload.target}`;
      window.alert(downloadPayload.stopped ? `${summary}。已按设置中的日上限停止：${downloadPayload.stopped}` : `下载完成：${summary}`);
      document.getElementById("raw-files-dialog")?.remove();
      showRawInterfaceFiles(entityId, displayName);
    } catch (errorValue) {
      window.alert(`下载失败：${errorValue.message}`);
    } finally {
      downloadButton.disabled = false;
      downloadButton.textContent = "下载资金流向";
    }
  });
  actions.append(hint, downloadButton);
  panel.append(actions);
}

function appendSuspendDDownload(panel, entityId, displayName) {
  const actions = document.createElement("div");
  actions.className = "raw-files-actions";
  const hint = document.createElement("p");
  hint.className = "form-note";
  hint.textContent = "接口 suspend_d（doc 214）。下载写入 raw/suspend_d/suspend_d.parquet。合并到宽表时：S=当日停牌（is_suspended=1，eligible=0）；R=复牌日可交易。日内停牌也按停牌处理。";
  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "btn primary";
  downloadButton.textContent = "下载停复牌";
  downloadButton.addEventListener("click", async () => {
    const startDate = window.prompt("开始日期 YYYYMMDD", "20180101");
    if (!startDate) return;
    const endDate = window.prompt("结束日期 YYYYMMDD", todayYyyymmdd());
    if (!endDate) return;
    downloadButton.disabled = true;
    downloadButton.textContent = "正在下载…";
    try {
      const downloadResponse = await fetch("/api/raw/download/suspend_d", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_date: startDate.trim(), end_date: endDate.trim() }),
      });
      const downloadPayload = await downloadResponse.json();
      if (!downloadResponse.ok) {
        window.alert(downloadPayload.message || downloadPayload.detail?.message || "下载失败");
        return;
      }
      window.alert(`下载完成：${downloadPayload.rows} 行，${downloadPayload.target}`);
      document.getElementById("raw-files-dialog")?.remove();
      showRawInterfaceFiles(entityId, displayName);
    } catch (errorValue) {
      window.alert(`下载失败：${errorValue.message}`);
    } finally {
      downloadButton.disabled = false;
      downloadButton.textContent = "下载停复牌";
    }
  });
  const mergeButton = document.createElement("button");
  mergeButton.type = "button";
  mergeButton.className = "btn";
  mergeButton.textContent = "合并到宽表";
  mergeButton.addEventListener("click", async () => {
    if (!window.confirm("用 suspend_d 重算宽表 is_suspended，并把停牌日的 eligible 置 0。大约一两分钟，期间不要关服务。继续？")) return;
    mergeButton.disabled = true;
    downloadButton.disabled = true;
    mergeButton.textContent = "正在合并…";
    try {
      const mergeResponse = await fetch("/api/raw/apply/suspend_d", { method: "POST" });
      const mergePayload = await mergeResponse.json();
      if (!mergeResponse.ok) {
        window.alert(mergePayload.message || mergePayload.detail?.message || "合并失败");
        return;
      }
      window.alert(`合并完成：宽表 ${mergePayload.rows} 行，标记停牌 ${mergePayload.marked} 行。`);
      document.getElementById("raw-files-dialog")?.remove();
      showRawInterfaceFiles(entityId, displayName);
    } catch (errorValue) {
      window.alert(`合并失败：${errorValue.message}`);
    } finally {
      mergeButton.disabled = false;
      downloadButton.disabled = false;
      mergeButton.textContent = "合并到宽表";
    }
  });
  actions.append(hint, downloadButton, mergeButton);
  panel.append(actions);
}

function rawDirectory(entityId, pathAlias) {
  if (pathAlias) return pathAlias;
  const interfaceName = String(entityId || "").replace(/^raw_/, "");
  return interfaceName ? `raw/${interfaceName}` : "";
}

async function showRawInterfaceFiles(entityId, displayName, pathAlias) {
  if (document.getElementById("raw-files-dialog")) return;
  const interfaceName = String(entityId || "").replace(/^raw_/, "");
  const overlay = document.createElement("div");
  overlay.className = "file-config-overlay";
  overlay.id = "raw-files-dialog";
  const panel = document.createElement("div");
  panel.className = "file-config-panel raw-files-panel";
  const head = document.createElement("div");
  head.className = "file-config-head";
  const title = document.createElement("h3");
  title.textContent = `${displayName || interfaceName} · 最新文件`;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn";
  close.textContent = "关闭";
  close.addEventListener("click", () => overlay.remove());
  head.append(title, close);
  const directory = document.createElement("p");
  directory.className = "raw-files-directory";
  const directoryLabel = document.createElement("span");
  directoryLabel.textContent = "文件目录";
  const directoryValue = document.createElement("code");
  directoryValue.textContent = rawDirectory(entityId, pathAlias);
  directory.append(directoryLabel, directoryValue);
  const loading = document.createElement("p");
  loading.className = "state";
  loading.textContent = "正在加载文件列表…";
  panel.append(head, directory, loading);
  overlay.append(panel);
  document.body.append(overlay);
  const response = await fetch(`/api/raw/${encodeURIComponent(interfaceName)}/files?limit=20`);
  const payload = await response.json();
  if (!response.ok) {
    loading.textContent = payload.message || payload.detail?.message || "加载失败";
    if (interfaceName === "index_weight") {
      loading.remove();
      const empty = document.createElement("p");
      empty.className = "raw-files-summary";
      empty.textContent = "尚未下载任何成分权重文件。";
      panel.append(empty);
      appendIndexWeightDownload(panel, entityId, displayName);
    }
    if (interfaceName === "moneyflow") {
      loading.remove();
      const empty = document.createElement("p");
      empty.className = "raw-files-summary";
      empty.textContent = "尚未下载任何资金流向文件。";
      panel.append(empty);
      appendMoneyflowDownload(panel, entityId, displayName);
    }
    if (interfaceName === "stk_week_month_adj") {
      loading.remove();
      const empty = document.createElement("p");
      empty.className = "raw-files-summary";
      empty.textContent = "尚未下载任何周月线文件。";
      panel.append(empty);
      appendStkWeekMonthAdjDownload(panel, entityId, displayName);
    }
    if (interfaceName === "suspend_d") {
      loading.remove();
      const empty = document.createElement("p");
      empty.className = "raw-files-summary";
      empty.textContent = "尚未下载任何停复牌文件。";
      panel.append(empty);
      appendSuspendDDownload(panel, entityId, displayName);
    }
    return;
  }
  loading.remove();
  const summary = document.createElement("p");
  summary.className = "raw-files-summary";
  summary.textContent = payload.total
    ? `共 ${payload.total} 个文件，显示最新 20 条`
    : "尚未下载任何文件。";
  panel.append(summary);
  if (payload.total) {
    const table = document.createElement("div");
    table.className = "raw-files-table";
    table.setAttribute("role", "table");
    const headerRow = document.createElement("div");
    headerRow.className = "raw-files-row raw-files-header";
    ["文件名", "大小", "建立时间", "路径"].forEach((label) => {
      const cell = document.createElement("span");
      cell.textContent = label;
      headerRow.append(cell);
    });
    table.append(headerRow);
    payload.items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "raw-files-row";
      [item.file_name, formatFileSize(item.size_bytes), String(item.created_at || "").replace("T", " "), item.relative_path].forEach((value) => {
        const cell = document.createElement("span");
        cell.textContent = value;
        row.append(cell);
      });
      table.append(row);
    });
    panel.append(table);
  }
  if (interfaceName === "index_weight") {
    appendIndexWeightDownload(panel, entityId, displayName);
  }
  if (interfaceName === "moneyflow") {
    appendMoneyflowDownload(panel, entityId, displayName);
  }
  if (interfaceName === "stk_week_month_adj") {
    appendStkWeekMonthAdjDownload(panel, entityId, displayName);
  }
  if (interfaceName === "suspend_d") {
    appendSuspendDDownload(panel, entityId, displayName);
  }
}

function dataDetailLine(container, label, value) {
  const field = document.createElement("div");
  field.className = "dataset-detail-field";
  const name = document.createElement("span");
  name.textContent = label;
  const content = document.createElement("strong");
  content.textContent = value ?? "—";
  field.append(name, content);
  container.append(field);
}

async function selectDataset(item) {
  const detail = document.getElementById("dataset-detail");
  if (!detail) return;
  detail.replaceChildren();
  const title = document.createElement("div");
  title.className = "dataset-detail-title";
  const name = document.createElement("strong");
  name.textContent = item.name;
  const entityId = document.createElement("span");
  entityId.textContent = item.entity_id;
  title.append(name, entityId);
  detail.append(title);
  const fields = item.fields || [];
  const metaGrid = document.createElement("div");
  metaGrid.className = "dataset-detail-grid";
  detail.append(metaGrid);
  dataDetailLine(metaGrid, "版本", item.version_id);
  dataDetailLine(metaGrid, "类别", item.category);
  dataDetailLine(metaGrid, "路径别名", item.path_alias || "未登记");
  dataDetailLine(metaGrid, "行数", item.row_count == null ? "—" : new Intl.NumberFormat("zh-CN").format(item.row_count));
  dataDetailLine(metaGrid, "日期范围", `${item.date_min ?? "—"} 至 ${item.date_max ?? "—"}`);
  dataDetailLine(metaGrid, "质量状态", item.quality_status);
  if (item.quality_reason) dataDetailLine(metaGrid, "质量说明", item.quality_reason);
  const checks = datasetQualityById.get(item.entity_id) || [];
  if (checks.length) {
    const checkLabel = document.createElement("h4");
    checkLabel.textContent = "注册质量检查";
    detail.append(checkLabel);
    const checkList = document.createElement("div");
    checkList.className = "dataset-check-list";
    checks.forEach((check) => {
      const row = document.createElement("div");
      row.className = `dataset-check-row ${check.passed ? "passed" : "failed"}`;
      const mark = document.createElement("b");
      mark.textContent = check.passed ? "✓" : "○";
      const label = document.createElement("span");
      label.textContent = check.name;
      const info = document.createElement("small");
      info.textContent = check.detail || "";
      row.append(mark, label, info);
      checkList.append(row);
    });
    detail.append(checkList);
  }
  const schemaLabel = document.createElement("h4");
  schemaLabel.textContent = `Schema（${fields.length} 个字段）`;
  detail.append(schemaLabel);
  const chips = document.createElement("div");
  chips.className = "dataset-field-chips";
  if (fields.length) {
    fields.forEach((field) => {
      const chip = document.createElement("span");
      chip.textContent = field;
      chips.append(chip);
    });
  } else {
    const empty = document.createElement("span");
    empty.textContent = "无字段摘要";
    empty.className = "muted";
    chips.append(empty);
  }
  detail.append(chips);
  const versions = await loadVersions(item.entity_id);
  if (versions.length) {
    const versionLabel = document.createElement("h4");
    versionLabel.textContent = "不可变版本历史";
    detail.append(versionLabel);
    const versionList = document.createElement("div");
    versionList.className = "dataset-version-list";
    versions.forEach((version) => {
      const row = document.createElement("div");
      row.className = "dataset-version-row";
      const id = document.createElement("strong");
      id.textContent = version.version_id;
      const info = document.createElement("span");
      const versionCount = version.row_count == null ? "—" : new Intl.NumberFormat("zh-CN").format(version.row_count);
      info.textContent = `${version.quality_status} · ${versionCount} 行 · ${version.date_min ?? "—"} 至 ${version.date_max ?? "—"}`;
      row.append(id, info);
      versionList.append(row);
    });
    detail.append(versionList);
  }
}

async function loadQualityOverview() {
  const container = document.getElementById("quality-overview");
  if (!container) return;
  container.replaceChildren();
  const loading = document.createElement("span");
  loading.className = "muted";
  loading.textContent = "正在计算质量概览…";
  container.append(loading);
  try {
    const [response, checksResponse] = await Promise.all([
      fetch("/api/datasets/raw"),
      fetch("/api/datasets/quality-summary"),
    ]);
    if (!response.ok || !checksResponse.ok) throw new Error("质量概览请求失败");
    const items = (await response.json()).items || [];
    datasetQualityById.clear();
    const qualityChecks = (await checksResponse.json()).items || [];
    qualityChecks.forEach((summary) => datasetQualityById.set(summary.entity_id, summary.checks || []));
    const totalRows = items.reduce((sum, item) => sum + (Number(item.row_count) || 0), 0);
    const counts = {passed: 0, warning: 0, needs_review: 0, failed: 0};
    items.forEach((item) => { counts[item.quality_status] = (counts[item.quality_status] || 0) + 1; });
    const summary = [
      [new Intl.NumberFormat("zh-CN").format(items.length), "注册数据集"],
      [new Intl.NumberFormat("zh-CN").format(totalRows), "总行数"],
      [new Intl.NumberFormat("zh-CN").format(counts.passed || 0), "质量通过"],
      [new Intl.NumberFormat("zh-CN").format((counts.warning || 0) + (counts.needs_review || 0) + (counts.failed || 0)), "需复核 / 异常"],
    ];
    container.replaceChildren();
    summary.forEach(([value, label]) => {
      const item = document.createElement("div");
      item.className = "quality-overview-item";
      const strong = document.createElement("strong");
      strong.textContent = value;
      const span = document.createElement("span");
      span.textContent = label;
      item.append(strong, span);
      container.append(item);
    });
  } catch (errorValue) {
    container.replaceChildren();
    const message = document.createElement("span");
    message.className = "state error";
    message.textContent = `质量概览加载失败：${errorValue.message}`;
    container.append(message);
  }
}

async function loadVersions(entityId) {
  const response = await fetch(`/api/datasets/${encodeURIComponent(entityId)}/versions`);
  if (!response.ok) return [];
  return response.json();
}


async function rescanDatasets() {
  const result = document.getElementById("rescan-result");
  const response = await fetch("/api/datasets/rescan", {method: "POST"});
  const payload = await response.json();
  result.textContent = response.ok ? `扫描完成：${payload.changed_count} 个版本需要复核。` : "扫描失败。";
  result.classList.remove("hidden");
  if (response.ok) {
    await loadDatasets();
    await loadQualityOverview();
  }
}
