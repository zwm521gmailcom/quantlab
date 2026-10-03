(function () {
  const errorBox = document.getElementById("qlib-runs-error");
  const body = document.getElementById("qlib-runs-body");
  const empty = document.getElementById("qlib-runs-empty");
  const pager = document.getElementById("qlib-runs-pager");
  const summary = document.getElementById("qlib-runs-summary");
  const live = document.getElementById("qlib-runs-live");
  const filter = document.getElementById("qlib-plan-filter");
  const purgeButton = document.getElementById("qlib-purge-errors");
  const deleteButton = document.getElementById("qlib-delete-plan");
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
    if (key === "plan") return row.plan.plan_id || "";
    if (key === "source") return sourceLabel(roundModel(row.plan, row.round));
    if (key === "round") return row.index + 1;
    if (key === "created_at") return row.time;
    if (key === "name") return round.name || "";
    if (key === "valid_ic") return round.valid_ic;
    if (key === "test_ic") return round.test_ic;
    if (key === "valid_annual") return ownValid(round).annual_return;
    if (key === "valid_ir") return ownValid(round).information_ratio;
    if (key === "test_annual") return ownTest(round).test_annual_return;
    if (key === "test_drawdown") return ownTest(round).test_max_drawdown;
    if (key === "test_ir") return ownTest(round).test_information_ratio;
    if (key === "status") return factorStatus(round, row.interruption).label;
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

  function interruptionItems(plan) {
    const stored = Array.isArray(plan.interruptions) ? plan.interruptions.filter((item) => item && item.message) : [];
    if (stored.length) return stored;
    if (plan.stop_reason && String(plan.stop_reason).includes("模型")) {
      return [{created_at: "", message: plan.stop_reason}];
    }
    return [];
  }

  function rowsOf(planList) {
    const rows = [];
    planList.forEach((plan) => {
      interruptionItems(plan).forEach((item) => {
        rows.push({
          plan,
          round: {name: "模型中断", error: item.message, created_at: item.created_at || "", model: plan.model},
          index: -1,
          time: item.created_at || "",
          interruption: true,
        });
      });
      const rounds = plan.rounds || [];
      if (!rounds.length && !interruptionItems(plan).length) {
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

  function ownValid(round) {
    const book = round && round.own_valid;
    return book && typeof book === "object" ? book : {};
  }

  function ownTest(round) {
    const book = round && round.own_test;
    return book && typeof book === "object" ? book : {};
  }

  function bookReady(book) {
    if (!book || book.error) return false;
    return [book.test_annual_return, book.test_benchmark_annual_return, book.test_information_ratio].every((value) => typeof value === "number" && Number.isFinite(value));
  }

  function factorStatus(round, interruption) {
    if (interruption) return {key: "interrupted", label: "中断", detail: (round && round.error) || ""};
    const error = round && round.error ? String(round.error) : "";
    const admission = error.includes("没有更好") || error.includes("换方向") || error.includes("截面相关");
    if (error && !admission) return {key: "failed", label: "错误", detail: error};
    if (!round || (!round.name && !round.formula)) return {key: "empty", label: "未写出", detail: ""};
    if (!bookReady(ownTest(round))) {
      const strategyError = round.strategy && round.strategy.error ? String(round.strategy.error) : "";
      if (strategyError) return {key: "failed", label: "错误", detail: strategyError};
      return {key: "pending", label: "待计算", detail: ""};
    }
    const test = ownTest(round);
    const annual = Number(test.test_annual_return);
    const benchmark = Number(test.test_benchmark_annual_return);
    const ir = Number(test.test_information_ratio);
    if (annual > benchmark && ir > 0) return {key: "passed", label: "过线", detail: ""};
    return {key: "declined", label: "未过线", detail: ""};
  }

  function planStats(plan) {
    const counts = {passed: 0, declined: 0, pending: 0, failed: 0, interrupted: 0, empty: 0};
    (plan.rounds || []).forEach((round) => {
      counts[factorStatus(round, false).key] += 1;
    });
    counts.interrupted = interruptionItems(plan).length;
    return {
      done: (plan.rounds || []).length,
      max: Number(plan.max_loops) || 0,
      calls: plan.agent_calls || 0,
      stop: plan.stop_reason || "",
      ...counts,
    };
  }

  function addStats(total, stats) {
    ["passed", "declined", "pending", "failed", "interrupted", "empty"].forEach((key) => {
      total[key] += stats[key] || 0;
    });
    return total;
  }

  function statusSummary(stats) {
    const parts = [`过线 ${stats.passed}`, `未过线 ${stats.declined}`];
    if (stats.pending) parts.push(`待计算 ${stats.pending}`);
    if (stats.failed) parts.push(`错误 ${stats.failed}`);
    if (stats.interrupted) parts.push(`中断 ${stats.interrupted}`);
    return parts.join(" · ");
  }

  function planOptionLabel(plan) {
    const stats = planStats(plan);
    const parts = [
      displayTime(planCreated(plan)),
      planSourceLabel(plan),
      plan.plan_id,
      `${stats.done}/${stats.max} ${statusLabel(plan.status)}`,
      statusSummary(stats),
      `调用 ${stats.calls} 次`,
    ];
    if (stats.stop) parts.push(stats.stop);
    return parts.filter(Boolean).join(" · ");
  }

  function syncFilter(planList) {
    const ordered = plansNewestFirst(planList);
    const signature = ordered.map((plan) => planOptionLabel(plan)).join("\n");
    if (signature === filterIds) return;
    filterIds = signature;
    const selected = view.planId;
    const totals = ordered.reduce((sum, plan) => addStats(sum, planStats(plan)), {passed: 0, declined: 0, pending: 0, failed: 0, interrupted: 0, empty: 0});
    filter.replaceChildren(new Option(`全部 · ${ordered.length} 个编号 · ${statusSummary(totals)}`, ""));
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

  function statusCell(round, interruption) {
    const status = factorStatus(round, interruption);
    const item = cell(status.label, "status");
    if (status.detail && status.detail !== status.label) item.title = status.detail;
    return item;
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

  function actionCell(plan, round, index) {
    const item = document.createElement("td");
    item.className = "qlib-actions";
    const link = document.createElement("a");
    link.className = "button-link";
    link.href = `/qlib?plan=${encodeURIComponent(plan.plan_id)}`;
    link.textContent = plan.status === "configured" ? "用这份配置" : "查看";
    item.append(link);
    const archive = backtestLink(plan, round, index);
    if (archive) item.append(archive);
    return item;
  }

  function backtestLink(plan, round, index) {
    const own = round && round.own_test;
    if (!own || !bookReady(own) || index < 0) return null;
    if (own.detail_url) {
      const archive = document.createElement("a");
      archive.className = "button-link";
      archive.href = own.detail_url;
      archive.textContent = "查看回测";
      archive.title = "这个因子自己的测试段回测";
      return archive;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "linkish";
    button.textContent = "查看回测";
    button.title = "写入这个因子自己的测试段回测，再打开结果档案";
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "写入中";
      try {
        const result = await window.apiFetch(`/api/qlib/plans/${plan.plan_id}/rounds/${index}/own-archive`, {method: "POST"});
        if (!result.detail_url) throw new Error("没有写入结果档案");
        window.location.href = result.detail_url;
      } catch (error) {
        showError(error);
        button.disabled = false;
        button.textContent = "查看回测";
      }
    });
    return button;
  }

  function roundRow(plan, row) {
    const round = row.round || {};
    const valid = ownValid(round);
    const test = ownTest(round);
    const tr = document.createElement("tr");
    const model = roundModel(plan, round);
    const source = cell(sourceLabel(model), "source");
    if (model) source.title = String(model);
    tr.append(
      cell(plan.plan_id || "—", "plan-id"),
      source,
      cell(row.interruption ? "—" : `${row.index + 1}/${plan.max_loops}`, "num"),
      cell(displayTime(row.time) || "—"),
      cell(round.name || "—"),
      reasonCell(round),
      formulaCell(round),
      cell(icText(round.valid_ic), "num"),
      cell(pct(valid.annual_return), "num"),
      cell(icText(valid.information_ratio), "num"),
      cell(icText(round.test_ic), "num"),
      cell(pct(test.test_annual_return), "num"),
      cell(pct(test.test_max_drawdown), "num"),
      cell(icText(test.test_information_ratio), "num"),
      statusCell(round, row.interruption),
      actionCell(plan, round, row.index),
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
    return `${head}正在向模型要公式，或正在算这个因子自己的验证段和测试段。记下之后才会出现在表里。`;
  }

  function noticeText(plan) {
    return interruptionItems(plan).map((item) => {
      const when = displayTime(item.created_at);
      return `${plan.plan_id}${when ? ` ${when}` : ""} ${item.message}`;
    }).join(" ");
  }

  function purgeScope(planList) {
    const chosen = view.planId ? planList.filter((plan) => plan.plan_id === view.planId) : planList;
    const idle = chosen.filter((plan) => plan.status !== "running");
    const running = chosen.filter((plan) => plan.status === "running");
    const totals = idle.reduce((sum, plan) => addStats(sum, planStats(plan)), {passed: 0, declined: 0, pending: 0, failed: 0, interrupted: 0, empty: 0});
    return {chosen, idle, running, totals};
  }

  function syncPurge(planList) {
    const scope = purgeScope(planList);
    const failed = scope.totals.failed || 0;
    const interrupted = scope.totals.interrupted || 0;
    if (!scope.idle.length) {
      purgeButton.disabled = true;
      purgeButton.title = scope.running.length ? "先停止这个任务，再删除错误和中断" : "没有可删除的记录";
      return;
    }
    if (!failed && !interrupted) {
      purgeButton.disabled = true;
      purgeButton.title = "没有错误或中断";
      return;
    }
    const where = view.planId || "全部任务";
    const skip = scope.running.length ? "进行中的任务会跳过。" : "";
    purgeButton.disabled = false;
    purgeButton.title = `删除 ${where} 的 ${failed} 条错误和 ${interrupted} 条中断。${skip}过线、未过线和待计算会留下。`;
  }

  function syncDelete(planList) {
    const plan = view.planId ? planList.find((item) => item.plan_id === view.planId) : null;
    if (!plan) {
      deleteButton.disabled = true;
      deleteButton.title = "先在编号里选一个任务";
      return;
    }
    if (plan.status === "running") {
      deleteButton.disabled = true;
      deleteButton.title = "先停止这个任务，再删除计划";
      return;
    }
    const stats = planStats(plan);
    deleteButton.disabled = false;
    deleteButton.title = `删除计划 ${plan.plan_id}。${statusSummary(stats)}`;
  }

  function syncLive(planList) {
    const chosen = view.planId ? planList.filter((plan) => plan.plan_id === view.planId) : planList;
    const totals = chosen.reduce((sum, plan) => addStats(sum, planStats(plan)), {passed: 0, declined: 0, pending: 0, failed: 0, interrupted: 0, empty: 0});
    const scope = view.planId ? `${view.planId} · ${statusLabel(chosen[0] && chosen[0].status)}` : `全部 · ${chosen.length} 个任务`;
    summary.textContent = chosen.length ? `${scope}。${statusSummary(totals)}。` : "还没有计划。";
    const text = chosen.map((plan) => liveText(plan) || noticeText(plan)).filter(Boolean).join(" ");
    live.hidden = !text;
    live.textContent = text;
  }

  function render(nextPlans) {
    plans = plansNewestFirst(nextPlans || []);
    syncFilter(plans);
    syncPurge(plans);
    syncDelete(plans);
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
  purgeButton.addEventListener("click", async () => {
    const scope = purgeScope(plans);
    const failed = scope.totals.failed || 0;
    const interrupted = scope.totals.interrupted || 0;
    if (!scope.idle.length || (!failed && !interrupted)) return;
    const where = view.planId || "全部任务";
    const skip = scope.running.length ? "进行中的任务会跳过。" : "";
    const agreed = window.confirm(`删除 ${where} 的 ${failed} 条错误和 ${interrupted} 条中断？${skip}过线、未过线和待计算会留下。`);
    if (!agreed) return;
    purgeButton.disabled = true;
    try {
      let removed = 0;
      for (const plan of scope.idle) {
        const stats = planStats(plan);
        if (!stats.failed && !stats.interrupted) continue;
        const result = await window.apiFetch(`/api/qlib/plans/${plan.plan_id}/purge-errors`, {method: "POST"});
        removed += result.removed || 0;
      }
      errorBox.classList.add("hidden");
      live.hidden = false;
      live.textContent = `已删除 ${removed} 条错误和中断。过线、未过线和待计算还在。`;
      await refresh();
    } catch (error) {
      showError(error);
      purgeButton.disabled = false;
    }
  });
  deleteButton.addEventListener("click", async () => {
    const plan = plans.find((item) => item.plan_id === view.planId);
    if (!plan || plan.status === "running") return;
    const stats = planStats(plan);
    const agreed = window.confirm(`删除计划 ${plan.plan_id}？${statusSummary(stats)}。这些记录都会从挖因子记录和筛选结果里去掉。结果档案里已有的回测还在。此操作不能恢复。`);
    if (!agreed) return;
    deleteButton.disabled = true;
    try {
      const result = await window.apiFetch(`/api/qlib/plans/${plan.plan_id}/delete`, {method: "POST"});
      errorBox.classList.add("hidden");
      await refresh();
      live.hidden = false;
      live.textContent = `已删除计划 ${result.plan_id}，共 ${result.rounds || 0} 条记录。`;
    } catch (error) {
      showError(error);
      deleteButton.disabled = false;
    }
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
      if (!timer) timer = window.setInterval(refresh, 5000);
    } catch (error) {
      showError(error);
    }
  }

  refresh();
})();
