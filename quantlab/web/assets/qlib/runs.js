(function () {
  const errorBox = document.getElementById("qlib-runs-error");
  const body = document.getElementById("qlib-runs-body");
  const empty = document.getElementById("qlib-runs-empty");
  const pager = document.getElementById("qlib-runs-pager");
  const live = document.getElementById("qlib-runs-live");
  const filter = document.getElementById("qlib-plan-filter");
  const table = body.closest("table");
  const dialog = document.getElementById("qlib-formula-dialog");
  const PAGE_SIZES = [10, 20, 50, 100];
  const view = {page: 1, sortKey: "created_at", sortDir: "desc", planId: ""};
  let plans = [];
  let pageSize = window.QuantLabPager ? window.QuantLabPager.readSize("qlib-runs-page-size", PAGE_SIZES, 20) : 20;
  let timer = 0;
  let filterIds = "";

  const STATUS_LABEL = {
    running: "进行中",
    completed: "已完成",
    failed: "已失败",
    stopped: "已停止",
    configured: "未开始",
  };

  function showError(error) {
    errorBox.textContent = error && error.message ? error.message : "请求失败";
    errorBox.classList.remove("hidden");
  }

  function statusLabel(status) {
    return STATUS_LABEL[status] || status || "—";
  }

  function icText(value) {
    return typeof value === "number" ? value.toFixed(4) : "—";
  }

  function pct(value) {
    return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
  }

  function cell(text, className) {
    const item = document.createElement("td");
    if (className) item.className = className;
    item.textContent = text == null || text === "" ? "—" : text;
    return item;
  }

  function timeOf(round) {
    if (!round) return "";
    if (round.created_at) return String(round.created_at);
    const id = round.strategy && round.strategy.archive_run_id;
    const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id || "");
    return match ? `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}` : "";
  }

  function displayTime(value) {
    const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value || "");
    return match ? `${match[1]} ${match[2]}` : "";
  }

  function sourceLabel(model) {
    const name = String(model || "");
    if (name.startsWith("grok")) return "Grok";
    if (name.startsWith("gpt")) return "GPT";
    return name || "—";
  }

  function roundModel(plan, round) {
    return (round && round.model) || (plan && plan.model) || "";
  }

  function planSourceLabel(plan) {
    const labels = [];
    const seen = new Set();
    const add = (model) => {
      const label = sourceLabel(model);
      if (!label || label === "—" || seen.has(label)) return;
      seen.add(label);
      labels.push(label);
    };
    (plan.rounds || []).forEach((round) => add(round && round.model));
    add(plan.model);
    return labels.join("/") || "—";
  }

  function sortValue(row, key) {
    const round = row.round || {};
    const strategy = round.strategy || {};
    if (key === "plan") return row.plan.plan_id || "";
    if (key === "source") return sourceLabel(roundModel(row.plan, row.round));
    if (key === "round") return row.index + 1;
    if (key === "created_at") return row.time;
    if (key === "name") return round.name || "";
    if (key === "valid_ic") return round.valid_ic;
    if (key === "test_ic") return round.test_ic;
    if (key === "annual") return strategy.test_annual_return;
    if (key === "drawdown") return strategy.test_max_drawdown;
    if (key === "ir") return strategy.test_information_ratio;
    return "";
  }

  function compareRows(left, right, view) {
    const a = sortValue(left, view.sortKey);
    const b = sortValue(right, view.sortKey);
    const aMissing = a == null || a === "";
    const bMissing = b == null || b === "";
    if (aMissing || bMissing) {
      if (aMissing && bMissing) return left.index - right.index;
      return aMissing ? 1 : -1;
    }
    let order = 0;
    if (typeof a === "number" && typeof b === "number") order = a - b;
    else order = String(a).localeCompare(String(b), "zh");
    if (order === 0) order = left.index - right.index;
    return view.sortDir === "asc" ? order : -order;
  }

  function rowsOf(planList) {
    const rows = [];
    planList.forEach((plan) => {
      const rounds = plan.rounds || [];
      if (!rounds.length) {
        rows.push({plan, round: null, index: 0, time: ""});
        return;
      }
      rounds.forEach((round, index) => rows.push({plan, round, index, time: timeOf(round)}));
    });
    return rows;
  }

  function planCreated(plan) {
    if (plan.created_at) return String(plan.created_at);
    let earliest = "";
    (plan.rounds || []).forEach((round) => {
      const time = timeOf(round);
      if (time && (!earliest || time < earliest)) earliest = time;
    });
    return earliest;
  }

  function plansNewestFirst(planList) {
    return [...planList].sort((left, right) => {
      const a = planCreated(left);
      const b = planCreated(right);
      if (a === b) return String(right.plan_id).localeCompare(String(left.plan_id));
      if (!a) return 1;
      if (!b) return -1;
      return a < b ? 1 : -1;
    });
  }

  function planStats(plan) {
    const rounds = plan.rounds || [];
    const done = rounds.length;
    const succeeded = rounds.filter((round) => round && !round.error && round.name).length;
    return {
      done,
      max: Number(plan.max_loops) || 0,
      succeeded,
      failed: done - succeeded,
      calls: plan.agent_calls || 0,
      reasons: errorCounts(rounds),
      stop: plan.stop_reason || "",
    };
  }

  function planOptionLabel(plan) {
    const stats = planStats(plan);
    const parts = [
      displayTime(planCreated(plan)),
      planSourceLabel(plan),
      plan.plan_id,
      `${stats.done}/${stats.max} ${statusLabel(plan.status)}`,
      `成功 ${stats.succeeded}`,
      `错误 ${stats.failed}`,
      `调用 ${stats.calls} 次`,
    ];
    if (stats.reasons) parts.push(stats.reasons);
    if (stats.stop) parts.push(stats.stop);
    return parts.filter(Boolean).join(" · ");
  }

  function syncFilter(planList) {
    const ordered = plansNewestFirst(planList);
    const signature = ordered.map((plan) => planOptionLabel(plan)).join("\n");
    if (signature === filterIds) return;
    filterIds = signature;
    const selected = view.planId;
    const total = ordered.reduce((sum, plan) => sum + (plan.rounds || []).length, 0);
    const succeeded = ordered.reduce((sum, plan) => sum + planStats(plan).succeeded, 0);
    const failed = ordered.reduce((sum, plan) => sum + planStats(plan).failed, 0);
    filter.replaceChildren(new Option(`全部 · ${ordered.length} 个编号 · 共 ${total} 条 · 成功 ${succeeded} · 错误 ${failed}`, ""));
    ordered.forEach((plan) => {
      filter.append(new Option(planOptionLabel(plan), plan.plan_id));
    });
    if (planList.some((plan) => plan.plan_id === selected)) {
      filter.value = selected;
    } else {
      view.planId = "";
      filter.value = "";
    }
  }

  function errorCounts(rounds) {
    const counts = new Map();
    rounds.forEach((round) => {
      const text = round && round.error ? String(round.error) : "";
      if (!text) return;
      counts.set(text, (counts.get(text) || 0) + 1);
    });
    return [...counts.entries()].map(([text, count]) => `${text} ${count}`).join("，");
  }

  function openFormula(round) {
    document.getElementById("qlib-formula-title").textContent = round.name || "公式";
    document.getElementById("qlib-formula-name").textContent = round.name || "";
    document.getElementById("qlib-formula-body").textContent = round.formula || "这一轮没有记下公式。";
    document.getElementById("qlib-formula-reason").textContent = round.reason || "";
    dialog.classList.remove("hidden");
  }

  function closeFormula() {
    dialog.classList.add("hidden");
  }

  function formulaCell(round) {
    const item = document.createElement("td");
    if (!round || !round.formula) {
      item.textContent = "—";
      return item;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "linkish";
    button.textContent = "公式";
    button.addEventListener("click", () => openFormula(round));
    item.append(button);
    return item;
  }

  function reasonCell(round) {
    const item = document.createElement("td");
    item.className = "reason";
    const text = round && round.reason ? String(round.reason) : "";
    if (!text) {
      item.textContent = "—";
      return item;
    }
    const span = document.createElement("span");
    span.textContent = text;
    item.append(span);
    return item;
  }

  function actionCell(plan, round) {
    const item = document.createElement("td");
    item.className = "qlib-actions";
    const link = document.createElement("a");
    link.className = "button-link";
    link.href = `/qlib?plan=${encodeURIComponent(plan.plan_id)}`;
    link.textContent = plan.status === "configured" ? "用这份配置" : "查看";
    item.append(link);
    const strategy = round && round.strategy;
    if (strategy && strategy.detail_url) {
      const archive = document.createElement("a");
      archive.className = "button-link";
      archive.href = strategy.detail_url;
      archive.textContent = "结果档案";
      item.append(archive);
    }
    return item;
  }

  function roundRow(plan, row) {
    const round = row.round || {};
    const strategy = round.strategy || {};
    const tr = document.createElement("tr");
    const model = roundModel(plan, round);
    const source = cell(sourceLabel(model), "source");
    if (model) source.title = String(model);
    tr.append(
      cell(plan.plan_id || "—", "plan-id"),
      source,
      cell(`${row.index + 1}/${plan.max_loops}`, "num"),
      cell(displayTime(row.time) || "—"),
      cell(round.name || "—"),
      reasonCell(round),
      formulaCell(round),
      cell(icText(round.valid_ic), "num"),
      cell(icText(round.test_ic), "num"),
      cell(pct(strategy.test_annual_return), "num"),
      cell(pct(strategy.test_max_drawdown), "num"),
      cell(icText(strategy.test_information_ratio), "num"),
      cell(round.error || strategy.error || strategy.archive_error || "—"),
      actionCell(plan, round),
    );
    return tr;
  }

  function markSort(table, view) {
    table.querySelectorAll("[data-sort]").forEach((button) => {
      const header = button.closest("th");
      const active = button.dataset.sort === view.sortKey;
      header.setAttribute("aria-sort", active ? (view.sortDir === "asc" ? "ascending" : "descending") : "none");
    });
  }

  function liveText(plan) {
    if (plan.status !== "running") return "";
    const done = (plan.rounds || []).length;
    const max = Number(plan.max_loops) || 0;
    const calls = Number(plan.agent_calls) || 0;
    const next = done + 1;
    const extra = calls - done;
    const head = `${plan.plan_id} 第 ${next}/${max} 轮还没写入表。已记下 ${done} 轮，模型调用 ${calls} 次。`;
    if (extra > 0) {
      return `${head}多出来的 ${extra} 次是这一轮提问没成功后的重试，正在再问。问完并算出验证、测试和策略后，这一行才会出现。`;
    }
    return `${head}正在向模型要公式，或正在算验证、测试和策略。记下之后才会出现在表里。`;
  }

  function syncLive(planList) {
    const chosen = view.planId ? planList.filter((plan) => plan.plan_id === view.planId) : planList;
    const text = chosen.map(liveText).filter(Boolean).join(" ");
    live.hidden = !text;
    live.textContent = text;
  }

  function render(nextPlans) {
    plans = plansNewestFirst(nextPlans || []);
    syncFilter(plans);
    syncLive(plans);
    empty.classList.toggle("hidden", plans.length > 0);
    table.hidden = plans.length === 0;
    let rows = rowsOf(plans);
    if (view.planId) rows = rows.filter((row) => row.plan.plan_id === view.planId);
    rows.sort((left, right) => compareRows(left, right, view));
    const pages = window.QuantLabPager ? window.QuantLabPager.pagesFor(rows.length, pageSize) : 1;
    view.page = window.QuantLabPager ? window.QuantLabPager.clampPage(view.page, pages) : 1;
    const visible = window.QuantLabPager ? window.QuantLabPager.slice(rows, view.page, pageSize) : rows;
    body.replaceChildren();
    visible.forEach((row) => body.append(roundRow(row.plan, row)));
    markSort(table, view);
    if (window.QuantLabPager && rows.length) {
      window.QuantLabPager.mount(pager, {
        page: view.page,
        pages,
        pageSize,
        sizes: PAGE_SIZES,
        total: rows.length,
        storageKey: "qlib-runs-page-size",
        onPage: (next) => {
          view.page = next;
          render(plans);
        },
        onPageSize: (next) => {
          pageSize = next;
          view.page = 1;
          render(plans);
        },
      });
    } else {
      pager.hidden = true;
      pager.replaceChildren();
    }
  }

  const tip = document.createElement("div");
  tip.className = "qlib-cell-tip hidden";
  tip.setAttribute("role", "tooltip");
  document.body.append(tip);
  let tipCell = null;

  function hideTip() {
    tipCell = null;
    tip.classList.add("hidden");
  }

  function clippedText(node) {
    const text = (node.textContent || "").trim();
    if (!text || text === "—") return "";
    const overflow = node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1;
    return overflow ? text : "";
  }

  function placeTip(cell) {
    const rect = cell.getBoundingClientRect();
    tip.style.left = "0px";
    tip.style.top = "0px";
    const box = tip.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + box.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - box.width - 8);
    if (top + box.height > window.innerHeight - 8) top = Math.max(8, rect.top - box.height - 4);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  table.addEventListener("mouseover", (event) => {
    const cell = event.target.closest(".qlib-runs-grid td");
    if (!cell || cell === tipCell) return;
    const probe = cell.querySelector("span") || cell;
    const text = clippedText(probe);
    if (!text) {
      hideTip();
      return;
    }
    tipCell = cell;
    tip.textContent = text;
    tip.classList.remove("hidden");
    placeTip(cell);
  });
  table.addEventListener("mouseout", (event) => {
    if (!tipCell) return;
    const next = event.relatedTarget && event.relatedTarget.closest ? event.relatedTarget.closest(".qlib-runs-grid td") : null;
    if (next !== tipCell) hideTip();
  });
  document.addEventListener("scroll", hideTip, true);

  table.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sort]");
    if (!button) return;
    const key = button.dataset.sort;
    if (view.sortKey === key) view.sortDir = view.sortDir === "asc" ? "desc" : "asc";
    else {
      view.sortKey = key;
      view.sortDir = key === "name" || key === "plan" ? "asc" : "desc";
    }
    view.page = 1;
    render(plans);
  });
  filter.addEventListener("change", () => {
    view.planId = filter.value;
    view.page = 1;
    render(plans);
  });

  document.getElementById("qlib-formula-close").addEventListener("click", closeFormula);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeFormula();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeFormula();
  });

  async function refresh() {
    try {
      const status = await window.apiFetch("/api/qlib/status");
      render(status.plans || []);
      errorBox.classList.add("hidden");
      const running = (status.plans || []).some((plan) => plan.status === "running") || (status.conversion || {}).status === "running";
      if (running && !timer) timer = window.setInterval(refresh, 2000);
      if (!running && timer) {
        window.clearInterval(timer);
        timer = 0;
      }
    } catch (error) {
      showError(error);
    }
  }

  refresh();
})();
