"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {responseStatus, createMonitor} = require("../mainApp/static/javascript/database_status.js");

function response(mode, code = 200, header = null) {
  return {ok: code === 200, status: code, headers: {get: () => header}, json: async () => ({mode})};
}
function fixture(fetch) {
  const rendered = [], scheduled = new Map();
  let next = 0, visible = true;
  const monitor = createMonitor({url: "/api/estado-conexion/", fetch, render: value => rendered.push(value),
    visible: () => visible, schedule: (fn, delay) => {scheduled.set(++next, {fn, delay}); return next;},
    cancel: id => scheduled.delete(id)});
  return {monitor, rendered, scheduled, hide: () => {visible = false;}};
}
test("server modes, not browser connectivity, determine the indicator", () => {
  for (const mode of ["online", "offline", "synchronizing", "blocked", "unavailable"]) {
    assert.equal(responseStatus(response(mode), {mode}).mode, mode);
  }
  assert.equal(responseStatus(response(), {mode: "invented"}).mode, "unknown");
  assert.equal(responseStatus(response(null, 503, "offline"), {}).mode, "unknown");
  assert.equal(responseStatus(response(null, 503, "blocked"), {}).mode, "blocked");
  assert.equal(responseStatus(response(null, 503, "unavailable"), {}).mode, "unavailable");
  assert.equal(responseStatus(response(null, 500, "online"), {}).mode, "unknown");
});
test("open page updates online -> local -> syncing -> online without a reload", async () => {
  const modes = ["online", "offline", "synchronizing", "online"];
  let calls = 0;
  const f = fixture(async (url, options) => {
    assert.equal(url, "/api/estado-conexion/");
    assert.equal(options.method, "GET");
    assert.equal(options.cache, "no-store");
    assert.equal(options.credentials, "same-origin");
    return response(modes[calls++]);
  });
  for (const mode of modes) {await f.monitor.refresh(); assert.equal(f.rendered.at(-1).mode, mode);}
  assert.equal(f.scheduled.size, 1);
  assert.equal([...f.scheduled.values()][0].delay, 15000);
});
test("server/network failure never claims local and retries less frequently", async () => {
  const f = fixture(async () => {throw new Error("network failure");});
  await f.monitor.refresh();
  assert.equal(f.rendered.at(-1).mode, "unknown");
  assert.equal([...f.scheduled.values()][0].delay, 30000);
});
test("hidden tabs do not poll; overlapping requests are prevented", async () => {
  let complete, count = 0;
  const f = fixture(() => {count++; return new Promise(resolve => {complete = resolve;});});
  const first = f.monitor.refresh();
  await f.monitor.refresh();
  assert.equal(count, 1);
  complete(response("online"));
  await first;
  f.hide();
  await f.monitor.refresh();
  assert.equal(count, 1);
  assert.equal(f.scheduled.size, 0);
});
test("expired session stops polling and HTML/login response is not online", async () => {
  const f = fixture(async () => response(null, 401));
  await f.monitor.refresh();
  assert.equal(f.rendered.at(-1).mode, "unknown");
  assert.match(f.rendered.at(-1).description, /sesión/);
  assert.equal(f.scheduled.size, 0);
  const html = fixture(async () => ({...response(), json: async () => {throw new Error("HTML");}}));
  await html.monitor.refresh();
  assert.equal(html.rendered.at(-1).mode, "unknown");
});
