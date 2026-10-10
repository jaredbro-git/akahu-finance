// Dashboard: net worth history, and the net worth tile adjusted for what the Projections
// page knows about (KiwiSaver, investments, student loan).
(() => {
  if (!window.financeCharts) return;
  const { data, isPrivate, v, series, money, maskDigits, chartMoney, moneyAxis, categoryAxis, barStyle, escape, addTable, draw } = window.financeCharts;

  // Net worth: bank accounts in blue; with the switch on, an orange line adds KiwiSaver and
  // investments and subtracts the student loan (Other accounts page). The tile follows the switch.
  const nw = data.net_worth;
  const tile = document.getElementById("nw-tile");
  const toggle = document.getElementById("include-other");
  const line = (label, values, colour) => ({
    label, data: values, borderColor: colour, backgroundColor: colour, borderWidth: 2,
    pointRadius: values.length > 30 ? 0 : 3, pointHoverRadius: 5, tension: 0.2,
  });
  const datasets = [line("Bank accounts", nw.values, series(0))];
  if (nw.with_other) datasets.push(line("Including other accounts", nw.with_other, series(1)));
  const chart = new Chart(document.getElementById("chart-net-worth"), {
    type: "line",
    data: { labels: nw.labels, datasets },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { ...categoryAxis, ticks: { ...categoryAxis.ticks, maxTicksLimit: 6 } }, y: moneyAxis },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            footer: (items) => items.length === 2
              ? `Other accounts: ${chartMoney(items[1].parsed.y - items[0].parsed.y, 2)}` : "",
          },
        },
      },
    },
  });

  function apply() {
    const on = Boolean(toggle?.checked && nw.with_other);
    chart.setDatasetVisibility(1, on);
    chart.data.datasets[0].label = on ? "Bank accounts" : "Net worth";
    chart.update();
    addTable("chart-net-worth", nw.labels, chart.data.datasets.filter((_, i) => i === 0 || on));
    tile.textContent = money(Number(on ? tile.dataset.withOther : tile.dataset.bank));
    document.getElementById("nw-tile-note").textContent = on
      ? `(incl. ${tile.dataset.otherNames.replace(/, ([^,]*)$/, " and $1")})`
      : "";
    try { localStorage.setItem("finance.includeOther.dashboard", on ? "1" : "0"); } catch { /* ignore */ }
  }
  if (toggle) {
    let saved = "1";
    try { saved = localStorage.getItem("finance.includeOther.dashboard") ?? "1"; } catch { /* ignore */ }
    toggle.checked = saved === "1";
    toggle.addEventListener("change", apply);
  }
  if (nw.with_other) apply(); else addTable("chart-net-worth", nw.labels, datasets);
})();
