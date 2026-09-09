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
