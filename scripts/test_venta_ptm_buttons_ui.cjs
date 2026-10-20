// Presentation-only test: real templates/styles, fictional data, no database or remote requests.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
let browser, pages;

before(async () => {
  const code = `
import os, json
os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace as NS
from django.template.loader import render_to_string
context = dict(csrf_token='fixture', nav_menu=[], turno_requerido=True,
    turno_activo=True, sucursal_nombre='Sucursal de prueba', puntopago_nombre='Caja de prueba',
    form=NS(initial={'sucursal': 1, 'puntopago': 1}),
    request=NS(user=NS(pk=1), resolver_match=NS(url_name='generar_venta')))
print(json.dumps({str(local): render_to_string('generar_venta.html', dict(context, hybrid_local=local))
    for local in (False, True)}))
`;
  pages = JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS || 'python', ['-B', '-c', code], {
    cwd: root, encoding: 'utf8',
  }));
  browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async () => { await browser?.close(); });

for (const width of [360, 768, 1440]) {
  test(`PTM shortcuts: responsive layout and keyboard access at ${width}px`, async () => {
    const page = await browser.newPage({viewport: {width, height: 1000}});
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1') return route.abort();
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve(root, 'mainApp', url.pathname.slice(1));
        if (!file.startsWith(path.join(root, 'mainApp', 'static') + path.sep) || !fs.existsSync(file)) {
          return route.fulfill({status: 404, body: ''});
        }
        return route.fulfill({path: file});
      }
      // Sales/payment scripts are deliberately excluded from this visual fixture.
      const html = pages.False.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
      return route.fulfill({contentType: 'text/html', body: html});
    });
    await page.goto('http://127.0.0.1/generar_venta/');
    const buttons = page.locator('.venta-ptm-action');
    assert.equal(await buttons.count(), 2);
    for (const [index, type] of ['retiro', 'recarga'].entries()) {
      const button = buttons.nth(index);
      assert.equal(await button.getAttribute('href'), `/ptm/?tipo=${type}`);
      assert.equal(await button.getAttribute('target'), '_blank');
      assert.equal(await button.getAttribute('rel'), 'noopener');
      const box = await button.boundingBox();
      assert.ok(box.height >= 44 && box.x >= 0 && box.x + box.width <= width);
      assert.equal(await button.evaluate(el => el.scrollWidth <= el.clientWidth), true);
    }
    await buttons.first().focus();
    await page.keyboard.press('Tab');
    assert.equal(await buttons.last().evaluate(el => el === document.activeElement), true);
    assert.equal(await buttons.last().evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
    if (process.env.POS_TEST_ARTIFACT_DIR) {
      await page.screenshot({path: path.join(process.env.POS_TEST_ARTIFACT_DIR, `venta-ptm-${width}.png`)});
    }
    await page.emulateMedia({reducedMotion: 'reduce'});
    await buttons.first().hover();
    assert.equal(await buttons.first().evaluate(el => getComputedStyle(el).transform), 'none');
    await page.close();
  });
}

test('PTM shortcuts remain excluded from the hybrid-local template', () => {
  assert.ok(pages.False.includes('class="venta-ptm"'));
  assert.ok(!pages.True.includes('class="venta-ptm"'));
});
