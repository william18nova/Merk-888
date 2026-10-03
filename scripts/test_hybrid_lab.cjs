// Verificación del laboratorio YA abierto. Registra dos ventas FICTICIAS; no imprime.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.argv[2] || 'playwright');
const PANEL = 'http://127.0.0.1:8895', POS = 'http://127.0.0.1:8793';
const OUT = path.resolve(__dirname, '../outputs/hybrid-lab');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function state() { return (await fetch(PANEL + '/api/state')).json(); }
async function until(check, text) {
  for (let i=0; i<120; i++) { const value = await state(); if (check(value)) return value; await sleep(500); }
  throw Error(text);
}
(async () => {
  assert.equal((await (await fetch(PANEL + '/health')).json()).application, 'nova-pos-local-lab-v1-NOT-PRODUCTION');
  const base = await until(s => s.ready && s.online && !s.error, 'El laboratorio no está preparado');
  assert.equal(base.local.name, 'PC LABORATORIO - NO REAL');
  assert.equal(base.local.pending, 0);
  const oldCount = base.cloud.sales, oldTotal = Number(base.cloud.total);
  const oldStock = base.cloud.products.find(p => p.id===1).stock;
  assert.equal(base.cloud.products.length, 3);
  assert.equal((await fetch(PANEL+'/api/mode', {method:'POST',headers:{'Content-Type':'application/json'},body:'{"online":false}'})).status, 403);
  const foreignHost = await new Promise((resolve, reject) => {
    const req = http.get(PANEL+'/api/state', {headers:{Host:'example.test'}}, res=>{res.resume(); resolve(res.statusCode);});
    req.on('error',reject);
  });
  assert.equal(foreignHost,403);
  console.log('Laboratorio preparado; origen y token verificados.');
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1050}});
    const errors = [];
    await context.addInitScript(() => { window.print = () => { throw Error('No se permite imprimir en estas pruebas'); }; });
    const panel = await context.newPage(); panel.on('pageerror', e=>errors.push(e.message));
    await panel.goto(PANEL);
    await panel.locator('#open:not(.disabled)').waitFor();
    // Equivale a una navegación directa: el botón usa el navegador del SO,
    // porque el servidor de caja rechaza entradas desde otro puerto/origen.
    const pos = await context.newPage(); pos.on('pageerror', e=>errors.push(e.message));
    await pos.goto(POS);
    await pos.locator('#pos').waitFor({state:'visible',timeout:8000});
    console.log('Caja visible en el navegador.');
    if (process.argv.includes('--session-only')) {
      await pos.locator('#release').click();
      await pos.locator('#confirm-release').click();
      await pos.locator('#login').waitFor({state:'visible'});
      await panel.reload();
      await panel.locator('#session:enabled').waitFor();
      await panel.locator('#session').click();
      await until(s=>!!s.local?.session && s.local.ready && !s.busy, 'No se pudo iniciar una nueva sesión desde el panel');
      await pos.reload(); await pos.locator('#pos').waitFor({state:'visible'});
      const result = await state();
      assert.equal(result.cloud.sales,oldCount);
      assert.equal(Number(result.cloud.total),oldTotal);
      assert.equal(result.local.pending,0);
      fs.mkdirSync(OUT,{recursive:true});
      await panel.screenshot({path:path.join(OUT,'panel-desktop.png'),fullPage:true});
      await panel.setViewportSize({width:390,height:844});
      assert.equal(await panel.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await panel.screenshot({path:path.join(OUT,'panel-mobile.png'),fullPage:true});
      await pos.screenshot({path:path.join(OUT,'caja-lista.png'),fullPage:true});
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({ok:true,checks:['acceso directo después de cierre','historial conservado','finalizar sesión','nueva sesión desde el panel'],sales:oldCount,pending:0,printing:false}));
      return;
    }
    async function sale(expected) {
      await pos.locator('#quantity').fill('500');
      await pos.locator('#search').fill('tomate');
      await pos.locator('#suggestions button').first().click();
      await pos.waitForFunction(()=>document.querySelector('#total').textContent.includes('1.900'));
      assert.match(await pos.locator('#total').innerText(), /1.900/);
      if(await pos.locator('#generar-venta').count())await pos.locator('#generar-venta').click();
      await pos.locator('#cash').fill('2000');
      await pos.locator('#checkout').click();
      await pos.locator('#receipt-dialog').waitFor({state:'visible'});
      assert.match(await pos.locator('#receipt-state').innerText(), expected);
      assert.match(await pos.locator('#receipt').innerText(), /CAMBIO:\s*\$\s*100/);
      await pos.locator('#close-receipt').click();
    }
    await sale(/confirmada|sincronizada|registrada/i);
    console.log('Venta conectada registrada.');
    await until(s=>s.cloud.sales===oldCount+1 && s.local.pending===0, 'No llegó la venta conectada');
    await panel.locator('#offline').click();
    await until(s=>!s.online && !s.busy, 'No se simuló el corte');
    await sale(/pendiente/i);
    console.log('Venta sin conexión registrada.');
    const pending = await until(s=>s.local.pending===1, 'La venta no quedó pendiente');
    assert.equal(pending.cloud.sales, oldCount+1);
    panel.once('dialog', dialog=>dialog.accept());
    await panel.locator('#restart').click();
    await until(s=>!s.busy && !s.online && s.local?.unlocked && s.local.pending===1, 'No se conservó el pendiente tras reiniciar');
    await pos.reload(); await pos.locator('#pos').waitFor({state:'visible'});
    await panel.locator('#online').click();
    const done = await until(s=>s.online && !s.busy && s.local.pending===0 && s.cloud.sales===oldCount+2, 'No terminó la sincronización');
    assert.equal(done.local.conflicts,0);
    assert.equal(Number(done.cloud.total),oldTotal+3800);
    assert.equal(Number(done.cloud.cash),oldTotal+3800);
    assert.equal(done.cloud.operations,oldCount+2);
    assert.equal(done.cloud.products.find(p=>p.id===1).stock,oldStock-1000);
    await panel.locator('#sync').click();
    const repeated = await until(s=>!s.busy && s.online, 'Sincronización adicional pendiente');
    assert.equal(repeated.cloud.sales,oldCount+2);
    await panel.reload(); await panel.locator('#open:not(.disabled)').waitFor();
    fs.mkdirSync(OUT,{recursive:true});
    await panel.screenshot({path:path.join(OUT,'panel-desktop.png'),fullPage:true});
    await panel.setViewportSize({width:390,height:844});
    assert.equal(await panel.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await panel.screenshot({path:path.join(OUT,'panel-mobile.png'),fullPage:true});
    await pos.screenshot({path:path.join(OUT,'caja-lista.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:['venta conectada','venta sin conexión','reinicio con pendiente','sincronización sin duplicados','stock negativo','cambio','seguridad origen y token','panel móvil y escritorio'],sales:repeated.cloud.sales,total:repeated.cloud.total,pending:repeated.local.pending,printing:false}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
