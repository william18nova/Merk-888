// Impresora interceptada: nunca contacta el POS real, ni abre el cajón.
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}

test("tirilla automática, reimpresión explícita y errores visibles sin perder el cierre", {skip: !chromium}, async () => {
  const data = {
    turno_id: 1576, cajero: "CAJERO DE PRUEBA", puntopago: "CAJA DE PRUEBA",
    inicio: "2026-09-10 07:00", fin: "2026-09-10 14:00",
    efectivo_real: 300000, base_apertura: 50000, facturas_pagadas: 20000,
    ventas_total: 270000, medios: [{label: "Efectivo", vendido: 270000}],
  };
  const counts = {b100000: 2, b50000: 2};
  const source = fs.readFileSync(path.join(__dirname, "templates/turno_caja_retiro.html"), "utf8");
  const block = name => source.match(new RegExp(`{% block ${name} %}([\\s\\S]*?){% endblock %}`))[1];
  const render = text => text
    .replace('{{ retiro_data|json_script:"retiro-data" }}', `<script id="retiro-data" type="application/json">${JSON.stringify(data)}</script>`)
    .replace(/{% static '([^']+)' %}/g, "/static/$1")
    .replace(/{% url 'turno_caja' %}/g, "/turno_caja/")
    .replace(/{{[\s\S]*?}}/g, "PRUEBA")
    .replace(/{%[\s\S]*?%}/g, "");
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const assets = {
      "/static/javascript/turno_caja_retiro.js": "application/javascript",
      "/static/css/turno_caja_retiro.css": "text/css",
    };
    if (assets[url.pathname]) {
      res.setHeader("Content-Type", assets[url.pathname]);
      res.end(fs.readFileSync(path.join(__dirname, url.pathname.slice(1))));
      return;
    }
    res.setHeader("Content-Type", "text/html");
    res.end(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${render(block("extra_head"))}</head><body>${render(block("content"))}<script>window.POS_AGENT_URL='http://printer.test';window.POS_AGENT_TOKEN=${url.searchParams.has("no-token") ? "''" : "'test-token'"};</script><script src="/static/javascript/turno_caja_retiro.js"></script></body></html>`);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, ...(process.env.SALE_DRAFT_BROWSER_CHANNEL ? {channel: process.env.SALE_DRAFT_BROWSER_CHANNEL} : {})});
    const url = `http://127.0.0.1:${server.address().port}/turno_caja/retiro/1576/`;
    async function setup({detail = true, mode = "success"} = {}) {
      const context = await browser.newContext();
      const state = {mode, requests: [], errors: []};
      await context.route("https://**/*", route => route.abort());
      await context.route("http://printer.test/**", async route => {
        state.requests.push({path: new URL(route.request().url()).pathname, body: route.request().postDataJSON()});
        if (state.mode === "network") return route.abort();
        await route.fulfill({status: state.mode === "rejected" ? 503 : 200, contentType: "application/json", body: JSON.stringify({success: state.mode !== "rejected"})});
      });
      if (detail) await context.addInitScript(({counts}) => {
        if (!sessionStorage.getItem("tc_retiro_denoms_1576")) sessionStorage.setItem("tc_retiro_denoms_1576", JSON.stringify({counts, ts: Date.now()}));
      }, {counts});
      const page = await context.newPage();
      page.on("pageerror", error => state.errors.push(error.message));
      return {context, page, state};
    }
    const first = await setup();
    await first.page.goto(url);
    await first.page.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(first.state.requests.length, 1);
    assert.equal(first.state.requests[0].path, "/print");
    const receipt = first.state.requests[0].body.text;
    assert.match(receipt, /Turno:\s+#1576/);
    assert.match(receipt, /Efectivo fisico:\s+\$300\.000/);
    assert.match(receipt, /Base a dejar:\s+\$50\.000/);
    assert.match(receipt, /Retiro:\s+\$250\.000/);
    assert.match(receipt, /Facturas pagadas:\s+\$20\.000/);
    assert.equal(first.page.url(), url);
    assert.ok(await first.page.evaluate(() => sessionStorage.getItem("tc_retiro_denoms_1576")));
    await first.page.reload();
    await first.page.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(first.state.requests.length, 1, "F5 no debe imprimir otra copia");
    const another = await first.context.newPage();
    await another.goto(url);
    await another.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(first.state.requests.length, 1, "Otra pestaña no debe duplicar automáticamente");
    await another.close();
    await first.page.locator("#base_final").fill("100000");
    assert.equal(first.state.requests.length, 1, "Editar la base no imprime hasta pedirlo");
    assert.equal(await first.page.locator("#btnPrintLabel").innerText(), "Imprimir cambios");
    await first.page.locator("#btnPrint").click();
    await first.page.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(first.state.requests.length, 2);
    assert.match(first.state.requests[1].body.text, /Base a dejar:\s+\$100\.000/);
    assert.deepEqual(first.state.errors, []);

    const failed = await setup({mode: "rejected"});
    await failed.page.goto(url);
    await failed.page.locator('#printStatus[data-state="error"]').waitFor();
    assert.equal(failed.page.url(), url);
    assert.equal(await failed.page.locator("#btnPrint").isEnabled(), true);
    assert.ok(await failed.page.evaluate(() => sessionStorage.getItem("tc_retiro_denoms_1576")));
    failed.state.mode = "success";
    await failed.page.locator("#btnPrint").click();
    await failed.page.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(failed.state.requests.length, 2);

    const uncertain = await setup({mode: "network"});
    await uncertain.page.goto(url);
    await uncertain.page.locator('#printStatus[data-state="uncertain"]').waitFor();
    await uncertain.page.reload();
    await uncertain.page.locator('#printStatus[data-state="uncertain"]').waitFor();
    assert.equal(uncertain.state.requests.length, 1, "No repetir un envío de resultado incierto");
    uncertain.state.mode = "success";
    await uncertain.page.locator("#btnPrint").click();
    await uncertain.page.locator('#printStatus[data-state="success"]').waitFor();
    assert.equal(uncertain.state.requests.length, 2);

    const missingToken = await setup();
    await missingToken.page.goto(`${url}?no-token=1`);
    await missingToken.page.locator('#printStatus[data-state="error"]').waitFor();
    assert.equal(missingToken.state.requests.length, 0);

    const oldTurn = await setup({detail: false});
    await oldTurn.page.goto(url);
    await oldTurn.page.locator('#printStatus[data-state="success"]').waitFor();
    const summary = oldTurn.state.requests[0].body.text;
    assert.match(summary, /Efectivo fisico:\s+\$300\.000/);
    assert.match(summary, /Detalle de denominaciones no disponible/);
    assert.doesNotMatch(summary, /Retiro:\s+\$0/);
    assert.equal(await oldTurn.page.locator("#totalContado").innerText(), "$ 300.000");
    assert.equal(await oldTurn.page.locator("#denomInputs").isVisible(), false);
    for (const fixture of [first, failed, uncertain, missingToken, oldTurn]) {
      assert.deepEqual(fixture.state.errors, []);
      assert.ok(fixture.state.requests.every(request => request.path === "/print"), "No abrir el cajón automáticamente");
      await fixture.context.close();
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
