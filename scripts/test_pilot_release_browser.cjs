// Solo caja FICTICIA después del ensayo de dos instalaciones. Sin impresión.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const base = 'http://127.0.0.1:8966';
const out = path.resolve(__dirname, '../outputs/two-pc-pilot/browser');
(async () => {
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const external = [], errors = [];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {external.push(route.request().url()); return route.abort();}
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {window.print = () => {throw Error('No imprimir');};});
    await page.goto(base+'/local/sincronizacion/');
    await page.locator('[name=nombreusuario]').fill('laboratorio');
    await page.locator('[name="contraseña"]').fill('solo-ficticio-2026');
    await page.getByRole('button',{name:'Entrar',exact:true}).click();
    await page.waitForURL(base+'/local/sincronizacion/');
    assert.equal(await page.locator('.sync-movement').count(),7);
    assert.equal(await page.locator('.sync-state.accepted').count(),7);
    assert.match(await page.locator('.sync-notice.good').innerText(),/cierre fue confirmado/i);
    await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    await page.locator('.sync-movement a').first().click();
    await page.waitForLoadState('networkidle');
    assert.match(await page.locator('body').innerText(),/Confirmada/);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({passed:true,desktop:true,mobile:true,
      login:true,confirmedOperations:7,closeConfirmed:true,detail:true,externalRequests:0,javascriptErrors:0,printing:false},null,2));
    console.log('PASS: login y sincronización del paquete final; escritorio/móvil; 0 peticiones externas.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
