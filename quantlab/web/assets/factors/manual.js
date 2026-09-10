function factorLibraryErrorMessage(payload, status) {
  const detail = payload?.detail && typeof payload.detail === "object" ? payload.detail : payload;
  const message = detail?.message || `HTTP ${status}`;
  const structured = detail?.details || detail?.errors;
  if (!structured) return message;
  const suffix = typeof structured === "string" ? structured : JSON.stringify(structured);
  return `${message}（${suffix}）`;
}

function manualFactorBody() {
  return {
    name: document.getElementById("manual-factor-name").value.trim(),
    category: document.getElementById("manual-factor-category").value.trim(),
    asset_class: document.getElementById("manual-factor-asset-class")?.value || "cn_a",
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
