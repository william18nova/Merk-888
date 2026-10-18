/* Read-only status: never switch databases, retry a sale or trust navigator.onLine. */
(function (root) {
  "use strict";
  const states = {
    online: ["Online", "Conectado a la base de datos online."],
    offline: ["Local", "Sin conexión a la base online. Trabajando con la copia local."],
    synchronizing: ["Sincronizando", "Usando la copia local mientras se sincronizan los pendientes con la base online."],
    blocked: ["Revisar", "La conexión o los pendientes necesitan revisión. No se han descartado datos."],
    unavailable: ["Sin conexión", "La base de datos no está disponible. No se ha confirmado el modo local."],
    unknown: ["Sin verificar", "No se pudo comprobar el estado. Verifica que el programa siga funcionando."]
  };
  function status(mode) {
    if (!Object.prototype.hasOwnProperty.call(states, mode)) mode = "unknown";
    return {mode, label: states[mode][0], description: states[mode][1]};
  }
  function responseStatus(response, body) {
    if (response.status === 401) {
      return {...status("unknown"), description: "La sesión venció. Vuelve a iniciar sesión para comprobar la conexión."};
    }
    if (response.ok) return status(body?.mode);
    if (response.status === 503) {
      const mode = response.headers.get("X-Nova-Mode") || body?.mode;
      if (mode === "blocked" || mode === "unavailable") return status(mode);
    }
    return status("unknown");
  }
  function createMonitor(options) {
    let running = false;
    let stopped = false;
    let timer;
    const schedule = options.schedule || root.setTimeout.bind(root);
    const cancel = options.cancel || root.clearTimeout.bind(root);
    async function refresh() {
      if (stopped || running) return;
      cancel(timer);
      if (!options.visible()) return;
      running = true;
      const controller = new root.AbortController();
      const timeout = schedule(() => controller.abort(), 6000);
      let delay = 15000;
      try {
        const response = await options.fetch(options.url, {
          method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {Accept: "application/json"}, signal: controller.signal
        });
        // Blocked hybrid requests may have only the authoritative mode header.
        let body = null;
        try { body = await response.json(); } catch (_) { /* Not a status payload. */ }
        if (!stopped) options.render(responseStatus(response, body));
        if (!response.ok) delay = 30000;
        if (response.status === 401) stopped = true;
      } catch (_) {
        // Losing the server does not prove the local database is operational.
        if (!stopped) options.render(status("unknown"));
        delay = 30000;
      } finally {
        cancel(timeout);
        running = false;
        if (!stopped && options.visible()) timer = schedule(refresh, delay);
      }
    }
    return {refresh, stop() { stopped = true; cancel(timer); }};
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {status, responseStatus, createMonitor};
    return;
  }
  const element = document.getElementById("nvConnection");
  if (!element?.dataset.statusUrl || !root.fetch) return;
  const monitor = createMonitor({
    url: element.dataset.statusUrl,
    fetch: root.fetch.bind(root),
    visible: () => document.visibilityState !== "hidden",
    render(value) {
      const label = element.querySelector("[data-connection-label]");
      element.dataset.mode = value.mode;
      element.title = value.description;
      element.setAttribute("aria-label", "Base de datos: " + value.label + ". " + value.description);
      if (label.textContent !== value.label) label.textContent = value.label;
    }
  });
  document.addEventListener("visibilitychange", monitor.refresh);
  root.addEventListener("pageshow", monitor.refresh);
  // Network events ask the application again; they never pick a mode themselves.
  root.addEventListener("online", monitor.refresh);
  root.addEventListener("offline", monitor.refresh);
  monitor.refresh();
})(typeof window !== "undefined" ? window : globalThis);
