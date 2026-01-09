(function () {
  "use strict";

  function parseMoney(v) {
    const s = String(v ?? "").trim().replace(",", ".");
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }
  function to2(n) { return (Math.round(n * 100) / 100).toFixed(2); }

  const form = document.getElementById("venta-form");
  const selectMedio = document.getElementById("id_mediopago");

  const bloquePagos = document.getElementById("bloque-mixto-pagos");
  const bloqueReint = document.getElementById("bloque-mixto-reintegro");

  const totalVentaEl = document.getElementById("mixto-total-venta");
  const sumaPagosEl = document.getElementById("mixto-suma-pagos");
  const errPagosEl = document.getElementById("mixto-error-pagos");

  const reintTotalEl = document.getElementById("reintegro-total");
  const reintSumaEl = document.getElementById("reintegro-suma");
  const errReintEl = document.getElementById("mixto-error-reintegro");

  function esMixto() {
    return ((selectMedio?.value || "").trim().toLowerCase() === "mixto");
  }

  function sumInputsWithin(container) {
    if (!container) return 0;
    const inputs = container.querySelectorAll("input[name$='-monto']");
    let s = 0;
    inputs.forEach(i => s += parseMoney(i.value));
    return Math.max(0, s);
  }

  function calcTotalDevolucion() {
    const rows = document.querySelectorAll("#tabla-detalles tbody tr");
    let total = 0;

    rows.forEach(tr => {
      const precio = parseMoney(tr.getAttribute("data-precio"));
      const inputDev = tr.querySelector("input[name^='dev-'][name$='-devolver']");
      const cant = inputDev ? parseInt(inputDev.value || "0", 10) : 0;
      if (cant > 0) total += cant * precio;
    });

    return Math.max(0, total);
  }

  function setReintegroInputsEnabled(enabled) {
    if (!bloqueReint) return;
    const inputs = bloqueReint.querySelectorAll("input[name$='-monto']");
    inputs.forEach(i => {
      i.disabled = !enabled;
      if (!enabled) i.value = "";
    });
  }

  function validateUI() {
    const mixto = esMixto();

    if (bloquePagos) bloquePagos.style.display = mixto ? "" : "none";
    // bloqueReint se muestra solo si mixto y hay devolución
    if (!mixto) {
      if (bloqueReint) bloqueReint.style.display = "none";
      return;
    }

    const totalVenta = parseMoney(window.VENTA_TOTAL || (totalVentaEl ? totalVentaEl.textContent : "0"));
    if (totalVentaEl) totalVentaEl.textContent = to2(totalVenta);

    const sumaPagos = sumInputsWithin(bloquePagos);
    if (sumaPagosEl) sumaPagosEl.textContent = to2(sumaPagos);

    if (errPagosEl) {
      if (Math.abs(sumaPagos - totalVenta) > 0.009) {
        errPagosEl.style.display = "";
        errPagosEl.textContent = `La suma de pagos (${to2(sumaPagos)}) debe ser igual al total (${to2(totalVenta)}).`;
      } else {
        errPagosEl.style.display = "none";
        errPagosEl.textContent = "";
      }
    }

    const totalDev = calcTotalDevolucion();
    if (reintTotalEl) reintTotalEl.textContent = to2(totalDev);

    if (totalDev > 0) {
      if (bloqueReint) bloqueReint.style.display = "";
      setReintegroInputsEnabled(true);
    } else {
      if (bloqueReint) bloqueReint.style.display = "none";
      setReintegroInputsEnabled(false);
    }

    const sumaReint = sumInputsWithin(bloqueReint);
    if (reintSumaEl) reintSumaEl.textContent = to2(sumaReint);

    if (errReintEl) {
      if (totalDev > 0 && Math.abs(sumaReint - totalDev) > 0.009) {
        errReintEl.style.display = "";
        errReintEl.textContent = `La suma (${to2(sumaReint)}) debe ser igual al total a devolver (${to2(totalDev)}).`;
      } else {
        errReintEl.style.display = "none";
        errReintEl.textContent = "";
      }
    }
  }

  selectMedio?.addEventListener("change", validateUI);

  document.addEventListener("input", (e) => {
    const t = e.target;
    if (!t) return;

    if (t.matches("input[name^='dev-'][name$='-devolver']")) validateUI();
    if (t.matches("input[name$='-monto']")) validateUI();
  });

  form?.addEventListener("submit", (e) => {
    if (!esMixto()) return;

    const totalVenta = parseMoney(window.VENTA_TOTAL || "0");
    const sumaPagos = sumInputsWithin(bloquePagos);

    if (Math.abs(sumaPagos - totalVenta) > 0.009) {
      e.preventDefault();
      alert(`⚠️ La suma de pagos (${to2(sumaPagos)}) debe ser igual al total (${to2(totalVenta)}).`);
      return;
    }

    const totalDev = calcTotalDevolucion();
    if (totalDev > 0) {
      const sumaReint = sumInputsWithin(bloqueReint);
      if (Math.abs(sumaReint - totalDev) > 0.009) {
        e.preventDefault();
        alert(`⚠️ La suma de la devolución (${to2(sumaReint)}) debe ser igual al total a devolver (${to2(totalDev)}).`);
        return;
      }
    }
  });

  // Init
  validateUI();
})();
