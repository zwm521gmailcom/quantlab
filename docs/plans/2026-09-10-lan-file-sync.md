# 局域网文件同步 Implementation Plan

> **For agentic workers:** 按任务顺序 TDD。不要 git commit，除非用户当场要求。

**Goal:** 设置页能看到局域网里开着的 QuantLab，一点同步回测产物（多轮互拉），行情单独从指定机拉，可开 04:00 自动（本机当源头）。

**Architecture:** UI 仍 127.0.0.1:8765。8766 上 UDP 宣告 + 只提供文件索引/下载的 FastAPI。本机 API 协调拉取。结束后调用已有 `sync_result_catalog`。

**Tech Stack:** FastAPI、httpx、UDP、现有 settings.json。

---

文件：`quantlab/services/lan_files.py`、`lan_peers.py`、`lan_sync.py`；`quantlab/api/lan_app.py`、`quantlab/api/routes/lan.py`；`cli.py` 起 8766；设置页一块「局域网」。
