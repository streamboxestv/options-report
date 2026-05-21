const state = {
  snapshots: [],
  selectedDate: null,
};

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}

function dedupeSnapshots(items) {
  const map = new Map();
  for (const item of items) {
    if (!item || !item.reportDate) {
      continue;
    }
    const key = `${item.reportDate}::${item.expiration || ""}`;
    if (!map.has(key)) {
      map.set(key, item);
    }
  }
  return Array.from(map.values()).sort((a, b) => {
    const aKey = a.reportDate || "";
    const bKey = b.reportDate || "";
    return bKey.localeCompare(aKey, undefined, { numeric: true });
  });
}

function statCard(label, value, detail = "") {
  return `
    <article class="stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value">${escapeHtml(value)}</div>
      <div class="stat-detail">${escapeHtml(detail)}</div>
    </article>
  `;
}

function renderTable(targetId, columns, rows) {
  const table = byId(targetId);
  const headerHtml = `
    <thead>
      <tr>
        ${columns.map((column) => `<th class="${column.numeric ? "num" : ""}">${escapeHtml(column.label)}</th>`).join("")}
      </tr>
    </thead>
  `;

  const bodyRows = rows.length
    ? rows.map((row) => `
      <tr>
        ${columns.map((column) => {
          const value = typeof column.render === "function" ? column.render(row) : escapeHtml(row[column.key] ?? "");
          return `<td class="${column.numeric ? "num" : ""}">${value}</td>`;
        }).join("")}
      </tr>
    `).join("")
    : `<tr><td colspan="${columns.length}">None</td></tr>`;

  table.innerHTML = `${headerHtml}<tbody>${bodyRows}</tbody>`;
}

function renderReviewList(targetId, rows) {
  const target = byId(targetId);
  if (!rows.length) {
    target.innerHTML = `<div class="review-item"><div class="review-item-title">None today</div></div>`;
    return;
  }
  target.innerHTML = rows.map((item) => `
    <article class="review-item">
      <div class="review-item-title">${escapeHtml(item.label)}: ${escapeHtml(item.ticker)}</div>
      <div class="review-item-meta">
        price <span class="mono">${escapeHtml(item.priceText)}</span> |
        avg weekly move <span class="mono">${escapeHtml(item.avgWeeklyMovePctText)}</span> |
        strike <span class="mono metric-strong">${escapeHtml(item.strikeText)}</span> |
        premium <span class="mono">${escapeHtml(item.premiumText)}</span> |
        ROI <span class="mono">${escapeHtml(item.roiPctText)}</span>
      </div>
    </article>
  `).join("");
}

function renderDateControls(snapshots) {
  const tabs = byId("date-tabs");
  const select = byId("report-select");
  tabs.innerHTML = "";
  select.innerHTML = "";

  for (const snapshot of snapshots) {
    const active = snapshot.reportDate === state.selectedDate;
    const label = `${snapshot.reportDate} - Exp ${snapshot.expiration || "N/A"}`;

    const button = document.createElement("button");
    button.className = `date-tab${active ? " active" : ""}`;
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => {
      state.selectedDate = snapshot.reportDate;
      renderDashboard();
    });
    tabs.appendChild(button);

    const option = document.createElement("option");
    option.value = snapshot.reportDate;
    option.textContent = label;
    option.selected = active;
    select.appendChild(option);
  }

  select.onchange = (event) => {
    state.selectedDate = event.target.value;
    renderDashboard();
  };
}

function currentSnapshot() {
  return state.snapshots.find((item) => item.reportDate === state.selectedDate) || state.snapshots[0];
}

function renderDashboard() {
  const snapshot = currentSnapshot();
  if (!snapshot) {
    byId("dashboard").classList.add("hidden");
    byId("loading-state").classList.add("hidden");
    byId("error-state").classList.remove("hidden");
    byId("error-message").textContent = "No report snapshots are available yet.";
    return;
  }

  renderDateControls(state.snapshots);

  byId("hero-title").textContent = `${snapshot.reportTitle} - ${snapshot.reportDate}`;
  byId("hero-subtitle").textContent = `Expiration ${snapshot.expiration || "N/A"} - ${snapshot.includedCount ?? 0} included out of ${snapshot.requestedCount ?? 0} tracked symbols.`;

  const statsHtml = [
    statCard("Report Date", snapshot.reportDate, `Expiration ${snapshot.expiration || "N/A"}`),
    statCard("Universe Included", String(snapshot.includedCount ?? 0), `${snapshot.requestedCount ?? 0} names tracked`),
    statCard("Portfolio Premium", snapshot.myPortfolio?.totalPremiumText || "$0.00", `${snapshot.myPortfolio?.rows?.length || 0} portfolio names`),
    statCard(
      "Sell Candidates",
      String((snapshot.coveredCalls?.rows?.length || 0) + (snapshot.cashSecuredPuts?.rows?.length || 0)),
      `${snapshot.coveredCalls?.rows?.length || 0} calls - ${snapshot.cashSecuredPuts?.rows?.length || 0} puts`,
    ),
  ].join("");
  byId("stats-grid").innerHTML = statsHtml;

  byId("portfolio-expiration").textContent = `Expiration ${snapshot.myPortfolio?.expiration || "N/A"}`;
  byId("covered-expiration").textContent = `Expiration ${snapshot.coveredCalls?.expiration || "N/A"}`;
  byId("puts-expiration").textContent = `Expiration ${snapshot.cashSecuredPuts?.expiration || "N/A"}`;

  renderTable(
    "portfolio-table",
    [
      { key: "ticker", label: "Ticker", render: (row) => `<span class="ticker">${escapeHtml(row.ticker)}</span>` },
      { key: "priceText", label: "Price", numeric: true },
      { key: "avgWeeklyMovePctText", label: "Avg Weekly Move %", numeric: true },
      { key: "strikeText", label: "Covered Call Strike", numeric: true, render: (row) => `<span class="metric-strong mono">${escapeHtml(row.strikeText)}</span>` },
      { key: "premiumText", label: "Premium", numeric: true },
    ],
    [
      ...(snapshot.myPortfolio?.rows || []),
      {
        ticker: "Total",
        priceText: "",
        avgWeeklyMovePctText: "",
        strikeText: "",
        premiumText: snapshot.myPortfolio?.totalPremiumText || "$0.00",
      },
    ],
  );

  const optionColumns = [
    { key: "ticker", label: "Ticker", render: (row) => `<span class="ticker">${escapeHtml(row.ticker)}</span>` },
    { key: "priceText", label: "Price", numeric: true },
    { key: "trend", label: "Trend" },
    { key: "avgWeeklyMovePctText", label: "Avg Weekly Move %", numeric: true },
    { key: "strikeText", label: "OTM Strike", numeric: true, render: (row) => `<span class="metric-strong mono">${escapeHtml(row.strikeText)}</span>` },
    { key: "premiumText", label: "Premium", numeric: true },
    { key: "roiPctText", label: "ROI %", numeric: true },
  ];

  renderTable("covered-table", optionColumns, snapshot.coveredCalls?.rows || []);
  renderTable("puts-table", optionColumns, snapshot.cashSecuredPuts?.rows || []);

  renderTable(
    "earnings-table",
    [
      { key: "ticker", label: "Ticker", render: (row) => `<span class="ticker">${escapeHtml(row.ticker)}</span>` },
      { key: "priceText", label: "Price", numeric: true },
      { key: "earningsDateText", label: "Earnings Date" },
      { key: "action", label: "Action", render: (row) => `<span class="mono">${escapeHtml(row.action)}</span>` },
      { key: "premiumText", label: "Premium", numeric: true },
      { key: "roiPctText", label: "ROI %", numeric: true },
    ],
    snapshot.earningsThisWeek?.rows || [],
  );

  renderReviewList("best-balance-list", snapshot.teamReview?.bestBalance || []);
  renderReviewList("aggressive-list", snapshot.teamReview?.aggressivePremium || []);
  byId("best-balance-why").textContent = snapshot.teamReview?.bestBalanceWhy || "";

  byId("loading-state").classList.add("hidden");
  byId("error-state").classList.add("hidden");
  byId("dashboard").classList.remove("hidden");
}

async function loadDashboard() {
  try {
    let latest;
    let history;
    try {
      [latest, history] = await Promise.all([
        fetchJson("/api/report"),
        fetchJson("/api/history"),
      ]);
    } catch (_apiError) {
      [latest, history] = await Promise.all([
        fetchJson("/latest_report.json"),
        fetchJson("/report_history.json").then((rows) => ({ snapshots: rows })),
      ]);
    }
    state.snapshots = dedupeSnapshots([latest, ...(history.snapshots || [])]);
    state.selectedDate = state.snapshots[0]?.reportDate || null;
    renderDashboard();
  } catch (error) {
    byId("loading-state").classList.add("hidden");
    byId("dashboard").classList.add("hidden");
    byId("error-state").classList.remove("hidden");
    byId("error-message").textContent = error.message || "The report feed could not be loaded.";
  }
}

loadDashboard();
