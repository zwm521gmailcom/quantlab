      const q = (id) => document.getElementById(id);
      let openDraft = null;
      const DRAFT_STORAGE_KEY = "quantlab-backtest-last-draft-id";
      const PLAN_STORAGE_KEY = "quantlab-backtest-optional-plan-id";
      let publishedFactors = [];
      let publishedStrategies = [];
      let modelVersions = [];
      let factorCatalog = [];
      let selectedFactors = [];
      let factorMenuIndex = 0;
      let submissionToken = crypto.randomUUID();
      const DEFAULT_OPEN_LIMIT_EXPR = "open < up_limit AND open > down_limit";
      const DEFAULT_STOCK_EXPRS = [
        "st_status == 0",
        "is_suspended == 0",
        "close > low",
        "close != up_limit AND close != down_limit",
      ];
      let datasetFields = ["open", "close", "low", "hfq_open", "hfq_close", "up_limit", "down_limit", "st_status", "is_suspended"];

      function draftIdFromLocation() {
        return new URLSearchParams(window.location.search).get("draft_id");
      }

      function storedDraftId() {
        try { return localStorage.getItem(DRAFT_STORAGE_KEY); } catch (_error) { return null; }
      }

      function rememberDraft(draftId) {
        const id = String(draftId || "").trim();
        if (!id) return;
        const url = new URL(window.location.href);
        url.searchParams.set("draft_id", id);
        history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
        try { localStorage.setItem(DRAFT_STORAGE_KEY, id); } catch (_error) {}
      }

      function applyBlankFilters() {
        setOpenFilterExpressions([DEFAULT_OPEN_LIMIT_EXPR]);
        setExpressions("pretrade-stock-list", DEFAULT_STOCK_EXPRS, DEFAULT_STOCK_EXPRS);
        setExpressions("pretrade-benchmark-list", [], []);
        setExpressions("train-filter-list", [], []);
        setExpressions("test-filter-list", [], []);
      }

      function showError(error) {
        q("status").textContent = "● 配置有误";
        q("status").classList.add("status-error");
        q("banner").classList.add("banner-error");
        q("banner-text").textContent = error.message || "请求失败";
      }

      function showStatus(text, error = false) {
        q("status").textContent = `● ${text}`;
        q("status").classList.toggle("status-error", error);
      }

      function showBanner(text, error = false) {
        q("banner-text").textContent = text;
        q("banner").classList.toggle("banner-error", error);
      }

      function showOutput(text) {
        const wrap = q("output-wrap");
        const out = q("output");
        const collapse = q("output-collapse");
        out.textContent = text;
        out.classList.remove("collapsed");
        wrap?.classList.remove("hidden");
        if (collapse) collapse.textContent = "缩小";
      }

      const RUN_STEP_LABELS = {
        snapshot_validation: "快照校验",
        model_training: "模型训练",
        prediction: "预测打分",
        positions: "生成仓位",
        execution: "撮合成交",
        metrics: "指标汇总",
      };
      const RUN_STEP_STATE = {
        pending: "等待",
        running: "进行中",
        completed: "完成",
        failed: "失败",
        skipped: "跳过",
      };
      let runPollTimer = null;
      let runLogKeys = new Set();
      let activeRunId = null;

      function logStamp() {
        const now = new Date();
        const pad = (value) => String(value).padStart(2, "0");
        return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
      }

      function appendRunLog(text) {
        const el = q("run-log");
        if (!el) return;
        const line = `${logStamp()}  ${text}`;
        const prev = el.textContent.trim();
        el.textContent = (!prev || prev === "尚未开始运行。") ? line : `${prev}\n${line}`;
        el.scrollTop = el.scrollHeight;
      }

      function logRunOnce(key, text) {
        if (runLogKeys.has(key)) return;
        runLogKeys.add(key);
        appendRunLog(text);
      }

      function resetRunStatus(runId) {
        runLogKeys = new Set();
        activeRunId = runId;
        document.querySelectorAll("#run-steps [data-step]").forEach((li) => {
          li.dataset.status = "pending";
          const state = li.querySelector(".run-step-state");
          if (state) state.textContent = RUN_STEP_STATE.pending;
        });
        const link = q("run-record-link");
        if (link) {
          link.classList.add("hidden");
          link.removeAttribute("href");
        }
        if (q("run-log")) q("run-log").textContent = "";
        logRunOnce("submitted", `已提交运行 ${runId}，进入六步流程`);
        setStopEnabled(true);
      }

      function stopRunPoll() {
        if (runPollTimer) {
          clearInterval(runPollTimer);
          runPollTimer = null;
        }
      }

      function setStopEnabled(on) {
        const button = q("stop-backtest");
        if (button) button.disabled = !on;
      }

      function applyRunStatus(payload) {
        const steps = payload.steps || [];
        let runningLabel = "";
        steps.forEach((step) => {
          const name = step.step_name;
          const status = step.status || "pending";
          const label = RUN_STEP_LABELS[name] || name;
          const li = document.querySelector(`#run-steps [data-step="${name}"]`);
          if (li) {
            const prev = li.dataset.status;
            li.dataset.status = status;
            const state = li.querySelector(".run-step-state");
            if (state) state.textContent = RUN_STEP_STATE[status] || status;
            if (prev !== status) {
              if (status === "running") logRunOnce(`${name}:running`, `进入${label}`);
              if (status === "completed") {
                logRunOnce(`${name}:completed`, `${label}完成`);
                if (name === "model_training") logRunOnce("enter-backtest", "训练完成，进入回测");
              }
              if (status === "failed") {
                logRunOnce(`${name}:failed`, `${label}失败${step.error_message ? `：${step.error_message}` : ""}`);
              }
              if (status === "skipped") logRunOnce(`${name}:skipped`, `${label}已跳过`);
            }
          }
          if (status === "running") runningLabel = label;
        });
        if (runningLabel) showStatus(`进行中：${runningLabel}`);
        const runStatus = payload.status;
        const runId = payload.run_id || activeRunId;
        if (runStatus === "completed") {
          logRunOnce("run-done", "运行完成");
          showStatus("运行完成");
          setStopEnabled(false);
          const link = q("run-record-link");
          if (link && runId) {
            link.href = `/backtests/runs/${encodeURIComponent(runId)}`;
            link.classList.remove("hidden");
          }
        }
        if (runStatus === "failed") {
          logRunOnce("run-fail", payload.error_message || "运行失败");
          showStatus(String(payload.error_message || "").includes("强行停止") ? "已强行停止" : "运行失败", true);
          setStopEnabled(false);
        }
      }

      async function pollRunStatus(runId) {
        try {
          const payload = await request(`/api/backtests/${encodeURIComponent(runId)}/status`);
          applyRunStatus(payload);
          if (payload.status === "completed" || payload.status === "failed") stopRunPoll();
        } catch (_error) {}
      }

      function startRunPoll(runId) {
        stopRunPoll();
        pollRunStatus(runId);
        runPollTimer = setInterval(() => pollRunStatus(runId), 700);
      }

      function blockUntilPublished() {
        ["start", "save", "preview", "add-to-plan"].forEach((id) => {
          if (q(id)) q(id).disabled = true;
        });
      }

      function readSelection(select) {
        const option = select.selectedOptions[0];
        if (!option || !option.value || !option.value.includes("::")) return null;
        const [entity_id, version_id] = option.value.split("::");
        return {entity_id, version_id, name: option.textContent};
      }

      function universeFilter(prefix) {
        return {
          st_status: 0,
          suspended: false,
          close_gt_low: true,
          skip_limit_close: true,
          expressions: collectExpressions(`${prefix}-filter-list`),
        };
      }

      function filterFieldOptions() {
        const extras = ["open", "close", "low", "high", "hfq_open", "hfq_close", "up_limit", "down_limit", "st_status", "is_suspended", "sma200"];
        const skip = new Set(["date", "instrument", "trade_date", "ts_code"]);
        const names = [];
        extras.concat(datasetFields).concat(selectedFactors.map((item) => item.field)).forEach((name) => {
          if (!name || skip.has(name) || names.includes(name)) return;
          names.push(name);
        });
        return names;
      }

      function fillFieldSelect(select) {
        const current = select.value;
        select.replaceChildren();
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "选字段…";
        select.append(empty);
        filterFieldOptions().forEach((name) => {
          const option = document.createElement("option");
          option.value = name;
          option.textContent = name === "sma200" ? "sma200（200日均线）" : name;
          select.append(option);
        });
        if ([...select.options].some((option) => option.value === current)) select.value = current;
      }

      function refreshFilterFieldSelects() {
        document.querySelectorAll(".filter-expr-fields").forEach(fillFieldSelect);
      }

      function insertAtCursor(input, text) {
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? start;
        const pad = start > 0 && !/\s$/.test(input.value.slice(0, start)) ? " " : "";
        input.value = `${input.value.slice(0, start)}${pad}${text}${input.value.slice(end)}`;
        const cursor = start + pad.length + text.length;
        input.focus();
        input.setSelectionRange(cursor, cursor);
      }

      function addFilterRow(listId, expr = "") {
        const list = q(listId);
        if (!list) return;
        const row = document.createElement("div");
        row.className = "filter-expr-row";
        const input = document.createElement("input");
        input.className = "input filter-expr-input";
        input.type = "text";
        input.placeholder = "例如 close > sma200";
        input.value = expr;
        input.spellcheck = false;
        const picker = document.createElement("select");
        picker.className = "select filter-expr-fields";
        picker.setAttribute("aria-label", "插入表字段");
        const insert = document.createElement("button");
        insert.className = "btn";
        insert.type = "button";
        insert.textContent = "插入字段";
        insert.onclick = () => {
          if (picker.value) insertAtCursor(input, picker.value);
        };
        const remove = document.createElement("button");
        remove.className = "btn";
        remove.type = "button";
        remove.textContent = "删除";
        remove.onclick = () => row.remove();
        row.append(input, picker, insert, remove);
        list.append(row);
        fillFieldSelect(picker);
      }

      function collectExpressions(listId) {
        return [...document.querySelectorAll(`#${listId} .filter-expr-input`)]
          .map((input) => input.value.trim())
          .filter(Boolean);
      }

      function withoutSharedExpressions(exprs, shared) {
        const skip = new Set((shared || []).map((text) => String(text || "").trim()).filter(Boolean));
        return (exprs || []).map((text) => String(text || "").trim()).filter((text) => text && !skip.has(text));
      }

      function setExpressions(listId, items, fallback = []) {
        const list = q(listId);
        if (!list) return;
        list.replaceChildren();
        const exprs = Array.isArray(items) ? items.map((text) => String(text || "").trim()).filter(Boolean) : [];
        const rows = exprs.length ? exprs : fallback;
        if (!rows.length) addFilterRow(listId, "");
        else rows.forEach((text) => addFilterRow(listId, String(text)));
      }

      function addOpenFilterRow(expr = "") {
        addFilterRow("open-filter-list", expr);
      }

      function openFilterExpressions() {
        return collectExpressions("open-filter-list");
      }

      function setOpenFilterExpressions(items) {
        setExpressions("open-filter-list", items, [DEFAULT_OPEN_LIMIT_EXPR]);
      }

      function datasetOption(source) {
        const option = document.createElement("option");
        option.value = source ? `${source.dsId}::${source.dsVersion}` : "ds_canonical_market::current";
        option.dataset.dsId = source?.dsId || "ds_canonical_market";
        option.dataset.dsVersion = source?.dsVersion || "current";
        option.textContent = source ? `${source.dsId} · ${source.dsVersion}（由因子锁定）` : "ds_canonical_market · current";
        return option;
      }

      function fillDatasetSelect(select, source) {
        if (!select) return;
        const current = select.value;
        select.replaceChildren(datasetOption(source));
        if ([...select.options].some((option) => option.value === current)) select.value = current;
      }

      async function loadDatasetFields() {
        const dataset = q("dataset")?.selectedOptions?.[0];
        const dsId = dataset?.dataset?.dsId;
        const dsVersion = dataset?.dataset?.dsVersion;
        if (!dsId || !dsVersion) {
          refreshFilterFieldSelects();
          return;
        }
        try {
          const payload = await request(`/api/datasets/${encodeURIComponent(dsId)}/versions/${encodeURIComponent(dsVersion)}`);
          datasetFields = Array.isArray(payload.fields) ? payload.fields.map(String) : [];
        } catch (_error) {
          datasetFields = ["open", "close", "low", "hfq_open", "hfq_close", "up_limit", "down_limit", "st_status", "is_suspended"];
        }
        refreshFilterFieldSelects();
      }

      function parseRange(value, fallbackFrom, fallbackTo, stockScope, prefix) {
        const match = String(value || "").match(/(\d{4}-\d{2}-\d{2})\D+(\d{4}-\d{2}-\d{2})/);
        return {
          date_from: match ? match[1] : fallbackFrom,
          date_to: match ? match[2] : fallbackTo,
          stock_scope: stockScope,
          filter: universeFilter(prefix),
        };
      }

      function selectedKind() {
        return q("model")?.selectedOptions?.[0]?.dataset.kind || "lightgbm_tree";
      }

      function syncActionAvailability() {
        const hasModels = [...(q("model")?.options || [])].some((option) => option.value);
        const blocked = !hasModels;
        ["start", "save", "preview", "add-to-plan"].forEach((id) => {
          if (q(id)) q(id).disabled = blocked;
        });
      }

      function syncKindFields(kind) {
        const current = kind || selectedKind();
        document.querySelectorAll("[data-kinds]").forEach((node) => {
          const kinds = String(node.dataset.kinds || "").split(/\s+/).filter(Boolean);
          node.classList.toggle("hidden", !kinds.includes(current));
        });
        syncRollPeriodFields();
      }

      function syncRollPeriodFields() {
        const wrap = q("bt-roll-fields");
        if (!wrap) return;
        const kind = selectedKind();
        const mode = q("bt-walk-forward")?.value || "once";
        wrap.classList.toggle("hidden", kind === "factor_rank" || mode !== "rolling");
        clampTestPeriodToLookback();
      }

      function clampTestPeriodToLookback() {
        const train = q("bt-train-period");
        const test = q("bt-test-period");
        if (!train || !test) return;
        const lookback = Number(train.value || 12);
        const period = Number(test.value || 3);
        if (period > lookback) test.value = String(Math.max(1, lookback));
      }

      function backtestHyperparams() {
        const kind = selectedKind();
        const random_seed = Number(q("random-seed")?.value || 123);
        if (kind === "ridge_linear" || kind === "lasso") return {alpha: Number(q("bt-alpha")?.value || 1), random_seed};
        if (kind === "elastic_net") return {alpha: Number(q("bt-alpha")?.value || 0.001), l1_ratio: Number(q("bt-l1-ratio")?.value || 0.5), random_seed};
        if (kind === "huber") return {alpha: Number(q("bt-alpha")?.value || 0.0001), epsilon: Number(q("bt-epsilon")?.value || 1.35), random_seed};
        if (kind === "random_forest") return {
          number_of_trees: Number(q("bt-forest-trees")?.value || 20),
          max_depth: Number(q("bt-max-depth")?.value || 8),
          min_child_samples: Number(q("bt-forest-min-samples")?.value || 20),
          random_seed,
        };
        if (kind === "xgboost_tree") return {
          number_of_trees: Number(q("bt-xgb-trees")?.value || 5),
          max_bins: Number(q("bt-xgb-bins")?.value || 256),
          max_depth: Number(q("bt-xgb-max-depth")?.value || 6),
          min_child_samples: Number(q("bt-xgb-min-samples")?.value || 1),
          learning_rate: Number(q("bt-xgb-learning-rate")?.value || 0.1),
          label_gain: q("bt-label-gain")?.value || "linear_0_19",
          metric: q("bt-ndcg-metric")?.value || "ndcg",
          ndcg_eval_at: Number(q("bt-ndcg-eval-at")?.value || 10),
          random_seed,
        };
        if (kind === "factor_rank" || kind === "ols") return {random_seed};
        return {
          number_of_trees: Number(q("bt-trees")?.value || 5),
          max_bins: Number(q("bt-bins")?.value || 511),
          num_leaves: Number(q("bt-leaves")?.value || 30),
          min_child_samples: Number(q("bt-min-samples")?.value || 1000),
          learning_rate: Number(q("bt-learning-rate")?.value || 0.1),
          label_gain: q("bt-label-gain")?.value || "linear_0_19",
          metric: q("bt-ndcg-metric")?.value || "ndcg",
          ndcg_eval_at: Number(q("bt-ndcg-eval-at")?.value || 10),
          ndcg_discount_base: Number(q("bt-ndcg-discount-base")?.value || 1),
          random_seed,
        };
      }

      function latestPublished(item) {
        const published = (item.versions || []).filter((version) => version.status === "published");
        return published.at(-1) || null;
      }

      function boundStrategyForModel(model) {
        if (!model?.entity_id) return null;
        const wanted = `${model.entity_id}::${model.version_id}`;
        for (const item of publishedStrategies) {
          const latest = latestPublished(item);
          if (!latest) continue;
          if (`${latest.model_entity_id}::${latest.model_version_id}` === wanted) {
            return {entity_id: item.entity_id, version_id: latest.version_id};
          }
        }
        const fallback = publishedStrategies.find((item) => item.entity_id === `strategy_${model.entity_id}`);
        const latest = fallback ? latestPublished(fallback) : null;
        return latest ? {entity_id: fallback.entity_id, version_id: latest.version_id} : null;
      }

      function configValue() {
        const dataset = q("dataset")?.selectedOptions?.[0];
        const model = readSelection(q("model"));
        const strategy = boundStrategyForModel(model);
        const first = selectedFactors[0];
        const datasetId = dataset?.dataset?.dsId || first?.dsId || "";
        const datasetVersion = dataset?.dataset?.dsVersion || first?.dsVersion || "";
        const trainScope = q("train-scope").value;
        const testScope = q("test-scope").value;
        return {
          submission_token: submissionToken,
          name: q("name").value,
          dataset_id: datasetId,
          dataset_version_id: datasetVersion,
          train_dataset_id: datasetId,
          train_dataset_version_id: datasetVersion,
          test_dataset_id: datasetId,
          test_dataset_version_id: datasetVersion,
          strategy_entity_id: strategy?.entity_id || "",
          strategy_version_id: strategy?.version_id || "",
          factor_versions: selectedFactorRefs(),
          model: model || {},
          kind: selectedKind(),
          stock_scope: "中国A股（SH/SZ）",
          train: parseRange(q("train-from")?.value, "2019-01-01", "2019-12-31", trainScope, "train"),
          test: parseRange(q("test-from")?.value, "2020-01-02", "2020-12-31", testScope, "test"),
          top_n: Number(q("topN").value),
          weighting: q("weighting").value,
          rebalance_every: Number(q("rebalance").value),
          signal_time: "close",
          buy_price: q("buy").value,
          sell_price: q("sell").value,
          buy_fee_rate: Number(q("buyFee").value),
          buy_fee_minimum: Number(q("buyMin").value),
          sell_fee_rate: Number(q("sellFee").value),
          sell_fee_minimum: Number(q("sellMin").value),
          stamp_tax_rate: Number(q("stampTax").value),
          skip_open_limit: openFilterExpressions().some((text) => text.includes("up_limit") && text.includes("down_limit")),
          skip_close_down_limit: q("closeLimit").value !== "fill",
          trade_filters: { open: openFilterExpressions() },
          open_when_benchmark_gt_ma200: Boolean(q("hs300-gt-ma200")?.checked),
          slippage: Number(q("slippage")?.value || 0.0005),
          lot_size: 100,
          unfilled_policy: "keep_cash",
          initial_capital: 1000000,
          benchmark: q("benchmark").value,
          missing_policy: {valuation: "drop", technical: "drop"},
          walk_forward: selectedKind() === "factor_rank" ? "once" : (q("bt-walk-forward")?.value || "once"),
          train_period_months: Number(q("bt-train-period")?.value || 12),
          test_period_months: Number(q("bt-test-period")?.value || 3),
          hyperparameters: backtestHyperparams(),
          pretrade_filters: {
            stock: collectExpressions("pretrade-stock-list"),
            benchmark: collectExpressions("pretrade-benchmark-list"),
          },
        };
      }

      function selectedFactorRefs() {
        return selectedFactors.map((item) => ({
          factor_id: item.factorId,
          version_id: item.versionId || "v1",
          field: item.field,
        }));
      }

      function factorLabel(item) {
        if (item.name && item.field && item.name !== item.field) return `${item.name}（${item.field}）`;
        return item.field || item.name || "因子";
      }

      function catalogFromPublished() {
        return publishedFactors
          .filter((item) => item.status === "published")
          .map((item) => {
            const field = fieldName(item.factor_id || item.factor_entity_id);
            return {
              field,
              factorId: item.factor_entity_id || `factor_${field}`,
              versionId: item.factor_version_id || item.version_id || "v1",
              name: item.name || field,
              dsId: item.dataset_id,
              dsVersion: item.dataset_version_id,
            };
          })
          .filter((item) => item.field);
      }

      function matchCatalog(query) {
        const text = String(query || "").trim().toLowerCase();
        return factorCatalog.filter((item) => {
          if (selectedFactors.some((row) => row.field === item.field)) return false;
          if (!text) return true;
          return [item.name, item.field, item.factorId].some((part) => String(part || "").toLowerCase().includes(text));
        });
      }

      function parseManualFactor(raw) {
        const text = String(raw || "").trim();
        if (!text) return null;
        const lower = text.toLowerCase();
        const exact = factorCatalog.find((item) => [item.field, item.name, item.factorId].some((part) => String(part || "").toLowerCase() === lower));
        if (exact) return exact;
        const filtered = matchCatalog(text);
        if (filtered.length === 1) return filtered[0];
        const field = text.replace(/^factor_/, "").replace(/\s+/g, "_");
        if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(field)) return null;
        const dataset = q("dataset")?.selectedOptions?.[0];
        return {
          field,
          factorId: `factor_${field}`,
          versionId: "v1",
          name: field,
          dsId: dataset?.dataset?.dsId || "",
          dsVersion: dataset?.dataset?.dsVersion || "",
        };
      }

      function addFactor(item) {
        if (!item?.field) return;
        if (selectedFactors.some((row) => row.field === item.field)) return;
        selectedFactors.push(item);
        renderFactorCombo();
        refreshFilterFieldSelects();
      }

      function removeFactor(field) {
        selectedFactors = selectedFactors.filter((item) => item.field !== field);
        renderFactorCombo();
        refreshFilterFieldSelects();
      }

      function renderFactorCombo() {
        const chips = q("factor-chips");
        const menu = q("factor-menu");
        const input = q("factor-search");
        if (!chips || !menu) return;
        chips.replaceChildren();
        selectedFactors.forEach((item) => {
          const chip = document.createElement("span");
          chip.className = "factor-chip";
          chip.append(document.createTextNode(factorLabel(item)));
          const remove = document.createElement("button");
          remove.type = "button";
          remove.setAttribute("aria-label", `移除 ${item.field}`);
          remove.textContent = "×";
          remove.addEventListener("click", (event) => {
            event.stopPropagation();
            removeFactor(item.field);
            q("factor-search")?.focus();
          });
          chip.append(remove);
          chips.append(chip);
        });
        const matches = matchCatalog(input?.value);
        menu.replaceChildren();
        if (!factorCatalog.length && !String(input?.value || "").trim()) {
          const empty = document.createElement("div");
          empty.className = "factor-combo-empty";
          empty.textContent = "暂无已发布因子。也可以直接输入字段名后回车。";
          menu.append(empty);
        } else if (!matches.length) {
          const empty = document.createElement("div");
          empty.className = "factor-combo-empty";
          empty.textContent = String(input?.value || "").trim() ? `回车后按字段「${input.value.trim()}」加入` : "没有更多可选项";
          menu.append(empty);
        } else {
          matches.forEach((item, index) => {
            const option = document.createElement("button");
            option.type = "button";
            option.className = "factor-combo-option" + (index === factorMenuIndex ? " active" : "");
            option.textContent = `${item.name}（${item.field} · ${item.versionId}）`;
            option.addEventListener("mousedown", (event) => {
              event.preventDefault();
              addFactor(item);
              if (input) input.value = "";
              factorMenuIndex = 0;
              renderFactorCombo();
            });
            menu.append(option);
          });
        }
      }

      function bindFactorCombo() {
        const combo = q("factors");
        const input = q("factor-search");
        const menu = q("factor-menu");
        if (!combo || !input || !menu || combo.dataset.bound === "1") return;
        combo.dataset.bound = "1";
        const openMenu = () => menu.classList.remove("hidden");
        const closeMenu = () => menu.classList.add("hidden");
        combo.addEventListener("click", () => {
          input.focus();
          openMenu();
          renderFactorCombo();
        });
        input.addEventListener("focus", () => {
          openMenu();
          renderFactorCombo();
        });
        input.addEventListener("input", () => {
          factorMenuIndex = 0;
          openMenu();
          renderFactorCombo();
        });
        input.addEventListener("keydown", (event) => {
          const matches = matchCatalog(input.value);
          if (event.key === "ArrowDown") {
            event.preventDefault();
            factorMenuIndex = Math.min(Math.max(matches.length - 1, 0), factorMenuIndex + 1);
            openMenu();
            renderFactorCombo();
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            factorMenuIndex = Math.max(0, factorMenuIndex - 1);
            renderFactorCombo();
          } else if (event.key === "Enter") {
            event.preventDefault();
            const picked = matches[factorMenuIndex] || parseManualFactor(input.value);
            if (picked) {
              addFactor(picked);
              input.value = "";
              factorMenuIndex = 0;
              renderFactorCombo();
            }
          } else if (event.key === "Backspace" && !input.value && selectedFactors.length) {
            selectedFactors.pop();
            renderFactorCombo();
          } else if (event.key === "Escape") {
            closeMenu();
          }
        });
        document.addEventListener("click", (event) => {
          if (!combo.contains(event.target)) closeMenu();
        });
      }

      function applyModelParams(params, kind) {
        if (kind) syncKindFields(kind);
        if (!params) return;
        if (params.random_seed != null && q("random-seed")) q("random-seed").value = params.random_seed;
        if (q("bt-walk-forward") && params.walk_forward != null && String(params.walk_forward) !== "") {
          const walk = String(params.walk_forward).toLowerCase();
          q("bt-walk-forward").value = walk === "once" || walk === "" ? "once" : "rolling";
        }
        if (q("bt-train-period") && (params.train_lookback_months != null || params.train_period_months != null)) {
          q("bt-train-period").value = params.train_lookback_months || params.train_period_months || 12;
        }
        if (q("bt-test-period") && params.test_period_months != null) {
          q("bt-test-period").value = params.test_period_months;
        }
        clampTestPeriodToLookback();
        syncRollPeriodFields();
        if (kind === "lightgbm_tree") {
          if (params.number_of_trees != null && q("bt-trees")) q("bt-trees").value = params.number_of_trees;
          if (params.max_bins != null && q("bt-bins")) q("bt-bins").value = params.max_bins;
          if (params.num_leaves != null && q("bt-leaves")) q("bt-leaves").value = params.num_leaves;
          if (params.min_child_samples != null && q("bt-min-samples")) q("bt-min-samples").value = params.min_child_samples;
          if (params.learning_rate != null && q("bt-learning-rate")) q("bt-learning-rate").value = params.learning_rate;
          if (q("bt-label-gain")) q("bt-label-gain").value = params.label_gain || "linear_0_19";
          if (q("bt-ndcg-metric")) q("bt-ndcg-metric").value = params.metric || "ndcg";
          if (params.ndcg_eval_at != null && q("bt-ndcg-eval-at")) q("bt-ndcg-eval-at").value = params.ndcg_eval_at;
          if (params.ndcg_discount_base != null && q("bt-ndcg-discount-base")) q("bt-ndcg-discount-base").value = params.ndcg_discount_base;
          return;
        }
        if (kind === "xgboost_tree") {
          if (params.number_of_trees != null && q("bt-xgb-trees")) q("bt-xgb-trees").value = params.number_of_trees;
          if (params.max_bins != null && q("bt-xgb-bins")) q("bt-xgb-bins").value = params.max_bins;
          if (params.max_depth != null && q("bt-xgb-max-depth")) q("bt-xgb-max-depth").value = params.max_depth;
          if (params.min_child_samples != null && q("bt-xgb-min-samples")) q("bt-xgb-min-samples").value = params.min_child_samples;
          if (params.learning_rate != null && q("bt-xgb-learning-rate")) q("bt-xgb-learning-rate").value = params.learning_rate;
          if (q("bt-label-gain")) q("bt-label-gain").value = params.label_gain || "linear_0_19";
          if (q("bt-ndcg-metric")) q("bt-ndcg-metric").value = params.metric || "ndcg";
          if (params.ndcg_eval_at != null && q("bt-ndcg-eval-at")) q("bt-ndcg-eval-at").value = params.ndcg_eval_at;
          return;
        }
        if (kind === "random_forest") {
          if (params.number_of_trees != null && q("bt-forest-trees")) q("bt-forest-trees").value = params.number_of_trees;
          if (params.max_depth != null && q("bt-max-depth")) q("bt-max-depth").value = params.max_depth;
          if (params.min_child_samples != null && q("bt-forest-min-samples")) q("bt-forest-min-samples").value = params.min_child_samples;
          return;
        }
        if (kind === "ridge_linear" || kind === "lasso" || kind === "elastic_net" || kind === "huber") {
          if (params.alpha != null && q("bt-alpha")) q("bt-alpha").value = params.alpha;
        }
        if (kind === "elastic_net" && params.l1_ratio != null && q("bt-l1-ratio")) q("bt-l1-ratio").value = params.l1_ratio;
        if (kind === "huber" && params.epsilon != null && q("bt-epsilon")) q("bt-epsilon").value = params.epsilon;
      }

      async function request(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.message || payload.detail?.message || `HTTP ${response.status}`);
        return payload;
      }

      function fieldName(entityId) {
        return String(entityId || "").replace(/^factor_/, "");
      }

      function renderFactorOptions() {
        bindFactorCombo();
        factorCatalog = catalogFromPublished();
        const source = factorCatalog[0];
        fillDatasetSelect(q("dataset"), source);
        renderFactorCombo();
      }

      function requestedModelValue(model) {
        const search = new URLSearchParams(window.location.search);
        const wanted = search.get("model");
        const kindWanted = search.get("kind");
        const options = [...model.options];
        if (wanted) {
          const exact = options.find((option) => option.value === wanted);
          if (exact) return exact.value;
          const entity = wanted.split("::")[0];
          const byEntity = options.find((option) => option.value.split("::")[0] === entity);
          if (byEntity) return byEntity.value;
        }
        if (kindWanted) {
          const byKind = options.find((option) => option.dataset.kind === kindWanted);
          if (byKind) return byKind.value;
        }
        return options[0]?.value || "";
      }

      function requestedHyperparams() {
        const raw = new URLSearchParams(window.location.search).get("hp");
        if (!raw) return null;
        try {
          const parsed = JSON.parse(raw);
          return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
        } catch (_error) {
          return null;
        }
      }

      function renderModelOptions() {
        const model = q("model");
        model.replaceChildren();
        publishedStrategies = publishedStrategies.filter((item) => item.status === "published" && (item.versions || []).some((version) => version.status === "published"));
        modelVersions = modelVersions.filter((item) => item.status === "published" && (item.versions || []).some((version) => version.status === "published"));
        if (!modelVersions.length) {
          const modelEmpty = document.createElement("option");
          modelEmpty.value = "";
          modelEmpty.textContent = "暂无已发布模型版本";
          model.append(modelEmpty);
          blockUntilPublished();
          showBanner("当前没有已发布的模型。请先在模型中心登记一种模型。", true);
          syncActionAvailability();
          return;
        }
        const seenKinds = new Set();
        [...modelVersions].reverse().forEach((item) => {
          const latest = latestPublished(item);
          if (!latest) return;
          const kind = latest.config?.kind || item.entity_id;
          if (seenKinds.has(kind)) return;
          seenKinds.add(kind);
          const option = document.createElement("option");
          option.value = `${item.entity_id}::${latest.version_id}`;
          option.textContent = item.name;
          option.dataset.params = JSON.stringify(latest.config?.hyperparameters || {});
          option.dataset.factors = JSON.stringify(latest.factor_versions || []);
          option.dataset.kind = kind;
          model.append(option);
        });
        const search = new URLSearchParams(window.location.search);
        model.value = requestedModelValue(model);
        const applySelected = () => {
          const selected = model.selectedOptions[0];
          if (!selected?.value) return;
          applyModelParams(JSON.parse(selected.dataset.params || "{}"), selected.dataset.kind);
          showBanner("已选中模型。因子在本页选，参数可改；开始回测时才按这种模型处理。");
        };
        model.addEventListener("change", applySelected);
        applySelected();
        const hp = requestedHyperparams();
        const selected = model.selectedOptions[0];
        if (hp && selected?.value) {
          applyModelParams(hp, selected.dataset.kind || search.get("kind"));
        }
        if (search.get("model") || search.get("kind")) {
          showBanner("已载入模型种类。因子在本页「因子组合」里选，参数可改；开始回测时才按这种模型处理。");
        }
      }

      async function refreshTestUsage() {
        const note = q("test-usage-note");
        if (!note) return;
        try {
          const payload = await request("/api/backtests/validate", {
            method: "POST",
            headers: {"content-type": "application/json"},
            body: JSON.stringify(configValue()),
          });
          const count = Number(payload.test_usage_count || 0);
          note.textContent = count >= 1
            ? `这段 test 已用于 ${count} 次已完成回测`
            : "这段 test 尚未用于已完成回测。";
        } catch (_error) {
          note.textContent = "暂时无法读取这段 test 的使用次数。";
        }
      }

      function expressionsFromFilter(filter) {
        const source = filter && typeof filter === "object" ? filter : {};
        const exprs = Array.isArray(source.expressions) ? source.expressions.map((text) => String(text || "").trim()).filter(Boolean) : [];
        if (source.close_gt_ma200) exprs.push("hfq_close > sma200");
        if (source.close_lt_ma200) exprs.push("hfq_close < sma200");
        return [...new Set(exprs)];
      }

      function applyConfig(configPayload) {
        q("name").value = configPayload.name || "未命名回测";
        selectedFactors = [];
        (configPayload.factor_versions || []).forEach((factor) => {
          const factorId = factor.factor_id || `factor_${factor.field || ""}`;
          const field = fieldName(factor.field || factorId);
          const found = factorCatalog.find((item) => item.field === field || item.factorId === factorId);
          if (found) selectedFactors.push(found);
          else if (field) selectedFactors.push({field, factorId, versionId: factor.version_id || "v1", name: field});
        });
        renderFactorCombo();
        const datasetId = configPayload.dataset_id || configPayload.train_dataset_id;
        const datasetVersion = configPayload.dataset_version_id || configPayload.train_dataset_version_id;
        const datasetTarget = [...(q("dataset")?.options || [])].find((option) => option.dataset.dsId === datasetId && option.dataset.dsVersion === datasetVersion);
        if (datasetTarget) q("dataset").value = datasetTarget.value;
        q("train-scope").value = configPayload.train?.stock_scope || configPayload.stock_scope || q("train-scope").value;
        q("test-scope").value = configPayload.test?.stock_scope || configPayload.stock_scope || q("test-scope").value;
        q("topN").value = configPayload.top_n ?? q("topN").value;
        q("weighting").value = configPayload.weighting || q("weighting").value;
        q("rebalance").value = configPayload.rebalance_every ?? q("rebalance").value;
        q("benchmark").value = configPayload.benchmark || q("benchmark").value;
        q("buyFee").value = configPayload.buy_fee_rate ?? q("buyFee").value;
        q("sellFee").value = configPayload.sell_fee_rate ?? q("sellFee").value;
        q("buyMin").value = configPayload.buy_fee_minimum ?? q("buyMin").value;
        q("sellMin").value = configPayload.sell_fee_minimum ?? q("sellMin").value;
        if (q("slippage")) q("slippage").value = configPayload.slippage ?? q("slippage").value;
        if (configPayload.stamp_tax_rate != null && Number(configPayload.stamp_tax_rate) > 0) {
          q("stampTax").value = configPayload.stamp_tax_rate;
        } else {
          q("stampTax").value = 0.001;
        }
        const buyPrice = configPayload.buy_price === "hfq_open" ? "open" : (configPayload.buy_price || "open");
        const sellPrice = configPayload.sell_price === "hfq_close" ? "close" : (configPayload.sell_price || "close");
        if ([...q("buy").options].some((option) => option.value === buyPrice)) q("buy").value = buyPrice;
        if ([...q("sell").options].some((option) => option.value === sellPrice)) q("sell").value = sellPrice;
        const openFilters = configPayload.trade_filters && Array.isArray(configPayload.trade_filters.open)
          ? configPayload.trade_filters.open
          : (configPayload.skip_open_limit === false ? [] : [DEFAULT_OPEN_LIMIT_EXPR]);
        setOpenFilterExpressions(openFilters);
        loadDatasetFields();
        q("closeLimit").value = configPayload.skip_close_down_limit === false ? "fill" : "skip";
        if (q("hs300-gt-ma200")) {
          q("hs300-gt-ma200").checked = Boolean(configPayload.open_when_benchmark_gt_ma200);
        }
        applyModelParams({
          ...(configPayload.hyperparameters || {}),
          walk_forward: configPayload.walk_forward || (configPayload.hyperparameters || {}).walk_forward || "once",
          train_lookback_months: configPayload.train_lookback_months || (configPayload.hyperparameters || {}).train_lookback_months,
          train_period_months: configPayload.train_period_months || (configPayload.hyperparameters || {}).train_period_months,
          test_period_months: configPayload.test_period_months || (configPayload.hyperparameters || {}).test_period_months,
        }, configPayload.kind);
        if (configPayload.train?.date_from) {
          const trainTo = configPayload.validation?.date_to || configPayload.train.date_to || configPayload.train.fit_date_to;
          if (trainTo) q("train-from").value = `${configPayload.train.date_from} — ${trainTo}`;
        }
        if (configPayload.test?.date_from && configPayload.test?.date_to) {
          q("test-from").value = `${configPayload.test.date_from} — ${configPayload.test.date_to}`;
        }
        const pretrade = configPayload.pretrade_filters && typeof configPayload.pretrade_filters === "object"
          ? configPayload.pretrade_filters
          : null;
        const sharedStock = pretrade && Array.isArray(pretrade.stock) ? pretrade.stock : DEFAULT_STOCK_EXPRS;
        setExpressions("pretrade-stock-list", pretrade ? pretrade.stock : DEFAULT_STOCK_EXPRS, DEFAULT_STOCK_EXPRS);
        setExpressions("pretrade-benchmark-list", pretrade ? pretrade.benchmark : [], []);
        setExpressions("train-filter-list", withoutSharedExpressions([
          ...expressionsFromFilter(configPayload.train?.filter),
          ...expressionsFromFilter(configPayload.validation?.filter),
        ].filter((text, index, list) => text && list.indexOf(text) === index), sharedStock), []);
        setExpressions("test-filter-list", withoutSharedExpressions(expressionsFromFilter(configPayload.test?.filter), sharedStock), []);
        refreshTestUsage();
        if (configPayload.model?.entity_id && configPayload.model?.version_id) {
          const modelTarget = `${configPayload.model.entity_id}::${configPayload.model.version_id}`;
          if ([...q("model").options].some((option) => option.value === modelTarget)) q("model").value = modelTarget;
        }
        syncKindFields(configPayload.kind || selectedKind());
        syncActionAvailability();
      }

      async function loadOpenPlans() {
        const select = q("identity-plan");
        if (!select) return;
        const payload = await request("/api/backtest-plans?open=1");
        const items = payload.items || [];
        let wanted = "";
        try { wanted = localStorage.getItem(PLAN_STORAGE_KEY) || ""; } catch (_error) {}
        select.replaceChildren();
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = items.length ? "不选择" : "没有未完结计划（选填）";
        select.append(blank);
        items.forEach((plan) => {
          const option = document.createElement("option");
          option.value = plan.plan_id;
          option.textContent = `${plan.name}（${plan.plan_id}）`;
          select.append(option);
        });
        select.value = items.some((plan) => plan.plan_id === wanted) ? wanted : "";
      }

      async function saveCurrent() {
        const payload = await request("/api/backtests/drafts", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({config: configValue(), draft_id: openDraft?.draft_id || null, expected_revision: openDraft?.revision || null})});
        openDraft = {draft_id: payload.draft_id, revision: payload.revision};
        rememberDraft(openDraft.draft_id);
        q("run-id").textContent = `草稿 ID：${openDraft.draft_id}`;
        q("output-wrap")?.classList.add("hidden");
        showStatus("配置已保存为草稿，尚未生成运行 ID");
        showBanner("配置草稿已保存，未创建 BacktestRun；开始回测才会生成正式运行 ID。");
      }

      async function boot() {
        try {
          const [factorsResponse, strategiesResponse, modelsResponse] = await Promise.all([
            request("/api/factor-data/catalog"),
            request("/api/strategies"),
            request("/api/models"),
          ]);
          publishedFactors = factorsResponse;
          publishedStrategies = strategiesResponse.items || [];
          modelVersions = modelsResponse.items || [];
          renderFactorOptions();
          renderModelOptions();
          const draftId = draftIdFromLocation() || storedDraftId();
          if (draftId) {
            try {
              const draft = await request(`/api/backtests/drafts/${encodeURIComponent(draftId)}`);
              openDraft = {draft_id: draft.draft_id, revision: draft.revision};
              rememberDraft(draft.draft_id);
              q("run-id").textContent = `草稿 ID：${draft.draft_id}`;
              if (draft.complete) applyConfig(draft.config);
              else applyBlankFilters();
              showStatus(`已载入草稿 ${draft.draft_id}（rev ${draft.revision}）`);
              showBanner("已从草稿载入配置；保存会继续更新同一草稿，开始回测才提交运行。");
            } catch (error) {
              try { localStorage.removeItem(DRAFT_STORAGE_KEY); } catch (_error) {}
              applyBlankFilters();
              if (!selectedFactors.length) {
                const preferred = factorCatalog.find((item) => item.field === "momentum_5") || factorCatalog[0];
                if (preferred) addFactor(preferred);
              }
              if (draftIdFromLocation()) showError(error);
            }
          } else {
            applyBlankFilters();
            if (!selectedFactors.length) {
              const preferred = factorCatalog.find((item) => item.field === "momentum_5") || factorCatalog[0];
              if (preferred) addFactor(preferred);
            }
          }
          await loadDatasetFields();
          refreshTestUsage();
          await loadOpenPlans();
          q("identity-plan")?.addEventListener("change", () => {
            try {
              const planId = q("identity-plan").value;
              if (planId) localStorage.setItem(PLAN_STORAGE_KEY, planId);
              else localStorage.removeItem(PLAN_STORAGE_KEY);
            } catch (_error) {}
          });
        } catch (error) {
          showError(error);
        }
      }

      q("preview").onclick = async () => {
        try {
          const payload = await request("/api/backtests/preview", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(configValue())});
          showOutput(JSON.stringify(payload, null, 2));
          showStatus("过滤预览已完成");
          q("status").classList.remove("status-error");
          q("banner").classList.remove("banner-error");
        } catch (error) { showError(error); }
      };
      q("save").onclick = async () => { try { await saveCurrent(); } catch (error) { showError(error); } };
      q("add-to-plan").onclick = async () => {
        try {
          const model = readSelection(q("model"));
          if (!model) {
            showBanner("请先选择一种模型。", true);
            return;
          }
          if (!selectedFactors.length) {
            showBanner("请至少选择一个因子。", true);
            return;
          }
          const planId = q("identity-plan")?.value || "";
          if (!planId) {
            showBanner("请先在运行身份里选择一份未完结的回测计划，或到回测计划页新建。加入计划才需要选计划，开始回测不需要。", true);
            return;
          }
          const updated = await request(`/api/backtest-plans/${encodeURIComponent(planId)}/items`, {
            method: "POST",
            headers: {"content-type": "application/json"},
            body: JSON.stringify({name: q("name").value, config: configValue()}),
          });
          try { localStorage.setItem(PLAN_STORAGE_KEY, planId); } catch (_error) {}
          showStatus("已加入回测计划，尚未运行");
          showBanner(`已写入「${updated.name}」${updated.plan_id}。打开回测计划后全选开始即可自动逐笔跑。`);
        } catch (error) { showError(error); }
      };
      q("output-collapse")?.addEventListener("click", () => {
        const shrunk = q("output").classList.toggle("collapsed");
        q("output-collapse").textContent = shrunk ? "展开" : "缩小";
      });
      q("output-hide")?.addEventListener("click", () => q("output-wrap").classList.add("hidden"));
      q("start").onclick = async () => {
        try {
          const model = readSelection(q("model"));
          if (!model) {
            showBanner("请先选择一种模型。", true);
            return;
          }
          if (!boundStrategyForModel(model)) {
            showBanner("这个模型还没有默认交易规则。请到模型中心再保存一次默认参数。", true);
            return;
          }
          if (!selectedFactors.length) {
            showBanner("请至少选择一个因子。", true);
            q("factor-search")?.focus();
            return;
          }
          submissionToken = crypto.randomUUID();
          q("start").disabled = true;
          setStopEnabled(false);
          const payload = await request("/api/backtests", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(configValue())});
          const runId = payload.run_id;
          q("run-id").textContent = runId;
          resetRunStatus(runId);
          document.querySelector('[data-panel-id="run-status"]')?.scrollIntoView({behavior: "smooth", block: "nearest"});
          showStatus(`已提交运行：${q("name").value}`);
          showBanner("运行已提交。进度写在「运行状态」模块；完成后可打开运行记录，不会自动跳走。");
          startRunPoll(runId);
          try {
            const executed = await request(`/api/backtests/${encodeURIComponent(runId)}/execute`, {method: "POST"});
            if (executed.status === "failed" && String(executed.error_message || "").includes("强行停止")) {
              showBanner("已强行停止运算，页面服务未停。");
            }
          } finally {
            await pollRunStatus(runId);
            stopRunPoll();
          }
        } catch (error) {
          stopRunPoll();
          if (activeRunId) await pollRunStatus(activeRunId);
          showError(error);
          appendRunLog(error.message || "运行失败");
        } finally {
          q("start").disabled = false;
        }
      };
      q("stop-backtest").onclick = async () => {
        const runId = activeRunId;
        if (!runId) {
          showBanner("还没有正在运行的回测。", true);
          return;
        }
        q("stop-backtest").disabled = true;
        try {
          const payload = await request(`/api/backtests/${encodeURIComponent(runId)}/stop`, {method: "POST"});
          applyRunStatus(payload);
          showBanner("已强行停止运算，页面服务未停。");
          stopRunPoll();
        } catch (error) {
          showError(error);
          q("stop-backtest").disabled = false;
        }
      };
      q("edit-root").onclick = () => showBanner("结果目录由系统设置管理；此处显示当前只读目录。");
      q("add-open-filter")?.addEventListener("click", () => addFilterRow("open-filter-list", ""));
      q("add-pretrade-stock")?.addEventListener("click", () => addFilterRow("pretrade-stock-list", ""));
      q("add-pretrade-benchmark")?.addEventListener("click", () => addFilterRow("pretrade-benchmark-list", ""));
      q("add-train-filter")?.addEventListener("click", () => addFilterRow("train-filter-list", ""));
      q("add-test-filter")?.addEventListener("click", () => addFilterRow("test-filter-list", ""));
      q("dataset")?.addEventListener("change", () => loadDatasetFields());
      q("test-from")?.addEventListener("change", () => refreshTestUsage());
      q("bt-walk-forward")?.addEventListener("change", () => syncRollPeriodFields());
      q("bt-train-period")?.addEventListener("input", () => clampTestPeriodToLookback());
      q("bt-test-period")?.addEventListener("change", () => clampTestPeriodToLookback());

      const PANEL_ORDER_KEY = "quantlab-backtest-panel-order";

      function layoutColumns() {
        return [...document.querySelectorAll(".layout-col")];
      }

      function panelOrder() {
        return layoutColumns().map((col) => [...col.querySelectorAll(":scope > .panel")].map((panel) => panel.dataset.panelId).filter(Boolean));
      }

      function savePanelOrder() {
        try { localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(panelOrder())); } catch (_error) {}
        refreshPanelTags();
      }

      function refreshPanelTags() {
        let index = 1;
        document.querySelectorAll(".layout .panel").forEach((panel) => {
          const tag = panel.querySelector(".head .tag");
          if (!tag || panel.dataset.panelId === "identity") return;
          tag.textContent = `模块 ${index++}`;
        });
      }

      function applyPanelOrder() {
        let saved;
        try { saved = JSON.parse(localStorage.getItem(PANEL_ORDER_KEY) || "null"); } catch (_error) { saved = null; }
        if (!Array.isArray(saved) || saved.length !== 2) return;
        const columns = layoutColumns();
        const panels = new Map([...document.querySelectorAll(".layout .panel")].map((panel) => [panel.dataset.panelId, panel]));
        const used = new Set();
        saved.forEach((ids, colIndex) => {
          const col = columns[colIndex];
          if (!col || !Array.isArray(ids)) return;
          ids.forEach((id) => {
            const panel = panels.get(id);
            if (!panel || used.has(id)) return;
            col.append(panel);
            used.add(id);
          });
        });
        [...panels.values()].filter((panel) => !used.has(panel.dataset.panelId)).forEach((panel) => panel.parentElement?.append(panel));
        refreshPanelTags();
      }

      function bindWorkbenchPanelDrag() {
        const columns = layoutColumns();
        const place = (event, col) => {
          event.preventDefault();
          const dragging = document.querySelector(".layout .panel.dragging");
          if (!dragging) return;
          const others = [...col.querySelectorAll(":scope > .panel:not(.dragging)")];
          const after = others.find((panel) => {
            const rect = panel.getBoundingClientRect();
            return event.clientY < rect.top + rect.height / 2;
          });
          col.insertBefore(dragging, after || null);
        };
        columns.forEach((col) => {
          col.addEventListener("dragover", (event) => place(event, col));
          col.addEventListener("drop", (event) => {
            event.preventDefault();
            savePanelOrder();
          });
        });
        document.querySelectorAll(".layout .panel-drag-handle").forEach((handle) => {
          handle.addEventListener("dragstart", (event) => {
            const panel = handle.closest(".panel");
            if (!panel) return;
            panel.classList.add("dragging");
            event.dataTransfer.setData("text/plain", panel.dataset.panelId || "");
            event.dataTransfer.effectAllowed = "move";
            try { event.dataTransfer.setDragImage(panel, 24, 16); } catch (_error) {}
          });
          handle.addEventListener("dragend", () => {
            document.querySelectorAll(".layout .panel.dragging").forEach((panel) => panel.classList.remove("dragging"));
            savePanelOrder();
          });
        });
        applyPanelOrder();
      }

      bindWorkbenchPanelDrag();
      window.configValue = configValue;
      window.selectedKind = selectedKind;
      window.applyRunStatus = applyRunStatus;
      window.pollRunStatus = pollRunStatus;
      boot();
    
