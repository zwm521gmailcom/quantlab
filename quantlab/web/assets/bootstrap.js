function fileConfigAllowedPath() {
  const path = window.location.pathname;
  return path === "/data" || path === "/research/factors" || path === "/factors" || path === "/factors/new/manual" || path === "/research/factor-mining";
}

function renderDirectoryPlan(container, plan) {
  container.replaceChildren();
  if (!plan) return;
  const heading = document.createElement("h4");
  heading.textContent = "资产分类目录规划";
  const lead = document.createElement("p");
  lead.className = "file-config-plan-lead";
  lead.textContent = plan.lead || "";
  container.append(heading, lead);
  const roles = Array.isArray(plan.path_roles) && plan.path_roles.length
    ? plan.path_roles
    : Object.keys((plan.asset_classes && plan.asset_classes[0] && plan.asset_classes[0].paths) || {}).map((key) => ({key, label: key}));
  (plan.asset_classes || []).forEach((item) => {
    const block = document.createElement("div");
    block.className = "file-config-class";
    const head = document.createElement("div");
    head.className = "file-config-class-head";
    const title = document.createElement("strong");
    title.textContent = `${item.label}（${item.code}）`;
    const badge = document.createElement("span");
    badge.className = "file-config-badge";
    badge.textContent = item.status || (item.current ? "现行" : "尚未使用");
    head.append(title, badge);
    block.append(head);
    if (item.note) {
      const note = document.createElement("p");
      note.className = "file-config-class-note";
      note.textContent = item.note;
      block.append(note);
    }
    roles.forEach((role) => {
      const value = (item.paths || {})[role.key];
      if (!value) return;
      const row = document.createElement("div");
      row.className = "file-config-path";
      const name = document.createElement("span");
      name.textContent = role.label;
      const pathText = document.createElement("code");
      pathText.textContent = value;
      pathText.title = value;
      row.append(name, pathText);
      block.append(row);
    });
    container.append(block);
  });
  const rules = document.createElement("ul");
  rules.className = "file-config-rules";
  (plan.rules || []).forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    rules.append(item);
  });
  if (rules.childElementCount) container.append(rules);
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
    const plan = document.createElement("section");
    plan.className = "file-config-plan";
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
    panel.append(list, plan, editRow);
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
      renderDirectoryPlan(plan, payload.directory_plan);
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

window.addEventListener("DOMContentLoaded", () => {
  setupFileConfigDialog();
  if (window.location.pathname === "/kline") {
    document.getElementById("kline-query").addEventListener("click", loadKline);
    document.getElementById("kline-export").addEventListener("click", exportKline);
    const query = new URLSearchParams(window.location.search);
    document.getElementById("kline-symbol").value = query.get("ts_code") || "000001.SZ";
    loadKlineVersions().then(loadKline).catch(loadKline);
  } else if (window.location.pathname === "/data") {
    document.getElementById("apply-dataset-filters").addEventListener("click", () => { datasetPage = 1; loadDatasets(); });
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
  } else if (typeof loadOverview === "function") {
    loadOverview();
  }
});
