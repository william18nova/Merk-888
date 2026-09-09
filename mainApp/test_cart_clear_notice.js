// Ejecutar: node --test mainApp/test_cart_clear_notice.js
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "static/javascript/generar_venta.js"), "utf8");
const functions = script.slice(
  script.indexOf("function showCartClearAuditNotice("),
  script.indexOf("function removeRowByPidWithAuditIfEmpty("),
);

function setup(extra = {}) {
  const notice = { hidden: true, warning: false, classList: { toggle(_name, value) { notice.warning = value; } } };
  const message = { textContent: "" };
  const requests = [];
  let now = 0;
  let nextTimer = 1;
  const timers = new Map();
  const advance = ms => {
    now += ms;
    for (const [id, timer] of [...timers]) {
      if (timer.due <= now) {
        timers.delete(id);
        timer.callback();
      }
    }
  };
  const context = {
    setTimeout: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, due: now + delay});
      return id;
    },
    clearTimeout: id => timers.delete(id),
    document: { getElementById: id => id === "venta-carrito-audit-notice" ? notice : message },
    CARRITO_LIMPIO_AUDIT_DISABLED: false,
    CARRITO_LIMPIO_AUDIT_URL: "/ventas/auditoria/carrito-limpiado/",
    getCSRF: () => "test-csrf",
    navigator: {}, window: {},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({success: true, daily_count: 1, audit_day: "2026-09-09"})};
    },
    ...extra,
  };
  vm.createContext(context);
  vm.runInContext(`let cartClearNoticeDay = ""; let cartClearNoticeCount = 0; let cartClearNoticeTimer = null; ${functions}`, context);
  return {notice, message, requests, advance, ...context};
}

test("el aviso usa el conteo del servidor sin bloquear ni mover el foco", async () => {
  const app = setup();
  await app.sendCartClearAudit({items: [{producto_id: 1}]});
  assert.equal(app.notice.hidden, false);
  assert.equal(app.message.textContent, "Venta no facturada #1 del día");
  assert.equal(JSON.parse(app.requests[0].options.body).motivo, "carrito_vaciado");
  assert.equal(app.requests[0].options.headers["X-CSRFToken"], "test-csrf");
});

test("las respuestas lentas no retroceden el conteo y el día siguiente empieza en uno", () => {
  const app = setup();
  const show = (daily_count, audit_day) => app.showCartClearAuditNotice({success: true, daily_count, audit_day});
  show(3, "2026-09-09");
  show(2, "2026-09-09");
  assert.equal(app.message.textContent, "Venta no facturada #3 del día");
  show(1, "2026-09-10");
  show(4, "2026-09-09");
  assert.equal(app.message.textContent, "Venta no facturada #1 del día");
});

test("la notificación desaparece a los dos segundos", async () => {
  const app = setup();
  await app.sendCartClearAudit({items: [{producto_id: 1}]});
  app.advance(1999);
  assert.equal(app.notice.hidden, false);
  app.advance(1);
  assert.equal(app.notice.hidden, true);
  assert.equal(app.message.textContent, "");
});

test("un nuevo vaciado tiene sus propios dos segundos de lectura", () => {
  const app = setup();
  app.showCartClearAuditNotice({success: true, daily_count: 1, audit_day: "2026-09-09"});
  app.advance(1500);
  app.showCartClearAuditNotice({success: true, daily_count: 2, audit_day: "2026-09-09"});
  app.advance(500);
  assert.equal(app.notice.hidden, false);
  assert.equal(app.message.textContent, "Venta no facturada #2 del día");
  app.advance(1500);
  assert.equal(app.notice.hidden, true);
});

test("los fallos de red y servidor no inventan un número de venta", async () => {
  for (const fetch of [async () => { throw new Error("offline"); }, async () => ({ok: false}),
                       async () => ({ok: true, json: async () => ({success: false})})]) {
    const app = setup({fetch});
    await app.sendCartClearAudit({items: [{producto_id: 1}]});
    assert.equal(app.notice.hidden, false);
    assert.equal(app.notice.warning, true);
    assert.match(app.message.textContent, /No se pudo confirmar el conteo diario/);
    assert.doesNotMatch(app.message.textContent, /Venta no facturada #/);
  }
});

test("no se envían carritos vacíos ni carritos de Web Master", async () => {
  const normal = setup();
  await normal.sendCartClearAudit({items: []});
  assert.equal(normal.requests.length, 0);
  const master = setup({CARRITO_LIMPIO_AUDIT_DISABLED: true});
  await master.sendCartClearAudit({items: [{producto_id: 1}]});
  assert.equal(master.requests.length, 0);
  assert.equal(master.notice.hidden, true);
});

test("un cierre de pestaña no muestra el aviso de carrito vaciado", async () => {
  const app = setup();
  await app.sendCartClearAudit({items: [{producto_id: 1}]}, {beacon: true, keepalive: true});
  assert.equal(JSON.parse(app.requests[0].options.body).motivo, "cierre_sin_borrador");
  assert.equal(app.notice.hidden, true);
});

test("ignora respuestas sin conteo válido o excluidas", () => {
  for (const result of [null, {success: true, ignored: true},
                        {success: true, daily_count: 0, audit_day: "2026-09-09"},
                        {success: true, daily_count: "<script>", audit_day: "2026-09-09"}]) {
    const app = setup();
    app.showCartClearAuditNotice(result);
    assert.equal(app.notice.hidden, true);
  }
});

test("eliminar productos uno por uno solo audita al quitar el último", () => {
  const calls = [];
  const context = {
    productos: ["1", "2"],
    buildCartClearAuditPayload: () => ({items: [{producto_id: 1}, {producto_id: 2}]}),
    removeRowByPid(pid) {
      const idx = this.productos.indexOf(pid);
      if (idx < 0) return false;
      this.productos.splice(idx, 1);
      return true;
    },
    sendCartClearAudit: payload => calls.push(payload),
    resetCartAuditSession: () => {},
  };
  // El mock mantiene el mismo estado del carrito usado por la función real.
  context.removeRowByPid = context.removeRowByPid.bind(context);
  vm.createContext(context);
  vm.runInContext(script.slice(script.indexOf("function removeRowByPidWithAuditIfEmpty("),
                              script.indexOf("function clearCartAndTotals(")), context);
  context.removeRowByPidWithAuditIfEmpty("1");
  assert.equal(calls.length, 0);
  context.removeRowByPidWithAuditIfEmpty("2");
  assert.equal(calls.length, 1);
  context.removeRowByPidWithAuditIfEmpty("2");
  assert.equal(calls.length, 1);
});
