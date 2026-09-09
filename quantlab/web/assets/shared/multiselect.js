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
