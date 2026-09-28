// Actual templates, JS and CSS; all business requests are mocked, no database.
// Requires Playwright, Python/Django and DataTables 1.13.8 (downloaded from its CDN
// or read from DATATABLES_TEST_DIR/jquery.dataTables.min.{js,css}).
// NODE_PATH=<node_modules> PYTHON_FOR_TESTS=<python> node --test scripts/test_sales_product_ui.cjs
const {test,before,after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const {chromium} = require('playwright');
const root = path.join(__dirname,'..');
let browser, html, dtJS, dtCSS;
async function dataTablesAsset(ext) {
  const name = `jquery.dataTables.min.${ext}`;
  if (process.env.DATATABLES_TEST_DIR) return fs.readFileSync(path.join(process.env.DATATABLES_TEST_DIR,name),'utf8');
  const response = await fetch(`https://cdn.datatables.net/1.13.8/${ext}/${name}`,{signal:AbortSignal.timeout(20000)});
  assert.equal(response.status,200);
  return response.text();
}
before(async () => {
  const renderCode = `
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace
from django.template.loader import render_to_string
labels=['Inicio','Métricas','Sucursales','Categorías','Productos','Inventarios','Proveedores','Precios proveedor','Puntos de pago','Usuarios','Empleados','Horarios','Clientes','Ventas','Pedidos','Caja','Seguridad','Visor Barcode']
nav=[dict(label=label,url='#',active=False,children=[]) for label in labels]
context=dict(nav_menu=nav,user=SimpleNamespace(is_authenticated=True),request=SimpleNamespace(resolver_match=SimpleNamespace(url_name='reporte_ventas_producto')),nav_session_name='Usuario de prueba',nav_session_username='Prueba',csrf_token='test',sucursales=[SimpleNamespace(pk=7,nombre='Yerbabuena'),SimpleNamespace(pk=8,nombre='Otra sucursal')])
print(json.dumps(render_to_string('ventas_producto_rango.html',context)))
`;
  html = JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS || 'python',['-B','-c',renderCode],{cwd:root,encoding:'utf8'}));
  [dtJS,dtCSS] = await Promise.all([dataTablesAsset('js'),dataTablesAsset('css')]);
  browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async () => {await browser?.close();});
function sample() {
  return {success:true,product:{id:2978,nombre:'FR TOMATE CHONTO XKG',codigo_de_barras:'7701234567890'},
    stats:{ventas_distintas:42,unidades:18500,ingresos:'92500.50'},range:{desde:'2026-09-22',hasta:'2026-09-28'},
    daily:[{fecha:'2026-09-22',ventas:8,unidades:3500},{fecha:'2026-09-23',ventas:12,unidades:6500},{fecha:'2026-09-24',ventas:22,unidades:8500}]};
}
async function fixture(width=1440,height=1000) {
  const page = await browser.newPage({viewport:{width,height}}), errors=[], requests=[];
  page.on('pageerror',error => errors.push(error.message));
  let data=sample(), status=200;
  await page.route('**/*',async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'cdn.datatables.net') return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:url.pathname.endsWith('.js')?dtJS:dtCSS});
    if (url.hostname !== '127.0.0.1') return route.abort();
    if (url.pathname === '/reportes/ventas-producto/') return route.fulfill({contentType:'text/html',body:html});
    if (url.pathname.startsWith('/static/')) {
      const file=path.resolve(root,'mainApp',url.pathname.slice(1));
      if (!file.startsWith(path.join(root,'mainApp','static')+path.sep) || !fs.existsSync(file)) return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    requests.push(url);
    if (url.pathname === '/ventas/producto/stats/') return route.fulfill({status,json:data});
    return route.fulfill({json:{results:[{id:2978,text:'FR TOMATE CHONTO XKG',barcode:'7701234567890'}]}});
  });
  await page.goto('http://127.0.0.1/reportes/ventas-producto/');
  await page.waitForFunction(() => window.jQuery?.fn.dataTable?.isDataTable('#ventasDiaTable') && document.documentElement.style.getPropertyValue('--nav-current-h'));
  return {page,errors,requests,setResponse(value,code=200){data=value;status=code;}};
}
async function selectProduct(page,input='#producto_busqueda_nombre',term='tomate') {
  await page.locator('#id_sucursal').selectOption('7');
  await page.locator(input).fill(term);
  await page.locator('.ui-autocomplete:visible .ui-menu-item').first().waitFor();
  await page.locator(input).press('Enter');
  assert.equal(await page.locator('#id_productoid').inputValue(),'2978');
}
async function consult(page) {
  await page.locator('#btnConsultar').click();
  await page.waitForFunction(() => document.querySelector('#btnConsultar').getAttribute('aria-busy') === 'false');
}

for (const width of [320,390,768,1280,1920]) test(`reporte ${width}px: filtros, consulta y tabla adaptables`,async () => {
  const {page,errors,requests}=await fixture(width);
  try {
    assert.equal(await page.locator('#resultsEmpty').isVisible(),true);
    const layout=await page.evaluate(() => ({scroll:document.documentElement.scrollWidth,bg:getComputedStyle(document.body).backgroundImage,
      heading:document.querySelector('.vpr-page-head').getBoundingClientRect().top,nav:document.querySelector('.nv').getBoundingClientRect().bottom,
      controls:[...document.querySelectorAll('.vpr-filters input:not([type=hidden]),.vpr-filters select,.vpr-filters button')].map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right};})}));
    assert.ok(layout.scroll<=width); assert.match(layout.bg,/gradient/); assert.ok(layout.heading>=layout.nav);
    assert.ok(layout.controls.every(r=>r.left>=0&&r.right<=width));
    await selectProduct(page); await consult(page);
    assert.equal(await page.locator('#resultCard').isVisible(),true);
    assert.equal(await page.locator('#resultsEmpty').isVisible(),false);
    assert.equal(await page.locator('#k_veces').textContent(),'42');
    assert.equal(await page.locator('#k_unidades').textContent(),'18.500');
    assert.match(await page.locator('#k_ingresos').textContent(),/\$\s*92\.500,50/);
    assert.equal(await page.locator('#ventasDiaTable tbody tr').count(),3);
    assert.equal(await page.locator('#ventasDiaTable thead').isVisible(),true);
    const query=requests.find(url=>url.pathname==='/ventas/producto/stats/');
    assert.equal(query.searchParams.get('sucursal_id'),'7'); assert.equal(query.searchParams.get('productoid'),'2978');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth));
    if (process.env.SALES_PRODUCT_SCREENSHOTS && [390,1920].includes(width)) {
      await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:path.join(process.env.SALES_PRODUCT_SCREENSHOTS,`sales-product-${width}.png`),fullPage:true});
    }
    await page.locator('#ventasDiaTable th').nth(1).click();
    await page.locator('#ventasDiaTable th').nth(1).click();
    assert.equal(await page.locator('#ventasDiaTable tbody tr').first().locator('td').nth(1).textContent(),'22');
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});

test('calendario en español, rango rápido activo y selección manual coherente',async () => {
  const {page,errors}=await fixture(390);
  try {
    assert.equal(await page.locator('[data-q="7"]').getAttribute('aria-pressed'),'true');
    await page.locator('[data-q="hoy"]').click();
    assert.equal(await page.locator('#id_desde').inputValue(),await page.locator('#id_hasta').inputValue());
    await page.locator('#id_hasta').click();
    assert.equal(await page.locator('#ui-datepicker-div').isVisible(),true);
    const calendar=await page.locator('#ui-datepicker-div').boundingBox();
    assert.ok(calendar.x>=0&&calendar.x+calendar.width<=390);
    assert.match(await page.locator('#ui-datepicker-div').textContent(),/LuMaMiJuViSáDo/);
    assert.equal(await page.evaluate(()=>window.jQuery('#id_hasta').datepicker('option','monthNamesShort')[0]),'Ene');
    await page.locator('#id_hasta').press('Escape');
    await page.locator('#id_desde').fill('2026-01-01');
    assert.equal(await page.locator('.quick-btn[aria-pressed="true"]').count(),0);
    await page.locator('[data-q="30"]').click();
    assert.equal(await page.locator('[data-q="30"]').getAttribute('aria-pressed'),'true');
    const days=await page.evaluate(()=> (new Date(document.querySelector('#id_hasta').value)-new Date(document.querySelector('#id_desde').value))/86400000);
    assert.equal(days,29);
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});

for (const [input,term] of [['#producto_busqueda_barras','7701234567890'],['#producto_busqueda_id','2978']]) test(`se conserva la búsqueda ${input}`,async () => {
  const {page,errors}=await fixture();
  try {await selectProduct(page,input,term);await consult(page);assert.equal(await page.locator('#r_id').textContent(),'2978');assert.deepEqual(errors,[]);}
  finally {await page.close();}
});

test('validación, mensaje sin ventas, cambio de sucursal y error seguro',async () => {
  const {page,requests,setResponse,errors}=await fixture(320);
  try {
    await page.locator('#btnConsultar').click();
    assert.match(await page.locator('#error-message').textContent(),/sucursal/);
    assert.equal(requests.length,0);
    await selectProduct(page);
    setResponse({...sample(),stats:{ventas_distintas:0,unidades:0,ingresos:'0'},daily:[]});
    await consult(page);
    assert.match(await page.locator('#ventasDiaTable').textContent(),/no tiene ventas/);
    assert.match(await page.locator('#k_ingresos').textContent(),/\$\s*0/);
    await page.locator('#id_sucursal').selectOption('8');
    assert.equal(await page.locator('#resultCard').isVisible(),false);
    assert.equal(await page.locator('#resultsEmpty').isVisible(),true);
    setResponse({success:false,error:'<b>Error de prueba</b>'},400);
    await consult(page);
    assert.equal(await page.locator('#error-message').textContent(),'<b>Error de prueba</b>');
    assert.equal(await page.locator('#error-message b').count(),0);
    assert.equal(await page.locator('#btnConsultar').isDisabled(),false);
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});

test('el panel de cámara cabe en móvil horizontal y cierra con Escape',async () => {
  const {page}=await fixture(667,375);
  try {
    // Solo inspeccionar el diseño; nunca solicitar acceso a una cámara real.
    await page.locator('#barcodeScannerOverlay').evaluate(el=>{el.style.display='flex';});
    const panel=await page.locator('.scanner-card').boundingBox();
    assert.ok(panel.x>=0&&panel.y>=0&&panel.x+panel.width<=667&&panel.y+panel.height<=375);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#barcodeScannerOverlay').isVisible(),false);
  } finally {await page.close();}
});
