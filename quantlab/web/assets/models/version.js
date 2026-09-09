async function loadModelVersionPage() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const entityId = decodeURIComponent(parts[1] || "");
  const versionId = decodeURIComponent(parts[3] || "");
  const detail = document.getElementById("detail");
  const identity = document.getElementById("identity");
  const title = document.getElementById("title");
  const toBacktest = document.getElementById("to-backtest");
  try {
    await fetchModelKindCatalog();
    const response = await fetch(`/api/models/${encodeURIComponent(entityId)}/versions/${encodeURIComponent(versionId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "模型版本加载失败");
    const config = payload.config || {};
    const kind = config.kind || "";
    const params = config.hyperparameters || {};
    const kindName = modelKindCatalog[kind]?.name || kind || "—";
    if (title) title.textContent = `${kindName} · ${payload.version_id || versionId}`;
    if (identity) identity.textContent = `${payload.entity_id || entityId} · ${payload.version_id || versionId} · ${modelStatusText(payload.status)}`;
    renderDefinitionList(detail, [
      ["种类", kindName],
      ["版本状态", modelStatusText(payload.status)],
      ...kindParamRows(kind, params).map(([label, value]) => [label, value]),
    ]);
    if (toBacktest) {
      const query = new URLSearchParams();
      if (kind) query.set("kind", kind);
      query.set("model", `${payload.entity_id || entityId}::${payload.version_id || versionId}`);
      query.set("hp", JSON.stringify(params));
      toBacktest.href = `/backtests/new?${query.toString()}`;
    }
  } catch (error) {
    if (identity) identity.textContent = error.message || "加载失败";
    if (detail) detail.textContent = error.message || "加载失败";
  }
}
