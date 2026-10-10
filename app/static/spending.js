// Spending page: the category and income/spending charts (with period, Actual/Average and
// date-range controls), the category popup, and top payees.
(() => {
  if (!window.financeCharts) return;
  const { data, isPrivate, v, series, money, maskDigits, chartMoney, moneyAxis, categoryAxis, barStyle, escape, addTable, draw } = window.financeCharts;

  // Charts with a Daily / Weekly / Fortnightly / Monthly switch and an Actual / Average
  // switch. Each mode has `build(view)` returning { labels, datasets, titles?, note? } and
  // its own chart options; `view` holds the server's labels and fuller titles for tooltips.
  const PERIODS = [["daily", "Daily"], ["weekly", "Weekly"], ["fortnightly", "Fortnightly"], ["monthly", "Monthly"]];
  const MODES = [["actual", "Actual"], ["average", "Average"]];
  const UNIT = { daily: "day", weekly: "week", fortnightly: "fortnight", monthly: "month" };
  const PER_TEXT = {
    daily: "per day",
    weekly: "per week",
    fortnightly: data.payday ? `per fortnight, payday to payday (${data.payday}s)` : "per fortnight",
    monthly: "per month",
  };
  // Averages leave out the current period, which isn't over yet.
  const complete = (values) => values.slice(0, -1);
  const average = (values) => { const v = complete(values); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0; };
  const averageNote = (view, period) => {
    const n = complete(view.labels).length;
    if (n === 0) return `average ${PER_TEXT[period]}: no full ${UNIT[period]} in this range yet, so pick a longer range`;
    return `average ${PER_TEXT[period]}, over the last ${n} full ${UNIT[period]}${n === 1 ? "" : "s"}`;
  };

  function periodChart(id, views, modes) {
    const canvas = document.getElementById(id);
    const figure = canvas.closest("figure");
    let chart, titles;
    const state = { period: "monthly", mode: "actual" };
    try {
      state.period = localStorage.getItem(`finance.period.${id}`) || state.period;
      state.mode = localStorage.getItem(`finance.mode.${id}`) || state.mode;
    } catch { /* ignore */ }
    if (!views[state.period]) state.period = "monthly";
    if (!modes[state.mode]) state.mode = "actual";

    const switcher = figure.querySelector(".period-switch");
    switcher.innerHTML = PERIODS.map(([key, name]) => `<button type="button" data-period="${key}">${name}</button>`).join("");
    const modeSwitch = document.createElement("span");
    modeSwitch.className = "tabs mode-switch";
    modeSwitch.setAttribute("role", "group");
    modeSwitch.setAttribute("aria-label", "Show");
    modeSwitch.innerHTML = MODES.map(([key, name]) => `<button type="button" data-mode="${key}">${name}</button>`).join("");
    const controls = document.createElement("span");
    controls.className = "chart-controls";
    switcher.before(controls);
    controls.append(modeSwitch, switcher);

    function show() {
      const view = views[state.period];
      const { build, options } = modes[state.mode];
      const built = build(view, state.period);
      titles = built.titles || built.labels;
      chart?.destroy();
      chart = new Chart(canvas, {
        type: "bar",
        data: { labels: built.labels, datasets: built.datasets },
        options: {
          ...options,
          onClick: (event, elements) => {
            if (elements.length && modes[state.mode].onBarClick) modes[state.mode].onBarClick(elements[0], chart, view, state.period);
          },
          onHover: (event, elements) => {
            if (modes[state.mode].onBarClick) event.native.target.style.cursor = elements.length ? "pointer" : "default";
          },
          plugins: {
            ...options.plugins,
            tooltip: { ...options.plugins?.tooltip, callbacks: { ...options.plugins?.tooltip?.callbacks, title: (items) => titles[items[0].dataIndex] } },
          },
        },
      });
      addTable(id, titles, built.datasets);
      figure.querySelector(".per").textContent = built.note || PER_TEXT[state.period];
      for (const b of switcher.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.period === state.period));
      for (const b of modeSwitch.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode));
      try {
        localStorage.setItem(`finance.period.${id}`, state.period);
        localStorage.setItem(`finance.mode.${id}`, state.mode);
      } catch { /* ignore */ }
    }
    // Redraw on "Hide numbers" too, so totals in the caption get masked.
    document.addEventListener("privacychange", show);
    switcher.addEventListener("click", (event) => { if (event.target.dataset.period) { state.period = event.target.dataset.period; show(); } });
    modeSwitch.addEventListener("click", (event) => { if (event.target.dataset.mode) { state.mode = event.target.dataset.mode; show(); } });
    show();
  }

  // Popup with what's inside one category bar: its most frequent merchants, then the rest,
  // and (for an actual period) the transactions themselves. Loaded from the server on click.
  const dialog = document.getElementById("breakdown");
  dialog.querySelector(".breakdown-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });  // the backdrop
  const amountCell = (n) => `<span class="private-mask">${money(n, 2)}</span>`;

  async function openBreakdown({ freq, category, period, colour }) {
    $title.textContent = category;
    $sub.textContent = "Loading…";
    $body.innerHTML = "";
    dialog.showModal();
    const params = new URLSearchParams({ freq, category, range: data.range });
    if (period !== undefined) params.set("period", period);
    let d;
    try {
      const response = await fetch(`/api/category-breakdown?${params}`);
      d = await response.json();
      if (!response.ok) throw new Error(d.error || response.statusText);
    } catch (error) {
      $sub.textContent = `Couldn't load the breakdown: ${error.message}`;
      return;
    }
    const per = d.mode === "average" ? ` a ${UNIT[freq]}` : "";
    $sub.textContent = d.heading;
    const widest = Math.max(...d.merchants.map((m) => Math.abs(m.amount)), 1);
    $body.innerHTML = `
      <p class="breakdown-total"><span class="private-mask">${money(d.total, 2)}</span>${per}
        <span class="muted">· ${d.count} transaction${d.count === 1 ? "" : "s"}${d.mode === "average" ? " in total" : ""}</span></p>
      <h3>${d.mode === "average" ? `Average${per}, by merchant` : "By merchant"} <small class="muted">· the ${Math.min(5, d.merchants.filter((m) => !m.other).length)} you paid most often</small></h3>
      <ul class="merchant-bars">
        ${d.merchants.map((m) => `
          <li class="${m.other ? "is-other" : ""} ${m.amount < 0 ? "is-negative" : ""}">
            <span class="merchant-name">${escape(m.name)} <small class="muted">${m.count}×</small></span>
            <span class="merchant-bar"><span style="width:${(Math.abs(m.amount) / widest) * 100}%;background:${m.other ? v("--text-2") : colour}"></span></span>
            <span class="num">${amountCell(m.amount)}</span>
          </li>`).join("")}
      </ul>
      ${d.merchants.some((m) => m.amount < 0) ? `<p class="muted small-print">A negative amount is money paid back to you, which offsets this category.</p>` : ""}
      ${d.transactions.length ? `
        <h3>Transactions</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>Date</th><th>Payee</th>${category === "Other" ? "<th>Category</th>" : ""}<th>Account</th><th class="num">Amount</th></tr></thead>
          <tbody>${d.transactions.map((t) => `<tr>
            <td>${escape(t.date)}</td><td>${escape(t.payee)}</td>${category === "Other" ? `<td>${escape(t.category)}</td>` : ""}
            <td>${escape(t.account)}</td><td class="num">${money(t.amount, 2)}</td></tr>`).join("")}</tbody>
        </table></div>` : ""}`;
  }
  const $title = document.getElementById("breakdown-title");
  const $sub = document.getElementById("breakdown-sub");
  const $body = document.getElementById("breakdown-body");

  // Spending by category: stacked bars. The top categories differ between views, so
  // colours follow the category name rather than its position; "Other" keeps the last slot.
  const groups = data.spend_by_group;
  const groupColour = new Map();
  for (const view of [groups.monthly, ...Object.values(groups)]) {
    for (const s of view.series) {
      if (s.name !== "Other" && !groupColour.has(s.name) && groupColour.size < 7) groupColour.set(s.name, series(groupColour.size));
    }
  }
  // A category that's only in the top 7 for some views borrows a colour unused in that view.
  function coloursFor(view) {
    const taken = new Set(view.series.map((s) => groupColour.get(s.name)).filter(Boolean));
    const free = [0, 1, 2, 3, 4, 5, 6].map(series).filter((c) => !taken.has(c));
    return view.series.map((s) => (s.name === "Other" ? series(7) : groupColour.get(s.name) ?? free.shift()));
  }
  periodChart("chart-spend-by-group", groups, {
    actual: {
      build: (view) => {
        const colours = coloursFor(view);
        return {
          labels: view.labels,
          titles: view.titles,
          datasets: view.series.map((s, i) => ({ label: s.name, data: s.data, ...barStyle(colours[i]), borderRadius: 2 })),
        };
      },
      onBarClick: (bar, chart, view, period) => {
        const dataset = chart.data.datasets[bar.datasetIndex];
        openBreakdown({ freq: period, category: dataset.label, period: bar.index, colour: dataset.backgroundColor });
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: { ...categoryAxis, stacked: true }, y: { ...moneyAxis, stacked: true } },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            itemSort: (a, b) => b.parsed.y - a.parsed.y,
            filter: (item) => item.parsed.y !== 0,
            callbacks: { footer: (items) => `Total: ${chartMoney(items.reduce((sum, i) => sum + i.parsed.y, 0), 2)}` },
          },
        },
      },
    },
    // One bar per category: its average per period. Categories keep their colours.
    average: {
      build: (view, period) => {
        const total = view.series.reduce((sum, s) => sum + average(s.data), 0);
        return {
          labels: view.series.map((s) => s.name),
          datasets: [{
            label: `Average per ${UNIT[period]}`,
            data: view.series.map((s) => Math.round(average(s.data) * 100) / 100),
            ...barStyle(coloursFor(view)),
          }],
          note: `${averageNote(view, period)} · ${chartMoney(total)} in total`,
        };
      },
      onBarClick: (bar, chart, view, period) => {
        openBreakdown({ freq: period, category: chart.data.labels[bar.index], colour: chart.data.datasets[0].backgroundColor[bar.index] });
      },
      // Horizontal, so every category name fits.
      options: {
        indexAxis: "y",
        scales: { x: moneyAxis, y: { ...categoryAxis, ticks: { ...categoryAxis.ticks, autoSkip: false } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => chartMoney(ctx.parsed.x, 2) } } },
      },
    },
  });

  // Income vs spending: grouped bars over time, or the averages side by side.
  periodChart("chart-cashflow", data.cashflow, {
    actual: {
      build: (view) => ({
        labels: view.labels,
        titles: view.titles,
        datasets: [
          { label: "Income", data: view.income, ...barStyle(series(0)) },
          { label: "Spending", data: view.spending, ...barStyle(series(1)) },
        ],
      }),
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: categoryAxis, y: moneyAxis },
        plugins: { legend: { position: "bottom" } },
      },
    },
    average: {
      build: (view, period) => {
        const income = average(view.income);
        const spending = average(view.spending);
        const round = (n) => Math.round(n * 100) / 100;
        return {
          labels: ["Income", "Spending", "Left over"],
          datasets: [{
            label: `Average per ${UNIT[period]}`,
            data: [round(income), round(spending), round(income - spending)],
            ...barStyle([series(0), series(1), series(2)]),
          }],
          note: averageNote(view, period),
        };
      },
      options: {
        scales: { x: categoryAxis, y: moneyAxis },
        plugins: { legend: { display: false } },
      },
    },
  });

  // Top payees: horizontal bars, one series.
  const payees = data.top_payees;
  draw("chart-top-payees", {
    type: "bar",
    data: {
      labels: payees.labels,
      datasets: [{ label: "Spent", data: payees.values, ...barStyle(series(0)) }],
    },
    options: {
      indexAxis: "y",
      scales: {
        x: moneyAxis,
        // Payee names can carry amounts, card and account numbers.
        y: { ...categoryAxis, border: { color: v("--axis") }, ticks: { ...categoryAxis.ticks, callback(value) { return maskDigits(this.getLabelForValue(value)); } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: (items) => maskDigits(items[0].label), label: (ctx) => chartMoney(ctx.parsed.x, 2) } },
      },
    },
  });

})();
