(function (global) {
  const DEFAULT_SIZES = [10, 20, 50, 100];

  function readSize(key, sizes, fallback) {
    const allowed = sizes && sizes.length ? sizes : DEFAULT_SIZES;
    const defaultValue = fallback == null ? allowed[0] : fallback;
    try {
      const stored = Number(localStorage.getItem(key));
      if (allowed.includes(stored)) return stored;
    } catch (_error) {}
    return defaultValue;
  }

  function writeSize(key, value) {
    try { localStorage.setItem(key, String(value)); } catch (_error) {}
  }

  function pagesFor(total, pageSize) {
    return Math.max(1, Math.ceil(Math.max(0, Number(total) || 0) / Math.max(1, Number(pageSize) || 1)));
  }

  function clampPage(page, pages) {
    return Math.min(Math.max(1, Number(page) || 1), Math.max(1, Number(pages) || 1));
  }

  function slice(items, page, pageSize) {
    const start = (Math.max(1, page) - 1) * Math.max(1, pageSize);
    return (items || []).slice(start, start + pageSize);
  }

  function mount(host, options) {
    if (!host) return;
    const page = options.page || 1;
    const pages = Math.max(1, options.pages || 1);
    const pageSize = options.pageSize;
    const sizes = options.sizes && options.sizes.length ? options.sizes : DEFAULT_SIZES;
    const total = options.total;
    host.classList.add("archive-pagination");
    host.hidden = false;
    host.replaceChildren();

    const prev = document.createElement("button");
    prev.type = "button";
    prev.textContent = "上一页";
    prev.disabled = page <= 1;
    prev.addEventListener("click", () => {
      if (typeof options.onPage === "function") options.onPage(page - 1);
    });

    const label = document.createElement("span");
    label.textContent = total == null
      ? `第 ${page} / ${pages} 页`
      : `第 ${page} / ${pages} 页 · 共 ${total} 条`;

    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "下一页";
    next.disabled = page >= pages;
    next.addEventListener("click", () => {
      if (typeof options.onPage === "function") options.onPage(page + 1);
    });

    const sizeLabel = document.createElement("label");
    sizeLabel.className = "table-page-size";
    sizeLabel.append("每页 ");
    const select = document.createElement("select");
    select.setAttribute("aria-label", "每页显示行数");
    sizes.forEach((size) => {
      const option = document.createElement("option");
      option.value = String(size);
      option.textContent = String(size);
      select.append(option);
    });
    select.value = String(pageSize);
    select.addEventListener("change", () => {
      const value = Number(select.value);
      if (options.storageKey) writeSize(options.storageKey, value);
      if (typeof options.onPageSize === "function") options.onPageSize(value);
    });
    sizeLabel.append(select);
    host.append(prev, label, next, sizeLabel);
  }

  global.QuantLabPager = {DEFAULT_SIZES, readSize, writeSize, pagesFor, clampPage, slice, mount};
})(window);
