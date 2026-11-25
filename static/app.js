const PRESETS = {
  xmas: { start: "2026-12-18", end: "2027-01-04", min: 6, max: 9 },
  before: { start: "2026-12-20", end: "2027-01-02", min: 6, max: 9 },
  early: { start: "2026-12-14", end: "2026-12-28", min: 6, max: 9 },
  after: { start: "2026-12-26", end: "2027-01-06", min: 5, max: 8 },
};

const state = {
  scanning: false,
  sortBy: "recommended",
  airline: "ALL",
  selectedPair: null, // "origin|dest|dep|ret" or null
  flights: [],
  matrix: [],
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shortDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
}

function dayAndDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-CA", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function fmtDur(mins) {
  const h = Math.floor((mins || 0) / 60);
  const m = (mins || 0) % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

function fmtCAD(n) {
  return `CA$${Number(n).toLocaleString("en-CA")}`;
}

/* ---------- Live Search ---------- */
async function runSearch(forceRefresh = false) {
  if (state.scanning) return;
  state.scanning = true;
  state.selectedPair = null;
  state.airline = "ALL";
  state.matrix = [];

  $("search-btn").disabled = true;
  $("search-btn").textContent = "Searching…";
  $("progress-wrap").classList.remove("hidden");
  $("progress-fill").style.width = "6%";
  $("progress-text").textContent = "Checking live Google Flights prices…";

  const minN = Math.max(2, parseInt($("min-nights").value, 10) || 6);
  const maxN = Math.max(minN, parseInt($("max-nights").value, 10) || 9);

  const payload = {
    origin: "YOW",
    destination: "YVR",
    window_start: $("window-start").value,
    window_end: $("window-end").value,
    min_nights: minN,
    max_nights: maxN,
    nonstop_only: $("nonstop-toggle").checked,
    include_carry_on: $("carry-on-toggle").checked,
    include_nearby_airports: $("nearby-toggle").checked,
    max_pairs: 10,
    force_refresh: forceRefresh,
  };

  try {
    const resp = await fetch("/api/search/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const evt = JSON.parse(line.slice(5).trim());
        onStreamEvent(evt);
      }
    }
  } catch (err) {
    $("progress-text").textContent = `Could not load prices: ${err.message}`;
  } finally {
    state.scanning = false;
    $("search-btn").disabled = false;
    $("search-btn").textContent = "Search";
  }
}

function onStreamEvent(evt) {
  if (evt.type === "start") {
    $("progress-text").textContent = `Checking ${evt.total} date pairs…`;
    return;
  }

  if (evt.type === "progress") {
    const pct = Math.max(8, Math.round((evt.completed / evt.total) * 100));
    $("progress-fill").style.width = `${pct}%`;
    $("progress-text").textContent = `Checked ${evt.completed} of ${evt.total} date pairs`;
    if (evt.cell) {
      state.matrix.push(evt.cell);
      $("dates-section").classList.remove("hidden");
      renderDateGrid();
    }
    return;
  }

  if (evt.type === "complete") {
    state.matrix = evt.matrix || [];
    state.flights = evt.flights || [];
    setTimeout(() => $("progress-wrap").classList.add("hidden"), 300);

    $("dates-section").classList.remove("hidden");
    $("results-section").classList.remove("hidden");
    renderDateGrid();
    populateAirlines();
    renderFlights();
  }
}

function renderDateGrid() {
  const grid = $("date-grid");
  const cells = state.matrix.filter((c) => c.cheapest_cad);
  if (!cells.length) {
    grid.innerHTML = `<div class="empty-state">No prices returned for this date range.</div>`;
    return;
  }

  const minPrice = Math.min(...cells.map((c) => c.cheapest_cad));
  $("clear-date-filter").classList.toggle("hidden", !state.selectedPair);

  grid.innerHTML = cells
    .map((c) => {
      const key = `${c.origin}|${c.destination}|${c.depart_date}|${c.return_date}`;
      const isLowest = c.cheapest_cad === minPrice;
      const isSelected = state.selectedPair === key;
      const routeNote = c.origin !== "YOW" || c.destination !== "YVR" ? ` · ${c.origin}–${c.destination}` : "";
      const nsNote = c.cheapest_nonstop_cad ? ` · Nonstop ${fmtCAD(c.cheapest_nonstop_cad)}` : "";
      return `
        <button type="button" class="date-cell ${isLowest ? "lowest" : ""} ${isSelected ? "selected" : ""}" data-key="${esc(key)}">
          <span class="date-cell-dates">${shortDate(c.depart_date)} – ${shortDate(c.return_date)}</span>
          <span class="date-cell-price">${fmtCAD(c.cheapest_cad)}</span>
          <span class="date-cell-sub">${c.nights} nights${esc(routeNote)}${nsNote}</span>
        </button>
      `;
    })
    .join("");

  grid.querySelectorAll(".date-cell").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.key;
      state.selectedPair = state.selectedPair === k ? null : k;
      renderDateGrid();
      renderFlights();
    });
  });
}

function populateAirlines() {
  const select = $("airline-select");
  const counts = {};
  state.flights.forEach((f) => {
    counts[f.primary_airline] = (counts[f.primary_airline] || 0) + 1;
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  select.innerHTML = [
    `<option value="ALL">All airlines</option>`,
    ...sorted.map(([name, cnt]) => `<option value="${esc(name)}">${esc(name)} (${cnt})</option>`),
  ].join("");
  select.value = state.airline;
}

function renderFlights() {
  const list = $("flight-list");
  let rows = [...state.flights];

  if (state.selectedPair) {
    const [orig, dest, dep, ret] = state.selectedPair.split("|");
    rows = rows.filter(
      (f) => f.origin === orig && f.destination === dest && f.depart_date === dep && f.return_date === ret
    );
  }
  if (state.airline !== "ALL") {
    rows = rows.filter((f) => f.primary_airline === state.airline);
  }

  if (state.sortBy === "price") {
    rows.sort((a, b) => a.price_cad - b.price_cad || a.total_duration_minutes - b.total_duration_minutes);
  } else if (state.sortBy === "nonstop") {
    rows.sort((a, b) => a.stops - b.stops || a.price_cad - b.price_cad);
  } else if (state.sortBy === "duration") {
    rows.sort((a, b) => a.total_duration_minutes - b.total_duration_minutes || a.price_cad - b.price_cad);
  } else {
    rows.sort((a, b) => b.rank_score - a.rank_score || a.price_cad - b.price_cad);
  }

  const pairLabel = state.selectedPair
    ? (() => {
        const [, , dep, ret] = state.selectedPair.split("|");
        return ` for ${shortDate(dep)} – ${shortDate(ret)}`;
      })()
    : "";
  $("results-count").textContent = `${rows.length} flight${rows.length === 1 ? "" : "s"}${pairLabel}`;

  if (!rows.length) {
    list.innerHTML = `<div class="empty-state">No flights match these filters. Try unchecking "Nonstop only" or widening your dates.</div>`;
    return;
  }

  list.innerHTML = rows
    .slice(0, 35)
    .map((f) => {
      const first = f.legs[0];
      const last = f.legs[f.legs.length - 1];
      const stopsText =
        f.stops === 0
          ? "Nonstop"
          : f.layovers && f.layovers.length
          ? `${f.stops} stop · ${f.layovers.map((l) => `${fmtDur(l.duration_minutes)} in ${l.airport_code}`).join(", ")}`
          : `${f.stops} stop`;

      const details = [first?.flight_number, first?.aircraft].filter(Boolean).join(" · ");
      const bagLabel = f.carry_on_included ? "round trip · carry-on incl." : "round trip";

      return `
        <article class="flight-row">
          <div class="fr-meta">
            <span class="fr-dates">${dayAndDate(f.depart_date)} – ${dayAndDate(f.return_date)} · ${f.nights}n</span>
            <span class="fr-airline">${esc(f.airlines.join(" + "))}</span>
            ${details ? `<span class="fr-sub">${esc(details)}</span>` : ""}
            ${f.note ? `<span class="fr-note">${esc(f.note)}</span>` : ""}
          </div>

          <div class="fr-route">
            <div class="fr-time">
              <strong>${esc(first?.departure_time || "--:--")}</strong>
              <span>${esc(f.origin)}</span>
            </div>
            <div class="fr-mid">
              <div class="fr-dur">${fmtDur(f.total_duration_minutes)}</div>
              <div class="fr-line"></div>
              <div class="fr-stops ${f.stops === 0 ? "nonstop" : ""}">${esc(stopsText)}</div>
            </div>
            <div class="fr-time" style="text-align:right">
              <strong>${esc(last?.arrival_time || "--:--")}</strong>
              <span>${esc(f.destination)}</span>
            </div>
          </div>

          <div class="fr-action">
            <div class="fr-price-wrap">
              <div class="fr-price">${fmtCAD(f.price_cad)}</div>
              <div class="fr-price-sub">${bagLabel}</div>
            </div>
            <a class="fr-book" href="${esc(f.booking_url)}" target="_blank" rel="noopener">
              View flight
            </a>
          </div>
        </article>
      `;
    })
    .join("");
}

/* ---------- Email Alert ---------- */
async function loadAlert() {
  try {
    const resp = await fetch("/api/alert");
    const cfg = await resp.json();
    $("alert-email").value = cfg.email || "";
    $("alert-price").value = cfg.max_price_cad || 850;
    $("alert-nonstop").checked = Boolean(cfg.nonstop_only);
    $("alert-daily").checked = Boolean(cfg.daily_summary);
    updateAlertButton(cfg);
  } catch (err) {
    console.error("Failed loading alert config:", err);
  }
}

function updateAlertButton(cfg) {
  const active = Boolean(cfg.enabled && cfg.email);
  $("alert-dot").classList.toggle("on", active);
  $("alert-toggle-label").textContent = active ? `Alert on · < CA$${cfg.max_price_cad}` : "Email alerts";
  $("disable-alert-btn").classList.toggle("hidden", !active);
}

async function saveAlert(enabled) {
  const status = $("alert-status");
  status.classList.remove("error");
  const email = $("alert-email").value.trim();
  if (enabled && (!email || !email.includes("@"))) {
    status.classList.add("error");
    status.textContent = "Please enter a valid email address.";
    return;
  }

  const body = {
    enabled,
    email,
    origin: "YOW",
    destination: "YVR",
    window_start: $("window-start").value,
    window_end: $("window-end").value,
    min_nights: parseInt($("min-nights").value, 10) || 6,
    max_nights: parseInt($("max-nights").value, 10) || 9,
    nonstop_only: $("alert-nonstop").checked,
    include_carry_on: $("carry-on-toggle").checked,
    max_price_cad: parseInt($("alert-price").value, 10) || 850,
    daily_summary: $("alert-daily").checked,
  };

  try {
    const resp = await fetch("/api/alert", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const saved = await resp.json();
    updateAlertButton(saved);
    if (!enabled) {
      status.textContent = "Email alert turned off.";
    } else if (saved.email_configured) {
      status.textContent = `Saved. We'll email ${saved.email} if a flight drops under CA$${saved.max_price_cad}.`;
    } else {
      status.textContent = `Saved for ${saved.email} (under CA$${saved.max_price_cad}). Email sending activates once RESEND_API_KEY or SMTP is added on the server.`;
    }
  } catch (err) {
    status.classList.add("error");
    status.textContent = `Could not save alert: ${err.message}`;
  }
}

/* ---------- Events ---------- */
document.addEventListener("DOMContentLoaded", () => {
  $("alert-toggle-btn").addEventListener("click", () => {
    $("alert-panel").classList.toggle("hidden");
  });
  $("close-alert-btn").addEventListener("click", () => {
    $("alert-panel").classList.add("hidden");
  });

  $("alert-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveAlert(true);
  });
  $("disable-alert-btn").addEventListener("click", () => saveAlert(false));

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const p = PRESETS[chip.dataset.preset];
      if (p) {
        $("window-start").value = p.start;
        $("window-end").value = p.end;
        $("min-nights").value = p.min;
        $("max-nights").value = p.max;
        runSearch(false);
      }
    });
  });

  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(false);
  });

  $("clear-date-filter").addEventListener("click", () => {
    state.selectedPair = null;
    renderDateGrid();
    renderFlights();
  });

  $("airline-select").addEventListener("change", (e) => {
    state.airline = e.target.value;
    renderFlights();
  });

  document.querySelectorAll(".sort-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".sort-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.sortBy = btn.dataset.sort;
      renderFlights();
    });
  });

  loadAlert();
  runSearch(false);
});
