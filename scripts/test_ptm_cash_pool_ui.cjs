// Real templates and assets; fictional data, no production requests or printing.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
let browser,html;
before(async()=>{
  const code=`
import os,json
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from types import SimpleNamespace as NS
from django.template.loader import render_to_string
from mainApp.ptm_cash_pool_views import CountForm
turn=NS(pk=123,cajero=NS(nombreusuario='Cajero de prueba'),puntopago=NS(sucursalid=NS(nombre='Yerbabuena de prueba')))
context=dict(turno=turn,entrega=True,form=CountForm(initial={'token':'fixture-only'}),csrf_token='test',nav_menu=[],request=NS(user=NS(pk=1),resolver_match=NS(url_name='bolsa_ptm_turno')))
print(json.dumps(render_to_string('bolsa_ptm_conteo.html',context)))
`;
  html=JSON.parse(cp.execFileSync(process.env.PYTHON_FOR_TESTS||'python',['-B','-c',code],{cwd:root,encoding:'utf8'}));
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'msedge'});
});
after(async()=>{await browser?.close();});

for(const width of [360,1440]) test(`conteo PTM ${width}px: formulario legible y sin esperado`,async()=>{
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1')return route.abort();
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,'mainApp',url.pathname.slice(1));
      if(!file.startsWith(path.join(root,'mainApp','static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    return route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto('http://127.0.0.1/ptm/bolsa/turno/123/');
  await page.locator('#id_contado').fill('90000');
  await page.locator('#id_confirma').check();
  assert.equal(await page.locator('form').evaluate(el=>el.checkValidity()),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.match(await page.locator('.bp-panel').innerText(),/no son descuentos automáticos/);
  assert.ok(await page.locator('form button').evaluate(el=>el.getBoundingClientRect().height)>=44);
  assert.equal(await page.locator('input').count(),4);
  if(process.env.POS_TEST_ARTIFACT_DIR) await page.screenshot({path:path.join(process.env.POS_TEST_ARTIFACT_DIR,`ptm-bolsa-${width}.png`),fullPage:true});
  assert.deepEqual(errors,[]);
  await page.close();
});

test('resumen compartido usa texto seguro y no duplica deuda ni conserva otro turno',async()=>{
  const page=await browser.newPage();
  await page.setContent('<div id="summary"></div>');
  await page.addScriptTag({path:path.join(root,'mainApp/static/javascript/ptm_cash_pool_summary.js')});
  const data={activa:true,mensaje:'Conteo registrado.',incidencias:[{id:5,faltante:'10000',estado:'provisional',participantes:[{turno:1,nombre:'<img src=x onerror=alert(1)>'},{turno:2,nombre:'Cajero B'}]}]};
  await page.evaluate(data=>NovaPTMBag.render(document.getElementById('summary'),data),data);
  assert.equal(await page.locator('#summary .bp-warning').count(),1);
  assert.equal(await page.locator('#summary img').count(),0);
  assert.match(await page.locator('#summary').innerText(),/10.000/);
  assert.doesNotMatch(await page.locator('#summary').innerText(),/Esperado:|Contado:|Sobrante/);
  await page.evaluate(()=>NovaPTMBag.render(document.getElementById('summary'),{activa:false}));
  assert.equal(await page.locator('#summary').innerText(),'');
  await page.close();
});
