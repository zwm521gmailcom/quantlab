const $ = (id) => document.getElementById(id);

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.message || payload.detail?.message || payload.detail || `HTTP ${response.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return payload;
}

function params() {
  const k = Math.max(1, Number($("offline-rl-k").value) || 8);
  const window = Math.max(2, Number($("offline-rl-window").value) || 20);
  $("offline-rl-k").value = String(k);
  $("offline-rl-window").value = String(window);
  return { k, window };
}

function fmtMetric(value, digits = 3) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return number.toFixed(digits);
}

function fmtPct(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return `${(number * 100).toFixed(2)}%`;
}

function shortLabel(text, size = 12) {
  const value = String(text || "");
  return value.length <= size ? value : `${value.slice(0, size)}…`;
}

function renderTable(container, columns, rows) {
  container.replaceChildren();
  const table = document.createElement("table");
  table.className = "archive-grid";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const column of columns) {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = columns.length;
    td.className = "state";
    td.textContent = "暂无数据";
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    for (const cells of rows) {
      const tr = document.createElement("tr");
      for (const cell of cells) {
        const td = document.createElement("td");
        if (cell instanceof Node) td.appendChild(cell);
        else td.textContent = cell;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  }
  table.appendChild(tbody);
  container.appendChild(table);
}

function renderExcluded(items) {
  const rows = (items || []).map((item) => [
    item.run_id || "—",
    item.date_from || "—",
    item.date_to || "—",
  ]);
  renderTable($("offline-rl-excluded"), ["运行 ID", "测试开始", "测试结束"], rows);
}

function renderFingerprints(fingerprints, byYear) {
  const rows = Object.entries(fingerprints || {}).map(([fp, meta]) => {
    const label = meta?.label || fp;
    const years = Object.entries(byYear || {})
      .filter(([, count]) => Number(count) > 0)
      .map(([year]) => year)
      .join("、");
    return [shortLabel(label, 24), shortLabel(fp, 16), years || "—"];
  });
  renderTable($("offline-rl-actions"), ["标签", "指纹", "出现年份"], rows);
}

function renderActionsByYear(actionsByYear, fingerprints) {
  const rows = [];
  for (const [year, fps] of Object.entries(actionsByYear || {}).sort(([a], [b]) => Number(a) - Number(b))) {
    const labels = (fps || []).map((fp, idx) => {
      const label = fingerprints?.[fp]?.label || fp;
      return `${idx + 1}. ${shortLabel(label, 20)}`;
    });
    rows.push([String(year), labels.length ? labels.join(" · ") : "—"]);
  }
  renderTable($("offline-rl-actions-by-year"), ["评分年", "动作表（K 个专家指纹）"], rows);
}

function formatActionSummary(summary) {
  if (!summary || !summary.length) return "—";
  return summary
    .slice(0, 3)
    .map(([fp, count]) => {
      const label = fp === "cash" ? "持币" : shortLabel(fp, 10);
      return `${label}×${count}`;
    })
    .join(" · ");
}

function formatCopyRatio(value) {
  const text = fmtPct(value);
  if (value != null && Number(value) >= 0.9) {
    const span = document.createElement("span");
    span.className = "warn-text";
    span.textContent = `${text} ⚠`;
    span.title = "与上年冠军高度重合";
    return span;
  }
  return text;
}

function renderYearResults(years) {
  const rows = (years || []).map((row) => [
    String(row.year ?? "—"),
    fmtMetric(row.sharpe_replay),
    fmtPct(row.excess_replay),
    fmtMetric(row.sharpe_policy),
    fmtPct(row.excess_policy),
    formatCopyRatio(row.champion_copy_ratio),
    formatActionSummary(row.action_summary),
  ]);
  renderTable(
    $("offline-rl-year-results"),
    ["年", "重放夏普", "重放超额", "Q 夏普", "Q 超额", "与冠军相同日占比", "动作摘要"],
    rows,
  );
}

function renderVerdict(verdict) {
  const banner = $("offline-rl-verdict");
  const text = $("offline-rl-verdict-text");
  if (!verdict) {
    banner.classList.add("hidden");
    text.textContent = "尚未运行";
    return;
  }
  banner.classList.remove("hidden");
  banner.classList.toggle("banner-error", !verdict.passed);
  banner.classList.toggle("banner-warn", Boolean(verdict.champion_copy_warning));
  const passed = verdict.passed ? "通过" : "未通过";
  const parts = [
    passed,
    `策略平均夏普 ${fmtMetric(verdict.mean_sharpe_policy)}`,
    `重放平均夏普 ${fmtMetric(verdict.mean_sharpe_replay)}`,
    `策略平均超额 ${fmtPct(verdict.mean_excess_policy)}`,
    `重放平均超额 ${fmtPct(verdict.mean_excess_replay)}`,
  ];
  if (verdict.mean_champion_copy_ratio != null) {
    parts.push(`冠军复制占比 ${fmtPct(verdict.mean_champion_copy_ratio)}`);
  }
  if (verdict.champion_copy_warning && verdict.champion_copy_warning_message) {
    parts.push(verdict.champion_copy_warning_message);
  }
  text.textContent = parts.join(" · ");
}

function previewSummary(payload) {
  const years = (payload.scoreable_years || []).join("、") || "无";
  const byYear = Object.entries(payload.by_year || {})
    .map(([year, count]) => `${year}×${count}`)
    .join("、");
  return `可评分年：${years}。自然年格子：${byYear || "无"}。排除长窗 ${(payload.excluded_long_windows || []).length} 条。`;
}

async function loadPreview() {
  const { k, window } = params();
  $("offline-rl-state").textContent = "正在加载预览…";
  $("offline-rl-preview").disabled = true;
  try {
    const payload = await request(`/api/offline-rl/preview?k=${k}&window=${window}`);
    renderExcluded(payload.excluded_long_windows);
    renderFingerprints(payload.fingerprints, payload.by_year);
    renderActionsByYear(payload.actions_by_year, payload.fingerprints);
    renderYearResults([]);
    renderVerdict(null);
    $("offline-rl-state").textContent = previewSummary(payload);
  } catch (error) {
    $("offline-rl-state").textContent = error.message || String(error);
  } finally {
    $("offline-rl-preview").disabled = false;
  }
}

async function pollProgress() {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    const payload = await request("/api/offline-rl/progress");
    if (payload.status === "completed") return payload;
    if (payload.status === "failed") throw new Error(payload.message || "实验失败");
    $("offline-rl-state").textContent = payload.message
      ? `${payload.message}（${payload.percent || 0}%）`
      : `运行中…（${payload.percent || 0}%）`;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("等待实验超时");
}

async function loadRun() {
  if (!confirm("将基于当前 K 与状态窗口运行离线 Q 实验，可能需要一些时间。确定继续？")) return;
  const { k, window } = params();
  $("offline-rl-run").disabled = true;
  $("offline-rl-preview").disabled = true;
  try {
    $("offline-rl-state").textContent = "正在启动实验…";
    const payload = await request("/api/offline-rl/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ k, window }),
    });
    await pollProgress();
    renderYearResults(payload.years);
    renderVerdict(payload.verdict);
    $("offline-rl-state").textContent = `实验完成 · run_id ${payload.run_id || "—"} · ${previewSummary(payload)}`;
  } catch (error) {
    $("offline-rl-state").textContent = error.message || String(error);
  } finally {
    $("offline-rl-run").disabled = false;
    $("offline-rl-preview").disabled = false;
  }
}

function bindOfflineRlPage() {
  if (window.location.pathname !== "/backtests/offline-rl") return;
  $("offline-rl-preview").addEventListener("click", loadPreview);
  $("offline-rl-run").addEventListener("click", loadRun);
}

window.addEventListener("DOMContentLoaded", bindOfflineRlPage);
