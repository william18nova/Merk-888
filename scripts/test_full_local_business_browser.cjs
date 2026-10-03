const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.argv[2]||'playwright');
const BASE='http://127.0.0.1:8914';
const OUT=path.resolve(__dirname,'../outputs/full-local-business');
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const ctx=await browser.newContext({viewport:{width:1400,height:1000}});
  const external=[],errors=[];
  await ctx.route('**/*',route=>{if(new URL(route.request().url()).origin!==BASE){external.push(route.request().url());return route.abort();}return route.continue();});
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.print=()=>{throw Error('Impresión excluida');};});
  await page.goto(BASE+'/generar_venta/');
  await page.locator('[name=nombreusuario]').fill('laboratorio');
  await page.locator('[name="contraseña"]').fill('prueba-local-2026');
  await page.getByRole('button',{name:'Entrar',exact:true}).click();
  await page.waitForURL(BASE+'/generar_venta/');
  await page.evaluate(async()=>{
    const csrf=JSON.parse(document.getElementById('hybrid-sale-context').textContent).csrf_token;
    const response=await fetch('/local/sales/api/test-network',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({offline:false})});
    if(!response.ok) throw Error('No se pudo preparar el laboratorio');
  });
  await page.locator('#producto_busqueda_nombre').fill('arroz');
  await page.locator('.ui-autocomplete:visible .ui-menu-item').first().click();
  await page.locator('#generar-venta:enabled').click();
  await page.locator('.pm-check[value=nequi]').check();
  await page.locator('#confirmar-pago').click();
  await page.waitForFunction(()=>!document.querySelector('#detalle-productos tbody tr'));
  let response=await page.request.get(BASE+'/local/sales/api/history');
  const sale=(await response.json())[0];assert.equal(sale.state,'accepted');
  assert.match(sale.receipt,/sin verificación/);
  await page.goto(BASE+'/local/devoluciones/?sale_id='+sale.sale_id);
  await page.locator('#local-return-form').waitFor();
  const network=async offline=>{
    const result=await page.evaluate(async offline=>{
      const csrf=document.querySelector('[name=csrfmiddlewaretoken]').value;
      const r=await fetch('/local/sales/api/test-network',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({offline})});
      return r.status;
    },offline);assert.equal(result,200);
  };
  const confirm=async button=>{
    await button.click();await page.locator('#business-confirm[open]').waitFor();
    await page.locator('#business-confirm button[value=confirm]').click();
    await page.waitForLoadState('networkidle');
  };
  await network(true);
  await page.goto(BASE+'/caja/registrar-pago/');
  await page.locator('[name=concept]').fill('coca cola');
  await page.locator('[data-money-input]').fill('1000');
  await page.locator('[name=method]').selectOption('nequi');
  assert.equal(await page.locator('[data-money-input]').inputValue(),'1.000');
  assert.match(await page.locator('#local-tax-preview').textContent(),/1\.004/);
  await confirm(page.getByRole('button',{name:'Registrar pago',exact:true}));
  assert.match(await page.locator('.business-history').textContent(),/Pendiente/);
  assert.match(await page.locator('.business-history').textContent(),/COCA COLA/);
  await page.goto(BASE+'/local/devoluciones/?sale_id='+sale.sale_id);
  await page.locator('[data-return-quantity]').fill('1');
  await page.locator('[name=method]').selectOption('nequi');
  await confirm(page.getByRole('button',{name:'Solicitar devolución',exact:true}));
  assert.match(await page.locator('.business-history').textContent(),/Pendiente/);
  await page.goto(BASE+'/turno_caja/');
  assert.match(await page.locator('.business-page').textContent(),/Paso 1 de 3/);
  await confirm(page.getByRole('button',{name:'Confirmar y continuar',exact:true}));
  assert.match(await page.locator('.business-page').textContent(),/Paso 2 de 3/);
  assert.equal(await page.locator('[name=bills_paid]').count(),0);
  await confirm(page.getByRole('button',{name:'Confirmar y continuar',exact:true}));
  assert.match(await page.locator('.business-page').textContent(),/Paso 3 de 3/);
  await page.locator('[name=method_nequi]').fill('2500');
  await confirm(page.getByRole('button',{name:'Declarar cierre',exact:true}));
  assert.match(await page.locator('.business-history').textContent(),/Pendiente/);
  await page.goto(BASE+'/local/estado/');
  await page.getByRole('button',{name:'Restablecer y sincronizar',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#local-sale-demo-result').textContent.includes('Comunicación restablecida'));
  for(const route of ['/caja/registrar-pago/','/local/devoluciones/','/turno_caja/']){
    await page.goto(BASE+route);assert.match(await page.locator('.business-history').textContent(),/Confirmado/);
  }
  assert.match(await page.locator('.business-page').textContent(),/turno está cerrado/);
  await page.screenshot({path:path.join(OUT,'closed-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.goto(BASE+'/caja/registrar-pago/');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(OUT,'expense-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={passed:true,nonCashSale:true,offlineExpenseWithTax:true,offlineReturnRequest:true,threeStepClose:true,orderedSync:true,externalRequests:0,javascriptErrors:0};
  fs.writeFileSync(path.join(OUT,'report.json'),JSON.stringify(report,null,2));console.log(report);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
