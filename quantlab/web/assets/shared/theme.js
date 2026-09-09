(function (w) {
  w.qlChartColors = function qlChartColors() {
    if (window.qlTheme && typeof window.qlTheme.chartColors === "function") {
      return window.qlTheme.chartColors();
    }
    return {bg: "#f3f4f0", text: "#1e2a24", grid: "#d2d7cf", border: "#c3c9c0"};
  };

  w.qlCss = function qlCss(prop, fallback) {
    if (window.qlTheme && typeof window.qlTheme.css === "function") {
      const value = window.qlTheme.css(prop);
      if (value) return value;
    }
    return fallback;
  };
})(window);
