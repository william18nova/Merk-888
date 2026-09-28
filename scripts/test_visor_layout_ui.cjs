// Isolated browser tests: actual Django templates/assets, no database or server.
// NODE_PATH=<node_modules> PYTHON_FOR_TESTS=<python> node --test scripts/test_visor_layout_ui.cjs
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const renderCode = `
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace
from django.template.loader import render_to_string
labels=['Inicio','Métricas','Sucursales','Categorías','Productos','Inventarios','Proveedores','Precios proveedor','Puntos de pago','Usuarios','Empleados','Horarios','Clientes','Ventas','Pedidos','Caja','Seguridad','Visor Barcode']
pages={}
for mode in ('public','authenticated_public','cashier'):
    authenticated=mode!='public'
    cashier=mode=='cashier'
    user=SimpleNamespace(is_authenticated=authenticated,pk=91 if authenticated else None)
    nav=[dict(label=label,url='#',active=False,children=[]) for label in (labels if authenticated else ['Inicio','Visor Barcode'])]
    request=SimpleNamespace(user=user,resolver_match=SimpleNamespace(url_name='visor_cajero' if cashier else 'visor_barcode'))
    context=dict(user=user,request=request,nav_menu=nav,nav_session_name='Usuario de prueba',nav_session_username='Prueba',csrf_token='test',visor_cajero=cashier,visor_turno_id=123 if authenticated else None)
    pages[mode]=render_to_string('visor_producto_barcode.html',context)
print(json.dumps(pages))
`;
let browser, pages;
before(async () => {
  pages = JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS || 'python', ['-B', '-c', renderCode], {cwd:root, encoding:'utf8'}));
  browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async () => {await browser?.close();});

async function fixture(mode, width, height=900) {
  const page = await browser.newPage({viewport:{width,height}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/visor-test/') return route.fulfill({contentType:'text/html',body:pages[mode]});
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, 'mainApp', url.pathname.slice(1));
      if (!file.startsWith(path.join(root, 'mainApp', 'static') + path.sep) || !fs.existsSync(file)) return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    return route.fulfill({json:{success:true,results:[]}});
  });
  await page.goto('http://127.0.0.1/visor-test/');
  await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--nav-current-h'));
  return {page, errors};
}

for (const mode of ['public', 'authenticated_public', 'cashier']) {
  for (const width of [320, 390, 768, 1280, 1920]) {
    test(`${mode} ${width}px: fondo continuo y contenido dentro de la pantalla`, async () => {
      const {page, errors} = await fixture(mode, width);
      try {
        await page.locator('#vb_name').evaluate(el => {el.textContent = 'PRODUCTO DE PRUEBA CON NOMBRE LARGO PARA CONSULTAR SU PRECIO';});
        const layout = await page.evaluate(() => {
          const body = getComputedStyle(document.body);
          const wrap = document.querySelector('.page-wrap');
          const viewer = document.querySelector('.vb-page');
          return {
            bodyMargin:body.margin, bodyBackground:body.backgroundImage, bodyColor:body.backgroundColor,
            bodyRepeat:body.backgroundRepeat, rootColor:getComputedStyle(document.documentElement).backgroundColor,
            wrapBackground:getComputedStyle(wrap).backgroundImage,
            viewerBackground:getComputedStyle(viewer).backgroundImage,
            wrapHeight:wrap.getBoundingClientRect().height,
            navBottom:document.querySelector('.nv').getBoundingClientRect().bottom,
            cardTop:document.querySelector('.vb-card').getBoundingClientRect().top,
            scrollWidth:document.documentElement.scrollWidth,
            controls:[...document.querySelectorAll('.vb-input,.vb-btn')].map(el => {
              const rect=el.getBoundingClientRect(); return {left:rect.left,right:rect.right};
            }),
          };
        });
        assert.equal(layout.bodyMargin, '0px');
        assert.match(layout.bodyBackground, /gradient/);
        assert.doesNotMatch(layout.bodyRepeat, /(^|, )repeat(,|$)/);
        assert.equal(layout.rootColor, 'rgb(21, 48, 96)');
        assert.equal(layout.bodyColor, layout.rootColor);
        assert.equal(layout.wrapBackground, 'none');
        assert.equal(layout.viewerBackground, 'none', 'el visor no inicia otro degradado dentro de la página');
        assert.ok(layout.wrapHeight >= 900);
        assert.ok(layout.cardTop >= layout.navBottom, 'el navbar no tapa el visor');
        assert.ok(layout.scrollWidth <= width, JSON.stringify(layout));
        assert.ok(layout.controls.every(rect => rect.left >= 0 && rect.right <= width));
        assert.equal(await page.locator('#vb_search').count(), mode === 'cashier' ? 1 : 0);
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundImage), layout.bodyBackground);
        assert.ok(await page.evaluate(() => document.body.getBoundingClientRect().bottom >= innerHeight - 1));
        if (process.env.VISOR_SCREENSHOTS && [390,1920].includes(width)) {
          await page.evaluate(() => window.scrollTo(0,0));
          await page.screenshot({path:path.join(process.env.VISOR_SCREENSHOTS,`visor-${mode}-${width}.png`),fullPage:true});
        }
        assert.deepEqual(errors, []);
      } finally {await page.close();}
    });
  }
}

test('el fondo cubre pantallas altas sin estirar innecesariamente el panel', async () => {
  const {page} = await fixture('public', 1280, 1600);
  try {
    const sizes = await page.evaluate(() => ({
      body:document.body.getBoundingClientRect().height,
      card:document.querySelector('.vb-card').getBoundingClientRect().height,
      scroll:document.documentElement.scrollHeight,
    }));
    assert.equal(sizes.body, 1600);
    assert.equal(sizes.scroll, 1600);
    assert.ok(sizes.card < 1000);
  } finally {await page.close();}
});
