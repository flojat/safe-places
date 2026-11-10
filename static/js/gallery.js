/* Bildergalerie (Lightbox) ohne Fremdbibliothek.
 * - Zeigt nur Bilder, die der Server fuer diese Seite/diesen Besucher geliefert hat;
 *   die Fotoauslieferung prueft die Berechtigung bei jedem Abruf erneut.
 * - Bildbeschreibung als Bildunterschrift und als Alternativtext.
 * - Tastatur: Esc schliesst, Pfeiltasten blaettern; Wischen auf Touchgeraeten.
 * - DOM-Ausgabe ausschliesslich ueber textContent/setAttribute.
 */
(function () {
  "use strict";

  var overlay, img, desc, counter, prevBtn, nextBtn, closeBtn;
  var items = [], index = 0, returnFocus = null, touchX = null;

  function sameOrigin(url) { return typeof url === "string" && url.charAt(0) === "/" && url.charAt(1) !== "/"; }

  function button(cls, label, text) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.setAttribute("aria-label", label);
    b.textContent = text;
    return b;
  }

  function build() {
    overlay = document.createElement("div");
    overlay.className = "lightbox";
    overlay.hidden = true;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Bildergalerie");
    var figure = document.createElement("figure");
    img = document.createElement("img");
    var caption = document.createElement("figcaption");
    desc = document.createElement("span");
    desc.className = "lightbox-desc";
    counter = document.createElement("span");
    counter.className = "lightbox-count";
    caption.append(desc, counter);
    figure.append(img, caption);
    prevBtn = button("lightbox-prev", "Vorheriges Bild", "‹");
    nextBtn = button("lightbox-next", "Nächstes Bild", "›");
    closeBtn = button("lightbox-close", "Galerie schliessen", "×");
    overlay.append(figure, prevBtn, nextBtn, closeBtn);
    document.body.appendChild(overlay);

    prevBtn.addEventListener("click", function () { show(index - 1); });
    nextBtn.addEventListener("click", function () { show(index + 1); });
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", function (ev) { if (ev.target === overlay) close(); });
    img.addEventListener("error", function () { desc.textContent = "Bild derzeit nicht verfügbar."; });
    overlay.addEventListener("touchstart", function (ev) { touchX = ev.changedTouches[0].clientX; }, { passive: true });
    overlay.addEventListener("touchend", function (ev) {
      if (touchX === null) return;
      var dx = ev.changedTouches[0].clientX - touchX;
      touchX = null;
      if (Math.abs(dx) > 40) show(index + (dx < 0 ? 1 : -1));
    });
    overlay.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.preventDefault(); close(); }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); show(index - 1); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); show(index + 1); }
      else if (ev.key === "Tab") {
        var focusable = [prevBtn, nextBtn, closeBtn].filter(function (b) { return !b.hidden; });
        var pos = focusable.indexOf(document.activeElement);
        ev.preventDefault();
        focusable[(pos + (ev.shiftKey ? -1 : 1) + focusable.length) % focusable.length].focus();
      }
    });
  }

  function show(i) {
    var n = items.length;
    index = ((i % n) + n) % n;
    var item = items[index];
    img.removeAttribute("src");
    if (sameOrigin(item.full)) img.src = item.full;
    img.alt = item.description || "";
    desc.textContent = item.description || "";
    counter.textContent = n > 1 ? (index + 1) + " / " + n : "";
    prevBtn.hidden = nextBtn.hidden = n < 2;
  }

  function open(list, start, from) {
    items = (list || []).filter(function (p) { return p && sameOrigin(p.full); });
    if (!items.length) return;
    if (!overlay) build();
    returnFocus = from || document.activeElement;
    overlay.hidden = false;
    document.body.classList.add("lightbox-open");
    show(start || 0);
    closeBtn.focus();
  }

  function close() {
    overlay.hidden = true;
    img.removeAttribute("src");
    document.body.classList.remove("lightbox-open");
    if (returnFocus && returnFocus.focus) returnFocus.focus();
  }

  window.Gallery = { open: open };

  document.querySelectorAll("[data-gallery]").forEach(function (container) {
    var source = document.getElementById(container.getAttribute("data-gallery"));
    if (!source) return;
    var data = JSON.parse(source.textContent);
    container.addEventListener("click", function (ev) {
      var link = ev.target.closest("a[data-gallery-index]");
      if (!link) return;
      ev.preventDefault();
      open(data, parseInt(link.getAttribute("data-gallery-index"), 10) || 0, link);
    });
  });
})();
