// Real template and JavaScript; simulated endpoints, no business database.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
let browser, html;
before(async () => {
  const code = `
import os, json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace as NS
from django.template.loader import render_to_string
from mainApp.services.employee_rotation import preset
people = [{'id': i, 'nombre': name, 'sucursal_id': 1} for i, name in enumerate(preset()['personas'], 1)]
config = dict(personal=False, editable=True, datosUrl='/datos/', guardarUrl='/guardar/', rotacionUrl='/crear/',
    plantilla=preset(), mapeoSugerido={p['nombre']:p['id'] for p in people}, empleados=people,
    sucursales=[{'sucursalid':1, 'nombre':'Yerbabuena'}, {'sucursalid':2, 'nombre':'Otra'}])
print(json.dumps(render_to_string('calendario_empleados.html', dict(calendar_config=config, personal=False,
    editable=True, csrf_token='fixture', nav_menu=[], request=NS(user=NS(pk=1), resolver_match=NS(url_name='calendario_empleados'))))))
`;
  html = JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS || 'python', ['-B', '-c', code], {cwd: root, encoding: 'utf8'}))
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, tag => /schedule-config|calendario_empleados\.js/.test(tag) ? tag : '');
  browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async () => { await browser?.close(); });
for (const width of [390, 1440]) test(`October rotation form at ${width}px`, async () => {
  const page = await browser.newPage({viewport: {width, height: 1000}});
  const errors = [];
  let sent;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1') return route.abort();
    if (url.pathname === '/datos/') return route.fulfill({json: {eventos: [], rotaciones: [
      {id: 17, nombre: 'Anterior de prueba', inicio: '2026-09-07', semanas: 4, version: 3, sucursal_ids: [1]},
      {id: 18, nombre: 'Finalizada', inicio: '2026-01-05', fin_exclusivo: '2026-09-07', semanas: 4, version: 2, sucursal_ids: [1]},
      {id: 19, nombre: 'Otra sucursal', inicio: '2026-09-07', semanas: 4, version: 1, sucursal_ids: [2]},
    ]}});
    if (url.pathname === '/crear/') {
      sent = route.request().postDataJSON();
      return route.fulfill({json: {rotacion: {id: 20}, guardado: true}});
    }
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, 'mainApp', url.pathname.slice(1));
      if (!file.startsWith(path.join(root, 'mainApp/static') + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
      return route.fulfill({path: file});
    }
    return route.fulfill({contentType: 'text/html', body: html});
  });
  await page.goto('http://127.0.0.1/horarios/empleados/');
  await page.waitForFunction(() => document.getElementById('schedule-calendar').getAttribute('aria-busy') === 'false');
  await page.locator('#rotation-open').click();
  assert.equal(await page.locator('#rotation-start').inputValue(), '2026-10-26');
  assert.equal(await page.locator('#rotation-replace').inputValue(), '');
  assert.equal(await page.locator('#rotation-replace option').count(), 2);
  assert.equal(await page.locator('#rotation-mapping select').count(), 10);
  assert.equal(await page.locator('#rotation-preview table').count(), 4);
  assert.match(await page.locator('#rotation-preview').textContent(), /Fabián · 07:00–12:00 y 15:00–19:00/);
  const box = await page.locator('#rotation-dialog').boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= width);
  await page.locator('#rotation-replace').selectOption('17');
  if (process.env.POS_TEST_ARTIFACT_DIR) await page.screenshot({path: path.join(process.env.POS_TEST_ARTIFACT_DIR, `rotation-october-${width}.png`)});
  await page.locator('#rotation-save').click();
  await page.waitForFunction(() => !document.getElementById('rotation-dialog').open);
  assert.equal(sent.reemplazar_id, 17);
  assert.equal(sent.reemplazar_version, 3);
  assert.equal(sent.inicio, '2026-10-26');
  assert.equal(Object.keys(sent.empleados).length, 10);
  assert.deepEqual(errors, []);
  await page.close();
});
