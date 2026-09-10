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
    { key: "pack_monedas", value: 10000, label: "Paquete monedas" },
    { key: "pack_m50", value: 2000, label: "Paquete monedas de 50" },
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
  if (TOASTS) document.body.appendChild(TOASTS);
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
  function openFloatingModal(el, focusSelector) {
    const previousFocus = document.activeElement;
    // Fuera de page-wrap: sus animaciones no deben desplazar el modal.
    document.body.appendChild(el);
    const background = Array.from(document.body.children)
      .filter(node => !node.matches(".tc-modal, .tc-toasts, script, style, link"))
      .map(node => ({node, inert: node.inert}));
    background.forEach(({node}) => { node.inert = true; });
    document.body.classList.add("tc-modal-open");
    el.hidden = false;
    const trapFocus = event => {
      if (event.key !== "Tab") return;
      const buttons = Array.from(el.querySelectorAll("button:not(:disabled), [href], input:not(:disabled), [tabindex='0']"))
        .filter(node => !node.hidden && node.getClientRects().length);
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (!first) { event.preventDefault(); el.querySelector(".tc-modal-card")?.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !el.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", trapFocus, true);
    requestAnimationFrame(() => {
      if (!el.hidden) el.querySelector(focusSelector)?.focus({preventScroll: true});
    });
    return () => {
      el.hidden = true;
      document.removeEventListener("keydown", trapFocus, true);
      document.body.classList.remove("tc-modal-open");
      background.forEach(({node, inert}) => { node.inert = inert; });
      if (previousFocus?.isConnected && !previousFocus.closest("[hidden], [inert]")) {
        previousFocus.focus({preventScroll: true});
      }
    };
  }

  function confirmModal({ title, msg, details = [], note = "", okText = "Confirmar", cancelText = "Cancelar", danger = false }) {
    return new Promise((resolve) => {
      const el = document.getElementById("tc-confirm");
      if (!el) { resolve(window.confirm(`${title}\n\n${msg}`)); return; }

      $("#tc-confirm-title").textContent = title || "¿Confirmar?";
      $("#tc-confirm-msg").textContent   = msg   || "";
      const detailList = $("#tc-confirm-details");
      detailList.replaceChildren();
      details.forEach(([label, value]) => {
        const row = document.createElement("div");
        const term = document.createElement("dt"), amount = document.createElement("dd");
        term.textContent = label; amount.textContent = value;
        row.append(term, amount); detailList.appendChild(row);
      });
      detailList.hidden = !details.length;
      const noteEl = $("#tc-confirm-note");
      noteEl.textContent = note; noteEl.hidden = !note;
      el.dataset.tone = danger ? "warning" : "info";

      const btnOk = $("#tc-confirm-ok");
      btnOk.textContent = okText;
      btnOk.className = "btn " + (danger ? "btn-danger" : "btn-primary");

      const ghost = el.querySelector(".btn-ghost");
      if (ghost) ghost.textContent = cancelText;

      const release = openFloatingModal(el, ".btn-ghost");
      let settled = false;

      const cleanup = () => {
        release();
        btnOk.removeEventListener("click", onOk);
        el.querySelectorAll("[data-close]").forEach((b) => b.removeEventListener("click", onCancel));
        document.removeEventListener("keydown", onKey, true);
      };
      const finish = result => {
        if (settled) return;
        settled = true;
        cleanup(); resolve(result);
      };
      const onOk = () => finish(true);
      const onCancel = () => finish(false);
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      };

      btnOk.addEventListener("click", onOk);
      el.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", onCancel));
      document.addEventListener("keydown", onKey, true);

    });
  }

  function summaryModal(html, onClose) {
    const el = document.getElementById("tc-summary");
    if (!el) { onClose?.(); return; }
    $("#tc-summary-body").innerHTML = html;
    const release = openFloatingModal(el, ".tc-modal-actions button");
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      release();
      document.removeEventListener("keydown", onKey, true);
      el.querySelectorAll("[data-close]").forEach(b => b.removeEventListener("click", close));
      onClose?.();
    };
    el.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", close)
    );
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
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

  const defaultCajero = {
    id: (cajHid?.value || "").trim(),
    text: (cajInp?.value || "").trim(),
  };

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
  const closePaymentsStep = $("#closePaymentsStep");
  const closeCashStep = $("#closeCashStep");
  const closeMediaStep = $("#closeMediaStep");
  const closeDenomInputs = $("#closeDenomInputs");
  const closeCashTotal = $("#closeCashTotal");
  const btnCashNext = $("#btnCashNext");
  const btnPaymentsNext = $("#btnPaymentsNext");

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
  const CLOSE_STEPS = ["payments", "cash", "media"];
  let closeProgress = { step: "payments", invoices: null, denominations: null };
  let closeStepAdvancing = false;
  const closePages = window.TURNO_CIERRE_PAGINAS || {};
  let closePageLeaving = false;
  let closeDraftCounts = {};
  let closeDraftPTM = "0";

  function navigateClosePage(which) {
    if (!TURNO_ID || !closePages[which] || closePages.page === which) return false;
    closePageLeaving = true;
    window.location.replace(closePages[which].replace("/0/", `/${TURNO_ID}/`));
    return true;
  }

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
    if (closeProgress.step === "media" && closeProgress.denominations) return { ...closeProgress.denominations };
    if (!closeDenomInputs) return { ...closeDraftCounts };
    const counts = {};
    DENOMS.forEach((d) => {
      counts[d.key] = intCount(closeDenomInputs?.querySelector(`[data-close-denom='${d.key}']`)?.value || 0);
    });
    return counts;
  }

  function setCloseDenomCounts(counts) {
    if (!counts || typeof counts !== "object") return;
    closeDraftCounts = { ...counts };
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

  function showCloseSubstep(which, focus = false) {
    if (!CLOSE_STEPS.includes(which)) which = "payments";
    if (CLOSE_STEPS.indexOf(which) < CLOSE_STEPS.indexOf(closeProgress.step)) which = closeProgress.step;
    if (facturasPagadasInp) {
      facturasPagadasInp.readOnly = closeProgress.step !== "payments";
      if (facturasPagadasInp.readOnly) facturasPagadasInp.value = String(closeProgress.invoices);
    }
    closeDenomInputs?.querySelectorAll("[data-close-denom]").forEach((input) => {
      input.readOnly = closeProgress.step === "media";
      if (input.readOnly) input.value = String(closeProgress.denominations[input.dataset.closeDenom] || 0);
    });
    if (closePaymentsStep) closePaymentsStep.style.display = which === "payments" ? "block" : "none";
    if (closeCashStep) closeCashStep.style.display = which === "cash" ? "block" : "none";
    if (closeMediaStep) closeMediaStep.style.display = which === "media" ? "block" : "none";
    for (const [name, panel] of [["payments", closePaymentsStep], ["cash", closeCashStep], ["media", closeMediaStep]]) {
      if (panel) panel.hidden = name !== which;
    }
    document.querySelectorAll("[data-close-step]").forEach((item) => {
      item.classList.toggle("is-complete", CLOSE_STEPS.indexOf(item.dataset.closeStep) < CLOSE_STEPS.indexOf(which));
      if (item.dataset.closeStep === which) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    if (focus) {
      const target = which === "payments" ? facturasPagadasInp
        : which === "cash" ? $("#closeCashWarningTitle") : $("#ptm_transacciones");
      target?.focus();
    }
  }

  function validatePaidInvoices() {
    if (closeProgress.step !== "payments") {
      if (facturasPagadasInp) facturasPagadasInp.value = String(closeProgress.invoices);
      return true;
    }
    const raw = (facturasPagadasInp?.value || "").trim();
    const value = raw ? Number(raw) : 0;
    if (!Number.isFinite(value) || value < 0 || facturasPagadasInp?.validity?.valid === false) {
      showCloseSubstep("payments", true);
      err("Escribe un valor válido para facturas y pagos a compañeros: 0 o mayor, con máximo dos decimales.");
      return false;
    }
    if (facturasPagadasInp && !raw) facturasPagadasInp.value = "0";
    return true;
  }

  function normalizedCloseProgress(value) {
    if (!value || !["cash", "media"].includes(value.step)) return null;
    const invoices = Number(value.invoices);
    if (value.invoices === null || value.invoices === "" || !Number.isFinite(invoices) || invoices < 0) return null;
    const progress = {step: value.step, invoices, denominations: null};
    if (value.step === "media") {
      if (!value.denominations || typeof value.denominations !== "object") return null;
      progress.denominations = {};
      for (const d of DENOMS) {
        const count = Number(value.denominations[d.key]);
        if (!Number.isSafeInteger(count) || count < 0) return null;
        progress.denominations[d.key] = count;
      }
    }
    return progress;
  }

  function adoptStoredCloseProgress() {
    if (!TURNO_ID) return false;
    try {
      const saved = normalizedCloseProgress(JSON.parse(localStorage.getItem(lsKey(TURNO_ID)) || "null")?.progress);
      if (saved && CLOSE_STEPS.indexOf(saved.step) >= CLOSE_STEPS.indexOf(closeProgress.step)) {
        if (JSON.stringify(saved) === JSON.stringify(closeProgress)) return false;
        closeProgress = saved;
        if (facturasPagadasInp) facturasPagadasInp.value = String(saved.invoices);
        if (saved.denominations) setCloseDenomCounts(saved.denominations);
        return true;
      }
    } catch {}
    return false;
  }

  async function advanceCloseStep(from, to) {
    if (closeStepAdvancing || inflightAction || !TURNO_ID) return;
    adoptStoredCloseProgress();
    if (closeProgress.step !== from) {
      if (!navigateClosePage(closeProgress.step)) showCloseSubstep(closeProgress.step, true);
      return;
    }
    if (from === "payments" && !validatePaidInvoices()) return;
    if (from === "cash") {
      const invalid = Array.from(closeDenomInputs?.querySelectorAll("[data-close-denom]") || []).find(input => !input.validity.valid);
      if (invalid) { err("Revisa el conteo: usa cantidades enteras de billetes y monedas, sin valores negativos."); invalid.focus(); return; }
    }
    const invoices = from === "payments" ? num(facturasPagadasInp?.value) : closeProgress.invoices;
    const denominations = from === "cash" ? readCloseDenomCounts() : null;
    closeStepAdvancing = true;
    try {
      const confirmed = await confirmModal({
        title: from === "payments" ? "Confirmar pagos de caja" : "Confirmar conteo de efectivo",
        msg: from === "payments"
          ? "Verifica el dinero pagado o apartado para facturas y compañeros."
          : "Verifica el efectivo que entregarás en caja, incluida la base.",
        details: [[from === "payments" ? "Pagos de caja" : "Efectivo contado", money2(from === "payments" ? invoices : closeCashTotalValue(denominations))]],
        note: "Después de confirmar no podrás volver atrás ni cambiar estos valores.",
        okText: "Confirmar y continuar",
      });
      if (!confirmed) return;
      const commit = () => {
        adoptStoredCloseProgress();
        if (closeProgress.step !== from) {
          if (!navigateClosePage(closeProgress.step)) showCloseSubstep(closeProgress.step, true);
          return;
        }
        const previous = closeProgress;
        closeProgress = {step: to, invoices, denominations};
        if (facturasPagadasInp) facturasPagadasInp.value = String(invoices);
        if (!persistContados()) {
          closeProgress = previous;
          err("No se pudo guardar el avance en este navegador. No se avanzó; revisa el almacenamiento antes de continuar.");
          return;
        }
        if (navigateClosePage(to)) return;
        showCloseSubstep(to, true);
        refreshCloseCashTotal();
        recalc();
      };
      if (navigator.locks?.request) await navigator.locks.request(`nova:turno-close:${TURNO_ID}`, commit);
      else commit();
    } finally { closeStepAdvancing = false; }
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
    if (!TURNO_ID || closePageLeaving) return false;
    try {
      if (adoptStoredCloseProgress()) showCloseSubstep(closeProgress.step);
      const payload = {
        efectivo_entregado: efectivoEntregadoInp?.value || "",
        facturas_pagadas: facturasPagadasInp?.value || "",
        ptm_transacciones: document.getElementById("ptm_transacciones")?.value.trim() || closeDraftPTM,
        denominaciones: readCloseDenomCounts(),
        contados: { ...CONTADOS },
        progress: closeProgress,
        ts: Date.now(),
      };
      localStorage.setItem(lsKey(TURNO_ID), JSON.stringify(payload));
      return true;
    } catch { return false; }
  }

  function restoreContados() {
    if (!TURNO_ID) return;
    try {
      const raw = localStorage.getItem(lsKey(TURNO_ID));
      if (!raw) return;
      const payload = JSON.parse(raw);
      if (!payload || typeof payload !== "object") return;
      // descartar caches de más de 24h
      const savedProgress = normalizedCloseProgress(payload.progress);
      if (!savedProgress && Date.now() - (payload.ts || 0) > 24 * 3600 * 1000) {
        localStorage.removeItem(lsKey(TURNO_ID));
        return;
      }
      if (efectivoEntregadoInp && payload.efectivo_entregado) {
        efectivoEntregadoInp.value = payload.efectivo_entregado;
      }
      if (facturasPagadasInp && payload.facturas_pagadas) {
        facturasPagadasInp.value = payload.facturas_pagadas;
      }
      if (document.getElementById("ptm_transacciones")) {
        document.getElementById("ptm_transacciones").value = String(payload.ptm_transacciones ?? "").trim() || "0";
      }
      closeDraftPTM = String(payload.ptm_transacciones ?? "").trim() || "0";
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
      if (savedProgress) {
        closeProgress = savedProgress;
        if (facturasPagadasInp) facturasPagadasInp.value = String(savedProgress.invoices);
        if (savedProgress.denominations) setCloseDenomCounts(savedProgress.denominations);
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

    // Debe seguir siendo clicable para poder mostrar los mensajes de
    // validación. Los gestores de contraseñas pueden rellenar el texto del
    // cajero sin actualizar el ID oculto del autocomplete.
    if (btnIniciar) btnIniciar.disabled = inflightAction === "iniciar";
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
    const efectivoContado = efectivoEntregado - BASE;
    const facturasPagadas = closeProgress.step === "payments"
      ? Math.max(0, num(facturasPagadasInp?.value || 0)) : closeProgress.invoices;
    const efectivoParaCuadre = efectivoContado + facturasPagadas;

    let sumContado = 0;
    CONTADOS["efectivo"] = efectivoParaCuadre;
    CONTADOS["facturas_pagadas"] = facturasPagadas;

    for (const m of MEDIOS) {
      const metodo = m.metodo;
      const autoConfirmado = metodo === "efectivo" ? 0 : num(m.auto_confirmado || 0);
      const reintegrado = metodo === "efectivo" ? 0 : num(m.reintegrado || 0);
      const contadoUsuario = metodo === "efectivo" ? efectivoParaCuadre : num(CONTADOS[metodo] || 0);
      const contado = contadoUsuario + autoConfirmado - reintegrado;
      sumContado += contado;

      if (metodo === "efectivo") {
        const contadoEl = document.querySelector(`[data-contado='${metodo}']`);
        if (contadoEl) contadoEl.textContent = money2(contado);
      } else {
        const reconocidoEl = document.querySelector(`[data-auto-total='${metodo}']`);
        if (reconocidoEl) reconocidoEl.textContent = `Reconocido en cierre: ${money2(contado)}`;
      }
    }

    if (mVentas) mVentas.textContent = money2(sumContado);
    persistContados();
  }

  function buildTable() {
    if (facturasPagadasInp && facturasPagadasInp.type !== "hidden") {
      facturasPagadasInp.oninput = () => { scheduleRecalc(); };
      facturasPagadasInp.onblur = () => {
        if (facturasPagadasInp.value !== "" && facturasPagadasInp.validity.valid) {
          facturasPagadasInp.value = num(facturasPagadasInp.value).toFixed(2);
        }
      };
    }
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
          <div class="hint">Efectivo contado sin base + pagos de caja del paso 1</div>
        `;
      } else {
        const autoConfirmado = num(m.auto_confirmado || 0);
        const reintegrado = num(m.reintegrado || 0);
        const manualCount = Math.max(0, Math.floor(num(m.manual_sin_api_count || 0)));
        const autoHint = autoConfirmado > 0
          ? `<div class="hint hint-ok">Confirmado por API: ${money2(autoConfirmado)}. Escribe solo lo no asociado.</div>
             <div class="hint" data-auto-total="${escapeHtml(m.metodo)}">Reconocido en cierre: ${money2(autoConfirmado)}</div>`
          : "";
        const manualHint = m.metodo === "nequi"
          ? `<div class="hint ${manualCount > 0 ? "hint-warn" : "hint-ok"}">Nequi sin API: ${manualCount} venta${manualCount === 1 ? "" : "s"} cerrada${manualCount === 1 ? "" : "s"} sin vincular a un pago por API.</div>`
          : "";
        const reintegroHint = reintegrado > 0
          ? `<div class="hint hint-warn">Devoluciones descontadas: -${money2(reintegrado)}</div>`
          : "";
        tdC.innerHTML = `
          <input class="in-num no-spin" type="number" step="0.01" min="0"
                 inputmode="decimal" data-in="${escapeHtml(m.metodo)}" placeholder="0.00">
          ${manualHint}
          ${autoHint}
          ${reintegroHint}
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
    closeProgress = { step: "payments", invoices: null, denominations: null };
    closeDraftCounts = {};
    closeDraftPTM = "0";
    const ptmCount = document.getElementById("ptm_transacciones");
    if (ptmCount) { ptmCount.value = "0"; ptmCount.oninput = persistContados; }
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
        auto_confirmado: num(x.auto_confirmado || 0),
        reintegrado: num(x.reintegrado || 0),
        manual_sin_api_count: Math.max(0, Math.floor(num(x.manual_sin_api_count || 0))),
        manual_sin_api_total: num(x.manual_sin_api_total || 0),
        label:
          x.label ||
          (x.metodo || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      }));

      // limpiar valores anteriores en memoria (no en localStorage — se restaura abajo)
      if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";
      if (facturasPagadasInp) facturasPagadasInp.value = "0";
      for (const k of Object.keys(CONTADOS)) delete CONTADOS[k];

      buildCloseDenomInputs();
      showCloseSubstep("payments");
      buildTable();
      MEDIOS.forEach((m) => {
        if (m.metodo === "efectivo" || m.contado === null || typeof m.contado === "undefined") return;
        CONTADOS[m.metodo] = Math.max(
          0,
          num(m.contado) - num(m.auto_confirmado || 0) + num(m.reintegrado || 0)
        );
        const inp = mediosBody?.querySelector(`[data-in='${m.metodo}']`);
        if (inp) inp.value = CONTADOS[m.metodo].toFixed(2);
      });
      restoreContados();   // ✅ recupera lo que el cajero ya había contado
      if (navigateClosePage(closeProgress.step)) return;
      refreshCloseCashTotal();
      scheduleRecalc();
      showSection("close");
      showCloseSubstep(closeProgress.step, true);
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
    const cajero_nombre = (cajInp?.value || "").trim();
    const password = passInp?.value || "";
    const base = baseInp?.value || "0";

    if (!pp_id)     { warn("Selecciona un punto de pago.");  ppInp?.focus(); return; }
    if (!cajero_id && !cajero_nombre) {
      warn("Selecciona un cajero.");
      cajInp?.focus();
      return;
    }
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
        cajero_nombre,
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
      msg: "Primero registrarás lo pagado en facturas o a compañeros; después contarás el efectivo y revisarás los otros medios. ¿Iniciar el cierre?",
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
    adoptStoredCloseProgress();
    if (closeProgress.step !== "media") {
      if (!navigateClosePage(closeProgress.step)) showCloseSubstep(closeProgress.step, true);
      return;
    }
    if (!validatePaidInvoices()) return;
    const ptmTransacciones = document.getElementById("ptm_transacciones")?.value.trim() || "0";
    if (!/^[0-9]{1,8}$/.test(ptmTransacciones)) { err("Escribe cuántas transacciones PTM hiciste (0 si no hubo)."); return; }

    const efectivoEntregado = refreshCloseCashTotal();
    if (efectivoEntregado < 0) { err("El efectivo entregado no puede ser negativo."); return; }
    const facturasPagadas = num(facturasPagadasInp?.value);
    if (facturasPagadas < 0) { err("Facturas pagadas no puede ser negativo."); return; }

    const nequiMedio = MEDIOS.find((m) => m.metodo === "nequi");
    const nequiAuto = num(nequiMedio?.auto_confirmado || 0);
    const nequiUsuario = num(CONTADOS.nequi || 0);
    const nequiMsg = nequiAuto > 0
      ? ` Nequi confirmado por API: ${money2(nequiAuto)}. Nequi digitado adicional: ${money2(nequiUsuario)}.`
      : "";

    const goAhead = await confirmModal({
      title: "Cerrar turno",
      msg: `Revisa los valores antes de guardar el cierre.${nequiMsg}`,
      details: [["Efectivo contado", money2(efectivoEntregado)], ["Facturas y compañeros", money2(facturasPagadas)], ["Transacciones PTM", ptmTransacciones]],
      note: "Al confirmar se cerrará el turno. Esta acción no se puede deshacer.",
      okText: "Confirmar cierre",
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
        ptm_transacciones: ptmTransacciones,
        medios_json: JSON.stringify(mediosOut),
      });

      if (!data.success) { err(data.error || "Error al cerrar el turno."); return; }

      if (data.retiro_url) {
        const oldId = data.turno_id || TURNO_ID;
        const deuda = Math.abs(Number(data.deuda_total ?? 0));
        persistRetiroDenoms(oldId);
        clearContados(oldId);
        summaryModal(
          `<div class="sum-row"><span>Deuda del turno</span><b>${money2(deuda)}</b></div>`,
          () => window.location.assign(data.retiro_url)
        );
        return;
      }

      ok(data.msg || "Turno cerrado.");

      // mostrar resumen no-bloqueante
      const ventas = Number(data.ventas_total ?? 0);
      const deuda  = Number(data.deuda_total ?? 0);
      const facturas = Number(data.facturas_pagadas ?? 0);
      const nequiApi = Number(data.auto_confirmados?.nequi ?? 0);
      const filas = [
        ["Ventas reconocidas", money2(ventas)],
        ["Pagos de caja: facturas y compañeros", money2(facturas)],
        ...(nequiApi > 0 ? [["Nequi confirmado por API", money2(nequiApi)]] : []),
        ["Faltante", money2(Math.abs(deuda))],
      ].map(([k, v]) => `<div class="sum-row"><span>${k}</span><b>${v}</b></div>`).join("");
      summaryModal(`<div class="sum-grid">${filas}</div>`, () => {
        if (closePages.page && closePages.home) window.location.replace(closePages.home);
      });

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
      if (cajInp) cajInp.value = defaultCajero.text;
      if (cajHid) cajHid.value = defaultCajero.id;
      if (passInp) passInp.value = "";
      if (baseInp) baseInp.value = "";
      if (efectivoEntregadoInp) efectivoEntregadoInp.value = "";
      if (facturasPagadasInp) facturasPagadasInp.value = "0";
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

  btnPaymentsNext?.addEventListener("click", () => { void advanceCloseStep("payments", "cash"); });
  btnCashNext?.addEventListener("click", () => { void advanceCloseStep("cash", "media"); });
  window.addEventListener("storage", event => {
    if (TURNO_ID && event.key === lsKey(TURNO_ID)) {
      adoptStoredCloseProgress();
      if (!navigateClosePage(closeProgress.step)) showCloseSubstep(closeProgress.step);
    }
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted && TURNO_ID) {
      closePageLeaving = false;
      adoptStoredCloseProgress();
      if (!navigateClosePage(closeProgress.step)) showCloseSubstep(closeProgress.step);
    }
  });

  formStart?.addEventListener("submit", actionIniciar);
  btnIniCierre?.addEventListener("click", actionIniciarCierre);
  btnCerrar?.addEventListener("click", actionCerrar);

  /* ============================================================
     Init
     ============================================================ */
  function readInitialTurno() {
    const el = document.getElementById("turno-activo-inicial");
    if (!el) return null;
    try {
      const data = JSON.parse(el.textContent || "null");
      return data && data.turno_id ? data : null;
    } catch {
      return null;
    }
  }

  buildCloseDenomInputs();
  showCloseSubstep("payments");
  refreshCloseCashTotal();
  refreshStartValidity();

  const initialTurno = readInitialTurno();
  if (initialTurno) {
    hydrateTurno(initialTurno);
    if (!closePages.page) ok(initialTurno.estado === "CIERRE" ? "Cierre pendiente recuperado." : "Turno abierto recuperado.");
  } else {
    showSection("start");
    // auto-foco al primer campo
    setTimeout(() => ppInp?.focus(), 50);
  }

  // limpia contados huérfanos en localStorage (>24h) — pequeño housekeeping
  try {
    const now = Date.now();
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith("tc_contados_")) continue;
      try {
        const p = JSON.parse(localStorage.getItem(k) || "{}");
        if (!normalizedCloseProgress(p?.progress) && (!p?.ts || now - p.ts > 24 * 3600 * 1000)) localStorage.removeItem(k);
      } catch { localStorage.removeItem(k); }
    }
  } catch {}
})();
