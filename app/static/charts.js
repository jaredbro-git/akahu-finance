// Shared by the pages with charts: Chart.js defaults, money formatting, and helpers.
// Each page script reads them from window.financeCharts.
(() => {
  // "Hide numbers" (common.js): CSS blurs amounts on the page; charts swap their labels for $•••.
  const isPrivate = () => document.documentElement.classList.contains("private");
  document.addEventListener("privacychange", () => {
    if (typeof Chart !== "undefined") Object.values(Chart.instances).forEach((chart) => chart.update());
  });

  const data = JSON.parse(document.getElementById("chart-data")?.textContent || "{}");
  if (typeof Chart === "undefined") return;

  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const series = (i) => v(`--series-${i + 1}`);

  const money = (n, decimals = 0) =>
    (n < 0 ? "−" : "") +
    "$" +
    Math.abs(n).toLocaleString("en-NZ", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  const maskDigits = (text) => (isPrivate() ? String(text).replace(/\d/g, "•") : text);
  const chartMoney = (n, decimals) => (isPrivate() ? "$•••" : money(n, decimals));

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = v("--muted");
  Chart.defaults.borderColor = v("--grid");
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.labels.color = v("--text-2");
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.useBorderRadius = true;
  Chart.defaults.plugins.legend.labels.borderRadius = 2;
  Chart.defaults.plugins.tooltip.callbacks.label = (ctx) =>
    `${ctx.dataset.label ? ctx.dataset.label + ": " : ""}${chartMoney(ctx.parsed.y ?? ctx.parsed.x, 2)}`;

  const moneyAxis = { ticks: { callback: (value) => chartMoney(value) }, grid: { color: v("--grid") }, border: { display: false } };
  const categoryAxis = {
    grid: { display: false },
    border: { color: v("--axis") },
    ticks: { maxRotation: 0, autoSkip: true, autoSkipPadding: 12 },
  };

  // Bars: rounded data-end, 2px surface gap between neighbouring fills.
  const barStyle = (color) => ({
    backgroundColor: color,
    borderColor: v("--surface"),
    borderWidth: 1,
    borderRadius: 4,
    borderSkipped: "start",
    maxBarThickness: 28,
  });

  const escape = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // Every chart gets a collapsible table with the same numbers. Redrawing replaces it.
  function addTable(canvasId, labels, datasets) {
    const figure = document.getElementById(canvasId).closest("figure");
    const head = `<tr><th></th>${datasets.map((d) => `<th class="num">${escape(d.label)}</th>`).join("")}</tr>`;
    const rows = labels
      .map((label, i) => `<tr><td>${escape(label)}</td>${datasets.map((d) => `<td class="num">${money(d.data[i], 2)}</td>`).join("")}</tr>`)
      .join("");
    const old = figure.querySelector(":scope > details");
    const details = document.createElement("details");
    details.open = Boolean(old?.open);
    details.innerHTML = `<summary>Show as table</summary><div class="table-wrap"><table><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
    old ? old.replaceWith(details) : figure.appendChild(details);
  }

  function draw(id, config) {
    new Chart(document.getElementById(id), config);
    addTable(id, config.data.labels, config.data.datasets);
  }

  window.financeCharts = { data, isPrivate, v, series, money, maskDigits, chartMoney, moneyAxis, categoryAxis, barStyle, escape, addTable, draw };
})();
