(() => {
  const errorBox = document.getElementById("qlib-picks-error");
  const summary = document.getElementById("qlib-picks-summary");
  const empty = document.getElementById("qlib-picks-empty");
  const body = document.getElementById("qlib-picks-body");
  const board = document.getElementById("qlib-picks-board");

  const KINDS = [
    { id: "stability", label: "振幅×量稳", color: "#e39158", hint: "日内振幅，再乘上成交量相对自身波动有多稳" },
    { id: "range", label: "纯振幅", color: "#6fbf91", hint: "只用高低价振幅，不看成交量" },
    { id: "weighted", label: "量加权振幅", color: "#8eb4e8", hint: "振幅按成交量加权，放量的那天权重大" },
    { id: "level", label: "振幅/量", color: "#e0a04a", hint: "振幅除以成交量水平，量越大因子越小" },
    { id: "ratio", label: "振幅×量比", color: "#c58bd0", hint: "振幅乘上两段成交量的比值" },
    { id: "other", label: "其他", color: "#a3ada6", hint: "上面几类都套不上" },
  ];

  function sourceLabel(model) {
    const name = String(model || "");
    if (name.startsWith("grok")) return "Grok";
    if (name.startsWith("gpt")) return "GPT";
    return name || "—";
  }

  function pct(value) {
    return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
  }

  function icText(value) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
  }

  function cell(text, className) {
    const item = document.createElement("td");
    if (className) item.className = className;
    item.textContent = text;
    if (text && text !== "—") item.title = text;
    return item;
  }

  function compact(formula) {
    return String(formula || "").replace(/\s+/g, "");
  }

  function kindOf(formula) {
    const text = compact(formula);
    const amplitude = text.includes("high") && text.includes("low");
    const volume = text.includes("volume");
    const stability = text.includes("Mean(volume") && text.includes("Std(volume");
    const weighted = text.includes("*volume") || text.includes(")volume");
    const ratio = /Mean\(volume,\d+\)\/Mean\(volume/.test(text);
    const scaled = /\/(Mean|Std)\(volume/.test(text);
    if (amplitude && stability) return KINDS[0];
    if (amplitude && !volume) return KINDS[1];
    if (amplitude && weighted) return KINDS[2];
    if (amplitude && ratio) return KINDS[4];
    if (amplitude && scaled) return KINDS[3];
    if (amplitude) return KINDS[2];
    return KINDS[5];
  }

  function styleOf(pick) {
    const ic = Number(pick.valid_ic);
    if (Number.isFinite(ic) && ic > 0) {
      return { label: "做高", hollow: true, text: "验证 IC 为正，模型买因子值更大的股票。" };
    }
    return { label: "做低", hollow: false, text: "验证 IC 为负，模型买因子值更小的股票。因子值大表示振幅大，所以这是在买更窄的振幅。" };
  }

  function excessOf(pick) {
    const annual = Number(pick.test_annual_return);
    const benchmark = Number(pick.test_benchmark_annual_return);
    if (!Number.isFinite(annual) || !Number.isFinite(benchmark)) return null;
    return annual - benchmark;
  }

  function why(pick) {
    const size = Number(pick.family_size) || 1;
    const lead = size > 1 ? `同一类窗口有 ${size} 条过线，这条信息比率最高。` : "这一类窗口只有它过线。";
    return `${lead}测试年化高于该轮基准，信息比率为正。`;
  }

  function svgEl(name) {
    return document.createElementNS("http://www.w3.org/2000/svg", name);
  }

  function renderPlot(picks) {
    const width = 680;
    const height = 380;
    const pad = { left: 52, right: 18, top: 16, bottom: 42 };
    const points = picks.map((pick) => ({
      pick,
      kind: kindOf(pick.formula),
      style: styleOf(pick),
      excess: excessOf(pick),
      ir: Number(pick.test_information_ratio),
    })).filter((point) => point.excess !== null && Number.isFinite(point.ir));
    const maxExcess = Math.max(0.02, ...points.map((point) => point.excess));
    const maxIr = Math.max(0.2, ...points.map((point) => point.ir));
    const xMax = maxExcess * 1.08;
    const yMax = maxIr * 1.12;
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const xOf = (value) => pad.left + (value / xMax) * plotW;
    const yOf = (value) => pad.top + plotH - (value / yMax) * plotH;

    const svg = svgEl("svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("class", "qlib-picks-plot");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "筛选因子散点图。横轴是超过基准的测试年化，纵轴是信息比率，颜色是公式类型。");

    [0.25, 0.5, 0.75, 1].forEach((share) => {
      const y = yOf(yMax * share);
      const line = svgEl("line");
      line.setAttribute("class", "grid");
      line.setAttribute("x1", pad.left);
      line.setAttribute("x2", width - pad.right);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      svg.append(line);
      const label = svgEl("text");
      label.setAttribute("x", pad.left - 8);
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.textContent = (yMax * share).toFixed(1);
      svg.append(label);
    });
    const xTicks = 4;
    for (let index = 0; index <= xTicks; index += 1) {
      const value = (xMax * index) / xTicks;
      const x = xOf(value);
      const label = svgEl("text");
      label.setAttribute("x", x);
      label.setAttribute("y", height - 16);
      label.setAttribute("text-anchor", "middle");
      label.textContent = `${(value * 100).toFixed(0)}%`;
      svg.append(label);
    }
    const axisX = svgEl("line");
    axisX.setAttribute("class", "axis");
    axisX.setAttribute("x1", pad.left);
    axisX.setAttribute("x2", width - pad.right);
    axisX.setAttribute("y1", pad.top + plotH);
    axisX.setAttribute("y2", pad.top + plotH);
    const axisY = svgEl("line");
    axisY.setAttribute("class", "axis");
    axisY.setAttribute("x1", pad.left);
    axisY.setAttribute("x2", pad.left);
    axisY.setAttribute("y1", pad.top);
    axisY.setAttribute("y2", pad.top + plotH);
    svg.append(axisX, axisY);
    const xTitle = svgEl("text");
    xTitle.setAttribute("x", pad.left + plotW / 2);
    xTitle.setAttribute("y", height - 2);
    xTitle.setAttribute("text-anchor", "middle");
    xTitle.textContent = "超过基准的测试年化";
    const yTitle = svgEl("text");
    yTitle.setAttribute("x", 14);
    yTitle.setAttribute("y", pad.top + plotH / 2);
    yTitle.setAttribute("text-anchor", "middle");
    yTitle.setAttribute("transform", `rotate(-90 14 ${pad.top + plotH / 2})`);
    yTitle.textContent = "信息比率";
    svg.append(xTitle, yTitle);

    const best = points.reduce((top, point) => (point.ir > top.ir ? point : top), points[0]);
    points.forEach((point) => {
      const circle = svgEl("circle");
      const strong = point.ir >= 1;
      circle.setAttribute("cx", xOf(point.excess));
      circle.setAttribute("cy", yOf(point.ir));
      circle.setAttribute("r", strong ? 6 : 4.5);
      circle.setAttribute("fill", point.style.hollow ? "transparent" : point.kind.color);
      circle.setAttribute("stroke", point.kind.color);
      circle.setAttribute("stroke-width", point.style.hollow ? 2 : 1);
      circle.style.cursor = "pointer";
      const title = svgEl("title");
      title.textContent = `${point.pick.name} · ${point.kind.label} · ${point.style.label}`;
      circle.append(title);
      circle.addEventListener("pointerenter", () => showDetail(point));
      svg.append(circle);
    });
    if (best) {
      const tag = svgEl("text");
      tag.setAttribute("x", Math.min(xOf(best.excess) + 8, width - pad.right - 120));
      tag.setAttribute("y", Math.max(yOf(best.ir) - 8, pad.top + 12));
      tag.setAttribute("fill", "var(--color-text)");
      tag.textContent = best.pick.name || "";
      svg.append(tag);
    }
    return { svg, best };
  }

  function showDetail(point) {
    const hover = document.getElementById("qlib-picks-hover");
    if (!hover || !point) return;
    const annual = pct(point.pick.test_annual_return);
    const drawdown = pct(point.pick.test_max_drawdown);
    hover.textContent = `${point.pick.name} · ${point.kind.label} · ${point.style.label}。信息比率 ${icText(point.ir)}，测试年化 ${annual}，超过基准 ${pct(point.excess)}，回撤 ${drawdown}。${point.style.text}${why(point.pick)}`;
  }

  function renderSide(picks) {
    const side = document.createElement("div");
    side.className = "qlib-picks-side";
    const typeTitle = document.createElement("h3");
    typeTitle.textContent = "类型";
    side.append(typeTitle);
    KINDS.forEach((kind) => {
      const rows = picks.filter((pick) => kindOf(pick.formula).id === kind.id);
      if (!rows.length) return;
      const best = rows.reduce((top, pick) => (
        Number(pick.test_information_ratio) > Number(top.test_information_ratio) ? pick : top
      ));
      const row = document.createElement("div");
      row.className = "qlib-kind";
      const swatch = document.createElement("i");
      swatch.className = "qlib-swatch";
      swatch.style.background = kind.color;
      const name = document.createElement("b");
      name.textContent = `${kind.label} · ${rows.length} 类`;
      name.title = kind.hint;
      const stat = document.createElement("span");
      stat.textContent = `最高 ${Number(best.test_information_ratio).toFixed(2)}`;
      row.append(swatch, name, stat);
      side.append(row);
      const hint = document.createElement("p");
      hint.className = "qlib-style";
      hint.textContent = kind.hint;
      side.append(hint);
    });

    const styleTitle = document.createElement("h3");
    styleTitle.textContent = "风格";
    side.append(styleTitle);
    const low = picks.filter((pick) => styleOf(pick).label === "做低").length;
    const high = picks.length - low;
    const lowNote = document.createElement("p");
    lowNote.className = "qlib-style";
    lowNote.textContent = `做低 ${low} 类，图上是实心点。验证 IC 为负，模型买因子值更小的股票。这些公式的因子值大表示振幅大，优势是偏向更窄的日内振幅。`;
    side.append(lowNote);
    if (high) {
      const highNote = document.createElement("p");
      highNote.className = "qlib-style";
      highNote.textContent = `做高 ${high} 类，图上是空心点。验证 IC 为正，模型买因子值更大的股票。`;
      side.append(highNote);
    }
    const read = document.createElement("h3");
    read.textContent = "为什么入选";
    const readNote = document.createElement("p");
    readNote.className = "qlib-style";
    readNote.textContent = "点越高，信息比率越稳；点越靠右，测试年化超过基准越多。左下角只是刚过线。每一类窗口只留信息比率最高的一条，所以图上每个点都是该类的代表。";
    side.append(read, readNote);
    return side;
  }

  function renderBoard(picks) {
    board.replaceChildren();
    if (!picks.length) {
      board.classList.add("hidden");
      return;
    }
    board.classList.remove("hidden");
    const plot = renderPlot(picks);
    const wrap = document.createElement("div");
    wrap.append(plot.svg);
    const hover = document.createElement("p");
    hover.id = "qlib-picks-hover";
    hover.className = "qlib-picks-hover";
    hover.textContent = "把鼠标放到点上，看这个因子的类型、风格和相对基准的优势。";
    wrap.append(hover);
    board.append(wrap, renderSide(picks));
    if (plot.best) showDetail(plot.best);
  }

  function render(payload) {
    const picks = payload.picks || [];
    summary.textContent = `过线 ${payload.candidate_count || 0} 条，留下 ${picks.length} 类。`;
    empty.classList.toggle("hidden", picks.length > 0);
    renderBoard(picks);
    body.replaceChildren();
    picks.forEach((pick) => {
      const tr = document.createElement("tr");
      const source = cell(sourceLabel(pick.model), "source");
      if (pick.model) source.title = String(pick.model);
      const actions = document.createElement("td");
      actions.className = "qlib-actions";
      if (pick.detail_url) {
        const link = document.createElement("a");
        link.className = "button-link";
        link.href = pick.detail_url;
        link.textContent = "结果档案";
        actions.append(link);
      }
      const family = Number(pick.family_size) > 1 ? `1/${pick.family_size}` : "1";
      const kind = kindOf(pick.formula);
      const nameCell = cell(pick.name || "—");
      nameCell.title = `${kind.label} · ${styleOf(pick).label}。${kind.hint}`;
      tr.append(
        source,
        cell(pick.plan_id || "—", "plan-id"),
        cell(`${pick.round}/${pick.max_loops || "—"}`, "num"),
        nameCell,
        cell(pick.formula || "—"),
        cell(icText(pick.valid_ic), "num"),
        cell(icText(pick.test_ic), "num"),
        cell(pct(pick.test_annual_return), "num"),
        cell(pct(pick.test_benchmark_annual_return), "num"),
        cell(pct(pick.test_max_drawdown), "num"),
        cell(icText(pick.test_information_ratio), "num"),
        cell(family, "num"),
        actions,
      );
      body.append(tr);
    });
  }

  async function load() {
    try {
      render(await window.apiFetch("/api/qlib/picks"));
      errorBox.classList.add("hidden");
    } catch (error) {
      errorBox.textContent = error.message || "筛选结果读取失败";
      errorBox.classList.remove("hidden");
    }
  }

  load();
  window.setInterval(load, 5000);
})();
