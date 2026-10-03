// Demo ficticia scripts/smoke_hybrid_server.py --sale-ui --port 8901. Nunca imprime.
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]||'playwright');
const URL='http://127.0.0.1:8901', OUT=path.resolve(__dirname,'../outputs/hybrid-ui');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],external=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(URL))external.push(r.url());});
    await page.addInitScript(()=>{window.print=()=>{throw Error('No imprimir en este ensayo');};});
    await page.goto(URL);await page.locator('#pos').waitFor({state:'visible'});
    assert.equal(await page.locator('h1').filter({hasText:'Generar Venta'}).count(),1);
    assert.equal(await page.locator('#branch').inputValue(),'Pruebas');
    const history=async()=>await(await page.request.get(URL+'/api/history')).json();
    const before=(await history()).length;
    async function readyTotal(text){await page.waitForFunction(value=>document.querySelector('#total').textContent.includes(value),text);await page.locator('#generar-venta:enabled').waitFor();}
    async function add(query,qty=1){await page.locator('#quantity').fill(String(qty));await page.locator('#search').fill(query);await page.locator('#suggestions button').first().click();}
    async function empty(){await page.locator('#clear').click();await page.locator('#confirm-clear').click();await page.locator('#empty-cart').waitFor({state:'visible'});}
    await page.locator('#search').fill('manzana acida');
    await page.locator('#suggestions button').waitFor();
    await page.locator('#search').press('ArrowDown');
    assert.equal(await page.locator('#suggestions button').getAttribute('aria-selected'),'true');
    await page.locator('#search').press('Enter');await readyTotal('2.200');
    assert.equal(await page.locator('.cart-row').count(),1);
    await page.locator('#clear').click();await page.locator('#cancel-clear').click();assert.equal(await page.locator('.cart-row').count(),1);
    await empty();
    await page.locator('#barcode').fill('770000');await page.locator('#barcode').press('Enter');
    assert.equal(await page.locator('.cart-row').count(),0);
    await page.locator('#barcode').fill('7700000000011');await page.locator('#barcode').press('Enter');
    await readyTotal('1.800');assert.equal(await page.locator('#barcode').inputValue(),'');
    await page.locator('#barcode').press('Enter');assert.equal(await page.locator('.cart-row input').inputValue(),'1');
    await page.locator('#barcode').fill('7700000000011');await page.locator('#barcode').press('Enter');
    await readyTotal('3.600');assert.equal(await page.locator('.cart-row input').inputValue(),'2');
    assert.equal((await history()).length,before);
    await page.keyboard.press('Alt+Space');await page.locator('#payment-dialog').waitFor({state:'visible'});
    await page.locator('#cash').fill('3000');assert.equal(await page.locator('#checkout').isDisabled(),true);
    await page.locator('#cash').fill('4000');assert.match(await page.locator('#change').innerText(),/400/);
    await page.locator('#cash').press('Enter');await page.locator('#receipt-dialog').waitFor({state:'visible'});
    assert.match(await page.locator('#receipt-state').innerText(),/pendiente de nube/);
    await page.locator('#close-receipt').click();assert.equal((await history()).length,before+1);
    assert.equal(await page.locator('#connection').getAttribute('data-state'),'offline');
    await add('13');await add('7318');await readyTotal('12.000');
    assert.match(await page.locator('.cart-row[data-id="7318"] .row-subtotal').innerText(),/\$\s*0/);
    await page.locator('#cart-search').fill('BOLSA');assert.equal(await page.locator('.cart-row:visible').count(),1);assert.equal(await page.locator('.cart-row').count(),2);
    await page.locator('#cart-search').fill('');
    fs.mkdirSync(OUT,{recursive:true});await page.screenshot({path:path.join(OUT,'generar-venta-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(OUT,'generar-venta-mobile.png'),fullPage:true});
    await page.locator('#generar-venta').click();await page.locator('#cash').fill('15000');
    await page.screenshot({path:path.join(OUT,'pago-mobile.png'),fullPage:true});
    await page.locator('#cancel-payment').click();await empty();
    await add('tomate',500);await readyTotal('1.900');
    await page.locator('#generar-venta').click();await page.locator('#cash').fill('2000');
    let lost=true;
    await page.route('**/api/checkout',async route=>{if(lost){lost=false;await route.fetch();await route.abort('failed');}else await route.continue();});
    await page.locator('#checkout').click();await page.locator('#payment-error').waitFor({state:'visible'});
    assert.equal((await history()).length,before+2);
    await page.locator('#checkout:enabled').click();await page.locator('#receipt-dialog').waitFor({state:'visible'});
    assert.equal((await history()).length,before+2);
    assert.match(await page.locator('#receipt').innerText(),/CAMBIO:\s*\$\s*100/);
    await page.locator('#close-receipt').click();await page.unroute('**/api/checkout');
    await page.locator('#history-panel summary').click();assert.equal(await page.locator('.history-row:visible').count(),before+2);
    await page.locator('#history button').first().click();await page.locator('#close-receipt').click();
    await page.locator('#lock').click();await page.locator('#unlock').waitFor({state:'visible'});
    await page.locator('#unlock input').fill('demo-local');await page.locator('#unlock button').click();await page.locator('#pos').waitFor({state:'visible'});
    await page.locator('#scope-settings').click();await page.locator('#scope-dialog').waitFor({state:'visible'});assert.match(await page.locator('#scope-dialog').innerText(),/No están habilitados/);await page.locator('#close-scope').click();
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    console.log('OK: diseño móvil/escritorio, recursos locales, autocompletes, teclado, barras exactas, stock negativo, carrito/búsqueda, promoción, modal/cambio, respuesta perdida sin duplicados, historial y bloqueo. Sin impresión.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
