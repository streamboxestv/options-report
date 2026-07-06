const state = {
  snapshots: [],
  selectedDateIso: null,
};

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
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
    const dateIso = snapshotDateIso(item);
    if (!item || !item.reportDate || !dateIso) {
      continue;
    }
    const key = `${dateIso}::${item.expiration || ""}`;
    if (!map.has(key)) {
      map.set(key, item);
    }
  }
  return Array.from(map.values()).sort((a, b) => {
    const aKey = snapshotDateIso(a) || "";
    const bKey = snapshotDateIso(b) || "";
    return bKey.localeCompare(aKey, undefined, { numeric: true });
  });
}

function snapshotDateIso(snapshot) {
  if (!snapshot) {
    return null;
  }
  return snapshot.reportDateIso || snapshot.generatedAt?.slice(0, 10) || null;
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

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "$0.00";
  }
  return number.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function portfolioPositionValueText(portfolio) {
  if (portfolio?.totalPositionValueText) {
    return portfolio.totalPositionValueText;
  }
  const total = (portfolio?.rows || []).reduce((sum, row) => sum + (Number(row.price) || 0) * 100, 0);
  return formatMoney(total);
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

function renderPortfolioTable(targetId, portfolio, strikeLabel) {
  const sortedRows = [...(portfolio?.rows || [])].sort(
    (left, right) => (right.premium || 0) - (left.premium || 0),
  );
  renderTable(
    targetId,
    [
      { key: "ticker", label: "Ticker", render: (row) => `<span class="ticker">${escapeHtml(row.ticker)}</span>` },
      { key: "priceText", label: "Price", numeric: true },
      { key: "avgWeeklyMovePctText", label: "Avg Weekly Move %", numeric: true },
      { key: "strikeText", label: strikeLabel, numeric: true, render: (row) => `<span class="metric-strong mono">${escapeHtml(row.strikeText)}</span>` },
      { key: "premiumText", label: "Premium", numeric: true },
    ],
    [
      ...sortedRows,
      {
        ticker: "Total",
        priceText: portfolioPositionValueText(portfolio),
        avgWeeklyMovePctText: "",
        strikeText: "",
        premiumText: portfolio?.totalPremiumText || "$0.00",
      },
    ],
  );
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
  const datePicker = byId("report-date-picker");

  const availableDates = snapshots
    .map((snapshot) => snapshotDateIso(snapshot))
    .filter(Boolean);
  if (availableDates.length) {
    datePicker.min = availableDates[availableDates.length - 1];
    datePicker.max = availableDates[0];
  }
  datePicker.value = state.selectedDateIso || "";

  datePicker.onchange = (event) => {
    state.selectedDateIso = event.target.value;
    renderDashboard();
  };
}

function currentSnapshot() {
  return state.snapshots.find((item) => snapshotDateIso(item) === state.selectedDateIso) || null;
}

function isArchivePlaceholder(snapshot) {
  return Boolean(
    snapshot &&
    (snapshot.includedCount ?? 0) === 0 &&
    (snapshot.coveredCalls?.rows?.length || 0) === 0 &&
    (snapshot.cashSecuredPuts?.rows?.length || 0) === 0 &&
    (snapshot.myPortfolio?.rows?.length || 0) === 0
  );
}

function renderDashboard() {
  renderDateControls(state.snapshots);
  const snapshot = currentSnapshot();
  if (!snapshot) {
    byId("dashboard").classList.add("hidden");
    byId("loading-state").classList.add("hidden");
    byId("error-state").classList.remove("hidden");
    byId("error-message").textContent = state.selectedDateIso
      ? `No report snapshot is available for ${state.selectedDateIso}.`
      : "No report snapshots are available yet.";
    return;
  }

  byId("hero-title").textContent = `${snapshot.reportTitle} - ${snapshot.reportDate}`;
  byId("hero-subtitle").textContent = `Expiration ${snapshot.expiration || "N/A"} - ${snapshot.includedCount ?? 0} included out of ${snapshot.requestedCount ?? 0} tracked symbols.`;

  const archiveNotice = byId("archive-notice");
  const archiveNoticeText = byId("archive-notice-text");
  if (isArchivePlaceholder(snapshot)) {
    archiveNoticeText.textContent = snapshot.teamReview?.bestBalanceWhy || "This archived date does not have a preserved full snapshot.";
    archiveNotice.classList.remove("hidden");
  } else {
    archiveNoticeText.textContent = "";
    archiveNotice.classList.add("hidden");
  }

  const statsHtml = [
    statCard("Report Date", snapshot.reportDate, `Expiration ${snapshot.expiration || "N/A"}`),
    statCard("Universe Included", String(snapshot.includedCount ?? 0), `${snapshot.requestedCount ?? 0} names tracked`),
    statCard("Covered Call Premium", snapshot.myPortfolio?.totalPremiumText || "$0.00", `${snapshot.myPortfolio?.rows?.length || 0} portfolio names`),
    statCard("Cash Puts Premium", snapshot.myPortfolioPuts?.totalPremiumText || "$0.00", `${snapshot.myPortfolioPuts?.rows?.length || 0} portfolio names`),
  ].join("");
  byId("stats-grid").innerHTML = statsHtml;

  byId("portfolio-expiration").textContent = `Expiration ${snapshot.myPortfolio?.expiration || "N/A"}`;
  byId("portfolio-put-expiration").textContent = `Expiration ${snapshot.myPortfolioPuts?.expiration || snapshot.myPortfolio?.expiration || "N/A"}`;
  byId("covered-expiration").textContent = `Expiration ${snapshot.coveredCalls?.expiration || "N/A"}`;
  byId("puts-expiration").textContent = `Expiration ${snapshot.cashSecuredPuts?.expiration || "N/A"}`;

  renderPortfolioTable("portfolio-table", snapshot.myPortfolio, "Covered Call Strike");
  renderPortfolioTable("portfolio-put-table", snapshot.myPortfolioPuts, "Cash Secured Puts");

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
    state.selectedDateIso = snapshotDateIso(state.snapshots[0]);
    renderDashboard();
  } catch (error) {
    byId("loading-state").classList.add("hidden");
    byId("dashboard").classList.add("hidden");
    byId("error-state").classList.remove("hidden");
    byId("error-message").textContent = error.message || "The report feed could not be loaded.";
  }
}

loadDashboard();
