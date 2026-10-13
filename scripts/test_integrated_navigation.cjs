const {test} = require('node:test');
const assert = require('node:assert/strict');
const {observeFetch} = require('../mainApp/static/javascript/integrated_navigation.js');
const fs = require('node:fs');
const vm = require('node:vm');

function browser(result) {
  const listeners = {}, requests = [], destinations = [], message = {textContent: ''};
  const dialog = {open: false, querySelector: () => message, showModal() {this.open = true;}};
  const document = {currentScript: {dataset: {statusUrl: '/local/disponibilidad/'}},
    getElementById: () => dialog, body: {classList: {remove() {}}},
    addEventListener: (name, handler) => {listeners[name] = handler;}};
  const window = {fetch: async (...args) => {requests.push(args); return Response.json(result);}};
  function XHR() {}
  XHR.prototype.send = () => {};
  const location = {href: 'http://localhost:8000/generar_venta/', origin: 'http://localhost:8000',
    assign: value => destinations.push(value)};
  vm.runInNewContext(fs.readFileSync(require.resolve('../mainApp/static/javascript/integrated_navigation.js'), 'utf8'),
    {window, document, location, XMLHttpRequest: XHR, URL, AbortController, setTimeout, clearTimeout});
  const anchor = {href: 'http://localhost:8000/agregar_producto/', target: '',
    hasAttribute: () => false, getAttribute: () => '/agregar_producto/'};
  const event = {isTrusted: true, button: 0, target: {closest: selector => selector === '[data-offline-retry]' ? null : anchor},
    preventDefault() {this.defaultPrevented = true;}, stopImmediatePropagation() {}};
  return {listeners, requests, destinations, dialog, message, event};
}

test('blocked response shows an explanation without retrying or consuming the response', async () => {
  let calls = 0, notices = 0;
  const response = new Response('{"success":false}', {status: 503, headers: {'X-Nova-Offline-Blocked': '1'}});
  const options = {method: 'POST', body: 'unchanged', headers: {'X-Nova-Operation-ID': 'same-reference'}};
  const fetch = observeFetch(async (url, received) => {calls++; assert.equal(received, options); return response;},
    message => {notices++; assert.match(message, /Estamos offline/);});
  assert.equal(await fetch('/agregar_producto/', options), response);
  assert.equal(calls, 1);
  assert.equal(notices, 1);
  assert.equal(response.bodyUsed, false);
});
test('success and network errors preserve their original behavior', async () => {
  let notices = 0;
  const response = new Response('{}');
  assert.equal(await observeFetch(async () => response, () => notices++)('/'), response);
  const error = new Error('disconnected');
  await assert.rejects(observeFetch(async () => {throw error;}, () => notices++)('/'), error);
  assert.equal(notices, 0);
});
test('unavailable navbar option opens dialog and does not leave current cart page', async () => {
  const page = browser({allowed: false, offline: true, message: 'Estamos offline'});
  await page.listeners.click(page.event);
  assert.equal(page.event.defaultPrevented, true);
  assert.equal(page.dialog.open, true);
  assert.equal(page.message.textContent, 'Estamos offline');
  assert.deepEqual(page.destinations, []);
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0][1].method, undefined); // Read-only GET, never a POST.
});
test('available option navigates after server check; new-tab gestures remain native', async () => {
  const page = browser({allowed: true, offline: false});
  await page.listeners.click(page.event);
  assert.deepEqual(page.destinations, ['http://localhost:8000/agregar_producto/']);
  assert.equal(page.dialog.open, false);
  const newTab = browser({allowed: false});
  newTab.event.ctrlKey = true;
  await newTab.listeners.click(newTab.event);
  assert.equal(newTab.event.defaultPrevented, undefined);
  assert.deepEqual(newTab.requests, []);
});
