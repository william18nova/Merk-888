// node --test mainApp/test_expense_amount_format.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const script = fs.readFileSync(path.join(__dirname, "static/javascript/registrar_egreso.js"), "utf8");

function setup(initial = "") {
  const events = {};
  const input = {
    value: initial, selectionStart: initial.length, selectionEnd: initial.length, error: "",
    addEventListener(name, callback) { events[name] = callback; },
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    setCustomValidity(error) { this.error = error; },
    setRangeText(value, start, end) {
      this.value = this.value.slice(0, start) + value + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + value.length;
    },
  };
  vm.runInNewContext(script, {document: {
    querySelectorAll: () => [], querySelector: () => null,
    getElementById: id => id === "id_monto" ? input : null,
  }});
  return {input, events, type(value, cursor = value.length) {
    input.value = value;
    input.selectionStart = input.selectionEnd = cursor;
    events.input();
  }};
}

test("se agrupan miles y millones en cada cambio sin redondear", () => {
  const app = setup();
  for (const [raw, expected] of [["1000", "1.000"], ["1000000", "1.000.000"],
                                 ["1234567,89", "1.234.567,89"], ["999999999999,99", "999.999.999.999,99"]]) {
    app.type(raw);
    assert.equal(app.input.value, expected);
    assert.equal(app.input.selectionStart, expected.length);
    assert.equal(app.input.error, "");
  }
});

test("la escritura sucesiva conserva el cursor y permite editar en medio", () => {
  const app = setup();
  for (const digit of "1234567") app.type(app.input.value + digit);
  assert.equal(app.input.value, "1.234.567");
  app.type("1.9234.567", 3);
  assert.equal(app.input.value, "19.234.567");
  assert.equal(app.input.selectionStart, 2);
});

test("el importe inicial de Django conserva los centavos", () => {
  assert.equal(setup("1234.50").input.value, "1.234,50");
  assert.equal(setup("1.234,50").input.value, "1.234,50");
  assert.equal(setup("1.2345").input.value, "1.2345");
});

test("pegar cantidades con punto decimal no las multiplica por cien", () => {
  const app = setup();
  let prevented = false;
  app.events.paste({clipboardData: {getData: () => "1234.50"}, preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(app.input.value, "1.234,50");
});

test("los signos y letras no se convierten silenciosamente en otro monto", () => {
  const app = setup();
  for (const raw of ["-1000", "abc1000", "100,123"]) {
    app.type(raw);
    assert.equal(app.input.value, raw);
    assert.notEqual(app.input.error, "");
  }
});

test("se puede borrar el importe y empezar por centavos", () => {
  const app = setup();
  app.type("");
  assert.equal(app.input.value, "");
  app.type(",");
  assert.equal(app.input.value, "0,");
  assert.equal(app.input.selectionStart, 2);
  app.type("0,5");
  assert.equal(app.input.value, "0,5");
  assert.equal(app.input.error, "");
});
