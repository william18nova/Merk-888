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
async function fixture({realConfirm = false, total = 1000} = {}) {
  const page = await browser.newPage();
  await page.route('**/*', route => {throw Error('No external requests allowed: '+route.request().url());});
  await page.setContent(`<form id="venta-form"><input type="hidden" id="nequi_notificacion_id" name="nequi_notificacion_id">
    <input type="hidden" id="pagos" name="pagos"><input type="hidden" id="medio_pago" name="medio_pago">
    <input type="hidden" id="efectivo_recibido" name="efectivo_recibido">
    <input type="hidden" id="empleado_password"><input type="hidden" id="codigo_descuento_merk2888"></form>
    <div id="myModal" style="display:block"><input class="pm-check" type="checkbox" value="nequi" checked>
    <input id="monto-recibido"><input id="mix-mode" type="checkbox"><div id="mix-error"></div><div id="nequi-payment-panel">
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
    const saleTotalForPayment = () => ${JSON.stringify(total)}, parseAmt = Number, safeNumber = Number, money = x => '$'+x;
    const isModalOpen = () => $modal.is(':visible');
    let modalConfirmBlocked = false;
    const isModalConfirmBlocked = () => modalConfirmBlocked;
    let confirmed = 0;
    ${realConfirm ? section('  const confirmPagoGuard', '  function getDigitFromAltEvent') : 'function triggerConfirmPago() {confirmed++;}'}
    function closeModal() {stopNequiAutoRefresh(); $modal.hide();}
    ${section('  const $nequiPanel =', '  function refreshEfectivoUI()')}
    ${section(handlersStart, '  const confirmPagoGuard')}
    ${keyboard}
    ${realConfirm ? `
      let confirmSubmitting = false;
      const REPRICE_ON_MODAL = false;
      const isMerk2888ClientSelected = () => false, isEmployeeClientSelected = () => false;
      const to2 = value => Number(value).toFixed(2);
      const $amountIn = $('#monto-recibido'), $hidPagos = $('#pagos'), $hidMedioPago = $('#medio_pago');
      const $hidEfectivoRecibido = $('#efectivo_recibido');
      const $hidEmpleadoPassword = $('#empleado_password'), $hidMerk2888Password = $('#codigo_descuento_merk2888');
      $('#venta-form').on('submit', event => {
        event.preventDefault(); event.stopImmediatePropagation(); confirmed++;
        window.submittedPayment = Object.fromEntries(new FormData(event.target));
      });
      ${section('  function buildPagosJSONOrError()', '  $nequiRefresh.on("click"')}
      ${section('  /* ================== CLICK CONFIRM (MIXTO / NO MIXTO)', '  /* ================== POS Agent helpers')}
    ` : ''}
    window.nequiTest = {
      render: renderNequiPaymentList, select: selectNequiPayment,
      reset: resetNequiPaymentState, load: loadNequiPayments, stop: stopNequiAutoRefresh,
      validate: validateSelectedNequiPayment,
      setItems(items) {nequiPaymentsCache = items; nequiPaymentsLoaded = true; renderNequiPaymentList();},
      selected() {return selectedNequiPayment?.id || null;},
      confirmed() {return confirmed;},
      setConfirmBlocked(value) {modalConfirmBlocked = value;},
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

test('Enter después de seleccionar con clic confirma sin quitar el Nequi', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
  } finally {await page.close();}
});

test('el flujo real de confirmar envía los pagos y el Nequi seleccionado una sola vez', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
    assert.equal(await page.locator('#myModal').isVisible(), false);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.deepEqual(JSON.parse(await page.locator('#pagos').inputValue()), [{medio_pago:'nequi',monto:'1000.00'}]);
  } finally {await page.close();}
});

test('Enter no envía la venta si el Nequi seleccionado no cubre el importe', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.evaluate(p => window.nequiTest.setItems([p]), {...item(1),monto_num:500});
    await page.locator('.nequi-payment-item').first().click();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.locator('#myModal').isVisible(), true);
    assert.match(await page.locator('#mix-error').textContent(), /no cubre/);
  } finally {await page.close();}
});

test('Enter no borra el aviso ni confirma un Nequi que pasó a no disponible', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.evaluate(async () => {
      window.fetch=async()=>({ok:true,json:async()=>({success:true,items:[]})});
      await window.nequiTest.load(true,{silent:true});
    });
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.match(await page.locator('#mix-error').textContent(), /ya no está disponible/);
  } finally {await page.close();}
});

test('primer Enter selecciona y segundo Enter confirma la venta', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
  } finally {await page.close();}
});

test('Enter en otra tarjeta cambia la selección sin cobrar con el Nequi anterior', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.locator('.nequi-payment-item').nth(1).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '2');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
  } finally {await page.close();}
});

test('mantener Enter al seleccionar no confirma por repetición de la tecla', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().focus();
    await page.keyboard.down('Enter');
    await page.keyboard.down('Enter');
    await page.keyboard.down('Enter');
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    await page.keyboard.up('Enter');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
  } finally {await page.close();}
});

test('Enter sobre el Nequi seleccionado respeta el bloqueo del escáner', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.evaluate(() => window.nequiTest.setConfirmBlocked(true));
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
    await page.evaluate(() => window.nequiTest.setConfirmBlocked(false));
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
  } finally {await page.close();}
});

test('Enter sobre Actualizar no confirma aunque haya un Nequi seleccionado', async () => {
  const page = await fixture();
  try {
    await page.locator('.nequi-payment-item').first().click();
    await page.locator('#nequi-refresh-payments').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.locator('#nequi_notificacion_id').inputValue(), '1');
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

for (const key of ['click', 'Enter']) {
  test(`efectivo: ${key} envía el recibido antes del submit para imprimir el cambio`, async () => {
    const page = await fixture({realConfirm:true});
    try {
      await page.locator('.pm-check').evaluate(el => {el.value = 'efectivo';});
      await page.locator('#monto-recibido').fill('2000');
      if (key === 'Enter') await page.keyboard.press('Enter');
      else await page.locator('#confirmar-pago').click();
      const sent = await page.evaluate(() => window.submittedPayment);
      assert.equal(sent.efectivo_recibido, '2000.00');
      assert.deepEqual(JSON.parse(sent.pagos), [{medio_pago:'efectivo',monto:'1000.00'}]);
      assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
    } finally {await page.close();}
  });
}

test('efectivo: sin escribir recibido se envía el total exacto', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.locator('.pm-check').evaluate(el => {el.value = 'efectivo';});
    await page.locator('#confirmar-pago').click();
    assert.equal(await page.evaluate(() => window.submittedPayment.efectivo_recibido), '1000.00');
  } finally {await page.close();}
});

test('efectivo insuficiente no envía una venta ni imprime una factura', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.locator('.pm-check').evaluate(el => {el.value = 'efectivo';});
    await page.locator('#monto-recibido').fill('500');
    await page.locator('#confirmar-pago').click();
    assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 0);
    assert.equal(await page.evaluate(() => window.submittedPayment), undefined);
    assert.match(await page.locator('#mix-error').textContent(), /insuficiente/);
  } finally {await page.close();}
});

for (const medio of ['nequi', 'tarjeta']) {
  test(`${medio}: no arrastra el recibido de un pago anterior en efectivo`, async () => {
    const page = await fixture({realConfirm:true});
    try {
      await page.locator('.pm-check').evaluate((el, value) => {el.value = value;}, medio);
      await page.locator('#efectivo_recibido').evaluate(el => {el.value = '2000.00';});
      await page.locator('#monto-recibido').fill('2000');
      await page.locator('#confirmar-pago').click();
      assert.equal(await page.evaluate(() => window.submittedPayment.efectivo_recibido), '');
      assert.equal(await page.evaluate(() => window.nequiTest.confirmed()), 1);
    } finally {await page.close();}
  });
}

test('una venta de total cero no envía efectivo recibido de una venta anterior', async () => {
  const page = await fixture({realConfirm:true,total:0});
  try {
    await page.locator('#efectivo_recibido').evaluate(el => {el.value = '2000.00';});
    await page.locator('#confirmar-pago').click();
    assert.equal(await page.evaluate(() => window.submittedPayment.efectivo_recibido), '');
    assert.deepEqual(JSON.parse(await page.locator('#pagos').inputValue()), []);
  } finally {await page.close();}
});

test('pago mixto conserva los importes y borra el recibido del modo efectivo', async () => {
  const page = await fixture({realConfirm:true});
  try {
    await page.evaluate(() => {
      document.querySelector('#efectivo_recibido').value = '2000.00';
      document.querySelector('#monto-recibido').value = '2000';
      document.querySelector('#mix-mode').checked = true;
      const nequiCheck = document.querySelector('.pm-check');
      const nequiRow = document.createElement('div');
      nequiRow.className = 'pm-row';
      nequiCheck.replaceWith(nequiRow);
      nequiRow.append(nequiCheck);
      nequiRow.insertAdjacentHTML('beforeend', '<input class="pm-amt" data-medio="nequi" value="600">');
      document.querySelector('#myModal').insertAdjacentHTML('beforeend',
        '<div class="pm-row"><input class="pm-check" type="checkbox" value="efectivo" checked><input class="pm-amt" data-medio="efectivo" value="400"></div>');
    });
    await page.locator('#confirmar-pago').click();
    const sent = await page.evaluate(() => window.submittedPayment);
    assert.equal(sent.efectivo_recibido, '');
    assert.equal(sent.medio_pago, 'mixto');
    assert.deepEqual(JSON.parse(sent.pagos), [
      {medio_pago:'nequi',monto:'600.00'}, {medio_pago:'efectivo',monto:'400.00'},
    ]);
  } finally {await page.close();}
});
