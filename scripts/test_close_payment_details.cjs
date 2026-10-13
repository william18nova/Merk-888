const test = require('node:test');
const assert = require('node:assert/strict');
const {cents, validate} = require('../mainApp/static/javascript/close_payment_details.js');

test('decimal amounts are compared in integer cents', () => {
  const result = validate('0.30', [{nombre:' factura ', monto:'0.10'}, {nombre:'juan', monto:'0.20'}]);
  assert.equal(result.total, 0.3);
  assert.deepEqual(result.items, [{nombre:'FACTURA', monto:'0.10'}, {nombre:'JUAN', monto:'0.20'}]);
});
test('zero requires no payments', () => assert.deepEqual(validate('0', []), {total:0, items:[]}));
test('missing name, wrong totals and invalid amounts cannot advance', () => {
  for (const rows of [[], [{nombre:'',monto:'100'}], [{nombre:'X',monto:'99.99'}], [{nombre:'X',monto:'100.01'}], [{nombre:'X',monto:'100.001'}]]) {
    assert.throws(() => validate('100', rows));
  }
  for (const amount of ['-1','Infinity','NaN','1e2','', '10000000000']) assert.throws(() => cents(amount));
});
test('round-trip through browser persistence retains details and exact total', () => {
  const payment_details = validate('1000', [{nombre:'María',monto:'350.25'}, {nombre:'Factura 12',monto:'649.75'}]).items;
  const saved = JSON.parse(JSON.stringify({step:'media', invoices:1000, payment_details}));
  assert.deepEqual(validate(saved.invoices, saved.payment_details).items, payment_details);
});
