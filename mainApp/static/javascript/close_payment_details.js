(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NovaClosePayments = api;
})(globalThis, function () {
  "use strict";
  const money = value => new Intl.NumberFormat("es-CO", {style:"currency", currency:"COP"}).format(value);
  function cents(value) {
    const raw = String(value ?? "").trim();
    if (!/^[0-9]{1,10}(?:\.[0-9]{1,2})?$/.test(raw)) throw new Error("Usa valores de 0 o mayores, con máximo dos decimales.");
    const [whole, fraction = ""] = raw.split(".");
    return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  }
  function validate(total, rows) {
    const expected = cents(total === "" || total == null ? "0" : total);
    if (!Array.isArray(rows) || rows.length > 100) throw new Error("Registra como máximo 100 pagos.");
    let sum = 0;
    const items = rows.map((row, index) => {
      const name = String(row.nombre ?? "").replace(/\s+/g, " ").trim().toUpperCase();
      if (!name || name.length > 160) throw new Error(`Escribe el nombre del pago ${index + 1} (máximo 160 caracteres).`);
      const value = cents(row.monto);
      if (value <= 0) throw new Error(`El pago ${index + 1} debe ser mayor que 0. Elimina los pagos vacíos.`);
      sum += value;
      return {nombre: name, monto: (value / 100).toFixed(2)};
    });
    if (sum !== expected) throw new Error(`La suma de los pagos (${money(sum / 100)}) debe ser igual al total inicial (${money(expected / 100)}).`);
    return {total: expected / 100, items};
  }
  function renderSummary(target, data) {
    if (!target) return;
    target.replaceChildren();
    const note = document.createElement("p");
    if (!data?.disponible) {
      note.textContent = "No hay un desglose guardado para este turno. Los cierres anteriores conservan solo su total.";
      target.append(note);
      return;
    }
    const list = document.createElement("ul");
    list.className = "cp-history";
    for (const item of data.pagos || []) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      const value = document.createElement("strong");
      name.textContent = item.nombre;
      value.textContent = money(item.monto);
      row.append(name, value);
      list.append(row);
    }
    note.textContent = data.pagos?.length ? `Registrado por ${data.registrado_por || "el cajero"}.` : "Se declaró que no hubo pagos de caja.";
    target.append(list, note);
  }
  return {cents, validate, renderSummary};
});
