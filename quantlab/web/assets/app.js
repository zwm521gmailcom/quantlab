function qlChartColors() {
  if (window.qlTheme && typeof window.qlTheme.chartColors === "function") {
    return window.qlTheme.chartColors();
  }
  return {bg: "#f3f4f0", text: "#1e2a24", grid: "#d2d7cf", border: "#c3c9c0"};
}

function qlCss(prop, fallback) {
  if (window.qlTheme && typeof window.qlTheme.css === "function") {
    const value = window.qlTheme.css(prop);
    if (value) return value;
  }
  return fallback;
}

async function loadOverview() {
  const loading = document.getElementById("loading-state");
  const empty = document.getElementById("empty-state");
  const error = document.getElementById("error-state");
  const summary = document.getElementById("overview-summary");
  const connectionSummary = document.getElementById("dataset-summary");
  const runs = document.getElementById("recent-runs");
  const alerts = document.getElementById("quality-alerts");
  const runStatus = document.getElementById("run-status");
  const RUN_TYPE_LABEL = { research: "因子研究", model_training: "模型训练", backtest: "回测" };
  const STATUS_LABEL = { completed: "已完成", running: "运行中", failed: "失败", queued: "排队中" };
  const STATUS_MARK = { completed: "✓", running: "●", failed: "✕", queued: "○" };
  const TYPE_ICON = { research: "ƒ", model_training: "◇", backtest: "▶" };
  const TYPE_LABEL = { research: "因子研究", model_training: "模型训练", backtest: "回测" };
  const appendText = (parent, tagName, className, value) => {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = value;
    parent.appendChild(element);
    return element;
  };
  const formatRunTime = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };
  try {
    const response = await fetch("/api/overview");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    summary.replaceChildren();
    [
      [payload.summary.dataset_count, "数据集", "▦"],
      [payload.summary.published_factor_count, "已发布因子", "ƒ"],
      [payload.summary.model_count || 0, "模型", "◇"],
      [payload.summary.backtest_count || 0, "回测次数", "▶"],
    ].forEach(([value, label, icon]) => {
      const metric = appendText(summary, "div", "dash-kpi", "");
      appendText(metric, "span", "dash-kpi-icon", icon);
      appendText(metric, "strong", null, value);
      appendText(metric, "span", null, label);
    });
    if (runStatus) {
      runStatus.replaceChildren();
      const counts = payload.run_status_counts || {};
      const chips = [
        ["completed", "已完成"],
        ["running", "运行中"],
        ["failed", "失败"],
        ["queued", "排队中"],
      ].filter(([key]) => counts[key]);
      if (chips.length) {
        chips.forEach(([key, label]) => {
          appendText(runStatus, "span", `dash-chip dash-chip-${key}`, `${label} ${counts[key]}`);
        });
      } else {
        appendText(runStatus, "span", "dash-chip", "尚无运行");
      }
    }
    runs.replaceChildren();
    const visibleRuns = payload.recent_runs.slice(0, 4);
    if (visibleRuns.length) {
      visibleRuns.forEach((run) => {
        const link = document.createElement("a");
        link.className = "dash-run";
        link.href = run.detail_url;
        appendText(link, "span", "dash-run-icon", TYPE_ICON[run.run_type] || "▶").setAttribute("aria-hidden", "true");
        const copy = appendText(link, "span", "dash-run-copy", "");
        appendText(copy, "strong", "dash-run-name", run.name);
        appendText(copy, "span", "dash-run-meta", `${TYPE_LABEL[run.run_type] || run.run_type} · ${formatRunTime(run.created_at)}`);
        appendText(link, "span", `dash-status dash-status-${run.status}`, `${STATUS_MARK[run.status] || ""} ${STATUS_LABEL[run.status] || run.status}`.trim());
        runs.appendChild(link);
      });
    } else {
      appendText(runs, "p", "dash-empty-note", "暂无运行记录。");
    }
    alerts.replaceChildren();
    if (payload.quality_alerts.length) {
      payload.quality_alerts.forEach((alert) => {
        const row = appendText(alerts, "p", "alert-row", "");
        appendText(row, "span", "dash-alert-icon", "⚠");
        appendText(row, "span", null, alert.message);
      });
    } else {
      appendText(alerts, "p", "dash-empty-note", "暂无质量告警。");
    }
    const performance = document.getElementById("recent-performance");
    if (performance) {
      performance.replaceChildren();
      performance.className = "dash-run-grid dash-run-grid-3";
      const backtestRuns = (payload.recent_runs || []).filter((run) => run.run_type === "backtest").slice(0, 3);
      if (backtestRuns.length) {
        backtestRuns.forEach((run) => {
          const row = appendText(performance, "a", "dash-run", "");
          row.href = run.detail_url;
          appendText(row, "span", "dash-run-icon", "▶").setAttribute("aria-hidden", "true");
          const copy = appendText(row, "span", "dash-run-copy", "");
          appendText(copy, "strong", "dash-run-name", run.name);
          appendText(copy, "span", "dash-run-meta", "净值曲线在运行记录里查看");
          appendText(row, "span", `dash-status dash-status-${run.status}`, `${STATUS_MARK[run.status] || ""} ${STATUS_LABEL[run.status] || run.status}`.trim());
        });
      } else {
        appendText(performance, "p", "dash-empty-note", "暂无真实回测记录；完成一次回测后会在此显示净值表现与基准对比。");
      }
    }
    const health = document.getElementById("data-health");
    if (health) {
      health.replaceChildren();
      health.className = "dash-health";
      const alertCount = payload.quality_alerts.length;
      appendText(health, "span", "dash-kpi-icon", "▦");
      appendText(health, "strong", null, String(payload.summary.dataset_count));
      appendText(
        health,
        "span",
        alertCount ? "is-warn" : "is-ok",
        alertCount
          ? `${alertCount} 项质量告警，打开数据中心复核。`
          : "已登记数据集，暂无质量告警。",
      );
    }
    connectionSummary.textContent = "quantlab 服务已连接";
    connectionSummary.classList.remove("hidden");
    loading.classList.add("hidden");
    empty.classList.toggle("hidden", payload.recent_runs.length !== 0);
  } catch (errorValue) {
    loading.classList.add("hidden");
    error.textContent = `服务连接失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

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
  const quality = document.getElementById("dataset-quality").value;
  if (query) params.set("q", query);
  if (category) params.set("category", category);
  if (quality) params.set("quality_status", quality);
  try {
    const response = await fetch(`/api/datasets/raw?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    table.replaceChildren();
    empty.classList.toggle("hidden", payload.items.length !== 0);
    const header = appendText(table, "div", "dataset-row dataset-header", "");
    header.setAttribute("role", "row");
    appendText(header, "span", null, "数据名称");
    appendText(header, "span", null, "中文名称");
    appendText(header, "span", null, "类型 / 版本");
    appendText(header, "span", null, "文件 / 日期范围");
    appendText(header, "span", null, "总行数");
    appendText(header, "span", null, "总大小");
    appendText(header, "span", null, "质量");
    appendText(header, "span", null, "文件目录");
    appendText(header, "span", null, "Tushare 接口");
    payload.items.forEach((item) => {
      const row = appendText(table, "div", "dataset-row", "");
      row.setAttribute("role", "row");
      const nameCell = appendText(row, "span", "dataset-name-cell", "");
      const nameButton = document.createElement("button");
      nameButton.type = "button";
      nameButton.className = "dataset-name-link";
      nameButton.textContent = item.name;
      nameButton.addEventListener("click", () => showRawInterfaceFiles(item.entity_id, item.name_cn || item.name));
      nameCell.append(nameButton);
      appendText(row, "span", "dataset-cn-name", item.name_cn || item.name);
      appendText(row, "span", null, `${item.category} · ${item.version_id}`);
      const count = item.file_count != null ? `${item.file_count} 个文件` : (item.row_count == null ? "—" : `${new Intl.NumberFormat("zh-CN").format(item.row_count)} 行`);
      appendText(row, "span", null, `${count} · ${item.date_min ?? "—"} 至 ${item.date_max ?? "—"}`);
      appendText(row, "span", null, item.row_count == null ? "—" : new Intl.NumberFormat("zh-CN").format(item.row_count));
      appendText(row, "span", null, formatFileSize(item.total_size_bytes));
      appendText(row, "span", `quality-${item.quality_status}`, item.quality_status);
      appendText(row, "span", "path-alias", item.path_alias);
      const linkCell = appendText(row, "span", null, "");
      const link = document.createElement("a");
      link.href = item.tushare_url || "https://tushare.pro/document/2";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "接口文档 ↗";
      link.className = "raw-doc-link";
      linkCell.append(link);
    });
  } catch (errorValue) {
    error.textContent = `数据目录加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
  result.classList.add("hidden");
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

async function showRawInterfaceFiles(entityId, displayName) {
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
  const loading = document.createElement("p");
  loading.className = "state";
  loading.textContent = "正在加载文件列表…";
  panel.append(head, loading);
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

function klineParams({downsample = null, pageSize = null, tail = false} = {}) {
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
  params.set("page", "1");
  params.set("page_size", tail ? "10" : pageSize ? String(pageSize) : downsample ? "500" : "100");
  params.set("max_rows", "500");
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

async function loadKline() {
  const error = document.getElementById("kline-error");
  error.classList.add("hidden");
  const tableParams = klineParams({tail: true});
  const chartParams = klineParams({pageSize: 500});
  const qualityParams = new URLSearchParams({version_id: "current"});
  try {
    const [tableResponse, chartResponse, qualityResponse] = await Promise.all([
      fetch(`/api/kline/query?${tableParams}`),
      fetch(`/api/kline/query?${chartParams}`),
      fetch(`/api/kline/quality?${qualityParams}`),
    ]);
    if (![tableResponse, chartResponse, qualityResponse].every((response) => response.ok)) throw new Error("标准行情宽表 API 请求失败");
    const [table, chart, quality] = await Promise.all([
      tableResponse.json(), chartResponse.json(), qualityResponse.json(),
    ]);
    renderKlineTable(table); renderKlineChart(chart); renderKlineQuality(quality);
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
  const symbol = document.getElementById("factor-symbol")?.value.trim() || "";
  const dateFrom = document.getElementById("factor-date-from")?.value || "";
  const dateTo = document.getElementById("factor-date-to")?.value || "";
  if (factor) params.set("factor", factor);
  if (symbol) params.set("ts_code", symbol);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  params.set("version_id", "v1");
  return params;
}

let factorInfoPopover = null;

function closeFactorInfoPopover() {
  if (factorInfoPopover) {
    factorInfoPopover.remove();
    factorInfoPopover = null;
  }
}

function openFactorInfoPopover(trigger, title, lines) {
  closeFactorInfoPopover();
  const popover = document.createElement("div");
  popover.className = "factor-info-popover";
  const heading = document.createElement("strong");
  heading.textContent = title;
  popover.append(heading);
  lines.forEach((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    popover.append(p);
  });
  document.body.append(popover);
  const rect = trigger.getBoundingClientRect();
  popover.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - popover.offsetWidth - 8))}px`;
  popover.style.top = `${rect.bottom + 6}px`;
  factorInfoPopover = popover;
  const dismiss = (event) => {
    if (factorInfoPopover && !factorInfoPopover.contains(event.target) && !trigger.contains(event.target)) {
      closeFactorInfoPopover();
      document.removeEventListener("click", dismiss);
    }
  };
  setTimeout(() => document.addEventListener("click", dismiss), 0);
}

function infoDot(title, lines) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "info-dot";
  button.setAttribute("aria-label", `解释：${title}`);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  circle.setAttribute("cx", "12");
  circle.setAttribute("cy", "12");
  circle.setAttribute("r", "9");
  const body = document.createElementNS("http://www.w3.org/2000/svg", "path");
  body.setAttribute("d", "M12 11v5M12 8h.01");
  svg.append(circle, body);
  button.append(svg);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    openFactorInfoPopover(button, title, lines);
  });
  return button;
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

function factorCatalogPageSize() {
  const select = document.getElementById("factor-page-size");
  const value = Number(select && select.value);
  return FACTOR_PAGE_SIZES.includes(value) ? value : 50;
}

function renderFactorCatalog(items) {
  factorCatalogAll = items;
  const table = document.getElementById("factor-table");
  const empty = document.getElementById("factor-empty");
  const query = (document.getElementById("factor-name-search")?.value || "").trim().toLowerCase();
  const filtered = query
    ? items.filter((item) => String(item.name || "").toLowerCase().includes(query) || String(item.factor_id || "").toLowerCase().includes(query))
    : items;
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
    renderFactorCatalog(items);
    document.getElementById("factor-name-search")?.addEventListener("input", () => { factorCatalogPage = 1; renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-page-prev")?.addEventListener("click", () => { factorCatalogPage = Math.max(1, factorCatalogPage - 1); renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-page-next")?.addEventListener("click", () => { factorCatalogPage += 1; renderFactorCatalog(factorCatalogAll); });
    document.getElementById("factor-page-size")?.addEventListener("change", () => { factorCatalogPage = 1; renderFactorCatalog(factorCatalogAll); });
  } catch (errorValue) {
    error.textContent = `因子目录加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

let activeFactorRunId = null;
let currentFactorItem = null;
let currentFactorSample = {items: [], fields: []};
let currentFactorHistory = [];

function factorConditionalPayload(latest) {
  if (!latest) return null;
  const payload = latest.conditional_ic && Object.keys(latest.conditional_ic).length
    ? latest.conditional_ic
    : latest.summary && latest.summary.conditional_ic;
  if (!payload || typeof payload !== "object") return null;
  if (!payload.by_float_market_cap && !payload.by_turn) return null;
  return payload;
}

function conditionalCapIc(latest, bucket) {
  const payload = factorConditionalPayload(latest);
  const buckets = payload && payload.by_float_market_cap && payload.by_float_market_cap.buckets || [];
  const match = buckets.find((item) => Number(item.bucket) === bucket);
  return match ? match.ic_mean : null;
}


async function runFactorAnalysis(item, previous = null) {
  const error = document.getElementById("factor-error");
  const button = document.getElementById("factor-run-analysis");
  if (!item) return;
  const dateFrom = document.getElementById("factor-date-from")?.value.trim() || previous?.date_from || "";
  const dateTo = document.getElementById("factor-date-to")?.value.trim() || previous?.date_to || "";
  const body = {
    factor_id: item.factor_id,
    version_id: item.factor_version_id || item.version_id || "v1",
  };
  if (dateFrom) body.date_from = dateFrom;
  if (dateTo) body.date_to = dateTo;
  let idleLabel = "运行分析";
  if (button) {
    idleLabel = button.textContent || idleLabel;
    button.disabled = true;
    button.textContent = "正在计算分析…";
  }
  error.classList.add("hidden");
  try {
    const response = await fetch("/api/factor-calculations", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || payload.detail?.message || `HTTP ${response.status}`);
    await loadFactorDetail(item.factor_id);
  } catch (errorValue) {
    error.textContent = `分析失败：${errorValue.message}`;
    error.classList.remove("hidden");
    if (button) {
      button.disabled = false;
      button.textContent = idleLabel;
    }
  }
}

function renderFactorDetail(item, summary, sample, calculation = null) {
  currentFactorItem = item;
  currentFactorSample = sample;
  const detail = document.getElementById("factor-detail");
  detail.replaceChildren();
  const titleRow = document.createElement("div");
  titleRow.className = "factor-detail-title-row";
  const backLink = document.createElement("a");
  backLink.href = "/factors";
  backLink.className = "factor-detail-back";
  backLink.textContent = "← 因子数据";
  const heading = document.createElement("h3");
  heading.textContent = `${item.name} · ${item.factor_id}`;
  titleRow.append(backLink, heading);
  const latestForButton = calculation && calculation.status ? calculation : item.latest_calculation;
  const completedForButton = latestForButton && latestForButton.status === "completed" ? latestForButton : null;
  const runButton = document.createElement("button");
  runButton.type = "button";
  runButton.id = "factor-run-analysis";
  runButton.className = "factor-run-analysis";
  runButton.textContent = completedForButton ? "重新运行分析" : "运行分析";
  runButton.addEventListener("click", () => runFactorAnalysis(item, latestForButton));
  titleRow.append(runButton);
  detail.append(titleRow);
  const formulaBlock = appendFactorText(detail, "div", "factor-formula-block", "");
  appendFactorText(formulaBlock, "h4", null, "公式");
  appendFactorText(formulaBlock, "pre", "factor-formula-expression", item.formula);
  (item.formula_explanation || []).forEach((line) => {
    if (line === (item.formula_explanation || [])[0]) appendFactorText(formulaBlock, "h4", null, "公式中文解释");
    appendFactorText(formulaBlock, "p", null, line);
  });
  appendFactorText(detail, "p", "factor-detail-meta", `因子版本 ${item.factor_version_id} · 数据版本 canonical/${item.dataset_version_id} · 方向 ${item.direction_label} · 频率 ${item.frequency}`);
  appendFactorText(detail, "p", "factor-storage", "因子数据文件：data/canonical.parquet（标准行情只读，因子按计算任务生成，不再依赖 features.parquet）");
  appendFactorText(detail, "p", "factor-detail-meta", `缺失规则：${item.missing_policy}；点时规则：${item.pit_policy}`);
  const summaryBox = document.getElementById("factor-summary");
  summaryBox.replaceChildren();
  const latest = calculation && calculation.status ? calculation : item.latest_calculation;
  const completed = latest && latest.status === "completed" ? latest : null;
  const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  const pct2 = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
  const four = (value) => value == null ? "—" : Number(value).toFixed(4);
  const groups = [
    {
      title: "数据质量",
      metrics: [
        ["计算行数", completed && completed.row_count != null ? new Intl.NumberFormat("zh-CN").format(completed.row_count) : "未诊断"],
        ["缺失行数", completed && completed.missing_rows != null ? new Intl.NumberFormat("zh-CN").format(completed.missing_rows) : "未诊断"],
        ["计算覆盖率", completed && completed.coverage != null ? pct2(completed.coverage) : "未诊断"],
        ["有效交易日", completed && completed.effective_days != null ? completed.effective_days : "未诊断"],
        ["计算周期", completed ? `${completed.date_from} — ${completed.date_to}` : "未诊断"],
        ["计算流水号", completed ? completed.serial_no : "未诊断"],
      ],
    },
    {
      title: "有效性指标",
      metrics: [
        ["IC 均值", completed ? four(completed.ic_mean) : "未诊断"],
        ["IC 正值比例", completed ? pct(completed.ic_positive_ratio) : "未诊断"],
        ["IC 标准差", completed ? four(completed.ic_std) : "未诊断"],
        ["ICIR", completed ? four(completed.icir) : "未诊断"],
        ["小盘 IC", completed ? four(conditionalCapIc(completed, 1)) : "未诊断"],
        ["大盘 IC", completed ? four(conditionalCapIc(completed, 5)) : "未诊断"],
      ],
    },
    {
      title: "分组表现",
      metrics: [
        ["Top-Bottom 价差", completed ? four(completed.top_bottom_spread) : "未诊断"],
        ["单调性", completed ? four(completed.monotonicity) : "未诊断"],
        ["换手率均值", completed ? four(completed.turnover_mean) : "未诊断"],
        ["方向", `${item.direction_label} ${item.direction === "negative" ? "↓" : "↑"}`],
      ],
    },
  ];
  if (completed) {
    const quantiles = completed.quantiles || {};
    groups.push({
      title: "分布特征",
      metrics: [
        ["均值", four(completed.factor_mean)],
        ["标准差", four(completed.factor_std)],
        ["最小值", four(completed.factor_min)],
        ["最大值", four(completed.factor_max)],
        ["P1 / P5", `${four(quantiles.p1)} / ${four(quantiles.p5)}`],
        ["P25 / P50", `${four(quantiles.p25)} / ${four(quantiles.p50)}`],
        ["P75 / P95 / P99", `${four(quantiles.p75)} / ${four(quantiles.p95)} / ${four(quantiles.p99)}`],
        ["偏度", four(completed.factor_skew)],
        ["峰度", four(completed.factor_kurtosis)],
      ],
    });
  }
  groups.forEach((group) => {
    const heading = document.createElement("h4");
    heading.textContent = group.title;
    summaryBox.append(heading);
    const grid = document.createElement("div");
    grid.className = "factor-metric-grid";
    group.metrics.forEach(([label, value]) => {
      const card = appendFactorText(grid, "div", "factor-metric", "");
      appendFactorText(card, "strong", null, value);
      appendFactorText(card, "span", null, label);
    });
    summaryBox.append(grid);
  });
    renderFactorDailyIc(completed);
  const table = document.getElementById("factor-sample");
  table.replaceChildren();
  const sampleRows = (sample.items || []).slice(0, 10);
  if (!sampleRows.length) return;
  table.style.setProperty("--factor-col-count", String(Math.max((sample.fields || []).length, 1)));
  const header = appendFactorText(table, "div", "factor-row factor-header", "");
  sample.fields.forEach((field) => appendFactorText(header, "span", null, field));
  sampleRows.forEach((item) => {
    const row = appendFactorText(table, "div", "factor-row", "");
    sample.fields.forEach((field) => appendFactorText(row, "span", null, item[field]));
  });
}

let factorIcChartInstance = null;

function renderFactorChartSeries(block, chartData, color, title) {
  if (typeof window.LightweightCharts === "undefined") return;
  const theme = qlChartColors();
  const chart = window.LightweightCharts.createChart(block, {
    autoSize: true,
    height: 220,
    layout: {background: {type: window.LightweightCharts.ColorType.Solid, color: theme.bg}, textColor: theme.text, fontSize: 11},
    grid: {vertLines: {color: theme.grid}, horzLines: {color: theme.grid}},
    rightPriceScale: {borderColor: theme.border},
    timeScale: {borderColor: theme.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN"},
    handleScroll: false,
    handleScale: false,
  });
  const heading = document.createElement("div");
  heading.className = "factor-chart-title";
  heading.textContent = title;
  block.append(heading);
  const series = chart.addLineSeries({color: color, lineWidth: 2, priceFormat: {type: "price", precision: 4}});
  series.setData(chartData.filter((item) => item.time));
  series.createPriceLine({price: 0, color: theme.border, lineWidth: 1, lineStyle: window.LightweightCharts.LineStyle.Dashed, axisLabelVisible: false});
  chart.timeScale().fitContent();
  chart.applyOptions({rightPriceScale: {scaleMargins: {top: 0.1, bottom: 0.1}}});
  return chart;
}


function factorChartHeading(title) {
  const heading = document.createElement("div");
  heading.className = "factor-chart-title";
  heading.append(title);
  return heading;
}

const FACTOR_CONDITIONAL_COLORS = ["#e0b48a", "#c4622d", "#8c5340", "#4d6b5c", "#2f6a4a"];
const FACTOR_CONDITIONAL_CHARTS = [
  {
    key: "by_float_market_cap",
    title: "分层 IC · 流通市值",
    missing: "行情宽表没有流通市值字段，无法分层。",
    thin: "有效样本不足以按市值分层（每档至少 3 只）。",
  },
  {
    key: "by_turn",
    title: "分层 IC · 换手率",
    missing: "行情宽表没有换手率字段，无法分层。",
    thin: "有效样本不足以按换手分层（每档至少 3 只）。",
  },
];

function renderFactorConditionalBars(container, title, buckets) {
  const block = document.createElement("div");
  block.className = "factor-chart-block factor-ic-buckets";
  block.append(factorChartHeading(title));
  const values = buckets.map((item) => item.ic_mean).filter((value) => value != null).map(Number);
  const maxAbs = Math.max(...values.map(Math.abs), 1e-6);
  const row = document.createElement("div");
  row.className = "factor-ic-bucket-bars";
  buckets.forEach((bucket) => {
    const cell = document.createElement("div");
    cell.className = "factor-ic-bucket";
    const track = document.createElement("div");
    track.className = "factor-ic-bucket-track";
    const zero = document.createElement("div");
    zero.className = "factor-ic-bucket-zero";
    track.append(zero);
    if (bucket.ic_mean != null) {
      const bar = document.createElement("div");
      const ic = Number(bucket.ic_mean);
      bar.className = `factor-ic-bucket-bar ${ic >= 0 ? "positive" : "negative"}`;
      bar.style.height = `${Math.max(2, Math.round((Math.abs(ic) / maxAbs) * 50))}%`;
      bar.title = `${bucket.label} · IC ${ic.toFixed(4)} · ${bucket.effective_days || 0} 日`;
      track.append(bar);
    }
    const label = document.createElement("span");
    label.className = "factor-ic-bucket-label";
    label.textContent = bucket.label || `Q${bucket.bucket}`;
    const value = document.createElement("span");
    value.className = "factor-ic-bucket-value";
    value.textContent = bucket.ic_mean == null ? "—" : Number(bucket.ic_mean).toFixed(4);
    cell.append(track, label, value);
    row.append(cell);
  });
  block.append(row);
  container.append(block);
}

function renderFactorConditionalLines(container, title, buckets) {
  if (typeof window.LightweightCharts === "undefined") return;
  const hasSeries = buckets.some((bucket) => (bucket.daily_ic || []).length);
  if (!hasSeries) return;
  const block = document.createElement("div");
  block.className = "factor-chart-block factor-ic-lines";
  const chartHost = document.createElement("div");
  chartHost.className = "factor-ic-line-host";
  block.append(factorChartHeading(title + " · 每日曲线"));
  const legend = document.createElement("div");
  legend.className = "factor-ic-bucket-legend";
  block.append(chartHost, legend);
  container.append(block);
  const _fc = qlChartColors();
  const chart = window.LightweightCharts.createChart(chartHost, {
    autoSize: true,
    height: 220,
    layout: {background: {type: window.LightweightCharts.ColorType.Solid, color: _fc.bg}, textColor: _fc.text, fontSize: 11},
    grid: {vertLines: {color: _fc.grid}, horzLines: {color: _fc.grid}},
    rightPriceScale: {borderColor: _fc.border},
    timeScale: {borderColor: _fc.border, timeVisible: false, secondsVisible: false},
    localization: {locale: "zh-CN", dateFormat: "yyyy-MM-dd"},
    handleScroll: false,
    handleScale: false,
  });
  buckets.forEach((bucket, index) => {
    const points = (bucket.daily_ic || [])
      .map((point) => ({time: klineBusinessDate(point.date), value: point.ic}))
      .filter((point) => point.time != null && Number.isFinite(point.value));
    if (!points.length) return;
    const series = chart.addLineSeries({
      color: FACTOR_CONDITIONAL_COLORS[index] || qlCss("--color-primary", "#c4622d"),
      lineWidth: 2,
      priceFormat: {type: "price", precision: 4},
    });
    series.setData(points);
    if (index === 0) series.createPriceLine({price: 0, color: "#c8d0dd", lineWidth: 1, lineStyle: window.LightweightCharts.LineStyle.Dashed, axisLabelVisible: false});
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "factor-ic-bucket-toggle";
    toggle.setAttribute("aria-pressed", "true");
    toggle.title = `隐藏 ${bucket.label || `Q${bucket.bucket}`}`;
    const swatch = document.createElement("span");
    swatch.className = "factor-ic-swatch";
    swatch.style.background = FACTOR_CONDITIONAL_COLORS[index] || qlCss("--color-primary", "#c4622d");
    toggle.append(swatch, bucket.label || `Q${bucket.bucket}`);
    toggle.addEventListener("click", () => {
      const visible = toggle.getAttribute("aria-pressed") !== "false";
      series.setData(visible ? [] : points);
      toggle.setAttribute("aria-pressed", String(!visible));
      toggle.title = `${visible ? "显示" : "隐藏"} ${bucket.label || `Q${bucket.bucket}`}`;
    });
    legend.append(toggle);
  });
  chart.timeScale().fitContent();
}

function renderFactorConditionalIc(container, latest) {
  const payload = factorConditionalPayload(latest);
  if (!payload) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "此任务未含分层 IC，重新运行分析后显示。";
    container.append(empty);
    return;
  }
  FACTOR_CONDITIONAL_CHARTS.forEach((spec) => {
    const dimension = payload[spec.key];
    if (!dimension) return;
    if (dimension.status === "missing_field") {
      const note = document.createElement("p");
      note.className = "state";
      note.textContent = spec.missing;
      container.append(note);
      return;
    }
    if (dimension.status !== "available") {
      const note = document.createElement("p");
      note.className = "state";
      note.textContent = spec.thin;
      container.append(note);
      return;
    }
    const buckets = dimension.buckets || [];
    renderFactorConditionalBars(container, spec.title, buckets);
    renderFactorConditionalLines(container, spec.title, buckets);
  });
}

function formatFactorChartNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const abs = Math.abs(number);
  if (abs >= 1e6 || (abs > 0 && abs < 1e-3)) return number.toExponential(2);
  if (abs >= 100) return number.toFixed(1);
  return number.toFixed(4);
}

function renderFactorHistogramOverlay(histogram, maxCount, height) {
  const hasExpected = histogram.some((item) => Number.isFinite(Number(item.expected)));
  if (!hasExpected) return null;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("factor-normal-overlay");
  svg.setAttribute("viewBox", `0 0 ${histogram.length} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", histogram.map((item, index) => {
    const expected = Math.max(0, Number(item.expected) || 0);
    const y = height - (expected / maxCount) * (height - 10);
    return `${index ? "L" : "M"}${(index + 0.5).toFixed(2)} ${y.toFixed(2)}`;
  }).join(" "));
  svg.append(path);
  return svg;
}

function renderFactorDailyIc(latest) {
  const container = document.getElementById("factor-charts");
  if (!container) return;
  container.replaceChildren();
  if (!latest || !latest.summary || typeof window.LightweightCharts === "undefined") {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "图表资源未加载或尚未运行计算。";
    container.append(empty);
    return;
  }
  const icPoints = latest.summary.daily_ic || [];
  const spreadPoints = latest.summary.daily_top_bottom || [];
  if (icPoints.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block";
    container.append(block);
    const chart = renderFactorChartSeries(block, icPoints.map((point) => ({time: klineBusinessDate(point.date), value: point.ic})), qlCss("--color-primary", "#c4622d"), "每日 IC 曲线");
    if (chart) factorIcChartInstance = chart;
  }
  if (spreadPoints.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block";
    container.append(block);
    renderFactorChartSeries(block, spreadPoints.map((point) => ({time: klineBusinessDate(point.date), value: point.spread})), qlCss("--color-danger", "#9b2c2c"), "Top-Bottom 每日价差");
  }
  renderFactorConditionalIc(container, latest);
  const histogram = latest.histogram || [];
  if (histogram.length) {
    const block = document.createElement("div");
    block.className = "factor-chart-block factor-histogram";
    const histogramRange = latest.histogram_range || latest.summary?.histogram_range || {};
    const logScale = histogramRange.scale === "log";
    const maxCount = Math.max(
      ...histogram.map((item) => item.count || 0),
      ...histogram.map((item) => Number(item.expected) || 0),
      1,
    );
    const yAxis = document.createElement("div");
    yAxis.className = "factor-histogram-y";
    [maxCount, Math.round(maxCount / 2), 0].forEach((value) => {
      const label = document.createElement("span");
      label.textContent = Math.round(value);
      yAxis.append(label);
    });
    const bars = document.createElement("div");
    bars.className = "factor-histogram-bars";
    histogram.forEach((item) => {
      const bar = document.createElement("div");
      bar.className = "factor-histogram-bar";
      bar.style.height = `${Math.max(2, Math.round((item.count / maxCount) * 140))}px`;
      const expected = Number.isFinite(Number(item.expected)) ? ` · 正态期望 ${Math.round(Number(item.expected))}` : "";
      bar.title = `${formatFactorChartNumber(item.left)} ~ ${formatFactorChartNumber(item.right)} · ${item.count} 行${expected}`;
      bars.append(bar);
    });
    const overlay = renderFactorHistogramOverlay(histogram, maxCount, 150);
    if (overlay) bars.append(overlay);
    const xAxis = document.createElement("div");
    xAxis.className = "factor-histogram-x";
    const minLabel = document.createElement("span");
    minLabel.textContent = formatFactorChartNumber(histogram[0].left);
    const midLabel = document.createElement("span");
    midLabel.textContent = formatFactorChartNumber(histogram[Math.floor(histogram.length / 2)].left);
    const maxLabel = document.createElement("span");
    maxLabel.textContent = formatFactorChartNumber(histogram[histogram.length - 1].right);
    xAxis.append(minLabel, midLabel, maxLabel);
    const rangeNote = document.createElement("span");
    rangeNote.className = "factor-histogram-range-note";
    const scaleLabel = logScale ? "对数" : "线性";
    if (histogramRange.basis === "p1_p99") {
      rangeNote.textContent = `显示 P1–P99 · ${scaleLabel} · 两端极值：${histogramRange.lower_tail_count || 0} / ${histogramRange.upper_tail_count || 0}`;
    } else {
      rangeNote.textContent = `显示全部原始范围 · ${scaleLabel}`;
    }
    block.append(factorChartHeading(logScale ? "原始因子值分布（对数分桶）" : "原始因子值分布"), rangeNote, yAxis, bars, xAxis);
    container.append(block);
  }
  if (!container.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "尚无每日 IC 序列，运行计算后在此显示。";
    container.append(empty);
  }
}

function renderFactorCalculationHistory(items) {
  const container = document.getElementById("factor-calculation-history");
  if (!container) return;
  container.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = "计算历史";
  container.append(heading);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "state";
    empty.textContent = "尚未运行真实计算；运行后每条记录的周期、时间、指标与错误都会留在这里。";
    container.append(empty);
    return;
  }
  const table = document.createElement("div");
  table.className = "factor-calculation-table";
  table.setAttribute("role", "table");
  const header = appendFactorText(table, "div", "factor-row factor-header factor-calculation-row", "");
  ["任务（流水号 · 周期）", "开始 → 完成", "覆盖 / 缺失", "IC 均值 / 正值 / 稳定性", "状态 / 说明"].forEach((label) => appendFactorText(header, "span", null, label));
  items.forEach((item) => {
    const row = appendFactorText(table, "div", "factor-calculation-row", "");
    row.classList.toggle("factor-calculation-row-active", item.calculation_id === activeFactorRunId);
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-label", `切换到 ${item.serial_no}`);
    appendFactorText(row, "span", null, `${item.serial_no}
${String(item.date_from).slice(0, 4)}-${String(item.date_from).slice(4, 6)}-${String(item.date_from).slice(6, 8)}\n${String(item.date_to).slice(0, 4)}-${String(item.date_to).slice(4, 6)}-${String(item.date_to).slice(6, 8)}`);
    const startRaw = String(item.started_at || "");
    const endRaw = item.finished_at ? String(item.finished_at) : "";
    const startDate = startRaw.slice(0, 10);
    const startTime = startRaw.slice(11, 16);
    const endDate = endRaw.slice(0, 10);
    const endTime = endRaw.slice(11, 16);
    const timeText = startDate === endDate && endDate ? `${startDate} ${startTime} → ${endTime}` : `${startDate} ${startTime} → ${endDate || "—"} ${endTime || "—"}`;
    appendFactorText(row, "span", null, timeText);
    appendFactorText(row, "span", null, `${item.coverage == null ? "—" : (item.coverage * 100).toFixed(2) + "%"} / ${item.missing_rows ?? "—"}`);
    appendFactorText(row, "span", null, `${item.ic_mean == null ? "—" : Number(item.ic_mean).toFixed(4)}\n${item.ic_positive_ratio == null ? "—" : (Number(item.ic_positive_ratio) * 100).toFixed(1) + "%"}\n${item.ic_std == null ? "—" : Number(item.ic_std).toFixed(4)}`);
    appendFactorText(row, "span", null, `${item.status === "completed" ? "已完成" : item.status === "failed" ? "失败" : "计算中"} · ${item.error_message || `${item.effective_days ?? 0} 日`}`);
    row.addEventListener("click", () => {
      activeFactorRunId = item.calculation_id;
      renderFactorDetail(currentFactorItem, null, currentFactorSample, item);
      renderFactorCalculationHistory(currentFactorHistory);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        row.click();
      }
    });
  });
  container.append(table);
}

async function loadFactorDetail(factorId) {
  const error = document.getElementById("factor-error");
  try {
    const [detailResponse, calculationResponse] = await Promise.all([
      fetch(`/api/factor-data/factors/${encodeURIComponent(factorId)}`),
      fetch(`/api/factor-calculations?factor_id=${encodeURIComponent(factorId)}`),
    ]);
    if (![detailResponse, calculationResponse].every((response) => response.ok)) throw new Error("因子详情请求失败");
    const item = await detailResponse.json();
    const historyItems = (await calculationResponse.json()).items || [];
    const latestRun = historyItems.find((entry) => entry.status === "completed") || null;
    const sampleQuery = new URLSearchParams({factor: factorId, page_size: "10", max_rows: "10"});
    const userFrom = document.getElementById("factor-date-from")?.value.trim();
    const userTo = document.getElementById("factor-date-to")?.value.trim();
    sampleQuery.set("date_from", userFrom || latestRun?.date_from || "2024-01-01");
    sampleQuery.set("date_to", userTo || latestRun?.date_to || "2024-12-31");
    let sample = {items: [], fields: []};
    try {
      const sampleResponse = await fetch(`/api/factor-data/query?${sampleQuery.toString()}`);
      if (sampleResponse.ok) sample = await sampleResponse.json();
    } catch (_error) {
      // Manual/derived factors may not have a physical feature column yet.
    }
    currentFactorHistory = historyItems;
    if (activeFactorRunId === null && latestRun) activeFactorRunId = latestRun.calculation_id;
    renderFactorDetail(item, null, sample, latestRun);
    renderFactorCalculationHistory(historyItems);
  } catch (errorValue) {
    error.textContent = `因子详情加载失败：${errorValue.message}`;
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
    base.set("page_size", "10");
    base.set("max_rows", "10");
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

function factorLibraryText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null || value === "" ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function factorLibraryParams() {
  const params = new URLSearchParams();
  const values = [
    ["q", document.getElementById("factor-library-keyword")?.value.trim()],
    ["category", document.getElementById("factor-library-category")?.value],
    ["source", document.getElementById("factor-library-source")?.value],
    ["lifecycle", document.getElementById("factor-library-lifecycle")?.value],
    ["quality", document.getElementById("factor-library-quality")?.value],
  ];
  values.forEach(([key, value]) => { if (value) params.set(key, value); });
  params.set("page", "1");
  params.set("page_size", "50");
  return params;
}

function factorLibraryReference(item) {
  return {entity_id: item.factor_entity_id || item.entity_id, version_id: item.factor_version_id || item.version_id};
}

function selectedFactorLibraryReferences() {
  return Array.from(document.querySelectorAll("#factor-library-table input[data-factor-checkbox]:checked")).map((checkbox) => ({
    entity_id: checkbox.dataset.entityId,
    version_id: checkbox.dataset.versionId,
  }));
}

function renderFactorLibrary(items) {
  const table = document.getElementById("factor-library-table");
  const empty = document.getElementById("factor-library-empty");
  table.replaceChildren();
  empty.classList.toggle("hidden", items.length !== 0);
  if (!items.length) return;
  const header = factorLibraryText(table, "div", "factor-library-row factor-library-header", "");
  factorLibraryText(header, "span", null, "");
  ["名称 / 代码", "版本", "分类", "方向", "公式摘要", "来源", "覆盖", "缺失", "生命周期 / 质量", "最后验证", "操作"].forEach((label) => factorLibraryText(header, "span", null, label));
  items.forEach((item) => {
    const row = factorLibraryText(table, "div", "factor-library-row", "");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.factorCheckbox = "true";
    checkbox.dataset.entityId = item.factor_entity_id || item.entity_id || "";
    checkbox.dataset.versionId = item.factor_version_id || item.version_id || "";
    checkbox.setAttribute("aria-label", `选择 ${item.name || checkbox.dataset.entityId}`);
    row.appendChild(checkbox);
    const nameCell = factorLibraryText(row, "span", null, "");
    const link = factorLibraryText(nameCell, "a", null, item.name);
    link.href = item.detail_url || `/factors/${encodeURIComponent(checkbox.dataset.entityId)}/${encodeURIComponent(checkbox.dataset.versionId)}`;
    factorLibraryText(nameCell, "span", "factor-library-code", checkbox.dataset.entityId);
    factorLibraryText(row, "span", null, `${checkbox.dataset.versionId} / ${item.dataset_version_id || "—"}`);
    factorLibraryText(row, "span", null, item.category);
    factorLibraryText(row, "span", null, item.direction === "negative" ? "反向 ↓" : "正向 ↑");
    factorLibraryText(row, "span", "factor-library-formula", item.formula);
    factorLibraryText(row, "span", null, `${item.source || "—"} · ${item.frequency || "—"}`);
    factorLibraryText(row, "span", null, item.coverage == null ? "—" : `${(Number(item.coverage) * 100).toFixed(2)}%`);
    factorLibraryText(row, "span", null, item.missing_rows);
    const status = factorLibraryText(row, "span", `factor-library-status quality-${item.quality_status}`, "");
    factorLibraryText(status, "strong", null, item.status);
    factorLibraryText(status, "small", null, item.quality_status);
    factorLibraryText(row, "span", null, item.last_verified_at || "—");
    const actions = factorLibraryText(row, "span", "factor-library-actions", "");
    const view = factorLibraryText(actions, "a", null, "查看详情");
    view.href = link.href;
    const join = factorLibraryText(actions, "a", null, "加入回测");
    join.href = "#";
    join.addEventListener("click", (event) => {
      event.preventDefault();
      joinFactorVersionsToBacktest([factorLibraryReference(item)]);
    });
  });
}

async function loadFactorLibrary() {
  const loading = document.getElementById("factor-library-loading");
  const error = document.getElementById("factor-library-error");
  loading.classList.remove("hidden");
  error.classList.add("hidden");
  try {
    const response = await fetch(`/api/factors?${factorLibraryParams().toString()}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    renderFactorLibrary(payload.items || []);
  } catch (errorValue) {
    error.textContent = `因子库加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  } finally {
    loading.classList.add("hidden");
  }
}

function factorLibraryErrorMessage(payload, status) {
  const detail = payload?.detail && typeof payload.detail === "object" ? payload.detail : payload;
  const message = detail?.message || `HTTP ${status}`;
  const structured = detail?.details || detail?.errors;
  if (!structured) return message;
  const suffix = typeof structured === "string" ? structured : JSON.stringify(structured);
  return `${message}（${suffix}）`;
}

async function factorLibraryBatch(path, action) {
  const status = document.getElementById("factor-library-operation-status");
  const items = selectedFactorLibraryReferences();
  if (!items.length) {
    status.textContent = "请先选择因子版本。";
    status.classList.remove("hidden");
    return;
  }
  try {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({items})});
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    status.textContent = `${action}成功：${payload.count ?? payload.items?.length ?? 0} 个因子版本。`;
    status.classList.remove("hidden");
    await loadFactorLibrary();
  } catch (errorValue) {
    status.textContent = `${action}失败：${errorValue.message}`;
    status.classList.remove("hidden");
  }
}

async function joinFactorVersionsToBacktest(references) {
  const status = document.getElementById("factor-library-operation-status");
  if (!references || !references.length) {
    status.textContent = "请先选择因子版本。";
    status.classList.remove("hidden");
    return;
  }
  try {
    const response = await fetch("/api/backtest-drafts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({factor_version_ids: references.map((item) => ({factor_id: item.entity_id || item.factor_id, version_id: item.version_id}))})});
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    window.location.assign(`/backtests/new?draft_id=${encodeURIComponent(payload.draft_id)}`);
  } catch (errorValue) {
    status.textContent = `加入回测失败：${errorValue.message}`;
    status.classList.remove("hidden");
  }
}

async function importFactorLibraryDefinition() {
  const status = document.getElementById("factor-library-operation-status");
  let definition;
  try {
    definition = JSON.parse(document.getElementById("factor-library-import").value);
  } catch (_error) {
    status.textContent = "导入失败：JSON 格式无效。";
    status.classList.remove("hidden");
    return;
  }
  try {
    const response = await fetch("/api/factors/import", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(definition)});
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    status.textContent = `导入成功：${payload.factor_entity_id || payload.entity_id || "因子版本"}。`;
    status.classList.remove("hidden");
    await loadFactorLibrary();
  } catch (errorValue) {
    status.textContent = `导入失败：${errorValue.message}`;
    status.classList.remove("hidden");
  }
}

function loadFactorLibraryPage() {
  document.getElementById("factor-library-filter").addEventListener("click", loadFactorLibrary);
  document.getElementById("factor-library-backtest").addEventListener("click", () => joinFactorVersionsToBacktest(selectedFactorLibraryReferences()));
  document.getElementById("factor-library-diagnose").addEventListener("click", () => factorLibraryBatch("/api/factors/diagnose", "诊断"));
  document.getElementById("factor-library-publish").addEventListener("click", () => factorLibraryBatch("/api/factors/publish", "发布"));
  document.getElementById("factor-library-deprecate").addEventListener("click", () => factorLibraryBatch("/api/factors/deprecate", "弃用"));
  document.getElementById("factor-library-import-button").addEventListener("click", importFactorLibraryDefinition);
  loadFactorLibrary();
}

function factorVersionText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null || value === "" ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function factorVersionContext() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const factorIndex = parts.indexOf("factors");
  const factorId = parts[factorIndex + 1];
  const versionId = parts[factorIndex + 2] === "versions" ? parts[factorIndex + 3] : parts[factorIndex + 2];
  if (!factorId || !versionId) throw new Error("因子版本地址无效。");
  return {factorId: decodeURIComponent(factorId), versionId: decodeURIComponent(versionId)};
}

function factorVersionMetric(parent, label, value) {
  const item = factorVersionText(parent, "div", "factor-version-metric", "");
  factorVersionText(item, "span", null, label);
  factorVersionText(item, "strong", null, value);
}

function formatFactorVersionValue(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  return value == null ? "—" : value;
}

function renderFactorVersionDiagnostics(diagnostics) {
  const state = document.getElementById("factor-version-diagnostic-state");
  const container = document.getElementById("factor-version-diagnostics");
  container.replaceChildren();
  if (diagnostics.status !== "diagnosed") {
    state.textContent = "尚未诊断：覆盖率、分布、IC、分组收益、换手和稳定性均不会显示替代数值。";
    return;
  }
  state.textContent = `已诊断：${diagnostics.diagnosed_at || "—"}。所有统计来自本次真实抽样复算或已登记结果。`;
  const quality = diagnostics.quality || {};
  const metrics = factorVersionText(container, "div", "factor-version-grid", "");
  factorVersionMetric(metrics, "抽样覆盖率", quality.coverage == null ? "—" : `${(Number(quality.coverage) * 100).toFixed(2)}%`);
  factorVersionMetric(metrics, "抽样缺失行", quality.missing_rows);
  factorVersionMetric(metrics, "登记覆盖率", quality.registered_coverage == null ? "—" : `${(Number(quality.registered_coverage) * 100).toFixed(2)}%`);
  factorVersionMetric(metrics, "读取行数", quality.sample?.rows_read);
  factorVersionMetric(metrics, "P50", formatFactorVersionValue(quality.quantiles?.p50));
  factorVersionMetric(metrics, "均值", formatFactorVersionValue(quality.distribution?.mean));
  factorVersionMetric(metrics, "标准差", formatFactorVersionValue(quality.distribution?.std));
  const histogram = quality.distribution?.histogram;
  if (Array.isArray(histogram) && histogram.length) {
    const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chart.setAttribute("viewBox", "0 0 640 180");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "因子分布直方图");
    chart.classList.add("factor-version-histogram");
    const maxCount = Math.max(...histogram.map(item => Number(item.count) || 0), 1);
    const width = 600 / histogram.length;
    histogram.forEach((item, index) => {
      const count = Number(item.count) || 0;
      const height = (count / maxCount) * 132;
      const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bar.setAttribute("x", String(20 + index * width)); bar.setAttribute("y", String(150 - height));
      bar.setAttribute("width", String(Math.max(2, width - 4))); bar.setAttribute("height", String(height));
      bar.setAttribute("class", "factor-version-histogram-bar");
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${formatFactorVersionValue(item.left)} 至 ${formatFactorVersionValue(item.right)}：${count}`;
      bar.appendChild(title); chart.appendChild(bar);
    });
    container.appendChild(chart);
  }
  [
    ["每日 IC", quality.daily_ic, item => `均值 ${formatFactorVersionValue(item.mean)} · ${item.count ?? 0} 日`],
    ["分组收益", quality.group_returns, item => `${item.count ?? 0} 个真实截面`],
    ["换手", quality.turnover, item => `均值 ${formatFactorVersionValue(item.mean)} · ${item.count ?? 0} 日`],
    ["稳定性", quality.stability, item => `IC 标准差 ${formatFactorVersionValue(item.ic_std)}`],
  ].forEach(([label, item, detail]) => {
    const block = factorVersionText(container, "div", "factor-version-diagnostic-item", "");
    factorVersionText(block, "strong", null, label);
    if (!item || item.status !== "available") factorVersionText(block, "span", null, item?.message || "真实数据不足，未诊断。");
    else factorVersionText(block, "span", null, detail(item));
  });
  if (diagnostics.artifact?.artifact_id) {
    const link = factorVersionText(container, "a", "button-link", `下载本次诊断 Artifact：${diagnostics.artifact.display_name}`);
    link.href = `/api/artifacts/${encodeURIComponent(diagnostics.artifact.artifact_id)}/download`;
  }
}

function renderFactorVersion(detail) {
  const definition = detail.definition;
  document.getElementById("factor-version-title").textContent = `${detail.factor.name} · ${definition.factor_version_id}`;
  const definitionBox = document.getElementById("factor-version-definition");
  definitionBox.replaceChildren();
  [["代码", definition.factor_entity_id], ["分类", definition.category], ["方向", definition.direction], ["频率", definition.frequency], ["缺失处理", definition.missing_policy], ["作者", definition.author || "未登记"], ["状态", `${definition.status} / ${definition.quality_status}`], ["输入字段", definition.input_fields.join(", ")], ["观察窗口", definition.pit_lineage?.window_mode || "未登记"]].forEach(([label, value]) => factorVersionMetric(definitionBox, label, value));
  document.getElementById("factor-version-formula").textContent = definition.formula;
  const lineage = detail.lineage || {};
  const lineageBox = document.getElementById("factor-version-lineage");
  lineageBox.replaceChildren();
  [["数据集版本", `${lineage.dataset_id || "—"} · ${lineage.dataset_version_id || "—"}`], ["因子文件位置", lineage.storage_alias || "未登记"], ["上游版本", (lineage.upstream_factor_versions || []).map(item => `${item.entity_id}:${item.version_id}`).join(", ") || "无"], ["代码哈希", lineage.code_hash], ["生成运行", lineage.generation_run_id], ["PIT 规则", lineage.pit_policy], ["PIT 快照", lineage.pit_lineage?.snapshot]].forEach(([label, value]) => factorVersionMetric(lineageBox, label, value));
  const artifactBox = document.getElementById("factor-version-artifact");
  artifactBox.replaceChildren();
  if (lineage.artifact?.artifact_id) {
    const link = factorVersionText(artifactBox, "a", "button-link", `下载 Artifact：${lineage.artifact.display_name}`);
    link.href = `/api/artifacts/${encodeURIComponent(lineage.artifact.artifact_id)}/download`;
  } else factorVersionText(artifactBox, "p", "state", "尚无已登记 Artifact。 ");
  renderFactorVersionDiagnostics(detail.diagnostics || {});
  const history = document.getElementById("factor-version-history");
  history.replaceChildren();
  (detail.history?.items || []).forEach((item) => {
    const row = factorVersionText(history, "div", "factor-version-history-row", "");
    const link = factorVersionText(row, "a", null, item.version_id);
    link.href = `/factors/${encodeURIComponent(detail.factor.entity_id)}/versions/${encodeURIComponent(item.version_id)}`;
    factorVersionText(row, "span", null, `${item.status} / ${item.quality_status}`);
    factorVersionText(row, "span", null, item.last_verified_at || "未验证");
    factorVersionText(row, "span", null, item.origin || "—");
  });
}

async function loadFactorVersionDetail() {
  const loading = document.getElementById("factor-version-loading");
  const error = document.getElementById("factor-version-error");
  try {
    const context = factorVersionContext();
    const response = await fetch(`/api/factors/${encodeURIComponent(context.factorId)}/versions/${encodeURIComponent(context.versionId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    renderFactorVersion(payload);
    document.getElementById("factor-version-content").classList.remove("hidden");
  } catch (errorValue) {
    error.textContent = `因子版本加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  } finally { loading.classList.add("hidden"); }
}

async function factorVersionAction(action) {
  const operation = document.getElementById("factor-version-operation");
  const context = factorVersionContext();
  try {
    let response;
    if (action === "backtest") response = await fetch("/api/backtest-drafts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({factor_version_ids: [{factor_id: context.factorId, version_id: context.versionId}]})});
    else response = await fetch(`/api/factors/${encodeURIComponent(context.factorId)}/versions/${encodeURIComponent(context.versionId)}/${action}`, {method: "POST"});
    const payload = await response.json();
    if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
    if (action === "backtest") { window.location.assign(`/backtests/new?draft_id=${encodeURIComponent(payload.draft_id)}`); return; }
    if (action === "copy") { window.location.assign(`/factors/${encodeURIComponent(payload.factor_entity_id)}/versions/${encodeURIComponent(payload.factor_version_id)}`); return; }
    operation.textContent = "真实诊断已完成；详情已刷新。";
    operation.classList.remove("hidden");
    await loadFactorVersionDetail();
  } catch (errorValue) { operation.textContent = `操作失败：${errorValue.message}`; operation.classList.remove("hidden"); }
}

function loadFactorVersionDetailPage() {
  document.getElementById("factor-version-diagnose").addEventListener("click", () => factorVersionAction("diagnose"));
  document.getElementById("factor-version-copy").addEventListener("click", () => factorVersionAction("copy"));
  document.getElementById("factor-version-backtest").addEventListener("click", () => factorVersionAction("backtest"));
  loadFactorVersionDetail();
}

function exportFactorSample() {
  const factor = document.getElementById("factor-filter").value;
  if (!factor) return;
  const params = factorParams();
  params.set("factor", factor);
  params.set("max_rows", "5000");
  window.location.assign(`/api/factor-data/export.csv?${params.toString()}`);
}

let activeResearchDraft = null;
let activeResearchType = "manual";

function researchText(parent, tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = value == null ? "—" : String(value);
  parent.appendChild(element);
  return element;
}

function researchQueryParams() {
  const params = new URLSearchParams();
  const query = document.getElementById("research-query")?.value.trim();
  const type = document.getElementById("research-type")?.value;
  const status = document.getElementById("research-status")?.value;
  if (query) params.set("q", query);
  if (type) params.set("research_type", type);
  if (status) params.set("status", status);
  params.set("page_size", "50");
  return params;
}

function renderResearchRuns(payload) {
  const table = document.getElementById("research-runs");
  const empty = document.getElementById("research-empty");
  table.replaceChildren();
  empty.classList.toggle("hidden", payload.items.length !== 0);
  payload.items.forEach((run) => {
    const row = researchText(table, "div", "research-run-row", "");
    const link = researchText(row, "a", "research-run-name", run.name);
    link.href = run.detail_url;
    researchText(row, "span", null, `${run.run_id} · ${run.research_type === "manual" ? "手动" : "自动"}`);
    researchText(row, "span", `research-status status-${run.status}`, run.status);
    researchText(row, "span", null, `创建 ${run.created_at} · ${run.duration_seconds == null ? "未完成" : `${run.duration_seconds} 秒`}`);
    researchText(row, "span", null, `数据版本 ${run.dataset_version_id || "—"}`);
    const summary = Object.entries(run.summary || {}).map(([key, value]) => `${key}: ${value}`).join(" · ");
    researchText(row, "span", null, summary || "暂无结果摘要");
  });
}

async function loadResearchRuns() {
  const loading = document.getElementById("research-loading");
  const error = document.getElementById("research-error");
  try {
    const [runsResponse, alertsResponse] = await Promise.all([
      fetch(`/api/research-runs?${researchQueryParams().toString()}`),
      fetch("/api/datasets/quality-alerts"),
    ]);
    if (!runsResponse.ok) throw new Error(`HTTP ${runsResponse.status}`);
    renderResearchRuns(await runsResponse.json());
    const alerts = document.getElementById("research-alerts");
    alerts.replaceChildren();
    if (alertsResponse.ok) {
      const values = await alertsResponse.json();
      if (values.length) values.forEach((item) => researchText(alerts, "p", "alert-row", item.message));
      else researchText(alerts, "p", null, "暂无质量告警。");
    }
    loading.classList.add("hidden");
  } catch (errorValue) {
    loading.classList.add("hidden");
    error.textContent = `研究清单加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

function populateResearchFactors(items) {
  const select = document.getElementById("manual-factor");
  select.replaceChildren();
  items.filter((item) => item.status === "published").forEach((item) => {
    const option = document.createElement("option");
    option.value = `${item.factor_entity_id}::${item.factor_version_id}`;
    option.dataset.factorEntityId = item.factor_entity_id;
    option.dataset.factorVersionId = item.factor_version_id;
    option.dataset.datasetId = item.dataset_id;
    option.dataset.datasetVersionId = item.dataset_version_id;
    option.textContent = `${item.name} · ${item.factor_version_id}`;
    option.dataset.datasetVersionId = item.dataset_version_id;
    select.appendChild(option);
  });
  const updateLineage = () => {
    const selected = select.selectedOptions[0];
    const item = items.find((candidate) => selected && candidate.factor_entity_id === selected.dataset.factorEntityId && candidate.factor_version_id === selected.dataset.factorVersionId && candidate.status === "published");
    document.getElementById("research-lineage").textContent = item ? `已锁定 DatasetVersion：${item.dataset_id} · ${item.dataset_version_id}` : "没有可用的已发布因子版本。";
  };
  select.addEventListener("change", updateLineage);
  updateLineage();
}

async function loadResearchForm() {
  const formPanel = document.getElementById("research-form-panel");
  if (!formPanel) return;
  formPanel.classList.remove("hidden");
  try {
    const response = await fetch("/api/factor-data/catalog");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    populateResearchFactors(await response.json());
  } catch (errorValue) {
    const error = document.getElementById("research-error");
    error.textContent = `已发布因子加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

async function loadResearchDraft() {
  const sourceId = new URLSearchParams(window.location.search).get("copy_from_run_id");
  if (!sourceId) return;
  try {
    const response = await fetch(`/api/research-runs/${encodeURIComponent(sourceId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    activeResearchDraft = {name: run.name, research_type: run.research_type, config: JSON.parse(JSON.stringify(run.config))};
    activeResearchType = run.research_type;
    document.getElementById("research-form-title").textContent = run.research_type === "automatic" ? "自动挖掘因子草稿" : "新建单因子诊断草稿";
    document.getElementById("draft-state").classList.remove("hidden");
    document.getElementById("draft-source").textContent = `源研究：${run.name} · 类型：${run.research_type === "automatic" ? "自动挖掘" : "手动建立"}`;
    document.getElementById("draft-source").classList.remove("hidden");
    document.getElementById("draft-config").textContent = JSON.stringify(run.config, null, 2);
    document.getElementById("draft-config").classList.remove("hidden");
    document.getElementById("research-name").value = `复制：${run.name}`;
    const sourceFactor = run.config.factor_versions?.[0];
    const factorSelect = document.getElementById("manual-factor");
    if (sourceFactor) factorSelect.value = `${sourceFactor.factor_id}::${sourceFactor.version_id}`;
    if (factorSelect.value !== `${sourceFactor?.factor_id}::${sourceFactor?.version_id}`) {
      factorSelect.disabled = true;
      document.getElementById("research-lineage").textContent = "源因子版本未处于已发布状态，不能提交草稿。";
    }
  } catch (errorValue) {
    const error = document.getElementById("research-error");
    error.textContent = `草稿加载失败：${errorValue.message}`;
    error.classList.remove("hidden");
  }
}

async function loadResearchPage() {
  const path = window.location.pathname;
  const hasDraft = new URLSearchParams(window.location.search).has("copy_from_run_id");
  const formMode = path.endsWith("/manual") || path.endsWith("/auto") || hasDraft;
  if (formMode) {
    activeResearchType = path.endsWith("/auto") ? "automatic" : "manual";
    document.getElementById("research-page-title").textContent = activeResearchType === "manual" ? "手动建立因子" : "自动挖掘因子";
    document.getElementById("research-form-title").textContent = activeResearchType === "manual" ? "新建单因子诊断" : "自动挖掘因子草稿";
    document.getElementById("research-form-description").textContent = activeResearchType === "manual" ? "选择一个已发布 FactorVersion，并锁定一致的 DatasetVersion。" : "以当前已发布因子作为自动挖掘种子；本页只保存草稿，不启动执行器。";
    await loadResearchForm();
    await loadResearchDraft();
  }
  await loadResearchRuns();
}

async function submitResearchDraft(event) {
  event.preventDefault();
  const select = document.getElementById("manual-factor");
  const selected = select.selectedOptions[0];
  if (!selected) return;
  const catalog = await fetch("/api/factor-data/catalog").then((response) => response.json());
  const factor = catalog.find((item) => item.factor_entity_id === selected.dataset.factorEntityId && item.factor_version_id === selected.dataset.factorVersionId && item.status === "published");
  if (!factor) return;
  const result = document.getElementById("research-error");
  let config;
  let researchType = activeResearchType;
  if (activeResearchDraft) {
    config = JSON.parse(JSON.stringify(activeResearchDraft.config));
    researchType = activeResearchDraft.research_type;
    const sourceFactor = config.factor_versions?.[0];
    if (!sourceFactor || sourceFactor.factor_id !== factor.factor_entity_id || sourceFactor.version_id !== factor.factor_version_id || config.dataset_id !== factor.dataset_id || config.dataset_version_id !== factor.dataset_version_id) {
      result.textContent = "源因子版本或 DatasetVersion 不一致，不能提交。";
      result.classList.remove("hidden");
      return;
    }
  } else {
    const now = new Date().toISOString();
    config = {
      dataset_id: factor.dataset_id, dataset_version_id: factor.dataset_version_id,
      factor_versions: [{factor_id: factor.factor_entity_id, version_id: factor.factor_version_id}],
      sample: {date_from: "20170101", date_to: "20241231", universe: "中国A股（SH/SZ）"},
      filters: {st_status: 0, suspended: false}, filter_snapshot: {st_status: 0, suspended: false, captured_at: now},
      label: {definition: "t+1 open -> t+2 close", price_fields: ["hfq_open", "hfq_close"]},
      pit_snapshot: {rule: "published factor and dataset versions only", captured_at: now},
    };
  }
  try {
    const response = await fetch("/api/research-runs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: document.getElementById("research-name").value, research_type: researchType, config})});
    result.textContent = response.ok ? "研究草稿已保存。" : "研究草稿保存失败。";
  } catch (errorValue) { result.textContent = `研究草稿保存失败：${errorValue.message}`; }
  result.classList.remove("hidden");
}

async function loadResearchDetail() {
  const runId = window.location.pathname.split("/").filter(Boolean).pop();
  const loading = document.getElementById("research-run-loading");
  const error = document.getElementById("research-run-error");
  try {
    const response = await fetch(`/api/research-runs/${encodeURIComponent(runId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    document.getElementById("research-run-title").textContent = `${run.name} · 研究运行详情`;
    const meta = document.getElementById("research-run-meta");
    [["运行 ID", run.run_id], ["类型", run.research_type === "manual" ? "手动建立因子" : "自动挖掘因子"], ["状态", run.status], ["创建时间", run.created_at], ["完成时间", run.finished_at], ["耗时", run.duration_seconds == null ? "未完成" : `${run.duration_seconds} 秒`], ["数据版本", `${run.dataset_id} · ${run.dataset_version_id}`], ["因子版本", (run.factor_versions || []).map((item) => `${item.factor_id}:${item.version_id}`).join("，")]].forEach(([label, value]) => { const item = researchText(meta, "div", "research-meta-item", ""); researchText(item, "span", null, label); researchText(item, "strong", null, value); });
    document.getElementById("research-run-config").textContent = JSON.stringify(run.config, null, 2);
    const summary = document.getElementById("research-run-summary");
    Object.entries(run.summary || {}).forEach(([key, value]) => researchText(summary, "p", null, `${key}: ${value}`));
    if (!Object.keys(run.summary || {}).length) researchText(summary, "p", null, "暂无结果摘要。");
    const artifacts = document.getElementById("research-run-artifacts");
    if (!run.artifacts.length) researchText(artifacts, "p", null, "暂无中文 Artifact。");
    run.artifacts.forEach((artifact) => { const item = researchText(artifacts, "div", "research-artifact", ""); researchText(item, "strong", null, artifact.display_name); researchText(item, "span", null, `${artifact.artifact_role} · ${artifact.original_name} · ${artifact.size_bytes} 字节`); });
    const detailConfig = run.config;
    [["样本", JSON.stringify(detailConfig.sample)], ["过滤快照", JSON.stringify(detailConfig.filter_snapshot)], ["PIT 快照", JSON.stringify(detailConfig.pit_snapshot)], ["标签口径", `${detailConfig.label.definition} · ${detailConfig.label.price_fields.join(" / ")}`]].forEach(([label, value]) => { const item = researchText(meta, "div", "research-meta-item", ""); researchText(item, "span", null, label); researchText(item, "strong", null, value); });
    loading.classList.add("hidden");
    document.getElementById("research-run-content").classList.remove("hidden");
    document.getElementById("research-copy-config").addEventListener("click", async () => { const errorBox = document.getElementById("research-run-error"); try { const copied = await fetch(`/api/research-runs/${encodeURIComponent(runId)}/copy-config`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})}); const payload = await copied.json(); if (!copied.ok) throw new Error(payload.message || `HTTP ${copied.status}`); window.location.assign(payload.target_url); } catch (errorValue) { errorBox.textContent = `复制配置失败：${errorValue.message}`; errorBox.classList.remove("hidden"); } });
  } catch (errorValue) { loading.classList.add("hidden"); error.textContent = `研究详情加载失败：${errorValue.message}`; error.classList.remove("hidden"); }
}

function multiSelectRoot(root) {
  return typeof root === "string" ? document.getElementById(root) : root;
}

function multiSelectOptionLabel(input) {
  if (input.dataset.label) return input.dataset.label;
  const text = (input.closest("label")?.textContent || input.value).replace(/\s+/g, " ").trim();
  return text;
}

function multiSelectItems(ms) {
  return [...ms.querySelectorAll("input[type='checkbox']")].filter((input) => input.dataset.role !== "all" && input.value !== "ALL");
}

function bindMultiSelect(root) {
  const ms = multiSelectRoot(root);
  if (!ms || ms.dataset.bound === "1") return;
  const toggle = ms.querySelector(".multi-select-toggle");
  const dropdown = ms.querySelector(".multi-select-dropdown");
  if (!toggle || !dropdown) return;
  ms.dataset.bound = "1";
  const emptyText = ms.dataset.empty || "未选择";
  const allText = ms.dataset.all || "全选";
  const refresh = () => {
    const items = multiSelectItems(ms);
    const selected = items.filter((cb) => cb.checked);
    const allInput = ms.querySelector("input[data-role='all'], input[value='ALL']");
    if (allInput) allInput.checked = items.length > 0 && selected.length === items.length;
    if (!selected.length) toggle.textContent = emptyText;
    else if (selected.length === items.length) toggle.textContent = allText;
    else {
      const names = selected.map(multiSelectOptionLabel);
      toggle.textContent = names.some((name) => name.length > 10) || names.length > 2 ? `已选 ${selected.length} 项` : names.join(", ");
    }
  };
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = dropdown.classList.contains("hidden");
    document.querySelectorAll(".multi-select-dropdown").forEach((node) => node.classList.add("hidden"));
    if (willOpen) dropdown.classList.remove("hidden");
  });
  dropdown.addEventListener("change", (event) => {
    const allInput = ms.querySelector("input[data-role='all'], input[value='ALL']");
    if (event.target === allInput) multiSelectItems(ms).forEach((cb) => { cb.checked = allInput.checked; });
    refresh();
  });
  if (!document.documentElement.dataset.multiSelectDocBound) {
    document.documentElement.dataset.multiSelectDocBound = "1";
    document.addEventListener("click", (event) => {
      document.querySelectorAll(".multi-select").forEach((node) => {
        if (!node.contains(event.target)) node.querySelector(".multi-select-dropdown")?.classList.add("hidden");
      });
    });
  }
  refresh();
}

function bindMarketMultiSelect(rootId) {
  bindMultiSelect(rootId);
}

function selectedMultiSelectValues(rootId) {
  const ms = multiSelectRoot(rootId);
  return ms ? multiSelectItems(ms).filter((cb) => cb.checked).map((cb) => cb.value) : [];
}

function selectedMarkets(rootId) {
  return selectedMultiSelectValues(rootId);
}

function miningDate(id) {
  return (document.getElementById(id)?.value || "").trim().replace(/-/g, "");
}

function manualFactorBody() {
  return {
    name: document.getElementById("manual-factor-name").value.trim(),
    category: document.getElementById("manual-factor-category").value.trim(),
    dataset_id: document.getElementById("manual-factor-dataset").value.trim(),
    dataset_version_id: document.getElementById("manual-factor-dataset-version").value.trim(),
    input_fields: document.getElementById("manual-factor-fields").value.split(",").map(value => value.trim()).filter(Boolean),
    formula: document.getElementById("manual-factor-formula").value.trim(),
    direction: document.getElementById("manual-factor-direction").value,
    missing_policy: document.getElementById("manual-factor-missing").value,
    markets: selectedMarkets("manual-factor-market"),
    date_from: miningDate("manual-factor-date-from"),
    date_to: miningDate("manual-factor-date-to"),
  };
}

function setWorkflowStep(stepsId, activeStep) {
  const list = document.getElementById(stepsId);
  if (!list) return;
  let reached = false;
  [...list.children].forEach((step) => {
    const current = step.dataset.step === activeStep;
    step.classList.toggle("current", current);
    step.classList.toggle("done", !current && !reached);
    reached = reached || current;
  });
}

async function alignDatasetVersionInput(datasetIdInputId, versionInputId) {
  const datasetId = document.getElementById(datasetIdInputId).value.trim();
  if (!datasetId) return;
  try {
    const response = await fetch("/api/datasets?page_size=200");
    if (!response.ok) return;
    const versions = (await response.json()).items
      .filter((item) => item.entity_id === datasetId)
      .map((item) => item.version_id);
    if (!versions.length) return;
    const current = document.getElementById(versionInputId).value.trim();
    if (!versions.includes(current)) document.getElementById(versionInputId).value = versions[0];
  } catch (errorValue) {
    // Registry is offline; keep the form value so the user can retry.
  }
}

async function manualFactorRequest(path, method, body) {
  const response = await fetch(path, {method, headers: {"Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  const payload = await response.json();
  if (!response.ok) throw new Error(factorLibraryErrorMessage(payload, response.status));
  return payload;
}

function loadManualFactorPage() {
  const error = document.getElementById("manual-factor-error");
  const result = document.getElementById("manual-factor-result");
  const previewBox = document.getElementById("manual-factor-preview-box");
  const previewOutput = document.getElementById("manual-factor-preview-output");
  let draft = null;
  bindMarketMultiSelect("manual-factor-market");
  alignDatasetVersionInput("manual-factor-dataset", "manual-factor-dataset-version");
  loadCanonicalFactorPack();
  document.getElementById("manual-factor-load-fields").addEventListener("click", async () => {
    try { const body = manualFactorBody(); const payload = await manualFactorRequest(`/api/factor-drafts/fields?dataset_id=${encodeURIComponent(body.dataset_id)}&dataset_version_id=${encodeURIComponent(body.dataset_version_id)}`, "GET"); document.getElementById("manual-factor-field-list").textContent = `登记字段：${payload.fields.join("，")}`; }
    catch (errorValue) { error.textContent = `字段加载失败：${errorValue.message}`; error.classList.remove("hidden"); }
  });
  document.getElementById("manual-factor-preview").addEventListener("click", async () => {
    try { error.classList.add("hidden"); setWorkflowStep("manual-factor-steps", "preview"); const payload = await manualFactorRequest("/api/factor-drafts/preview", "POST", manualFactorBody()); previewOutput.textContent = JSON.stringify(payload, null, 2); previewBox.classList.remove("hidden"); }
    catch (errorValue) { error.textContent = `公式校验失败：${errorValue.message}`; error.classList.remove("hidden"); }
  });
  document.getElementById("manual-factor-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try { draft = await manualFactorRequest("/api/factor-drafts", "POST", manualFactorBody()); result.textContent = `草稿已保存：${draft.factor_entity_id} · ${draft.factor_version_id}`; result.classList.remove("hidden"); document.getElementById("manual-factor-diagnose").classList.remove("hidden"); document.getElementById("manual-factor-publish").classList.remove("hidden"); }
    catch (errorValue) { error.textContent = `草稿保存失败：${errorValue.message}`; error.classList.remove("hidden"); }
  });
  document.getElementById("manual-factor-diagnose").addEventListener("click", async () => { try { setWorkflowStep("manual-factor-steps", "diagnose"); const payload = await manualFactorRequest(`/api/factor-drafts/${encodeURIComponent(draft.factor_entity_id)}/${encodeURIComponent(draft.factor_version_id)}/diagnose`, "POST"); previewOutput.textContent = JSON.stringify(payload.preview, null, 2); result.textContent = `诊断完成：质量状态 ${payload.quality_status}`; result.classList.remove("hidden"); } catch (errorValue) { error.textContent = `诊断失败：${errorValue.message}`; error.classList.remove("hidden"); } });
  document.getElementById("manual-factor-publish").addEventListener("click", async () => { try { await manualFactorRequest(`/api/factor-drafts/${encodeURIComponent(draft.factor_entity_id)}/${encodeURIComponent(draft.factor_version_id)}/publish`, "POST"); result.textContent = "因子版本已发布。"; result.classList.remove("hidden"); setWorkflowStep("manual-factor-steps", "publish"); } catch (errorValue) { error.textContent = `发布失败：${errorValue.message}`; error.classList.remove("hidden"); } });
}

function packStatusLabel(status) {
  if (status === "published") return "已发布";
  if (status === "draft") return "草稿";
  if (status === "validated") return "已验证";
  return "未入库";
}

function renderCanonicalFactorPack(items) {
  const list = document.getElementById("canonical-factor-pack-list");
  if (!list) return;
  list.textContent = "";
  (items || []).forEach((item) => {
    const row = document.createElement("div");
    row.className = "form-note";
    row.textContent = `${item.name} · ${item.field} · ${item.formula} · ${packStatusLabel(item.status)}`;
    list.append(row);
  });
}

async function loadCanonicalFactorPack() {
  const list = document.getElementById("canonical-factor-pack-list");
  const result = document.getElementById("canonical-factor-pack-result");
  const button = document.getElementById("canonical-factor-pack-ingest");
  if (!list || !button) return;
  const datasetId = document.getElementById("manual-factor-dataset")?.value?.trim() || "ds_canonical_market";
  const datasetVersionId = document.getElementById("manual-factor-dataset-version")?.value?.trim() || "current";
  try {
    const payload = await manualFactorRequest("/api/factor-packs/canonical", "GET");
    renderCanonicalFactorPack(payload.items);
  } catch (errorValue) {
    list.textContent = `公式包清单加载失败：${errorValue.message}`;
  }
  if (button.dataset.bound === "1") return;
  button.dataset.bound = "1";
  button.addEventListener("click", async () => {
    try {
      button.disabled = true;
      result.classList.remove("hidden");
      const listed = await manualFactorRequest("/api/factor-packs/canonical", "GET");
      const items = listed.items || [];
      let published = 0;
      let skipped = 0;
      let failed = 0;
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        result.textContent = `正在计算验证 ${index + 1}/${items.length}：${item.name}`;
        const payload = await manualFactorRequest("/api/factor-packs/canonical/ingest", "POST", {
          field: item.field,
          dataset_id: datasetId,
          dataset_version_id: datasetVersionId,
        });
        const row = (payload.items || [])[0] || {};
        if (row.status === "published") published += 1;
        else if (row.status === "skipped") skipped += 1;
        else failed += 1;
      }
      const refreshed = await manualFactorRequest("/api/factor-packs/canonical", "GET");
      renderCanonicalFactorPack(refreshed.items);
      result.textContent = `完成：新发布 ${published}，跳过 ${skipped}，失败 ${failed}。可在因子数据页查看 IC。`;
    } catch (errorValue) {
      result.textContent = `入库失败：${errorValue.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

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
      const response = await fetch("/api/factor-jobs?page_size=50");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      const items = payload.items || [];
      empty.classList.toggle("hidden", items.length !== 0);
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
      const items = payload.items || [];
      if (!items.length) {
        researchText(box, "div", "state", "这个任务还没有候选公式。");
        return;
      }
      items.forEach((item) => {
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
        checkbox.checked = alreadyIn || (kept && !unevaluable);
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
    } catch (errorValue) {
      showError(`任务详情加载失败：${errorValue.message}`);
    }
  }

  document.getElementById("factor-jobs-select-kept")?.addEventListener("click", () => {
    error.classList.add("hidden");
    result.classList.add("hidden");
    const boxes = [...document.querySelectorAll("#factor-jobs-items input[data-kept='1']")].filter((input) => !input.disabled);
    if (!boxes.length) {
      showError("没有还能勾选的公式。未达标的不能入库；已经入库的也不用再勾。");
      return;
    }
    boxes.forEach((input) => { input.checked = true; });
    result.textContent = `已勾选 ${boxes.length} 条可入库公式。再点「入库并计算分析」才会写入并算 IC。`;
    result.classList.remove("hidden");
  });

  document.getElementById("factor-jobs-enable")?.addEventListener("click", async () => {
    error.classList.add("hidden");
    const enableButton = document.getElementById("factor-jobs-enable");
    const checked = [...document.querySelectorAll("#factor-jobs-items input[type='checkbox']:checked:not(:disabled)")].map((input) => input.value);
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


function fileConfigAllowedPath() {
  const path = window.location.pathname;
  return path === "/data" || path === "/research/factors" || path === "/factors" || path === "/factors/new/manual" || path === "/research/factor-mining";
}

function setupFileConfigDialog() {
  if (!fileConfigAllowedPath() || document.getElementById("file-config-dialog")) return;
  const main = document.querySelector("main");
  if (!main) return;
  const openButton = document.createElement("button");
  openButton.type = "button";
  openButton.id = "file-config-open";
  openButton.className = "btn file-config-open";
  openButton.textContent = "文件目录配置";
  main.prepend(openButton);
  openButton.addEventListener("click", async () => {
    if (document.getElementById("file-config-dialog")) return;
    const overlay = document.createElement("div");
    overlay.className = "file-config-overlay";
    overlay.id = "file-config-dialog";
    const panel = document.createElement("div");
    panel.className = "file-config-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "文件目录配置");
    panel.replaceChildren();
    const title = document.createElement("h3");
    title.textContent = "文件目录配置";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.textContent = "关闭";
    closeButton.className = "btn";
    closeButton.addEventListener("click", () => overlay.remove());
    const head = document.createElement("div");
    head.className = "file-config-head";
    head.append(title, closeButton);
    panel.append(head);
    const list = document.createElement("div");
    list.className = "file-config-list";
    list.textContent = "正在加载路径…";
    panel.append(list);
    const editRow = document.createElement("div");
    editRow.className = "file-config-edit";
    const editLabel = document.createElement("label");
    editLabel.textContent = "Raw 根目录（可修改，保存后自动更新）";
    const editInput = document.createElement("input");
    editInput.type = "text";
    editInput.id = "file-config-raw";
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.textContent = "保存配置";
    const editStatus = document.createElement("span");
    editStatus.className = "state";
    editRow.append(editLabel, editInput, editButton, editStatus);
    panel.append(editRow);
    overlay.append(panel);
    document.body.append(overlay);
    async function load() {
      const response = await fetch("/api/settings");
      const payload = await response.json();
      list.replaceChildren();
      const paths = payload.paths || {};
      Object.entries(paths).forEach(([key, value]) => {
        const row = document.createElement("div");
        row.className = "file-config-path";
        const name = document.createElement("span");
        name.textContent = key;
        const pathText = document.createElement("code");
        pathText.textContent = value;
        pathText.title = value;
        row.append(name, pathText);
        list.append(row);
      });
      editInput.value = paths.raw_root || "";
      editStatus.textContent = "";
    }
    await load();
    editButton.addEventListener("click", async () => {
      const pathValue = editInput.value.trim();
      if (!pathValue) return;
      const response = await fetch("/api/settings/raw-root", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({path: pathValue})});
      const payload = await response.json();
      if (!response.ok) {
        editStatus.textContent = payload.message || "保存失败";
        return;
      }
      editStatus.textContent = "已保存并自动更新，正在刷新路径…";
      await load();
      if (window.location.pathname === "/data") {
        loadQualityOverview();
        loadDatasets();
      }
    });
  });
}

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

async function loadModelVersionPage() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const entityId = decodeURIComponent(parts[1] || "");
  const versionId = decodeURIComponent(parts[3] || "");
  const detail = document.getElementById("detail");
  const identity = document.getElementById("identity");
  const title = document.getElementById("title");
  const toBacktest = document.getElementById("to-backtest");
  try {
    await fetchModelKindCatalog();
    const response = await fetch(`/api/models/${encodeURIComponent(entityId)}/versions/${encodeURIComponent(versionId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "模型版本加载失败");
    const config = payload.config || {};
    const kind = config.kind || "";
    const params = config.hyperparameters || {};
    const kindName = modelKindCatalog[kind]?.name || kind || "—";
    if (title) title.textContent = `${kindName} · ${payload.version_id || versionId}`;
    if (identity) identity.textContent = `${payload.entity_id || entityId} · ${payload.version_id || versionId} · ${modelStatusText(payload.status)}`;
    renderDefinitionList(detail, [
      ["种类", kindName],
      ["版本状态", modelStatusText(payload.status)],
      ...kindParamRows(kind, params).map(([label, value]) => [label, value]),
    ]);
    if (toBacktest) {
      const query = new URLSearchParams();
      if (kind) query.set("kind", kind);
      query.set("model", `${payload.entity_id || entityId}::${payload.version_id || versionId}`);
      query.set("hp", JSON.stringify(params));
      toBacktest.href = `/backtests/new?${query.toString()}`;
    }
  } catch (error) {
    if (identity) identity.textContent = error.message || "加载失败";
    if (detail) detail.textContent = error.message || "加载失败";
  }
}

async function loadModelRunPage() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const runId = decodeURIComponent(parts[2] || "");
  const detail = document.getElementById("detail");
  const identity = document.getElementById("identity");
  const title = document.getElementById("title");
  const foldsNode = document.getElementById("train-folds");
  try {
    await fetchModelKindCatalog();
    const response = await fetch(`/api/models/runs/${encodeURIComponent(runId)}`);
    const payload = await response.json();
    if (!response.ok) {
      const raw = payload.message || "训练运行加载失败";
      throw new Error(raw === "training run not found" ? "找不到这次训练运行。" : raw);
    }
    let version = null;
    if (payload.model_entity_id && payload.model_version_id) {
      const versionResponse = await fetch(`/api/models/${encodeURIComponent(payload.model_entity_id)}/versions/${encodeURIComponent(payload.model_version_id)}`);
      if (versionResponse.ok) version = await versionResponse.json();
    }
    const config = payload.config || {};
    const versionConfig = version?.config || {};
    const kind = versionConfig.kind || config.kind || "";
    const params = {...(versionConfig.hyperparameters || {}), ...(config.hyperparameters || {})};
    const walkMeta = (payload.metrics && payload.metrics.model && payload.metrics.model.walk_forward) || {};
    const walk = config.walk_forward || params.walk_forward || walkMeta.mode || "once";
    const lookback = config.train_lookback_months ?? params.train_lookback_months ?? config.train_period_months ?? params.train_period_months ?? walkMeta.train_period_months;
    const folds = collectTrainFolds(payload);
    const first = folds[0] || {};
    const kindName = modelKindCatalog[kind]?.name || kind || "—";
    if (title) title.textContent = `训练运行 · ${payload.run_id || runId}`;
    if (identity) identity.textContent = `${payload.run_id || runId} · ${modelStatusText(payload.status)}`;
    renderDefinitionList(detail, [
      ["种类", kindName],
      ["训练方式", walkForwardText(walk)],
      ["回看月数", walk === "lookback" || walk === "rolling" || walk === "monthly" ? (lookback ?? "—") : "—"],
      ["哪一折", first.month || first.fold || (folds.length ? "1" : "—")],
      ["训练区间", formatTrainWindow(first.train_start, first.train_end)],
      ["训练行数", first.train_rows == null || first.train_rows === "" ? "—" : first.train_rows],
      ["模型", payload.model_entity_id || "—"],
      ["版本", payload.model_version_id || "—"],
    ]);
    renderTrainFoldsTable(foldsNode, folds);
  } catch (error) {
    if (identity) identity.textContent = error.message || "加载失败";
    if (detail) detail.textContent = error.message || "加载失败";
    if (foldsNode) foldsNode.textContent = error.message || "加载失败";
  }
}

window.addEventListener("DOMContentLoaded", () => {
  setupFileConfigDialog();
  if (window.location.pathname === "/kline") {
    document.getElementById("kline-query").addEventListener("click", loadKline);
    document.getElementById("kline-export").addEventListener("click", exportKline);
    const query = new URLSearchParams(window.location.search);
    document.getElementById("kline-symbol").value = query.get("ts_code") || "000001.SZ";
    loadKlineVersions().then(loadKline).catch(loadKline);
  } else if (window.location.pathname === "/data") {
    document.getElementById("apply-dataset-filters").addEventListener("click", loadDatasets);
    document.getElementById("rescan-datasets").addEventListener("click", rescanDatasets);
    loadDatasets();
  } else if (window.location.pathname === "/factors" || window.location.pathname === "/data/factors") {
    document.getElementById("factor-query")?.addEventListener("click", queryFactorSample);
    document.getElementById("factor-export")?.addEventListener("click", exportFactorSample);
    loadFactors();
  } else if (window.location.pathname === "/factors/new" || window.location.pathname === "/factors/new/manual") {
    loadManualFactorPage();
  } else if (window.location.pathname === "/research/factor-mining") {
    loadFactorMiningPage();
  } else if (window.location.pathname.startsWith("/research/factor-jobs")) {
    loadFactorJobsPage();
  } else if (window.location.pathname.startsWith("/data/factors/") || (window.location.pathname.startsWith("/factors/") && !window.location.pathname.startsWith("/factors/new/"))) {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const factorId = window.location.pathname.startsWith("/data/factors/") ? parts[2] : parts[1];
    if (factorId && factorId !== "versions") {
      document.getElementById("factor-catalog-view").classList.add("hidden");
      document.getElementById("factor-detail-view").classList.remove("hidden");
      loadFactorDetail(decodeURIComponent(factorId));
    } else {
      loadFactors();
    }
  } else if (window.location.pathname === "/research/factors" || window.location.pathname.startsWith("/research/factors/")) {
    document.getElementById("research-query-button")?.addEventListener("click", loadResearchRuns);
    document.getElementById("research-form")?.addEventListener("submit", submitResearchDraft);
    loadResearchPage();
  } else if (window.location.pathname.startsWith("/research/runs/")) {
    loadResearchDetail();
  } else if (window.location.pathname.startsWith("/models/runs/")) {
    loadModelRunPage();
  } else if (window.location.pathname.startsWith("/models/") && window.location.pathname.includes("/versions/")) {
    loadModelVersionPage();
  } else if (window.location.pathname === "/models") {
    loadModelsPage();
  } else {
    loadOverview();
  }
});
