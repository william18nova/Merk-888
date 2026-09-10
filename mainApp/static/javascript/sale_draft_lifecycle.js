/* Presencia real de la página y exclusión de recuperaciones entre pestañas.
   No se usan vencimientos de heartbeat: una pestaña en segundo plano sigue viva. */
(function (root) {
  "use strict";
  function createSaleDraftLifecycle({ locks, tabID }) {
    const supported = !!(locks && locks.request && locks.query);
    const prefix = "nova:venta-page:";
    let release = null;
    let starting = null;
    let held = false;
    let generation = 0;

    function start() {
      if (!supported) return Promise.resolve(false);
      if (starting) return starting;
      const run = ++generation;
      starting = new Promise((ready) => {
        try {
          Promise.resolve(locks.request(prefix + tabID, () => {
            if (run !== generation) { ready(false); return; }
            held = true;
            ready(true);
            return new Promise((resolve) => { release = resolve; });
          })).catch(() => { held = false; ready(false); });
        } catch (_) { ready(false); }
      });
      return starting;
    }

    function stop() {
      generation += 1;
      held = false;
      if (release) release();
      release = null;
      starting = null;
    }

    async function openOwners() {
      if (!supported) throw new Error("No hay comprobación segura de pestañas.");
      const snapshot = await locks.query();
      return new Set([...(snapshot.held || []), ...(snapshot.pending || [])]
        .filter((lock) => lock.name.startsWith(prefix))
        .map((lock) => lock.name.slice(prefix.length)));
    }

    async function withClosedDraft(draft, action) {
      if (!supported || !(await start())) return false;
      // El dueño no puede reactivarse (Atrás / caché del navegador) durante
      // la transferencia. Dos recuperaciones tampoco pueden ganar a la vez.
      const owner = draft.owner_tab_id;
      if (owner === tabID && held) return false;
      const exclusive = () => locks.request(
        "nova:venta-recover:" + draft.storage_key,
        { ifAvailable: true },
        (lock) => lock ? action() : false,
      );
      if (!owner) return exclusive();
      return locks.request(prefix + owner, { ifAvailable: true }, (lock) => lock ? exclusive() : false);
    }

    return { start, stop, openOwners, withClosedDraft, supported, get held() { return held; } };
  }
  if (typeof module === "object" && module.exports) module.exports = createSaleDraftLifecycle;
  else root.createSaleDraftLifecycle = createSaleDraftLifecycle;
})(typeof globalThis === "object" ? globalThis : window);
