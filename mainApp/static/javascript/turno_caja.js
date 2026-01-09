// static/javascript/turno_caja.js
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

  // ====== Autocomplete simple con paginado ======
  function setupAutocomplete(inp, hid, box, url) {
    let page = 1,
      more = true,
      loading = false,
      term = "",
      cache = Object.create(null),
      req = 0;

    function render(list, replace = true) {
      if (replace) box.innerHTML = "";
      const frag = document.createDocumentFragment();
      (list || []).forEach((r) => {
        const d = document.createElement("div");
        d.className = "ac-opt";
        d.dataset.id = r.id;
        d.textContent = r.text;
        frag.appendChild(d);
      });
      box.appendChild(frag);
      box.style.display = "block";
    }

    function fetchPage(q, p, replace = true) {
      const qs = new URLSearchParams({ term: q || "", page: String(p) }).toString();

      if (cache[qs]) {
        render(cache[qs].results || [], replace);
        more = !!cache[qs].has_more;
        return;
      }

      const my = ++req;
      loading = true;

      fetch(`${url}?${qs}`)
        .then((r) => r.json())
        .then((data) => {
          cache[qs] = data || { results: [], has_more: false };
          if (my !== req) return;
          render(data.results || [], replace);
          more = !!data.has_more;
        })
        .catch(() => {})
        .finally(() => (loading = false));
    }

    inp.addEventListener("input", () => {
      hid.value = "";
      term = inp.value.trim();
      page = 1;
      more = true;
      fetchPage(term, page, true);
    });

    inp.addEventListener("focus", () => {
      page = 1;
      more = true;
      term = inp.value.trim();
      fetchPage(term, page, true);
    });

    box.addEventListener("click", (e) => {
      const opt = e.target.closest(".ac-opt");
      if (!opt) return;
      inp.value = opt.textContent;
      hid.value = opt.dataset.id;
      box.style.display = "none";
      inp.dispatchEvent(new CustomEvent("ac:selected"));
    });

    box.addEventListener("scroll", () => {
      if (box.scrollTop + box.clientHeight >= box.scrollHeight - 4 && more && !loading) {
        page += 1;
        fetchPage(term, page, false);
      }
    });

    document.addEventListener("click", (e) => {
      if (!inp.contains(e.target) && !box.contains(e.target)) box.style.display = "none";
    });
  }

  // ====== Helpers ======
  function getNumber(v) {
    const n = Number(String(v || "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  }

  async function postForm(url, dataObj) {
    const fd = new FormData();
    Object.entries(dataObj).forEach(([k, v]) => fd.append(k, v));

    const r = await fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": getCookie("csrftoken") || "" },
      body: fd,
    });

    const txt = await r.text();
    let data = null;
    try {
      data = JSON.parse(txt);
    } catch (e) {}

    if (!r.ok) {
      console.error("POST error", r.status, txt);
      return data || { success: false, error: `HTTP ${r.status}`, raw: txt };
    }
    return data || { success: false, error: "Respuesta inválida", raw: txt };
  }

  // ====== Elements ======
  const stepStart = $("#stepStart");
  const stepOpen = $("#stepOpen");
  const stepClose = $("#stepClose");

  const ppInp = $("#pp_ac"),
    ppHid = $("#pp_id"),
    ppBox = $("#pp_box");
  const cajInp = $("#cajero_ac"),
    cajHid = $("#cajero_id"),
    cajBox = $("#cajero_box");
  const passInp = $("#password");
  const baseInp = $("#base");

  const btnIniciar = $("#btnIniciar");
  const btnIniCierre = $("#btnIniciarCierre");
  const btnCerrar = $("#btnCerrar");

  const infoPP = $("#infoPP");
  const infoCajero = $("#infoCajero");
  const infoInicio = $("#infoInicio");
  const infoBase = $("#infoBase");

  const estadoBadge = $("#estadoBadge");
  const estadoBadge2 = $("#estadoBadge2");

  const efectivoEntregadoInp = $("#efectivo_entregado");
  const mediosBody = $("#mediosBody");

  const mVentas = $("#mVentas");
  const mEsperado = $("#mEsperado");
  const mDiff = $("#mDiff");
  const mDeuda = $("#mDeuda");

  setupAutocomplete(ppInp, ppHid, ppBox, PP_AC_URL);
  setupAutocomplete(cajInp, cajHid, cajBox, CAJERO_AC_URL);

  // ====== State ======
  let TURNO_ID = null;
  let BASE = 0;

  // medios: array {metodo,label,esperado}
  let MEDIOS = [];
  // contados: metodo -> number
  const CONTADOS = Object.create(null);

  function showSection(which) {
    stepStart.style.display = which === "start" ? "block" : "none";
    stepOpen.style.display = which === "open" ? "block" : "none";
    stepClose.style.display = which === "close" ? "block" : "none";
  }

  function setBadge(el, estado) {
    el.textContent = estado;
    el.classList.remove("pill-open", "pill-close", "pill-done");
    if (estado === "ABIERTO") el.classList.add("pill-open");
    else if (estado === "CIERRE") el.classList.add("pill-close");
    else el.classList.add("pill-done");
  }

  // ====== Recalc cierre ======
  let raf = null;
  function scheduleRecalc() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      recalc();
    });
  }

  function recalc() {
    const efectivoEntregado = getNumber(efectivoEntregadoInp.value);
    const efectivoContado = Math.max(0, efectivoEntregado - BASE);

    let sumContado = 0;
    let sumEsperado = 0;

    CONTADOS["efectivo"] = efectivoContado;

    for (const m of MEDIOS) {
      const metodo = m.metodo;
      const esperado = getNumber(m.esperado);
      const contado = metodo === "efectivo" ? efectivoContado : getNumber(CONTADOS[metodo] || 0);
      const diff = contado - esperado;

      sumContado += contado;
      sumEsperado += esperado;

      const diffEl = document.querySelector(`[data-diff='${metodo}']`);
      const contadoEl = document.querySelector(`[data-contado='${metodo}']`);

      if (diffEl) {
        diffEl.textContent = money2(diff);
        diffEl.classList.toggle("neg", diff < 0);
        diffEl.classList.toggle("pos", diff > 0);
      }
      if (contadoEl && metodo === "efectivo") {
        contadoEl.textContent = money2(contado);
      }
    }

    const diferenciaTotal = sumContado - sumEsperado;
    const deudaTotal = diferenciaTotal < 0 ? diferenciaTotal : 0;

    mVentas.textContent = money2(sumContado);
    mEsperado.textContent = money2(sumEsperado);
    mDiff.textContent = money2(diferenciaTotal);
    mDeuda.textContent = money2(deudaTotal);

    mDiff.classList.toggle("neg", diferenciaTotal < 0);
    mDiff.classList.toggle("pos", diferenciaTotal > 0);
    mDeuda.classList.toggle("neg", deudaTotal < 0);
    mDeuda.classList.toggle("pos", deudaTotal > 0);
  }

  function buildTable() {
    mediosBody.innerHTML = "";
    CONTADOS["efectivo"] = 0;

    const frag = document.createDocumentFragment();

    MEDIOS.forEach((m) => {
      const tr = document.createElement("tr");

      const tdM = document.createElement("td");
      tdM.innerHTML = `<span class="chip">${m.label}</span>`;
      tr.appendChild(tdM);

      const tdE = document.createElement("td");
      tdE.className = "num";
      tdE.textContent = money2(m.esperado);
      tr.appendChild(tdE);

      const tdC = document.createElement("td");
      tdC.className = "num";

      if (m.metodo === "efectivo") {
        tdC.innerHTML = `<span class="readonly" data-contado="${m.metodo}">${money2(
          0
        )}</span>
                         <div class="hint">Efectivo contado = (entregado - base)</div>`;
      } else {
        tdC.innerHTML = `<input class="in-num" type="number" step="0.01" min="0"
                          data-in="${m.metodo}" placeholder="0.00">`;
      }
      tr.appendChild(tdC);

      const tdD = document.createElement("td");
      tdD.className = "num";
      tdD.innerHTML = `<span class="diff" data-diff="${m.metodo}">${money2(0)}</span>`;
      tr.appendChild(tdD);

      frag.appendChild(tr);
    });

    mediosBody.appendChild(frag);

    // listeners inputs
    mediosBody.querySelectorAll("[data-in]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const metodo = e.target.getAttribute("data-in");
        CONTADOS[metodo] = getNumber(e.target.value);
        scheduleRecalc();
      });
    });

    // evita duplicar listeners si buildTable se llama varias veces
    efectivoEntregadoInp.oninput = scheduleRecalc;

    scheduleRecalc();
  }

  // ====== Rellena UI con datos de backend ======
  function hydrateTurno(data) {
    TURNO_ID = data.turno_id;

    // ✅ tolerante: backend puede mandar base o saldo_apertura_efectivo
    BASE = Number((data.base ?? data.saldo_apertura_efectivo) ?? 0);

    infoPP.textContent = data.puntopago?.nombre || ppInp.value || "—";
    infoCajero.textContent = data.cajero?.nombreusuario || cajInp.value || "—";
    infoInicio.textContent = (data.inicio || "—")
      .replace("T", " ")
      .replace(/:\d\d\..*$/, "");
    infoBase.textContent = money2(BASE);

    const estado = data.estado || "ABIERTO";

    if (estado === "ABIERTO") {
      setBadge(estadoBadge, "ABIERTO");
      showSection("open");
      return;
    }

    if (estado === "CIERRE") {
      setBadge(estadoBadge2, "CIERRE");

      MEDIOS = (data.medios || []).map((x) => ({
        metodo: x.metodo,
        label: x.label,
        esperado: Number(x.esperado || 0),
      }));

      efectivoEntregadoInp.value = "";
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

      buildTable();
      showSection("close");
      return;
    }

    showSection("start");
  }

  // ====== Actions ======
  btnIniciar.addEventListener("click", async () => {
    const pp_id = (ppHid.value || "").trim();
    const cajero_id = (cajHid.value || "").trim();
    const password = passInp.value || "";
    const base = baseInp.value || "0";

    if (!pp_id || !cajero_id) {
      flash(false, "Selecciona punto de pago y cajero.");
      return;
    }

    btnIniciar.disabled = true;
    try {
      // ✅ MUY IMPORTANTE: action, si no la View responde 400 "Acción inválida"
      // ✅ ENVIAMOS AMBOS KEYS (usuario_id y cajero_id) para evitar 400 por nombres
      const data = await postForm(API_RECUPERAR, {
        action: "recuperar_o_iniciar",
        puntopago_id: pp_id,
        usuario_id: cajero_id,
        cajero_id: cajero_id,
        password: password,
        saldo_apertura_efectivo: base,
      });

      if (!data.success) {
        flash(false, data.error || "Error");
        return;
      }

      flash(true, data.msg || "Turno recuperado/iniciado.");
      hydrateTurno(data);
    } catch (e) {
      flash(false, "Error de red");
    } finally {
      btnIniciar.disabled = false;
    }
  });

  btnIniCierre.addEventListener("click", async () => {
    if (!TURNO_ID) {
      flash(false, "No hay turno activo.");
      return;
    }

    btnIniCierre.disabled = true;
    try {
      const data = await postForm(API_INI_CIERRE, { turno_id: TURNO_ID });

      if (!data.success) {
        flash(false, data.error || "Error");
        return;
      }

      setBadge(estadoBadge2, data.estado || "CIERRE");

      BASE = Number((data.base ?? data.saldo_apertura_efectivo) ?? BASE);

      MEDIOS = (data.medios || []).map((x) => ({
        metodo: x.metodo,
        label: x.label,
        esperado: Number(x.esperado || 0),
      }));

      // orden visual
      const order = ["efectivo", "nequi", "daviplata", "tarjeta", "banco_caja_social"];
      MEDIOS.sort((a, b) => {
        const ia = order.indexOf(a.metodo);
        const ib = order.indexOf(b.metodo);
        if (ia === -1 && ib === -1) return String(a.label).localeCompare(String(b.label));
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      });

      efectivoEntregadoInp.value = "";
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

      buildTable();
      flash(true, "Cierre iniciado.");
      showSection("close");
    } catch (e) {
      flash(false, "Error de red");
    } finally {
      btnIniCierre.disabled = false;
    }
  });

  btnCerrar.addEventListener("click", async () => {
    if (!TURNO_ID) {
      flash(false, "No hay turno en cierre.");
      return;
    }

    const efectivoEntregado = getNumber(efectivoEntregadoInp.value);
    if (efectivoEntregado <= 0) {
      flash(false, "Ingresa el efectivo entregado (total caja).");
      return;
    }

    const mediosSend = [];
    MEDIOS.forEach((m) => {
      if (m.metodo === "efectivo") return;
      mediosSend.push({ metodo: m.metodo, contado: getNumber(CONTADOS[m.metodo] || 0) });
    });

    btnCerrar.disabled = true;
    try {
      const data = await postForm(API_CERRAR, {
        turno_id: TURNO_ID,
        efectivo_entregado: String(efectivoEntregado),
        medios_json: JSON.stringify(mediosSend),
      });

      if (!data.success) {
        flash(false, data.error || "Error");
        return;
      }

      flash(true, data.msg || "Turno cerrado.");
      alert(
        `Turno cerrado.\n` +
          `Ventas (usuario): ${money2(data.ventas_total)}\n` +
          `Esperado (BD): ${money2(data.esperado_total)}\n` +
          `Diferencia: ${money2(data.diferencia_total)}\n` +
          `Deuda: ${money2(data.deuda_total)}`
      );

      // reset
      TURNO_ID = null;
      BASE = 0;
      MEDIOS = [];
      showSection("start");
      ppInp.value = "";
      ppHid.value = "";
      cajInp.value = "";
      cajHid.value = "";
      passInp.value = "";
      baseInp.value = "";
      efectivoEntregadoInp.value = "";
      mediosBody.innerHTML = "";
      mVentas.textContent = "—";
      mEsperado.textContent = "—";
      mDiff.textContent = "—";
      mDeuda.textContent = "—";
    } catch (e) {
      flash(false, "Error de red");
    } finally {
      btnCerrar.disabled = false;
    }
  });
})();
