const test = require("node:test");
const assert = require("node:assert/strict");
const ac = require("./static/javascript/product_autocomplete.js");

const products = [
  {id: 1, name: "FR LIMÓN X KG", barcode: "770000000001", price: "5", stock: -10},
  {id: 2, name: "GASEOSA COCA COLA X 6 500 ML", barcode: "770000000002", price: "4000", stock: 0},
  {id: 3, name: "COCA COLA 1,5 L", barcode: "770000000003", price: "6500", stock: 12},
  {id: 4, name: "AGUA 500ML", barcode: "770000000004", price: "2000", stock: 50},
  {id: 5, name: "COLA COCA 500ML", barcode: "770000000005", price: "3900", stock: 100},
];
const index = ac.createIndex(products);
const engine = ac.createSearch();

test("misma normalización del POS: tildes, mayúsculas, espacios, unidades y empaques", () => {
  assert.equal(ac.normalizeUnits("  LIMÓN   X 6  500 ML  "), "limon x6 500ml");
  assert.equal(ac.normalizeUnits("Gaseosa 1,5 L"), "gaseosa 1.5l");
  assert.deepEqual(engine.buildLocalSmart("limon", index).map(p => p.id), [1]);
  assert.equal(engine.buildLocalSmart("coca 500 ml", index)[0].id, 5);
  assert.equal(engine.buildLocalSmart("cola coca", index)[0].id, 5);
});

test("ID exacto y código de barras conservan prioridad y el producto sin stock aparece", () => {
  assert.equal(engine.buildLocalSmart("2", index)[0].id, 2);
  assert.equal(engine.buildLocalSmart("770000000002", index)[0].id, 2);
  assert.equal(engine.buildLocalSmart("FR LIMÓN X KG", index)[0].stock, -10);
  assert.deepEqual(engine.buildLocalSmart("inexistente", index), []);
  assert.deepEqual(engine.buildLocalSmart("", index), []);
});

test("ranking limitado a 40 y preferencias actualizadas sin reconstruir el índice", () => {
  const rows = Array.from({length: 100}, (_, i) => ({id: i+1, name: `PRODUCTO ${i+1}`, price: 100, stock: 1}));
  const idx = ac.createIndex(rows);
  let boost = {};
  const search = ac.createSearch({getBoost: () => boost});
  assert.equal(search.buildLocalSmart("producto", idx).length, 40);
  boost = {100: 1000};
  assert.equal(search.buildLocalSmart("producto", idx)[0].id, 100);
});

test("el catálogo descarga una sola vez para búsquedas y Enter simultáneos", async () => {
  let requests = 0, release;
  const catalog = ac.createCatalog({url: "/catalogo", storageKey: "prueba", fetcher: async () => {
    requests++; await new Promise(resolve => {release = resolve;});
    return {ok: true, json: async () => ({results: products})};
  }});
  const a = catalog.ensure(), b = catalog.ensure();
  assert.equal(a, b);
  release(); await a;
  assert.equal(catalog.search("limon")[0].id, 1);
  await catalog.ensure();
  assert.equal(requests, 1);
  assert.equal(catalog.get("2").price, "4000");
});

test("el catálogo local válido da resultados inmediatos sin peticiones por tecla", async () => {
  const storage = {getItem: () => JSON.stringify({at: 1000, items: products})};
  const catalog = ac.createCatalog({url: "/catalogo", storageKey: "prueba", storage, now: () => 2000,
    fetcher: () => {throw new Error("No debe consultar");}});
  assert.equal(catalog.ready, true);
  assert.equal(catalog.search("limon")[0].id, 1);
  await catalog.ensure();
});

test("fallos de almacenamiento no rompen el catálogo y los de red permiten reintentar", async () => {
  let calls = 0;
  const catalog = ac.createCatalog({url: "/catalogo", storageKey: "prueba",
    storage: {getItem() {throw new Error("Bloqueado");}, setItem() {throw new Error("Cuota");}},
    fetcher: async () => ({ok: ++calls > 1, json: async () => ({results: products})})});
  await assert.rejects(catalog.ensure());
  assert.equal(catalog.ready, false);
  await catalog.ensure();
  assert.equal(catalog.search("coca").length > 0, true);
});

test("el catálogo vencido se renueva con los precios actuales", async () => {
  const catalog = ac.createCatalog({url: "/catalogo", storageKey: "prueba", now: () => 999999,
    storage: {getItem: () => JSON.stringify({at: 1, items: products}), setItem() {}},
    fetcher: async () => ({ok: true, json: async () => ({results: [{...products[0], price: "7.20"}]})})});
  assert.equal(catalog.ready, false);
  await catalog.ensure();
  assert.equal(catalog.search("limon")[0].price, "7.20");
});
