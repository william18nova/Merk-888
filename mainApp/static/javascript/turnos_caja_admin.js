// static/javascript/turnos_caja_admin.js
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);

  const money2 = (v) =>
    new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency: "COP",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(v || 0));

  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return m ? decodeURIComponent(m[2]) : null;
  }

  function flash(ok, msg) {
    const box = $("#flash");
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = msg;
    box.hidden = false;
    setSaveStatus(msg, ok ? "success" : "error");
  }

  function num(v) {
    const n = Number(String(v ?? "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  }

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store", headers: { "X-Requested-With": "XMLHttpRequest" } });
    const txt = await r.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) {}
    if (!r.ok || !data?.success) throw new Error(data?.error || "No se pudo cargar el turno. Revisa tu conexión o sesión.");
    return data;
  }

  async function postJSON(url, payload) {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCookie("csrftoken") || "",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    const txt = await r.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) {}
    if (!r.ok || !data?.success) throw new Error(data?.error || "No se pudo completar la operación. Revisa tu conexión o sesión antes de reintentar.");
    return data;
  }

  // ===== elements
  const turnoId = $("#turnoId");
  const btnLoad = $("#btnLoad");
  const editor = $("#editor");
  const loadState = $("#loadState");
  const saveStatus = $("#saveStatus");

  const estado = $("#estado");
  const base = $("#base");
  const inicio = $("#inicio");
  const cierre = $("#cierre");
  const fin = $("#fin");
  const efectivoReal = $("#efectivoReal");

  const ppName = $("#ppName");
  const cajName = $("#cajName");

  const mediosBody = $("#mediosBody");

  const mEsperado = $("#mEsperado");
  const mVentas = $("#mVentas");
  const mDiff = $("#mDiff");
  const mDeuda = $("#mDeuda");
  const mEf = $("#mEf");
  const mNoEf = $("#mNoEf");
  const mFacturasPagadas = $("#mFacturasPagadas");

  const btnSave = $("#btnSave");
  const btnDelete = $("#btnDelete");

  // ===== state
  let TURNO = null;
  let MEDIOS = []; // [{metodo, esperado, contado, diferencia}]
  let busy = false;
  let protectedPTM = false;

  function setSaveStatus(message, state = "") {
    saveStatus.textContent = message;
    saveStatus.dataset.state = state;
  }

  function syncControls() {
    btnLoad.disabled = busy;
    turnoId.disabled = busy;
    editor.setAttribute("aria-busy", String(busy));
    editor.querySelectorAll("input,select").forEach(input => { input.disabled = busy || protectedPTM; });
    btnSave.disabled = busy || protectedPTM || !TURNO;
    if (btnDelete) btnDelete.disabled = busy || protectedPTM || !TURNO;
  }

  function showLoadState(title, message) {
    loadState.hidden = false;
    $("#loadStateTitle").textContent = title;
    $("#loadStateText").textContent = message;
  }

  function numClass(v) {
    const n = Number(v || 0);
    if (n < 0) return "neg";
    if (n > 0) return "pos";
    return "";
  }

  function recalcUI() {
    let esperadoTotal = 0;
    let ventasTotal = 0;
    let esperadoEf = 0;
    let contadoEf = 0;
    let deudaTotal = 0;

    for (const m of MEDIOS) {
      const esp = num(m.esperado);
      const con = (m.contado === "" || m.contado === null || typeof m.contado === "undefined") ? 0 : num(m.contado);
      m.diferencia = con - esp;

      esperadoTotal += esp;
      ventasTotal += con;

      if ((m.metodo || "").toLowerCase() === "efectivo") {
        esperadoEf = esp;
        contadoEf = con;
      }
      if (m.diferencia < 0) deudaTotal += m.diferencia;
    }

    const diffTotal = ventasTotal - esperadoTotal;

    mEsperado.textContent = money2(esperadoTotal);
    mVentas.textContent = money2(ventasTotal);
    mDiff.textContent = money2(diffTotal);
    mDeuda.textContent = money2(deudaTotal);
    mEf.textContent = money2(contadoEf);
    mNoEf.textContent = money2(ventasTotal - contadoEf);

    mDiff.className = "v " + numClass(diffTotal);
    mDeuda.className = "v " + (deudaTotal < 0 ? "neg" : "");

    // pintar diffs por fila
    for (const m of MEDIOS) {
      const el = Array.from(mediosBody.querySelectorAll("[data-diff]")).find(node => node.dataset.diff === m.metodo);
      if (el) {
        el.textContent = money2(m.diferencia);
        el.className = "diff " + numClass(m.diferencia);
      }
    }
  }

  function buildMediosTable() {
    mediosBody.innerHTML = "";
    const frag = document.createDocumentFragment();

    MEDIOS.forEach((m) => {
      const tr = document.createElement("tr");
      tr.setAttribute("role", "row");

      const tdM = document.createElement("td");
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = m.label || String(m.metodo || "").toUpperCase();
      tdM.appendChild(chip);
      tr.appendChild(tdM);

      const tdE = document.createElement("td");
      tdE.className = "num";
      tdE.dataset.label = "Esperado";
      const inputEsperado = document.createElement("input");
      inputEsperado.className = "in in-sm numin";
      inputEsperado.type = "number";
      inputEsperado.step = "0.01";
      inputEsperado.min = "0";
      inputEsperado.dataset.esp = m.metodo;
      inputEsperado.setAttribute("aria-label", `Esperado · ${chip.textContent}`);
      inputEsperado.value = String(num(m.esperado));
      tdE.appendChild(inputEsperado);
      tr.appendChild(tdE);

      const tdC = document.createElement("td");
      tdC.className = "num";
      tdC.dataset.label = "Contado";
      const inputContado = document.createElement("input");
      inputContado.className = "in in-sm numin";
      inputContado.type = "number";
      inputContado.step = "0.01";
      inputContado.min = "0";
      inputContado.dataset.con = m.metodo;
      inputContado.setAttribute("aria-label", `Contado · ${chip.textContent}`);
      inputContado.value = m.contado === null ? "" : String(num(m.contado));
      inputContado.placeholder = "(vacío = 0)";
      tdC.appendChild(inputContado);
      tr.appendChild(tdC);

      const tdD = document.createElement("td");
      tdD.className = "num";
      tdD.dataset.label = "Diferencia";
      const diff = document.createElement("span");
      diff.className = "diff";
      diff.dataset.diff = m.metodo;
      diff.textContent = money2(m.diferencia || 0);
      tdD.appendChild(diff);
      tr.appendChild(tdD);

      frag.appendChild(tr);
    });

    mediosBody.appendChild(frag);
    mediosBody.querySelectorAll("td").forEach(cell => cell.setAttribute("role", "cell"));
    if (!MEDIOS.length) {
      const row = mediosBody.insertRow();
      row.className = "tca-no-medios";
      const cell = row.insertCell();
      cell.colSpan = 4;
      cell.textContent = "Este turno no tiene medios de pago registrados.";
    }

    mediosBody.querySelectorAll("[data-esp]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const metodo = e.target.getAttribute("data-esp");
        const obj = MEDIOS.find(x => x.metodo === metodo);
        if (obj) obj.esperado = num(e.target.value);
        recalcUI();
      });
    });

    mediosBody.querySelectorAll("[data-con]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const metodo = e.target.getAttribute("data-con");
        const obj = MEDIOS.find(x => x.metodo === metodo);
        if (obj) obj.contado = (e.target.value === "" ? "" : num(e.target.value));
        recalcUI();
      });
    });

    recalcUI();
  }

  function hydrate(data) {
    TURNO = data.turno;
    MEDIOS = (data.medios || []).map(m => ({
      metodo: m.metodo,
      label: m.label || String(m.metodo || "").toUpperCase(),
      esperado: num(m.esperado),
      contado: (m.contado === null ? "" : num(m.contado)),
      diferencia: num(m.diferencia),
    }));

    // turno fields
    estado.value = TURNO.estado || "ABIERTO";
    base.value = num(TURNO.saldo_apertura_efectivo || 0);
    inicio.value = TURNO.inicio_local || "";
    cierre.value = TURNO.cierre_iniciado_local || "";
    fin.value = TURNO.fin_local || "";
    efectivoReal.value = (TURNO.efectivo_real === null || typeof TURNO.efectivo_real === "undefined") ? "" : num(TURNO.efectivo_real);

    ppName.textContent = TURNO.puntopago || "—";
    cajName.textContent = TURNO.cajero || "—";
    $("#loadedTurnoId").textContent = `#${TURNO.id}`;
    $("#turnoStatus").textContent = {ABIERTO:"ABIERTO", CIERRE:"EN CIERRE", CERRADO:"CERRADO"}[TURNO.estado] || TURNO.estado;
    $("#turnoStatus").dataset.state = TURNO.estado;
    // Dato informativo: el contado de Efectivo ya incluye estas facturas.
    mFacturasPagadas.textContent = money2(TURNO.facturas_pagadas ?? 0);
    const ptm = TURNO.ptm || {};
    $("#ptmCantidad").textContent = ptm.cantidad || 0;
    $("#ptmDeclarado").textContent = ptm.declarado ?? "—";
    $("#ptmRecargas").textContent = money2(ptm.recargas || 0);
    $("#ptmRetiros").textContent = money2(ptm.retiros || 0);
    $("#ptmNeto").textContent = money2(ptm.neto || 0);
    $("#ptmNeto").className = numClass(ptm.neto || 0);
    document.getElementById("mPTMHistorial").search = `?turno=${TURNO.id}`;

    buildMediosTable();
    protectedPTM = Number(ptm.cantidad || 0) > 0;
    document.getElementById("mVentasLabel").textContent = protectedPTM ? "Neto reconocido (incluye PTM)" : "Total reportado";
    document.getElementById("mEfectivoLabel").textContent = protectedPTM ? "Efectivo neto (incluye PTM)" : "Efectivo reportado";
    $("#ptmProtection").hidden = !protectedPTM;
    btnSave.title = protectedPTM ? "Este turno tiene registros PTM protegidos. Utiliza el cierre normal." : "";
    setSaveStatus(protectedPTM ? "Solo consulta: turno protegido por PTM." : "Sin cambios pendientes.");
    syncControls();
    editor.hidden = false;
    loadState.hidden = true;
  }

  async function loadTurno() {
    if (busy) return;
    const id = (turnoId.value || "").trim();
    if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) {
      flash(false, "Ingresa un ID válido.");
      turnoId.focus();
      return;
    }
    busy = true;
    TURNO = null;
    MEDIOS = [];
    $("#flash").hidden = true;
    editor.hidden = true;
    showLoadState(`Cargando turno #${id}…`, "Estamos consultando sus datos y medios de pago.");
    syncControls();
    try {
      const data = await getJSON(API_DETAIL(id));
      hydrate(data);
      const url = new URL(window.location.href);
      url.searchParams.set("turno_id", String(TURNO.id));
      window.history.replaceState(window.history.state, "", url);
    } catch (e) {
      editor.hidden = true;
      TURNO = null;
      showLoadState("No pudimos cargar el turno", "Comprueba el ID e inténtalo de nuevo.");
      flash(false, e.message || "Error cargando turno.");
    } finally {
      busy = false;
      syncControls();
    }
  }

  $("#turnoSearch").addEventListener("submit", (e) => {
    e.preventDefault();
    loadTurno();
  });
  editor.addEventListener("input", () => {
    if (!busy && !protectedPTM) setSaveStatus("Hay cambios pendientes de guardar.", "dirty");
  });

  btnSave.addEventListener("click", async () => {
    if (!TURNO?.id || busy || protectedPTM) return;
    const id = TURNO.id;

    const payload = {
      estado: estado.value,
      saldo_apertura_efectivo: base.value,
      inicio_local: inicio.value,
      cierre_iniciado_local: cierre.value,
      fin_local: fin.value,
      efectivo_real: efectivoReal.value,
      medios: MEDIOS.map(m => ({
        metodo: m.metodo,
        esperado: String(num(m.esperado)),
        contado: (m.contado === "" ? "" : String(num(m.contado))),
      })),
    };

    busy = true;
    $("#flash").hidden = true;
    setSaveStatus("Guardando cambios…");
    syncControls();
    try {
      const data = await postJSON(API_UPDATE(id), payload);
      // recarga para ver lo recalculado por backend (dif total, deuda, etc.)
      try {
        const fresh = await getJSON(API_DETAIL(id));
        hydrate(fresh);
        flash(true, data.msg || "Cambios guardados.");
      } catch (_) {
        flash(false, "Los cambios se guardaron, pero no pudimos actualizar el resumen. Vuelve a cargar el turno para verificarlo.");
      }
    } catch (e) {
      flash(false, e.message || "Error guardando.");
    } finally {
      busy = false;
      syncControls();
    }
  });

  btnDelete?.addEventListener("click", async () => {
    if (!TURNO?.id || busy || protectedPTM) return;
    const id = TURNO.id;

    const ok = confirm(
      `¿Eliminar el turno #${TURNO.id}?\n\nEsto borrará también sus medios asociados.`
    );
    if (!ok) return;

    busy = true;
    $("#flash").hidden = true;
    setSaveStatus("Eliminando turno…");
    syncControls();
    try {
      const data = await postJSON(API_DELETE(id), {});
      flash(true, data.msg || "Eliminado.");
      editor.hidden = true;
      TURNO = null;
      MEDIOS = [];
      turnoId.value = "";
      showLoadState("Turno eliminado", "Puedes introducir el ID de otro turno para consultarlo.");
      const url = new URL(window.location.href);
      url.searchParams.delete("turno_id");
      window.history.replaceState(window.history.state, "", url);
    } catch (e) {
      flash(false, e.message || "Error eliminando.");
    } finally {
      busy = false;
      syncControls();
    }
  });

  const params = new URLSearchParams(window.location.search);
  const initialTurnoId = (params.get("turno_id") || "").trim();
  if (/^\d+$/.test(initialTurnoId)) {
    turnoId.value = initialTurnoId;
    loadTurno();
  }
})();
