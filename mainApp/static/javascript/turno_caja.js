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
    if (!box) return;
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = msg || (ok ? "OK" : "Error");
    box.style.display = "block";
    setTimeout(() => (box.style.display = "none"), 3500);
  }

  // ====== Autocomplete simple con paginado ======
  function setupAutocomplete(inp, hid, box, url) {
    let page = 1, more = true, loading = false, term = "", cache = Object.create(null), req = 0;

    function render(list, replace = true) {
      if (!box) return;
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
      if (!url) return;
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
    try { data = JSON.parse(txt); } catch (e) {}

    if (!r.ok) {
      console.error("POST error", r.status, txt);
      return data || { success: false, error: `HTTP ${r.status}`, raw: txt };
    }
    return data || { success: false, error: "Respuesta inválida", raw: txt };
  }

  // ====== Elements ======
  const stepStart = $("#stepStart");
  const stepOpen  = $("#stepOpen");
  const stepClose = $("#stepClose");

  const ppInp = $("#pp_ac"), ppHid = $("#pp_id"), ppBox = $("#pp_box");
  const cajInp = $("#cajero_ac"), cajHid = $("#cajero_id"), cajBox = $("#cajero_box");

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
  const mDeuda = $("#mDeuda");

  if (typeof PP_AC_URL !== "undefined" && ppInp && ppHid && ppBox)
    setupAutocomplete(ppInp, ppHid, ppBox, PP_AC_URL);
  if (typeof CAJERO_AC_URL !== "undefined" && cajInp && cajHid && cajBox)
    setupAutocomplete(cajInp, cajHid, cajBox, CAJERO_AC_URL);

  // ====== State ======
  let TURNO_ID = null;
  let BASE = 0;

  // medios: array {metodo,label}
  let MEDIOS = [];
  // contados: metodo -> number
  const CONTADOS = Object.create(null);

  function showSection(which) {
    if (stepStart) stepStart.style.display = which === "start" ? "block" : "none";
    if (stepOpen)  stepOpen.style.display  = which === "open"  ? "block" : "none";
    if (stepClose) stepClose.style.display = which === "close" ? "block" : "none";
  }

  function setBadge(el, estado) {
    if (!el) return;
    el.textContent = estado;
    el.classList.remove("pill-open", "pill-close", "pill-done");
    if (estado === "ABIERTO") el.classList.add("pill-open");
    else if (estado === "CIERRE") el.classList.add("pill-close");
    else el.classList.add("pill-done");
  }

  // ====== Recalc cierre (solo ventas/deuda) ======
  let raf = null;
  function scheduleRecalc() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; recalc(); });
  }

  function recalc() {
    if (!efectivoEntregadoInp) return;

    const efectivoEntregado = getNumber(efectivoEntregadoInp.value);
    const efectivoContado = Math.max(0, efectivoEntregado - BASE);

    let sumContado = 0;

    CONTADOS["efectivo"] = efectivoContado;

    for (const m of MEDIOS) {
      const metodo = m.metodo;
      const contado = metodo === "efectivo" ? efectivoContado : getNumber(CONTADOS[metodo] || 0);
      sumContado += contado;

      if (metodo === "efectivo") {
        const contadoEl = document.querySelector(`[data-contado='${metodo}']`);
        if (contadoEl) contadoEl.textContent = money2(contado);
      }
    }

    // deuda: aquí la dejamos en 0 (porque ya no existe "esperado" para comparar)
    // si quieres que deuda sea "negativos" de algún cálculo, eso requiere esperado.
    const deudaTotal = 0;

    if (mVentas) mVentas.textContent = money2(sumContado);
    if (mDeuda)  mDeuda.textContent  = money2(deudaTotal);
  }

  function buildTable() {
    if (!mediosBody) return;

    mediosBody.innerHTML = "";
    CONTADOS["efectivo"] = 0;

    const frag = document.createDocumentFragment();

    MEDIOS.forEach((m) => {
      const tr = document.createElement("tr");

      const tdM = document.createElement("td");
      tdM.innerHTML = `<span class="chip">${m.label}</span>`;
      tr.appendChild(tdM);

      const tdC = document.createElement("td");
      tdC.className = "num";

      if (m.metodo === "efectivo") {
        tdC.innerHTML = `
          <span class="readonly" data-contado="${m.metodo}">${money2(0)}</span>
          <div class="hint">Efectivo contado = (entregado - base)</div>
        `;
      } else {
        tdC.innerHTML = `
          <input class="in-num" type="number" step="0.01" min="0"
                 data-in="${m.metodo}" placeholder="0.00">
        `;
      }

      tr.appendChild(tdC);
      frag.appendChild(tr);
    });

    mediosBody.appendChild(frag);

    mediosBody.querySelectorAll("[data-in]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const metodo = e.target.getAttribute("data-in");
        CONTADOS[metodo] = getNumber(e.target.value);
        scheduleRecalc();
      });
    });

    if (efectivoEntregadoInp) efectivoEntregadoInp.oninput = scheduleRecalc;

    scheduleRecalc();
  }

  // ====== Rellena UI con datos de backend ======
  function hydrateTurno(data) {
    TURNO_ID = data.turno_id || data.turno?.id || null;

    const baseFrom =
      data.base ??
      data.saldo_apertura_efectivo ??
      data.turno?.saldo_apertura_efectivo ??
      0;

    BASE = Number(baseFrom ?? 0);

    const ppObj = data.puntopago ?? data.turno?.puntopago;
    const cajObj = data.cajero ?? data.turno?.cajero;

    if (infoPP) infoPP.textContent = ppObj?.nombre || ppInp?.value || data.turno?.puntopago || "—";
    if (infoCajero) infoCajero.textContent = cajObj?.nombreusuario || cajInp?.value || data.turno?.cajero || "—";

    const inicioTxt = data.inicio || data.turno?.inicio || "—";
    if (infoInicio) infoInicio.textContent = String(inicioTxt).replace("T", " ").replace(/:\d\d\..*$/, "");
    if (infoBase) infoBase.textContent = money2(BASE);

    const estado = data.estado || data.turno?.estado || "ABIERTO";

    if (estado === "ABIERTO") {
      setBadge(estadoBadge, "ABIERTO");
      showSection("open");
      return;
    }

    if (estado === "CIERRE") {
      setBadge(estadoBadge2, "CIERRE");

      MEDIOS = (data.medios || []).map((x) => ({
        metodo: (x.metodo || "").toLowerCase().trim(),
        label:
          x.label ||
          (x.metodo || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      }));

      if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

      buildTable();
      showSection("close");
      return;
    }

    showSection("start");
  }

  // ====== Actions ======
  if (btnIniciar) {
    btnIniciar.addEventListener("click", async () => {
      const pp_id = (ppHid?.value || "").trim();
      const cajero_id = (cajHid?.value || "").trim();
      const password = passInp?.value || "";
      const base = baseInp?.value || "0";

      if (!pp_id || !cajero_id) {
        flash(false, "Selecciona punto de pago y cajero.");
        return;
      }

      btnIniciar.disabled = true;
      try {
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
  }

  if (btnIniCierre) {
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

        flash(true, "Cierre iniciado.");
        hydrateTurno({
          ...data,
          turno_id: data.turno_id || TURNO_ID,
          estado: data.estado || "CIERRE",
          base: data.base ?? BASE,
        });
      } catch (e) {
        flash(false, "Error de red");
      } finally {
        btnIniCierre.disabled = false;
      }
    });
  }

  if (btnCerrar) {
    btnCerrar.addEventListener("click", async () => {
      if (!TURNO_ID) {
        flash(false, "No hay turno en cierre.");
        return;
      }

      const efectivoEntregado = getNumber(efectivoEntregadoInp?.value);
      if (efectivoEntregado < 0) {
        flash(false, "El efectivo entregado no puede ser negativo.");
        return;
      }

      const mediosOut = [];
      MEDIOS.forEach((m) => {
        if (m.metodo === "efectivo") return;
        mediosOut.push({
          metodo: m.metodo,
          contado: getNumber(CONTADOS[m.metodo] || 0),
        });
      });

      btnCerrar.disabled = true;
      try {
        const data = await postForm(API_CERRAR, {
          turno_id: TURNO_ID,
          efectivo_entregado: String(efectivoEntregado),
          medios_json: JSON.stringify(mediosOut),
        });

        if (!data.success) {
          flash(false, data.error || "Error");
          return;
        }

        flash(true, data.msg || "Turno cerrado.");

        alert(
          `Turno cerrado.\n` +
          `Ventas (usuario): ${money2(data.ventas_total ?? 0)}\n` +
          `Deuda: ${money2(data.deuda_total ?? 0)}`
        );

        // reset UI
        TURNO_ID = null;
        BASE = 0;
        MEDIOS = [];
        for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

        showSection("start");

        if (ppInp) ppInp.value = "";
        if (ppHid) ppHid.value = "";
        if (cajInp) cajInp.value = "";
        if (cajHid) cajHid.value = "";
        if (passInp) passInp.value = "";
        if (baseInp) baseInp.value = "";
        if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";

        if (mediosBody) mediosBody.innerHTML = "";

        if (mVentas) mVentas.textContent = "—";
        if (mDeuda) mDeuda.textContent = "—";
      } catch (e) {
        flash(false, "Error de red");
      } finally {
        btnCerrar.disabled = false;
      }
    });
  }

  // init
  showSection("start");
})();
