// Ejecutar tras test_expense_editing con POS_TEST_ARTIFACT_DIR; solo usa datos ficticios.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
let chromium;
try { ({chromium} = require('playwright')); } catch (_) {}
const artifacts = process.env.POS_TEST_ARTIFACT_DIR;

test('edición de pagos: diseño adaptable, importe exacto y envío visible', {
  skip: !chromium || !artifacts || !fs.existsSync(path.join(artifacts, 'expense-edit.html')),
}, async () => {
  let submitted;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (req.method === 'POST') {
      let body = '';
      req.on('data', part => body += part);
      req.on('end', () => { submitted = new URLSearchParams(body); res.end('<h1>Cambio recibido por el simulador</h1>'); });
      return;
    }
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(__dirname, 'static', url.pathname.slice(8));
      if (!file.startsWith(path.resolve(__dirname, 'static') + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/png');
      res.end(fs.readFileSync(file)); return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(fs.readFileSync(path.join(artifacts, url.pathname.endsWith('/editar/') ? 'expense-edit.html' : 'expense-list.html')));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless: true, channel: process.env.SALE_DRAFT_BROWSER_CHANNEL || 'chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    // Nunca salir del servidor simulado ni enviar datos a servicios externos.
    await context.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/caja/pagos/1/editar/');
    assert.ok(await page.evaluate(() => getComputedStyle(document.body).fontFamily.includes('Segoe UI')));
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundAttachment), 'scroll, scroll, scroll');
    assert.equal(await page.locator('#id_monto').inputValue(), '1.250.000,50');
    assert.equal(await page.locator('#id_medio_pago').inputValue(), 'nequi');
    assert.ok(await page.getByRole('heading', {name: 'Historial de correcciones'}).isVisible());
    await page.screenshot({path: path.join(artifacts, 'editar-pago-desktop.png'), fullPage: true});
    await page.locator('#id_concepto').fill('servicio de agua');
    assert.equal(await page.locator('#id_concepto').inputValue(), 'SERVICIO DE AGUA');
    await page.locator('#id_monto').fill('1234567,89');
    assert.equal(await page.locator('#id_monto').inputValue(), '1.234.567,89');
    await page.locator('#id_motivo').fill('Corregir factura');
    await page.getByRole('button', {name: 'Guardar cambios'}).click();
    await page.getByRole('heading', {name: 'Cambio recibido por el simulador'}).waitFor();
    assert.equal(submitted.get('monto'), '1.234.567,89');
    assert.equal(submitted.get('concepto'), 'SERVICIO DE AGUA');
    assert.ok(submitted.get('version'));
    assert.equal(submitted.get('motivo'), 'Corregir factura');
    await page.goto(base + '/caja/pagos/');
    assert.ok(await page.getByRole('link', {name: 'Editar pago 1'}).isVisible());
    await page.screenshot({path: path.join(artifacts, 'editar-pagos-lista.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    for (const suffix of ['/caja/pagos/', '/caja/pagos/1/editar/']) {
      await page.goto(base + suffix);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    }
    await page.screenshot({path: path.join(artifacts, 'editar-pago-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
