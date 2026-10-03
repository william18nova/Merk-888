const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
const BASE = 'http://127.0.0.1:8904';
const OUT = path.resolve(__dirname,'../outputs/full-local-replica');
(async () => {
  fs.mkdirSync(OUT,{recursive:true});
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const external=[],errors=[],failures=[];
    await context.route('**/*',route => {
      if(new URL(route.request().url()).origin !== BASE){external.push(route.request().url());return route.abort();}
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('response',r=>{if(r.status()>=400)failures.push({url:r.url(),status:r.status()});});
    await page.goto(BASE+'/local/estado/');
    await page.locator('[name="nombreusuario"]').fill('laboratorio');
    await page.locator('[name="contraseña"]').fill('prueba-local-2026');
    await page.getByRole('button',{name:'Entrar',exact:true}).click();
    await page.waitForURL(BASE+'/local/estado/');
    await page.getByText('Copia de referencia actualizada',{exact:true}).waitFor();
    assert.equal(await page.locator('.local-lab-modules a').count(),3);
    await page.screenshot({path:path.join(OUT,'replica-status.png'),fullPage:true});
    await page.goto(BASE+'/visualizar_productos/');
    await page.locator('#productosTable').getByText('ARROZ FICTICIO',{exact:true}).waitFor();
    await page.locator('#buscador-productos').fill('TOMATE');
    await page.waitForFunction(()=>document.querySelector('#productosTable tbody').textContent.includes('TOMATE')&&!document.querySelector('#productosTable tbody').textContent.includes('ARROZ'));
    await page.reload();
    await page.locator('#productosTable').getByText('TOMATE FICTICIO X GR',{exact:true}).waitFor();
    for(const url of ['/visualizar_inventarios/','/visualizar_clientes/']){
      assert.equal((await page.goto(BASE+url)).status(),200);
      await page.waitForLoadState('networkidle');
    }
    assert.deepEqual(external,[]);
    assert.deepEqual(errors,[]);
    assert.deepEqual(failures,[]);
    // No mostrar totales globales falsos cuando no se descargaron ventas/pagos.
    assert.equal((await page.goto(BASE+'/reportes/metricas/')).status(),409);
    const report={passed:true,externalRequests:0,javascriptErrors:0,referencePages:true,unsynchronizedReportsBlocked:true};
    fs.writeFileSync(path.join(OUT,'browser-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
