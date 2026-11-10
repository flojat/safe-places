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
    categoryBoxes: Array.prototype.slice.call(document.querySelectorAll('#category-filter input[name="category"]')),
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

  var state = { q: "", categories: [], basemap: null, page: 1, pages: 1, bbox: null, view: { lat: CH_CENTER[0], lon: CH_CENTER[1], z: 8 } };
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
    var basis = p.get("basis"); if (basis === "luftbild" || basis === "karte") state.basemap = basis;
    var known = els.categoryBoxes.map(function (b) { return b.value; });
    state.categories = p.getAll("category").filter(function (c, i, all) {
      return known.indexOf(c) >= 0 && all.indexOf(c) === i;
    });
    var lat = parseFloat(p.get("lat")), lon = parseFloat(p.get("lon")), z = parseInt(p.get("z"), 10);
    if (isFinite(lat) && isFinite(lon) && isFinite(z)) {
      state.view = { lat: lat, lon: lon, z: Math.min(config.maxZoom, Math.max(config.minZoom, z)) };
    }
    els.q.value = state.q;
    els.categoryBoxes.forEach(function (b) { b.checked = state.categories.indexOf(b.value) >= 0; });
  })();

  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    state.categories.forEach(function (c) { p.append("category", c); });
    if (map) {
      var c = map.getCenter();
      p.set("lat", c.lat.toFixed(5)); p.set("lon", c.lng.toFixed(5)); p.set("z", String(map.getZoom()));
      if (state.basemap === "luftbild") p.set("basis", "luftbild");
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
    state.categories.forEach(function (c) { p.append("category", c); });
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
        if (r.status === 403) { lastUrl[kind] = null; throw new Error("Sitzung abgelaufen. Bitte Seite neu laden."); }
        if (r.status === 503) { lastUrl[kind] = null; throw new Error("Vorübergehend nicht verfügbar. Bitte später erneut versuchen."); }
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

  // ---- Symbole (einfarbig, Strichgrafik; Pfade statisch, keine Serverdaten) ----
  var SVG_NS = "http://www.w3.org/2000/svg";
  var ICONS = {
    pin: "M12 21s-6-5.3-6-10a6 6 0 1 1 12 0c0 4.7-6 10-6 10z M12 9a2 2 0 1 0 0 4a2 2 0 1 0 0-4z",
    chevron: "M9 6l6 6-6 6",
    // Kategoriesymbole: Namen wie CATEGORY_ICONS in locations/models.py
    etikett: "M3 12V4h8l9 9-8 8z M7.5 7.5h.01",
    sprechblase: "M4 5h16v11H9l-5 4z",
    personen: "M9 11a3 3 0 1 0 0-6a3 3 0 1 0 0 6z M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6 M16 11a2.5 2.5 0 1 0 0-5 M17.5 14.2c2 .6 3.5 2.9 3.5 5.8",
    haus: "M3 11l9-7 9 7 M5 10v10h14V10 M10 20v-5h4v5",
    herz: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
    buch: "M4 5h6a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H4z M20 5h-6a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h6z",
    sonne: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
    blatt: "M5 19c0-8 5-13 15-14-1 10-6 15-14 15 M5 19l8-8",
    mond: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
    wlan_aus: "M2 8.5a15 15 0 0 1 6-3 M22 8.5a15 15 0 0 0-9.5-3.4 M5 12a10 10 0 0 1 4-2 M19 12a10 10 0 0 0-2.5-1.6 M8.5 15.5a5 5 0 0 1 7 0 M12 19h.01 M3 3l18 18",
    berg: "M3 20l6-11 4 6 2-3 6 8z M9 9l2 3",
    wasser: "M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z",
  };

  function icon(name) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", ICONS[name] || ICONS.etikett);
    svg.appendChild(path);
    return svg;
  }

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
      var href = "/objekt/" + encodeURIComponent(item.id) + "/";
      var li = document.createElement("li");
      li.className = "card";
      li.dataset.id = item.id;

      var media = document.createElement("div");
      media.className = "card-media";
      if (item.thumb) {
        var img = document.createElement("img");
        img.src = item.thumb.url;
        img.alt = item.thumb.alt || "";
        img.loading = "lazy";
        if (item.gallery && item.gallery.length && window.Gallery) {
          // Klick aufs Bild: Galerie der fuer Gaeste freigegebenen Bilder dieses Objekts.
          var open = document.createElement("button");
          open.type = "button";
          open.className = "card-media-open";
          open.setAttribute("aria-label", "Bilder ansehen: " + item.title +
            (item.gallery.length > 1 ? " (" + item.gallery.length + " Bilder)" : ""));
          open.appendChild(img);
          open.addEventListener("click", function () { window.Gallery.open(item.gallery, 0, open); });
          media.appendChild(open);
          if (item.gallery.length > 1) {
            var badge = document.createElement("span");
            badge.className = "card-media-count";
            badge.setAttribute("aria-hidden", "true");
            badge.textContent = String(item.gallery.length);
            media.appendChild(badge);
          }
        } else {
          media.appendChild(img);
        }
      }

      var body = document.createElement("div");
      body.className = "card-body";
      var head = document.createElement("div");
      head.className = "card-head";
      var h = document.createElement("h2");
      var a = document.createElement("a");
      a.href = href;
      a.textContent = item.title;
      h.appendChild(a);
      head.appendChild(h);
      if (item.place) {
        var place = document.createElement("span");
        place.className = "place";
        var placeText = document.createElement("span");
        placeText.textContent = item.place;
        place.append(icon("pin"), placeText);
        head.appendChild(place);
      }
      body.appendChild(head);
      if (item.categories && item.categories.length) {
        var tags = document.createElement("ul");
        tags.className = "tags";
        tags.setAttribute("aria-label", "Kategorien");
        item.categories.forEach(function (c) {
          var tag = document.createElement("li");
          tag.className = "tag";
          var label = document.createElement("span");
          label.textContent = c.label;
          tag.append(icon(c.icon), label);
          tags.appendChild(tag);
        });
        body.appendChild(tags);
      }
      var text = document.createElement("p");
      text.className = "card-text";
      text.textContent = item.preview_text;
      body.appendChild(text);

      var go = document.createElement("a");
      go.className = "card-go";
      go.href = href;
      go.setAttribute("aria-label", "Details: " + item.title);
      go.appendChild(icon("chevron"));

      li.append(media, body, go);
      li.addEventListener("click", function (ev) {
        if (ev.target.closest("a, button")) return;
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
    var basemaps = window.Basemaps.add(map, {
      minZoom: config.minZoom, maxZoom: config.maxZoom, bounds: CH_BOUNDS, initial: state.basemap,
      onChange: function (name) { state.basemap = name; writeUrl(); },
    });
    state.basemap = basemaps.current();
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
  els.categoryBoxes.forEach(function (box) {
    box.addEventListener("change", function () {
      state.categories = els.categoryBoxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
      state.page = 1; writeUrl(); loadPoints(); loadList();
    });
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
