// Isolated UI regression tests. Requires Playwright and Python with Django.
// NODE_PATH=<node_modules> PYTHON_FOR_TESTS=<python> node --test scripts/test_turnos_dashboard_ui.cjs
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), cp=require('node:child_process');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
const python=process.env.PYTHON_FOR_TESTS || 'python';
const renderCode=`
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace
from django.template.loader import render_to_string
labels=['Inicio','Métricas','Sucursales','Categorías','Productos','Inventarios','Proveedores','Precios proveedor','Puntos de pago','Usuarios','Empleados','Horarios','Clientes','Ventas','Pedidos','Caja','Seguridad','Visor Barcode']
nav=[dict(label=label,url='#',active=False,children=[] if i in (0,1,17) else [dict(label='Consultar',url='#',active=False)]) for i,label in enumerate(labels)]
context=dict(can_edit_turnos=True,nav_menu=nav,user=SimpleNamespace(is_authenticated=True),request=SimpleNamespace(resolver_match=SimpleNamespace(url_name='turnos_caja_dashboard')),nav_session_name='Usuario de prueba',nav_session_username='Prueba',csrf_token='test')
print(json.dumps(render_to_string('turnos_caja_dashboard.html',context)))
`;
let browser,html;
before(async()=>{
  html=JSON.parse(cp.execFileSync(python,['-B','-c',renderCode],{cwd:root,encoding:'utf8'}));
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async()=>{await browser?.close();});
const items=Array.from({length:25},(_,i)=>({id:1676-i,estado:i<2?'ABIERTO':i===2?'CIERRE':'CERRADO',puntopago:i%2?'Auxiliar':'Principal',cajero:['Ana Martínez','Carlos Pérez','Daniela Rojas','Andrés Rodríguez'][i%4],inicio:'2026-09-23 07:39:21',fin:i<3?null:'2026-09-23 15:02:16',esperado_total:i<3?0:1597334,ventas_total:i<3?0:1602050,diferencia_total:i<3?0:i%2?-21026:4716,deuda_total:i===5?-25000:0}));
async function fixture(width=1440,height=1000,canEdit=true){
  const page=await browser.newPage({viewport:{width,height}}), errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1') {await route.abort();return;}
    if(url.pathname==='/turnos_caja_dashboard/') return route.fulfill({contentType:'text/html',body:canEdit?html:html.replace('CAN_EDIT_TURNOS = true','CAN_EDIT_TURNOS = false')});
    if(url.pathname.startsWith('/static/')) {
      const file=path.resolve(root,'mainApp',url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'mainApp','static')+path.sep) || !fs.existsSync(file)) return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    requests.push(url);
    if(url.pathname.includes('/list/')) {
      const pageNo=Number(url.searchParams.get('page')||1), empty=url.searchParams.get('q')==='sin resultados';
      return route.fulfill({json:{success:true,total:empty?0:30,items:empty?[]:pageNo===1?items:items.slice(0,5),page:pageNo}});
    }
    if(/^\/api\/turnos_caja\/\d+\/$/.test(url.pathname)) {
      const t=items[0];
      return route.fulfill({json:{success:true,turno:{...t,esperado_total_bd:1597334},medios:['Efectivo','Nequi','Tarjeta / Banco Caja Social'].map(label=>({label,esperado_bd:1200000,esperado_calc:1250000,contado:1240000,diferencia:-10000})),expected_calc:{esperado_total_calc:3750000}}});
    }
    return route.fulfill({status:404,body:'Unexpected test route'});
  });
  await page.goto('http://127.0.0.1/turnos_caja_dashboard/');
  await page.waitForFunction(()=>document.querySelector('#resultSummary').textContent.includes('de 30'));
  // El navbar mide su altura en el siguiente animation frame.
  await page.waitForFunction(()=>document.documentElement.style.getPropertyValue('--nav-current-h'));
  return {page,errors,requests};
}

for(const width of [320,390,768,1280,1920])test(`diseño ${width}px: fondo azul, sin desbordamiento y todas las acciones visibles`,async()=>{
  const {page,errors}=await fixture(width);
  try{
    const sizes=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      background:getComputedStyle(document.body).backgroundImage,
      rowDisplay:getComputedStyle(document.querySelector('#tbodyTurnos tr')).display,
      navbar:document.querySelector('.nv').getBoundingClientRect().bottom,
      title:document.querySelector('.tcd-page-head').getBoundingClientRect().top,
      fields:[...document.querySelectorAll('.tcd-filters input,.tcd-filters select,.tcd-filters button')].map(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right}))}));
    assert.ok(sizes.scroll<=width,JSON.stringify(sizes));assert.match(sizes.background,/gradient/);
    assert.ok(sizes.title>=sizes.navbar,`el navbar no tapa el título: ${JSON.stringify(sizes)}`);
    assert.ok(sizes.fields.every(f=>f.left>=0&&f.right<=width));
    assert.equal(sizes.rowDisplay,width<=900?'grid':'table-row');
    if(process.env.DASHBOARD_SCREENSHOTS && [390,1920].includes(width)) await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOTS,`dashboard-${width}.png`),fullPage:false});
    await page.locator('[data-detail]').first().click();
    await page.waitForFunction(()=>document.querySelector('#mBodyMedios').children.length>0);
    const modal=await page.locator('.m-card').boundingBox();
    assert.ok(modal.x>=0&&modal.x+modal.width<=width);
    if(process.env.DASHBOARD_SCREENSHOTS && width===390) await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOTS,'dashboard-mobile-detail.png')});
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#modal').isVisible(),false);
    assert.equal(await page.evaluate(()=>document.activeElement.hasAttribute('data-detail')),true);
    assert.deepEqual(errors,[]);
  }finally{await page.close();}
});

test('filtrar, limpiar, paginar y validar fechas conservan el funcionamiento',async()=>{
  const {page,requests}=await fixture();
  try{
    await page.locator('#btnNext').click(); await page.waitForFunction(()=>document.querySelector('#pgInfo').textContent==='Página 2 de 2');
    assert.equal(await page.locator('#btnNext').isDisabled(),true);
    await page.locator('#fQ').fill('sin resultados'); await page.locator('#fQ').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#resultSummary').textContent.startsWith('0 turnos'));
    assert.match(await page.locator('#tbodyTurnos').textContent(),/No encontramos turnos/);
    await page.locator('#btnClear').click();await page.waitForFunction(()=>document.querySelector('#resultSummary').textContent.includes('de 30'));
    assert.equal(await page.locator('#fQ').inputValue(),'');
    await page.locator('#fFrom').fill('2026-09-25');await page.locator('#fTo').fill('2026-09-23');
    const count=requests.length;await page.locator('#btnRefresh').click();
    assert.match(await page.locator('#flash').textContent(),/posterior/);assert.equal(requests.length,count);
  }finally{await page.close();}
});

test('sin permiso no ofrece editar y los nombres se renderizan como texto seguro',async()=>{
  const {page}=await fixture(1280,1000,false);
  try{
    assert.equal(await page.locator('.btn-edit-turno').count(),0);
    await page.route('**/api/turnos_caja/list/**',route=>route.fulfill({json:{success:true,total:1,items:[{...items[0],cajero:'<img src=x onerror=alert(1)>',puntopago:'<b>Principal</b>'}]}}));
    await page.locator('#btnRefresh').click();await page.waitForFunction(()=>document.querySelector('#tbodyTurnos').textContent.includes('<img'));
    assert.equal(await page.locator('#tbodyTurnos img,#tbodyTurnos b').count(),0);
  }finally{await page.close();}
});
