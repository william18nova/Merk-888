// static/javascript/turno_caja.js
// ✅ Refactor v9: AbortController, CSRF desde form, doble-submit guard real,
// keyboard nav en AC, persistencia localStorage del contado, toast no bloqueante,
// modal de confirmación, reloj de duración del turno, manejo 401/403.
(function () {
  "use strict";

  /* ============================================================
     UTILS
     ============================================================ */
  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const money2 = (v) =>
    new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency: "COP",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(v || 0));

  const num = (v) => {
    const n = Number(String(v ?? "").replace(",", ".").trim());
    return Number.isFinite(n) ? n : 0;
  };

  const DENOMS = [
    { key: "b100000", value: 100000, label: "Billete $100.000" },
    { key: "b50000", value: 50000, label: "Billete $50.000" },
    { key: "b20000", value: 20000, label: "Billete $20.000" },
    { key: "b10000", value: 10000, label: "Billete $10.000" },
    { key: "b5000", value: 5000, label: "Billete $5.000" },
    { key: "b2000", value: 2000, label: "Billete $2.000" },
    { key: "m1000", value: 1000, label: "Moneda $1.000" },
    { key: "m500", value: 500, label: "Moneda $500" },
    { key: "m200", value: 200, label: "Moneda $200" },
    { key: "m100", value: 100, label: "Moneda $100" },
    { key: "m50", value: 50, label: "Moneda $50" },
  ];

  const safeFromIso = (s) => {
    if (!s) return null;
    try {
      const d = new Date(String(s));
      return isNaN(d.getTime()) ? null : d;
    } catch { return null; }
  };

  const fmtDateTime = (s) => {
    const d = safeFromIso(s);
    if (!d) return String(s || "—");
    return d.toLocaleString("es-CO", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false
    });
  };

  const fmtDuration = (ms) => {
    if (!Number.isFinite(ms) || ms < 0) return "—";
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(h)}:${pad(m)}:${pad(ss)}`;
  };

  /* ============================================================
     CSRF: leer del form (más robusto que cookie)
     ============================================================ */
  function getCSRF() {
    const inp = document.querySelector("input[name='csrfmiddlewaretoken']");
    if (inp && inp.value) return inp.value;
    const m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  /* ============================================================
     TOAST no-bloqueante (reemplaza al alert/flash anterior)
     ============================================================ */
  const TOASTS = $("#tc-toasts");
  function toast(kind, msg, ttl = 3500) {
    if (!TOASTS) return;
    const t = document.createElement("div");
    t.className = `tc-toast tc-toast-${kind === "ok" ? "ok" : kind === "warn" ? "warn" : "err"}`;
    t.setAttribute("role", "status");
    t.textContent = String(msg || "");
    TOASTS.appendChild(t);
    requestAnimationFrame(() => t.classList.add("show"));
    const close = () => {
      t.classList.remove("show");
      setTimeout(() => t.remove(), 220);
    };
    const id = setTimeout(close, ttl);
    t.addEventListener("click", () => { clearTimeout(id); close(); });
  }
  const ok   = (m) => toast("ok",   m, 3000);
  const warn = (m) => toast("warn", m, 4000);
  const err  = (m) => toast("err",  m, 5000);

  /* ============================================================
     MODAL de confirmación + resumen
     ============================================================ */
  function modal(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const close = () => {
      el.hidden = true;
      el.querySelectorAll("[data-close]").forEach((b) => b.replaceWith(b.cloneNode(true)));
    };
    el.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
    return { el, close };
  }

  function confirmModal({ title, msg, okText = "Confirmar", cancelText = "Cancelar", danger = false }) {
    return new Promise((resolve) => {
      const el = document.getElementById("tc-confirm");
      if (!el) { resolve(window.confirm(`${title}\n\n${msg}`)); return; }

      $("#tc-confirm-title").textContent = title || "¿Confirmar?";
      $("#tc-confirm-msg").textContent   = msg   || "";

      const btnOk = $("#tc-confirm-ok");
      btnOk.textContent = okText;
      btnOk.className = "btn " + (danger ? "btn-danger" : "btn-primary");

      const ghost = el.querySelector(".btn-ghost");
      if (ghost) ghost.textContent = cancelText;

      el.hidden = false;

      const cleanup = () => {
        el.hidden = true;
        btnOk.removeEventListener("click", onOk);
        el.querySelectorAll("[data-close]").forEach((b) => b.removeEventListener("click", onCancel));
        document.removeEventListener("keydown", onKey, true);
      };
      const onOk = () => { cleanup(); resolve(true); };
      const onCancel = () => { cleanup(); resolve(false); };
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        else if (e.key === "Enter") { e.preventDefault(); onOk(); }
      };

      btnOk.addEventListener("click", onOk);
      el.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", onCancel));
      document.addEventListener("keydown", onKey, true);

      requestAnimationFrame(() => btnOk.focus());
    });
  }

  function summaryModal(html) {
    const el = document.getElementById("tc-summary");
    if (!el) return;
    $("#tc-summary-body").innerHTML = html;
    el.hidden = false;
    el.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => (el.hidden = true), { once: true })
    );
    const onKey = (e) => {
      if (e.key === "Escape" || e.key === "Enter") {
        e.preventDefault();
        el.hidden = true;
        document.removeEventListener("keydown", onKey, true);
      }
    };
    document.addEventListener("keydown", onKey, true);
  }

  /* ============================================================
     postForm con AbortController + manejo 401/403
     ============================================================ */
  async function postForm(url, dataObj, { signal } = {}) {
    const fd = new FormData();
    Object.entries(dataObj || {}).forEach(([k, v]) => fd.append(k, v == null ? "" : v));

    let resp;
    try {
      resp = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRF(), "X-Requested-With": "XMLHttpRequest" },
        body: fd,
        signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      return { success: false, error: "Sin conexión con el servidor." };
    }

    if (resp.status === 401 || resp.status === 403) {
      err("Tu sesión expiró. Vamos a recargar para que vuelvas a iniciar sesión.");
      setTimeout(() => location.reload(), 1500);
      return { success: false, error: "Sesión expirada." };
    }

    const txt = await resp.text();
    let data = null;
    try { data = JSON.parse(txt); } catch {}
    if (!resp.ok) {
      console.error("POST error", resp.status, txt);
      return data || { success: false, error: `HTTP ${resp.status}` };
    }
    return data || { success: false, error: "Respuesta inválida del servidor." };
  }

  /* ============================================================
     AUTOCOMPLETE con AbortController + keyboard nav + paginado
     ============================================================ */
  function setupAutocomplete(inp, hid, box, url, opts = {}) {
    const { onSelected = null } = opts;
    let page = 1, more = true, loading = false, term = "";
    let req = 0, controller = null;
    let activeIdx = -1; // índice del item activo
    let lastFocusOpenAt = 0;

    function setExpanded(v) { inp.setAttribute("aria-expanded", v ? "true" : "false"); }
    function items() { return $$(".ac-opt", box); }

    function clearActive() {
      items().forEach((el) => el.classList.remove("ac-active"));
      activeIdx = -1;
    }

    function setActive(idx) {
      const list = items();
      if (!list.length) { activeIdx = -1; return; }
      const i = ((idx % list.length) + list.length) % list.length;
      list.forEach((el) => el.classList.remove("ac-active"));
      list[i].classList.add("ac-active");
      list[i].scrollIntoView({ block: "nearest" });
      activeIdx = i;
    }

    function render(list, replace = true) {
      if (!box) return;
      if (replace) { box.innerHTML = ""; activeIdx = -1; }
      const frag = document.createDocumentFragment();
      (list || []).forEach((r) => {
        const d = document.createElement("div");
        d.className = "ac-opt";
        d.dataset.id = r.id;
        d.setAttribute("role", "option");
        d.textContent = r.text;
        frag.appendChild(d);
      });
      box.appendChild(frag);
      box.style.display = "block";
      setExpanded(true);
    }

    async function fetchPage(q, p, replace = true) {
      if (!url) return;
      // cancelar request anterior
      try { controller?.abort(); } catch {}
      controller = new AbortController();
      const my = ++req;

      const qs = new URLSearchParams({ term: q || "", page: String(p) }).toString();
      loading = true;
      try {
        const r = await fetch(`${url}?${qs}`, { signal: controller.signal });
        if (my !== req) return;
        if (!r.ok) {
          if (r.status === 401 || r.status === 403) {
            err("Tu sesión expiró.");
            setTimeout(() => location.reload(), 1200);
          }
          return;
        }
        const data = await r.json().catch(() => ({ results: [], has_more: false }));
        if (my !== req) return;
        render(data.results || [], replace);
        more = !!data.has_more;
      } catch (e) {
        if (e.name !== "AbortError") console.error("AC error", e);
      } finally {
        if (my === req) loading = false;
      }
    }

    function selectItem(opt) {
      if (!opt) return;
      inp.value = opt.textContent;
      hid.value = opt.dataset.id || "";
      box.style.display = "none";
      setExpanded(false);
      clearActive();
      inp.dispatchEvent(new CustomEvent("ac:selected"));
      onSelected?.(opt.dataset.id, opt.textContent);
    }

    inp.addEventListener("input", () => {
      hid.value = "";
      term = inp.value.trim();
      page = 1; more = true;
      fetchPage(term, page, true);
      // disparar evento custom para revalidar formulario
      inp.dispatchEvent(new CustomEvent("ac:cleared"));
    });

    inp.addEventListener("focus", () => {
      // evita re-disparar muchas veces si el usuario hace click luego de ya estar abierto
      const now = Date.now();
      if (now - lastFocusOpenAt < 100) return;
      lastFocusOpenAt = now;
      page = 1; more = true;
      term = inp.value.trim();
      fetchPage(term, page, true);
    });

    box.addEventListener("mousedown", (e) => {
      // mousedown (no click) para evitar perder foco antes de seleccionar
      const opt = e.target.closest(".ac-opt");
      if (!opt) return;
      e.preventDefault();
      selectItem(opt);
    });

    box.addEventListener("scroll", () => {
      if (box.scrollTop + box.clientHeight >= box.scrollHeight - 4 && more && !loading) {
        page += 1;
        fetchPage(term, page, false);
      }
    });

    inp.addEventListener("keydown", (e) => {
      const visible = box.style.display !== "none" && items().length > 0;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (!visible) { fetchPage(inp.value.trim(), 1, true); return; }
        setActive(activeIdx < 0 ? 0 : activeIdx + 1);
      } else if (e.key === "ArrowUp") {
        if (!visible) return;
        e.preventDefault();
        setActive(activeIdx <= 0 ? items().length - 1 : activeIdx - 1);
      } else if (e.key === "Enter") {
        if (visible) {
          e.preventDefault();
          const list = items();
          const target = activeIdx >= 0 ? list[activeIdx] : list[0];
          if (target) selectItem(target);
        }
        // si no está visible, dejamos que el form maneje el Enter (submit)
      } else if (e.key === "Escape") {
        if (visible) { e.preventDefault(); box.style.display = "none"; setExpanded(false); }
      }
    });

    document.addEventListener("click", (e) => {
      if (!inp.contains(e.target) && !box.contains(e.target)) {
        box.style.display = "none";
        setExpanded(false);
      }
    });

    return { reset() { inp.value = ""; hid.value = ""; box.innerHTML = ""; box.style.display = "none"; setExpanded(false); } };
  }

  /* ============================================================
     ELEMENTS
     ============================================================ */
  const stepStart = $("#stepStart");
  const stepOpen  = $("#stepOpen");
  const stepClose = $("#stepClose");

  const formStart = $("#formStart");
  const formClose = $("#formClose");

  const ppInp  = $("#pp_ac"),     ppHid  = $("#pp_id"),     ppBox  = $("#pp_box");
  const cajInp = $("#cajero_ac"), cajHid = $("#cajero_id"), cajBox = $("#cajero_box");

  const passInp = $("#password");
  const baseInp = $("#base");
  const togglePass = $("#togglePass");

  const btnIniciar   = $("#btnIniciar");
  const btnIniCierre = $("#btnIniciarCierre");
  const btnCerrar    = $("#btnCerrar");

  const infoPP        = $("#infoPP");
  const infoCajero    = $("#infoCajero");
  const infoInicio    = $("#infoInicio");
  const infoBase      = $("#infoBase");
  const infoDuracion  = $("#infoDuracion");
  const infoPP2       = $("#infoPP2");
  const infoCajero2   = $("#infoCajero2");
  const infoBase2     = $("#infoBase2");
  const infoCierre    = $("#infoCierre");

  const estadoBadge  = $("#estadoBadge");
  const estadoBadge2 = $("#estadoBadge2");

  const efectivoEntregadoInp = $("#efectivo_entregado");
  const facturasPagadasInp = $("#facturas_pagadas");
  const mediosBody = $("#mediosBody");
  const mVentas = $("#mVentas");
  const closeCashStep = $("#closeCashStep");
  const closeMediaStep = $("#closeMediaStep");
  const closeDenomInputs = $("#closeDenomInputs");
  const closeCashTotal = $("#closeCashTotal");
  const btnCashNext = $("#btnCashNext");
  const btnBackCash = $("#btnBackCash");

  if (typeof PP_AC_URL !== "undefined" && ppInp && ppHid && ppBox)
    setupAutocomplete(ppInp, ppHid, ppBox, PP_AC_URL);
  if (typeof CAJERO_AC_URL !== "undefined" && cajInp && cajHid && cajBox)
    setupAutocomplete(cajInp, cajHid, cajBox, CAJERO_AC_URL);

  /* ============================================================
     STATE
     ============================================================ */
  let TURNO_ID = null;
  let BASE = 0;
  let MEDIOS = [];                 // [{metodo, label}]
  const CONTADOS = Object.create(null);
  let TURNO_INICIO_DT = null;      // Date

  let inflightAction = null;       // 'iniciar' | 'inicierre' | 'cerrar' | null
  let durationTimer = null;

  /* ============================================================
     PERSISTENCIA del contado (sobrevive a F5 mientras estés en CIERRE)
     ============================================================ */
  const lsKey = (turnoId) => `tc_contados_${turnoId}`;
  const retiroDenomsKey = (turnoId) => `tc_retiro_denoms_${turnoId}`;

  function intCount(v) {
    const n = Math.floor(num(v));
    return n > 0 ? n : 0;
  }

  function buildCloseDenomInputs() {
    if (!closeDenomInputs || closeDenomInputs.dataset.ready === "1") return;
    closeDenomInputs.innerHTML = DENOMS.map((d) => `
      <div class="tc-denom-item">
        <label for="close_${escapeHtml(d.key)}">
          <span>${escapeHtml(d.label)}</span>
          <strong>${money2(d.value)}</strong>
        </label>
        <input id="close_${escapeHtml(d.key)}"
               class="no-spin"
               type="number"
               min="0"
               step="1"
               inputmode="numeric"
               data-close-denom="${escapeHtml(d.key)}"
               value="0">
      </div>
    `).join("");
    closeDenomInputs.dataset.ready = "1";
  }

  function readCloseDenomCounts() {
    const counts = {};
    DENOMS.forEach((d) => {
      counts[d.key] = intCount(closeDenomInputs?.querySelector(`[data-close-denom='${d.key}']`)?.value || 0);
    });
    return counts;
  }

  function setCloseDenomCounts(counts) {
    if (!counts || typeof counts !== "object") return;
    buildCloseDenomInputs();
    DENOMS.forEach((d) => {
      const input = closeDenomInputs?.querySelector(`[data-close-denom='${d.key}']`);
      if (input) input.value = String(intCount(counts[d.key] || 0));
    });
    refreshCloseCashTotal();
  }

  function closeCashTotalValue(counts = readCloseDenomCounts()) {
    return DENOMS.reduce((acc, d) => acc + (counts[d.key] || 0) * d.value, 0);
  }

  function refreshCloseCashTotal() {
    const counts = readCloseDenomCounts();
    const total = closeCashTotalValue(counts);
    if (closeCashTotal) closeCashTotal.textContent = money2(total);
    if (efectivoEntregadoInp) efectivoEntregadoInp.value = total.toFixed(2);
    scheduleRecalc();
    return total;
  }

  function showCloseSubstep(which) {
    if (closeCashStep) closeCashStep.style.display = which === "cash" ? "block" : "none";
    if (closeMediaStep) closeMediaStep.style.display = which === "media" ? "block" : "none";
  }

  function persistRetiroDenoms(turnoId) {
    if (!turnoId) return;
    try {
      const counts = readCloseDenomCounts();
      sessionStorage.setItem(retiroDenomsKey(turnoId), JSON.stringify({
        counts,
        total: closeCashTotalValue(counts),
        ts: Date.now(),
      }));
    } catch {}
  }

  function persistContados() {
    if (!TURNO_ID) return;
    try {
      const payload = {
        efectivo_entregado: efectivoEntregadoInp?.value || "",
        facturas_pagadas: facturasPagadasInp?.value || "",
        denominaciones: readCloseDenomCounts(),
        contados: { ...CONTADOS },
        ts: Date.now(),
      };
      localStorage.setItem(lsKey(TURNO_ID), JSON.stringify(payload));
    } catch {}
  }

  function restoreContados() {
    if (!TURNO_ID) return;
    try {
      const raw = localStorage.getItem(lsKey(TURNO_ID));
      if (!raw) return;
      const payload = JSON.parse(raw);
      if (!payload || typeof payload !== "object") return;
      // descartar caches de más de 24h
      if (Date.now() - (payload.ts || 0) > 24 * 3600 * 1000) {
        localStorage.removeItem(lsKey(TURNO_ID));
        return;
      }
      if (efectivoEntregadoInp && payload.efectivo_entregado) {
        efectivoEntregadoInp.value = payload.efectivo_entregado;
      }
      if (facturasPagadasInp && payload.facturas_pagadas) {
        facturasPagadasInp.value = payload.facturas_pagadas;
      }
      if (payload.denominaciones && typeof payload.denominaciones === "object") {
        setCloseDenomCounts(payload.denominaciones);
      }
      if (payload.contados && typeof payload.contados === "object") {
        for (const [k, v] of Object.entries(payload.contados)) {
          CONTADOS[k] = num(v);
          const inp = mediosBody?.querySelector(`[data-in='${k}']`);
          if (inp) inp.value = (Number(v) || 0).toFixed(2);
        }
      }
    } catch {}
  }

  function clearContados(turnoId) {
    try { localStorage.removeItem(lsKey(turnoId)); } catch {}
  }

  /* ============================================================
     UI helpers
     ============================================================ */
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

  function setBtnLoading(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-loading", !!on);
    btn.disabled = !!on;
  }

  function startDurationClock() {
    stopDurationClock();
    if (!infoDuracion || !TURNO_INICIO_DT) return;
    const tick = () => {
      if (!TURNO_INICIO_DT) return;
      infoDuracion.textContent = fmtDuration(Date.now() - TURNO_INICIO_DT.getTime());
    };
    tick();
    durationTimer = setInterval(tick, 1000);
  }

  function stopDurationClock() {
    if (durationTimer) clearInterval(durationTimer);
    durationTimer = null;
  }

  /* ============================================================
     Validación visual del form de inicio
     ============================================================ */
  function refreshStartValidity() {
    const okPP   = !!(ppHid?.value || "").trim();
    const okCaj  = !!(cajHid?.value || "").trim();
    const okPass = !!(passInp?.value || "").length;

    [["pp_id", okPP], ["cajero_id", okCaj]].forEach(([t, isOk]) => {
      const ck = $(`.tc-check[data-target='${t}']`);
      if (ck) ck.classList.toggle("on", isOk);
    });

    if (btnIniciar) btnIniciar.disabled = !(okPP && okCaj && okPass) || inflightAction === "iniciar";
  }

  ppInp?.addEventListener("ac:selected", refreshStartValidity);
  ppInp?.addEventListener("ac:cleared",  refreshStartValidity);
  cajInp?.addEventListener("ac:selected", refreshStartValidity);
  cajInp?.addEventListener("ac:cleared",  refreshStartValidity);
  passInp?.addEventListener("input", refreshStartValidity);

  // toggle password
  togglePass?.addEventListener("click", () => {
    if (!passInp) return;
    const showing = passInp.getAttribute("type") === "text";
    passInp.setAttribute("type", showing ? "password" : "text");
    togglePass.querySelector("i")?.classList.toggle("fa-eye", showing);
    togglePass.querySelector("i")?.classList.toggle("fa-eye-slash", !showing);
    passInp.focus();
  });

  /* ============================================================
     RECALC del cierre
     ============================================================ */
  let raf = null;
  function scheduleRecalc() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; recalc(); });
  }

  function recalc() {
    if (!efectivoEntregadoInp) return;
    const efectivoEntregado = num(efectivoEntregadoInp.value);
    const efectivoContado = Math.max(0, efectivoEntregado - BASE);
    const facturasPagadas = Math.max(0, num(facturasPagadasInp?.value || 0));
    const efectivoParaCuadre = efectivoContado + facturasPagadas;

    let sumContado = 0;
    CONTADOS["efectivo"] = efectivoParaCuadre;
    CONTADOS["facturas_pagadas"] = facturasPagadas;

    for (const m of MEDIOS) {
      const metodo = m.metodo;
      const contado = metodo === "efectivo" ? efectivoParaCuadre : num(CONTADOS[metodo] || 0);
      sumContado += contado;

      if (metodo === "efectivo") {
        const contadoEl = document.querySelector(`[data-contado='${metodo}']`);
        if (contadoEl) contadoEl.textContent = money2(contado);
      }
    }

    if (mVentas) mVentas.textContent = money2(sumContado);
    persistContados();
  }

  function buildTable() {
    if (!mediosBody) return;
    mediosBody.innerHTML = "";
    CONTADOS["efectivo"] = 0;

    const frag = document.createDocumentFragment();
    MEDIOS.forEach((m) => {
      const tr = document.createElement("tr");
      const tdM = document.createElement("td");
      tdM.innerHTML = `<span class="chip">${escapeHtml(m.label)}</span>`;
      tr.appendChild(tdM);

      const tdC = document.createElement("td");
      tdC.className = "num";

      if (m.metodo === "efectivo") {
        tdC.innerHTML = `
          <span class="readonly" data-contado="${escapeHtml(m.metodo)}">${money2(0)}</span>
          <div class="hint">Efectivo contado + facturas pagadas</div>
        `;
      } else {
        tdC.innerHTML = `
          <input class="in-num no-spin" type="number" step="0.01" min="0"
                 inputmode="decimal" data-in="${escapeHtml(m.metodo)}" placeholder="0.00">
        `;
      }
      tr.appendChild(tdC);
      frag.appendChild(tr);
    });

    mediosBody.appendChild(frag);

    mediosBody.querySelectorAll("[data-in]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const metodo = e.target.getAttribute("data-in");
        let v = num(e.target.value);
        if (v < 0) { v = 0; e.target.value = "0"; }
        CONTADOS[metodo] = v;
        scheduleRecalc();
      });
      inp.addEventListener("blur", (e) => {
        const v = num(e.target.value);
        if (e.target.value !== "" && Number.isFinite(v)) e.target.value = v.toFixed(2);
      });
    });

    if (efectivoEntregadoInp) {
      efectivoEntregadoInp.oninput = () => {
        let v = num(efectivoEntregadoInp.value);
        if (v < 0) { efectivoEntregadoInp.value = "0"; }
        scheduleRecalc();
      };
      efectivoEntregadoInp.onblur = () => {
        const v = num(efectivoEntregadoInp.value);
        if (efectivoEntregadoInp.value !== "" && Number.isFinite(v)) {
          efectivoEntregadoInp.value = v.toFixed(2);
        }
      };
    }

    if (facturasPagadasInp) {
      facturasPagadasInp.oninput = () => {
        let v = num(facturasPagadasInp.value);
        if (v < 0) { facturasPagadasInp.value = "0"; }
        scheduleRecalc();
      };
      facturasPagadasInp.onblur = () => {
        const v = num(facturasPagadasInp.value);
        if (facturasPagadasInp.value !== "" && Number.isFinite(v)) {
          facturasPagadasInp.value = v.toFixed(2);
        }
      };
    }

    scheduleRecalc();
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /* ============================================================
     Hidratar UI desde respuesta backend
     ============================================================ */
  function hydrateTurno(data) {
    TURNO_ID = data.turno_id || data.turno?.id || null;

    const baseFrom =
      data.base ??
      data.saldo_apertura_efectivo ??
      data.turno?.saldo_apertura_efectivo ??
      0;
    BASE = Number(baseFrom ?? 0);

    const ppObj  = data.puntopago ?? data.turno?.puntopago;
    const cajObj = data.cajero    ?? data.turno?.cajero;

    const ppName  = ppObj?.nombre || ppInp?.value || data.turno?.puntopago || "—";
    const cajName = cajObj?.nombreusuario || cajInp?.value || data.turno?.cajero || "—";

    if (infoPP)      infoPP.textContent      = ppName;
    if (infoCajero)  infoCajero.textContent  = cajName;
    if (infoBase)    infoBase.textContent    = money2(BASE);
    if (infoPP2)     infoPP2.textContent     = ppName;
    if (infoCajero2) infoCajero2.textContent = cajName;
    if (infoBase2)   infoBase2.textContent   = money2(BASE);

    const inicioRaw = data.inicio || data.turno?.inicio || null;
    TURNO_INICIO_DT = safeFromIso(inicioRaw);
    if (infoInicio) infoInicio.textContent = inicioRaw ? fmtDateTime(inicioRaw) : "—";

    const cierreRaw = data.cierre_iniciado || data.turno?.cierre_iniciado || null;
    if (infoCierre) infoCierre.textContent = cierreRaw ? fmtDateTime(cierreRaw) : "—";

    const estado = data.estado || data.turno?.estado || "ABIERTO";

    if (estado === "ABIERTO") {
      setBadge(estadoBadge, "ABIERTO");
      startDurationClock();
      showSection("open");
      // si quedó algo persistido de una sesión previa de cierre del MISMO turno, no lo cargamos aquí
      return;
    }

    if (estado === "CIERRE") {
      setBadge(estadoBadge2, "CIERRE");
      stopDurationClock();

      MEDIOS = (data.medios || []).map((x) => ({
        metodo: (x.metodo || "").toLowerCase().trim(),
        contado: x.contado ?? null,
        label:
          x.label ||
          (x.metodo || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      }));

      // limpiar valores anteriores en memoria (no en localStorage — se restaura abajo)
      if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";
      if (facturasPagadasInp) facturasPagadasInp.value = "";
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

      buildCloseDenomInputs();
      showCloseSubstep("cash");
      buildTable();
      MEDIOS.forEach((m) => {
        if (m.metodo === "efectivo" || m.contado === null || typeof m.contado === "undefined") return;
        CONTADOS[m.metodo] = num(m.contado);
        const inp = mediosBody?.querySelector(`[data-in='${m.metodo}']`);
        if (inp) inp.value = num(m.contado).toFixed(2);
      });
      restoreContados();   // ✅ recupera lo que el cajero ya había contado
      refreshCloseCashTotal();
      scheduleRecalc();
      showSection("close");
      return;
    }

    // CERRADO u otros: volvemos al inicio
    showSection("start");
    stopDurationClock();
  }

  /* ============================================================
     Actions (con doble-submit guard real)
     ============================================================ */
  async function actionIniciar(ev) {
    if (ev) ev.preventDefault();
    if (inflightAction) return;

    const pp_id = (ppHid?.value || "").trim();
    const cajero_id = (cajHid?.value || "").trim();
    const password = passInp?.value || "";
    const base = baseInp?.value || "0";

    if (!pp_id)     { warn("Selecciona un punto de pago.");  ppInp?.focus(); return; }
    if (!cajero_id) { warn("Selecciona un cajero.");          cajInp?.focus(); return; }
    if (!password)  { warn("Ingresa la contraseña.");         passInp?.focus(); return; }

    inflightAction = "iniciar";
    setBtnLoading(btnIniciar, true);
    refreshStartValidity();

    try {
      const data = await postForm(API_RECUPERAR, {
        action: "recuperar_o_iniciar",
        puntopago_id: pp_id,
        usuario_id: cajero_id,
        cajero_id: cajero_id,
        password,
        saldo_apertura_efectivo: base,
      });

      if (!data.success) { err(data.error || "Error al iniciar/recuperar el turno."); return; }

      ok(data.msg || "Turno iniciado/recuperado.");
      hydrateTurno(data);

      // limpiar password por seguridad
      if (passInp) passInp.value = "";
    } finally {
      inflightAction = null;
      setBtnLoading(btnIniciar, false);
      refreshStartValidity();
    }
  }

  async function actionIniciarCierre() {
    if (inflightAction) return;
    if (!TURNO_ID) { warn("No hay turno activo."); return; }

    const goAhead = await confirmModal({
      title: "Iniciar cierre",
      msg: "El turno pasará a estado CIERRE. Vas a poder ingresar lo contado por cada medio. ¿Continuar?",
      okText: "Sí, iniciar cierre",
      danger: false,
    });
    if (!goAhead) return;

    inflightAction = "inicierre";
    setBtnLoading(btnIniCierre, true);

    try {
      const data = await postForm(API_INI_CIERRE, { turno_id: TURNO_ID });
      if (!data.success) { err(data.error || "Error al iniciar el cierre."); return; }

      ok("Cierre iniciado.");
      hydrateTurno({
        ...data,
        turno_id: data.turno_id || TURNO_ID,
        estado: data.estado || "CIERRE",
        base: data.base ?? BASE,
        // si la respuesta no trae los nombres, los conservamos visualmente
        puntopago: data.puntopago ?? { nombre: infoPP?.textContent || "—" },
        cajero:    data.cajero    ?? { nombreusuario: infoCajero?.textContent || "—" },
      });
    } finally {
      inflightAction = null;
      setBtnLoading(btnIniCierre, false);
    }
  }

  async function actionCerrar() {
    if (inflightAction) return;
    if (!TURNO_ID) { warn("No hay turno en cierre."); return; }

    const efectivoEntregado = refreshCloseCashTotal();
    if (efectivoEntregado < 0) { err("El efectivo entregado no puede ser negativo."); return; }
    const facturasPagadas = num(facturasPagadasInp?.value);
    if (facturasPagadas < 0) { err("Facturas pagadas no puede ser negativo."); return; }

    const goAhead = await confirmModal({
      title: "Cerrar turno",
      msg: `Vas a cerrar el turno con efectivo contado de ${money2(efectivoEntregado)} y facturas pagadas por ${money2(facturasPagadas)}. Esta accion no se puede deshacer.`,
      okText: "Si, continuar",
      danger: true,
    });
    if (!goAhead) return;

    const mediosOut = [];
    MEDIOS.forEach((m) => {
      if (m.metodo === "efectivo") return;
      mediosOut.push({ metodo: m.metodo, contado: num(CONTADOS[m.metodo] || 0) });
    });

    inflightAction = "cerrar";
    setBtnLoading(btnCerrar, true);

    try {
      const data = await postForm(API_CERRAR, {
        turno_id: TURNO_ID,
        efectivo_entregado: String(efectivoEntregado),
        facturas_pagadas: String(facturasPagadas),
        medios_json: JSON.stringify(mediosOut),
      });

      if (!data.success) { err(data.error || "Error al cerrar el turno."); return; }

      if (data.retiro_url) {
        const oldId = data.turno_id || TURNO_ID;
        const deuda = Math.abs(Number(data.deuda_total ?? 0));
        persistRetiroDenoms(oldId);
        clearContados(oldId);
        if (deuda > 0) warn(`Deuda del turno: ${money2(deuda)}`, 1200);
        else ok("Deuda del turno: $0", 1200);
        setTimeout(() => window.location.assign(data.retiro_url), 900);
        return;
      }

      ok(data.msg || "Turno cerrado.");

      // mostrar resumen no-bloqueante
      const ventas = Number(data.ventas_total ?? 0);
      const deuda  = Number(data.deuda_total ?? 0);
      const facturas = Number(data.facturas_pagadas ?? 0);
      const filas = [
        ["Ventas (según usuario)", money2(ventas)],
        ["Facturas pagadas", money2(facturas)],
        ["Faltante", money2(Math.abs(deuda))],
      ].map(([k, v]) => `<div class="sum-row"><span>${k}</span><b>${v}</b></div>`).join("");
      summaryModal(`<div class="sum-grid">${filas}</div>`);

      // limpiar persistencia
      const oldId = TURNO_ID;
      clearContados(oldId);

      // reset UI
      TURNO_ID = null;
      BASE = 0;
      MEDIOS = [];
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];
      stopDurationClock();
      TURNO_INICIO_DT = null;

      showSection("start");

      if (ppInp)  ppInp.value  = "";
      if (ppHid)  ppHid.value  = "";
      if (cajInp) cajInp.value = "";
      if (cajHid) cajHid.value = "";
      if (passInp) passInp.value = "";
      if (baseInp) baseInp.value = "";
      if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";
      if (facturasPagadasInp) facturasPagadasInp.value = "";
      if (mediosBody) mediosBody.innerHTML = "";
      if (mVentas) mVentas.textContent = "—";
      if (infoDuracion) infoDuracion.textContent = "—";

      refreshStartValidity();
      ppInp?.focus();
    } finally {
      inflightAction = null;
      setBtnLoading(btnCerrar, false);
    }
  }

  /* ============================================================
     Wiring
     ============================================================ */
  closeDenomInputs?.addEventListener("input", (event) => {
    const target = event.target;
    if (!target || !target.matches("input[data-close-denom]")) return;
    if (num(target.value) < 0) target.value = "0";
    refreshCloseCashTotal();
    persistContados();
  });

  closeDenomInputs?.addEventListener("blur", (event) => {
    const target = event.target;
    if (!target || !target.matches("input[data-close-denom]")) return;
    target.value = String(intCount(target.value));
    refreshCloseCashTotal();
    persistContados();
  }, true);

  btnCashNext?.addEventListener("click", () => {
    refreshCloseCashTotal();
    showCloseSubstep("media");
    facturasPagadasInp?.focus();
  });

  btnBackCash?.addEventListener("click", () => {
    showCloseSubstep("cash");
    closeDenomInputs?.querySelector("input[data-close-denom]")?.focus();
  });

  formStart?.addEventListener("submit", actionIniciar);
  btnIniCierre?.addEventListener("click", actionIniciarCierre);
  btnCerrar?.addEventListener("click", actionCerrar);

  /* ============================================================
     Init
     ============================================================ */
  showSection("start");
  buildCloseDenomInputs();
  showCloseSubstep("cash");
  refreshCloseCashTotal();
  refreshStartValidity();

  // auto-foco al primer campo
  setTimeout(() => ppInp?.focus(), 50);

  // limpia contados huérfanos en localStorage (>24h) — pequeño housekeeping
  try {
    const now = Date.now();
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith("tc_contados_")) continue;
      try {
        const p = JSON.parse(localStorage.getItem(k) || "{}");
        if (!p?.ts || now - p.ts > 24 * 3600 * 1000) localStorage.removeItem(k);
      } catch { localStorage.removeItem(k); }
    }
  } catch {}
})();
