/* Punkt auf der Karte waehlen (Alternative zur Adresssuche), z. B. fuer Waldhaus oder Grillplatz.
 * - Oeffnet ein Popup mit Karte (gleichursprungliche Kacheln, Karte/Luftbild umschaltbar).
 * - Klick oder Tippen setzt den Punkt; der Marker laesst sich verschieben.
 * - "Uebernehmen" schreibt Breite/Laenge (6 Nachkommastellen) in die Formularfelder;
 *   gespeichert wird erst mit dem Formular, der Server prueft die Lage erneut.
 */
(function () {
  "use strict";

  var CH_BOUNDS = [[45.8, 5.9], [47.85, 10.55]];
  var CH_CENTER = [46.8, 8.23];

  function inSwitzerland(lat, lon) {
    return lat >= CH_BOUNDS[0][0] && lat <= CH_BOUNDS[1][0] && lon >= CH_BOUNDS[0][1] && lon <= CH_BOUNDS[1][1];
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  document.querySelectorAll("[data-location-picker]").forEach(function (box) {
    var latField = document.getElementById(box.dataset.latField);
    var lonField = document.getElementById(box.dataset.lonField);
    var openBtn = box.querySelector("button");
    var status = box.querySelector(".picker-status");
    if (!latField || !lonField || !openBtn || !window.L) return;

    var dialog = null, map = null, marker = null, chosen = null, coords, takeBtn;

    function build() {
      dialog = el("dialog", "picker-dialog");
      dialog.setAttribute("aria-label", "Punkt auf der Karte wählen");
      var head = el("div", "picker-head");
      head.append(el("strong", "", "Punkt auf der Karte wählen"),
                  el("span", "picker-hint", "Klicken oder tippen Sie auf die gewünschte Stelle. Den Punkt können Sie verschieben."));
      var mapBox = el("div", "picker-map");
      var foot = el("div", "picker-foot");
      coords = el("span", "picker-coords", "Noch kein Punkt gewählt.");
      var cancel = el("button", "secondary", "Abbrechen");
      cancel.type = "button";
      takeBtn = el("button", "", "Koordinaten übernehmen");
      takeBtn.type = "button";
      takeBtn.disabled = true;
      foot.append(coords, cancel, takeBtn);
      dialog.append(head, mapBox, foot);
      document.body.appendChild(dialog);

      map = L.map(mapBox, { minZoom: 8, maxZoom: 18, maxBounds: [[45.5, 5.6], [48.15, 10.85]], maxBoundsViscosity: 1.0 });
      window.Basemaps.add(map, { minZoom: 8, maxZoom: 18 });
      map.on("click", function (ev) { setPoint(ev.latlng.lat, ev.latlng.lng); });

      cancel.addEventListener("click", function () { dialog.close(); });
      takeBtn.addEventListener("click", function () {
        if (!chosen) return;
        latField.value = chosen[0].toFixed(6);
        lonField.value = chosen[1].toFixed(6);
        latField.dispatchEvent(new Event("change", { bubbles: true }));
        lonField.dispatchEvent(new Event("change", { bubbles: true }));
        status.textContent = "Koordinaten übernommen (" + latField.value + ", " + lonField.value + "). Bitte speichern.";
        dialog.close();
      });
      dialog.addEventListener("close", function () { openBtn.focus(); });
    }

    function setPoint(lat, lon) {
      if (!inSwitzerland(lat, lon)) {
        coords.textContent = "Der Punkt liegt ausserhalb der Schweiz.";
        takeBtn.disabled = true;
        chosen = null;
        return;
      }
      chosen = [lat, lon];
      if (!marker) {
        var pin = L.divIcon({ className: "picker-pin", iconSize: [22, 22], iconAnchor: [11, 11] });
        marker = L.marker([lat, lon], { icon: pin, draggable: true, keyboard: true, title: "Gewählter Punkt" }).addTo(map);
        marker.on("dragend", function () { var p = marker.getLatLng(); setPoint(p.lat, p.lng); });
      } else {
        marker.setLatLng([lat, lon]);
      }
      coords.textContent = "Gewählt: " + lat.toFixed(6) + ", " + lon.toFixed(6);
      takeBtn.disabled = false;
    }

    openBtn.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (!dialog) build();
      dialog.showModal();
      var lat = parseFloat(String(latField.value).replace(",", "."));
      var lon = parseFloat(String(lonField.value).replace(",", "."));
      map.invalidateSize();
      if (isFinite(lat) && isFinite(lon) && inSwitzerland(lat, lon)) {
        map.setView([lat, lon], 16);
        setPoint(lat, lon);
      } else {
        map.setView(CH_CENTER, 8);
      }
      setTimeout(function () { map.invalidateSize(); }, 50);
    });
  });
})();
