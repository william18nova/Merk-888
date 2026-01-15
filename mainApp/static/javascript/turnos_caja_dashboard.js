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

  function flash(ok, msg) {
    const box = $("#flash");
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = msg;
    box.style.display = "block";
    setTimeout(() => (box.style.display = "none"), 3200);
  }

  async function getJSON(url) {
    const r = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
    const txt = await r.text();
    let data = null;
    try { data = JSON.parse(txt); } catch (e) {}
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  // ====== UI ======
  const fEstado = $("#fEstado");
  const fQ = $("#fQ");
  const fFrom = $("#fFrom");
  const fTo = $("#fTo");
  const btnRefresh = $("#btnRefresh");

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

  // ====== State ======
  let PAGE = 1;
  const PAGE_SIZE = 25;
  let TOTAL = 0;
  let LAST_ITEMS = [];
  let MODAL_TURNO_ID = null;

  function pill(estado) {
    const cls =
      estado === "ABIERTO" ? "pill pill-open" :
      estado === "CIERRE"  ? "pill pill-close" :
      "pill pill-done";
    return `<span class="${cls}">${estado}</span>`;
  }

  function fmtDT(s) {
    if (!s) return "—";
    return String(s);
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

    return `
      <tr>
        <td class="mono">#${t.id}</td>
        <td>${pill(t.estado)}</td>
        <td>${t.puntopago || "—"}</td>
        <td>${t.cajero || "—"}</td>
        <td class="mono">${fmtDT(t.inicio)}</td>
        <td class="mono">${fmtDT(t.fin)}</td>
        <td class="num">${money2(esperado)}</td>
        <td class="num">${money2(real)}</td>
        <td class="num ${numClass(diff)}">${money2(diff)}</td>
        <td class="num ${numClass(deuda)}">${money2(deuda)}</td>
        <td class="act">
          <button class="btn btn-ghost btn-sm" data-detail="${t.id}">
            <i class="fa-solid fa-eye"></i>
          </button>
        </td>
      </tr>
    `;
  }

  async function loadList() {
    const qs = new URLSearchParams({
      estado: fEstado.value || "ALL",
      q: fQ.value || "",
      date_from: fFrom.value || "",
      date_to: fTo.value || "",
      page: String(PAGE),
      page_size: String(PAGE_SIZE),
    }).toString();

    tbody.innerHTML = `<tr><td colspan="11" class="loading">Cargando…</td></tr>`;
    try {
      const data = await getJSON(`${API_LIST}?${qs}`);
      if (!data.success) throw new Error(data.error || "Error");

      TOTAL = data.total || 0;
      LAST_ITEMS = data.items || [];

      tbody.innerHTML = LAST_ITEMS.length
        ? LAST_ITEMS.map(buildRow).join("")
        : `<tr><td colspan="11" class="empty">No hay turnos con esos filtros.</td></tr>`;

      const totalPages = Math.max(1, Math.ceil(TOTAL / PAGE_SIZE));
      pgInfo.textContent = `Página ${PAGE} / ${totalPages} — ${TOTAL} turnos`;

      btnPrev.disabled = PAGE <= 1;
      btnNext.disabled = PAGE >= totalPages;
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="11" class="empty">Error cargando lista.</td></tr>`;
      flash(false, e.message || "Error");
    }
  }

  function openModal() {
    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
  function closeModal() {
    modal.style.display = "none";
    document.body.style.overflow = "";
    MODAL_TURNO_ID = null;
    mBodyMedios.innerHTML = "";
    mEsperadoCalc.textContent = "—";
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
        <tr>
          <td><span class="chip">${(m.metodo || "").toUpperCase()}</span></td>
          <td class="num">${money2(m.esperado_bd || 0)}</td>
          <td class="num">${ec === null ? "—" : money2(ec)}</td>
          <td class="num">${contado === null ? "—" : money2(contado)}</td>
          <td class="num ${numClass(diff)}">${money2(diff)}</td>
        </tr>
      `;
    }).join("");

    openModal();
  }

  async function openDetail(turnoId, computeExpected = false) {
    const url = computeExpected ? `${API_DETAIL(turnoId)}?compute_expected=1` : API_DETAIL(turnoId);
    try {
      const data = await getJSON(url);
      if (!data.success) throw new Error(data.error || "Error");
      if (computeExpected && data.expected_calc) {
        mEsperadoCalc.textContent = money2(data.expected_calc.esperado_total_calc || 0);
      } else {
        mEsperadoCalc.textContent = "—";
      }
      renderDetail(data);
    } catch (e) {
      flash(false, e.message || "Error cargando detalle");
    }
  }

  // ====== Events ======
  btnRefresh.addEventListener("click", () => { PAGE = 1; loadList(); });
  fEstado.addEventListener("change", () => { PAGE = 1; loadList(); });

  // enter en buscar
  fQ.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { PAGE = 1; loadList(); }
  });

  btnPrev.addEventListener("click", () => { PAGE = Math.max(1, PAGE - 1); loadList(); });
  btnNext.addEventListener("click", () => { PAGE += 1; loadList(); });

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-detail]");
    if (!btn) return;
    openDetail(Number(btn.getAttribute("data-detail")));
  });

  mClose.addEventListener("click", closeModal);
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
