/* Original sale form transport. Does not replace fields, autocompletes or UI. */
(function (root) {
  "use strict";

  function createTransport({storage, randomUUID, fetchRequest, userId, confirmationUrl = null,
    wait = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
    async function recover(id) {
      if (!confirmationUrl) return null;
      for (let attempt = 0; attempt < 3; attempt++) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 3000);
        try {
          const response = await fetchRequest(confirmationUrl(id), {method: "GET", credentials: "same-origin",
            cache: "no-store", signal: controller.signal, headers: {Accept: "application/json"}});
          if (!response.ok || response.redirected) return null;
          const data = await response.json();
          if (data.status === "confirmed" && data.response?.success === true
              && Number.isInteger(data.response.venta_id) && data.response.venta_id > 0) return data.response;
          if (data.status === "saved_offline" && data.response?.success === true
              && data.response.offline_saved === true && data.response.operation_id === id
              && data.response.venta_id === `LOCAL-${id}`) return data.response;
          if (data.status === "not_confirmed") return {success: false,
            error: "La venta no quedó confirmada en Aiven. Conserva el carrito y vuelve a intentarlo; no repitas la entrega de dinero."};
          if (data.status === "review") return {success: false,
            error: "Esta venta necesita revisión. Conserva el carrito y no repitas el cobro."};
          if (data.status !== "pending") return null;
        } catch (_) { return null; }
        finally { clearTimeout(timer); }
        if (attempt < 2) await wait(1000);
      }
      return null;
    }
    function reference(draftId) {
      if (!draftId || !userId) throw new Error("No se pudo identificar este carrito. Consérvalo y vuelve a iniciar sesión.");
      const key = `nova:integrated-request:${userId}:${draftId}`;
      let id = storage.getItem(key);
      if (!id) {
        id = randomUUID();
        storage.setItem(key, id);
        // Never submit first and persist the retry reference afterwards.
        if (storage.getItem(key) !== id) throw new Error("No se pudo proteger la referencia de esta venta. Conserva el carrito.");
      }
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id)) {
        throw new Error("La referencia guardada no es válida. Conserva el carrito y solicita revisión.");
      }
      return id;
    }

    return {
      async submit(body, draftId, total, url, csrf) {
        const id = reference(draftId);
        let response;
        try {
          response = await fetchRequest(url, {
            method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest",
              "X-Nova-Operation-ID": id, "X-Nova-Expected-Total": String(total)},
            body,
          });
        } catch (_) {
          const recovered = await recover(id);
          if (recovered) return recovered;
          throw new Error("Se perdió la respuesta. Conserva este carrito: al reintentar se verificará la misma referencia para no duplicar la venta.");
        }
        let result;
        try { result = await response.json(); }
        catch (_) {
          const recovered = await recover(id);
          if (recovered) return recovered;
          throw new Error("La respuesta no confirmó la venta. Conserva el carrito y no repitas el cobro.");
        }
        if (!result || typeof result !== "object" || Array.isArray(result)) {
          throw new Error("No se pudo confirmar la venta. Conserva el carrito.");
        }
        if (!response.ok && result.success !== false) {
          throw new Error("No se pudo confirmar la venta. Conserva el carrito.");
        }
        if (["sale_uncertain", "operation_busy", "reconcile_before_cloud"].includes(result.code)) {
          return await recover(id) || result;
        }
        return result;
      },
    };
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {createTransport};
  else root.NovaIntegratedSale = createTransport({
    // Access can throw when storage is disabled. Defer it until submit so the
    // transport remains installed and never falls back to an unprotected POST.
    storage: {getItem: key => root.localStorage.getItem(key),
      setItem: (key, value) => root.localStorage.setItem(key, value)},
    randomUUID: () => root.crypto.randomUUID(),
    fetchRequest: (...args) => root.fetch(...args), userId: String(root.ventaUsuarioId || ""),
    confirmationUrl: root.novaJournalEnabled ? id => `/local/ventas/confirmacion/${id}/` : null,
  });
})(typeof window !== "undefined" ? window : globalThis);
