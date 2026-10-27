/* Disposable cache never takes priority over a sale or a closing checkpoint. */
(function(root) {
  "use strict";
  if (root.NovaStorage) return;
  const cacheKey = key => /^catalog_\d+(?:_ts)?$/.test(key) || /^pick_boost_\d+$/.test(key)
    || /^nova:visor-(?:catalog|picks):v1:u\d+$/.test(key);
  function cleanCache(storage) {
    const keys = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (cacheKey(key)) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
    return keys.length;
  }
  function safeSet(key, value, storage = root.localStorage) {
    try { storage.setItem(key, value); }
    catch (error) {
      if (!['QuotaExceededError', 'NS_ERROR_DOM_QUOTA_REACHED'].includes(error?.name)) throw error;
      cleanCache(storage); // Allowlist only. No carts, requests, print receipts or close data.
      storage.setItem(key, value);
    }
  }
  let opening;
  function open() {
    if (!opening) opening = new Promise((resolve, reject) => {
      const request = root.indexedDB.open('nova-pos-storage-v1', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('cache');
        request.result.createObjectStore('protected');
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('El almacenamiento está ocupado por otra versión.'));
      request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
    }).catch(error => { opening = null; throw error; });
    return opening;
  }
  async function access(store, key, value, remove = false) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const write = value !== undefined || remove;
      const tx = db.transaction(store, write ? 'readwrite' : 'readonly');
      const object = tx.objectStore(store);
      const request = remove ? object.delete(key) : write ? object.put(value, key) : object.get(key);
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error('No se guardó el respaldo local.'));
    });
  }
  const durable = {get: key => access('protected', key), set: (key, value) => access('protected', key, value),
    remove: key => access('protected', key, undefined, true)};
  async function reference(key, randomUUID) {
    let legacy;
    let conflict = false;
    try { legacy = root.localStorage.getItem(key); } catch {}
    try {
      const db = await open();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction('protected', 'readwrite');
        const store = tx.objectStore('protected'); const request = store.get(key);
        let value;
        request.onsuccess = () => {
          value = request.result || legacy || randomUUID();
          if (legacy && request.result && legacy !== request.result) { conflict = true; tx.abort(); return; }
          if (!request.result) store.put(value, key);
        };
        tx.oncomplete = () => resolve(value);
        tx.onerror = tx.onabort = () => reject(tx.error || new Error('Referencias incompatibles.'));
      });
    } catch {
      // Never mint a replacement if IndexedDB might contain an earlier request.
      if (legacy && !conflict) return legacy;
      throw new Error('No se pudo proteger la referencia de esta venta. Conserva el carrito y revisa el almacenamiento; no se envió el cobro.');
    }
  }
  async function catalogGet(sid) {
    const key = `catalog_${sid}`;
    try {
      const saved = await access('cache', key);
      if (saved) return saved;
      const raw = root.localStorage.getItem(key);
      if (!raw) return null;
      const record = {items: JSON.parse(raw), ts: Number(root.localStorage.getItem(`${key}_ts`) || 0)};
      if (!Array.isArray(record.items)) return null;
      await catalogSet(sid, record.items, record.ts);
      return record;
    } catch { return null; }
  }
  async function catalogSet(sid, items, ts = Date.now()) {
    const key = `catalog_${sid}`;
    try {
      await access('cache', key, {items, ts});
      // Remove legacy cache only after the IndexedDB transaction committed.
      try { root.localStorage.removeItem(key); root.localStorage.removeItem(`${key}_ts`); } catch {}
      const db = await open();
      await new Promise((resolve, reject) => {
        const tx = db.transaction('cache', 'readwrite');
        const cursor = tx.objectStore('cache').openCursor();
        cursor.onsuccess = () => {
          const row = cursor.result;
          if (!row) return;
          if (row.key !== key && Date.now() - Number(row.value.ts || row.value.at || 0) > 7 * 86400000) row.delete();
          row.continue();
        };
        tx.oncomplete = resolve; tx.onerror = tx.onabort = () => reject(tx.error);
      });
      return true;
    } catch { return false; } // Catalog still works in memory; no large localStorage fallback.
  }
  const cache = {
    async get(key) {
      try {
        const saved = await access('cache', key);
        if (saved) return saved;
        const legacy = JSON.parse(root.localStorage.getItem(key) || 'null');
        if (legacy && await cache.set(key, legacy)) return legacy;
      } catch {}
      return null;
    },
    async set(key, value) {
      try {
        await access('cache', key, value);
        try { root.localStorage.removeItem(key); } catch {}
        return true;
      } catch { return false; }
    },
  };
  root.NovaStorage = {safeSet, cleanCache, durable, cache, catalogGet, catalogSet, reference};
  if (typeof module !== 'undefined') module.exports = root.NovaStorage;
})(typeof window !== 'undefined' ? window : globalThis);
