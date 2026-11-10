/* Genaue Lage im freigegebenen Einzelobjekt (nur Mitgliedsansicht mit Buchung). */
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("detail-location").textContent);
  var map = L.map("detail-map", { minZoom: 12, maxZoom: 18, scrollWheelZoom: false }).setView([data.lat, data.lon], 15);
  window.Basemaps.add(map, { minZoom: 12, maxZoom: 18 });
  L.circleMarker([data.lat, data.lon], { radius: 8, color: "#b3261e" }).addTo(map);
})();
