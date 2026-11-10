// Plantilla y JS reales con datos ficticios; todos los POST se interceptan.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');

test('la devolución envía solo el turno elegido y el selector cabe en móvil', async () => {
  const code = `
import os, json
os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.test_settings'
import django
django.setup()
from decimal import Decimal
from types import SimpleNamespace as NS
from django.template.loader import render_to_string
from django.utils import timezone
from mainApp.forms import DevolucionFormSet, PagoMixtoFormSet, ReintegroMixtoFormSet
original = NS(nombre='Caja inicial')
venta = NS(pk=901, total=Decimal('500'), mediopago='efectivo', puntopagoid=original,
    sucursalid=NS(nombre='Sucursal ficticia'), empleadoid=NS(nombre='Operador ficticio', apellido=''))
turnos = [NS(pk=700, puntopago=original, cajero=NS(nombreusuario='Cajero uno'), estado='ABIERTO', inicio=timezone.now()),
    NS(pk=702, puntopago=NS(nombre='Caja alternativa'), cajero=NS(nombreusuario='Cajera dos'), estado='CIERRE', inicio=timezone.now())]
dev = DevolucionFormSet(initial=[dict(detalle_id=81, devolver=0)], prefix='dev')
reint = ReintegroMixtoFormSet(initial=[dict(medio_pago='efectivo', monto=0)], prefix='reint')
reint.forms[0].payment_method_label = 'Efectivo'
filas = [(NS(productoid=NS(nombre='Artículo ficticio'), cantidad=2, preciounitario=250, subtotal=500), dev.forms[0])]
ctx = dict(venta=venta, filas=filas, dev_formset=dev, reintegro_formset=reint, pagos_formset=PagoMixtoFormSet(prefix='pagos'),
    venta_total_cobrado=venta.total, reintegro_ledger_ready=True, csrf_token='fixture',
    refund_turn_picker=dict(turns=turnos, default_id=700, allow_no_turn=False),
    request=NS(resolver_match=NS(url_name='ver_venta')), nav_menu=[], user=NS(is_authenticated=True))
print(json.dumps(render_to_string('ver_venta.html', ctx)))
`;
  const html = JSON.parse(execFileSync(process.env.PYTHON_FOR_TESTS || 'python', ['-B', '-c', code], {cwd: root, encoding: 'utf8'}));
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 850}});
    const errors = [], posts = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async dialog => { errors.push(dialog.message()); await dialog.dismiss(); });
    const assets = new Set(['/static/javascript/ver_venta.js', '/static/css/ver_venta.css', '/static/css/generar_venta.css']);
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== 'http://127.0.0.1') return route.abort();
      if (assets.has(url.pathname)) return route.fulfill({path: path.join(root, 'mainApp', url.pathname.slice(1))});
      if (url.pathname !== '/ver_venta/901/') return route.fulfill({status: 404});
      if (req.method() === 'POST') {
        posts.push(Object.fromEntries(new URLSearchParams(req.postData())));
        return route.fulfill({contentType: 'text/html; charset=utf-8', body: '<p>Devolución ficticia recibida</p>'});
      }
      return route.fulfill({contentType: 'text/html', body: html});
    });
    await page.goto('http://127.0.0.1/ver_venta/901/');
    const quantity = page.locator('[name="dev-0-devolver"]');
    const turn = page.getByLabel('¿A qué turno se carga la devolución?');
    assert.equal(await turn.isVisible(), false);
    assert.equal(await turn.isDisabled(), true);
    await quantity.fill('1');
    assert.equal(await turn.inputValue(), '700');
    assert.equal(await turn.isVisible(), true);
    assert.equal(await turn.isEnabled(), true);
    assert.equal(await turn.evaluate(el => el.getBoundingClientRect().right <= innerWidth), true);
    await turn.selectOption('702');
    await quantity.fill('0');
    assert.equal(await turn.isDisabled(), true);
    assert.equal(await page.evaluate(() => new FormData(document.querySelector('#venta-form')).has('turno_reintegro_id')), false);
    await quantity.fill('1');
    assert.equal(await page.locator('[name="reint-0-monto"]').inputValue(), '250.00');
    assert.deepEqual(await page.locator('#venta-form :invalid').evaluateAll(nodes => nodes.map(el => el.name)), []);
    await page.getByRole('button', {name: 'Guardar cambios / devolución'}).click();
    await page.getByText('Devolución ficticia recibida').waitFor({state: 'visible', timeout: 6000});
    assert.equal(posts.length, 1);
    assert.equal(posts[0].turno_reintegro_id, '702');
    assert.equal(posts[0]['reint-0-monto'], '250.00');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
