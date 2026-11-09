/* Genaue Lage im freigegebenen Einzelobjekt (nur Mitgliedsansicht mit Buchung). */
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("detail-location").textContent);
  var map = L.map("detail-map", { minZoom: 12, maxZoom: 18, scrollWheelZoom: false }).setView([data.lat, data.lon], 15);
  L.tileLayer("/map/tiles/{z}/{x}/{y}.jpeg", {
    minZoom: 12, maxZoom: 18, noWrap: true, updateWhenIdle: true, updateWhenZooming: false,
    keepBuffer: 1, detectRetina: false, attribution: '&copy; Data: <a href="https://www.swisstopo.admin.ch/" rel="noopener noreferrer">swisstopo</a>',
  }).addTo(map);
  L.circleMarker([data.lat, data.lon], { radius: 8, color: "#b3261e" }).addTo(map);
})();
