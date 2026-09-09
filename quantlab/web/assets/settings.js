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
  $("compute-hint").textContent = `本机 ${x.compute_hint.cpu_count} 核、约 ${x.compute_hint.ram_gb} GB 内存。折并行建议不超过 ${x.compute_hint.safe_fold_workers}；分层净值建议 ${x.compute_hint.safe_bucket_workers}。留空时的自动核数（${x.compute_hint.auto_workers}）只适合折训练，不要用到分层进程。`;
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
  $("saved").textContent = "已恢复默认值（含运算资源）";
  $("compute-saved").textContent = "";
  load();
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
