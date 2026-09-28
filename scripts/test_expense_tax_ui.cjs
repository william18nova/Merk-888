// Fixtures HTML reales de Django, generadas por mainApp.test_expense_tax.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const artifacts = process.env.POS_TEST_ARTIFACT_DIR;
let server, browser, base;

test.before(async () => {
  assert.ok(artifacts, 'Defina POS_TEST_ARTIFACT_DIR con los HTML de pruebas aisladas');
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const root = path.resolve(__dirname, '../mainApp/static');
    const file = url.pathname.startsWith('/static/')
      ? path.resolve(root, url.pathname.slice(8))
      : path.join(artifacts, `expense-tax-${url.pathname.slice(1) || 'create'}.html`);
    if ((url.pathname.startsWith('/static/') && !file.startsWith(root + path.sep)) || !fs.existsSync(file)) {
      res.writeHead(404).end(); return;
    }
    res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'text/html; charset=utf-8');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({headless: true, channel: 'msedge'});
});
test.after(async () => { await browser?.close(); await new Promise(resolve => server ? server.close(resolve) : resolve()); });

async function open(route, width = 1440) {
  const context = await browser.newContext({viewport: {width, height: 1000}});
  await context.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/${route}`);
  return {page, context, errors};
}

for (const width of [390, 1440]) {
  test(`formulario y configuración adaptables a ${width}px`, async () => {
    const {page, context, errors} = await open('create', width);
    try {
      await page.locator('#id_monto').fill('100000');
      assert.equal(await page.locator('#id_monto').inputValue(), '100.000');
      await page.locator('#id_medio_pago').selectOption('nequi');
      assert.match(await page.locator('[data-expense-tax]').textContent(), /400/);
      assert.match(await page.locator('[data-expense-total]').textContent(), /100\.400/);
      assert.equal(await page.locator('#id_impuesto_esperado').inputValue(), '1');
      assert.ok(await page.locator('[data-expense-tax-preview]').isVisible());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(artifacts, `expense-tax-${width}.png`), fullPage: true});
      await page.locator('#id_medio_pago').selectOption('efectivo');
      assert.match(await page.locator('[data-expense-total]').textContent(), /100\.000/);
      assert.equal(await page.locator('#id_impuesto_esperado').inputValue(), '0');
      await page.goto(`${base}/config`);
      const nequi = page.locator('[data-method-card]').filter({has: page.locator('input[name="code"][value="nequi"]')});
      const cash = page.locator('[data-method-card]').filter({has: page.locator('input[name="code"][value="efectivo"]')});
      assert.ok(await nequi.locator('[name="expense_tax_enabled"]').isChecked());
      assert.equal(await cash.locator('[name="expense_tax_enabled"]').isChecked(), false);
      await cash.locator('[name="expense_tax_enabled"]').check();
      assert.ok(await cash.locator('[name="expense_tax_enabled"]').isChecked());
      await page.evaluate(() => scrollTo(0, 0));
      assert.ok(await page.evaluate(() => getComputedStyle(document.body).fontFamily.includes('Segoe UI')));
      await page.screenshot({path: path.join(artifacts, `expense-tax-config-${width}.png`), fullPage: true});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}

test('editar usa el valor base y recalcula sin duplicar impuesto', async () => {
  const {page, context, errors} = await open('edit');
  try {
    assert.equal(await page.locator('#id_monto').inputValue(), '100.000,00');
    assert.match(await page.locator('[data-expense-total]').textContent(), /100\.400/);
    for (const [amount, tax, total] of [['200000', /800/, /200\.800/], ['1,25', /0,01/, /1,26/], ['1234567,89', /4\.938,27/, /1\.239\.506,16/]]) {
      await page.locator('#id_monto').fill(amount);
      assert.match(await page.locator('[data-expense-tax]').textContent(), tax);
      assert.match(await page.locator('[data-expense-total]').textContent(), total);
    }
    await page.locator('#id_monto').fill('-100');
    assert.equal(await page.locator('[data-expense-total]').textContent(), '—');
    assert.equal(await page.locator('#id_monto').evaluate(el => el.checkValidity()), false);
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});
