// node --test mainApp/test_visor_new_sale.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function extract(file, start, end) {
  const source = fs.readFileSync(path.join(__dirname, "static/javascript", file), "utf8");
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
const linkCode = extract("visor_barcode.js", "function updateNewSaleLink()", '$newSale.on("click"');
const importCode = extract("generar_venta.js", "function importProductFromVisor()", "/* ================== Init ================== */");

function scanner(product, quantity) {
  const attrs = {"data-sale-url": "/generar_venta/", "data-turno-id": "17"};
  const link = {length: 1, attr(key, value) {
    if (value === undefined) return attrs[key];
    attrs[key] = value; return this;
  }, removeAttr(key) {delete attrs[key]; return this;}};
  const context = vm.createContext({currentProduct: product, $quantity: {val: () => quantity},
    $newSale: link, URL, window: {location: {origin: "https://pos.example"}}});
  vm.runInContext(linkCode + "\nupdateNewSaleLink();", context);
  return {attrs, context};
}

test("el enlace solo transporta ID, gramos y turno, nunca precio ni otro carrito", () => {
  const {attrs} = scanner({id: 2978, precio: "3.8", nombre: "Tomate"}, "500");
  const url = new URL(attrs.href);
  assert.equal(url.pathname, "/generar_venta/");
  assert.deepEqual(Object.fromEntries(url.searchParams), {visor_producto: "2978", visor_cantidad: "500", visor_turno: "17"});
  assert.equal(attrs["aria-disabled"], "false");
});

test("limpiar la selección o introducir cantidades inválidas quita el enlace", () => {
  for (const quantity of ["", "0", "-1", "0.5", "1000001", "abc", "1e3"]) {
    const {attrs} = scanner({id: 12}, quantity);
    assert.equal(attrs.href, undefined);
    assert.equal(attrs["aria-disabled"], "true");
  }
  const {attrs, context} = scanner({id: 12}, "1");
  vm.runInContext("currentProduct = null; updateNewSaleLink();", context);
  assert.equal(attrs.href, undefined);
});

function destination({existing = [], quantity = 500, failAdd = false, failBackup = false} = {}) {
  const item = {id: "2978", cantidad: quantity, precio_unitario: "4.20", nombre: "Tomate"};
  let source = {textContent: JSON.stringify(item), remove() {source = null;}};
  const calls = [], messages = [], urls = [];
  const products = [...existing];
  const notice = {text(text) {messages.push(text); return this;},
    removeClass() {return this;}, addClass() {return this;}, attr() {return this;}};
  const context = vm.createContext({
    URL, productos: products, hasSucursal: () => true, console: {warn() {}},
    document: {getElementById: () => source},
    window: {location: {href: "https://pos.example/generar_venta/?visor_producto=2978&visor_cantidad=500&visor_turno=17&keep=1"},
      history: {state: null, replaceState: (_, __, url) => urls.push(url)}},
    $: () => notice,
    updateCache: (id, data) => calls.push(["cache", id, data.precio_unitario]),
    addToCart: (id, qty) => {
      if (failAdd) throw new Error("No se pudo insertar");
      calls.push(["add", id, qty]); products.push(id);
    },
    persistSaleDraftNow: () => {if (failBackup) throw new Error("Storage bloqueado"); calls.push(["save"]);},
  });
  vm.runInContext(importCode + "\nimportProductFromVisor();", context);
  return {calls, messages, urls, context, get source() {return source;}};
}

test("el destino agrega los gramos por el flujo normal y guarda su propio borrador", () => {
  const result = destination();
  assert.deepEqual(result.calls, [["cache", "2978", "4.20"], ["add", "2978", 500], ["save"]]);
  assert.equal(result.urls[0], "https://pos.example/generar_venta/?keep=1");
  assert.match(result.messages[0], /independiente/);
  vm.runInContext("importProductFromVisor();", result.context);
  assert.equal(result.calls.length, 3, "no agrega el producto dos veces");
});

test("la importación nunca vacía ni se mezcla con un carrito existente", () => {
  assert.deepEqual(destination({existing: ["99"]}).calls, []);
  assert.deepEqual(destination({quantity: -5}).calls, []);
  assert.doesNotMatch(importCode, /clearCart|removeSaleDraft|restorePendingSaleDraft|localStorage\./);
});

test("si falla la inserción conserva el producto y la URL para reintentar", () => {
  const result = destination({failAdd: true});
  assert.ok(result.source);
  assert.equal(result.urls.length, 0);
  assert.match(result.messages[0], /Recarga esta página/);
  result.context.addToCart = (id, qty) => {
    result.calls.push(["add", id, qty]); result.context.productos.push(id);
  };
  vm.runInContext("importProductFromVisor();", result.context);
  assert.equal(result.source, null);
  assert.equal(result.calls.filter(call => call[0] === "add").length, 1);
});

test("un fallo en el respaldo no impide cargar el producto y mostrar confirmación", () => {
  const result = destination({failBackup: true});
  assert.deepEqual(result.calls, [["cache", "2978", "4.20"], ["add", "2978", 500]]);
  assert.equal(result.source, null);
  assert.match(result.messages[0], /independiente/);
});

test("la precarga no espera un bloqueo del navegador ni falla si rechaza el respaldo", async () => {
  const startup = extract("generar_venta.js", "void resumeSaleDraftPage().catch(", "cleanupExpiredSaleDrafts();");
  for (const resume of [() => new Promise(() => {}), () => Promise.reject(new Error("Sin almacenamiento"))]) {
    let imported = 0;
    const context = vm.createContext({resumeSaleDraftPage: resume, importProductFromVisor: () => imported++, console: {warn() {}}});
    vm.runInContext(startup, context);
    assert.equal(imported, 1);
    await new Promise(resolve => setImmediate(resolve));
  }
});
