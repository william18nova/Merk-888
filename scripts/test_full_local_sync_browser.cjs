// Datos ficticios de --review-demo; sin conexiones externas ni impresión.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const BASE = 'http://127.0.0.1:8931';
const OUT = path.resolve(__dirname, '../outputs/full-local-sync');
(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
    const errors = [], external = [];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== BASE) {external.push(route.request().url()); return route.abort();}
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {window.print = () => {throw Error('Impresión excluida');};});
    await page.goto(BASE + '/local/sincronizacion/');
    await page.locator('[name=nombreusuario]').fill('laboratorio');
    await page.locator('[name="contraseña"]').fill('prueba-local-2026');
    await page.getByRole('button', {name:'Entrar', exact:true}).click();
    await page.waitForURL(BASE + '/local/sincronizacion/');
    assert.equal(await page.locator('.sync-movement').count(), 2);
    assert.match(await page.locator('.sync-movements').innerText(), /Por revisar/);
    await page.screenshot({path:path.join(OUT, 'sync-desktop.png'), fullPage:true});
    await page.setViewportSize({width:390, height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path:path.join(OUT, 'sync-mobile.png'), fullPage:true});
    await page.locator('[name=state]').selectOption('conflict');
    await page.getByRole('button', {name:'Filtrar', exact:true}).click();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.sync-movement').count(), 1);
    await page.locator('.sync-movement a').click();
    await page.locator('#sync-note').fill('Revisé el producto en el servidor ficticio.');
    await page.getByRole('button', {name:'Verificar de nuevo en la nube', exact:true}).click();
    await page.locator('#sync-confirm[open]').waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path:path.join(OUT, 'confirm-mobile.png')});
    await page.getByRole('button', {name:'Cancelar', exact:true}).click();
    assert.equal(await page.locator('#sync-confirm[open]').count(), 0);
    assert.equal(await page.locator('body.is-page-leaving').count(), 0);
    await page.setViewportSize({width:1440, height:1050});
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path:path.join(OUT, 'detail-desktop.png'), fullPage:true});
    await page.getByRole('button', {name:'Verificar de nuevo en la nube', exact:true}).click();
    await page.getByRole('button', {name:'Sí, verificar', exact:true}).click();
    await page.waitForLoadState('networkidle');
    assert.match(await page.locator('.sync-timeline').innerText(), /Revisé el producto/);
    assert.match(await page.locator('.sync-heading .sync-state').innerText(), /Pendiente/);
    await page.goto(BASE + '/local/estado/');
    await page.getByRole('button', {name:'Restablecer y sincronizar', exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#local-sale-demo-result').textContent.includes('Comunicación restablecida'));
    await page.goto(BASE + '/local/sincronizacion/');
    assert.equal(await page.locator('.sync-movement .sync-state.accepted').count(), 2);
    await page.getByRole('button', {name:'Sincronizar pendientes', exact:true}).click();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.sync-movement').count(), 2);
    assert.equal(await page.locator('.sync-movement .sync-state.accepted').count(), 2);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    const report = {passed:true, desktop:true, mobile:true, filters:true, confirmation:true, audit:true,
      offlineRetry:true, reconnection:true, unchangedOperationCount:2, externalRequests:0, javascriptErrors:0};
    fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2)); console.log(report);
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
