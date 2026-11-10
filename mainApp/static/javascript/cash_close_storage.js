/* Confirmed steps have a database checkpoint plus an independent browser backup. */
(function(root) {
  'use strict';
  const memory = new Map();
  const ranks = {payments: 0, cash: 1, media: 2};
  const rank = p => ranks[p?.progress?.step] || 0;
  // A reset starts a different draft even if another browser still has the old one open.
  const key = (id, attempt = '') => `tc_contados_${id}${attempt ? `_${attempt}` : ''}`;
  const url = id => root.TURNO_CIERRE_RESPALDO?.replace('/0/', `/${id}/`);
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const clone = value => JSON.parse(JSON.stringify(value));
  function read(id, attempt) {
    const draftKey = key(id, attempt);
    let local;
    try { local = JSON.parse(root.localStorage.getItem(draftKey) || 'null'); } catch {}
    const cached = memory.get(draftKey);
    if (local && (!cached || rank(local) > rank(cached) || (rank(local) === rank(cached) && local.ts > cached.ts))) {
      memory.set(draftKey, local); return local;
    }
    return cached || null;
  }
  async function remote(id, progress, attempt) {
    if (!url(id)) return null;
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 6000);
    try {
      const response = await fetch(url(id), {method: progress ? 'POST' : 'GET', credentials: 'same-origin',
        cache: 'no-store', signal: ctrl.signal, headers: {'Accept': 'application/json',
          'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest'},
        ...(progress ? {body: JSON.stringify(attempt ? {...progress, attempt} : progress)} : {})});
      if (response.redirected) throw Object.assign(new Error('La sesión venció. Conserva el cierre y vuelve a iniciar sesión.'), {blocked: true});
      const data = await response.json().catch(() => null);
      if (response.ok && data?.success) {
        if ((data.attempt || '') !== (attempt || '')) {
          throw Object.assign(new Error('El cierre cambió. Vuelve a Turno de caja para continuar con el cierre vigente.'), {blocked: true});
        }
        return data;
      }
      if (response.status >= 400 && response.status < 500) {
        throw Object.assign(new Error(data?.error || 'No tienes autorización para guardar este cierre.'), {blocked: true});
      }
      return null;
    } catch (error) { if (error.blocked) throw error; return null; }
    finally { clearTimeout(timeout); }
  }
  function put(id, payload, attempt) {
    const draftKey = key(id, attempt);
    const copy = clone(payload);
    memory.set(draftKey, copy);
    let stored = false;
    try { root.NovaStorage.safeSet(draftKey, JSON.stringify(copy)); stored = true; } catch {}
    // Invocation order is transaction order; no unhandled rejection on input events.
    const durable = root.NovaStorage.durable.set(draftKey, copy).then(() => true, () => false);
    return {stored, durable};
  }
  async function load(id, attempt) {
    let payload = read(id, attempt);
    try {
      const saved = await root.NovaStorage.durable.get(key(id, attempt));
      if (saved && (!payload || rank(saved) > rank(payload) || (rank(saved) === rank(payload) && saved.ts > payload.ts))) payload = saved;
    } catch {}
    const result = await remote(id, null, attempt);
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
    if (payload) put(id, payload, attempt);
    return payload;
  }
  async function commit(id, payload, attempt) {
    const result = await remote(id, payload.progress, attempt);
    const previous = read(id, attempt);
    const saved = put(id, payload, attempt);
    const durable = await saved.durable;
    if (!result && !saved.stored && !durable) {
      const draftKey = key(id, attempt);
      if (previous) memory.set(draftKey, previous); else memory.delete(draftKey);
      throw new Error('No se pudo guardar el avance en la base ni en este navegador. No se avanzó. Descarga el respaldo y conserva esta pestaña.');
    }
    if (!result && url(id)) root.dispatchEvent(new CustomEvent('nova:close-local-only'));
    return true;
  }
  function forget(id, attempt) {
    const draftKey = key(id, attempt);
    memory.delete(draftKey);
    try { root.localStorage.removeItem(draftKey); } catch {}
    void root.NovaStorage.durable.remove(draftKey).catch(() => {});
  }
  function download(id, payload, attempt) {
    const blob = new Blob([JSON.stringify({version: 1, turno_id: id, intento: attempt || '', payload}, null, 2)], {type: 'application/json'});
    const link = document.createElement('a'); const href = URL.createObjectURL(blob);
    link.href = href; link.download = `respaldo-cierre-${id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
  }
  root.NovaCloseStorage = {key, read, put, load, commit, forget, download};
})(window);
