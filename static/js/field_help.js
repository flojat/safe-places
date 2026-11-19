/* Hilfetexte zu Objektfeldern: "i"-Knopf direkt beim Feldnamen, Text im Overlay.
 * - Daten aus #field-help-data (vom Server nur fuer angemeldete Konten ausgegeben).
 * - Ausgabe ausschliesslich ueber textContent; Leerzeilen trennen Absaetze.
 * - Native <dialog>: Esc schliesst, Fokus kehrt zum Knopf zurueck; Klick ausserhalb schliesst.
 */
(function () {
  "use strict";
  var source = document.getElementById("field-help-data");
  if (!source) return;
  var data;
  try { data = JSON.parse(source.textContent); } catch (e) { return; }

  var dialog, title, body, returnFocus = null;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "field-help-dialog";
    dialog.setAttribute("aria-labelledby", "field-help-title");
    var head = document.createElement("div");
    head.className = "field-help-head";
    title = document.createElement("h2");
    title.id = "field-help-title";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "field-help-close";
    close.setAttribute("aria-label", "Hilfe schliessen");
    close.textContent = "×";
    close.addEventListener("click", function () { dialog.close(); });
    head.append(title, close);
    body = document.createElement("div");
    body.className = "field-help-body";
    dialog.append(head, body);
    dialog.addEventListener("click", function (ev) { if (ev.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () { if (returnFocus) returnFocus.focus(); });
    document.body.appendChild(dialog);
  }

  function open(entry, from) {
    if (!dialog) build();
    returnFocus = from;
    title.textContent = "Hilfe: " + entry.label;
    body.textContent = "";
    String(entry.text).split(/\n\s*\n/).forEach(function (part) {
      var p = document.createElement("p");
      p.textContent = part.trim();
      body.appendChild(p);
    });
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  Object.keys(data).forEach(function (field) {
    var entry = data[field];
    var label = document.querySelector('label[for="id_' + field + '"]');
    if (!label || !entry || !entry.text) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "field-help-btn";
    btn.setAttribute("aria-label", "Hilfe zu " + entry.label);
    btn.setAttribute("aria-haspopup", "dialog");
    btn.title = "Hilfe zu " + entry.label;
    btn.textContent = "i";
    btn.addEventListener("click", function (ev) { ev.preventDefault(); open(entry, btn); });
    // Feldname und Knopf in eine Zeile fassen (Formulare stapeln Label und Feld sonst untereinander).
    var row = document.createElement("span");
    row.className = "field-help-row";
    label.parentNode.insertBefore(row, label);
    row.append(label, btn);
    label.classList.add("has-field-help");
  });
})();
