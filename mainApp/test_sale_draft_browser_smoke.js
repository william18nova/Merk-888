// Prueba opcional con Chromium real. No inicia Django ni consulta datos del POS.
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}

test("el navegador conserva la presencia en segundo plano y la libera al cerrar", {skip: !chromium}, async () => {
  const source = fs.readFileSync(path.join(__dirname, "static/javascript/sale_draft_lifecycle.js"));
  const server = http.createServer((req, res) => {
    if (req.url === "/lifecycle.js") {
      res.setHeader("Content-Type", "application/javascript"); res.end(source); return;
    }
    res.setHeader("Content-Type", "text/html");
    res.end('<!doctype html><title>Prueba aislada de carritos</title><script src="/lifecycle.js"></script>');
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      ...(process.env.SALE_DRAFT_BROWSER_CHANNEL ? {channel: process.env.SALE_DRAFT_BROWSER_CHANNEL} : {}),
    });
    const context = await browser.newContext();
    const url = `http://127.0.0.1:${server.address().port}/`;
    async function page(id) {
      const tab = await context.newPage();
      await tab.goto(url);
      await tab.evaluate(async id => {
        window.life = createSaleDraftLifecycle({locks: navigator.locks, tabID: id});
        window.id = id;
        await life.start();
      }, id);
      return tab;
    }
    const a = await page("A"); const b = await page("B"); const c = await page("C");
    await a.evaluate(() => localStorage.setItem("pending", JSON.stringify({owner_tab_id: "A", storage_key: "pending", items: [1, 2]})));
    await b.bringToFront();
    assert.equal(await b.evaluate(async () => (await life.openOwners()).has("A")), true);
    assert.equal(await b.evaluate(() => life.withClosedDraft(JSON.parse(localStorage.getItem("pending")), () => true)), false);
    // Cierre real sin llamar stop: Chromium debe liberar el bloqueo del documento.
    await a.close();
    await b.waitForFunction(async () => !(await life.openOwners()).has("A"));
    const recover = tab => tab.evaluate(() => life.withClosedDraft(
      {owner_tab_id: "A", storage_key: "pending"},
      () => {
        const source = localStorage.getItem("pending");
        if (!source) return false;
        localStorage.setItem("copy", JSON.stringify({...JSON.parse(source), owner_tab_id: id}));
        localStorage.removeItem("pending");
        return true;
      },
    ));
    const results = await Promise.all([recover(b), recover(c)]);
    assert.equal(results.filter(Boolean).length, 1);
    assert.equal(await b.evaluate(() => localStorage.getItem("pending")), null);
    const winner = await b.evaluate(() => JSON.parse(localStorage.getItem("copy")).owner_tab_id);
    const survivor = winner === "B" ? c : b;
    await (winner === "B" ? b : c).close();
    await survivor.waitForFunction(async winner => !(await life.openOwners()).has(winner), winner);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
