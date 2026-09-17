(function (w) {
  w.qlChartColors = function qlChartColors() {
    if (window.qlTheme && typeof window.qlTheme.chartColors === "function") {
      return window.qlTheme.chartColors();
    }
    return {bg: "#1c241e", text: "#e6ebe4", grid: "#2a332d", border: "#334038"};
  };

  w.qlCss = function qlCss(prop, fallback) {
    if (window.qlTheme && typeof window.qlTheme.css === "function") {
      const value = window.qlTheme.css(prop);
      if (value) return value;
    }
    return fallback;
  };
})(window);
