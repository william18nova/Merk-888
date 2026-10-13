/* One durable UUID per original form/turn action; no automatic POST retry. */
(function (root) {
  "use strict";
  function createTransport({storage, randomUUID, fetchRequest, userId}) {
    const prefix = `nova:business:${userId}:`;
    function reference(key) {
      let id = storage.getItem(prefix + key);
      if (!id) { id = randomUUID(); storage.setItem(prefix + key, id); }
      if (storage.getItem(prefix + key) !== id || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id)) {
        throw new Error("No se pudo proteger esta operación en el navegador. No la registres otra vez.");
      }
      storage.setItem(prefix + "return:" + id, key);
      return id;
    }
    return {
      reference,
      acknowledge(key, id) { if (storage.getItem(prefix + key) === id) storage.removeItem(prefix + key); },
      acknowledgeReturn(id) {
        const binding = prefix + "return:" + id;
        const key = storage.getItem(binding);
        if (key && storage.getItem(prefix + key) === id) storage.removeItem(prefix + key);
        storage.removeItem(binding);
      },
      async submit(url, body, key, csrf, signal) {
        const id = reference(key);
        let response, data;
        try {
          response = await fetchRequest(url, {method: "POST", credentials: "same-origin", signal,
            headers: {"X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest", "X-Nova-Business": "1",
              "X-Nova-Operation-ID": id}, body});
          data = await response.json();
        } catch (_) { throw new Error("No se confirmó la respuesta. Conserva este formulario y su referencia; no repitas la entrega de dinero."); }
        if (!data || typeof data !== "object" || (data.success === true && !response.ok)) {
          throw new Error("La respuesta no confirmó la operación. Consulta Sincronización antes de repetirla.");
        }
        return data;
      }
    };
  }
  if (typeof module !== "undefined" && module.exports) { module.exports = {createTransport}; return; }
  const tag = document.currentScript;
  root.NovaBusiness = createTransport({
    storage: {getItem: key => sessionStorage.getItem(key), setItem: (key, value) => sessionStorage.setItem(key, value),
      removeItem: key => sessionStorage.removeItem(key)},
    randomUUID: () => crypto.randomUUID(), fetchRequest: (...args) => fetch(...args), userId: tag.dataset.user
  });
  const pageKey = location.pathname;
  const ack = new URL(location.href).searchParams.get("nova_ack");
  if (ack) {
    root.NovaBusiness.acknowledgeReturn(ack);
    root.NovaBusiness.acknowledge(pageKey, ack);
  }
  document.addEventListener("submit", async event => {
    const form = event.target;
    if (!form.matches("[data-nova-business-form]") || event.defaultPrevented) return;
    event.preventDefault();
    const button = event.submitter;
    const body = new FormData(form);
    if (button?.name) body.set(button.name, button.value);
    let alert = form.querySelector("[data-nova-result]");
    if (!alert) { alert = document.createElement("p"); alert.dataset.novaResult = ""; alert.setAttribute("role", "alert"); form.prepend(alert); }
    try {
      const result = await root.NovaBusiness.submit(form.action || location.pathname, body, pageKey,
        body.get("csrfmiddlewaretoken"));
      if (result.success && result.redirect) { location.assign(result.redirect); return; }
      alert.textContent = result.error || "Revisa los datos. No se confirmó el registro.";
    } catch (error) { alert.textContent = error.message; }
    finally {
      if (button) { button.disabled = false; const label = button.querySelector("span"); if (label && form.hasAttribute("data-expense-form")) label.textContent = "Registrar pago"; }
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
