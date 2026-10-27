/* Confirmed steps have a database checkpoint plus an independent browser backup. */
(function(root) {
  'use strict';
  const memory = new Map();
  const ranks = {payments: 0, cash: 1, media: 2};
  const rank = p => ranks[p?.progress?.step] || 0;
  const key = id => `tc_contados_${id}`;
  const url = id => root.TURNO_CIERRE_RESPALDO?.replace('/0/', `/${id}/`);
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const clone = value => JSON.parse(JSON.stringify(value));
  function read(id) {
    let local;
    try { local = JSON.parse(root.localStorage.getItem(key(id)) || 'null'); } catch {}
    const cached = memory.get(String(id));
    if (local && (!cached || rank(local) > rank(cached) || (rank(local) === rank(cached) && local.ts > cached.ts))) {
      memory.set(String(id), local); return local;
    }
    return cached || null;
  }
  async function remote(id, progress) {
    if (!url(id)) return null;
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 6000);
    try {
      const response = await fetch(url(id), {method: progress ? 'POST' : 'GET', credentials: 'same-origin',
        cache: 'no-store', signal: ctrl.signal, headers: {'Accept': 'application/json',
          'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest'},
        ...(progress ? {body: JSON.stringify(progress)} : {})});
      if (response.redirected) throw Object.assign(new Error('La sesión venció. Conserva el cierre y vuelve a iniciar sesión.'), {blocked: true});
      const data = await response.json().catch(() => null);
      if (response.ok && data?.success) return data;
      if (response.status >= 400 && response.status < 500) {
        throw Object.assign(new Error(data?.error || 'No tienes autorización para guardar este cierre.'), {blocked: true});
      }
      return null;
    } catch (error) { if (error.blocked) throw error; return null; }
    finally { clearTimeout(timeout); }
  }
  function put(id, payload) {
    const copy = clone(payload);
    memory.set(String(id), copy);
    let stored = false;
    try { root.NovaStorage.safeSet(key(id), JSON.stringify(copy)); stored = true; } catch {}
    // Invocation order is transaction order; no unhandled rejection on input events.
    const durable = root.NovaStorage.durable.set(key(id), copy).then(() => true, () => false);
    return {stored, durable};
  }
  async function load(id) {
    let payload = read(id);
    try {
      const saved = await root.NovaStorage.durable.get(key(id));
      if (saved && (!payload || rank(saved) > rank(payload) || (rank(saved) === rank(payload) && saved.ts > payload.ts))) payload = saved;
    } catch {}
    const result = await remote(id);
    if (result?.progress) {
      // Keep unconfirmed counts from the latest draft, but server-confirmed fields win.
      const server = result.progress;
      if (!payload || rank({progress: server}) >= rank(payload)) {
        payload = {...payload, progress: server, facturas_pagadas: server.invoices,
          pagos_detalle: server.payment_details, ...(server.denominations ? {denominaciones: server.denominations} : {}), ts: Date.now()};
      } else if (String(Number(server.invoices)) !== String(Number(payload.progress.invoices))
          || JSON.stringify(server.payment_details) !== JSON.stringify(payload.progress.payment_details)) {
        throw new Error('El respaldo local no coincide con los pagos confirmados en el servidor. Conserva los datos y solicita revisión.');
      }
    }
    if (payload) { memory.set(String(id), payload); put(id, payload); }
    return payload;
  }
  async function commit(id, payload) {
    const result = await remote(id, payload.progress);
    const previous = read(id);
    const saved = put(id, payload);
    const durable = await saved.durable;
    if (!result && !saved.stored && !durable) {
      if (previous) memory.set(String(id), previous); else memory.delete(String(id));
      throw new Error('No se pudo guardar el avance en la base ni en este navegador. No se avanzó. Descarga el respaldo y conserva esta pestaña.');
    }
    if (!result && url(id)) root.dispatchEvent(new CustomEvent('nova:close-local-only'));
    return true;
  }
  function forget(id) {
    memory.delete(String(id));
    try { root.localStorage.removeItem(key(id)); } catch {}
    void root.NovaStorage.durable.remove(key(id)).catch(() => {});
  }
  function download(id, payload) {
    const blob = new Blob([JSON.stringify({version: 1, turno_id: id, payload}, null, 2)], {type: 'application/json'});
    const link = document.createElement('a'); const href = URL.createObjectURL(blob);
    link.href = href; link.download = `respaldo-cierre-${id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
  }
  root.NovaCloseStorage = {read, put, load, commit, forget, download};
})(window);
