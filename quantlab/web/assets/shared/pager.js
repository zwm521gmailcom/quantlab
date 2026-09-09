function tablePager() {
  return window.QuantLabPager;
}

function tablePageSize(key, fallback, sizes) {
  const pager = tablePager();
  if (!pager) return fallback;
  return pager.readSize(key, sizes || pager.DEFAULT_SIZES, fallback);
}

function bindTablePager(host, options) {
  const pager = tablePager();
  if (!pager || !host) return;
  pager.mount(host, options);
}
