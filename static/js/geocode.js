/* Adresssuche im Arbeitsbereich: nur per Knopfdruck, gleichursprunglich, textContent-Ausgabe. */
(function () {
  "use strict";
  document.querySelectorAll("[data-geocode-url]").forEach(function (box) {
    var input = box.querySelector("input[type=search]");
    var button = box.querySelector("button");
    var list = box.querySelector("ol");
    var status = box.querySelector(".geocode-status");
    var form = box.closest("form") || document;
    var csrf = (form.querySelector("input[name=csrfmiddlewaretoken]") || document.querySelector("input[name=csrfmiddlewaretoken]")).value;
    var latField = document.getElementById(box.dataset.latField);
    var lonField = document.getElementById(box.dataset.lonField);
    var busy = false;
    button.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (busy) return;
      busy = true;
      list.replaceChildren();
      status.textContent = "Suche läuft …";
      var body = new FormData();
      body.append("q", input.value);
      body.append("csrfmiddlewaretoken", csrf);
      fetch(box.dataset.geocodeUrl, { method: "POST", body: body, credentials: "same-origin", headers: { "X-CSRFToken": csrf } })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) { status.textContent = res.data.error || "Fehler"; return; }
          status.textContent = res.data.results.length ? "Treffer übernehmen:" : "Keine Treffer. Lage bitte manuell eingeben.";
          res.data.results.forEach(function (hit) {
            var li = document.createElement("li");
            var b = document.createElement("button");
            b.type = "button";
            b.textContent = hit.label + " (" + hit.lat.toFixed(5) + ", " + hit.lon.toFixed(5) + ")";
            b.addEventListener("click", function () {
              latField.value = hit.lat.toFixed(6);
              lonField.value = hit.lon.toFixed(6);
              status.textContent = "Koordinaten übernommen. Bitte speichern.";
            });
            li.appendChild(b);
            list.appendChild(li);
          });
        })
        .catch(function () { status.textContent = "Adresssuche nicht erreichbar. Lage bitte manuell eingeben."; })
        .finally(function () { busy = false; });
    });
  });
})();
