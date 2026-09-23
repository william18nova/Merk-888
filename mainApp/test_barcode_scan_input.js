const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {attach} = require('./static/javascript/barcode_scan_input');

function fixture(options = {}) {
  const handlers = new Map(), scans = [], errors = [];
  const input = {value: '', addEventListener(type, fn) {handlers.set(type, fn);},
    removeEventListener(type) {handlers.delete(type);}};
  const reader = attach(input, {onScan: code => {scans.push(code); options.onScan?.(code);},
    onChange: options.onChange, onInvalid: error => errors.push(error)});
  function emit(type, values = {}) {
    const event = {preventDefault() {this.defaultPrevented = true;}, stopImmediatePropagation() {}, ...values};
    handlers.get(type)?.(event); return event;
  }
  function type(text) {
    for (const key of text) {
      const event = emit('keydown', {key});
      if (event.defaultPrevented) continue;
      emit('beforeinput', {inputType: 'insertText', data: key});
      input.value += key;
      emit('input', {data: key});
    }
  }
  function paste(text) {emit('paste', {clipboardData: {getData: () => text}});}
  return {input, reader, scans, errors, emit, type, paste};
}

test('100 lecturas consecutivas sin ninguna espera nunca concatenan códigos', () => {
  const f = fixture();
  for (let i = 0; i < 100; i++) {
    const code = String(7700000000000 + i);
    f.type(code); f.emit('keydown', {key: 'Enter'});
    assert.equal(f.input.value, code);
    assert.equal(f.scans.at(-1), code);
  }
  assert.equal(f.scans.length, 100);
});

test('la siguiente lectura reemplaza aunque la consulta anterior siga pendiente', () => {
  const f = fixture({onScan: () => new Promise(() => {})});
  f.type('77012345'); f.emit('keydown', {key: 'Enter'});
  f.type('880'); assert.equal(f.input.value, '880');
  f.type('12345'); f.emit('keydown', {key: 'Enter'});
  assert.deepEqual(f.scans, ['77012345', '88012345']);
});

test('Tab cierra la lectura sin mover el foco y mantener Enter pulsado no duplica', () => {
  const f = fixture();
  f.type('001234'); assert.equal(f.emit('keydown', {key: 'Tab'}).defaultPrevented, true);
  f.type('009876'); f.emit('keydown', {key: 'Enter'});
  assert.deepEqual(f.scans, ['001234', '009876']);
  f.emit('keydown', {key: 'Enter', repeat: true});
  assert.equal(f.scans.length, 2);
});

test('las pausas de escritura no separan un código ni requieren umbrales de velocidad', async () => {
  const f = fixture();
  f.type('770'); await new Promise(resolve => setTimeout(resolve, 120));
  f.type('123'); f.emit('keydown', {key: 'Enter'});
  assert.deepEqual(f.scans, ['770123']);
});

test('pegar reemplaza incluso un código incompleto y rechaza múltiples códigos', () => {
  const f = fixture();
  f.type('770'); f.paste(' 00123456\r\n');
  assert.equal(f.input.value, '00123456');
  for (const text of ['00123456\n88012345', '00123456 88012345', '0'.repeat(101)]) {
    f.paste(text); assert.equal(f.input.value, '');
  }
  assert.deepEqual(f.scans, ['00123456']); assert.equal(f.errors.length, 3);
});

test('cámara y lecturas iniciadas desde el fondo no concatenan la siguiente entrada', () => {
  const f = fixture();
  f.reader.accept('77012345'); f.type('88012345'); f.emit('keydown', {key: 'Enter'});
  f.reader.start('9'); f.type('9900'); f.emit('keydown', {key: 'Enter'});
  assert.deepEqual(f.scans, ['88012345', '99900']);
});

test('beforeinput móvil reemplaza el código finalizado sin depender del cursor', () => {
  const f = fixture(); f.reader.accept('77012345');
  f.emit('beforeinput', {inputType: 'insertText', data: '8'});
  f.input.value += '8'; f.emit('input', {data: '8'});
  assert.equal(f.input.value, '8');
});

test('borrar, soltar texto y escribir después de un error mantienen una sola lectura', () => {
  const f = fixture(); f.paste('77012345'); f.reader.clear();
  f.emit('drop', {dataTransfer: {getData: () => '88012345'}});
  f.type('9 9'); assert.equal(f.scans.length, 2);
  f.reader.clear(); f.type('0011'); f.emit('keydown', {key: 'Enter'});
  assert.deepEqual(f.scans, ['77012345', '88012345', '0011']);
});

function lookupFixture() {
  const text = fs.readFileSync(path.join(__dirname, 'static/javascript/visor_barcode.js'), 'utf8');
  const start = text.indexOf('async function handleScanNow(barcode)');
  const code = text.slice(start, text.indexOf('function isPrintableChar', start));
  const waiting = [], painted = [], errors = [];
  const input = {value: '', select() {}};
  const context = vm.createContext({publicBarcodeOnly: true, lookupRevision: 0, barcodeInput: null,
    sanitizeBarcode: x => String(x).trim(), hideErr() {}, forceFocus() {},
    $inp: {0: input, val(value) {if (value === undefined) return input.value; input.value = value;}},
    paintEmpty() {context.lookupRevision++;},
    paintProduct(p) {context.lookupRevision++; painted.push(p.codigo_de_barras);},
    showErr: text => errors.push(text), openAutocompletePickFirst() {throw Error('No debe buscar sugerencias');},
    lookupExact: bc => new Promise(resolve => waiting.push({bc, resolve})),
  });
  vm.runInContext(code, context);
  return {context, waiting, painted, errors, input};
}

test('una respuesta antigua no cambia el producto del último código leído', async () => {
  const f = lookupFixture();
  const first = f.context.handleScanNow('111');
  const second = f.context.handleScanNow('222');
  f.waiting[1].resolve({codigo_de_barras: '222'}); await second;
  f.waiting[0].resolve({codigo_de_barras: '111'}); await first;
  assert.deepEqual(f.painted, ['222']); assert.equal(f.input.value, '222');
});

test('escribir el siguiente código o limpiar invalida la consulta pendiente', async () => {
  const f = lookupFixture(); const first = f.context.handleScanNow('111');
  f.context.paintEmpty(); f.input.value = '2';
  f.waiting[0].resolve({codigo_de_barras: '111'}); await first;
  assert.deepEqual(f.painted, []); assert.equal(f.input.value, '2');
});

test('código desconocido no busca por nombre, ID ni coincidencia parcial', async () => {
  const f = lookupFixture(); const lookup = f.context.handleScanNow('123');
  f.waiting[0].resolve(null); await lookup;
  assert.equal(f.errors.length, 1); assert.deepEqual(f.painted, []);
});
