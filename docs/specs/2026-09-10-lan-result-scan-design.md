# 局域网回测产物扫描入库

日期：2026-09-10

## 决定

不换 SQLite，不开数据库端口。三台各有一份库。局域网只同步文件（`data/`、`quantlab_runtime/results/` 等）。同步后本机扫描 `results/<run_id>/run.json`，把缺的回测写入本地库。

## 机器码

- 每台首次启动生成 `quantlab_runtime/config/machine.json`（`machine_id` + `serial_prefix`）。
- **不要同步这个文件**（与 `settings.json`、token 一样留在各台）。
- 写入 `run.json`、`config_json.machine_id`、`metrics_json.resources.machine_id`。
- 启动或打开结果档案时，把本机库和产物里**缺失**的机器码补上：优先用已有的 `run.json` / 配置 / 指标；都没有则给本机自己跑的旧回测盖上当前机器码。已有的不改。从对端扫入、且任何地方都没有机器码的，不冒充本机。
- 三台都要完成一次补齐（重启服务或打开结果档案即可），再拷 `results/`。
- 运行 ID 仍是 `YYYYMMDD-HHMMSS-NNNN`；NNNN 用 `serial_prefix * 100 + 序号`，降低三台同一秒撞号。
- 资源先验只使用**本机** `machine_id` 的样本。

## 清单

每次回测结束写 `quantlab_runtime/results/<run_id>/run.json`（配置、状态、步骤、产物文件名、机器码）。启动时：先把本机库里还没有清单的运行补写出 `run.json`，再把磁盘上有清单、库里没有的运行导入。打开结果档案列表时再扫一次（服务开着时拷完文件也能看见）。

导入走现有状态机：`queued → running → completed|failed`。对端没有对应数据集/策略版本时，外键留空，配置仍留在 `config_json`。已有相同 `run_id` 不覆盖。扫描默认只增不删。

## 删除传播

任意一台在结果档案里删除一条回测时：

1. 先写 `quantlab_runtime/results/_deleted/<run_id>.json`（`run_id`、`machine_id`、`deleted_at`）。
2. 再删本机库记录和 `results/<run_id>/` 目录。
3. 标记和 `results/` 一起拷。另外两台启动或打开结果档案时看到标记：从本机库删掉这条，清掉对应目录，**不再扫入**。有标记的 ID 即使 `run.json` 又被拷回来也不恢复。

标记放在独立的 `_deleted/`，不放在回测目录内，避免对端把整份结果拷回来盖掉标记。拷 `results/` 时把 `_deleted/` **合并**进去（多的标记都留着）。不要对整个 `results/` 做镜像删除，否则一台没有的回测会把另一台还在的结果干掉。不提供恢复。

## 明确不做

- 同步或合并 `.sqlite3`
- 因子/模型目录的完整双向同步（本阶段只做回测产物）
- 实时文件监视
- 从删除标记恢复回测
