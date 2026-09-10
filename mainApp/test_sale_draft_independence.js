// node --test mainApp/test_sale_draft_independence.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const createLifecycle = require("./static/javascript/sale_draft_lifecycle.js");

// Semántica de Web Locks: adquisición exclusiva, ifAvailable y espera por dueño.
class BrowserLocks {
  held = new Map();
  pending = [];
  request(name, options, action) {
    if (typeof options === "function") { action = options; options = {}; }
    if (this.held.has(name)) {
      if (options.ifAvailable) return Promise.resolve().then(() => action(null));
      return new Promise((resolve, reject) => this.pending.push({name, action, resolve, reject}));
    }
    const lock = {name};
    this.held.set(name, lock);
    return Promise.resolve().then(() => action(lock)).finally(() => {
      this.held.delete(name);
      const index = this.pending.findIndex(item => item.name === name);
      if (index >= 0) {
        const next = this.pending.splice(index, 1)[0];
        this.request(name, next.action).then(next.resolve, next.reject);
      }
    });
  }
  async query() { return {held: [...this.held.values()], pending: this.pending.map(({name}) => ({name}))}; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));

const script = fs.readFileSync(path.join(__dirname, "static/javascript/generar_venta.js"), "utf8");
const between = (start, end) => script.slice(script.indexOf(start), script.indexOf(end, script.indexOf(start)));
const draftCode = [
  between("function saleDraftStorageKey()", "function saleDraftStorageKeyForPoint("),
  between("function sanitizeDraftText(", "function captureSaleDraftItems()"),
  between("function clearSaleDraftForCurrentScope(", "function persistSaleDraftNow()"),
  between("function persistSaleDraftNow()", "function scheduleSaleDraftSave()"),
  between("function listManagedSaleDrafts()", "function renderSaleDraftManager()"),
  between("async function refreshSaleDraftAvailability()", "function continueWithNewSale()"),
  between("async function restorePendingSaleDraft(", "async function discardPendingSaleDraft("),
  between('window.addEventListener("storage",', 'if (!POS_AGENT_TOKEN) console.warn'),
].join("\n");
const scopeKey = "nova:venta-draft:v2:u1:s1:p1:t1";
const originalKey = `${scopeKey}:doriginal`;
const copy = value => JSON.parse(JSON.stringify(value));

function scenario({legacy = false, status = "active"} = {}) {
  const locks = new BrowserLocks();
  const sourceKey = legacy ? scopeKey : originalKey;
  const original = {
    version: 2, draft_id: legacy ? "" : "original", user_id: "1", sucursal_id: "1",
    puntopago_id: "1", turno_id: "1", owner_tab_id: "original-tab",
    presence_version: legacy ? 0 : 1,
    created_at: Date.now(), updated_at: Date.now(), status,
    items: [{producto_id: "12", nombre: "ARROZ", cantidad: 2, precio: 1500, codigo_barras: "77001"}],
    client: {id: "25", name: "CLIENTE"},
    payment: {mixed: false, methods: [{code: "nequi", amount: 3000}]},
  };
  const storage = new Map([[sourceKey, JSON.stringify(original)]]);
  const tabs = [];
  const events = [];
  function publish(sender, key, value) {
    const oldValue = storage.get(key) || null;
    if (value === null) storage.delete(key);
    else storage.set(key, value);
    if (oldValue !== value) {
      for (const tab of tabs) if (tab !== sender) events.push(() => tab.onStorage?.({key, oldValue, newValue: value}));
    }
  }
  function flush() { while (events.length) events.shift()(); }

  function tab(name) {
    const elements = new Map();
    const alerts = [];
    const confirmations = [];
    const revalidations = [];
    const rows = new Map();
    let id = 0;
    function element(selector) {
      if (!elements.has(selector)) elements.set(selector, {
        length: 1, current: "",
        val(value) { if (value === undefined) return this.current; this.current = String(value); return this; },
        text(value) { if (value === undefined) return this.current; this.current = String(value); return this; },
        first() { return this; }, find() { return this; },
      });
      return elements.get(selector);
    }
    const context = {
      SALE_DRAFT_VERSION: 2, SALE_DRAFT_MAX_ITEMS: 500, SALE_DRAFT_TTL_MS: 86400000,
      saleDraftTabID: name, saleDraftActiveID: `${name}-cart`, saleDraftAutosaveReady: true,
      saleDraftRestoring: false, saleDraftOwnershipLost: false, saleDraftSaleConfirmed: false,
      saleDraftPaymentState: null, saleDraftSubmissionPending: false, saleDraftSubmittedKey: "",
      saleDraftLastStorageKey: "", saleDraftSaveTimer: null, saleDraftValidationPending: false,
      saleDraftValidationError: "", saleDraftInvalidProductIds: new Set(), saleDraftManagerSignature: "",
      saleDraftPageHidden: false, saleDraftRecoveryBusy: false,
      saleDraftAvailabilityRefreshing: false, saleDraftAvailabilityAgain: false,
      saleDraftRecoverableKeys: new Set(), saleDraftRecoveredFrom: [],
      saleDraftLifecycle: createLifecycle({locks, tabID: name}),
      pendingSaleDraft: null, saleSubmitting: false, confirmSubmitting: false,
      selectedClientLabel: "", selectedEmployeeClient: {}, lastAddedPid: null,
      productos: [], shouldConfirm: true, failSaving: false,
      createSaleDraftID: () => `${name}-copy-${++id}`,
      saleDraftScopeStorageKey: () => scopeKey,
      saleDraftScope: () => ({userId: "1", sucursalId: "1", puntoPagoId: "1", turnoId: "1"}),
      saleDraftIDFromStorageKey: key => key.startsWith(`${scopeKey}:d`) ? key.slice(`${scopeKey}:d`.length) : "",
      isSaleDraftKeyForCurrentScope: key => key === scopeKey || String(key).startsWith(`${scopeKey}:d`),
      to2: value => Number(value).toFixed(2),
      $: element, $inpCliente: element("client"),
      $hidEmpleadoPassword: element("employee-secret"), $hidMerk2888Password: element("special-secret"),
      $hidNequiNotification: element("nequi-link"),
      $tbody: {find: selector => element(selector)},
      captureSaleDraftItems: () => context.productos.map(pid => ({
        ...copy(rows.get(pid)), nombre: element(`tr[data-pid='${pid}']`).current || rows.get(pid).nombre,
      })),
      captureSaleDraftClient: () => element("#cliente_id").val() ? {
        id: element("#cliente_id").val(), name: context.$inpCliente.val(),
      } : null,
      captureSaleDraftPayment: () => null,
      clearCartAndTotals() { context.productos.length = 0; rows.clear(); context.saleDraftPaymentState = null; },
      insertOrUpdateRowInstant(pid, cantidad, nombre, precio) {
        context.productos.push(String(pid));
        rows.set(String(pid), {producto_id: String(pid), cantidad, nombre, precio, codigo_barras: ""});
      },
      updateCache() {}, rememberCartAuditRow() {}, refreshEmployeeDiscountUI() {},
      enforceTotalIntegrity() {}, syncHiddenFieldsNow() {}, closeSaleDraftPanel() {},
      offerSaleDraftForCurrentScope() {}, renderSaleDraftManager() {},
      updateSaleDraftStatus(message, state) { context.lastStatus = {message, state}; },
      setSaleDraftValidation({pending = false, error = ""} = {}) {
        context.saleDraftValidationPending = pending;
        context.saleDraftValidationError = error;
      },
      revalidateRestoredSaleDraft: async draft => { revalidations.push(copy(draft)); },
      queueMicrotask: callback => callback(), clearTimeout() {},
      alert: message => alerts.push(message),
      confirm: message => { confirmations.push(message); return context.shouldConfirm; },
      window: {addEventListener: (name, callback) => { if (name === "storage") context.onStorage = callback; }},
    };
    context.localStorage = {
      get length() { return storage.size; },
      key: index => [...storage.keys()][index],
      getItem: key => storage.get(key) || null,
      setItem(key, value) {
        if (context.failSaving) throw new Error("QuotaExceededError");
        publish(context, key, value);
      },
      removeItem(key) {
        if (context.failRemoving === key) throw new Error("SecurityError");
        publish(context, key, null);
      },
    };
    context.removeSaleDraftByKey = key => {
      try { context.localStorage.removeItem(key); return true; } catch (_) { return false; }
    };
    vm.createContext(context);
    vm.runInContext(draftCode, context);
    tabs.push(context);
    context.ready = context.saleDraftLifecycle.start();
    return Object.assign(context, {
      alerts, confirmations, revalidations, rows, elements,
      recover: async (key = sourceKey) => {
        await context.ready;
        return context.restorePendingSaleDraft({storage_key: key});
      },
      managed: async () => {
        await context.ready;
        await context.refreshSaleDraftAvailability();
        return Array.from(context.listManagedSaleDrafts(), draft => draft.storage_key);
      },
      close: async () => { context.suspendSaleDraftPage(); await tick(); },
      current: () => JSON.parse(storage.get(context.saleDraftStorageKey()) || "null"),
    });
  }
  return {tab, locks, storage, sourceKey, original, flush, externalWrite: (key, value) => publish(null, key, value)};
}

test("recuperar agrega los productos bajo otra clave y consume el respaldo", async () => {
  const app = scenario();
  const tab = app.tab("A");
  tab.$hidEmpleadoPassword.val("old-secret");
  tab.$hidNequiNotification.val("old-nequi");
  await tab.recover();
  assert.notEqual(tab.saleDraftStorageKey(), app.sourceKey);
  assert.equal(app.storage.has(app.sourceKey), false);
  assert.equal(tab.current().owner_tab_id, "A");
  assert.equal(tab.current().items[0].producto_id, "12");
  assert.equal(tab.current().items[0].nombre, "ARROZ");
  assert.equal(tab.current().items[0].cantidad, 2);
  assert.equal(tab.current().client.id, "25");
  assert.equal(tab.current().payment, null);
  assert.equal(tab.$hidEmpleadoPassword.val(), "");
  assert.equal(tab.$hidNequiNotification.val(), "");
  assert.equal(tab.revalidations.length, 1);
  assert.deepEqual(await app.tab("B").managed(), []);
});

test("dos clics simultáneos solo permiten recuperar el respaldo una vez", async () => {
  const app = scenario();
  const tabs = ["A", "B", "C", "D"].map(name => app.tab(name));
  await Promise.all(tabs.map(tab => tab.recover()));
  app.flush();
  assert.equal(tabs.filter(tab => tab.productos.length).length, 1);
  assert.equal(app.storage.size, 1);
  for (const tab of tabs) {
    assert.equal(tab.saleDraftOwnershipLost, false);
    assert.equal(tab.saleDraftValidationError, "");
    assert.equal(tab.alerts.length, 0);
  }
});

test("facturar el carrito recuperado no deja ningún respaldo para recuperar otra vez", async () => {
  const app = scenario();
  const a = app.tab("A"); const b = app.tab("B");
  await a.recover(); app.flush();
  const aKey = a.saleDraftStorageKey();
  a.saleDraftSaleConfirmed = true;
  a.clearSaleDraftForCurrentScope(aKey);
  app.flush();
  assert.equal(app.storage.has(aKey), false);
  assert.equal(app.storage.has(app.sourceKey), false);
  await a.close();
  assert.deepEqual(await b.managed(), []);
  assert.equal(b.saleDraftValidationError, "");
});

test("cambiar o eliminar el origen viejo no afecta al carrito recuperado", async () => {
  const app = scenario(); const tab = app.tab("A");
  await tab.recover();
  app.externalWrite(app.sourceKey, JSON.stringify({...app.original, status: "submission_pending"}));
  app.flush();
  app.externalWrite(app.sourceKey, null); app.flush();
  assert.equal(tab.saleDraftOwnershipLost, false);
  assert.equal(tab.saleDraftValidationError, "");
  assert.equal(tab.current().items[0].cantidad, 2);
});

test("un respaldo antiguo exige confirmar que se cerró su pestaña", async () => {
  const app = scenario({legacy: true}); const tab = app.tab("A");
  tab.shouldConfirm = false;
  await tab.recover();
  assert.equal(tab.productos.length, 0);
  assert.match(tab.confirmations[0], /cerraste la pestaña/);
  tab.shouldConfirm = true;
  await tab.recover();
  assert.equal(tab.current().draft_id, "A-copy-1");
  assert.equal(app.storage.has(app.sourceKey), false);
});

test("el mismo respaldo desaparece después de recuperarlo y solo vuelve al cerrar", async () => {
  const app = scenario(); const tab = app.tab("A");
  const observer = app.tab("B");
  await tab.recover();
  const firstKey = tab.saleDraftStorageKey();
  assert.deepEqual(await observer.managed(), []);
  await observer.recover(firstKey); // ni con un botón/listado obsoleto
  assert.equal(observer.productos.length, 0);
  await tab.close();
  assert.deepEqual(await observer.managed(), [firstKey]);
  await observer.recover(firstKey);
  assert.notEqual(observer.saleDraftStorageKey(), firstKey);
  assert.equal(app.storage.has(firstKey), false);
  assert.deepEqual(await app.tab("C").managed(), []);
});

test("una pestaña duplicada no ofrece ni puede recuperar el carrito aún abierto", async () => {
  const app = scenario(); const tab = app.tab("A");
  app.storage.delete(app.sourceKey);
  await tab.ready;
  tab.insertOrUpdateRowInstant("99", 3, "PAN", 500);
  tab.persistSaleDraftNow();
  const firstKey = tab.saleDraftStorageKey();
  const duplicate = app.tab("DUPLICADA");
  assert.deepEqual(await duplicate.managed(), []);
  await duplicate.recover(firstKey);
  assert.equal(duplicate.productos.length, 0);
  assert.equal(app.storage.has(firstKey), true);
  assert.equal(tab.current().owner_tab_id, "A");
});

test("recuperar no mezcla ni manda a la reserva una venta que sigue abierta", async () => {
  const app = scenario(); const tab = app.tab("A");
  const oldKey = tab.saleDraftStorageKey();
  tab.insertOrUpdateRowInstant("99", 3, "PAN", 500);
  await tab.recover();
  assert.deepEqual(Array.from(tab.productos), ["99"]);
  assert.equal(tab.saleDraftStorageKey(), oldKey);
  assert.equal(app.storage.has(app.sourceKey), true);
});

test("si no se puede guardar la venta actual no se reemplaza ni se pierde", async () => {
  const app = scenario(); const tab = app.tab("A");
  const oldKey = tab.saleDraftStorageKey();
  tab.insertOrUpdateRowInstant("99", 3, "PAN", 500);
  tab.failSaving = true;
  await tab.recover();
  assert.equal(tab.saleDraftStorageKey(), oldKey);
  assert.deepEqual(Array.from(tab.productos), ["99"]);
  assert.equal(app.storage.has(app.sourceKey), true);
  assert.equal(tab.alerts.length, 1);
});

test("fallar al guardar la copia no consume el original ni permite cobrar la copia", async () => {
  const app = scenario(); const tab = app.tab("A");
  tab.failSaving = true;
  await tab.recover();
  assert.equal(app.storage.has(app.sourceKey), true);
  assert.deepEqual(Array.from(tab.productos), []);
  assert.equal(tab.revalidations.length, 0);
});

test("una confirmación de cobro pendiente exige revisión explícita", async () => {
  const app = scenario({status: "submission_pending"}); const tab = app.tab("A");
  tab.shouldConfirm = false; await tab.recover();
  assert.equal(tab.productos.length, 0);
  assert.match(tab.confirmations[0], /NO está facturada/);
  tab.shouldConfirm = true; await tab.recover();
  assert.equal(tab.current().status, "active");
  assert.equal(tab.current().payment, null);
  assert.equal(app.storage.has(app.sourceKey), false);
});

test("no se cambia de carrito mientras se cobra o se valida una recuperación", async () => {
  for (const flag of ["saleSubmitting", "confirmSubmitting", "saleDraftSaleConfirmed", "saleDraftRestoring", "saleDraftValidationPending"]) {
    const app = scenario(); const tab = app.tab("A");
    const oldKey = tab.saleDraftStorageKey();
    tab[flag] = true; await tab.recover();
    assert.equal(tab.productos.length, 0);
    assert.equal(tab.saleDraftStorageKey(), oldKey);
  }
});

test("se consulta el estado más reciente y no se restaura un borrador eliminado", async () => {
  const app = scenario(); const tab = app.tab("A");
  app.storage.delete(app.sourceKey); await tab.recover();
  assert.equal(tab.productos.length, 0);
  assert.equal(app.storage.size, 0);
});

test("si no se puede retirar el origen, la recuperación se revierte sin duplicarlo", async () => {
  const app = scenario(); const tab = app.tab("A");
  tab.failRemoving = app.sourceKey;
  await tab.recover();
  assert.deepEqual(Array.from(tab.productos), []);
  assert.equal(app.storage.size, 1);
  assert.equal(app.storage.has(app.sourceKey), true);
});

test("cuatro pestañas cerradas permiten recuperar sus cuatro ventas por separado", async () => {
  const app = scenario(); app.storage.clear();
  const tabs = ["A", "B", "C", "D"].map(name => app.tab(name));
  await Promise.all(tabs.map(tab => tab.ready));
  tabs.forEach((tab, index) => {
    tab.insertOrUpdateRowInstant(String(index + 1), index + 2, "PRODUCTO", 500);
    tab.persistSaleDraftNow();
  });
  const observer = app.tab("E");
  assert.deepEqual(await observer.managed(), []);
  await Promise.all(tabs.map(tab => tab.close()));
  const keys = await observer.managed();
  assert.equal(keys.length, 4);
  const receivers = ["E", "F", "G", "H"].map(name => name === "E" ? observer : app.tab(name));
  await Promise.all(receivers.map((tab, index) => tab.recover(keys[index])));
  assert.equal(receivers.filter(tab => tab.productos.length === 1).length, 4);
  assert.deepEqual(await app.tab("I").managed(), []);
});

test("volver con Atrás no revive una venta que ya se recuperó en otra pestaña", async () => {
  const app = scenario(); const oldTab = app.tab("A");
  await oldTab.recover();
  const oldKey = oldTab.saleDraftStorageKey();
  await oldTab.close();
  const nextTab = app.tab("B");
  await nextTab.recover(oldKey);
  app.flush();
  await oldTab.resumeSaleDraftPage();
  assert.equal(oldTab.productos.length, 0);
  assert.equal(oldTab.saleDraftOwnershipLost, false);
  assert.equal(oldTab.saleDraftValidationError, "");
  assert.equal(app.storage.has(oldKey), false);
  assert.equal(nextTab.current().items.length, 1);
});

test("si se interrumpe una transferencia con el destino guardado, no ofrece dos respaldos", async () => {
  const app = scenario(); const a = app.tab("A");
  await a.recover();
  await a.close();
  app.storage.set(app.sourceKey, JSON.stringify(app.original)); // ventana entre las dos escrituras
  const observer = app.tab("B");
  assert.deepEqual(await observer.managed(), [a.saleDraftStorageKey()]);
  await observer.recover(app.sourceKey);
  assert.equal(observer.productos.length, 0);
  await observer.recover(a.saleDraftStorageKey());
  assert.equal(app.storage.has(app.sourceKey), false);
  await observer.close();
  assert.deepEqual(await app.tab("C").managed(), [observer.saleDraftStorageKey()]);
});

test("sin comprobación segura del navegador no se recupera a ciegas", async () => {
  const app = scenario(); const a = app.tab("A");
  await a.ready;
  a.saleDraftLifecycle.stop();
  a.saleDraftLifecycle = createLifecycle({locks: null, tabID: "A"});
  await a.recover();
  assert.equal(a.productos.length, 0);
  assert.equal(app.storage.has(app.sourceKey), true);
  assert.match(a.alerts[0], /HTTPS o localhost/);
});

test("la señal de vida no caduca por tiempo ni por estar en segundo plano", async () => {
  const locks = new BrowserLocks();
  const life = createLifecycle({locks, tabID: "A"});
  assert.equal(await life.start(), true);
  assert.equal((await life.openOwners()).has("A"), true);
  assert.equal(await life.withClosedDraft({owner_tab_id: "A", storage_key: "key"}, () => true), false);
  life.stop(); await tick();
  assert.equal((await life.openOwners()).has("A"), false);
});
