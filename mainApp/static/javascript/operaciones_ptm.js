(() => {
  "use strict";
  const form = document.getElementById("ptm-form");
  if (!form) return;
  const tipo = document.getElementById("id_tipo");
  const monto = document.getElementById("id_monto");
  const message = document.getElementById("ptm-direction");
  const turno = document.getElementById("id_turno");
  const help = document.getElementById("ptm-source-help");
  const refresh = () => {
    const option = turno.selectedOptions[0];
    const source = option?.dataset.ptmSource;
    if (!source) {
      message.textContent = "Selecciona tu turno para consultar de dónde sale o a dónde entra el efectivo.";
      help.textContent = "";
      return;
    }
    const bag = source === "bolsa";
    const place = bag ? `la bolsa PTM de ${option.dataset.ptmName}` : `la caja ${option.dataset.ptmName}`;
    const amount = Number(monto.value || 0);
    message.textContent = `${tipo.value === "retiro" ? "SALE de" : "ENTRA a"} ${place}: ${new Intl.NumberFormat("es-CO", {style:"currency", currency:"COP"}).format(Number.isFinite(amount) ? amount : 0)}.`;
    help.textContent = bag
      ? "No afecta el efectivo de tu caja. El reporte del lector actualiza el saldo esperado de la bolsa; este comprobante no suma ni resta el dinero otra vez."
      : "Esta sucursal no tiene bolsa PTM configurada. Esta operación sí ajusta el efectivo y el cierre de la caja seleccionada.";
  };
  tipo.addEventListener("change", refresh);
  monto.addEventListener("input", refresh);
  turno.addEventListener("change", refresh);
  refresh();
  form.addEventListener("submit", () => { form.querySelector('button[type="submit"]').disabled = true; });
  window.addEventListener("pageshow", () => {
    // Una respuesta perdida se puede reenviar: el servidor conserva la misma clave.
    if (document.getElementById("id_turno").options.length > 1) form.querySelector('button[type="submit"]').disabled = false;
  });
})();
