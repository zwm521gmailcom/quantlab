(function () {
  const errorBox = document.getElementById("qlib-error");
  const sourceBox = document.getElementById("qlib-source");
  const conversionBox = document.getElementById("qlib-conversion");
  const convertButton = document.getElementById("qlib-convert");
  const saveButton = document.getElementById("qlib-save");
  const startButton = document.getElementById("qlib-start");
  const stopButton = document.getElementById("qlib-stop");
  const continueButton = document.getElementById("qlib-continue");
  const resumeSelect = document.getElementById("qlib-resume");
  const miningBox = document.getElementById("qlib-mining");
  let activePlanId = "";
  let conversionReady = false;
  let mining = false;
  let timer = 0;

  function showError(error) {
    errorBox.textContent = error && error.message ? error.message : "请求失败";
    errorBox.classList.remove("hidden");
  }

  function clearError() {
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
  }

  function planBody() {
    const maxLoops = Number(document.getElementById("qlib-loops").value);
    return {
      max_loops: maxLoops,
      patience: maxLoops,
      model: document.getElementById("qlib-model").value,
      train_end: document.getElementById("qlib-train-end").value,
      valid_end: document.getElementById("qlib-valid-end").value,
    };
  }

  function selectPlan(plan) {
    activePlanId = plan.plan_id;
    document.getElementById("qlib-loops").value = plan.max_loops;
    const modelSelect = document.getElementById("qlib-model");
    if ([...modelSelect.options].some((option) => option.value === plan.model)) {
      modelSelect.value = plan.model;
    }
    document.getElementById("qlib-train-end").value = plan.train_end;
    document.getElementById("qlib-valid-end").value = plan.valid_end;
    startButton.disabled = mining || plan.status !== "configured" || !conversionReady;
    stopButton.disabled = plan.status !== "running";
  }

  function factorCount(plan) {
    return (plan.rounds || []).filter((round) => round && round.name && round.formula && !round.error).length;
  }

  function unfinishedPlans(plans) {
    return (plans || [])
      .filter((plan) => factorCount(plan) < Number(plan.max_loops))
      .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")));
  }

  function resumeLabel(plan) {
    const done = factorCount(plan);
    const when = String(plan.created_at || "").replace("T", " ").slice(0, 16);
    const state = plan.status === "running" ? "进行中" : (plan.stop_reason || "已停止");
    return `${when || "无时间"} · ${plan.plan_id} · ${done}/${plan.max_loops} 个因子 · ${state}`;
  }

  function renderResume(plans) {
    const selected = resumeSelect.value;
    const items = unfinishedPlans(plans);
    resumeSelect.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "没有可继续的任务";
      resumeSelect.append(empty);
    }
    items.forEach((plan) => {
      const option = document.createElement("option");
      option.value = plan.plan_id;
      option.dataset.status = plan.status || "";
      option.textContent = resumeLabel(plan);
      option.title = option.textContent;
      resumeSelect.append(option);
    });
    if (selected && [...resumeSelect.options].some((option) => option.value === selected)) {
      resumeSelect.value = selected;
    }
    const chosen = resumeSelect.selectedOptions[0];
    const chosenRunning = Boolean(chosen && chosen.dataset.status === "running");
    continueButton.disabled = mining || chosenRunning || !conversionReady || !resumeSelect.value;
  }

  function renderStatus(status) {
    const source = status.source_ready ? "宽表已找到" : "找不到宽表";
    sourceBox.textContent = `${source}：${status.source_path}`;
    const conversion = status.conversion || {};
    if (!conversion.status && !conversion.date_min) {
      conversionBox.textContent = "尚未转换。";
    } else if (conversion.status === "running") {
      conversionBox.textContent = "正在转换，这一步不调用模型。";
    } else if (conversion.status === "failed") {
      conversionBox.textContent = `转换失败：${conversion.error || ""}`;
    } else {
      const extra = (conversion.fields || []).includes("turnover_rate") ? "，已含每日指标和资金流向" : "";
      conversionBox.textContent = `已转换 ${conversion.symbols || 0} 只股票，${conversion.date_min || ""} 至 ${conversion.date_max || ""}，股票池 ${conversion.universe || "all"}${extra}。目录 ${conversion.qlib_dir || ""}`;
    }
    convertButton.disabled = conversion.status === "running";
    conversionReady = conversion.status === "completed";
    if (!activePlanId) activePlanId = new URLSearchParams(window.location.search).get("plan") || "";
    const runningPlan = (status.plans || []).find((plan) => plan.status === "running");
    const current = (status.plans || []).find((plan) => plan.plan_id === activePlanId);
    if (runningPlan) {
      activePlanId = runningPlan.plan_id;
      stopButton.disabled = false;
    } else if (current) {
      selectPlan(current);
    } else {
      stopButton.disabled = true;
    }
    const running = Boolean(runningPlan);
    mining = running;
    startButton.disabled = !conversionReady || running;
    if (runningPlan) {
      const done = factorCount(runningPlan);
      miningBox.textContent = `正在挖掘 ${runningPlan.plan_id}，已挖出 ${done}/${runningPlan.max_loops} 个因子。开始循环已停用，点停止或挖满这个数量才会停。`;
    } else {
      miningBox.textContent = "当前没有正在运行的挖掘。还没挖满数量的任务可以在下面选择后继续。";
    }
    renderResume(status.plans);
    if ((conversion.status === "running" || running) && !timer) {
      timer = window.setInterval(refresh, 2000);
    }
    if (conversion.status !== "running" && !running && timer) {
      window.clearInterval(timer);
      timer = 0;
    }
  }

  async function refresh() {
    try {
      renderStatus(await window.apiFetch("/api/qlib/status"));
      clearError();
    } catch (error) {
      showError(error);
    }
  }

  convertButton.addEventListener("click", async () => {
    clearError();
    convertButton.disabled = true;
    try {
      renderStatus(await window.apiFetch("/api/qlib/convert", { method: "POST" }));
    } catch (error) {
      showError(error);
      convertButton.disabled = false;
    }
  });

  document.getElementById("qlib-plan").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    saveButton.disabled = true;
    try {
      const plan = await window.apiFetch("/api/qlib/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(planBody()),
      });
      selectPlan(plan);
      await refresh();
      selectPlan(plan);
    } catch (error) {
      showError(error);
    } finally {
      saveButton.disabled = false;
    }
  });

  startButton.addEventListener("click", async () => {
    clearError();
    startButton.disabled = true;
    try {
      const plan = await window.apiFetch("/api/qlib/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(planBody()),
      });
      activePlanId = plan.plan_id;
      const started = await window.apiFetch(`/api/qlib/plans/${plan.plan_id}/start`, { method: "POST" });
      selectPlan(started);
      await refresh();
    } catch (error) {
      showError(error);
      startButton.disabled = false;
    }
  });

  stopButton.addEventListener("click", async () => {
    if (!activePlanId) return;
    stopButton.disabled = true;
    try {
      const plan = await window.apiFetch(`/api/qlib/plans/${activePlanId}/stop`, { method: "POST" });
      selectPlan(plan);
      await refresh();
    } catch (error) {
      showError(error);
      stopButton.disabled = !mining;
    }
  });

  continueButton.addEventListener("click", async () => {
    const planId = resumeSelect.value;
    if (!planId || mining) return;
    clearError();
    continueButton.disabled = true;
    startButton.disabled = true;
    try {
      activePlanId = planId;
      const started = await window.apiFetch(`/api/qlib/plans/${planId}/start`, { method: "POST" });
      selectPlan(started);
      await refresh();
    } catch (error) {
      showError(error);
      startButton.disabled = mining || !conversionReady;
      continueButton.disabled = mining || !conversionReady || !resumeSelect.value;
    }
  });

  refresh();
})();
