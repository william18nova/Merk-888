// static/javascript/turnos_caja_dashboard.js
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

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Los iconos de las acciones no dependen de que cargue un CDN externo.
  function icon(name) {
    const shapes = {
      edit: '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15l-1 5Z"/>',
      eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
      refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
      search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
      warning: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
    };
    return `<svg class="tcd-icon" viewBox="0 0 24 24" aria-hidden="true">${shapes[name] || shapes.warning}</svg>`;
  }

  function flash(ok, msg) {
    const box = $("#flash");
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = msg;
    box.hidden = false;
  }

  async function getJSON(url, signal) {
    const r = await fetch(url, { cache:"no-store", signal, headers: { "X-Requested-With": "XMLHttpRequest" } });
    const txt = await r.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) {}
    if (!r.ok || !data) throw new Error((data && data.error) || "No se pudo cargar la información. Revisa tu conexión o sesión.");
    return data;
  }

  // ====== UI ======
  const fEstado = $("#fEstado");
  const fQ = $("#fQ");
  const fFrom = $("#fFrom");
  const fTo = $("#fTo");
  const btnRefresh = $("#btnRefresh");
  const btnClear = $("#btnClear");
  const filterForm = $("#turnosFilters");
  const resultSummary = $("#resultSummary");
  const lastUpdated = $("#lastUpdated");

  const tbody = $("#tbodyTurnos");
  const btnPrev = $("#btnPrev");
  const btnNext = $("#btnNext");
  const pgInfo = $("#pgInfo");

  // modal
  const modal = $("#modal");
  const mClose = $("#mClose");
  const mSub = $("#mSub");
  const mEsperadoBD = $("#mEsperadoBD");
  const mEsperadoCalc = $("#mEsperadoCalc");
  const mReal = $("#mReal");
  const mDiff = $("#mDiff");
  const mDeuda = $("#mDeuda");
  const mBodyMedios = $("#mBodyMedios");
  const btnCalcExpected = $("#btnCalcExpected");
  const detailStatus = $("#detailStatus");

  // ====== State ======
  let PAGE = 1;
  const PAGE_SIZE = 25;
  let TOTAL = 0;
  let LAST_ITEMS = [];
  let MODAL_TURNO_ID = null;
  let listController = null, listRevision = 0;
  let detailController = null, detailRevision = 0;
  let modalReturnFocus = null, previousOverflow = "";

  function pill(estado) {
    const cls =
      estado === "ABIERTO" ? "pill pill-open" :
      estado === "CIERRE"  ? "pill pill-close" :
      "pill pill-done";
    const label = {ABIERTO:"ABIERTO", CIERRE:"EN CIERRE", CERRADO:"CERRADO"}[estado] || estado;
    return `<span class="${cls}">${escapeHtml(label)}</span>`;
  }

  function fmtDT(s) {
    if (!s) return "—";
    const match = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/);
    if (!match) return escapeHtml(s);
    const months = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
    return `<time class="tcd-datetime" datetime="${escapeHtml(String(s).replace(' ', 'T'))}"><span>${match[3]} ${months[Number(match[2])-1] || match[2]} ${match[1]}</span><small>${match[4]}</small></time>`;
  }

  function numClass(v) {
    const n = Number(v || 0);
    if (n < 0) return "neg";
    if (n > 0) return "pos";
    return "";
  }

  function buildRow(t) {
    const esperado = Number(t.esperado_total || 0);
    const real = Number(t.ventas_total || 0);
    const diff = Number(t.diferencia_total || 0);
    const deuda = Number(t.deuda_total || 0);
    const canEdit = typeof CAN_EDIT_TURNOS !== "undefined" && CAN_EDIT_TURNOS === true;
    const adminUrl = typeof TURNO_ADMIN_URL !== "undefined" ? TURNO_ADMIN_URL : "/turnos_caja_admin/";
    const editHref = `${adminUrl}?turno_id=${encodeURIComponent(t.id)}`;
    const id = escapeHtml(t.id);
    const editButton = canEdit
      ? `<a class="btn btn-ghost btn-sm btn-edit-turno" href="${escapeHtml(editHref)}" title="Editar turno #${id}" aria-label="Editar turno #${id}">
           ${icon("edit")}
         </a>`
      : "";

    return `
      <tr role="row">
        <td class="turno-id" role="cell">#${id}</td>
        <td class="turno-status" role="cell">${pill(t.estado)}</td>
        <td data-label="Punto de pago" role="cell">${escapeHtml(t.puntopago || "—")}</td>
        <td data-label="Cajero" role="cell">${escapeHtml(t.cajero || "—")}</td>
        <td data-label="Inicio" role="cell">${fmtDT(t.inicio)}</td>
        <td data-label="Fin" role="cell">${fmtDT(t.fin)}</td>
        <td data-label="Esperado" class="num" role="cell">${money2(esperado)}</td>
        <td data-label="Reportado" class="num" role="cell">${money2(real)}</td>
        <td data-label="Diferencia" class="num ${numClass(diff)}" role="cell">${money2(diff)}</td>
        <td data-label="Deuda" class="num ${numClass(deuda)}" role="cell">${money2(deuda)}</td>
        <td class="act">
          <div class="row-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-detail="${id}" title="Ver detalle" aria-label="Ver detalle del turno #${id}">
            ${icon("eye")}<span>Detalle</span>
          </button>
          ${editButton}
          </div>
        </td>
      </tr>
    `;
  }

  async function loadList() {
    if (fFrom.value && fTo.value && fFrom.value > fTo.value) {
      flash(false, "La fecha Desde no puede ser posterior a Hasta.");
      fTo.focus();
      return;
    }
    listController?.abort();
    listController = new AbortController();
    const revision = ++listRevision;
    $("#flash").hidden = true;
    $(".tcd-results").setAttribute("aria-busy", "true");
    btnRefresh.disabled = true;
    btnPrev.disabled = btnNext.disabled = true;
    lastUpdated.textContent = "Actualizando…";
    const qs = new URLSearchParams({
      estado: fEstado.value || "ALL",
      q: fQ.value || "",
      date_from: fFrom.value || "",
      date_to: fTo.value || "",
      page: String(PAGE),
      page_size: String(PAGE_SIZE),
    }).toString();

    tbody.innerHTML = stateRow("Cargando turnos…", "Un momento, estamos consultando la información.", "refresh");
    try {
      const data = await getJSON(`${API_LIST}?${qs}`, listController.signal);
      if (revision !== listRevision) return;
      if (!data.success) throw new Error(data.error || "Error");

      TOTAL = data.total || 0;
      LAST_ITEMS = Array.isArray(data.items) ? data.items : [];
      const totalPages = Math.max(1, Math.ceil(TOTAL / PAGE_SIZE));
      if (PAGE > totalPages) { PAGE = totalPages; return loadList(); }

      tbody.innerHTML = LAST_ITEMS.length
        ? LAST_ITEMS.map(buildRow).join("")
        : stateRow("No encontramos turnos", "Prueba otro nombre, cambia las fechas o limpia los filtros.", "search");

      pgInfo.textContent = `Página ${PAGE} de ${totalPages}`;
      resultSummary.textContent = TOTAL ? `Mostrando ${(PAGE-1)*PAGE_SIZE+1}–${(PAGE-1)*PAGE_SIZE+LAST_ITEMS.length} de ${TOTAL} turnos` : "0 turnos con los filtros actuales";
      lastUpdated.textContent = `Actualizado a las ${new Date().toLocaleTimeString("es-CO", {hour:"2-digit",minute:"2-digit"})}`;

      btnPrev.disabled = PAGE <= 1;
      btnNext.disabled = PAGE >= totalPages;
    } catch (e) {
      if (e.name === "AbortError" || revision !== listRevision) return;
      tbody.innerHTML = stateRow("No pudimos cargar los turnos", "Pulsa Actualizar para volver a intentarlo.", "warning");
      resultSummary.textContent = "Información no disponible";
      lastUpdated.textContent = "No se pudo actualizar";
      pgInfo.textContent = "—";
      flash(false, e.message || "Error");
    } finally {
      if (revision === listRevision) {
        btnRefresh.disabled = false;
        $(".tcd-results").setAttribute("aria-busy", "false");
      }
    }
  }

  function stateRow(title, detail, iconName) {
    return `<tr class="state-row"><td colspan="11" class="empty"><div class="tcd-state">${icon(iconName)}<strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></div></td></tr>`;
  }

  function openModal() {
    if (modal.style.display !== "flex") {
      modalReturnFocus = document.activeElement;
      previousOverflow = document.body.style.overflow;
    }
    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
    mClose.focus();
  }
  function closeModal() {
    detailRevision++;
    detailController?.abort();
    modal.style.display = "none";
    document.body.style.overflow = previousOverflow;
    MODAL_TURNO_ID = null;
    mBodyMedios.innerHTML = "";
    mEsperadoCalc.textContent = "—";
    if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  }

  function renderDetail(data) {
    const t = data.turno || {};
    MODAL_TURNO_ID = t.id;

    mSub.textContent =
      `#${t.id} — ${t.estado} — ${t.puntopago || "—"} — ${t.cajero || "—"} | ${t.inicio || "—"}`;

    mEsperadoBD.textContent = money2(t.esperado_total_bd || 0);
    mReal.textContent = money2(t.ventas_total || 0);
    mDiff.textContent = money2(t.diferencia_total || 0);
    mDeuda.textContent = money2(t.deuda_total || 0);

    mDiff.className = "val " + numClass(t.diferencia_total || 0);
    mDeuda.className = "val " + numClass(t.deuda_total || 0);

    // botón calcular live solo si NO está CERRADO (o si quieres igual dejarlo)
    btnCalcExpected.style.display = (t.estado === "ABIERTO" || t.estado === "CIERRE") ? "inline-flex" : "none";

    const medios = data.medios || [];
    mBodyMedios.innerHTML = medios.map((m) => {
      const diff = Number(m.diferencia || 0);
      const contado = m.contado === null || typeof m.contado === "undefined" ? null : Number(m.contado);
      const ec = (m.esperado_calc === null || typeof m.esperado_calc === "undefined") ? null : Number(m.esperado_calc);

      return `
        <tr role="row">
          <td><span class="chip">${escapeHtml(m.label || String(m.metodo || "").toUpperCase())}</span></td>
          <td data-label="Esperado registrado" class="num">${money2(m.esperado_bd || 0)}</td>
          <td data-label="Esperado calculado" class="num">${ec === null ? "—" : money2(ec)}</td>
          <td data-label="Contado" class="num">${contado === null ? "—" : money2(contado)}</td>
          <td data-label="Diferencia" class="num ${numClass(diff)}">${money2(diff)}</td>
        </tr>
      `;
    }).join("") || '<tr><td colspan="5">Sin medios de pago registrados.</td></tr>';
  }

  async function openDetail(turnoId, computeExpected = false) {
    detailController?.abort();
    detailController = new AbortController();
    const revision = ++detailRevision;
    if (!computeExpected) {
      MODAL_TURNO_ID = turnoId;
      mSub.textContent = `Turno #${turnoId}`;
      for (const node of [mEsperadoBD,mEsperadoCalc,mReal,mDiff,mDeuda]) node.textContent = "—";
      mBodyMedios.innerHTML = "";
      btnCalcExpected.style.display = "none";
      openModal();
    }
    detailStatus.hidden = false;
    detailStatus.textContent = computeExpected ? "Calculando el esperado actual…" : "Cargando detalle del turno…";
    btnCalcExpected.disabled = true;
    const url = computeExpected ? `${API_DETAIL(turnoId)}?compute_expected=1` : API_DETAIL(turnoId);
    try {
      const data = await getJSON(url, detailController.signal);
      if (revision !== detailRevision) return;
      if (!data.success) throw new Error(data.error || "Error");
      if (computeExpected && data.expected_calc) {
        mEsperadoCalc.textContent = money2(data.expected_calc.esperado_total_calc || 0);
      } else {
        mEsperadoCalc.textContent = "—";
      }
      renderDetail(data);
      detailStatus.hidden = true;
    } catch (e) {
      if (e.name === "AbortError" || revision !== detailRevision) return;
      detailStatus.textContent = e.message || "No se pudo cargar el detalle. Cierra y vuelve a intentarlo.";
    } finally {
      if (revision === detailRevision) btnCalcExpected.disabled = false;
    }
  }

  // ====== Events ======
  filterForm.addEventListener("submit", e => { e.preventDefault(); PAGE = 1; loadList(); });
  btnClear.addEventListener("click", () => { filterForm.reset(); PAGE = 1; loadList(); });
  fEstado.addEventListener("change", () => { PAGE = 1; loadList(); });

  btnPrev.addEventListener("click", () => { PAGE = Math.max(1, PAGE - 1); loadList(); });
  btnNext.addEventListener("click", () => { PAGE += 1; loadList(); });

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-detail]");
    if (!btn) return;
    openDetail(Number(btn.getAttribute("data-detail")));
  });

  mClose.addEventListener("click", closeModal);
  modal.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); closeModal(); return; }
    if (e.key !== "Tab") return;
    const focusable = Array.from(modal.querySelectorAll('button:not([disabled]),a[href],[tabindex="0"]')).filter(el => el.offsetParent !== null);
    const first = focusable[0], last = focusable[focusable.length-1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  btnCalcExpected.addEventListener("click", () => {
    if (!MODAL_TURNO_ID) return;
    openDetail(MODAL_TURNO_ID, true);
  });

  // init
  loadList();
})();
