const ORIGIN_META = {
  YOW: { city: "OTTAWA", code: "YOW", label: "Ottawa", list: ["YOW"] },
  YYZ: { city: "TORONTO PEARSON", code: "YYZ", label: "Toronto Pearson", list: ["YYZ"] },
  YTZ: { city: "BILLY BISHOP", code: "YTZ", label: "Billy Bishop", list: ["YTZ"] },
  ALL: { city: "OTTAWA / TORONTO", code: "YOW·YYZ·YTZ", label: "Ottawa or Toronto", list: ["YOW", "YYZ", "YTZ"] },
};

const state = {
  scanning: false,
  originMode: "YOW",
  sortBy: "recommended",
  airline: "ALL",
  showAllFlights: false,
  flights: [],
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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

function updateOriginLabels() {
  const meta = ORIGIN_META[state.originMode] || ORIGIN_META.YOW;
  $("ticket-origin-city").textContent = meta.city;
  const codeEl = $("ticket-origin-code");
  codeEl.textContent = meta.code;
  codeEl.style.fontSize = state.originMode === "ALL" ? "1.02rem" : "";
  $("outbound-title").textContent = `Flying out · ${meta.label} to Vancouver`;
  $("return-title").textContent = `Flying home · Vancouver to ${meta.label}`;
}

/* ---------- Live Search ---------- */
async function runSearch(forceRefresh = false) {
  if (state.scanning) return;
  state.scanning = true;
  state.showAllFlights = false;
  state.airline = "ALL";

  $("search-btn").disabled = true;
  $("search-btn").textContent = "Searching…";
  $("progress-wrap").classList.remove("hidden");
  $("progress-fill").style.width = "6%";
  $("progress-text").textContent = "Checking live Google Flights prices…";

  const minN = Math.max(1, parseInt($("min-nights").value, 10) || 5);
  const maxN = Math.max(minN, parseInt($("max-nights").value, 10) || 10);
  const meta = ORIGIN_META[state.originMode] || ORIGIN_META.YOW;

  const payload = {
    origin: meta.list[0],
    origins: meta.list,
    destination: "YVR",
    depart_start: $("depart-start").value,
    depart_end: $("depart-end").value,
    return_start: $("return-start").value,
    return_end: $("return-end").value,
    min_nights: minN,
    max_nights: maxN,
    nonstop_only: $("nonstop-toggle").checked,
    include_carry_on: $("carry-on-toggle").checked,
    include_nearby_airports: false,
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
    $("search-btn").textContent = "Find flights";
  }
}

function onStreamEvent(evt) {
  if (evt.type === "start") {
    $("progress-text").textContent = `Checking ${evt.total} date combinations…`;
    return;
  }

  if (evt.type === "progress") {
    const pct = Math.max(8, Math.round((evt.completed / evt.total) * 100));
    $("progress-fill").style.width = `${pct}%`;
    $("progress-text").textContent = `Checked ${evt.completed} of ${evt.total} combinations`;
    return;
  }

  if (evt.type === "complete") {
    state.flights = evt.flights || [];
    setTimeout(() => $("progress-wrap").classList.add("hidden"), 250);
    $("results-section").classList.remove("hidden");
    populateAirlines();
    renderFlights();
  }
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

  const meta = ORIGIN_META[state.originMode] || ORIGIN_META.YOW;
  $("results-count").textContent = `${rows.length} flight${rows.length === 1 ? "" : "s"} from ${meta.label}`;

  if (!rows.length) {
    $("show-more-wrap").classList.add("hidden");
    const extraHint =
      state.originMode === "YTZ"
        ? " Billy Bishop (YTZ) doesn't have nonstop flights to Vancouver, so try unchecking Nonstop only."
        : " Try unchecking Nonstop only or widening your dates.";
    list.innerHTML = `<div class="empty-state">No flights match these filters.${extraHint}</div>`;
    return;
  }

  const defaultLimit = 4;
  const limit = state.showAllFlights ? 40 : defaultLimit;
  const visible = rows.slice(0, limit);
  const showMoreWrap = $("show-more-wrap");
  if (rows.length > defaultLimit) {
    showMoreWrap.classList.remove("hidden");
    $("show-more-btn").textContent = state.showAllFlights
      ? "Show fewer flights"
      : `Show all ${Math.min(rows.length, 40)} flights`;
  } else {
    showMoreWrap.classList.add("hidden");
  }

  list.innerHTML = visible
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

/* ---------- Events ---------- */
document.addEventListener("DOMContentLoaded", () => {
  $("share-trip-btn").addEventListener("click", async () => {
    const lbl = $("share-trip-label");
    try {
      await navigator.clipboard.writeText(window.location.href);
      lbl.textContent = "Link copied";
      setTimeout(() => {
        lbl.textContent = "Share trip";
      }, 2000);
    } catch {
      lbl.textContent = "Copy URL above";
      setTimeout(() => {
        lbl.textContent = "Share trip";
      }, 2000);
    }
  });

  document.querySelectorAll(".origin-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".origin-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.originMode = chip.dataset.origin;
      updateOriginLabels();
      runSearch(false);
    });
  });

  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(false);
  });

  $("show-more-btn").addEventListener("click", () => {
    state.showAllFlights = !state.showAllFlights;
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

  updateOriginLabels();
  runSearch(false);
});
