/* Read-only status: never retries or submits a sale. */
(function () {
  "use strict";
  const status = document.getElementById("nova-integrated-status");
  const sales = document.getElementById("nova-integrated-sales");
  if (!status || !sales) return;
  async function refresh() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      if (document.hidden) return;
      const response = await fetch("/local/ventas/estado/", {credentials: "same-origin", cache: "no-store",
        signal: controller.signal, headers: {Accept: "application/json"}});
      if (!response.ok || response.redirected) throw new Error("status unavailable");
      const data = await response.json();
      const labels = {online: "Con conexión · guardando en la nube", offline: "Sin conexión · guardando en este equipo",
        synchronizing: "Enviando ventas guardadas · puedes seguir vendiendo localmente",
        blocked: "Se necesita revisión antes de seguir vendiendo", unavailable: "Copia local no disponible"};
      status.textContent = `${labels[data.mode] || "Verificando conexión"}. Pendientes: ${data.pending}. En revisión: ${data.review}.`;
      sales.replaceChildren();
      for (const row of data.sales || []) {
        const item = document.createElement("p");
        const result = row.state === "accepted" ? `Enviada · factura #${row.cloud_sale_id}`
          : row.state === "conflict" ? `Revisar: ${row.reason}` : "Guardada, pendiente de envío";
        item.textContent = `LOCAL-${row.reference} · $${Number(row.total).toLocaleString("es-CO")} · ${result}`;
        sales.appendChild(item);
      }
      if (!sales.childElementCount) sales.textContent = "Todavía no hay ventas offline en este equipo.";
    } catch (_) {
      status.textContent = "No se pudo consultar el estado. Conserva cualquier carrito sin confirmar.";
    } finally {
      clearTimeout(timer);
      setTimeout(refresh, 5000);
    }
  }
  refresh();
})();
