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
