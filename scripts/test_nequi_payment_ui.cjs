// NODE_PATH must include Playwright. No production server, database or payments.
// node --test scripts/test_nequi_payment_ui.cjs
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'mainApp/static/javascript/generar_venta.js'), 'utf8').replace(/\r\n/g, '\n');
function section(from, to) {
  const start = source.indexOf(from);
  assert.ok(start >= 0, from);
  const end = source.indexOf(to, start);
  assert.ok(end > start, to);
  return source.slice(start, end);
}
let browser;
before(async () => { browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'}); });
after(async () => { await browser?.close(); });
const item = id => ({id, monto_num: 5000, monto_label:'$5.000', nombre:'Persona '+id, texto:'Pago de prueba', fecha:'2026-09-23', hora:'10:00'});
async function fixture() {
  const page = await browser.newPage();
  await page.route('**/*', route => {throw Error('No external requests allowed: '+route.request().url());});
  await page.setContent(`<form id="venta-form"><input type="hidden" id="nequi_notificacion_id" name="nequi_notificacion_id"></form>
    <div id="myModal" style="display:block"><input class="pm-check" type="checkbox" value="nequi" checked>
    <input id="mix-mode" type="checkbox"><div id="mix-error"></div><div id="nequi-payment-panel">
    <button id="nequi-refresh-payments">Actualizar</button><div id="nequi-selected-payment" hidden></div>
    <div id="nequi-payment-list" style="max-height:260px;overflow:auto;width:500px"></div><small id="nequi-payment-status"></small>
    </div><button id="confirmar-pago">Confirmar</button></div>
    <style>.nequi-payment-item{display:block;width:100%;height:85px}.nequi-payment-meta{display:block}</style>`);
  await page.addScriptTag({content:fs.readFileSync(path.join(root, 'mainApp/static/vendor/jquery/jquery-3.6.4.min.js'), 'utf8')});
  const keyboard = section('  $(document).on("keydown", function (e) {\n    if (!$modal.is', '  $modal.on("change", ".pm-check"');
  const handlersStart = source.includes('/* Nequi interaction handlers */')
    ? '  /* Nequi interaction handlers */' : '  $modal.on("click", ".nequi-payment-item"';
  await page.addScriptTag({content:`(() => {
    const $ = window.jQuery, $modal = $('#myModal'), $hidNequiNotification = $('#nequi_notificacion_id'), $mixMode = $('#mix-mode');
    const NEQUI_DISPONIBLES_URL = '/payments', NEQUI_FEATURE_KEY = 'nequi_api_recepcion';
    let nequiApiEnabled = true;
    const saleTotalForPayment = () => 1000, parseAmt = Number, safeNumber = Number, money = x => '$'+x;
    const isModalOpen = () => $modal.is(':visible');
    const isModalConfirmBlocked = () => false;
    let confirmed = 0;
    function triggerConfirmPago() {confirmed++;}
    function closeModal() {stopNequiAutoRefresh(); $modal.hide();}
    ${section('  const $nequiPanel =', '  function refreshEfectivoUI()')}
    ${section(handlersStart, '  const confirmPagoGuard')}
    ${keyboard}
    window.nequiTest = {
      render: renderNequiPaymentList, select: selectNequiPayment,
      reset: resetNequiPaymentState, load: loadNequiPayments, stop: stopNequiAutoRefresh,
      validate: validateSelectedNequiPayment,
      setItems(items) {nequiPaymentsCache = items; nequiPaymentsLoaded = true; renderNequiPaymentList();},
      selected() {return selectedNequiPayment?.id || null;},
      confirmed() {return confirmed;},
      state() {return {loading:nequiPaymentsLoading, items:nequiPaymentsCache.map(x=>x.id)};},
    };
  })();`});
  await page.evaluate(items => window.nequiTest.setItems(items), [item(1), item(2), item(3), item(4)]);
  return page;
}

test('el clic selecciona aunque la actualización llegue entre pulsar y soltar', async () => {
  const page = await fixture();
  try {
    const button = page.locator('.nequi-payment-item').first();
    const rect = await button.boundingBox();
    await page.mouse.move(rect.x + 30, rect.y + 30);
    await page.mouse.down();
    await page.evaluate(() => window.nequiTest.render());
    await page.mouse.up();
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
  } finally {await page.close();}
});

test('Enter sobre un pago lo selecciona sin confirmar la venta', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
  } finally {await page.close();}
});

test('un pago nuevo durante el clic no mueve el objetivo ni selecciona otro', async () => {
  const page = await fixture();
  try {
    const rect = await page.locator('.nequi-payment-item').first().boundingBox();
    await page.mouse.move(rect.x+30, rect.y+30); await page.mouse.down();
    await page.evaluate(items => window.nequiTest.setItems(items), [item(99),item(1),item(2)]);
    await page.mouse.up();
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
  } finally {await page.close();}
});

test('refrescar sin cambios conserva tarjetas, scroll, foco y el botón Quitar', async () => {
  const page = await fixture();
  try {
    await page.evaluate(p => window.nequiTest.select(p), item(1));
    await page.locator('#nequi-clear-payment').focus();
    const result = await page.evaluate(() => {
      const list=document.querySelector('#nequi-payment-list'), button=list.firstChild;
      const clear=document.querySelector('#nequi-clear-payment');
      list.scrollTop=70;
      for(let i=0;i<20;i++) window.nequiTest.render();
      return {same:button===list.firstChild, scroll:list.scrollTop, focus:document.activeElement===clear};
    });
    assert.deepEqual(result, {same:true,scroll:70,focus:true});
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
  } finally {await page.close();}
});

test('una consulta iniciada antes del clic no borra la nueva selección', async () => {
  const page = await fixture();
  try {
    await page.evaluate(() => {
      window.fetch = () => new Promise(resolve => {window.resolveFetch=resolve;});
      window.pendingLoad=window.nequiTest.load(true,{silent:true});
    });
    await page.locator('.nequi-payment-item').first().click();
    await page.evaluate(async items => {
      window.resolveFetch({ok:true,json:async()=>({success:true,items})}); await window.pendingLoad;
    }, [item(2)]);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.equal(await page.evaluate(() => window.nequiTest.validate()), '');
  } finally {await page.close();}
});

test('cada actualización pide comprobar el ID seleccionado y conserva la asociación', async () => {
  const page = await fixture();
  try {
    await page.evaluate(async p => {
      window.nequiTest.select(p);
      window.fetch=async url=>{window.requestedUrl=url;return {ok:true,json:async()=>({success:true,items:[p]})};};
      await window.nequiTest.load(true,{silent:true});
    }, item(1));
    assert.match(await page.evaluate(()=>window.requestedUrl), /selected_id=1/);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.equal(await page.locator('.nequi-payment-item').getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('#nequi-selected-payment').textContent(), /Se vinculará al confirmar/);
  } finally {await page.close();}
});

test('un pago usado por otra caja exige decidir, no convierte la venta en no vinculada', async () => {
  const page = await fixture();
  try {
    await page.evaluate(async p => {
      window.nequiTest.select(p);
      window.fetch=async()=>({ok:true,json:async()=>({success:true,items:[]})});
      await window.nequiTest.load(true,{silent:true});
    }, item(1));
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.match(await page.evaluate(()=>window.nequiTest.validate()), /ya no está disponible/);
    await page.locator('#nequi-clear-payment').click();
    assert.equal(await page.evaluate(()=>window.nequiTest.validate()), '');
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '');
  } finally {await page.close();}
});

test('fallo de red conserva la selección y el mensaje de error no se borra al renderizar', async () => {
  const page = await fixture();
  try {
    await page.evaluate(async p => {
      window.nequiTest.select(p);
      window.fetch=async()=>{throw Error('offline');};
      await window.nequiTest.load(true,{silent:true});
    }, item(1));
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.match(await page.locator('#nequi-payment-status').textContent(), /No se pudo actualizar/);
  } finally {await page.close();}
});

test('una respuesta del modal anterior no sobreescribe la consulta nueva ni su estado de carga', async () => {
  const page = await fixture();
  try {
    await page.evaluate(() => {
      window.resolvers=[];
      window.fetch=()=>new Promise(resolve=>window.resolvers.push(resolve));
      window.oldLoad=window.nequiTest.load(true,{silent:true});
      window.nequiTest.stop();
      window.newLoad=window.nequiTest.load(true,{silent:true});
    });
    const state = await page.evaluate(async items => {
      window.resolvers[0]({ok:true,json:async()=>({success:true,items})});
      await window.oldLoad; return window.nequiTest.state();
    }, [item(99)]);
    assert.equal(state.loading,true); assert.deepEqual(state.items,[1,2,3,4]);
    await page.evaluate(async items => {
      window.resolvers[1]({ok:true,json:async()=>({success:true,items})}); await window.newLoad;
    }, [item(2)]);
    assert.deepEqual(await page.evaluate(()=>window.nequiTest.state()), {loading:false,items:[2]});
  } finally {await page.close();}
});

test('Espacio selecciona la tarjeta incluso si llega una nueva al mantenerlo pulsado', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().focus();
    await page.keyboard.down('Space');
    await page.evaluate(items=>window.nequiTest.setItems(items),[item(99),item(1),item(2)]);
    await page.keyboard.up('Space');
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.equal(await page.evaluate(()=>window.nequiTest.confirmed()),0);
  } finally {await page.close();}
});

test('la selección se incluye en los datos del formulario, sin enviar ninguna venta', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().click();
    assert.equal(await page.evaluate(() => new FormData(document.querySelector('#venta-form')).get('nequi_notificacion_id')), '1');
  } finally {await page.close();}
});

test('un importe insuficiente se selecciona visiblemente pero no autoriza el cobro', async () => {
  const page = await fixture();
  try {
    await page.evaluate(p=>window.nequiTest.setItems([p]), {...item(1),monto_num:500,monto_label:'$500'});
    await page.locator('.nequi-payment-item').first().click();
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.match(await page.evaluate(()=>window.nequiTest.validate()), /no cubre/);
  } finally {await page.close();}
});
