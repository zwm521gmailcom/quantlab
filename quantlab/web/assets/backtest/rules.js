      const q = (id) => document.getElementById(id);
      const draftQuery = new URLSearchParams(window.location.search).get("draft_id");
      let openDraft = null;
      let submissionToken = crypto.randomUUID();
      const OPEN_LIMIT_EXPR = "open < up_limit AND open > down_limit";

      function rememberDraft(draftId) {
        const id = String(draftId || "").trim();
        if (!id) return;
        const url = new URL(window.location.href);
        url.searchParams.set("draft_id", id);
        history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
      }

      function showError(error) {
        q("status").textContent = "● 配置有误";
        q("status").classList.add("status-error");
        q("banner").classList.add("banner-error");
        q("banner-text").textContent = error.message || "请求失败";
      }

      function showStatus(text) {
        q("status").textContent = `● ${text}`;
        q("status").classList.remove("status-error");
      }

      function showBanner(text, error = false) {
        q("banner-text").textContent = text;
        q("banner").classList.toggle("banner-error", error);
      }

      async function request(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.message || payload.detail?.message || `HTTP ${response.status}`);
        return payload;
      }

      function datasetIds() {
        const option = q("dataset").selectedOptions[0];
        return {
          dataset_id: option?.dataset?.dsId || "ds_canonical_market",
          dataset_version_id: option?.dataset?.dsVersion || "current",
        };
      }

      function ensureDatasetOption(dsId, dsVersion) {
        const select = q("dataset");
        const value = `${dsId}::${dsVersion}`;
        if (![...select.options].some((option) => option.value === value)) {
          const option = document.createElement("option");
          option.value = value;
          option.dataset.dsId = dsId;
          option.dataset.dsVersion = dsVersion;
          option.textContent = `${dsId} · ${dsVersion}`;
          select.append(option);
        }
        select.value = value;
      }

      function strategyParams() {
        return {
          rsi_low: Number(q("rsi-low").value || 40),
          rsi_high: Number(q("rsi-high").value || 72),
          top_n: Number(q("topN").value || 10),
        };
      }

      function dateWindow() {
        const date_from = q("date-from").value || "2018-01-01";
        const date_to = q("date-to").value || "2026-09-07";
        return {
          date_from,
          date_to,
          filter: {
            st_status: 0,
            suspended: false,
            expressions: [
              "st_status == 0",
              "is_suspended == 0",
              "close > low",
              "close != up_limit AND close != down_limit",
            ],
          },
        };
      }

      function configValue() {
        const strategy_params = strategyParams();
        const window = dateWindow();
        const dataset = datasetIds();
        return {
          submission_token: submissionToken,
          name: q("name").value,
          dataset_id: dataset.dataset_id,
          dataset_version_id: dataset.dataset_version_id,
          strategy_entity_id: null,
          strategy_version_id: null,
          factor_versions: [],
          model: {},
          kind: "rule_signal",
          rule_strategy_id: q("rule-strategy").value || "wiki_trend_follow",
          account_mode: "target_weight_exits",
          stock_scope: "中国A股（SH/SZ）",
          train: window,
          test: window,
          top_n: Number(q("topN").value),
          weighting: "equal",
          rebalance_every: Number(q("rebalance").value),
          signal_time: "close",
          buy_price: "open",
          sell_price: "close",
          buy_fee_rate: Number(q("buyFee").value),
          buy_fee_minimum: Number(q("buyMin").value),
          sell_fee_rate: Number(q("sellFee").value),
          sell_fee_minimum: Number(q("sellMin").value),
          stamp_tax_rate: Number(q("stampTax").value),
          skip_open_limit: true,
          skip_close_down_limit: true,
          trade_filters: {open: [OPEN_LIMIT_EXPR]},
          open_when_benchmark_gt_ma200: false,
          stop_loss: Number(q("stop-loss").value || 0.10),
          take_profit: Number(q("take-profit").value || 0.25),
          max_hold_days: Number(q("max-hold-days").value || 45),
          strategy_params,
          params: strategy_params,
          slippage: Number(q("slippage").value || 0.0005),
          lot_size: 100,
          unfilled_policy: "keep_cash",
          initial_capital: 1000000,
          benchmark: q("benchmark").value,
          missing_policy: {valuation: "drop", technical: "drop"},
        };
      }

      function applyConfig(configPayload) {
        q("name").value = configPayload.name || "Wiki 多指标趋势跟踪";
        if (configPayload.dataset_id && configPayload.dataset_version_id) {
          ensureDatasetOption(configPayload.dataset_id, configPayload.dataset_version_id);
        }
        const test = configPayload.test || {};
        if (test.date_from) q("date-from").value = String(test.date_from).slice(0, 10);
        if (test.date_to) q("date-to").value = String(test.date_to).slice(0, 10);
        if (configPayload.rule_strategy_id) q("rule-strategy").value = configPayload.rule_strategy_id;
        const params = configPayload.strategy_params || configPayload.params || {};
        if (params.rsi_low != null) q("rsi-low").value = params.rsi_low;
        if (params.rsi_high != null) q("rsi-high").value = params.rsi_high;
        if (configPayload.top_n != null) q("topN").value = configPayload.top_n;
        if (configPayload.rebalance_every != null) q("rebalance").value = configPayload.rebalance_every;
        if (configPayload.stop_loss != null) q("stop-loss").value = configPayload.stop_loss;
        if (configPayload.take_profit != null) q("take-profit").value = configPayload.take_profit;
        if (configPayload.max_hold_days != null) q("max-hold-days").value = configPayload.max_hold_days;
        if (configPayload.benchmark) q("benchmark").value = configPayload.benchmark;
        if (configPayload.buy_fee_rate != null) q("buyFee").value = configPayload.buy_fee_rate;
        if (configPayload.sell_fee_rate != null) q("sellFee").value = configPayload.sell_fee_rate;
        if (configPayload.stamp_tax_rate != null) q("stampTax").value = configPayload.stamp_tax_rate;
        if (configPayload.buy_fee_minimum != null) q("buyMin").value = configPayload.buy_fee_minimum;
        if (configPayload.sell_fee_minimum != null) q("sellMin").value = configPayload.sell_fee_minimum;
        if (configPayload.slippage != null) q("slippage").value = configPayload.slippage;
      }

      async function saveCurrent() {
        const payload = await request("/api/backtests/drafts", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({
            config: configValue(),
            draft_id: openDraft?.draft_id || null,
            expected_revision: openDraft?.revision || null,
          }),
        });
        openDraft = {draft_id: payload.draft_id, revision: payload.revision};
        rememberDraft(openDraft.draft_id);
        q("run-id").textContent = `草稿 ID：${openDraft.draft_id}`;
        showStatus("配置已保存为草稿，尚未生成运行 ID");
        showBanner("配置草稿已保存，未创建 BacktestRun；开始回测才会生成正式运行 ID。");
        q("output").textContent = JSON.stringify(payload.config, null, 2);
        q("output").classList.remove("hidden");
      }

      async function boot() {
        try {
          if (draftQuery) {
            const draft = await request(`/api/backtests/drafts/${encodeURIComponent(draftQuery)}`);
            openDraft = {draft_id: draft.draft_id, revision: draft.revision};
            if (draft.complete) applyConfig(draft.config);
            showStatus(`已载入草稿 ${draft.draft_id}（rev ${draft.revision}）`);
            showBanner("已从草稿载入配置；保存会继续更新同一草稿，开始回测才提交运行。");
          }
        } catch (error) {
          showError(error);
        }
      }

      q("save").onclick = async () => {
        try { await saveCurrent(); } catch (error) { showError(error); }
      };
      q("start").onclick = async () => {
        try {
          submissionToken = crypto.randomUUID();
          q("start").disabled = true;
          const payload = await request("/api/backtests", {
            method: "POST",
            headers: {"content-type": "application/json"},
            body: JSON.stringify(configValue()),
          });
          const runId = payload.run_id;
          q("run-id").textContent = runId;
          q("stop-backtest").disabled = false;
          showStatus(`已提交运行：${q("name").value}`);
          showBanner("规则回测已提交，正在按信号表与账户规则执行。");
          try {
            const executed = await request(`/api/backtests/${encodeURIComponent(runId)}/execute`, {method: "POST"});
            if (executed.status === "failed" && String(executed.error_message || "").includes("强行停止")) {
              showBanner("已强行停止运算，页面服务未停。");
              return;
            }
          } finally {
            q("stop-backtest").disabled = true;
          }
          window.location.assign(`/backtests/runs/${encodeURIComponent(runId)}`);
        } catch (error) {
          q("start").disabled = false;
          q("stop-backtest").disabled = true;
          showError(error);
        }
      };
      q("stop-backtest").onclick = async () => {
        const runId = (q("run-id").textContent || "").trim();
        if (!runId || runId.includes("开始回测")) {
          showBanner("还没有正在运行的回测。", true);
          return;
        }
        q("stop-backtest").disabled = true;
        try {
          await request(`/api/backtests/${encodeURIComponent(runId)}/stop`, {method: "POST"});
          showBanner("已强行停止运算，页面服务未停。");
          showStatus("已强行停止");
        } catch (error) {
          q("stop-backtest").disabled = false;
          showError(error);
        }
      };
      window.configValue = configValue;
      boot();
    
