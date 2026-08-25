let snapshot = null;
let revision = null;
let filter = "all";
let failures = 0;
let previousRisk = 0;

const board = document.querySelector("#board");
const dialog = document.querySelector("#order-dialog");

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function metric(id, value) {
  document.querySelector(id).textContent = value.toLocaleString();
}

function renderMetrics(data) {
  metric("#metric-created", data.metrics.created);
  metric("#metric-flight", data.metrics.in_flight);
  metric("#metric-completed", data.metrics.completed);
  metric("#metric-failed", data.metrics.failed);
  metric("#metric-risk", data.metrics.charged_no_ticket);
  metric("#metric-dedupe", data.metrics.duplicate_charges_prevented);
  if (data.metrics.charged_no_ticket > previousRisk) {
    const card = document.querySelector(".kpi-risk");
    card.classList.remove("flash");
    requestAnimationFrame(() => card.classList.add("flash"));
  }
  previousRisk = data.metrics.charged_no_ticket;
}

function renderHeader(data) {
  const badge = document.querySelector("#engine-badge");
  badge.textContent = data.run.engine === "temporal" ? "TEMPORAL" : "NAÏVE";
  badge.classList.toggle("temporal", data.run.engine === "temporal");
  document.querySelector("#run-number").textContent = `Run ${data.run.run_number}`;
  const previous = document.querySelector("#previous-run");
  if (data.previous_summary) {
    const item = data.previous_summary;
    previous.hidden = false;
    previous.textContent = `Previous ${item.engine.toUpperCase()} run · ${item.created} created · ${item.completed} completed · ${item.failed} failed · ${item.charged_no_ticket} charged without a ticket`;
  } else {
    previous.hidden = true;
  }
}

const healthLabels = {
  processing: "Processing",
  retrying: "Retrying",
  worker_unavailable: "Worker offline",
  stranded: "Stranded",
  failed: "Failed",
  complete: "Complete",
};

function renderCard(order) {
  const card = document.createElement("article");
  card.className = `order-card ${order.health} ${order.source}`;
  card.dataset.orderId = order.id;
  card.tabIndex = 0;
  card.title = `${order.id} · ${order.supporter_alias} · ${healthLabels[order.health]}`;
  card.innerHTML = `<div class="card-top"><span class="source-icon">${order.source === "audience" ? "A" : "S"}</span><h3>${escapeHtml(order.supporter_alias)}</h3></div><p class="order-ref">${order.id}</p><div class="card-detail"><span>${escapeHtml(order.section)}</span><span class="health-label">${healthLabels[order.health]}</span></div>`;
  card.addEventListener("click", () => openOrder(order.id));
  card.addEventListener("keydown", (event) => { if (event.key === "Enter") openOrder(order.id); });
  return card;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function renderBoard(data) {
  const search = document.querySelector("#order-search").value.trim().toLowerCase();
  const visible = data.orders.filter((order) => {
    if (filter !== "all" && order.source !== filter) return false;
    if (search && !`${order.id} ${order.supporter_alias}`.toLowerCase().includes(search)) return false;
    return true;
  });
  const dense = visible.length > 50 && filter !== "audience";
  board.classList.toggle("dense", dense);
  document.querySelector("#density-label").textContent = dense ? "Dense tiles" : "Cards";
  document.querySelectorAll(".board-column").forEach((column) => {
    column.querySelector(".card-stack").replaceChildren();
    column.querySelector("h2 span").textContent = "0";
  });
  const counts = {};
  for (const order of visible) {
    const columnName = order.health === "failed" ? "failed" : order.milestone;
    const column = document.querySelector(`[data-column="${columnName}"]`);
    if (!column) continue;
    column.querySelector(".card-stack").append(renderCard(order));
    counts[columnName] = (counts[columnName] || 0) + 1;
  }
  for (const [name, count] of Object.entries(counts)) {
    document.querySelector(`[data-column="${name}"] h2 span`).textContent = count;
  }
}

function renderControls(data) {
  const health = document.querySelector("#worker-health");
  const naive = data.workers["naive-worker"]?.online || false;
  const temporal = data.workers["temporal-worker"]?.online || false;
  health.innerHTML = `<div class="health-row"><span>Web + ledger</span><span class="online"><i></i>Online</span></div><div class="health-row"><span>Naïve worker</span><span class="${naive ? "online" : ""}"><i></i>${naive ? "Online" : "Offline"}</span></div><div class="health-row"><span>Temporal worker</span><span class="${temporal ? "online" : ""}"><i></i>${temporal ? "Online" : "Offline"}</span></div>`;
  document.querySelector("#generator-state").textContent = data.generator.running
    ? `Running · ${data.generator.submitted} / ${data.generator.target_count} submitted`
    : `Paused · ${data.generator.submitted} submitted`;
  const crash = data.crash_token;
  const armed = crash && !crash.consumed_at;
  document.querySelector(".crash-control").classList.toggle("armed", Boolean(armed));
  document.querySelector("#crash-state").textContent = armed
    ? "ARMED · waiting for the next audience payment"
    : crash?.consumed_order_id
      ? `Consumed by ${crash.consumed_order_id}`
      : "Not armed";
  document.querySelector("#join-url").textContent = data.join_url;
  document.querySelector("#join-qr").src = `/api/admin/qr?revision=${data.revision}`;
  const faults = data.faults;
  setRange("reservation-failure", faults.reservation_failure_pct, "%");
  setRange("payment-failure", faults.payment_failure_pct, "%");
  setRange("ticket-failure", faults.ticket_failure_pct, "%");
  setRange("latency", faults.latency_ms, "ms");
}

function setRange(id, value, suffix) {
  const input = document.querySelector(`#${id}`);
  if (document.activeElement !== input) input.value = value;
  const outputId = id === "latency" ? "latency-output" : id.replace("failure", "output");
  document.querySelector(`#${outputId}`).value = `${input.value}${suffix}`;
}

async function poll() {
  try {
    const suffix = revision === null ? "" : `?revision=${revision}`;
    const response = await fetch(`/api/admin/dashboard${suffix}`);
    if (response.status === 204) return;
    if (!response.ok) throw new Error(response.statusText);
    snapshot = await response.json();
    revision = snapshot.revision;
    failures = 0;
    document.querySelector("#connection-state").textContent = "Live";
    renderHeader(snapshot);
    renderMetrics(snapshot);
    renderBoard(snapshot);
    renderControls(snapshot);
  } catch (_) {
    failures += 1;
    document.querySelector("#connection-state").textContent = failures > 6 ? "Data may be stale" : "Reconnecting…";
  }
}

async function openOrder(id) {
  const order = await api(`/api/orders/${encodeURIComponent(id)}`);
  const temporal = order.temporal_ui_url ? `<p><a href="${order.temporal_ui_url}" target="_blank">Open this Workflow in Temporal ↗</a></p>` : "";
  document.querySelector("#dialog-content").innerHTML = `<div class="dialog-order-header"><h2>${order.id}</h2><p>${escapeHtml(order.supporter_alias)} · ${escapeHtml(order.section)} · ${order.engine.toUpperCase()}</p>${temporal}</div><ol class="timeline">${order.events.map((event) => `<li><strong>${escapeHtml(event.message)}</strong><span>${event.step}${event.attempt ? ` · attempt ${event.attempt}` : ""}</span></li>`).join("")}</ol>`;
  dialog.showModal();
}

document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".filter").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  filter = button.dataset.filter;
  if (snapshot) renderBoard(snapshot);
}));
document.querySelector("#order-search").addEventListener("input", () => { if (snapshot) renderBoard(snapshot); });
document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", async () => { await api(`/api/admin/presets/${button.dataset.preset}`, { method: "POST" }); revision = null; poll(); }));
document.querySelector("#generator-start").addEventListener("click", async () => { await api("/api/admin/generator/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rate_per_second: Number(document.querySelector("#generator-rate").value), target_count: Number(document.querySelector("#generator-target").value) }) }); revision = null; poll(); });
document.querySelector("#generator-pause").addEventListener("click", async () => { await api("/api/admin/generator/pause", { method: "POST" }); revision = null; poll(); });
document.querySelector("#arm-crash").addEventListener("click", async () => { await api("/api/admin/crash-token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_source: "audience" }) }); revision = null; poll(); });
document.querySelector("#fresh-run").addEventListener("click", async () => { if (!confirm("Start a fresh run? Active Temporal Workflows will be terminated; history is preserved.")) return; await api("/api/admin/runs/fresh", { method: "POST" }); revision = null; poll(); });
document.querySelector("#apply-faults").addEventListener("click", async () => { await api("/api/admin/faults", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reservation_failure_pct: Number(document.querySelector("#reservation-failure").value), payment_failure_pct: Number(document.querySelector("#payment-failure").value), ticket_failure_pct: Number(document.querySelector("#ticket-failure").value), card_decline_pct: 0, latency_ms: Number(document.querySelector("#latency").value) }) }); revision = null; poll(); });
document.querySelectorAll(".slider-grid input").forEach((input) => input.addEventListener("input", () => { const suffix = input.id === "latency" ? "ms" : "%"; const outputId = input.id === "latency" ? "latency-output" : input.id.replace("failure", "output"); document.querySelector(`#${outputId}`).value = `${input.value}${suffix}`; }));
document.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });

poll();
setInterval(poll, 500);
