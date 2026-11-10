/* Grundkarten (Landeskarte / Luftbild) fuer alle Leaflet-Karten der Seite.
 * - Nur gleichursprungliche Kachelwege; der Server holt die Kacheln bei swisstopo.
 * - Umschalter als kleine Schaltflaeche oben rechts; Wahl je Browser gemerkt (falls erlaubt).
 */
(function () {
  "use strict";

  var URLS = { karte: "/map/tiles/{z}/{x}/{y}.jpeg", luftbild: "/map/luftbild/{z}/{x}/{y}.jpeg" };
  var LABELS = { karte: "Karte", luftbild: "Luftbild" };
  var STORE_KEY = "adresskarte.grundkarte";

  function tileSource() {
    var el = document.getElementById("map-config");
    try { return el ? JSON.parse(el.textContent).tileSource : "swisstopo"; } catch (e) { return "swisstopo"; }
  }

  function remembered() {
    try { var v = window.localStorage.getItem(STORE_KEY); return URLS[v] ? v : null; } catch (e) { return null; }
  }

  function remember(name) {
    try { window.localStorage.setItem(STORE_KEY, name); } catch (e) { /* ohne Speicher: nur fuer diese Seite */ }
  }

  function attribution(name) {
    if (tileSource() === "local_test") return "Lokale Testkacheln (keine Kartendaten)";
    return '&copy; ' + (name === "luftbild" ? "SWISSIMAGE, " : "") +
      'Data: <a href="https://www.swisstopo.admin.ch/" rel="noopener noreferrer">swisstopo</a>';
  }

  /* Fuegt die Grundkarte und den Umschalter hinzu. opts: minZoom, maxZoom, bounds, initial, onChange */
  function add(map, opts) {
    opts = opts || {};
    var current = URLS[opts.initial] ? opts.initial : (remembered() || "karte");
    var layers = {};
    Object.keys(URLS).forEach(function (name) {
      layers[name] = L.tileLayer(URLS[name], {
        minZoom: opts.minZoom, maxZoom: opts.maxZoom, bounds: opts.bounds, noWrap: true,
        updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1, detectRetina: false,
        crossOrigin: false, attribution: attribution(name),
      });
    });
    layers[current].addTo(map);

    var Toggle = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        var box = L.DomUtil.create("div", "leaflet-bar basemap-toggle");
        box.setAttribute("role", "group");
        box.setAttribute("aria-label", "Grundkarte");
        Object.keys(URLS).forEach(function (name) {
          var b = L.DomUtil.create("button", "", box);
          b.type = "button";
          b.textContent = LABELS[name];
          b.dataset.basemap = name;
          b.setAttribute("aria-pressed", String(name === current));
          L.DomEvent.on(b, "click", function (ev) {
            L.DomEvent.stop(ev);
            select(name);
          });
        });
        L.DomEvent.disableClickPropagation(box);
        return box;
      },
    });
    var control = new Toggle().addTo(map);

    function select(name) {
      if (!URLS[name] || name === current) return;
      map.removeLayer(layers[current]);
      layers[name].addTo(map);
      current = name;
      remember(name);
      control.getContainer().querySelectorAll("button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.dataset.basemap === name));
      });
      if (opts.onChange) opts.onChange(name);
    }

    return { select: select, current: function () { return current; } };
  }

  window.Basemaps = { add: add, names: Object.keys(URLS) };
})();
