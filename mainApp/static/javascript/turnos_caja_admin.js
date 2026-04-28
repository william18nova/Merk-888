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
    box.style.display = "block";
    setTimeout(() => (box.style.display = "none"), 3500);
  }

  function num(v) {
    const n = Number(String(v ?? "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  }

  async function getJSON(url) {
    const r = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
    const txt = await r.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) {}
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
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
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  // ===== elements
  const turnoId = $("#turnoId");
  const btnLoad = $("#btnLoad");
  const editor = $("#editor");

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

  const btnSave = $("#btnSave");
  const btnDelete = $("#btnDelete");

  // ===== state
  let TURNO = null;
  let MEDIOS = []; // [{metodo, esperado, contado, diferencia}]

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
      const el = mediosBody.querySelector(`[data-diff='${m.metodo}']`);
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

      const tdM = document.createElement("td");
      tdM.innerHTML = `<span class="chip">${String(m.metodo || "").toUpperCase()}</span>`;
      tr.appendChild(tdM);

      const tdE = document.createElement("td");
      tdE.className = "num";
      tdE.innerHTML = `<input class="in in-sm numin" type="number" step="0.01" min="0"
                        data-esp="${m.metodo}" value="${num(m.esperado)}">`;
      tr.appendChild(tdE);

      const tdC = document.createElement("td");
      tdC.className = "num";
      tdC.innerHTML = `<input class="in in-sm numin" type="number" step="0.01" min="0"
                        data-con="${m.metodo}" value="${m.contado === null ? "" : num(m.contado)}"
                        placeholder="(vacío = 0)">`;
      tr.appendChild(tdC);

      const tdD = document.createElement("td");
      tdD.className = "num";
      tdD.innerHTML = `<span class="diff" data-diff="${m.metodo}">${money2(m.diferencia || 0)}</span>`;
      tr.appendChild(tdD);

      frag.appendChild(tr);
    });

    mediosBody.appendChild(frag);

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

    buildMediosTable();
    editor.style.display = "block";
  }

  async function loadTurno() {
    const id = (turnoId.value || "").trim();
    if (!id || !/^\d+$/.test(id)) {
      flash(false, "Ingresa un ID válido.");
      return;
    }
    btnLoad.disabled = true;
    try {
      const data = await getJSON(API_DETAIL(id));
      if (!data.success) throw new Error(data.error || "Error");
      hydrate(data);
      flash(true, `Turno #${id} cargado.`);
    } catch (e) {
      editor.style.display = "none";
      flash(false, e.message || "Error cargando turno.");
    } finally {
      btnLoad.disabled = false;
    }
  }

  btnLoad.addEventListener("click", loadTurno);
  turnoId.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadTurno();
  });

  btnSave.addEventListener("click", async () => {
    if (!TURNO?.id) return;

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

    btnSave.disabled = true;
    try {
      const data = await postJSON(API_UPDATE(TURNO.id), payload);
      flash(true, data.msg || "Guardado.");
      // recarga para ver lo recalculado por backend (dif total, deuda, etc.)
      const fresh = await getJSON(API_DETAIL(TURNO.id));
      hydrate(fresh);
    } catch (e) {
      flash(false, e.message || "Error guardando.");
    } finally {
      btnSave.disabled = false;
    }
  });

  btnDelete.addEventListener("click", async () => {
    if (!TURNO?.id) return;

    const ok = confirm(
      `¿Eliminar el turno #${TURNO.id}?\n\nEsto borrará también sus medios asociados.`
    );
    if (!ok) return;

    btnDelete.disabled = true;
    try {
      const data = await postJSON(API_DELETE(TURNO.id), {});
      flash(true, data.msg || "Eliminado.");
      editor.style.display = "none";
      TURNO = null;
      MEDIOS = [];
      turnoId.value = "";
    } catch (e) {
      flash(false, e.message || "Error eliminando.");
    } finally {
      btnDelete.disabled = false;
    }
  });
})();
