(function (w) {
  w.ql$ = function (id) { return document.getElementById(id); };
  w.qlQ = function (sel, root) { return (root || document).querySelector(sel); };
})(window);

let factorInfoPopover = null;

function closeFactorInfoPopover() {
  if (factorInfoPopover) {
    factorInfoPopover.remove();
    factorInfoPopover = null;
  }
}

function openFactorInfoPopover(trigger, title, lines) {
  closeFactorInfoPopover();
  const popover = document.createElement("div");
  popover.className = "factor-info-popover";
  const heading = document.createElement("strong");
  heading.textContent = title;
  popover.append(heading);
  lines.forEach((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    popover.append(p);
  });
  document.body.append(popover);
  const rect = trigger.getBoundingClientRect();
  popover.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - popover.offsetWidth - 8))}px`;
  popover.style.top = `${rect.bottom + 6}px`;
  factorInfoPopover = popover;
  const dismiss = (event) => {
    if (factorInfoPopover && !factorInfoPopover.contains(event.target) && !trigger.contains(event.target)) {
      closeFactorInfoPopover();
      document.removeEventListener("click", dismiss);
    }
  };
  setTimeout(() => document.addEventListener("click", dismiss), 0);
}

function infoDot(title, lines) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "info-dot";
  button.setAttribute("aria-label", `解释：${title}`);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  circle.setAttribute("cx", "12");
  circle.setAttribute("cy", "12");
  circle.setAttribute("r", "9");
  const body = document.createElementNS("http://www.w3.org/2000/svg", "path");
  body.setAttribute("d", "M12 11v5M12 8h.01");
  svg.append(circle, body);
  button.append(svg);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    openFactorInfoPopover(button, title, lines);
  });
  return button;
}
