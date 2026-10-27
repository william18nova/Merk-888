const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

test('quota real, migración de catálogo, respaldo, caída total y dos pestañas', async () => {
  const checkpoints = new Map();
  const server = http.createServer((req, res) => {
    const match = req.url.match(/^\/close\/(\d+)\//);
    if (match) {
      let body = '';
      req.on('data', b => { body += b; });
      req.on('end', () => {
        if (body) {
          const next = JSON.parse(body), old = checkpoints.get(match[1]);
          if (old && old.invoices !== next.invoices) {
            res.writeHead(409, {'Content-Type': 'application/json'});
            return res.end(JSON.stringify({success: false, error: 'Conflicto confirmado'}));
          }
          checkpoints.set(match[1], next);
        }
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify({success: true, progress: checkpoints.get(match[1]) || null}));
      });
      return;
    }
    if (req.url.endsWith('.js')) {
      res.setHeader('Content-Type', 'application/javascript');
      res.end(fs.readFileSync(path.join(__dirname, 'static/javascript', path.basename(req.url))));
      return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end('<!doctype html><input name="csrfmiddlewaretoken" value="fictional"><script>window.TURNO_CIERRE_RESPALDO="/close/0/"</script><script src="/nova_storage.js"></script><script src="/cash_close_storage.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.SALE_DRAFT_BROWSER_CHANNEL || 'chrome'});
    const context = await browser.newContext();
    const page = await context.newPage();
    const url = `http://127.0.0.1:${server.address().port}`;
    await page.goto(url);
    const quota = await page.evaluate(() => {
      localStorage.setItem('nova:venta-draft:pending', 'DO NOT DELETE');
      localStorage.setItem('nova:integrated-request:pending', 'KEEP REFERENCE');
      localStorage.setItem('tc_contados_99', 'KEEP CLOSE');
      localStorage.setItem('tc_retiro_print_99', 'KEEP PRINT');
      let filled = false;
      for (let n = 1; n < 100; n++) {
        try { localStorage.setItem(`catalog_${n}`, 'x'.repeat(200000)); }
        catch (error) { filled = error.name === 'QuotaExceededError'; break; }
      }
      NovaStorage.safeSet('tc_contados_100', 'protected'.repeat(50000));
      return {filled, sale: localStorage.getItem('nova:venta-draft:pending'),
        request: localStorage.getItem('nova:integrated-request:pending'),
        close: localStorage.getItem('tc_contados_99'), print: localStorage.getItem('tc_retiro_print_99'),
        saved: !!localStorage.getItem('tc_contados_100')};
    });
    assert.deepEqual(quota, {filled: true, sale: 'DO NOT DELETE', request: 'KEEP REFERENCE', close: 'KEEP CLOSE', print: 'KEEP PRINT', saved: true});
    const migrated = await page.evaluate(async () => {
      localStorage.setItem('catalog_77', JSON.stringify([{id: 1, name: 'FICTICIO'}]));
      localStorage.setItem('catalog_77_ts', String(Date.now()));
      const saved = await NovaStorage.catalogGet(77);
      return {saved: saved.items[0].name, legacy: localStorage.getItem('catalog_77')};
    });
    assert.deepEqual(migrated, {saved: 'FICTICIO', legacy: null});
    await page.reload();
    assert.equal(await page.evaluate(async () => (await NovaStorage.catalogGet(77)).items[0].name), 'FICTICIO');
    const refs = await page.evaluate(async () => {
      const key = 'nova:integrated-request:fictional:cart';
      const ids = await Promise.all(Array.from({length: 10}, () => NovaStorage.reference(key, () => crypto.randomUUID())));
      return {ids, local: localStorage.getItem(key)};
    });
    assert.equal(new Set(refs.ids).size, 1);
    assert.equal(refs.local, null);
    await page.reload();
    assert.equal(await page.evaluate(() => NovaStorage.reference('nova:integrated-request:fictional:cart', () => crypto.randomUUID())), refs.ids[0]);
    assert.equal(await page.evaluate(async () => {
      const key = 'nova:integrated-request:fictional:cart';
      localStorage.setItem(key, 'conflicting-legacy');
      try { await NovaStorage.reference(key, () => crypto.randomUUID()); return false; }
      catch { return true; }
      finally { localStorage.removeItem(key); }
    }), true);
    // Denied localStorage + unavailable server: IndexedDB still commits and survives reload.
    await page.route('**/close/1/', route => route.abort());
    await page.evaluate(async () => {
      Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
      await NovaCloseStorage.commit(1, {progress: {step: 'cash', invoices: 0, payment_details: []}, ts: Date.now()});
    });
    await page.reload();
    assert.equal(await page.evaluate(async () => (await NovaCloseStorage.load(1)).progress.step), 'cash');
    // Both browser stores denied: server alone must preserve the step on a fresh tab.
    await page.evaluate(async () => {
      Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
      NovaStorage.durable.set = async () => { throw new Error('Disk full'); };
      await NovaCloseStorage.commit(2, {progress: {step: 'cash', invoices: 100, payment_details: [{nombre: 'FICTICIO', monto: '100'}]}, ts: Date.now()});
    });
    const second = await context.newPage();
    await second.goto(url);
    assert.equal(await second.evaluate(async () => (await NovaCloseStorage.load(2)).progress.invoices), 100);
    const conflict = await second.evaluate(async () => {
      try { await NovaCloseStorage.commit(2, {progress: {step: 'cash', invoices: 99, payment_details: []}, ts: Date.now()}); return false; }
      catch (e) { return e.message; }
    });
    assert.equal(conflict, 'Conflicto confirmado');
    // No backend and no browser storage: do not advance even in memory.
    await page.route('**/close/3/', route => route.abort());
    const failed = await page.evaluate(async () => {
      try { await NovaCloseStorage.commit(3, {progress: {step: 'cash', invoices: 0, payment_details: []}, ts: Date.now()}); return false; }
      catch (e) { return {error: e.message, value: NovaCloseStorage.read(3)}; }
    });
    assert.match(failed.error, /No se pudo guardar el avance/);
    assert.equal(failed.value, null);
    assert.equal(await page.evaluate(() => localStorage.getItem('nova:venta-draft:pending')), 'DO NOT DELETE');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
