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
