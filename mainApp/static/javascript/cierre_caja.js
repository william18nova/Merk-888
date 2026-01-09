(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function parseMoney(str) {
    if (str == null) return 0;
    let s = String(str).trim().replace(/\s/g, "");
    if (!s) return 0;

    // Si tiene coma y punto => miles "." y decimal ","
    if (s.includes(",") && s.includes(".")) {
      s = s.replace(/\./g, "").replace(",", ".");
    } else if (s.includes(",")) {
      // solo coma => decimal
      s = s.replace(",", ".");
    }
    // solo punto => decimal con punto (no se toca)

    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }

  function fmt(n) {
    const num = Number.isFinite(n) ? n : 0;
    return num.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function setDiffClass(el, diff) {
    el.classList.remove("pos", "neg");
    if (diff > 0.000001) el.classList.add("pos");
    else if (diff < -0.000001) el.classList.add("neg");
  }

  const form = $("#cierreForm");
  const tabla = $("#tablaMedios");
  if (!form || !tabla) return;

  const rows = $$(".medio-row", tabla);
  const efectivoRow = $(".efectivo-row", tabla);
  const efectivoInp = $("#efectivo_real");

  const totalEsperadoEl = $("#totalEsperado");
  const totalContadoEl  = $("#totalContado");
  const totalDiffEl     = $("#totalDiff");
  const diffEfectivoEl  = $("#diffEfectivo");

  function recalcular() {
    let totalEsperado = 0;
    let totalContado = 0;

    rows.forEach((tr) => {
      const esperado = parseMoney(tr.dataset.esperado);
      const inp = $(".contado", tr);
      const contado = parseMoney(inp.value);

      const diff = contado - esperado;

      totalEsperado += esperado;
      totalContado += contado;

      const diffEl = $(".diff", tr);
      diffEl.textContent = fmt(diff);
      setDiffClass(diffEl, diff);
    });

    const totalDiff = totalContado - totalEsperado;

    if (totalEsperadoEl) totalEsperadoEl.textContent = fmt(totalEsperado);
    if (totalContadoEl) totalContadoEl.textContent = fmt(totalContado);
    if (totalDiffEl) {
      totalDiffEl.textContent = fmt(totalDiff);
      setDiffClass(totalDiffEl, totalDiff);
    }

    // Efectivo físico
    if (efectivoRow && efectivoInp && diffEfectivoEl) {
      const esperadoE = parseMoney(efectivoRow.dataset.esperado);
      const realE = parseMoney(efectivoInp.value);
      const diffE = realE - esperadoE;

      diffEfectivoEl.textContent = fmt(diffE);
      setDiffClass(diffEfectivoEl, diffE);
    }
  }

  rows.forEach((tr) => {
    const inp = $(".contado", tr);
    if (!inp) return;
    inp.addEventListener("input", recalcular);
  });

  if (efectivoInp) {
    efectivoInp.addEventListener("input", recalcular);
  }

  form.addEventListener("submit", () => {
    // asegura que no queden vacíos
    $$("input.inp-money", form).forEach((inp) => {
      if (!inp.value || !inp.value.trim()) inp.value = "0";
    });
  });

  recalcular();
})();
