// Generar Venta ORIGINAL + servidor ficticio, nunca una impresora o BD real.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const BASE = 'http://127.0.0.1:8902';
const OUT = path.resolve(__dirname, '../outputs/hybrid-original');
const control = JSON.parse(fs.readFileSync(path.join(OUT, 'test-control.json'))).token;
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
    const page = await context.newPage(), errors = [], external = [], dialogs = [];
    const network = async offline => {
      const r = await page.request.post(BASE + '/__test__/network', {headers: {'X-Test-Control': control}, data: {offline}});
      assert.equal(r.status(), 200); return r.json();
    };
    const history = async () => (await page.request.get(BASE + '/api/history')).json();
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => {if (!r.url().startsWith(BASE)) external.push(r.url());});
    page.on('dialog', async d => {dialogs.push(d.message()); await d.accept();});
    await page.addInitScript(() => {window.print = () => {throw Error('Impresión excluida');};});
    await network(false);
    const before = (await history()).length;
    await page.goto(BASE + '/generar_venta/');
    await page.locator('#producto_busqueda_nombre').waitFor();
    assert(await page.evaluate(() => getComputedStyle(document.body).backgroundImage.includes('gradient')));
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg-1').trim()), '#153060');
    async function add(term) {
      await page.locator('#producto_busqueda_nombre').fill(term);
      await page.locator('.ui-autocomplete:visible .ui-menu-item').first().click();
      await page.locator('#detalle-productos tbody tr').first().waitFor();
    }
    async function total(value) {await page.waitForFunction(v => document.querySelector('#total').textContent.includes(v), value);}
    async function charge(cash) {
      await page.locator('#generar-venta').click();
      await page.locator('#myModal').waitFor({state:'visible'});
      await page.locator('.pm-check[value="efectivo"]').check();
      await page.locator('#monto-recibido').fill(String(cash));
      await page.locator('#monto-recibido').dispatchEvent('input');
      await page.locator('#confirmar-pago').click();
      await page.waitForFunction(() => !document.querySelector('#detalle-productos tbody tr'));
    }
    await add('tomate');
    await page.locator('.qty-input').fill('500');
    await page.locator('.qty-input').dispatchEvent('input');
    await page.locator('.qty-input').dispatchEvent('change');
    await total('1.900');
    // Se corta la nube mientras hay un carrito en la MISMA página.
    await network(true);
    await page.waitForFunction(() => document.querySelector('#hybrid-connection').textContent.includes('Sin conexión'));
    assert.equal(await page.locator('.qty-input').inputValue(), '500');
    assert.equal(page.url(), BASE + '/generar_venta/');
    await page.screenshot({path: path.join(OUT, 'original-desktop-offline.png'), fullPage: true});
    await charge(2000);
    let rows = await history();
    assert.equal(rows.length, before + 1); assert.equal(rows[0].state, 'pending');
    assert.match(rows[0].receipt, /CAMBIO: \$ 100/);

    // Recargar sin internet no redirige ni cambia de formulario.
    await page.reload(); await page.locator('#producto_busqueda_nombre').waitFor();
    await add('manzana acida'); await total('2.200');
    await charge(3000);
    rows = await history(); assert.equal(rows.length, before + 2);
    await network(false);
    await page.waitForFunction(() => document.querySelector('#hybrid-connection').textContent.includes('sincronizadas'));
    rows = await history(); assert(rows.every(r => r.state === 'accepted'));
    const ids = rows.map(r => r.id); assert.equal(new Set(ids).size, rows.length);

    // Dos pestañas son carritos independientes, cada una con su referencia.
    const second = await context.newPage();
    await second.goto(BASE + '/generar_venta/');
    await add('agua'); await total('1.800');
    await second.locator('#codigo_o_barras').fill('770123');
    await second.locator('#codigo_o_barras').press('Enter');
    await second.locator('#detalle-productos tbody tr').waitFor();
    assert.equal(await page.locator('.cart-product-name').textContent(), 'AGUA DE PRUEBA');
    assert.match(await second.locator('.cart-product-name').textContent(), /TOMATE/);

    // POST local guardado, respuesta perdida: debe resolver el mismo UUID.
    let lost = false;
    await page.route('**/api/checkout', async route => {
      if (!lost) {lost = true; await route.fetch(); await route.abort('failed');}
      else await route.continue();
    });
    await charge(2000);
    assert(lost); assert.equal((await history()).length, before + 3);
    await page.unroute('**/api/checkout');
    await second.close();

    // Recargar un carrito sin cobrar permite recuperarlo; no un cobro registrado.
    await add('tomate');
    await page.reload();
    await page.locator('#venta-draft-toggle').waitFor({state: 'visible'});
    await page.locator('#venta-draft-toggle').click();
    await page.locator('.js-draft-restore').first().click();
    await page.locator('#detalle-productos tbody tr').waitFor();
    await page.locator('#generar-venta:enabled').waitFor();

    await page.setViewportSize({width: 390, height: 844});
    await page.locator('.panel-right').scrollIntoViewIfNeeded();
    await page.locator('#generar-venta').waitFor({state: 'visible'});
    await page.screenshot({path: path.join(OUT, 'original-mobile-cart.png')});
    await page.screenshot({path: path.join(OUT, 'original-mobile.png'), fullPage: true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.locator('#hybrid-history-toggle').click();
    await page.locator('#hybrid-history details').first().waitFor();
    await page.locator('#hybrid-history-close').click();
    assert.deepEqual(errors, []); assert.deepEqual(external, []);

    // Cierre tras el commit pero antes de recibir respuesta: el borrador no
    // puede recuperarse como otra venta y cobrar de nuevo esos productos.
    const closeContext = await browser.newContext();
    const closing = await closeContext.newPage();
    await closing.goto(BASE + '/generar_venta/');
    await closing.locator('#codigo_o_barras').fill('770123');
    await closing.locator('#codigo_o_barras').press('Enter');
    await closing.locator('#detalle-productos tbody tr').waitFor();
    let committed, release;
    const committedPromise = new Promise(resolve => {committed=resolve;});
    const releasePromise = new Promise(resolve => {release=resolve;});
    await closing.route('**/api/checkout', async route => {
      await route.fetch(); committed(); await releasePromise;
      try {await route.abort('failed');} catch (_) {}
    });
    await closing.locator('#generar-venta').click();
    await closing.locator('.pm-check[value="efectivo"]').check();
    await closing.locator('#monto-recibido').fill('1000');
    await closing.locator('#monto-recibido').dispatchEvent('input');
    await closing.locator('#confirmar-pago').click();
    await committedPromise;
    await closing.close(); release();
    const recover = await closeContext.newPage(), recoveryDialogs = [];
    recover.on('dialog', async d => {recoveryDialogs.push(d.message()); await d.accept();});
    await recover.goto(BASE + '/generar_venta/');
    await recover.locator('#venta-draft-toggle').waitFor({state:'visible'});
    await recover.locator('#venta-draft-toggle').click();
    await recover.locator('.js-draft-restore').first().click();
    await recover.waitForFunction(() => document.getElementById('venta-draft-center').hidden);
    assert.equal(await recover.locator('#detalle-productos tbody tr').count(), 0);
    assert(recoveryDialogs.some(t => t.includes('ya está registrada')));
    assert.equal((await history()).length, before + 4);
    await closeContext.close();
    console.log(JSON.stringify({ok:true, salesCreated:4, errors, external, dialogs, recoveryDialogs, url:page.url()}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
