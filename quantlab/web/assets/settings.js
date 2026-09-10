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
  $("compute-hint").textContent = `本机 ${x.compute_hint.cpu_count} 核、约 ${x.compute_hint.ram_gb} GB 内存。折并行建议不超过 ${x.compute_hint.safe_fold_workers}；分层净值建议 ${x.compute_hint.safe_bucket_workers}。同时回测固定 1 条，不会根据历史消耗自动加路。留空时的自动核数（${x.compute_hint.auto_workers}）只适合折训练，不要用到分层进程。`;
  const machine = x.machine || {};
  if ($("machine-id")) $("machine-id").value = machine.machine_id || "";
  if ($("serial-prefix")) $("serial-prefix").value = machine.serial_prefix == null ? "" : String(machine.serial_prefix);
  if ($("lan-market-0400")) $("lan-market-0400").checked = !!(x.lan && x.lan.market_sync_at_0400);
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

function renderPeers(peers) {
  const root = $("lan-peers");
  if (!root) return;
  root.replaceChildren();
  if (!peers.length) {
    const empty = document.createElement("p");
    empty.className = "lan-empty";
    empty.textContent = "还没有发现开着的 QuantLab。请确认各机已用默认地址启动（监听 0.0.0.0:8765），且防火墙放行 TCP 8765 和 UDP/TCP 8766。";
    root.appendChild(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "lan-peers";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["主机", "机器码", "地址", "序号前缀", "页面", ""]) {
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
    action.appendChild(button);
    row.append(hostname, machine, address, prefix, page, action);
    body.appendChild(row);
  }
  table.appendChild(body);
  root.appendChild(table);
}

async function refreshPeers() {
  if (!$("lan-peers")) return;
  try {
    const r = await fetch("/api/lan/peers");
    const x = await r.json();
    renderPeers(x.peers || []);
  } catch {
    lanStatus("无法读取局域网机器列表", false);
  }
}

async function syncResults() {
  lanStatus("正在同步回测产物…", true);
  const r = await fetch("/api/lan/sync/results", {method: "POST"});
  const x = await r.json();
  if (!r.ok) {
    lanStatus(x.message || "同步回测产物失败", false);
    return;
  }
  const runs = (x.pulled_runs || []).length;
  const deleted = (x.pulled_deleted || []).length;
  lanStatus(`回测产物已同步：新目录 ${runs}，删除标记 ${deleted}`, true);
  refreshPeers();
}

async function syncMarket(machineId) {
  lanStatus("正在同步行情…", true);
  const r = await fetch("/api/lan/sync/market", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(machineId ? {machine_id: machineId} : {}),
  });
  const x = await r.json();
  if (!r.ok) {
    lanStatus(x.message || "同步行情失败", false);
    return;
  }
  const copied = (x.local && x.local.copied) || 0;
  const peers = (x.peers || []).length;
  lanStatus(`行情已从 ${x.source_machine_id || "本机"} 同步：本机写入 ${copied} 个文件，已通知 ${peers} 台`, true);
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
  setInterval(refreshPeers, 5000);
}
