"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {createTransport} = require("../mainApp/static/javascript/integrated_sale_transport.js");

const uuid = "21912c30-83ae-4a13-8a52-2396260f00c7";
function setup(send, storage = new Map(), options = {}) {
  const persist = storage instanceof Map ? {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)} : storage;
  return {storage, transport: createTransport({storage: persist, randomUUID: () => uuid, fetchRequest: send, userId: "test-user", ...options})};
}

test("keeps ALL original fields, including linked Nequi and employee authorization", async () => {
  const form = new URLSearchParams({productos: "[7]", cantidades: "[500]", cliente_id: "2",
    nequi_notificacion_id: "5", empleado_password: "not-persisted", codigo_descuento_merk2888: "not-persisted-either"});
  const {transport, storage} = setup(async (url, request) => {
    assert.equal(storage.size, 1, "identity must be durable before the first send");
    assert.equal(url, "/generar_venta/");
    assert.equal(request.body, form);
    assert.equal(request.credentials, "same-origin");
    assert.equal(request.headers["X-Nova-Operation-ID"], uuid);
    assert.equal(request.headers["X-CSRFToken"], "csrf");
    return {ok: true, json: async () => ({success: true, venta_id: 8})};
  });
  assert.equal((await transport.submit(form, "draft-1", 4000, "/generar_venta/", "csrf")).venta_id, 8);
  assert.deepEqual([...storage.values()], [uuid], "no passwords, form data or tokens in storage");
});

test("a lost response is not automatically retried and a page restart uses the same UUID", async () => {
  const storage = new Map();
  let attempts = 0;
  const {transport} = setup(async () => { attempts++; throw new Error("timeout"); }, storage);
  await assert.rejects(transport.submit(new URLSearchParams(), "draft-1", 1, "/", ""), /misma referencia/);
  assert.equal(attempts, 1);
  const restarted = setup(async (_, request) => {
    assert.equal(request.headers["X-Nova-Operation-ID"], uuid);
    return {ok: true, json: async () => ({success: true})};
  }, storage);
  await restarted.transport.submit(new URLSearchParams(), "draft-1", 1, "/", "");
});

test("no submission if reference cannot be saved or has been corrupted", async () => {
  let sent = false;
  for (const storage of [
    {getItem: () => null, setItem: () => { throw new Error("storage denied"); }},
    {getItem: () => "not-a-uuid", setItem: () => {}},
    {getItem: () => null, setItem: () => {}},
  ]) {
    const {transport} = setup(async () => { sent = true; }, storage);
    await assert.rejects(transport.submit(new URLSearchParams(), "draft", 1, "/", ""));
  }
  assert.equal(sent, false);
});

test("does not hide a safe validation error or interpret HTML errors as success", async () => {
  const validation = setup(async () => ({ok: false, json: async () => ({success: false, error: "Revisa el pago"})}));
  assert.equal((await validation.transport.submit(new URLSearchParams(), "draft", 1, "/", "")).error, "Revisa el pago");
  const html = setup(async () => ({ok: false, json: async () => { throw new Error("html"); }}));
  await assert.rejects(html.transport.submit(new URLSearchParams(), "draft", 1, "/", ""), /no confirmó/);
});

test("lost POST response recovers with GET only and never sends a second charge", async () => {
  const methods = [];
  const {transport} = setup(async (url, request) => {
    methods.push(request.method);
    if (request.method === "POST") throw new Error("connection lost");
    assert.equal(url, `/local/ventas/confirmacion/${uuid}/`);
    return {ok: true, json: async () => ({status: "confirmed", response: {success: true, venta_id: 91}})};
  }, new Map(), {confirmationUrl: id => `/local/ventas/confirmacion/${id}/`, wait: async () => {}});
  assert.equal((await transport.submit(new URLSearchParams(), "draft", 1, "/generar_venta/", "csrf")).venta_id, 91);
  assert.deepEqual(methods, ["POST", "GET"]);
});

test("pending confirmation polling is bounded and malformed successes cannot clear cart", async () => {
  for (const status of ["pending", "confirmed"]) {
    let posts = 0, gets = 0;
    const {transport} = setup(async (_, request) => {
      if (request.method === "POST") { posts++; throw new Error("lost response"); }
      gets++;
      return {ok: true, json: async () => ({status, response: {success: true}})};
    }, new Map(), {confirmationUrl: () => "/confirmation/", wait: async () => {}});
    await assert.rejects(transport.submit(new URLSearchParams(), "draft", 1, "/", "csrf"));
    assert.equal(posts, 1);
    assert.ok(gets <= 3);
  }
});

test("confirmed absence reports no success and never automatically resubmits", async () => {
  let posts = 0;
  const {transport} = setup(async (_, request) => {
    if (request.method === "POST") { posts++; return {ok: false, json: async () => ({success: false, code: "sale_uncertain"})}; }
    return {ok: true, json: async () => ({status: "not_confirmed"})};
  }, new Map(), {confirmationUrl: () => "/confirmation/", wait: async () => {}});
  assert.equal((await transport.submit(new URLSearchParams(), "draft", 1, "/", "csrf")).success, false);
  assert.equal(posts, 1);
});

test("lost offline response recovers durable local UUID without another POST", async () => {
  let posts = 0;
  const saved = {success: true, offline_saved: true, operation_id: uuid, venta_id: `LOCAL-${uuid}`};
  const {transport} = setup(async (_, request) => {
    if (request.method === "POST") { posts++; throw new Error("lost local ACK"); }
    return {ok: true, json: async () => ({status: "saved_offline", response: saved})};
  }, new Map(), {confirmationUrl: () => "/confirmation/", wait: async () => {}});
  assert.deepEqual(await transport.submit(new URLSearchParams(), "draft", 1, "/", "csrf"), saved);
  assert.equal(posts, 1);
});

test("offline recovery refuses a ticket belonging to a different UUID", async () => {
  const {transport} = setup(async (_, request) => {
    if (request.method === "POST") throw new Error("lost local ACK");
    return {ok: true, json: async () => ({status: "saved_offline", response: {
      success: true, offline_saved: true, operation_id: "another", venta_id: "LOCAL-another"}})};
  }, new Map(), {confirmationUrl: () => "/confirmation/", wait: async () => {}});
  await assert.rejects(transport.submit(new URLSearchParams(), "draft", 1, "/", "csrf"));
});
