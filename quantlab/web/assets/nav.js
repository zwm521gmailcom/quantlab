(function () {
  const _savedTheme = localStorage.getItem("theme");
  if (_savedTheme === "dark") document.documentElement.classList.add("dark");
  else if (_savedTheme === "light") document.documentElement.classList.add("light");
  const NAV_HIDE_KEY = "nav-autohide";
  if (localStorage.getItem(NAV_HIDE_KEY) === "1") document.documentElement.classList.add("nav-autohide");

  window.qlTheme = {
    isDark() { return document.documentElement.classList.contains("dark"); },
    css(prop) { return getComputedStyle(document.documentElement).getPropertyValue(prop).trim(); },
    chartColors() {
      return {
        bg: this.css("--color-bg-card") || "#f3f4f0",
        text: this.css("--color-text") || "#1e2a24",
        grid: this.css("--color-border-light") || "#d2d7cf",
        border: this.css("--color-border") || "#c3c9c0",
      };
    },
  };

  const exact = (route) => (pathname) => pathname === route;
  const under = (route) => (pathname) => pathname === route || pathname.startsWith(`${route}/`);
  const groups = [
    {
      label: "工作区",
      items: [
        { href: "/", label: "研究总览", icon: "⌂", active: exact("/") },
        {
          href: "/data",
          label: "数据中心",
          icon: "▦",
          active: exact("/data"),
          children: [
            { href: "/kline", label: "标准行情宽表", active: exact("/kline") },
          ],
        },
        {
          href: "/factors",
          label: "因子研究",
          icon: "ƒ",
          active: (pathname) => pathname === "/factors" || (pathname.startsWith("/factors/") && !pathname.startsWith("/factors/new")),
          children: [
            { href: "/factors/new/manual", label: "手动建立因子", active: (pathname) => pathname === "/factors/new" || pathname === "/factors/new/manual" },
            { href: "/research/factor-mining", label: "自动挖掘因子", active: under("/research/factor-mining") },
            { href: "/research/factor-jobs", label: "因子计算任务", active: under("/research/factor-jobs") },
          ],
        },
        { href: "/models", label: "模型中心", icon: "◇", active: under("/models") },
        {
          href: "/backtests/new",
          label: "回测中心",
          icon: "▶",
          active: exact("/backtests/new"),
          children: [
            { href: "/backtests/plan", label: "回测计划", active: exact("/backtests/plan") },
            { href: "/backtests/rules", label: "规则回测", active: exact("/backtests/rules") },
          ],
        },
        { href: "/backtests/runs", label: "结果档案", icon: "↗", active: under("/backtests/runs") },
      ],
    },
    {
      label: "系统",
      items: [{ href: "/settings", label: "设置", icon: "⚙", active: under("/settings") }],
    },
  ];

  function link(item, current, child) {
    const anchor = document.createElement("a");
    anchor.href = item.href;
    anchor.className = child ? "nav-subitem" : "nav-item";
    if (child) {
      anchor.textContent = item.label;
    } else {
      const icon = document.createElement("span");
      icon.className = "nav-icon";
      icon.textContent = item.icon;
      const label = document.createElement("span");
      label.className = "nav-label";
      label.textContent = item.label;
      anchor.append(icon, " ", label);
    }
    if (current) anchor.setAttribute("aria-current", "page");
    return anchor;
  }

  function render() {
    const aside = document.querySelector(".shell > aside");
    if (!aside) return;
    const pathname = window.location.pathname;
    aside.replaceChildren();

    const brandRow = document.createElement("div");
    brandRow.className = "nav-brand-row";
    const brand = document.createElement("div");
    brand.className = "nav-brand";
    const mark = document.createElement("span");
    mark.className = "nav-brand-mark";
    mark.textContent = "Q";
    const name = document.createElement("span");
    name.className = "nav-brand-text";
    name.textContent = "QuantLab";
    brand.append(mark, name);
    const subtitle = document.createElement("small");
    subtitle.textContent = "本地量化研究工作台";
    brand.append(subtitle);
    const hideToggle = document.createElement("button");
    hideToggle.className = "nav-autohide-toggle";
    hideToggle.type = "button";
    const syncHide = () => {
      const on = document.documentElement.classList.contains("nav-autohide");
      hideToggle.setAttribute("aria-pressed", on ? "true" : "false");
      hideToggle.title = on ? "固定导航" : "自动隐藏导航";
      hideToggle.setAttribute("aria-label", on ? "固定导航" : "自动隐藏导航");
      hideToggle.textContent = on ? "»" : "«";
    };
    let holdCollapse = false;
    const autohideOn = () => document.documentElement.classList.contains("nav-autohide");
    const setExpanded = (on) => aside.classList.toggle("nav-expanded", Boolean(on));
    hideToggle.addEventListener("mousedown", (event) => event.preventDefault());
    hideToggle.addEventListener("click", () => {
      const on = !autohideOn();
      document.documentElement.classList.toggle("nav-autohide", on);
      localStorage.setItem(NAV_HIDE_KEY, on ? "1" : "0");
      holdCollapse = on;
      setExpanded(false);
      syncHide();
    });
    syncHide();
    brandRow.append(brand, hideToggle);
    aside.append(brandRow);

    groups.forEach((group) => {
      const title = document.createElement("div");
      title.className = "nav-title";
      title.textContent = group.label;
      aside.append(title);
      const nav = document.createElement("nav");
      nav.setAttribute("aria-label", `${group.label}导航`);
      group.items.forEach((item) => {
        const groupElement = document.createElement("div");
        groupElement.className = item.children ? "nav-group" : "nav-group nav-group-leaf";
        const parentActive = Boolean(item.active?.(pathname));
        const childActive = Boolean(item.children?.some((child) => child.active?.(pathname)));
        const parent = link(item, parentActive, false);
        if (!parentActive && childActive) parent.classList.add("nav-parent-active");
        groupElement.append(parent);
        if (item.children) {
          const children = document.createElement("div");
          children.className = "nav-children";
          item.children.forEach((child) => {
            const childCurrent = Boolean(child.active?.(pathname));
            children.append(link(child, childCurrent, true));
          });
          groupElement.append(children);
        }
        nav.append(groupElement);
      });
      aside.append(nav);
    });

    const themeToggle = document.createElement("button");
    themeToggle.className = "theme-toggle";
    themeToggle.type = "button";
    const themeIcon = document.createElement("span");
    themeIcon.className = "nav-icon";
    const themeLabel = document.createElement("span");
    themeLabel.className = "nav-label";
    const syncThemeButton = () => {
      const dark = document.documentElement.classList.contains("dark");
      themeIcon.textContent = dark ? "☀" : "☾";
      themeLabel.textContent = dark ? "浅色模式" : "深色模式";
    };
    syncThemeButton();
    themeToggle.append(themeIcon, " ", themeLabel);
    themeToggle.addEventListener("click", () => {
      const goingDark = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", goingDark);
      document.documentElement.classList.toggle("light", !goingDark);
      localStorage.setItem("theme", goingDark ? "dark" : "light");
      syncThemeButton();
      window.dispatchEvent(new CustomEvent("ql-theme-change", {detail: {dark: goingDark}}));
    });
    aside.append(themeToggle);

    const foot = document.createElement("div");
    foot.className = "nav-foot";
    foot.textContent = "数据源：Tushare / Parquet\n运行模式：本地单机";
    aside.append(foot);

    aside.addEventListener("mouseenter", () => {
      if (autohideOn() && !holdCollapse) setExpanded(true);
    });
    aside.addEventListener("mouseleave", () => {
      holdCollapse = false;
      setExpanded(false);
    });
    aside.addEventListener("focusin", () => {
      if (!autohideOn() || holdCollapse) return;
      const active = document.activeElement;
      if (active === hideToggle || active === themeToggle) return;
      setExpanded(true);
    });
    aside.addEventListener("focusout", (event) => {
      if (!aside.contains(event.relatedTarget)) setExpanded(false);
    });
  }

  window.addEventListener("DOMContentLoaded", render);
})();
