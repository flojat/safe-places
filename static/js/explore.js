/* Karte und Liste fuer die Gastansicht.
 * - Nur gleichursprungliche Endpunkte (/api/..., /map/tiles/...), kein Direktzugriff auf Provider.
 * - Entprellen: Kartenbewegung 300 ms, Suche 350 ms; veraltete Requests abbrechen.
 * - Kein Polling, kein Prefetch, kein automatischer Retry bei Fehlern/Limits.
 * - DOM-Ausgabe ausschliesslich ueber textContent.
 */
(function () {
  "use strict";

  var config = JSON.parse(document.getElementById("map-config").textContent);
  var CH_BOUNDS = [[45.5, 5.6], [48.15, 10.85]];
  var CH_CENTER = [46.8, 8.23];

  var root = document.querySelector("main.explore");
  var els = {
    q: document.getElementById("q"),
    category: document.getElementById("category"),
    status: document.getElementById("status"),
    results: document.getElementById("results"),
    prev: document.getElementById("prev"),
    next: document.getElementById("next"),
    pageInfo: document.getElementById("page-info"),
    hint: document.getElementById("map-hint"),
    showList: document.getElementById("show-list"),
    showMap: document.getElementById("show-map"),
    mapPanel: document.getElementById("map-panel"),
  };

  var state = { q: "", category: "", page: 1, pages: 1, bbox: null, view: { lat: CH_CENTER[0], lon: CH_CENTER[1], z: 8 } };
  var map = null;
  var markerLayer = null;
  var markers = {};
  var activeId = null;
  var lastUrl = { points: null, list: null };
  var controllers = { points: null, list: null };

  // ---- Zustand aus URL (Server validiert unabhaengig davon) ----
  (function readUrl() {
    var p = new URLSearchParams(window.location.search);
    var q = p.get("q"); if (q) state.q = q.slice(0, 80);
    var c = p.get("category"); if (c) state.category = c;
    var lat = parseFloat(p.get("lat")), lon = parseFloat(p.get("lon")), z = parseInt(p.get("z"), 10);
    if (isFinite(lat) && isFinite(lon) && isFinite(z)) {
      state.view = { lat: lat, lon: lon, z: Math.min(config.maxZoom, Math.max(config.minZoom, z)) };
    }
    els.q.value = state.q;
    els.category.value = state.category;
  })();

  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.category) p.set("category", state.category);
    if (map) {
      var c = map.getCenter();
      p.set("lat", c.lat.toFixed(5)); p.set("lon", c.lng.toFixed(5)); p.set("z", String(map.getZoom()));
    }
    var qs = p.toString();
    var path = window.location.pathname + (qs ? "?" + qs : "");
    window.history.replaceState(null, "", path);
    // Rueckkehr nach Login: nur gleichursprunglicher relativer Pfad.
    document.querySelectorAll('a[href*="next="]').forEach(function (a) {
      var u = new URL(a.href, window.location.origin);
      u.searchParams.set("next", path);
      a.href = u.pathname + u.search;
    });
  }

  function debounce(fn, ms) {
    var t = null;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function buildQuery(extra) {
    var p = new URLSearchParams();
    if (state.bbox) p.set("bbox", state.bbox);
    if (state.q) p.set("q", state.q);
    if (state.category) p.set("category", state.category);
    Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
    return p.toString();
  }

  function fetchJson(kind, url) {
    if (lastUrl[kind] === url) return Promise.resolve(null); // gleiche Anfrage vermeiden
    if (controllers[kind]) controllers[kind].abort();
    var ctrl = new AbortController();
    controllers[kind] = ctrl;
    lastUrl[kind] = url;
    return fetch(url, { signal: ctrl.signal, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 429) { lastUrl[kind] = null; throw new Error("Zu viele Anfragen. Bitte kurz warten und dann erneut versuchen."); }
        if (!r.ok) { lastUrl[kind] = null; throw new Error("Die Anfrage konnte nicht verarbeitet werden."); }
        return r.json();
      })
      .then(function (data) { return controllers[kind] === ctrl ? data : null; })
      .catch(function (err) {
        if (err.name === "AbortError") return null;
        throw err;
      });
  }

  function setStatus(text) { els.status.textContent = text; }

  // ---- Liste ----
  function loadList() {
    var url = "/api/liste?" + buildQuery({ page: state.page });
    fetchJson("list", url).then(function (data) {
      if (!data) return;
      state.pages = data.pages;
      renderList(data);
    }).catch(function (err) { setStatus(err.message); });
  }

  function renderList(data) {
    els.results.replaceChildren();
    data.results.forEach(function (item) {
      var li = document.createElement("li");
      li.dataset.id = item.id;
      var h = document.createElement("h2");
      var a = document.createElement("a");
      a.href = "/objekt/" + encodeURIComponent(item.id) + "/";
      a.textContent = item.title;
      h.appendChild(a);
      var meta = document.createElement("p");
      meta.className = "muted";
      meta.textContent = item.category_label + " · " + item.place;
      var text = document.createElement("p");
      text.textContent = item.preview_text;
      li.append(h, meta, text);
      li.addEventListener("click", function (ev) {
        if (ev.target.tagName === "A") return;
        highlight(item.id, true);
      });
      els.results.appendChild(li);
    });
    var label = data.count === 1 ? "1 Treffer" : data.count + " Treffer";
    setStatus(map ? label + " im Kartenausschnitt" : label);
    els.pageInfo.textContent = "Seite " + data.page + " / " + data.pages;
    els.prev.disabled = data.page <= 1;
    els.next.disabled = data.page >= data.pages;
    if (activeId) highlight(activeId, false);
  }

  // ---- Karte ----
  function loadPoints() {
    if (!map) return;
    var url = "/api/punkte?" + buildQuery();
    fetchJson("points", url).then(function (data) {
      if (!data) return;
      markerLayer.clearLayers();
      markers = {};
      if (data.too_many) {
        els.hint.textContent = data.count + " Treffer – bitte hineinzoomen, um Punkte anzuzeigen (max. " + data.max_points + ").";
        els.hint.hidden = false;
        return;
      }
      els.hint.hidden = true;
      data.points.forEach(function (pt) {
        var m = L.circleMarker([pt.lat, pt.lon], { radius: 7, weight: 2, color: "#0b6e4f", fillColor: "#3fa37f", fillOpacity: 0.8 });
        m.bindPopup(function () {
          var div = document.createElement("div");
          var a = document.createElement("a");
          a.href = "/objekt/" + encodeURIComponent(pt.id) + "/";
          a.textContent = pt.title;
          var small = document.createElement("div");
          small.className = "muted";
          small.textContent = "Ungefähre Lage";
          div.append(a, small);
          return div;
        });
        m.on("click", function () { highlight(pt.id, false); });
        m.addTo(markerLayer);
        markers[pt.id] = m;
      });
    }).catch(function (err) { els.hint.textContent = err.message; els.hint.hidden = false; });
  }

  function highlight(id, fromList) {
    activeId = id;
    els.results.querySelectorAll("li").forEach(function (li) {
      li.classList.toggle("active", li.dataset.id === id);
    });
    var m = markers[id];
    if (m) {
      Object.keys(markers).forEach(function (k) { markers[k].setStyle({ color: k === id ? "#b3261e" : "#0b6e4f" }); });
      if (fromList) m.openPopup();
    }
  }

  var lastView = null;

  function updateBboxFromMap() {
    var b = map.getBounds();
    var box = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    state.bbox = box.map(function (v) { return v.toFixed(4); }).join(",");
    lastView = { box: box, zoom: map.getZoom() };
  }

  // Kleine Nachkorrekturen (Traegheit, Kartengrenze) erzeugen keine neue Anfrage.
  function viewChangedSignificantly() {
    if (!lastView || map.getZoom() !== lastView.zoom) return true;
    var b = map.getBounds();
    var now = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    var spanX = lastView.box[2] - lastView.box[0], spanY = lastView.box[3] - lastView.box[1];
    return now.some(function (v, i) {
      return Math.abs(v - lastView.box[i]) > 0.005 * (i % 2 === 0 ? spanX : spanY);
    });
  }

  var onMapMove = debounce(function () {
    if (!viewChangedSignificantly()) return;
    updateBboxFromMap();
    state.page = 1;
    writeUrl();
    loadPoints();
    loadList();
  }, 300);

  function initMap() {
    if (map || !window.L) return;
    map = L.map("map", {
      minZoom: config.minZoom,
      maxZoom: config.maxZoom,
      maxBounds: CH_BOUNDS,
      maxBoundsViscosity: 1.0,
      worldCopyJump: false,
    }).setView([state.view.lat, state.view.lon], state.view.z);
    var attribution = config.tileSource === "local_test"
      ? "Lokale Testkacheln (keine Kartendaten)"
      : '&copy; <a href="https://www.swisstopo.admin.ch/" rel="noopener">swisstopo</a>';
    L.tileLayer("/map/tiles/{z}/{x}/{y}.jpeg", {
      minZoom: config.minZoom,
      maxZoom: config.maxZoom,
      bounds: CH_BOUNDS,
      noWrap: true,
      updateWhenIdle: true,
      updateWhenZooming: false,
      keepBuffer: 1,
      detectRetina: false,
      crossOrigin: false,
      attribution: attribution,
    }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    map.on("moveend", onMapMove);
    updateBboxFromMap();
    writeUrl();
    loadPoints();
    loadList();
  }

  function mapVisible() { return els.mapPanel.offsetParent !== null && els.mapPanel.clientHeight > 0; }

  function setView(view) {
    root.dataset.view = view;
    els.showList.setAttribute("aria-selected", String(view === "list"));
    els.showMap.setAttribute("aria-selected", String(view === "map"));
    if (view === "map") {
      if (!map) initMap(); else map.invalidateSize();
    }
  }

  // ---- Ereignisse ----
  els.q.addEventListener("input", debounce(function () {
    var v = els.q.value.trim().slice(0, 80);
    if (v === state.q) return;
    state.q = v; state.page = 1; writeUrl(); loadPoints(); loadList();
  }, 350));
  els.category.addEventListener("change", function () {
    state.category = els.category.value; state.page = 1; writeUrl(); loadPoints(); loadList();
  });
  document.getElementById("filters").addEventListener("submit", function (ev) { ev.preventDefault(); });
  els.prev.addEventListener("click", function () { if (state.page > 1) { state.page -= 1; loadList(); } });
  els.next.addEventListener("click", function () { if (state.page < state.pages) { state.page += 1; loadList(); } });
  els.showList.addEventListener("click", function () { setView("list"); });
  els.showMap.addEventListener("click", function () { setView("map"); });

  // Karte nur laden, wenn sichtbar (mobil erst nach Umschalten).
  root.dataset.view = "list";
  if (mapVisible()) initMap(); else loadList();
})();
