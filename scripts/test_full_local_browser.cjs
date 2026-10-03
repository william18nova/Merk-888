// Prueba del Django completo local. No usa datos reales ni imprime.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const BASE = 'http://127.0.0.1:8903';
const OUT = path.resolve(__dirname, '../outputs/full-local');

(async () => {
  fs.mkdirSync(OUT, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1050}});
    const external = [], errors = [], failed = [];
    // Simula falta de internet sin impedir la conexión al proceso local.
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== BASE) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    await context.addInitScript(() => {window.print = () => {throw Error('Impresión excluida');};});
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => {if(r.status() >= 400) failed.push({url:r.url(),status:r.status()});});
    page.on('dialog', async d => {errors.push(d.message()); await d.dismiss();});
    await page.goto(BASE + '/local/estado/');
    await page.locator('[name="nombreusuario"]').fill('laboratorio');
    await page.locator('[name="contraseña"]').fill('prueba-local-2026');
    await page.getByRole('button', {name:'Entrar',exact:true}).click();
    await page.waitForURL(BASE + '/local/estado/');
    assert.equal(await page.locator('.local-lab-modules a').count(), 9);
    assert(await page.locator('.nv__links a').count() > 15);
    await page.screenshot({path:path.join(OUT,'modules-desktop.png'),fullPage:true});
    await page.goto(BASE + '/visualizar_productos/');
    await page.locator('#productosTable').getByText('ARROZ FICTICIO',{exact:true}).waitFor();
    await page.locator('#buscador-productos').fill('TOMATE');
    await page.waitForFunction(() => document.querySelector('#productosTable tbody').textContent.includes('TOMATE') && !document.querySelector('#productosTable tbody').textContent.includes('ARROZ'));
    assert(await page.evaluate(() => !!window.jQuery.fn.DataTable));
    await page.reload();
    await page.locator('#productosTable').getByText('TOMATE FICTICIO X GR',{exact:true}).waitFor();
    await page.goto(BASE + '/visualizar_categorias/');
    await page.locator('#categoriasTable_wrapper').waitFor();
    await page.goto(BASE + '/visualizar_inventarios/');
    await page.waitForLoadState('networkidle');
    for (const url of ['/visualizar_clientes/','/visualizar_proveedores/','/visualizar_empleados/','/caja/pagos/','/turnos_caja_dashboard/']) {
      const response = await page.goto(BASE + url);
      assert.equal(response.status(),200,url);
      await page.waitForLoadState('networkidle');
    }
    await page.goto(BASE + '/horarios/empleados/');
    await page.waitForFunction(() => document.querySelector('#schedule-period').textContent !== 'Cargando calendario…');
    await page.goto(BASE + '/reportes/metricas/');
    await page.waitForFunction(() => window.Chart && Chart.getChart('chart-daily'));
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#mn-error').isVisible(), false);
    const balance = await page.locator('#table-payment-balance').innerText();
    assert.match(balance,/2\.500/);
    assert.match(balance,/1\.000/);
    assert.match(balance,/1\.500/);
    await page.goto(BASE + '/local/estado/');
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert(await page.locator('.local-lab-banner').isVisible());
    const navbarBottom = await page.locator('.nv').evaluate(el => el.getBoundingClientRect().bottom);
    const bannerTop = await page.locator('.local-lab-banner').evaluate(el => el.getBoundingClientRect().top);
    assert(bannerTop >= navbarBottom - 1, 'El aviso del laboratorio no debe quedar detrás del menú');
    await page.screenshot({path:path.join(OUT,'modules-mobile.png'),fullPage:true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.locator('#nvBurger').click();
    assert.equal(await page.locator('#nvBurger').getAttribute('aria-expanded'),'true');
    assert.deepEqual(external, [], 'No debe solicitar dependencias de internet');
    assert.deepEqual(failed, [], 'No debe tener endpoints o recursos rotos');
    assert.deepEqual(errors, [], 'No debe tener errores JavaScript');
    // Operación no conectada: aviso explícito, sin guardar ni simular éxito.
    const blocked = await page.goto(BASE + '/agregar_categoria/');
    assert.equal(blocked.status(),409);
    await page.getByRole('heading',{name:'Esta función aún está en preparación'}).waitFor();
    const report = {passed:true,externalRequests:external.length,javascriptErrors:errors.length,
                    tested:'UI original, tablas/búsqueda/recarga, calendario, métricas, navegación móvil y bloqueo de escrituras'};
    fs.writeFileSync(path.join(OUT,'browser-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(e => {console.error(e);process.exitCode=1;});
