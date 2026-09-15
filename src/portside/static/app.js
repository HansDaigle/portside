const $ = (selector) => document.querySelector(selector);
const editor = $("#editor");
const form = $("#connection-form");
let state = { proxies: [], token: "", caddy_available: false };
let filter = "all";
let editing = null;
let deleting = null;
let viewing = null;
let signature = "";
let toastTimer;
let refreshInFlight = false;
const pending = new Set();
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const icons = {
  edit: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m15 5 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15z"/></svg>',
  delete:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/></svg>',
};

function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 3200);
}

async function api(path, method, body = {}) {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Portside-Token": state.token,
    },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "The request could not be completed.");
  return result;
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const response = await fetch("/api/state");
    if (!response.ok)
      throw new Error("The dashboard could not load its configuration.");
    state = await response.json();
    $("#banner").hidden = state.caddy_available;
    $("#banner").textContent = state.caddy_available
      ? ""
      : "Caddy is not installed. Run brew install caddy, then restart Portside.";
    $("#engine-status").textContent = state.caddy_available
      ? "Caddy ready · WebSockets supported"
      : "Caddy missing";
    $("#engine-status").classList.toggle("ready", state.caddy_available);
    $("#version").textContent = `v${state.version}`;
    $("#running-count").textContent = state.proxies.filter(
      (p) => p.status === "running",
    ).length;
    $("#saved-count").textContent = state.proxies.length;
    $("#nav-count").textContent = state.proxies.length;
    render();
  } catch (error) {
    $("#banner").hidden = false;
    $("#banner").textContent =
      "Portside is disconnected. Check the terminal running the dashboard.";
    $("#engine-status").textContent = "Disconnected";
    $("#engine-status").classList.remove("ready");
  } finally {
    refreshInFlight = false;
  }
}

function render(force = false) {
  const query = $("#search").value.toLowerCase();
  const rows = state.proxies.filter(
    (p) =>
      (filter === "all" ||
        (filter === "stopped"
          ? ["stopped", "error"].includes(p.status)
          : p.status === filter)) &&
      `${p.name} ${p.local_url} ${p.upstream}`.toLowerCase().includes(query),
  );
  const nextSignature = JSON.stringify([
    rows.map(({ metrics, events, ...row }) => row),
    [...pending],
    state.proxies.length,
    query,
    filter,
  ]);
  if (!force && signature === nextSignature) {
    renderTelemetry();
    return;
  }
  signature = nextSignature;
  const focus = document.activeElement?.closest("[data-action]");
  const focusedId = focus?.dataset.id;
  const focusedAction = focus?.dataset.action;
  if (!rows.length) {
    $("#connection-list").innerHTML = state.proxies.length
      ? '<div class="empty"><h2>No matching connections.</h2><p>Try another search or filter.</p></div>'
      : '<div class="empty"><div class="empty-art" aria-hidden="true"><div class="endpoint-box">:↗</div><div class="connection-line"></div><div class="endpoint-box remote">◎</div></div><h2>A place for your first connection.</h2><p>Pick a local port and a destination. Portside takes care of the journey between them.</p><button class="primary" data-action="new">Create a connection <span aria-hidden="true">＋</span></button><button class="text-button" data-action="example">Try it with example.com</button></div>';
    return;
  }
  $("#connection-list").innerHTML = rows
    .map((p) => {
      const busy =
        pending.has(p.id) || ["starting", "stopping"].includes(p.status);
      const running = p.status === "running";
      const e = escapeHtml;
      return `<article class="connection-row" data-connection-id="${e(p.id)}"><div class="row-heading"><h3>${e(p.name)}</h3><span class="status ${e(p.status)}">${e(p.status[0].toUpperCase() + p.status.slice(1))}</span></div><div class="row-main"><div class="route"><a href="${e(p.local_url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${e(p.name)} at ${e(p.local_url)}">${e(p.local_url)} ↗</a><span class="route-arrow" aria-hidden="true">→</span><code>${e(p.upstream)}</code></div><div class="row-actions"><button class="${running ? "secondary" : "primary"}" data-action="${running ? "stop" : "start"}" data-id="${e(p.id)}" ${busy || (!running && !state.caddy_available) ? "disabled" : ""}>${busy ? "Working…" : running ? "Stop" : "Start"}</button><button class="icon-button" data-action="edit" data-id="${e(p.id)}" aria-label="Edit ${e(p.name)}" title="${running ? "Stop before editing" : "Edit connection"}" ${busy || running ? "disabled" : ""}>${icons.edit}</button><button class="icon-button" data-action="delete" data-id="${e(p.id)}" aria-label="Delete ${e(p.name)}" title="${running ? "Stop before deleting" : "Delete connection"}" ${busy || running ? "disabled" : ""}>${icons.delete}</button></div></div>${p.error ? `<p class="row-error">${e(p.error)}</p>` : ""}<div class="connection-metrics" data-metrics-id="${e(p.id)}"></div><div class="row-foot"><span class="capabilities">${p.https ? "LOCAL HTTPS" : "LOCAL HTTP"} · WEBSOCKETS${p.rewrite_cookies ? " · COOKIES" : ""}${p.rewrite_origin ? " · ORIGIN" : ""}</span><button class="text-button" data-action="details" data-id="${e(p.id)}">Activity & details</button></div></article>`;
    })
    .join("");
  renderTelemetry();
  if (focusedId)
    [...document.querySelectorAll("[data-action]")]
      .find(
        (el) =>
          el.dataset.id === focusedId && el.dataset.action === focusedAction,
      )
      ?.focus();
}

function formatDuration(value) {
  if (value == null) return "—";
  return value >= 1000
    ? `${(value / 1000).toFixed(1)} s`
    : `${Math.round(value)} ms`;
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function graph(samples, key, label) {
  const values = samples.map((sample) => sample[key]);
  const peak = Math.max(1, ...values.filter((value) => value != null));
  const x = (index) => 4 + index * (292 / Math.max(1, values.length - 1));
  const y = (value) => 56 - Math.min(value / peak, 1) * 46;
  let marks = "";
  if (key === "requests") {
    marks = values
      .map((value, index) =>
        value
          ? `<rect x="${x(index) - 1.6}" y="${y(value)}" width="3.2" height="${56 - y(value)}" rx="1"/>`
          : "",
      )
      .join("");
  } else {
    let path = "",
      previous = false;
    values.forEach((value, index) => {
      if (value == null) {
        previous = false;
        return;
      }
      path += `${previous ? "L" : "M"}${x(index)},${y(value)} `;
      previous = true;
      marks += `<circle cx="${x(index)}" cy="${y(value)}" r="1.8"/>`;
    });
    marks += `<path d="${path}" fill="none" stroke="currentColor" stroke-width="1.7"/>`;
  }
  const axis = key === "requests" ? `${peak}/s` : formatDuration(peak);
  return `<svg viewBox="0 0 300 62" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title><path class="graph-grid" d="M0 10H300 M0 33H300 M0 56H300"/>${marks}</svg><span class="chart-scale">${axis}</span>`;
}

function telemetryMarkup(metrics) {
  if (!metrics)
    return '<p class="field-help">Restart Portside to enable live activity.</p>';
  const count = metrics.samples.reduce(
    (total, sample) => total + sample.requests,
    0,
  );
  const weightedDuration = metrics.samples.reduce(
    (total, sample) => total + (sample.duration_ms || 0) * sample.requests,
    0,
  );
  const avg = count ? weightedDuration / count : null;
  return `<div class="traffic-chart"><div class="chart-heading"><span>Requests <small>/ last 60s</small></span><strong>${count}</strong></div><div class="plot requests-plot">${graph(metrics.samples, "requests", `${count} completed requests in the last 60 seconds`)}</div><div class="chart-axis"><span>60s ago</span><span>Now</span></div></div><div class="traffic-chart"><div class="chart-heading"><span>Avg. duration</span><strong>${formatDuration(avg)}</strong></div><div class="plot duration-plot">${graph(metrics.samples, "duration_ms", `Average completed request duration over the last 60 seconds: ${formatDuration(avg)}`)}</div><div class="chart-axis"><span>${count ? "Full request duration" : "Waiting for completed requests"}</span><span>Now</span></div></div>`;
}

function renderTelemetry() {
  document.querySelectorAll("[data-metrics-id]").forEach((container) => {
    const mapping = state.proxies.find(
      (p) => p.id === container.dataset.metricsId,
    );
    if (mapping) container.innerHTML = telemetryMarkup(mapping.metrics);
  });
  if (!viewing || !$("#details-dialog").open) return;
  const mapping = state.proxies.find((p) => p.id === viewing);
  if (!mapping) {
    $("#details-dialog").close();
    return;
  }
  const metrics = mapping.metrics;
  $("#details-title").textContent = mapping.name;
  $("#details-content").innerHTML =
    `<p class="details-route"><code>${escapeHtml(mapping.local_url)}</code><span aria-hidden="true"> → </span><code>${escapeHtml(mapping.upstream)}</code></p><div class="details-totals"><div><span>Requests</span><strong>${metrics?.requests ?? 0}</strong></div><div><span>HTTP errors <small>4xx / 5xx</small></span><strong>${metrics?.errors ?? 0}</strong></div><div><span>Response data</span><strong>${formatBytes(metrics?.response_bytes ?? 0)}</strong></div></div><p class="metrics-caption">Since this connection started</p><div class="connection-metrics">${telemetryMarkup(metrics)}</div>`;
  const events = mapping.events;
  const eventSignature = JSON.stringify(events);
  if ($("#details-events").dataset.signature !== eventSignature) {
    $("#details-events").dataset.signature = eventSignature;
    $("#details-events").innerHTML = events.length
      ? events
          .slice()
          .reverse()
          .map(
            (event) =>
              `<div class="event"><time>${escapeHtml(new Date(event.time).toLocaleTimeString())}</time>${escapeHtml(event.message)}</div>`,
          )
          .join("")
      : '<p class="field-help">No events yet. Start the connection to begin.</p>';
  }
}

function preview() {
  $("#preview-local").textContent =
    `${form.elements.https.checked ? "https://" : ""}${form.elements.local_host.value || "localhost"}:${form.elements.port.value || "4444"}`;
  $("#preview-remote").textContent =
    form.elements.upstream.value || "your destination";
}

function openEditor(mapping = null, example = false) {
  editing = mapping?.id || null;
  form.reset();
  $("#form-error").hidden = true;
  $("#editor-title").textContent = mapping
    ? "Edit connection"
    : "New connection";
  $("#save-button").textContent = "Save connection";
  $("#save-button").disabled = false;
  form.querySelector("details").open = false;
  for (const key of ["name", "port", "upstream", "local_host"]) {
    if (mapping) form.elements[key].value = mapping[key];
  }
  for (const key of ["https", "rewrite_cookies", "rewrite_origin"])
    form.elements[key].checked = mapping?.[key] || false;
  if (example) {
    form.elements.name.value = "Example website";
    form.elements.upstream.value = "https://example.com";
  }
  if (!mapping) {
    let port = 4444;
    while (state.proxies.some((p) => p.port === port)) port++;
    form.elements.port.value = port;
  }
  preview();
  editor.showModal();
  form.elements.name.focus();
}

function showInfo(title, html) {
  $("#info-title").textContent = title;
  $("#info-content").innerHTML = html;
  $("#info-dialog").showModal();
}

$("#connections-button").addEventListener("click", () => {
  $("#main").scrollIntoView({ behavior: "instant" });
});
$("#details-dialog").addEventListener("close", () => {
  viewing = null;
});
$("#add-button").addEventListener("click", () => openEditor());
form.addEventListener("input", preview);
document
  .querySelectorAll(".close-dialog")
  .forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog").close()),
  );
$("#search").addEventListener("input", () => render());
document.querySelectorAll("[data-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((el) => el.setAttribute("aria-pressed", String(el === button)));
    render();
  }),
);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = {
    name: form.elements.name.value,
    port: Number(form.elements.port.value),
    upstream: form.elements.upstream.value.trim(),
    local_host: form.elements.local_host.value.trim(),
  };
  for (const key of ["https", "rewrite_cookies", "rewrite_origin"])
    data[key] = form.elements[key].checked;
  $("#save-button").disabled = true;
  $("#save-button").textContent = "Saving…";
  $("#form-error").hidden = true;
  try {
    await api(
      editing ? `/api/proxies/${editing}` : "/api/proxies",
      editing ? "PUT" : "POST",
      data,
    );
    editor.close();
    await refresh();
    toast(editing ? "Connection updated" : "Connection saved · ready to start");
  } catch (error) {
    $("#form-error").textContent = error.message;
    $("#form-error").hidden = false;
  } finally {
    $("#save-button").disabled = false;
    $("#save-button").textContent = "Save connection";
  }
});

$("#connection-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === "new" || action === "example")
    return openEditor(null, action === "example");
  const mapping = state.proxies.find((p) => p.id === button.dataset.id);
  if (!mapping) return;
  if (action === "edit") return openEditor(mapping);
  if (action === "details") {
    viewing = mapping.id;
    $("#details-title").textContent = mapping.name;
    $("#details-dialog").showModal();
    renderTelemetry();
    return;
  }
  if (action === "delete") {
    deleting = mapping.id;
    $("#delete-description").textContent =
      `Remove “${mapping.name}” from your saved connections?`;
    $("#delete-error").hidden = true;
    $("#delete-dialog").showModal();
    return;
  }
  pending.add(mapping.id);
  render(true);
  try {
    await api(`/api/proxies/${mapping.id}/${action}`, "POST");
    toast(
      action === "start" ? "Local connection is running" : "Connection stopped",
    );
  } catch (error) {
    toast(error.message);
  } finally {
    pending.delete(mapping.id);
    await refresh();
  }
});

$("#confirm-delete").addEventListener("click", async () => {
  $("#confirm-delete").disabled = true;
  try {
    await api(`/api/proxies/${deleting}`, "DELETE");
    $("#delete-dialog").close();
    await refresh();
    toast("Connection deleted");
  } catch (error) {
    $("#delete-error").textContent = error.message;
    $("#delete-error").hidden = false;
  } finally {
    $("#confirm-delete").disabled = false;
  }
});

$("#config-button").addEventListener("click", () =>
  showInfo(
    "Your saved connections",
    `<p>The dashboard and CLI use the same TOML format. Stop this dashboard before editing this file externally.</p><code>${escapeHtml(state.config_path || "Loading…")}</code><p>Save a new connection to create the file. Its contents stay on your machine.</p>`,
  ),
);
$("#help-button").addEventListener("click", () =>
  showInfo(
    "A shorter way there.",
    "<ol><li><strong>Create a connection.</strong> Pick a name, local port, and HTTP or HTTPS destination.</li><li><strong>Start it.</strong> Open the local URL to use your destination through Portside.</li><li><strong>Stop when finished.</strong> Your mapping stays saved for next time.</li></ol><p>Prefer the terminal?</p><code>portside --port 4444 --to https://example.com</code><p>For browser logins, consider local HTTPS and a unique .localhost hostname. Trust the local certificate explicitly. Some sites also need cookie/origin translation or callback configuration; arbitrary login flows are not guaranteed.</p><p>Ctrl+C in the dashboard terminal stops its running connections. WebSockets are supported automatically.</p>",
  ),
);
refresh();
setInterval(() => {
  if (!document.hidden) refresh();
}, 2000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
