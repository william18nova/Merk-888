/* Server-authoritative navigation. Never replay a write or clear a cart. */
(function (root) {
  "use strict";
  const offlineMessage = "Estamos offline. Esta opción necesita conexión a internet. Puedes seguir trabajando en las funciones disponibles.";
  function observeFetch(fetchRequest, notify) {
    return async function (...args) {
      const response = await fetchRequest(...args);
      if (response.headers.get("X-Nova-Offline-Blocked") === "1") notify(offlineMessage, args);
      return response; // Same response/body/UUID; no retry, no consumption.
    };
  }
  if (typeof module !== "undefined" && module.exports) { module.exports = {observeFetch}; return; }
  const statusUrl = document.currentScript?.dataset.statusUrl;
  const dialog = document.getElementById("nova-offline-dialog");
  if (!statusUrl || !dialog) return;
  let lastInteraction = 0;
  let navigating = false;
  function show(message) {
    document.body.classList.remove("is-page-loading", "is-page-leaving");
    dialog.querySelector("#nova-offline-message").textContent = message;
    if (!dialog.open) dialog.showModal();
  }
  // The browser's navigator.onLine only knows the LAN, not Aiven availability.
  // Ask the same server policy that protects each page. This GET has no effects.
  const rawFetch = root.fetch.bind(root);
  root.fetch = observeFetch(rawFetch, message => {
    if (Date.now() - lastInteraction < 3000) show(message);
  });
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      if (this.getResponseHeader("X-Nova-Offline-Blocked") === "1" && Date.now() - lastInteraction < 3000) show(offlineMessage);
    }, {once: true});
    return send.apply(this, args);
  };
  document.addEventListener("click", async event => {
    if (event.isTrusted) lastInteraction = Date.now();
    if (event.target.closest?.("[data-offline-retry]")) { location.reload(); return; }
    const anchor = event.target.closest?.(".nv a[href], .quick-action[href]");
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        || anchor.target || anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href, location.href);
    if (url.origin !== location.origin || anchor.getAttribute("href").startsWith("#")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (navigating) return;
    navigating = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3500);
    try {
      const endpoint = new URL(statusUrl, location.origin);
      endpoint.searchParams.set("path", url.pathname);
      const response = await rawFetch(endpoint, {credentials: "same-origin", cache: "no-store",
        headers: {Accept: "application/json"}, signal: controller.signal});
      const result = await response.json();
      if (response.ok && result.allowed === true) { location.assign(url.href); return; }
      show(result.message || result.error || "No se pudo comprobar esta opción. Conserva tu trabajo y vuelve a intentarlo.");
    } catch (_) {
      show("No se pudo contactar con el programa local. Comprueba que sigue ejecutándose; tu carrito no se ha borrado.");
    } finally { clearTimeout(timer); navigating = false; }
  }, true);
  document.addEventListener("submit", event => {
    if (event.isTrusted && !event.target.matches('[method="dialog"]')) lastInteraction = Date.now();
  }, true);
})(typeof window !== "undefined" ? window : globalThis);
