// Navegador real contra cajas y nube ficticias del ensayo. Nunca imprime.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const args = Object.fromEntries(Array.from({length:(process.argv.length-2)/2}, (_,i)=>[process.argv[2+i*2], process.argv[3+i*2]]));
const {chromium} = require(args['--playwright'] || 'playwright');

(async()=>{
  for (const key of ['--first','--second']) assert.match(args[key], /^http:\/\/127\.0\.0\.1:\d+$/);
  fs.mkdirSync(args['--output'], {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    const errors=[];
    const pages=await Promise.all([context.newPage(),context.newPage()]);
    for(const [index,page] of pages.entries()){
      page.on('pageerror', error=>errors.push(error.message));
      // Fallar si alguna acción intenta imprimir, incluso por accidente.
      await page.addInitScript(()=>{ window.print=()=>{throw new Error('No se permite imprimir en este ensayo');}; });
      await page.goto(args[index===0?'--first':'--second']);
      await page.locator('#pos').waitFor({state:'visible'});
      await page.locator('#search').waitFor({state:'visible'});
      assert.equal(await page.locator('.history-row').count(),0);
    }
    async function sale(page,query,quantity,byEnter=false){
      await page.locator('#quantity').fill(String(quantity));
      const input=query==='770123'?'#barcode':'#search';
      const list=input==='#barcode'?'#barcode-suggestions':'#suggestions';
      await page.locator(input).fill(query);
      await page.locator(list+' button').first().waitFor();
      if(byEnter) await page.locator(input).press('Enter');
      else await page.locator(list+' button').first().click();
      await page.waitForFunction(()=>document.querySelector('#total').textContent.includes('1.900'));
      await page.locator('#generar-venta').click();
      await page.locator('#cash').fill('2000');
      await page.locator('#checkout').click();
      await page.locator('#receipt-dialog').waitFor({state:'visible'});
      assert.match(await page.locator('#receipt-state').innerText(),/pendiente de nube/);
      assert.match(await page.locator('#receipt').innerText(),/CAMBIO: \$ 100/);
      await page.locator('#close-receipt').click();
      assert.equal(await page.locator('.cart-row').count(),0);
    }
    await Promise.all([sale(pages[0],'tomate',500),sale(pages[1],'770123',500,true)]);
    // Dos pestañas del mismo puesto pueden cobrar carritos independientes.
    const extra=await context.newPage();
    extra.on('pageerror',error=>errors.push(error.message));
    await extra.goto(args['--first']);
    await extra.locator('#pos').waitFor({state:'visible'});
    await sale(extra,args['--product'],500,true);
    await extra.close();
    await pages[0].reload();
    await pages[0].locator('#history-panel summary').click();
    await pages[0].locator('.history-row').nth(1).waitFor();
    assert.equal(await pages[0].locator('.history-row').count(),2);
    await pages[0].locator('#history button').first().click();
    await pages[0].locator('#receipt-dialog').waitFor({state:'visible'});
    assert.match(await pages[0].locator('#receipt').innerText(),/CAMBIO/);
    await pages[0].locator('#close-receipt').click();
    await pages[0].screenshot({path:path.join(args['--output'],'aceptacion-desktop.png'),fullPage:true});
    await pages[0].setViewportSize({width:390,height:844});
    assert.ok(await pages[0].evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await pages[0].screenshot({path:path.join(args['--output'],'aceptacion-mobile.png'),fullPage:true});
    await pages[0].locator('#backup-settings').click();
    await pages[0].locator('#backup-form input[name="pin"]').fill('clave-local-solo-pruebas');
    await pages[0].locator('#backup-form input[name="password"]').fill('respaldo-ficticio-solo-pruebas');
    await pages[0].locator('#backup-form input[name="confirmation"]').fill('respaldo-ficticio-solo-pruebas');
    const downloadReady=pages[0].waitForEvent('download');
    await pages[0].locator('#backup-form button[type="submit"]').click();
    const download=await downloadReady;
    await download.saveAs(path.join(args['--output'],'aceptacion.novabackup'));
    await pages[0].locator('#backup-dialog').waitFor({state:'hidden'});
    assert.equal(await pages[0].locator('#backup-form input[name="password"]').inputValue(),'');
    await pages[0].locator('#lock').click();
    await pages[0].locator('#unlock').waitFor({state:'visible'});
    await pages[0].locator('#unlock input').fill('incorrecta');
    await pages[0].locator('#unlock button').click();
    await pages[0].getByText('Clave local incorrecta.',{exact:true}).waitFor();
    await pages[0].locator('#unlock input').fill('clave-local-solo-pruebas');
    await pages[0].locator('#unlock button').click();
    await pages[0].locator('#pos').waitFor({state:'visible'});
    await pages[0].locator('#release').click();
    await pages[0].locator('#confirm-release').click();
    await pages[0].locator('#release-error').waitFor({state:'visible'});
    assert.match(await pages[0].locator('#release-error').innerText(),/Quedan operaciones pendientes/);
    assert.ok(await pages[0].locator('#confirm-dialog').evaluate(e=>e.open));
    await pages[0].screenshot({path:path.join(args['--output'],'aceptacion-cierre-bloqueado.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('OK navegador: 3 ventas en 2 cajas/3 pestañas, nombre-ID-barras, gramos, cambio, recarga, móvil/escritorio, respaldo, PIN y cierre bloqueado. Sin impresión.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
