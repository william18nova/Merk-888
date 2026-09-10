// UI aislada con la plantilla, CSS y JavaScript reales; no inicia Django ni usa su BD.
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}

test("cierre en páginas distintas, solo hacia adelante y con valores conservados tras F5", {skip: !chromium}, async () => {
  const template = fs.readFileSync(path.join(__dirname, "templates/turno_caja.html"), "utf8");
  const initial = {
    turno_id: 8080, estado: "ABIERTO", base: 1000,
    puntopago: {nombre: "CAJA DE PRUEBA"}, cajero: {nombreusuario: "CAJERO DE PRUEBA"},
    inicio: "2026-09-10T07:00:00-05:00", cierre_iniciado: "2026-09-10T14:00:00-05:00",
    medios: [{metodo: "efectivo", label: "Efectivo"}, {metodo: "nequi", label: "Nequi"}, {metodo: "tarjeta", label: "Tarjeta / Caja Social"}],
  };
  const block = name => template.match(new RegExp(`{% block ${name} %}([\\s\\S]*?){% endblock %}`))[1];
  // Replica únicamente los condicionales de página; Django también los prueba
  // directamente en test_cash_turn_paid_invoices.py.
  function selectPage(content, page) {
    const stack = []; let visible = true; let result = "";
    for (const part of content.split(/({%\s*(?:if\s+[^%]+|else|endif)\s*%})/g)) {
      const conditional = part.match(/^{%\s*if\s+(.+?)\s*%}$/);
      if (conditional) {
        const condition = conditional[1];
        const match = condition.match(/^close_page == "(payments|cash|media)"$/);
        assert.ok(match || ["close_page", "not close_page"].includes(condition), `Condición inesperada: ${condition}`);
        const truth = match ? page === match[1] : condition === "close_page" ? !!page : !page;
        stack.push({parent: visible, truth}); visible = visible && truth;
      } else if (part.match(/^{%\s*else\s*%}$/)) {
        visible = stack.at(-1).parent && !stack.at(-1).truth;
      } else if (part.match(/^{%\s*endif\s*%}$/)) {
        visible = stack.pop().parent;
      } else if (visible) result += part;
    }
    assert.equal(stack.length, 0);
    return result;
  }
  const render = (content, page) => selectPage(content, page)
    .replace('{{ turno_activo_inicial|json_script:"turno-activo-inicial" }}', `<script id="turno-activo-inicial" type="application/json">${JSON.stringify(initial)}</script>`)
    .replace('{{ close_page|default:\'\'|escapejs }}', page)
    .replace(/{% static '([^']+)' %}/g, "/static/$1")
    .replace(/{% url 'turno_caja_cierre_(pagos|efectivo|medios)' turno_id=0 %}/g, "/turno_caja/cierre/0/$1/")
    .replace(/{% url '([^']+)' %}/g, "/fixture/$1")
    .replace(/{% csrf_token %}/g, '<input type="hidden" name="csrfmiddlewaretoken" value="test-only">')
    .replace(/{%[\s\S]*?%}/g, "").replace(/{{[\s\S]*?}}/g, "");
  const html = page => `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${render(block("extra_head"), page)}</head><body>${render(block("content"), page)}</body></html>`;
  const posts = [];
  let starts = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/fixture/turno_caja_iniciar_cierre") {
      starts += 1;
      initial.estado = "CIERRE";
      req.resume();
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({...initial, success: true}));
      return;
    }
    if (url.pathname === "/fixture/turno_caja_cerrar") {
      let body = "";
      req.on("data", data => { body += data; });
      req.on("end", () => {
        posts.push(String(req.headers["content-type"]).includes("multipart/form-data")
          ? Object.fromEntries([...body.matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)\r\n--/g)].map(match => [match[1], match[2]]))
          : Object.fromEntries(new URLSearchParams(body)));
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({success: false, error: "Prueba aislada: no se guardó ningún turno real."}));
      });
      return;
    }
    const assets = {"/static/css/turno_caja.css": "text/css", "/static/javascript/turno_caja.js": "application/javascript"};
    if (assets[url.pathname]) {
      res.setHeader("Content-Type", assets[url.pathname]);
      res.end(fs.readFileSync(path.join(__dirname, url.pathname.slice(1)))); return;
    }
    const page = {pagos: "payments", efectivo: "cash", medios: "media"}[url.pathname.split("/").filter(Boolean).at(-1)] || "";
    res.setHeader("Content-Type", "text/html"); res.end(html(page));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, ...(process.env.SALE_DRAFT_BROWSER_CHANNEL ? {channel: process.env.SALE_DRAFT_BROWSER_CHANNEL} : {})});
    const context = await browser.newContext({viewport: {width: 1280, height: 1000}});
    await context.route("https://**/*", route => route.abort());
    const page = await context.newPage();
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}/`;
    await page.goto(url);
    await page.locator("#stepOpen").waitFor({state: "visible"});
    await page.locator("#btnIniciarCierre").click();
    await page.locator("#tc-confirm-ok").click();
    await page.locator("#closePaymentsStep").waitFor({state: "visible"});
    assert.equal(starts, 1);
    assert.match(page.url(), /\/turno_caja\/cierre\/8080\/pagos\/$/);
    assert.equal(await page.locator("#closeCashStep, #closeMediaStep, #closeDenomInputs").count(), 0);
    const staleTab = await context.newPage();
    await staleTab.goto(url);
    await staleTab.locator("#closePaymentsStep").waitFor({state: "visible"});
    if (process.env.POS_TEST_ARTIFACT_DIR) {
      await page.locator("#stepClose").screenshot({path: path.join(process.env.POS_TEST_ARTIFACT_DIR, "cierre-pagos-desktop.png")});
    }
    await page.locator("#facturas_pagadas").fill("-1");
    await page.locator("#btnPaymentsNext").click();
    assert.equal(await page.locator("#closeCashStep").isVisible(), false);
    await page.locator("#facturas_pagadas").fill("2000");
    await page.locator("#btnPaymentsNext").click();
    assert.equal(await page.locator("#closePaymentsStep").isVisible(), true);
    await page.locator("#tc-confirm .btn-ghost").click();
    assert.equal(await page.locator("#closePaymentsStep").isVisible(), true);
    assert.equal(await page.locator("#facturas_pagadas").isEditable(), true);
    await page.locator("#btnPaymentsNext").click();
    await page.locator("#tc-confirm-ok").click();
    await page.locator("#closeCashStep").waitFor({state: "visible"});
    assert.match(page.url(), /\/efectivo\/$/);
    assert.equal(await page.locator("#closePaymentsStep, #closeMediaStep").count(), 0);
    await staleTab.locator("#closeCashStep").waitFor({state: "visible"});
    assert.equal(await staleTab.locator("#facturas_pagadas").evaluate(el => el.readOnly), true);
    assert.equal(await staleTab.locator("#facturas_pagadas").inputValue(), "2000");
    assert.equal(await page.locator("#closeCashWarningTitle").isVisible(), true);
    assert.equal(await page.locator("#facturas_pagadas").isVisible(), false);
    assert.equal(await page.locator("#facturas_pagadas").evaluate(el => el.readOnly), true);
    assert.equal(await page.locator("#btnBackPayments, #btnBackCash").count(), 0);
    await page.reload();
    await page.locator("#closeCashStep").waitFor({state: "visible"});
    assert.equal(await page.locator("#facturas_pagadas").inputValue(), "2000");
    await page.locator("#close_b5000").fill("2");
    if (process.env.POS_TEST_ARTIFACT_DIR) {
      await page.locator("#stepClose").screenshot({path: path.join(process.env.POS_TEST_ARTIFACT_DIR, "cierre-conteo-desktop.png")});
    }
    await page.locator("#btnCashNext").click();
    await page.locator("#tc-confirm-ok").click();
    await page.locator("#closeMediaStep").waitFor({state: "visible"});
    await staleTab.locator("#closeMediaStep").waitFor({state: "visible"});
    assert.equal(Number(await staleTab.locator("#efectivo_entregado").inputValue()), 10000);
    await staleTab.close();
    assert.equal(await page.locator("#closeMediaStep").isVisible(), true);
    assert.match(page.url(), /\/medios\/$/);
    assert.equal(await page.locator("#closePaymentsStep, #closeCashStep, #closeDenomInputs").count(), 0);
    assert.equal(await page.locator("#facturas_pagadas").isVisible(), false);
    await page.locator('[data-in="nequi"]').fill("3000");
    await page.waitForFunction(() => document.getElementById("mVentas").textContent.includes("14.000"));
    assert.equal(await page.locator("#ptm_transacciones").inputValue(), "0");
    await page.locator("#btnCerrar").click();
    await page.locator("#tc-confirm-ok").click();
    await page.waitForFunction(() => document.getElementById("tc-toasts").textContent.includes("Prueba aislada"));
    assert.equal(posts[0].facturas_pagadas, "2000");
    assert.equal(posts[0].efectivo_entregado, "10000");
    assert.equal(posts[0].ptm_transacciones, "0");
    assert.equal(JSON.parse(posts[0].medios_json).some(row => row.metodo === "facturas_pagadas"), false);
    assert.equal(Number(await page.locator("#efectivo_entregado").inputValue()), 10000);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("tc_contados_8080")).progress.denominations.b5000), 2);
    await page.reload();
    await page.locator("#closeMediaStep").waitFor({state: "visible"});
    assert.equal(Number(await page.locator("#facturas_pagadas").inputValue()), 2000);
    assert.equal(await page.locator("#facturas_pagadas").evaluate(el => el.readOnly), true);
    assert.equal(Number(await page.locator("#efectivo_entregado").inputValue()), 10000);
    await page.goto(`${url}turno_caja/cierre/8080/pagos/`);
    await page.locator("#closeMediaStep").waitFor({state: "visible"});
    assert.match(page.url(), /\/medios\/$/);
    assert.equal(await page.locator("#closePaymentsStep, #closeCashStep").count(), 0);
    const otherTab = await context.newPage();
    await otherTab.goto(url);
    await otherTab.locator("#closeMediaStep").waitFor({state: "visible"});
    assert.equal(await otherTab.locator("#facturas_pagadas").inputValue(), "2000");
    await otherTab.close();
    const freshContext = await browser.newContext({viewport: {width: 390, height: 844}});
    await freshContext.route("https://**/*", route => route.abort());
    const fresh = await freshContext.newPage();
    await fresh.goto(url);
    await fresh.locator("#facturas_pagadas").fill("");
    await fresh.locator("#btnPaymentsNext").click();
    await fresh.locator("#tc-confirm-ok").click();
    await fresh.locator("#closeCashStep").waitFor({state: "visible"});
    assert.equal(await fresh.locator("#facturas_pagadas").inputValue(), "0");
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await fresh.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.ok(await fresh.locator("#closeCashWarningTitle").evaluate(el => parseFloat(getComputedStyle(el).fontSize)) >= 20);
    if (process.env.POS_TEST_ARTIFACT_DIR) {
      await fresh.locator("#stepClose").screenshot({path: path.join(process.env.POS_TEST_ARTIFACT_DIR, "cierre-conteo-mobile.png")});
    }
    await fresh.evaluate(() => {
      Storage.prototype.setItem = () => { throw new Error("Storage disabled for test"); };
    });
    await fresh.locator("#btnCashNext").click();
    await fresh.locator("#tc-confirm-ok").click();
    await fresh.waitForFunction(() => document.getElementById("tc-toasts").textContent.includes("No se pudo guardar el avance"));
    assert.equal(await fresh.locator("#closeCashStep").isVisible(), true);
    assert.equal(await fresh.locator("#closeMediaStep").isVisible(), false);
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
