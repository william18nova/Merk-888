(() => {
  "use strict";
  const form = document.getElementById("ptm-form");
  if (!form) return;
  const tipo = document.getElementById("id_tipo");
  const monto = document.getElementById("id_monto");
  const message = document.getElementById("ptm-direction");
  const refresh = () => {
    const amount = Number(monto.value || 0);
    message.textContent = `${tipo.value === "retiro" ? "SALE de la caja" : "ENTRA a la caja"}: ${new Intl.NumberFormat("es-CO", {style:"currency", currency:"COP"}).format(amount)}.`;
  };
  tipo.addEventListener("change", refresh);
  monto.addEventListener("input", refresh);
  refresh();
  form.addEventListener("submit", () => { form.querySelector('button[type="submit"]').disabled = true; });
  window.addEventListener("pageshow", () => {
    // Una respuesta perdida se puede reenviar: el servidor conserva la misma clave.
    if (document.getElementById("id_turno").options.length > 1) form.querySelector('button[type="submit"]').disabled = false;
  });
})();
