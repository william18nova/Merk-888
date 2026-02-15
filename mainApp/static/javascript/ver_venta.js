(function () {
  "use strict";

  /* =========================
     Helpers
     ========================= */
  function parseMoney(v) {
    const s = String(v ?? "").trim().replace(",", ".");
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }
  function to2(n) { return (Math.round(n * 100) / 100).toFixed(2); }

  function getCSRFToken() {
    return document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  }

  /* =========================
     DOM
     ========================= */
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

  const btnPrint = document.getElementById("btn-imprimir-factura");

  /* =========================
     Mixto UI
     ========================= */
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

  // ✅ Autofill al cambiar a mixto (para que puedas guardar el cambio sin que te bloquee suma=0)
  let prevMixto = esMixto();

  selectMedio?.addEventListener("change", () => {
    const nowMixto = esMixto();

    if (nowMixto && !prevMixto && bloquePagos) {
      const totalVenta = parseMoney(window.VENTA_TOTAL || "0");
      const inputs = bloquePagos.querySelectorAll("input[name$='-monto']");

      let sum = 0;
      inputs.forEach(i => sum += parseMoney(i.value));

      // Si estaba todo en 0, poner el total en el primer input
      if (sum < 0.009 && inputs.length) {
        inputs.forEach((i, idx) => i.value = (idx === 0 ? to2(totalVenta) : "0.00"));
      }
    }

    prevMixto = nowMixto;
    validateUI();
  });

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

  /* =========================
     ✅ IMPRIMIR DIRECTO (POS AGENT)
     ========================= */
  async function fetchTicketText(ventaId) {
    const csrf = getCSRFToken();
    const fd = new FormData();
    fd.append("csrfmiddlewaretoken", csrf);
    fd.append("venta_id", String(ventaId));

    const r = await fetch(window.ticketTextoUrl, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: fd
    });

    const data = await r.json().catch(() => ({}));

    if (!r.ok || !data.success) {
      throw new Error(data.error || "No se pudo generar el texto del ticket.");
    }
    return String(data.receipt_text || "");
  }

  async function posAgentPrint(text) {
    const base = (window.POS_AGENT_URL || "").trim().replace(/\/$/, "");
    if (!base) return false;

    const PRINT_PATH = "/print";
    const url = base + PRINT_PATH;

    const token = (window.POS_AGENT_TOKEN || "").trim();
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const r = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ text })
    });

    if (!r.ok) throw new Error("POS Agent no respondió correctamente al imprimir.");
    return true;
  }

  function imprimirFallbackBrowser(text) {
    const w = window.open("", "_blank");
    if (!w) {
      alert("⚠️ El navegador bloqueó la ventana emergente para imprimir.");
      return;
    }
    w.document.write("<pre style='font:12px/1.3 monospace;white-space:pre-wrap;margin:16px'></pre>");
    w.document.querySelector("pre").textContent = text;
    w.document.close();
    w.focus();
    w.print();
  }

  btnPrint?.addEventListener("click", async (e) => {
    e.preventDefault();

    const ventaId = btnPrint.getAttribute("data-venta-id");

    try {
      btnPrint.disabled = true;

      if (!ventaId) throw new Error("No encontré el ID de la venta.");

      const text = await fetchTicketText(ventaId);

      try {
        const ok = await posAgentPrint(text);
        if (ok) return;
      } catch (err) {
        console.warn("POS Agent falló, usando fallback navegador:", err);
      }

      imprimirFallbackBrowser(text);

    } catch (err) {
      alert("⚠️ " + (err?.message || "Error al imprimir."));
      console.error(err);
    } finally {
      btnPrint.disabled = false;
    }
  });

  // Init
  validateUI();
})();
