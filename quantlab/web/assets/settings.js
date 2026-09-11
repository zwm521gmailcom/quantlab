const $ = id => document.getElementById(id);
function workerValue(id) {
  const raw = $(id).value.trim();
  return raw ? Number(raw) : 0;
}
function fillWorker(id, value) {
  $(id).value = value ? String(value) : "";
}
function autoPlaceholder(hint) {
  return `留空=自动（约 ${hint.auto_workers}，本机 ${hint.cpu_count} 核）`;
}
async function load() {
  const r = await fetch("/api/settings");
  const x = await r.json();
  $("settings-paths").textContent = JSON.stringify({paths: x.paths, environment: x.environment, secrets: x.secrets}, null, 2);
  $("top-n").value = x.defaults.top_n;
  $("rebalance-days").value = x.defaults.rebalance_days;
  $("capital").value = x.defaults.capital;
  $("benchmark").value = x.defaults.benchmark;
  fillWorker("fold-workers", x.compute.fold_workers);
  fillWorker("bucket-workers", x.compute.bucket_workers);
  $("fold-workers").placeholder = autoPlaceholder(x.compute_hint);
  $("bucket-workers").placeholder = autoPlaceholder(x.compute_hint);
  $("bucket-pool").value = x.compute.bucket_pool || "process";
  $("compute-hint").textContent = `本机 ${x.compute_hint.cpu_count} 核、约 ${x.compute_hint.ram_gb} GB 内存。折并行建议不超过 ${x.compute_hint.safe_fold_workers}；分层净值建议 ${x.compute_hint.safe_bucket_workers}。同时回测 ${x.compute_hint.max_concurrent_backtests} 条（按内存和核数自动算，没有手动档）。留空时的自动核数（${x.compute_hint.auto_workers}）只适合折训练，不要用到分层进程。`;
  const machine = x.machine || {};
  if ($("machine-id")) $("machine-id").value = machine.machine_id || "";
  if ($("serial-prefix")) $("serial-prefix").value = machine.serial_prefix == null ? "" : String(machine.serial_prefix);
  if ($("lan-market-0400")) $("lan-market-0400").checked = !!(x.lan && x.lan.market_sync_at_0400);
  if ($("instance-asset")) $("instance-asset").value = (x.instance && x.instance.asset) || "a_share";
  if ($("instance-port")) $("instance-port").value = x.instance && x.instance.port != null ? String(x.instance.port) : "";
  if ($("instance-lan-port")) $("instance-lan-port").value = x.instance && x.instance.lan_port != null ? String(x.instance.lan_port) : "";
  if ($("instance-saved") && x.instance && x.instance.restart_required) {
    $("instance-saved").textContent = "已保存，重启 QuantLab 后切换资产与端口";
  }
}
$("settings-form").onsubmit = async e => {
  e.preventDefault();
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      defaults: {
        top_n: +$("top-n").value,
        rebalance_days: +$("rebalance-days").value,
        capital: +$("capital").value,
        benchmark: $("benchmark").value,
      },
    }),
  });
  $("saved").textContent = r.ok ? "已保存（仅影响新草稿）" : "保存失败";
  if (r.ok) load();
};
$("compute-form").onsubmit = async e => {
  e.preventDefault();
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      compute: {
        fold_workers: workerValue("fold-workers"),
        bucket_workers: workerValue("bucket-workers"),
        bucket_pool: $("bucket-pool").value,
      },
    }),
  });
  $("compute-saved").textContent = r.ok ? "已保存（下一次开始回测生效，页面服务未停）" : "保存失败";
  if (r.ok) load();
};
$("reset").onclick = async () => {
  await fetch("/api/settings/reset", {method: "POST"});
  $("saved").textContent = "已恢复默认值（含运算资源和局域网开关）";
  $("compute-saved").textContent = "";
  load();
};
$("serial-prefix-save").onclick = async () => {
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({machine: {serial_prefix: Number($("serial-prefix").value)}}),
  });
  $("serial-prefix-saved").textContent = r.ok ? "已保存（下一次开始回测生效）" : "保存失败，请填 10–99";
  if (r.ok) load();
};
if ($("instance-save")) $("instance-save").onclick = async () => {
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      instance: {
        asset: $("instance-asset").value,
        port: Number($("instance-port").value),
        lan_port: Number($("instance-lan-port").value),
      },
    }),
  });
  $("instance-saved").textContent = r.ok ? "已保存，重启 QuantLab 后切换资产与端口" : "保存失败，两个端口必须不同且在 1–65535";
  if (r.ok) load();
};
$("scan").onclick = async () => {
  const x = await (await fetch("/api/settings/scan", {method: "POST"})).json();
  $("scan-result").textContent = ` 已扫描 ${x.roots.length} 个目录`;
};
load();

async function refreshTushareTokenStatus() {
  const input = document.getElementById("tushare-token");
  const status = document.getElementById("tushare-token-status");
  const r = await fetch("/api/settings/tushare-token");
  if (r.ok) {
    const p = await r.json();
    input.value = "";
    input.placeholder = p.configured ? "已配置（输入新值可覆盖）" : "未配置，输入 Tushare Token";
    status.textContent = p.configured ? "已配置" : "未配置";
  }
}
document.getElementById("tushare-token-save").addEventListener("click", async () => {
  const token = document.getElementById("tushare-token").value.trim();
  const r = await fetch("/api/settings/tushare-token", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({token})});
  const p = await r.json();
  document.getElementById("tushare-token-status").textContent = r.ok ? (p.configured ? "已保存" : "已清除") : "保存失败";
  if (r.ok) refreshTushareTokenStatus();
});
document.getElementById("tushare-token-clear").addEventListener("click", async () => {
  await fetch("/api/settings/tushare-token", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({token: ""})});
  refreshTushareTokenStatus();
});
refreshTushareTokenStatus();

function lanStatus(text, ok) {
  const node = $("lan-status");
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("error", !ok);
}

function paintProgress(root, snap, active) {
  if (!root) return;
  const done = Number(snap.done || 0);
  const total = Number(snap.total || 0);
  const percent = Number(snap.percent || 0);
  const counts = root.querySelector("[data-role='counts']");
  const pct = root.querySelector("[data-role='percent']");
  const fill = root.querySelector("[data-role='fill']");
  const track = root.querySelector(".lan-sync-progress-track");
  if (counts) counts.textContent = `${done} / ${total}`;
  if (pct) pct.textContent = `${percent}%`;
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  if (track) track.setAttribute("aria-valuenow", String(percent));
  root.classList.toggle("is-active", !!(active && snap.status === "running"));
  root.classList.toggle("is-failed", !!(active && snap.status === "failed"));
}

function createPeerProgress(machineId) {
  const wrap = document.createElement("div");
  wrap.className = "lan-sync-progress";
  wrap.dataset.peerProgress = "1";
  wrap.dataset.machineId = String(machineId || "");
  const head = document.createElement("div");
  head.className = "lan-sync-progress-head";
  const label = document.createElement("span");
  label.append("总进度 ");
  const counts = document.createElement("strong");
  counts.dataset.role = "counts";
  counts.textContent = "0 / 0";
  label.appendChild(counts);
  const percent = document.createElement("strong");
  percent.dataset.role = "percent";
  percent.textContent = "0%";
  head.append(label, percent);
  const track = document.createElement("div");
  track.className = "lan-sync-progress-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", "0");
  const fill = document.createElement("span");
  fill.className = "lan-sync-progress-fill";
  fill.dataset.role = "fill";
  track.appendChild(fill);
  wrap.append(head, track);
  return wrap;
}

let lastLanProgress = {status: "idle", kind: null, done: 0, total: 0, percent: 0, source_machine_id: ""};
const LAN_PROGRESS_KEY = "quantlab-lan-sync-progress";
try {
  const saved = JSON.parse(sessionStorage.getItem(LAN_PROGRESS_KEY) || "");
  if (saved && saved.status && saved.status !== "idle") lastLanProgress = saved;
} catch {
  /* ignore */
}

function applyProgress(snap) {
  lastLanProgress = snap || lastLanProgress;
  try {
    sessionStorage.setItem(LAN_PROGRESS_KEY, JSON.stringify(lastLanProgress));
  } catch {
    /* ignore */
  }
  const kind = lastLanProgress.kind;
  const resultsActive = kind === "results";
  const marketActive = kind === "market";
  if (resultsActive) paintProgress($("lan-progress-results"), lastLanProgress, true);
  if (marketActive) paintProgress($("lan-progress-market"), lastLanProgress, true);
  const sourceId = String(lastLanProgress.source_machine_id || "");
  document.querySelectorAll("[data-peer-progress]").forEach(node => {
    const match = marketActive && node.getAttribute("data-machine-id") === sourceId;
    if (match) paintProgress(node, lastLanProgress, true);
  });
  const busy = lastLanProgress.status === "running";
  if ($("lan-sync-results")) $("lan-sync-results").disabled = busy;
  if ($("lan-sync-market")) $("lan-sync-market").disabled = busy;
  document.querySelectorAll("#lan-peers button").forEach(button => {
    button.disabled = busy;
  });
}

async function refreshProgress() {
  if (!$("lan-progress-results")) return;
  try {
    const r = await fetch("/api/lan/sync/progress");
    const incoming = await r.json();
    if (incoming.status === "idle" && lastLanProgress.status && lastLanProgress.status !== "idle") {
      applyProgress(lastLanProgress);
      return;
    }
    applyProgress(incoming);
  } catch {
    applyProgress(lastLanProgress);
  }
}

async function withProgressPoll(work) {
  await refreshProgress();
  const timer = setInterval(refreshProgress, 300);
  try {
    return await work();
  } finally {
    clearInterval(timer);
    await refreshProgress();
  }
}

const ASSET_LABELS = {a_share: "A股", crypto: "数字货币"};
function renderPeers(peers, meta) {
  const root = $("lan-peers");
  if (!root) return;
  root.replaceChildren();
  meta = meta || {};
  if (!peers.length) {
    const empty = document.createElement("p");
    empty.className = "lan-empty";
    const ui = meta.port != null ? meta.port : 8765;
    const lan = meta.lan_port != null ? meta.lan_port : 8766;
    empty.textContent = `还没有发现同一资产的 QuantLab。请确认各机已启动（本机监听 0.0.0.0:${ui}），且防火墙放行 TCP ${ui} 和 UDP/TCP ${lan}。`;
    root.appendChild(empty);
    applyProgress(lastLanProgress);
    return;
  }
  const table = document.createElement("table");
  table.className = "lan-peers";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["主机", "机器码", "资产", "地址", "序号前缀", "页面", ""]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  head.appendChild(headRow);
  table.appendChild(head);
  const body = document.createElement("tbody");
  for (const peer of peers) {
    const row = document.createElement("tr");
    const hostname = document.createElement("td");
    hostname.textContent = peer.self ? `${peer.hostname}（本机）` : String(peer.hostname || "");
    const machine = document.createElement("td");
    machine.textContent = String(peer.machine_id || "");
    const asset = document.createElement("td");
    asset.textContent = ASSET_LABELS[peer.asset] || String(peer.asset || "");
    const address = document.createElement("td");
    address.textContent = `${peer.host}:${peer.port}`;
    const prefix = document.createElement("td");
    prefix.textContent = String(peer.serial_prefix ?? "");
    const page = document.createElement("td");
    const open = document.createElement("a");
    open.href = String(peer.ui_url || `http://${peer.host}:8765/`);
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = "打开页面";
    page.appendChild(open);
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "以此机为源头同步行情";
    button.addEventListener("click", () => syncMarket(peer.machine_id));
    action.append(button, createPeerProgress(peer.machine_id));
    row.append(hostname, machine, asset, address, prefix, page, action);
    body.appendChild(row);
  }
  table.appendChild(body);
  root.appendChild(table);
  applyProgress(lastLanProgress);
}

async function refreshPeers() {
  if (!$("lan-peers")) return;
  try {
    const r = await fetch("/api/lan/peers");
    const x = await r.json();
    renderPeers(x.peers || [], x);
  } catch {
    lanStatus("无法读取局域网机器列表", false);
  }
}

async function syncResults() {
  lanStatus("正在同步回测产物…", true);
  const x = await withProgressPoll(async () => {
    const r = await fetch("/api/lan/sync/results", {method: "POST"});
    const body = await r.json();
    return {ok: r.ok, body};
  });
  if (!x.ok) {
    lanStatus(x.body.message || "同步回测产物失败", false);
    return;
  }
  const runs = (x.body.pulled_runs || []).length;
  const deleted = (x.body.pulled_deleted || []).length;
  lanStatus(`回测产物已同步：新目录 ${runs}，删除标记 ${deleted}`, true);
  refreshPeers();
}

async function syncMarket(machineId) {
  lanStatus("正在同步行情…", true);
  const x = await withProgressPoll(async () => {
    const r = await fetch("/api/lan/sync/market", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(machineId ? {machine_id: machineId} : {}),
    });
    const body = await r.json();
    return {ok: r.ok, body};
  });
  if (!x.ok) {
    lanStatus(x.body.message || "同步行情失败", false);
    return;
  }
  const copied = (x.body.local && x.body.local.copied) || 0;
  const peers = (x.body.peers || []).length;
  lanStatus(`行情已从 ${x.body.source_machine_id || "本机"} 同步：本机写入 ${copied} 个文件，已通知 ${peers} 台`, true);
}

if ($("lan-sync-results")) {
  $("lan-sync-results").onclick = syncResults;
  $("lan-sync-market").onclick = () => syncMarket("");
  $("lan-market-0400").onchange = async () => {
    const r = await fetch("/api/settings", {
      method: "PUT",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({lan: {market_sync_at_0400: $("lan-market-0400").checked}}),
    });
    lanStatus(r.ok ? ($("lan-market-0400").checked ? "已打开每天 04:00 自动同步行情" : "已关闭每天 04:00 自动同步行情") : "保存失败", r.ok);
  };
  refreshPeers();
  if (lastLanProgress.status && lastLanProgress.status !== "idle") applyProgress(lastLanProgress);
  refreshProgress();
  setInterval(refreshPeers, 5000);
  setInterval(refreshProgress, 1000);
}
