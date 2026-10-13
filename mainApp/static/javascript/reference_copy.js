/* Read-only status refresh; never submits payments or starts a download. */
(() => {
  "use strict";
  const panel = document.getElementById("reference-copy");
  if (!panel) return;
  const title = document.getElementById("reference-title");
  const message = document.getElementById("reference-message");
  const dates = document.getElementById("reference-dates");
  const button = document.getElementById("reference-submit");
  const format = new Intl.DateTimeFormat("es-CO", { dateStyle: "short", timeStyle: "short" });
  let stopped = false;
  async function update() {
    if (stopped) return;
    if (document.hidden) { window.setTimeout(update, 5000); return; }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(panel.dataset.statusUrl, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json" }
      });
      if (response.redirected || response.status === 401 || response.status === 403) {
        stopped = true;
        message.textContent = "Tu sesión o autorización cambió. Vuelve a iniciar sesión para consultar la copia.";
        if (button) button.disabled = true;
        return;
      }
      if (!response.ok) throw new Error("status unavailable");
      const data = await response.json();
      title.textContent = data.ready ? "Copia disponible" : "Copia pendiente de preparación o renovación";
      message.textContent = data.message;
      dates.textContent = data.synchronized_at && data.expires_at
        ? `Última actualización: ${format.format(new Date(data.synchronized_at))} · Vigente hasta ${format.format(new Date(data.expires_at))}` : "";
      panel.querySelectorAll("[data-reference-count]").forEach(element => {
        element.textContent = Number(data.counts[element.dataset.referenceCount] || 0).toLocaleString("es-CO");
      });
      if (button) {
        button.disabled = data.busy;
        button.textContent = data.busy ? "Actualizando…" : (data.has_copy ? "Actualizar copia" : "Preparar copia");
      }
    } catch (_) {
      message.textContent = "No se pudo consultar el estado. La copia guardada no se ha borrado; se reintentará en unos segundos.";
    } finally {
      window.clearTimeout(timeout);
      if (!stopped) window.setTimeout(update, 5000);
    }
  }
  window.addEventListener("pagehide", () => { stopped = true; });
  window.setTimeout(update, 1000);
})();
