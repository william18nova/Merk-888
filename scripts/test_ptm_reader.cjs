const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const folder = path.join(__dirname, '../browser_extensions/ptm_reader');
const parser = require(path.join(folder, 'parser.js'));

test('Colombian amounts preserve precision and signs', () => {
  assert.equal(parser.money('100.000,00'), '100000.00');
  assert.equal(parser.money('-50.000,25'), '-50000.25');
  assert.equal(parser.money('0,00'), '0.00');
  for (const s of ['50.00', '1,000.00', 'NaN', '50', '1.00,00']) assert.throws(() => parser.money(s));
});
function row(id = 'TX-DEMO-001') {
  return [id, '2026/10/14 12:00:00', '', 'Bolsa PTM', 'NEQUI - RECARGAS', 'PRIVATE-PHONE',
    '100.000,00', 'Exitosa', 'Aplicación Web', 'PRIVATE-IP', 'M-DEMO-001', 'DEMO', 'PRIVATE-ERROR'];
}
test('Parser drops contract, terminal and error details', () => {
  const parsed = parser.parse(parser.headers, [row('900719925474099312345')]);
  assert.equal(parsed[0].id_tx, '900719925474099312345');
  assert.equal(parsed[0].valor, '100000.00');
  assert.equal(JSON.stringify(parsed).includes('PRIVATE'), false);
});
test('Changing header order or shape stops collection', () => {
  assert.throws(() => parser.parse([...parser.headers].reverse(), [row()]));
  assert.throws(() => parser.parse(parser.headers, [row().slice(1)]));
  assert.deepEqual(parser.parse(parser.headers, [['Ningún dato disponible en esta tabla']]), []);
});
test('Endpoint validation requires HTTPS or loopback, no PTM or credential URL', () => {
  assert.equal(parser.endpoint('https://pos.example.test/'), 'https://pos.example.test');
  assert.equal(parser.endpoint('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000');
  for (const url of ['http://example.test', 'https://ptm.transacciones.com.co', 'https://a:b@example.test', 'https://example.test/private', 'https://example.test/?token=x']) assert.throws(() => parser.endpoint(url));
});

function worker() {
  const local = {config: {origin: 'https://pos.example.test', token: 'test-token', enabled: true}}, session = {};
  const sent = [], tabs = new Map();
  let fail = false;
  const storage = data => ({get: async keys => Object.fromEntries((typeof keys === 'string' ? [keys] : keys).map(k => [k, data[k] === undefined ? undefined : structuredClone(data[k])])),
    set: async values => Object.assign(data, structuredClone(values)), remove: async keys => { for (const k of (Array.isArray(keys) ? keys : [keys])) delete data[k]; }, setAccessLevel: async () => {}});
  const chrome = {runtime: {id: 'test-id', getURL: p => 'chrome-extension://test-id/' + p,
    onMessage: {addListener() {}}, onStartup: {addListener() {}}, openOptionsPage() {}},
    storage: {local: storage(local), session: storage(session)},
    alarms: {get: async () => ({}), create: async () => {}, onAlarm: {addListener() {}}},
    action: {onClicked: {addListener() {}}}, tabs: {
      create: async data => { const tab = {id: tabs.size + 1, ...data}; tabs.set(tab.id, tab); return tab; },
      get: async id => tabs.get(id), update: async (id, data) => Object.assign(tabs.get(id), data)
    }};
  const ctx = vm.createContext({chrome, console, URL, Date, Intl, AbortSignal, NovaPTMParser: parser,
    importScripts() {}, fetch: async (url, request) => {
      if (fail) throw Error('network down');
      sent.push({url, request, payload: JSON.parse(request.body)});
      return {ok: true, json: async () => ({ok: true})};
    }});
  vm.runInContext(fs.readFileSync(path.join(folder, 'background.js'), 'utf8'), ctx);
  const query = 'https://ptm.transacciones.com.co/DistribuidoresTFCC/controller/FCConsultaTransacciones';
  function sender(url = query, tabId = session.readerTab, frameId = 5) { return {id: 'test-id', tab: {id: tabId}, frameId, url}; }
  return {ctx, local, session, sent, tabs, sender, query, setFail(value) { fail = value; }};
}
test('Reader only drives its own tab and follows the home frame', async () => {
  const w = worker(); await w.ctx.begin();
  assert.equal(w.tabs.get(w.session.readerTab).url.endsWith('/FCInicio'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(await w.ctx.onMessage({type: 'ready'}, w.sender(w.query, 999)))), {});
  const query = await w.ctx.onMessage({type: 'ready'}, w.sender());
  assert.equal(query.action, 'query');
  assert.equal(w.session.job.frameId, 5);
});
test('Network failure keeps durable pending batch, replay does not transmit elsewhere', async () => {
  const w = worker(); await w.ctx.begin();
  await w.ctx.onMessage({type: 'ready'}, w.sender());
  const date = w.session.job.date;
  w.setFail(true);
  await assert.rejects(w.ctx.onMessage({type: 'batch', date, rows: parser.parse(parser.headers, [row()])}, w.sender(w.query + '.do.consultar')));
  assert.equal(w.local.pending.filas.length, 1);
  w.setFail(false); await w.ctx.flush();
  assert.equal(w.local.pending, undefined);
  assert.equal(w.sent[0].url, 'https://pos.example.test/api/ptm/lector/');
  assert.equal(w.sent[0].request.redirect, 'error');
  assert.equal(w.sent[0].request.credentials, 'omit');
});
test('No successful report acknowledgement for partial or wrong frame reads', async () => {
  const w = worker(); await w.ctx.begin(); await w.ctx.onMessage({type:'ready'}, w.sender());
  const date = w.session.job.date;
  await w.ctx.onMessage({type:'batch', date, rows: []}, w.sender(w.query + '.do.consultar', w.session.readerTab, 999));
  assert.equal(w.sent.length, 0);
  await assert.rejects(w.ctx.onMessage({type:'complete', date, count: 1}, w.sender(w.query + '.do.consultar')));
  assert.equal(w.sent.length, 0);
});
test('Long interruption is marked for recovery, no unbounded polling', () => {
  const w = worker();
  assert.equal(w.ctx.daysSince('2000-01-01').gap, true);
  assert.equal(w.ctx.daysSince('2000-01-01').days.length, 1);
  assert.equal(w.ctx.daysSince(null).days.length, 2);
});
test('Overlapping alarms do not create multiple reader tabs', async () => {
  const w = worker(); await w.ctx.begin(); await w.ctx.begin();
  assert.equal(w.tabs.size, 1);
});
test('Expired session does not overwrite login form', async () => {
  const w = worker(); await w.ctx.begin();
  w.session.job.started = 0;
  w.tabs.get(w.session.readerTab).url = 'https://ptm.transacciones.com.co/seguridad/FCSalida';
  await w.ctx.begin();
  assert.equal(w.tabs.size, 1);
  assert.equal(w.tabs.get(w.session.readerTab).url.includes('/seguridad/'), true);
  assert.match(w.local.status.message, /sesión manualmente/);
});
test('Configuration cannot reassign an in-flight read to another account', async () => {
  const w = worker(); await w.ctx.begin();
  const options = {id:'test-id', url:'chrome-extension://test-id/options.html'};
  await assert.rejects(w.ctx.onMessage({type:'configure', config:{origin:'https://another.example.test',
    token:'00000000-0000-0000-0000-000000000000.' + 'a'.repeat(43), enabled:true}}, options));
  assert.equal(w.local.config.origin, 'https://pos.example.test');
});
test('Only extension options can configure destination', async () => {
  const w = worker(); await w.ctx.begin();
  await w.ctx.onMessage({type:'configure', config:{origin:'https://another.example.test', token:'x'}}, w.sender());
  assert.equal(w.local.config.origin, 'https://pos.example.test');
});

async function content({total = 205, failAt = -1, duplicate = false, changedHeaders = false, action = 'collect'} = {}) {
  const calls = []; let start = 0, size = 10, clicks = 0;
  const date = '2026-10-14';
  const current = () => Array.from({length: Math.min(size, total - start)}, (_, i) => row('TX-' + (duplicate ? 'SAME' : start + i)));
  const doc = {querySelector(selector) {
    if (selector === 'form#formConsultaTX') return {getAttribute: () => 'FCConsultaTransacciones'};
    if (selector === '#fechaTransaccion') return {value: '2026/10/14'};
    if (selector === '.dataTables_info') return {innerText: `Mostrando registros del ${total ? start + 1 : 0} al ${Math.min(start + size, total)} de un total de ${total} registros`};
    if (selector === 'select[name="DataTables_Table_0_length"]') return {options: [{value:'100'}], set value(v) { size = +v; }, dispatchEvent() {}};
    if (selector === '#DataTables_Table_0') return {querySelectorAll(q) {
      if (q === 'thead th') return (changedHeaders ? [] : parser.headers).map(innerText => ({innerText}));
      return current().map(cells => ({cells: cells.map(innerText => ({innerText}))}));
    }};
    if (selector === '#DataTables_Table_0_next') return {classList: {contains: () => false}, querySelector: () => ({click() { start += size; clicks++; }})};
    throw Error('Unexpected selector: ' + selector);
  }};
  const ctx = vm.createContext({document: doc, NovaPTMParser: parser, Event: class {}, setTimeout,
    chrome: {runtime: {sendMessage: async m => {
      calls.push(m);
      if (m.type === 'ready') return action ? {action, date} : {};
      if (m.type === 'batch' && calls.filter(c => c.type === 'batch').length === failAt) return {ok:false};
      return {ok:true};
    }}}});
  await vm.runInContext(fs.readFileSync(path.join(folder, 'content.js'), 'utf8'), ctx);
  return {calls, clicks};
}
test('Content collects all 205 rows across three pages', async () => {
  const {calls, clicks} = await content();
  assert.equal(calls.filter(c => c.type === 'batch').length, 3);
  assert.equal(calls.find(c => c.type === 'complete').count, 205);
  assert.equal(clicks, 2);
});
test('Empty report is explicitly complete', async () => {
  const {calls} = await content({total:0});
  assert.equal(calls.find(c => c.type === 'complete').count, 0);
});
test('Mid-pagination interruption never reports completion', async () => {
  const {calls} = await content({failAt:2});
  assert.equal(calls.some(c => c.type === 'complete'), false);
  assert.equal(calls.at(-1).type, 'error');
});
test('Duplicate IDs and changed report format fail closed', async () => {
  for (const options of [{duplicate:true}, {changedHeaders:true}]) {
    const {calls} = await content(options);
    assert.equal(calls.some(c => c.type === 'batch'), false);
    assert.equal(calls.at(-1).error, 'estructura');
  }
});
test('Regular user PTM tab is never operated', async () => {
  const {calls, clicks} = await content({action:null});
  assert.equal(calls.length, 1);
  assert.equal(clicks, 0);
});
