// Página original + Django + PostgreSQL + origen HTTP ficticio. No impresión.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const BASE = 'http://127.0.0.1:8906';
const OUT = path.resolve(__dirname, '../outputs/full-local-sales');
(async () => {
  fs.mkdirSync(OUT, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1050}});
    const external=[],errors=[],dialogs=[];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== BASE) {external.push(route.request().url());return route.abort();}
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',async d=>{dialogs.push(d.message());await d.accept();});
    await page.addInitScript(()=>{window.print=()=>{throw Error('Impresión excluida');};});
    await page.goto(BASE+'/generar_venta/');
    await page.locator('[name="nombreusuario"]').fill('laboratorio');
    await page.locator('[name="contraseña"]').fill('prueba-local-2026');
    await page.getByRole('button',{name:'Entrar',exact:true}).click();
    await page.waitForURL(BASE+'/generar_venta/');
    await page.locator('#producto_busqueda_nombre').waitFor();
    const history=async()=>{
      const response=await page.request.get(BASE+'/local/sales/api/history');
      assert.equal(response.status(),200);return response.json();
    };
    const network=async offline=>{
      const result=await page.evaluate(async offline=>{
        const s=JSON.parse(document.querySelector('#hybrid-sale-context').textContent);
        const response=await fetch('/local/sales/api/test-network',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token},body:JSON.stringify({offline})});
        return {status:response.status,body:await response.json()};
      },offline);
      assert.equal(result.status,200,JSON.stringify(result.body));
    };
    const before=(await history()).length;
    async function add(term){
      await page.locator('#producto_busqueda_nombre').fill(term);
      await page.locator('.ui-autocomplete:visible .ui-menu-item').first().click();
      await page.locator('#detalle-productos tbody tr').waitFor();
    }
    async function charge(cash){
      await page.locator('#generar-venta:enabled').click();
      await page.locator('.pm-check[value="efectivo"]').check();
      await page.locator('#monto-recibido').fill(String(cash));
      await page.locator('#monto-recibido').dispatchEvent('input');
      await page.locator('#confirmar-pago').click();
      await page.waitForFunction(()=>!document.querySelector('#detalle-productos tbody tr'));
    }
    await add('tomate');
    await page.locator('.qty-input').fill('500');
    await page.locator('.qty-input').dispatchEvent('input');
    await page.locator('.qty-input').dispatchEvent('change');
    await page.waitForFunction(()=>document.querySelector('#total').textContent.includes('1.900'));
    await charge(2000);
    let rows=await history();
    assert.equal(rows.length,before+1);assert.equal(rows[0].state,'accepted');
    await add('arroz');
    await network(true);
    await page.waitForFunction(()=>document.querySelector('#hybrid-connection').textContent.includes('Sin conexión'));
    assert.match(await page.locator('.cart-product-name').textContent(),/ARROZ/);
    await page.screenshot({path:path.join(OUT,'offline-original-page.png'),fullPage:true});
    await charge(3000);
    rows=await history();assert.equal(rows.length,before+2);assert.equal(rows[0].state,'pending');
    assert.match(rows[0].receipt,/CAMBIO: \$ 500/);
    await page.reload();
    await page.locator('#codigo_o_barras').fill('7700000000001');
    await page.locator('#codigo_o_barras').press('Enter');
    await page.locator('#detalle-productos tbody tr').waitFor();
    await charge(1000);
    rows=await history();assert.equal(rows.length,before+3);assert.equal(rows[0].state,'pending');
    await network(false);
    await page.waitForFunction(()=>document.querySelector('#hybrid-connection').textContent.includes('sincronizadas'));
    assert((await history()).every(row=>row.state==='accepted'));
    // Respuesta HTTP local perdida después de registrar: recuperar el UUID.
    let lost=false;
    await page.route('**/local/sales/api/checkout',async route=>{
      if(!lost){lost=true;await route.fetch();await route.abort('failed');}else await route.continue();
    });
    await add('arroz');await charge(3000);
    assert(lost);rows=await history();assert.equal(rows.length,before+4);
    assert.equal(new Set(rows.map(row=>row.id)).size,rows.length);
    await page.unroute('**/local/sales/api/checkout');
    await page.locator('#hybrid-history-toggle').click();
    await page.locator('#hybrid-history details').first().waitFor();
    await page.locator('#hybrid-history-close').click();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(OUT,'mobile-original-page.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.goto(BASE+'/local/estado/');
    await page.getByRole('button',{name:'Simular sin conexión',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#local-sale-demo-result').textContent.includes('Corte simulado'));
    let status=await page.request.get(BASE+'/local/sales/api/status');
    assert.equal((await status.json()).online,false);
    await page.getByRole('button',{name:'Restablecer y sincronizar',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#local-sale-demo-result').textContent.includes('Comunicación restablecida'));
    assert((await history()).every(row=>row.state==='accepted'));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(OUT,'mobile-test-controls.png'),fullPage:true});
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    const report={passed:true,salesCreated:4,externalRequests:0,javascriptErrors:0,offlineSamePage:true,lostResponseNoDuplicate:true,testControls:true,dialogs};
    fs.writeFileSync(path.join(OUT,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
