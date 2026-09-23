/* Lecturas independientes, delimitadas por Enter/Tab, no por velocidad. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NovaBarcodeScanInput = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function attach(input, {onScan, onChange = () => {}, onInvalid = () => {}}) {
    let replaceNext = true;
    const listeners = [];
    const valid = value => value.length <= 100 && !/[\s\x00-\x1f\x7f]/.test(value);
    function listen(type, handler) {
      input.addEventListener(type, handler, true);
      listeners.push([type, handler]);
    }
    function clear() {
      input.value = "";
      replaceNext = true;
      onChange();
    }
    function invalid() {
      clear();
      onInvalid("Escanea un solo código de barras completo, sin espacios ni saltos de línea.");
    }
    function accept(raw) {
      const code = String(raw || "").trim();
      if (!code || !valid(code)) { invalid(); return ""; }
      input.value = code;
      // Se sella ANTES de consultar: la siguiente tecla nunca concatena, aun
      // si la red tarda o ambas lecturas llegan dentro del mismo milisegundo.
      replaceNext = true;
      return code;
    }
    function submit(raw = input.value) {
      if (!String(raw || "").trim()) return;
      const code = accept(raw);
      if (code) onScan(code);
    }
    function beginEdit() {
      if (replaceNext) {
        input.value = "";
        replaceNext = false;
        onChange();
      }
    }
    listen("keydown", event => {
      if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
      if (event.key === "Enter" || (event.key === "Tab" && !event.shiftKey && input.value)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) submit();
        return;
      }
      if (event.key?.length === 1 || event.key === "Backspace" || event.key === "Delete") beginEdit();
    });
    listen("beforeinput", event => {
      if (event.inputType === "insertLineBreak" || event.inputType === "insertParagraph") {
        event.preventDefault(); submit(); return;
      }
      if (event.inputType?.startsWith("insert") || event.inputType?.startsWith("delete")) beginEdit();
    });
    listen("input", event => {
      // Teclados móviles/autofill pueden no emitir keydown/beforeinput.
      if (replaceNext && event.data != null) input.value = event.data;
      replaceNext = false;
      if (!valid(input.value)) { invalid(); return; }
      onChange();
    });
    for (const type of ["paste", "drop"]) listen(type, event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const raw = (event.clipboardData || event.dataTransfer)?.getData("text/plain") || "";
      // Pegar/soltar siempre reemplaza; nunca mezcla con el texto anterior.
      submit(raw);
    });
    function start(raw) {
      input.value = String(raw || "");
      replaceNext = false;
      if (!valid(input.value)) { invalid(); return; }
      onChange();
    }
    return {accept, clear, submit, start, destroy() {
      for (const [type, handler] of listeners) input.removeEventListener(type, handler, true);
    }};
  }
  return {attach};
});
