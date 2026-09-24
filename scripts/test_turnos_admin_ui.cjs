// Pruebas aisladas: todas las peticiones (incluidos POST) usan datos simulados.
// NODE_PATH=<node_modules> PYTHON_FOR_TESTS=<python> node --test scripts/test_turnos_admin_ui.cjs
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
let browser,templates;
before(async()=>{
  const renderCode=`
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace
from django.template.loader import render_to_string
labels=['Inicio','Métricas','Sucursales','Categorías','Productos','Inventarios','Proveedores','Precios proveedor','Puntos de pago','Usuarios','Empleados','Horarios','Clientes','Ventas','Pedidos','Caja','Seguridad','Visor Barcode']
nav=[dict(label=label,url='#',active=False,children=[] if i in (0,1,17) else [dict(label='Consultar',url='#',active=False)]) for i,label in enumerate(labels)]
context=dict(nav_menu=nav,user=SimpleNamespace(is_authenticated=True),request=SimpleNamespace(resolver_match=SimpleNamespace(url_name='turnos_caja_admin')),nav_session_name='Usuario de prueba',nav_session_username='Prueba',csrf_token='test')
print(json.dumps([render_to_string('turnos_caja_admin.html',dict(context,can_delete_turnos=allowed)) for allowed in (False,True)]))
`;
  templates=JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS||'python',['-B','-c',renderCode],{cwd:root,encoding:'utf8'}));
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'msedge'});
});
after(async()=>{await browser?.close();});

function sample(id=1674){
  return {success:true,turno:{id,estado:'CERRADO',puntopago:'Principal',cajero:'Ana Martínez',saldo_apertura_efectivo:100000,
    inicio_local:'2026-09-23T07:00',cierre_iniciado_local:'2026-09-23T14:00',fin_local:'2026-09-23T14:20',
    efectivo_real:1050000,facturas_pagadas:50000,ptm:{cantidad:0,declarado:0,recargas:0,retiros:0,neto:0}},
    medios:[{metodo:'efectivo',label:'Efectivo',esperado:1000000,contado:1000000,diferencia:0},
      {metodo:'nequi',label:'Nequi',esperado:500000,contado:505000,diferencia:5000},
      {metodo:'tarjeta',label:'Tarjeta / Banco Caja Social',esperado:200000,contado:195000,diferencia:-5000}]};
}

async function fixture({width=1440,initial='1674',canDelete=true,data=sample()}={}){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],requests=[];
  const state={data:structuredClone(data)};
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1')return route.abort();
    if(url.pathname==='/turnos_caja_admin/')return route.fulfill({contentType:'text/html',body:templates[Number(canDelete)]});
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,'mainApp',url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'mainApp','static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    requests.push({url:url.pathname,method:route.request().method(),body:route.request().postDataJSON()});
    const match=url.pathname.match(/^\/api\/admin\/turnos_caja\/(\d+)\/(update\/|delete\/)?$/);
    if(!match)return route.fulfill({status:404,body:'Unexpected test route'});
    if(match[2]==='update/'){
      const body=route.request().postDataJSON();
      state.data.medios=state.data.medios.map(m=>({...m,...body.medios.find(v=>v.metodo===m.metodo)}));
      return route.fulfill({json:{success:true,msg:'Turno actualizado y recalculado.'}});
    }
    if(match[2]==='delete/')return route.fulfill({json:{success:true,msg:'Turno eliminado.'}});
    return route.fulfill({json:{...state.data,turno:{...state.data.turno,id:Number(match[1])}}});
  });
  await page.goto(`http://127.0.0.1/turnos_caja_admin/?turno_id=${initial}`);
  if(initial)await page.waitForFunction(()=>!document.querySelector('#editor').hidden&&!document.querySelector('#btnLoad').disabled);
  await page.waitForFunction(()=>document.documentElement.style.getPropertyValue('--nav-current-h'));
  return {page,errors,requests,state};
}
const currency=value=>new Intl.NumberFormat('es-CO',{style:'currency',currency:'COP',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);

for(const width of [320,390,768,1280,1920])test(`administración ${width}px: fondo continuo y campos dentro de pantalla`,async()=>{
  const {page,errors,requests}=await fixture({width});
  try{
    const sizes=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      background:getComputedStyle(document.body).backgroundImage,bodyMargin:getComputedStyle(document.body).margin,
      title:document.querySelector('.tca-page-head').getBoundingClientRect().top,nav:document.querySelector('.nv').getBoundingClientRect().bottom,
      row:getComputedStyle(document.querySelector('#mediosBody tr')).display,
      controls:[...document.querySelectorAll('.tca-wrap input,.tca-wrap select,.tca-wrap button')].map(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right}))}));
    assert.ok(sizes.scroll<=width,JSON.stringify(sizes));assert.match(sizes.background,/gradient/);assert.equal(sizes.bodyMargin,'0px');
    assert.ok(sizes.title>=sizes.nav);assert.ok(sizes.controls.every(el=>el.left>=0&&el.right<=width),JSON.stringify(sizes));
    assert.equal(sizes.row,width<=760?'grid':'table-row');
    assert.equal(await page.locator('#loadedTurnoId').textContent(),'#1674');
    assert.equal(await page.locator('#mFacturasPagadas').textContent(),currency(50000));
    assert.match(await page.locator('#mPTMHistorial').getAttribute('href'),/turno=1674/);
    assert.equal(await page.locator('#btnSave').isEnabled(),true);
    assert.equal(requests.filter(r=>r.method==='POST').length,0);
    if(process.env.ADMIN_SCREENSHOTS&&[390,1920].includes(width)){
      fs.mkdirSync(process.env.ADMIN_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.ADMIN_SCREENSHOTS,`admin-${width}.png`)});
      await page.locator('.tca-payments').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.ADMIN_SCREENSHOTS,`admin-payments-${width}.png`)});
    }
    assert.deepEqual(errors,[]);
  }finally{await page.close();}
});

test('URL sin ID muestra ayuda y permite cargar por Enter',async()=>{
  const {page,requests}=await fixture({initial:''});
  try{
    assert.equal(await page.locator('#editor').isVisible(),false);assert.equal(requests.length,0);
    await page.locator('#turnoId').fill('123');await page.locator('#turnoId').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#loadedTurnoId').textContent==='#123');
    assert.match(page.url(),/turno_id=123/);assert.equal(await page.locator('#loadState').isVisible(),false);
  }finally{await page.close();}
});

test('editar y guardar conserva cálculos, facturas y payload existente',async()=>{
  const {page,requests}=await fixture();
  try{
    assert.equal(await page.locator('#mEsperado').textContent(),currency(1700000));
    assert.equal(await page.locator('#mVentas').textContent(),currency(1700000));
    await page.getByRole('spinbutton',{name:'Contado · Efectivo',exact:true}).fill('1010000');
    assert.equal(await page.locator('#mVentas').textContent(),currency(1710000));
    assert.equal(await page.locator('#mDiff').textContent(),currency(10000));
    assert.equal(await page.locator('#mDeuda').textContent(),currency(-5000));
    assert.match(await page.locator('#saveStatus').textContent(),/pendientes/);
    await page.locator('#btnSave').click();
    await page.waitForFunction(()=>document.querySelector('#saveStatus').dataset.state==='success');
    const posts=requests.filter(r=>r.method==='POST');assert.equal(posts.length,1);
    assert.equal(posts[0].url,'/api/admin/turnos_caja/1674/update/');
    assert.deepEqual(posts[0].body,{estado:'CERRADO',saldo_apertura_efectivo:'100000',inicio_local:'2026-09-23T07:00',cierre_iniciado_local:'2026-09-23T14:00',fin_local:'2026-09-23T14:20',efectivo_real:'1050000',medios:[{metodo:'efectivo',esperado:'1000000',contado:'1010000'},{metodo:'nequi',esperado:'500000',contado:'505000'},{metodo:'tarjeta',esperado:'200000',contado:'195000'}]});
    assert.equal(await page.locator('#mFacturasPagadas').textContent(),currency(50000));
    assert.equal(await page.locator('#btnSave').isEnabled(),true);
  }finally{await page.close();}
});

test('PTM conserva bloqueo de edición y eliminación y explica por qué',async()=>{
  const data=sample();data.turno.ptm={cantidad:3,declarado:3,recargas:100000,retiros:20000,neto:80000};
  const {page,requests}=await fixture({data});
  try{
    assert.equal(await page.locator('#ptmProtection').isVisible(),true);
    assert.equal(await page.locator('#ptmCantidad').textContent(),'3');
    assert.equal(await page.locator('#ptmNeto').textContent(),currency(80000));
    assert.equal(await page.locator('#btnSave').isDisabled(),true);assert.equal(await page.locator('#btnDelete').isDisabled(),true);
    assert.equal(await page.locator('#editor input:enabled,#editor select:enabled').count(),0);
    assert.equal(await page.locator('#btnLoad').isEnabled(),true);
    await page.evaluate(()=>document.querySelector('#btnSave').dispatchEvent(new MouseEvent('click',{bubbles:true})));
    assert.equal(requests.filter(r=>r.method==='POST').length,0);
  }finally{await page.close();}
});

test('permiso de edición sin eliminación no muestra botón eliminar',async()=>{
  const {page}=await fixture({canDelete:false});
  try{assert.equal(await page.locator('#btnDelete').count(),0);assert.equal(await page.locator('#btnSave').isEnabled(),true);}
  finally{await page.close();}
});

test('error al consultar no permite editar el turno anterior',async()=>{
  const {page,requests}=await fixture();
  try{
    await page.route('**/api/admin/turnos_caja/999/',route=>route.fulfill({status:404,json:{success:false,error:'No se encontró el turno.'}}));
    await page.locator('#turnoId').fill('999');await page.locator('#btnLoad').click();
    await page.waitForFunction(()=>document.querySelector('#flash').textContent.includes('No se encontró'));
    assert.equal(await page.locator('#editor').isVisible(),false);assert.equal(await page.locator('#btnSave').isDisabled(),true);
    assert.equal(await page.locator('#loadState').isVisible(),true);assert.equal(requests.filter(r=>r.method==='POST').length,0);
  }finally{await page.close();}
});

test('guardar impide doble envío y cambiar de turno mientras responde',async()=>{
  const {page,requests}=await fixture();let release,entered;
  const pending=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  let posts=0;
  await page.route('**/api/admin/turnos_caja/*/update/',async route=>{posts++;entered();await pending;await route.fulfill({json:{success:true,msg:'Guardado.'}});});
  try{
    await page.locator('#btnSave').click();await started;
    assert.equal(await page.locator('#btnLoad').isDisabled(),true);assert.equal(await page.locator('#turnoId').isDisabled(),true);
    assert.equal(await page.locator('#btnDelete').isDisabled(),true);assert.equal(await page.locator('#editor input:enabled').count(),0);
    await page.evaluate(()=>{document.querySelector('#btnSave').dispatchEvent(new MouseEvent('click'));document.querySelector('#turnoSearch').dispatchEvent(new Event('submit',{cancelable:true}));});
    assert.equal(posts,1);assert.equal(requests.filter(r=>r.method==='GET').length,1);
    release();await page.waitForFunction(()=>!document.querySelector('#btnSave').disabled);
    assert.equal(posts,1);
  }finally{release();await page.close();}
});

test('eliminar conserva confirmación y limpia la URL solo al confirmar',async()=>{
  const {page,requests}=await fixture();
  try{
    page.once('dialog',dialog=>dialog.dismiss());await page.locator('#btnDelete').click();
    assert.equal(requests.filter(r=>r.method==='POST').length,0);assert.equal(await page.locator('#editor').isVisible(),true);
    page.once('dialog',dialog=>dialog.accept());await page.locator('#btnDelete').click();
    await page.waitForFunction(()=>document.querySelector('#editor').hidden);
    assert.equal(new URL(page.url()).searchParams.has('turno_id'),false);assert.equal(requests.filter(r=>r.method==='POST').length,1);
    assert.equal(await page.locator('#loadStateTitle').textContent(),'Turno eliminado');
  }finally{await page.close();}
});

test('nombres y medios especiales se muestran como texto seguro',async()=>{
  const data=sample();data.turno.cajero='<img src=x onerror=alert(1)>';data.medios[0].metodo="pago'prueba";data.medios[0].label='<b>Prueba</b>';
  const {page,errors}=await fixture({data});
  try{
    assert.equal(await page.locator('#cajName').textContent(),data.turno.cajero);
    assert.equal(await page.locator('#cajName img,#mediosBody b').count(),0);
    assert.equal(await page.locator('#mediosBody input[aria-label]').count(),6);assert.deepEqual(errors,[]);
  }finally{await page.close();}
});

test('guardado exitoso con recarga fallida no informa que se perdió el cambio',async()=>{
  const {page}=await fixture();
  try{
    await page.route('**/api/admin/turnos_caja/1674/',route=>route.fulfill({status:503,body:'Temporalmente no disponible'}));
    await page.locator('#btnSave').click();
    await page.waitForFunction(()=>document.querySelector('#saveStatus').textContent.includes('Los cambios se guardaron'));
    assert.match(await page.locator('#saveStatus').textContent(),/Vuelve a cargar/);assert.equal(await page.locator('#editor').isVisible(),true);
  }finally{await page.close();}
});
