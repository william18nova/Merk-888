/* Solo presentación y accesibilidad de controles DataTables; no altera datos. */
(() => {
  "use strict";
  function start() {
    const page = document.querySelector("body.nova-ui .nova-page");
    if (!page || !page.querySelector("table.display")) return;
    function enhance() {
      page.querySelectorAll("td.dataTables_empty").forEach(cell => {
        if (cell.textContent.trim() === "No data available in table") cell.textContent = "No hay registros para mostrar.";
      });
      page.querySelectorAll(".dataTables_wrapper:not([data-nova-controls-ready])").forEach(wrapper => {
        const search = wrapper.querySelector(".dataTables_filter input");
        if (search) {
          search.setAttribute("aria-label", "Buscar en este listado");
          if (!search.placeholder) search.placeholder = "Buscar en el listado…";
          const label = search.closest("label");
          if (label && !label.textContent.trim()) label.prepend(document.createTextNode("Buscar: "));
        }
        const length = wrapper.querySelector(".dataTables_length label");
        if (length) {
          length.childNodes.forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) {
              node.textContent = node.textContent.replace(/\bShow\b/, "Mostrar").replace(/\bentries\b/, "registros");
            }
          });
          length.querySelector("select")?.setAttribute("aria-label", "Registros por página");
        }
        wrapper.querySelectorAll("thead th").forEach(header => header.setAttribute("scope", "col"));
        wrapper.dataset.novaControlsReady = "1";
      });
    }
    enhance();
    new MutationObserver(enhance).observe(page, {childList: true, subtree: true});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
