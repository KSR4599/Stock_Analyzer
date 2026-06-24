const state = {
  overview: null,
  portfolio: null,
  ideas: { production: null, shadow: null },
  performance: null,
  health: null,
};

const colors = ["#087a61", "#3167d4", "#7246d6", "#d3731f", "#bd2354", "#238ca8", "#6d768a", "#b28a13", "#5045ba", "#80a896"];
const actionColors = {
  "EXIT REVIEW": "#b83232",
  "TRIM REVIEW": "#d58a00",
  "BUY-MORE REVIEW": "#3167d4",
  "WATCH": "#c6a322",
  "HOLD": "#087a61",
};

const money = (value) => value == null ? "—" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: value >= 1000 ? 0 : 2,
}).format(value);
const number = (value, digits = 1) => value == null ? "—" : Number(value).toFixed(digits);
const pct = (value, digits = 1) => value == null ? "—" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
const dateText = (value) => value ? new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "No data";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const signedClass = (value) => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
const signed = (value, digits = 1) => value == null ? "—" : `${Number(value) >= 0 ? "+" : ""}${number(value, digits)}`;
const actionClass = (action) => `action-${String(action).toLowerCase().replaceAll(" ", "-")}`;
const actionBadge = (action) => `<span class="action-badge ${actionClass(action)}">${escapeHtml(action || "—")}</span>`;
const movementBadge = (item) => {
  const state = String(item.signal_state || "steady").replaceAll("_", " ");
  const delta = item.score_delta == null ? "new" : `${Number(item.score_delta) >= 0 ? "+" : ""}${number(item.score_delta, 1)}`;
  return `<span class="movement-badge ${escapeHtml(item.signal_state || "steady")}">${escapeHtml(state)} · ${delta}</span>`;
};
const calibrationLabel = (calibration) => {
  if (!calibration) return "unmeasured";
  return `${calibration.confidence || "unmeasured"} · n=${calibration.sample_count || 0} episodes`;
};
const calibrationWin = (calibration) => {
  if (!calibration || calibration.win_rate_pct == null) return "—";
  const median = calibration.median_return_pct == null ? "" : ` · med ${pct(calibration.median_return_pct, 1)}`;
  return `${number(calibration.win_rate_pct, 1)}%${median}`;
};

async function api(path) {
  const response = await fetch(path, { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
  return response.json();
}

function showError(error) {
  const toast = document.querySelector("#error-toast");
  toast.textContent = error.message || String(error);
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 5000);
}

function healthDot(status) {
  return status === "healthy" ? "●" : status === "warning" ? "▲" : "■";
}

async function loadAll() {
  try {
    const [overview, portfolio, production, shadow, performance, health] = await Promise.all([
      api("/api/overview"), api("/api/portfolio"), api("/api/ideas?source=production"),
      api("/api/ideas?source=shadow"), api("/api/performance"), api("/api/health"),
    ]);
    Object.assign(state, { overview, portfolio, performance, health });
    state.ideas.production = production;
    state.ideas.shadow = shadow;
    renderOverview();
    renderPortfolio();
    renderIdeas();
    renderPerformance();
  } catch (error) {
    showError(error);
  }
}

function renderOverview() {
  const { overview } = state;
  const services = overview.health.services;
  const worst = services.some(x => x.status === "critical") ? "critical" : services.some(x => x.status === "warning") ? "warning" : "healthy";
  const global = document.querySelector("#global-health");
  global.className = `health-pill ${worst}`;
  global.textContent = worst === "healthy" ? "All monitored systems healthy" : worst === "warning" ? "Attention - data stale" : "Operational issue detected";

  document.querySelector("#health-banner").innerHTML = services.map(service => `
    <div class="health-card ${service.status}">
      <strong>${healthDot(service.status)} ${escapeHtml(service.name)}</strong>
      <span>${escapeHtml(service.detail)}</span>
    </div>`).join("");

  const portfolio = overview.portfolio || {};
  const actions = portfolio.actions || {};
  document.querySelector("#summary-cards").innerHTML = [
    ["Market value", money(portfolio.market_value), `${overview.allocation.length} allocation groups`],
    ["Return vs. cost", pct(portfolio.return_pct, 2), `Cost basis ${money(portfolio.total_cost)}`],
    ["Priority reviews", (actions["EXIT REVIEW"] || 0) + (actions["TRIM REVIEW"] || 0) + (actions["BUY-MORE REVIEW"] || 0), `${actions.WATCH || 0} watch · ${actions.HOLD || 0} hold`],
    ["Market coverage", `${number(portfolio.coverage_pct, 1)}%`, portfolio.degraded ? "Actionable changes suppressed" : "Healthy assessment"],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${label}</span><div class="metric">${value}</div><span>${note}</span></div>`).join("");

  const pulse = overview.pulse || {};
  document.querySelector("#decision-pulse").innerHTML = [
    ["New candidates", pulse.new_candidates || 0, "Crossed into actionable review"],
    ["Upgrades", pulse.upgrades || 0, "Score or rank accelerated"],
    ["Downgrades", pulse.downgrades || 0, "Momentum or evidence weakened"],
    ["Fresh insights", pulse.fresh_insights || 0, "New reasons and watchouts"],
  ].map(([label, value, note]) => `<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join("");

  renderAllocation(overview.allocation);
  renderCandidateList("#production-candidates", overview.production.ideas, false);
  renderCandidateList("#shadow-candidates", overview.shadow.ideas, true);
  renderMovers(overview.movers || []);
  renderAgreement(overview.agreement || { rows: [], sample_count: 0, aligned_count: 0 });
  document.querySelector("#allocation-as-of").textContent = dateText(state.portfolio?.as_of);
  document.querySelector("#production-as-of").textContent = dateText(overview.production.as_of);
  document.querySelector("#shadow-as-of").textContent = dateText(overview.shadow.as_of);
  document.querySelector("#change-feed").innerHTML = overview.changes.length ? overview.changes.map(item => `
    <div class="change-row ${item.kind}"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p></div>`).join("") : `<div class="empty">No material changes recorded.</div>`;
}

function renderAllocation(allocation) {
  const total = allocation.reduce((sum, item) => sum + item.allocation_pct, 0) || 100;
  let cursor = 0;
  const segments = allocation.map((item, index) => {
    const start = cursor;
    cursor += item.allocation_pct / total * 100;
    return `${colors[index % colors.length]} ${start}% ${cursor}%`;
  });
  document.querySelector("#allocation-chart").style.background = `conic-gradient(${segments.join(",")})`;
  document.querySelector("#allocation-count").textContent = state.portfolio?.sample_count ?? "—";
  document.querySelector("#allocation-legend").innerHTML = allocation.map((item, index) => `
    <div class="legend-row">
      <i class="legend-dot" style="background:${colors[index % colors.length]}"></i>
      <strong>${escapeHtml(item.symbol)}</strong><span>${number(item.allocation_pct, 1)}%</span>
    </div>`).join("");
}

function renderCandidateList(selector, ideas, shadow) {
  const candidates = ideas.filter(item => item.action === "candidate").slice(0, 6);
  document.querySelector(selector).innerHTML = candidates.length ? candidates.map(item => `
    <div class="idea-row" data-symbol="${escapeHtml(item.symbol)}">
      <div class="idea-row-top"><strong>${escapeHtml(item.symbol)}</strong><span class="score">${number(item.score, 1)}</span></div>
      <p>${shadow ? "Shadow context" : `${money(item.suggested_amount)} starter review`} · ${escapeHtml(item.risk_level)} · ${escapeHtml(String(item.signal_state || "steady").replaceAll("_", " "))}</p>
    </div>`).join("") : `<div class="empty">No current candidates.</div>`;
}

function renderMovers(items) {
  document.querySelector("#signal-movers").innerHTML = items.length ? items.map(item => `
    <div class="mover-row" data-symbol="${escapeHtml(item.symbol)}">
      <div><strong>${escapeHtml(item.symbol)}</strong><small>${escapeHtml(String(item.signal_state).replaceAll("_", " "))}</small></div>
      <div class="mover-track"><i class="${signedClass(item.score_delta)}" style="width:${Math.min(100, Math.abs(item.score_delta || 0) * 6)}%"></i></div>
      <strong class="${signedClass(item.score_delta)}">${pct(item.score_delta, 1)}</strong>
      <span>rank ${item.rank_delta == null ? "—" : `${item.rank_delta >= 0 ? "+" : ""}${item.rank_delta}`}</span>
    </div>`).join("") : `<div class="empty">A second comparable run is needed for movement analysis.</div>`;
}

function renderAgreement(agreement) {
  document.querySelector("#agreement-summary").textContent = agreement.sample_count
    ? `${agreement.aligned_count}/${agreement.sample_count} aligned`
    : "No overlapping coverage";
  document.querySelector("#source-agreement").innerHTML = agreement.rows.length ? agreement.rows.map(item => `
    <div class="agreement-row" data-symbol="${escapeHtml(item.symbol)}">
      <strong>${escapeHtml(item.symbol)}</strong>
      <span>Prod ${number(item.production_score, 1)}</span>
      <span>Shadow ${number(item.shadow_score, 1)}</span>
      <strong class="${Math.abs(item.score_gap) >= 10 ? "negative" : "positive"}">${item.score_gap >= 0 ? "+" : ""}${number(item.score_gap, 1)}</strong>
    </div>`).join("") : `<div class="empty">No common production and shadow symbols.</div>`;
}

function renderPortfolio() {
  const p = state.portfolio;
  const alerts = [];
  p.positions.filter(x => x.allocation_pct >= 20 && !x.concentration_exempt).forEach(x => alerts.push(`${x.symbol} is ${number(x.allocation_pct, 1)}% of portfolio value.`));
  if (p.freshness.status !== "healthy") alerts.push(`Portfolio data is ${p.freshness.label}; treat prices and actions as stale.`);
  if (p.summary.notification_status === "failed") alerts.push("Latest portfolio assessment was stored, but Telegram delivery failed.");
  const topFive = p.positions.slice().sort((a, b) => b.allocation_pct - a.allocation_pct).slice(0, 5)
    .reduce((sum, item) => sum + item.allocation_pct, 0);
  if (topFive >= 60) alerts.push(`Top five positions represent ${number(topFive, 1)}% of portfolio value.`);
  document.querySelector("#portfolio-alerts").innerHTML = alerts.map(x => `<div class="notice">${escapeHtml(x)}</div>`).join("");
  renderPortfolioTable();
  renderValueChart(p.history);
  renderActionHistory(p.history.slice(-10));
}

function renderPortfolioTable() {
  if (!state.portfolio) return;
  const search = document.querySelector("#portfolio-search").value.trim().toLowerCase();
  const action = document.querySelector("#portfolio-action").value;
  const sort = document.querySelector("#portfolio-sort").value;
  let rows = state.portfolio.positions.filter(item =>
    (!search || `${item.symbol} ${item.action} ${item.classification}`.toLowerCase().includes(search)) &&
    (!action || item.action === action)
  );
  rows.sort((a, b) => sort === "symbol" ? a.symbol.localeCompare(b.symbol)
    : sort === "pl" ? b.pl_pct - a.pl_pct
    : sort === "score" ? b.score - a.score
    : b.allocation_pct - a.allocation_pct);
  document.querySelector("#portfolio-table").innerHTML = rows.map(item => `
    <tr data-symbol="${escapeHtml(item.symbol)}">
      <td><strong>${escapeHtml(item.symbol)}</strong><br><small>${escapeHtml(item.classification)}</small></td>
      <td>${actionBadge(item.action)}</td><td>${number(item.quantity, 4).replace(/\.?0+$/, "")}</td>
      <td>${money(item.price)}</td><td>${money(item.value)}</td><td>${number(item.allocation_pct, 2)}%</td>
      <td>${money(item.average_cost)}</td><td class="${signedClass(item.pl_pct)}">${pct(item.pl_pct, 2)}</td>
      <td class="${signedClass(item.daily_return_pct)}">${pct(item.daily_return_pct, 2)}</td>
      <td class="${signedClass(item.return_5d_pct)}">${pct(item.return_5d_pct, 2)}</td>
      <td>${number(item.score, 1)}</td><td>${item.action_streak} run${item.action_streak === 1 ? "" : "s"}</td>
    </tr>`).join("");
}

function renderValueChart(history) {
  const target = document.querySelector("#value-chart");
  if (!history.length) { target.innerHTML = `<div class="empty">No history yet.</div>`; return; }
  const values = history.map(x => x.value);
  const min = Math.min(...values), max = Math.max(...values);
  const width = 600, height = 180, pad = 22;
  const x = i => pad + i * (width - pad * 2) / Math.max(1, values.length - 1);
  const y = v => height - pad - ((v - min) / Math.max(1, max - min)) * (height - pad * 2);
  const points = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const area = `${pad},${height - pad} ${points} ${x(values.length - 1)},${height - pad}`;
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Portfolio value history">
    <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#087a61" stop-opacity=".28"/><stop offset="1" stop-color="#087a61" stop-opacity=".02"/></linearGradient></defs>
    <line class="chart-axis" x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"/>
    <polygon class="chart-area" points="${area}"/><polyline class="chart-line" points="${points}"/>
    <text class="chart-label" x="${pad}" y="12">${money(max)}</text><text class="chart-label" x="${pad}" y="${height - 3}">${dateText(history[0].started_at)}</text>
    <text class="chart-label" text-anchor="end" x="${width - pad}" y="${height - 3}">${dateText(history.at(-1).started_at)}</text>
  </svg>`;
}

function renderActionHistory(history) {
  document.querySelector("#action-history").innerHTML = history.map(run => {
    const total = Object.values(run.actions).reduce((a, b) => a + b, 0) || 1;
    const bar = Object.entries(run.actions).map(([action, count]) =>
      `<i class="stack-segment" title="${escapeHtml(action)}: ${count}" style="width:${count / total * 100}%;background:${actionColors[action] || "#87938f"}"></i>`).join("");
    return `<div class="stack-row"><span>${dateText(run.started_at)}</span><div class="stack-bar">${bar}</div><strong>${total}</strong></div>`;
  }).join("");
}

function renderIdeas() {
  const source = document.querySelector("#idea-source").value;
  const data = state.ideas[source];
  if (!data) return;
  const search = document.querySelector("#idea-search").value.trim().toLowerCase();
  const sort = document.querySelector("#idea-sort").value;
  let ideas = data.ideas.filter(item => !search || `${item.symbol} ${item.setup} ${item.risk_level} ${item.reasons.join(" ")}`.toLowerCase().includes(search));
  ideas.sort((a, b) => sort === "symbol" ? a.symbol.localeCompare(b.symbol)
    : sort === "outcomes" ? b.outcome_sample_count - a.outcome_sample_count
    : sort === "movement" ? Math.abs(b.score_delta || 0) - Math.abs(a.score_delta || 0)
    : sort === "evidence" ? b.evidence_coverage - a.evidence_coverage
    : b.score - a.score);
  document.querySelector("#idea-source-note").innerHTML = `<span class="source-badge ${source === "shadow" ? "shadow" : ""}">${escapeHtml(data.source)}</span> · ${escapeHtml(data.provider || "No provider")} · ${escapeHtml(data.freshness.label)} · ${data.sample_count} names`;
  document.querySelector("#ideas-grid").innerHTML = ideas.length ? ideas.map(item => `
    <article class="idea-card" data-symbol="${escapeHtml(item.symbol)}">
      <div class="idea-card-head"><div><h3>${escapeHtml(item.symbol)}</h3><span>${money(item.price)}</span></div><span class="score">${number(item.score, 1)}</span></div>
      <div class="tag-row">${actionBadge(item.action)}${movementBadge(item)}<span class="source-badge ${source === "shadow" ? "shadow" : ""}">${escapeHtml(data.source)}</span></div>
      <div class="idea-stats">
        <div><span>Market score</span><strong>${number(item.market_score, 1)}</strong></div>
        <div><span>Catalyst</span><strong>${pct(item.catalyst_score, 1)}</strong></div>
        <div><span>Rank movement</span><strong>${item.rank_delta == null ? "New" : `${item.rank_delta >= 0 ? "+" : ""}${item.rank_delta}`}</strong></div>
        <div><span>Evidence coverage</span><strong>${item.evidence_coverage}/100</strong></div>
        <div><span>Calibration</span><strong>${escapeHtml(calibrationLabel(item.calibration))}</strong></div>
        <div><span>Measured wins</span><strong>${calibrationWin(item.calibration)}</strong></div>
      </div>
      <p class="idea-copy"><strong>Why:</strong> ${escapeHtml(item.reasons[0] || "No stored rationale.")}</p>
      <p class="idea-copy"><strong>Risk:</strong> ${escapeHtml(item.risks[0] || "No stored risk note.")}</p>
      ${item.new_reasons.length || item.new_risks.length ? `<p class="idea-copy fresh"><strong>New this run:</strong> ${escapeHtml(item.new_reasons[0] || item.new_risks[0])}</p>` : ""}
    </article>`).join("") : `<div class="empty">No ideas match this filter.</div>`;
}

function renderPerformance() {
  const dimension = document.querySelector("#performance-dimension").value;
  const rows = state.performance.summaries.filter(item => item.dimension === dimension)
    .sort((a, b) => a.horizon_days - b.horizon_days || a.label.localeCompare(b.label));
  document.querySelector("#performance-table").innerHTML = rows.map(item => `
    <tr><td><strong>${escapeHtml(item.label)}</strong></td><td>${item.horizon_days}d</td><td>${item.sample_count}</td>
    <td>${number(item.win_rate_pct, 1)}%</td><td class="${signedClass(item.average_return_pct)}">${pct(item.average_return_pct, 2)}</td>
    <td class="${signedClass(item.median_return_pct)}">${pct(item.median_return_pct, 2)}</td>
    <td class="${signedClass(item.average_relative_return_pct)}">${pct(item.average_relative_return_pct, 2)}</td>
    <td class="positive">${pct(item.average_favorable_pct, 2)}</td><td class="negative">${pct(item.average_adverse_pct, 2)}</td></tr>`).join("");
  document.querySelector("#provider-health").innerHTML = state.health.providers.map(item => `
    <div class="provider-row"><div><strong>${escapeHtml(item.provider)}</strong><br><small>${escapeHtml(item.role || "context")} · ${item.calls} calls · ${item.cache_hits} cached</small></div>
    <div><div class="progress"><i style="width:${item.success_rate_pct}%"></i></div><small>${escapeHtml(item.activation_state || "observed")}${item.plan_limited ? ` · ${item.plan_limited} plan-limited` : ""}</small></div><strong>${number(item.success_rate_pct, 1)}%</strong></div>`).join("");
  document.querySelector("#scheduler-health").innerHTML = state.health.launch_agents.map(item => `
    <div class="provider-row"><div><strong>${escapeHtml(item.label.replace("com.stock-analyzer.", ""))}</strong><br><small>${escapeHtml(item.state)}</small></div>
    <div></div><strong>${item.last_exit_code == null ? "—" : `Exit ${escapeHtml(item.last_exit_code)}`}</strong></div>`).join("");
  const gate = state.health.shadow_promotion || {};
  document.querySelector("#shadow-gate").innerHTML = `
    <div class="provider-row"><div><strong>${escapeHtml(String(gate.state || "unknown").replaceAll("_", " "))}</strong><br><small>Shadow context stays non-actionable until every criterion passes.</small></div>
    <div></div><strong>${gate.ready_for_manual_promotion ? "Ready" : "Blocked"}</strong></div>
    ${(gate.criteria || []).map(item => `
      <div class="provider-row"><div><strong>${escapeHtml(String(item.name).replaceAll("_", " "))}</strong><br><small>${escapeHtml(item.detail || "")}</small></div>
      <div></div><strong>${item.passed ? "Pass" : "Block"}</strong></div>`).join("")}
  `;
}

async function openStock(symbol) {
  const drawer = document.querySelector("#stock-drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  document.querySelector("#drawer-content").innerHTML = `<div class="empty">Loading ${escapeHtml(symbol)}…</div>`;
  try {
    const data = await api(`/api/stocks/${encodeURIComponent(symbol)}`);
    renderDrawer(data);
  } catch (error) { showError(error); closeDrawer(); }
}

function renderDrawer(data) {
  const p = data.position;
  const latestProd = data.scores.find(x => x.source === "Production SEC");
  const latestShadow = data.scores.find(x => x.source === "Shadow Context");
  const metrics = latestProd?.metrics || latestShadow?.metrics || {};
  const metricEntries = Object.entries(metrics).filter(([, value]) => typeof value === "number").slice(0, 12);
  document.querySelector("#drawer-content").innerHTML = `
    <div class="drawer-title"><p class="eyebrow">STOCK VIEW</p><h1 id="drawer-title">${escapeHtml(data.symbol)}</h1>
      <div class="tag-row">${p ? actionBadge(p.action) : ""}${latestProd ? '<span class="source-badge">Production SEC</span>' : ""}${latestShadow ? '<span class="source-badge shadow">Shadow context</span>' : ""}</div>
    </div>
    <section class="detail-section"><h2>Score trajectory</h2>${scoreTrend(data.scores)}</section>
    <section class="detail-section"><h2>Portfolio position</h2>${p ? `<div class="detail-grid">
      ${detailBox("Last refresh", dateText(p.started_at))}${detailBox("Quantity", number(p.quantity, 4).replace(/\.?0+$/, ""))}
      ${detailBox("Price", money(p.current_price))}${detailBox("Equity value", money(p.current_value))}
      ${detailBox("Average cost", money(p.average_cost))}${detailBox("P/L", pct(p.return_from_cost_pct, 2))}
      ${detailBox("Allocation", `${number(p.weight_pct, 2)}%`)}${detailBox("Score", number(p.score, 1))}
    </div><p><strong>Why:</strong> ${escapeHtml(p.reasons?.[0] || "No stored rationale.")}</p><p><strong>Risk:</strong> ${escapeHtml(p.risks?.[0] || "No stored risk note.")}</p>` : '<div class="empty">Not currently held.</div>'}</section>
    <section class="detail-section"><h2>Latest deterministic research</h2>${latestProd || latestShadow ? `
      <div class="detail-grid">${detailBox("Production score", number(latestProd?.score, 1))}${detailBox("Shadow score", number(latestShadow?.score, 1))}
      ${detailBox("Suggested review", latestProd ? money(latestProd.suggested_amount) : "—")}${detailBox("Risk level", metrics.risk_level || "Unavailable")}</div>
      <div class="tag-row">${latestProd ? movementBadge({
        signal_state: latestProd.metrics.signal_state,
        score_delta: latestProd.metrics.score_delta,
      }) : ""}</div>
      <p><strong>Thesis:</strong> ${escapeHtml((latestProd || latestShadow).reasons?.[0] || "Unavailable")}</p>
      <p><strong>Key risk:</strong> ${escapeHtml((latestProd || latestShadow).risks?.[0] || "Unavailable")}</p>
      ${latestProd?.metrics?.new_reasons?.length ? `<p class="fresh"><strong>New insight:</strong> ${escapeHtml(latestProd.metrics.new_reasons[0])}</p>` : ""}` : '<div class="empty">No stored scanner research.</div>'}</section>
    <section class="detail-section"><h2>Technical snapshot</h2><div class="detail-grid">${metricEntries.length ? metricEntries.map(([key, value]) => detailBox(key.replaceAll("_", " "), number(value, 2))).join("") : detailBox("Metrics", "Unavailable")}</div></section>
    <section class="detail-section"><h2>Measured outcomes</h2>${data.outcomes.length ? `<div class="detail-grid">${data.outcomes.map(x =>
      detailBox(`${x.horizon_days} day · ${x.samples} samples`, `${pct(x.average_return_pct, 2)} avg · ${number(x.win_rate_pct, 1)}% wins`)).join("")}</div>` : '<div class="empty">No matured outcomes yet.</div>'}</section>
    <section class="detail-section"><h2>Evidence dossier</h2>${renderDossier(data.dossier || {})}</section>
    <section class="detail-section"><h2>Action history</h2>${data.action_history.length ? data.action_history.slice(0, 12).map(x =>
      `<div class="evidence-item"><strong>${dateText(x.started_at)}</strong> · ${actionBadge(x.action)} · score ${number(x.score, 1)}</div>`).join("") : '<div class="empty">No portfolio action history.</div>'}</section>`;
}

function scoreTrend(scores) {
  const production = scores.filter(x => x.source === "Production SEC").slice(0, 12).reverse();
  if (production.length < 2) return `<div class="empty">A second production appearance is needed.</div>`;
  const width = 600, height = 150, pad = 22;
  const values = production.map(x => Number(x.score));
  const min = Math.min(...values) - 3, max = Math.max(...values) + 3;
  const x = i => pad + i * (width - pad * 2) / Math.max(1, values.length - 1);
  const y = value => height - pad - ((value - min) / Math.max(1, max - min)) * (height - pad * 2);
  const points = values.map((value, i) => `${x(i)},${y(value)}`).join(" ");
  return `<div class="score-trend"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Production score trajectory">
    <line class="threshold-line" x1="${pad}" x2="${width - pad}" y1="${y(78)}" y2="${y(78)}"></line>
    <polyline class="chart-line" points="${points}"></polyline>
    ${values.map((value, i) => `<circle cx="${x(i)}" cy="${y(value)}" r="4"></circle>`).join("")}
    <text class="chart-label" x="${pad}" y="12">${number(max - 3, 1)}</text>
    <text class="chart-label" x="${pad}" y="${height - 4}">${dateText(production[0].started_at)}</text>
    <text class="chart-label" text-anchor="end" x="${width - pad}" y="${height - 4}">${dateText(production.at(-1).started_at)}</text>
  </svg></div>`;
}

function detailBox(label, value) {
  return `<div class="detail-box"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}
function renderDossier(dossier) {
  const fundamentals = dossier.fundamentals?.items || [];
  const missingFundamentals = dossier.fundamentals?.unavailable || [];
  const filingCatalysts = dossier.filing_catalysts?.items || [];
  const scoreEvidence = dossier.score_evidence?.items || [];
  return `
    <p class="panel-note">${escapeHtml(dossier.methodology || "Evidence-only dossier from stored sources.")}</p>
    <h3>Fundamentals</h3>
    ${fundamentals.length ? `<div class="detail-grid">${fundamentals.map(item =>
      detailBox(item.label, `${formatDossierValue(item.value)} · ${item.provider}`)
    ).join("")}</div>` : '<div class="empty">No sourced fundamental snapshot available.</div>'}
    ${missingFundamentals.length ? `<p class="panel-note">Not yet sourced: ${escapeHtml(missingFundamentals.slice(0, 6).join(", "))}${missingFundamentals.length > 6 ? "…" : ""}</p>` : ""}
    <h3>Filing-backed catalysts</h3>
    ${filingCatalysts.length ? filingCatalysts.map(event => `
      <div class="evidence-item"><strong>${escapeHtml(event.category)} · ${escapeHtml(event.provider)}</strong>
      <p>${escapeHtml(event.headline)}</p><small>${dateText(event.published_at)} · ${escapeHtml(event.source)}</small>
      ${safeUrl(event.url) ? `<br><a href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer noopener">Open source</a>` : ""}</div>`).join("") : '<div class="empty">No filing-backed catalyst events stored.</div>'}
    <h3>Score evidence</h3>
    ${scoreEvidence.length ? scoreEvidence.map(item => `
      <div class="evidence-item"><strong>${escapeHtml(item.category)} · ${escapeHtml(item.provider)} · ${signed(item.score_delta, 2)}</strong>
      <p>${escapeHtml(item.summary)}</p><small>${escapeHtml(item.source || "stored contribution")}</small></div>`).join("") : '<div class="empty">No scored evidence contributions stored.</div>'}
  `;
}
function formatDossierValue(value) {
  if (typeof value === "number") {
    if (Math.abs(value) >= 1000000000) return `$${number(value / 1000000000, 2)}B`;
    if (Math.abs(value) >= 1000000) return `$${number(value / 1000000, 2)}M`;
    return number(value, 2);
  }
  return String(value ?? "Unavailable");
}
function safeUrl(value) {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol); } catch { return false; }
}
function closeDrawer() {
  const drawer = document.querySelector("#stock-drawer");
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

document.addEventListener("click", event => {
  const tab = event.target.closest(".tab");
  if (tab) {
    document.querySelectorAll(".tab,.view").forEach(x => x.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`#view-${tab.dataset.view}`).classList.add("active");
  }
  const stock = event.target.closest("[data-symbol]");
  if (stock) openStock(stock.dataset.symbol);
  if (event.target.closest("[data-close-drawer]")) closeDrawer();
});
document.addEventListener("keydown", event => { if (event.key === "Escape") closeDrawer(); });
["portfolio-search", "portfolio-action", "portfolio-sort"].forEach(id => document.querySelector(`#${id}`).addEventListener("input", renderPortfolioTable));
["idea-search", "idea-source", "idea-sort"].forEach(id => document.querySelector(`#${id}`).addEventListener("input", renderIdeas));
document.querySelector("#performance-dimension").addEventListener("input", renderPerformance);

loadAll();
