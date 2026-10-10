// Projections page: savings account and net worth projections with "what if" controls.
(() => {
  if (!window.financeCharts) return;
  const { data, isPrivate, v, series, money, maskDigits, chartMoney, moneyAxis, categoryAxis, barStyle, escape, addTable, draw } = window.financeCharts;

  // Projections: actual month-end balances, then two projections from today's
  // balance ("current pace" and "with change") that the controls recalculate.
  const pj = data.projections;
  const MONTHS = 5 * 12;
  const FORTNIGHTS_PER_MONTH = 26 / 12;
  const HORIZONS = [1, 2, 5];
  const $ = (id) => document.getElementById(id);
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const num = (el, fallback = 0) => (el.value === "" ? fallback : Number(el.value) || 0);
  const signed = (n) => (n >= 0 ? "+" : "") + money(n);
  const amount = (n) => `<span class="private-mask">${money(n)}</span>`;
  const gap = (n) => Array(n).fill(null);

  // Browser storage is a convenience only: the page works without it.
  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } },
  };

  const [startMonth, startYear] = pj.start_label.split(" ");
  const start = new Date(`${startMonth} 1, ${startYear}`);
  const futureLabels = Array.from({ length: MONTHS + 1 }, (_, i) =>
    new Date(start.getFullYear(), start.getMonth() + i, 1).toLocaleDateString("en-NZ", { month: "short", year: "numeric" }),
  );

  function makeChart(canvasId, hist) {
    const line = (label, color, dashed) => ({
      label, data: [], borderColor: color, backgroundColor: color, borderWidth: 2,
      borderDash: dashed ? [6, 4] : [], pointRadius: 0, pointHoverRadius: 5, tension: dashed ? 0 : 0.2,
    });
    // Chart.js legends can't show dashes, so draw our own.
    const key = (label, color, dashed, cls = "") =>
      `<span class="${cls}"><svg width="26" height="10" aria-hidden="true"><line x1="1" y1="5" x2="25" y2="5" stroke="${color}" stroke-width="2"${dashed ? ' stroke-dasharray="6 4"' : ""}/></svg>${label}</span>`;
    $(canvasId).closest(".chart").insertAdjacentHTML("afterend",
      `<div class="line-legend">${key("Actual", series(0), false)}${key("Current pace", series(0), true)}${key("With change", series(1), true, "scenario-key")}</div>`);
    const actual = line("Actual", series(0), false);
    actual.data = [...hist.month_end_balance, hist.balance, ...gap(MONTHS)];
    return new Chart($(canvasId), {
      type: "line",
      data: {
        labels: [...hist.labels, ...futureLabels],
        datasets: [actual, line("Current pace", series(0), true), line("With change", series(1), true)],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: { ...categoryAxis, ticks: { ...categoryAxis.ticks, maxTicksLimit: 8 } }, y: moneyAxis },
        plugins: {
          legend: { display: false },
          // Skip the projections' starting point, which repeats today's actual balance.
          tooltip: { filter: (item) => item.parsed.y !== null && !(item.datasetIndex > 0 && item.dataIndex === hist.labels.length && item.chart.data.datasets[0].data[item.dataIndex] === item.parsed.y) },
        },
      },
    });
  }

  function setPaths(chart, hist, base, scenario, showScenario) {
    const round = (path) => path.map((b) => Math.round(b * 100) / 100);
    chart.data.datasets[1].data = [...gap(hist.labels.length), ...round(base)];
    chart.data.datasets[2].data = [...gap(hist.labels.length), ...round(scenario)];
    chart.data.datasets[2].hidden = !showScenario;
    chart.canvas.closest("figure").querySelector(".scenario-key").hidden = !showScenario;
    chart.update();
  }

  // With a "what if" change, the changed value is the headline, with the difference under it.
  function renderTiles(containerId, base, scenario, showScenario) {
    $(containerId).innerHTML = HORIZONS.map((years) => {
      const b = base[years * 12];
      const s = scenario[years * 12];
      const label = `<span class="tile-label">In ${years} year${years > 1 ? "s" : ""}</span>`;
      if (!showScenario) {
        return `<div class="tile">${label}<span class="tile-value">${money(b)}</span>
          <span class="tile-sub"><i class="swatch" style="background:${series(0)}"></i>At current pace</span></div>`;
      }
      const diff = s - b;
      return `<div class="tile">${label}
        <span class="tile-value scenario">${money(s)}</span>
        <span class="tile-change ${diff >= 0 ? "up" : "down"}">${diff >= 0 ? "▲" : "▼"} <span class="private-mask">${signed(diff)}</span></span>
        <span class="tile-sub">At current pace <b>${money(b)}</b></span>
      </div>`;
    }).join("");
  }

  // Balance after each month: grows by `monthly`, plus monthly-compounded interest.
  function simulate(startBalance, monthly, ratePct) {
    const r = ratePct / 100 / 12;
    const path = [startBalance];
    for (let i = 1; i <= MONTHS; i++) path.push(path[i - 1] * (1 + r) + monthly);
    return path;
  }

  const basisEl = $("pj-basis");
  const deltaEl = $("pj-delta");
  const deltaRangeEl = $("pj-delta-range");
  const rateEl = $("pj-rate");
  // KiwiSaver, investments and student loan come from the Other accounts page; the switch
  // decides whether the net worth projection includes them.
  const includeEl = $("include-other");
  if (includeEl) {
    includeEl.checked = (store.get("finance.includeOther.projections") ?? true) === true;
    includeEl.addEventListener("change", () => { store.set("finance.includeOther.projections", includeEl.checked); update(); });
  }
  const other = (item, field, fallback = 0) => {
    const value = includeEl?.checked ? pj.other[item]?.[field] : undefined;
    return value ?? (includeEl?.checked ? fallback : 0);
  };

  const sv = pj.savings;
  const bank = pj.net_worth;
  const savingsChart = sv && makeChart("chart-pj-savings", sv);
  const netWorthChart = makeChart("chart-pj-net-worth", bank);
  // The last n complete months, but never before the account's first full month.
  const paceMonths = (hist, n) => {
    const from = Math.max(hist.labels.length - n, hist.pace_start);
    return { inflow: hist.money_in.slice(from), outflow: hist.money_out.slice(from) };
  };
  const avgNet = (hist, n) => { const p = paceMonths(hist, n); return mean(p.inflow) - mean(p.outflow); };
  const periodText = (hist, n) => {
    const used = paceMonths(hist, n).inflow.length;
    return used === n ? `Over the last ${n} months` : `Over the ${used} full month${used === 1 ? "" : "s"} since this account opened`;
  };

  function update() {
    const n = Number(basisEl.value);
    const delta = num(deltaEl);
    const extraMonthly = delta * FORTNIGHTS_PER_MONTH;
    const rate = num(rateEl);
    const changed = delta !== 0;

    // Savings account
    let interestBase = gap(MONTHS + 1).fill(0);
    let interestScenario = interestBase;
    if (sv) {
      const monthly = avgNet(sv, n);
      const base = simulate(sv.balance, monthly, rate);
      const scenario = simulate(sv.balance, monthly + extraMonthly, rate);
      // Interest earned, so the net worth projection can include it too.
      interestBase = base.map((b, i) => b - (sv.balance + monthly * i));
      interestScenario = scenario.map((b, i) => b - (sv.balance + (monthly + extraMonthly) * i));
      setPaths(savingsChart, sv, base, scenario, changed);
      renderTiles("pj-savings-tiles", base, scenario, changed);
      const pace = paceMonths(sv, n);
      $("pj-savings-note").innerHTML = (pace.inflow.length === 0
        ? `${escape(sv.account_name)} doesn't have a full month of history yet, so its current pace is shown as ${amount(0)}. ` +
          `Its opening deposit isn't counted as saving. Use the "what if" amount to try out your plans.`
        : `${periodText(sv, n)}, ${amount(mean(pace.inflow))} a month went into ${escape(sv.account_name)} on average ` +
          `and ${amount(mean(pace.outflow))} came out, so it grew ${amount(monthly)} a month.`) +
        (changed ? ` With your change it would grow ${amount(monthly + extraMonthly)} a month.` : "");
    }

    // Net worth: bank accounts + KiwiSaver + investments − student loan
    const ksBalance = other("kiwisaver", "balance");
    const ksContrib = other("kiwisaver", "contrib") * FORTNIGHTS_PER_MONTH;
    const ksReturn = other("kiwisaver", "return", pj.default_return);
    const slBalance = other("student_loan", "balance");
    const slRepay = other("student_loan", "repay") * FORTNIGHTS_PER_MONTH;
    const kiwisaver = simulate(ksBalance, ksContrib, ksReturn);
    const invBalance = other("investment", "balance");
    const invTotal = simulate(invBalance, other("investment", "contrib") * FORTNIGHTS_PER_MONTH, other("investment", "return", pj.default_return));
    const loan = [slBalance];
    for (let i = 1; i <= MONTHS; i++) loan.push(Math.max(0, loan[i - 1] - slRepay));

    const monthly = avgNet(bank, n);
    const total = (extra, interest) => (_, i) => bank.balance + (monthly + extra) * i + interest[i] + kiwisaver[i] + invTotal[i] - loan[i];
    const base = kiwisaver.map(total(0, interestBase));
    const scenario = kiwisaver.map(total(extraMonthly, interestScenario));
    setPaths(netWorthChart, bank, base, scenario, changed);
    renderTiles("pj-net-worth-tiles", base, scenario, changed);

    const included = [ksBalance > 0 && "KiwiSaver", invBalance > 0 && "investments", slBalance > 0 && "student loan"].filter(Boolean);
    const hasManual = included.length > 0;
    let note =
      `Over the last ${n} months your bank accounts together grew ${amount(monthly)} a month on average ` +
      `(everything that came in minus everything that went out).`;
    if (changed) note += ` With your change: ${amount(monthly + extraMonthly)} a month.`;
    if (slBalance > 0) {
      note += slRepay > 0
        ? ` At ${amount(slRepay / FORTNIGHTS_PER_MONTH)} a fortnight, the student loan is paid off around ${
            new Date(start.getFullYear(), start.getMonth() + Math.ceil(slBalance / slRepay), 1)
              .toLocaleDateString("en-NZ", { month: "long", year: "numeric" })}.`
        : " Add your student loan repayments on the Other accounts page to see it go down.";
    }
    $("pj-net-worth-note").innerHTML = note;
    $("pj-net-worth-caption").textContent = hasManual
      ? `· the actual line is bank accounts only; projections also include ${included.join(", ").replace(/, ([^,]*)$/, " and $1")}`
      : "";
  }

  basisEl.addEventListener("change", update);
  rateEl.addEventListener("input", update);
  deltaEl.addEventListener("input", () => { deltaRangeEl.value = deltaEl.value || 0; update(); });
  deltaRangeEl.addEventListener("input", () => { deltaEl.value = deltaRangeEl.value; update(); });

  // Tabs
  const tabs = [...document.querySelectorAll("#projections [role=tab]")];
  function selectTab(name) {
    for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    for (const panel of document.querySelectorAll("#projections [role=tabpanel]")) panel.hidden = panel.dataset.panel !== name;
    store.set("finance.tab", name);
  }
  for (const tab of tabs) tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  const savedTab = store.get("finance.tab");
  selectTab(tabs.some((t) => t.dataset.tab === savedTab) ? savedTab : tabs[0].dataset.tab);

  update();
})();
