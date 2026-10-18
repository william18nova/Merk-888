/* Only render server-authorized data. Cashier payloads never contain surplus. */
(function () {
  "use strict";
  const money = (n) => new Intl.NumberFormat("es-CO", {style: "currency", currency: "COP"}).format(Number(n || 0));
  function render(root, data) {
    if (!root) return;
    root.replaceChildren();
    if (!data?.activa) return;
    const box = document.createElement("section");
    box.className = "bp-summary";
    function text(tag, value, parent = box) {
      const node = document.createElement(tag);
      node.textContent = value;
      parent.append(node);
      return node;
    }
    text("h3", "Bolsa PTM · independiente de caja");
    text("p", data.mensaje || "No incluyas esta bolsa en los billetes de la caja.");
    for (const item of data.incidencias || []) {
      const row = document.createElement("div");
      row.className = "bp-warning";
      const admin = Object.hasOwn(item, "diferencia");
      text("strong", admin ? `Corte PTM #${item.id} · Diferencia: ${money(item.diferencia)}` :
        `Faltante PTM #${item.id}: ${money(item.faltante)}`, row);
      text("p", `Estado: ${item.estado}. Participaron: ${(item.participantes || []).map(p => `${p.nombre} (turno #${p.turno})`).join(", ")}.`, row);
      if (admin) {
        text("p", `Base: ${money(item.base)} · Esperado: ${money(item.esperado)} · Contado: ${money(item.contado)} · Revisión ${item.revision}`, row);
        for (const warning of item.advertencias || []) text("p", warning, row);
      }
      text("p", "Una sola incidencia compartida. No se duplica entre empleados ni se suma a la deuda normal de caja.", row);
      box.append(row);
    }
    root.append(box);
  }
  window.NovaPTMBag = {render};
})();
