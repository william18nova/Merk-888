(function () {
  "use strict";

  const $sucursal = $("#id_sucursal_autocomplete");
  const $sucursalId = $("#id_sucursal");
  const inputFotos = document.getElementById("id_fotos");
  const previewGrid = document.getElementById("previewGrid");
  const btnProcesar = document.getElementById("btnProcesar");
  const btnLimpiar = document.getElementById("btnLimpiar");
  const btnConfirmar = document.getElementById("btnConfirmar");
  const scanAddBarcode = document.getElementById("scanAddBarcode");
  const scanAddQty = document.getElementById("scanAddQty");
  const btnScanAdd = document.getElementById("btnScanAdd");
  const resultadoBody = document.getElementById("resultadoBody");
  const providerInfo = document.getElementById("providerInfo");
  const providerModal = document.getElementById("providerModal");
  const providerNameInput = document.getElementById("providerNameInput");
  const providerCompanyInput = document.getElementById("providerCompanyInput");
  const providerPhoneInput = document.getElementById("providerPhoneInput");
  const providerEmailInput = document.getElementById("providerEmailInput");
  const providerAddressInput = document.getElementById("providerAddressInput");
  const providerModalError = document.getElementById("providerModalError");
  const btnProviderSave = document.getElementById("btnProviderSave");
  const rawResponse = document.getElementById("rawResponse");
  const invfAlert = document.getElementById("invfAlert");
  const invfLoading = document.getElementById("invfLoading");
  const agentStatus = document.getElementById("agentStatus");
  const agentStatusText = document.getElementById("agentStatusText");
  const btnMobileSession = document.getElementById("btnMobileSession");
  const mobileQrBox = document.getElementById("mobileQrBox");
  const mobileQrImage = document.getElementById("mobileQrImage");
  const mobileUploadUrl = document.getElementById("mobileUploadUrl");
  const btnCopyMobileUrl = document.getElementById("btnCopyMobileUrl");
  const mobileStatusText = document.getElementById("mobileStatusText");
  const mobileFilesList = document.getElementById("mobileFilesList");
  const summaryProducts = document.getElementById("summaryProducts");
  const summaryUnits = document.getElementById("summaryUnits");
  const summaryReady = document.getElementById("summaryReady");
  const summaryPending = document.getElementById("summaryPending");
  const reviewHint = document.getElementById("reviewHint");
  const resultFilters = Array.from(document.querySelectorAll("[data-result-filter]"));
  const draftNotice = document.getElementById("draftNotice");
  const draftNoticeText = document.getElementById("draftNoticeText");
  const btnRestoreDraft = document.getElementById("btnRestoreDraft");
  const btnDiscardDraft = document.getElementById("btnDiscardDraft");
  const photoPreviewModal = document.getElementById("photoPreviewModal");
  const photoPreviewImage = document.getElementById("photoPreviewImage");
  const photoPreviewTitle = document.getElementById("photoPreviewTitle");
  const confirmSummaryModal = document.getElementById("confirmSummaryModal");
  const confirmSummaryContent = document.getElementById("confirmSummaryContent");
  const btnConfirmSummarySave = document.getElementById("btnConfirmSummarySave");
  const flowSteps = {
    upload: document.getElementById("invfStepUpload"),
    process: document.getElementById("invfStepProcess"),
    review: document.getElementById("invfStepReview"),
    confirm: document.getElementById("invfStepConfirm")
  };
  const errorSucursal = document.getElementById("errorSucursal");
  const errorFotos = document.getElementById("errorFotos");
  const form = document.getElementById("inventarioFotosForm");

  const DRAFT_KEY = "inventarioFotosDraft:v1";
  let currentRows = [];
  let currentProvider = {};
  let currentFilter = "all";
  let pendingConfirmOptions = null;
  let mobileSession = null;
  let mobilePollTimer = null;
  let mobileImportedFiles = new Set();

  function getCSRFToken() {
    return form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function normalizeBarcode(value) {
    return String(value || "").replace(/\s+/g, "").toLowerCase();
  }

  function parsePositiveQty(value, fallback = 1) {
    const qty = Number.parseInt(String(value || "").trim(), 10);
    return Number.isFinite(qty) && qty > 0 ? qty : fallback;
  }

  function normalizePriceText(value) {
    return String(value ?? "").trim();
  }

  function normalizeIvaText(value) {
    return String(value ?? "").replace("%", "").trim();
  }

  function isTruthyFlag(value) {
    return value === true || value === "true" || value === 1 || value === "1";
  }

  function showAlert(type, message) {
    invfAlert.className = `invf-alert invf-alert--${type}`;
    invfAlert.textContent = message;
    invfAlert.style.display = "block";
  }

  function clearAlert() {
    invfAlert.style.display = "none";
    invfAlert.textContent = "";
    invfAlert.className = "invf-alert";
  }

  function clearErrors() {
    errorSucursal.textContent = "";
    errorFotos.textContent = "";
  }

  function setFlowStep(stage) {
    const order = ["upload", "process", "review", "confirm"];
    const activeIndex = Math.max(0, order.indexOf(stage));
    order.forEach((name, index) => {
      const el = flowSteps[name];
      if (!el) return;
      el.classList.toggle("is-active", index === activeIndex);
      el.classList.toggle("is-complete", index < activeIndex);
    });
  }

  function getRowStatusKind(row) {
    if (!row?.encontrado) return "pending";
    if (row.agregado_por_barcode) return "added";
    if (row.reemplazado_por_barcode) return "corrected";
    return "ok";
  }

  function getReviewStats(rows = currentRows) {
    return rows.reduce((stats, row) => {
      const qty = parsePositiveQty(row?.cantidad, 0);
      const kind = getRowStatusKind(row);
      stats.products += 1;
      stats.units += qty;
      if (kind === "pending") stats.pending += 1;
      else stats.ready += 1;
      if (kind === "corrected") stats.corrected += 1;
      if (kind === "added") stats.added += 1;
      return stats;
    }, { products: 0, units: 0, ready: 0, pending: 0, corrected: 0, added: 0 });
  }

  function updateReviewSummary() {
    const stats = getReviewStats();
    if (summaryProducts) summaryProducts.textContent = stats.products;
    if (summaryUnits) summaryUnits.textContent = stats.units;
    if (summaryReady) summaryReady.textContent = stats.ready;
    if (summaryPending) summaryPending.textContent = stats.pending;

    if (reviewHint) {
      if (!stats.products) {
        reviewHint.textContent = "Procesa fotos para ver los resultados.";
      } else if (stats.pending) {
        reviewHint.textContent = `${stats.pending} producto(s) necesitan correccion antes de guardar.`;
      } else {
        reviewHint.textContent = "Todo listo para confirmar el inventario.";
      }
    }

    if (stats.products && !stats.pending) {
      setFlowStep("confirm");
    } else if (stats.products) {
      setFlowStep("review");
    }
  }

  function applyResultFilter() {
    resultadoBody.querySelectorAll("tr[data-index]").forEach(tr => {
      const index = Number(tr.dataset.index);
      const row = currentRows[index];
      const kind = getRowStatusKind(row);
      const show = currentFilter === "all" || currentFilter === kind;
      tr.classList.toggle("is-hidden", !show);
    });

    resultFilters.forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.resultFilter === currentFilter);
    });
  }

  function hideDraftNotice() {
    if (draftNotice) draftNotice.style.display = "none";
  }

  function readSavedDraft() {
    try {
      const raw = window.localStorage.getItem(DRAFT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_err) {
      return null;
    }
  }

  function persistDraft() {
    if (!currentRows.length) return;
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify({
        rows: currentRows,
        provider: currentProvider,
        rawResponse: rawResponse?.textContent || "",
        sucursalText: $sucursal.val() || "",
        sucursalId: $sucursalId.val() || "",
        savedAt: new Date().toISOString()
      }));
    } catch (_err) {
      // Local storage is optional; the workflow still works without it.
    }
  }

  function clearSavedDraft() {
    try {
      window.localStorage.removeItem(DRAFT_KEY);
    } catch (_err) {
      // Nothing to do.
    }
    hideDraftNotice();
  }

  function showDraftNoticeIfNeeded() {
    const draft = readSavedDraft();
    if (!draft?.rows?.length || !draftNotice) return;
    if (draftNoticeText) {
      const saved = draft.savedAt ? new Date(draft.savedAt) : null;
      const when = saved && !Number.isNaN(saved.getTime()) ? saved.toLocaleString() : "reciente";
      draftNoticeText.textContent = `${draft.rows.length} producto(s) guardados como borrador. Ultimo cambio: ${when}.`;
    }
    draftNotice.style.display = "flex";
  }

  function restoreDraft() {
    const draft = readSavedDraft();
    if (!draft?.rows?.length) return;
    currentProvider = draft.provider || {};
    $sucursal.val(draft.sucursalText || "");
    $sucursalId.val(draft.sucursalId || "");
    rawResponse.textContent = draft.rawResponse || "Borrador restaurado.";
    currentFilter = "all";
    renderProviderInfo(currentProvider);
    renderTable(draft.rows);
    hideDraftNotice();
    showAlert("success", "Borrador restaurado. Revisa y confirma cuando este listo.");
  }

  function openPhotoPreview(src, title) {
    if (!photoPreviewModal || !photoPreviewImage) return;
    photoPreviewImage.src = src;
    if (photoPreviewTitle) photoPreviewTitle.textContent = title || "Foto adjunta";
    photoPreviewModal.classList.add("is-open");
    photoPreviewModal.setAttribute("aria-hidden", "false");
  }

  function closePhotoPreview() {
    if (!photoPreviewModal || !photoPreviewImage) return;
    photoPreviewModal.classList.remove("is-open");
    photoPreviewModal.setAttribute("aria-hidden", "true");
    photoPreviewImage.removeAttribute("src");
  }

  function closeConfirmSummary() {
    if (!confirmSummaryModal) return;
    confirmSummaryModal.classList.remove("is-open");
    confirmSummaryModal.setAttribute("aria-hidden", "true");
    pendingConfirmOptions = null;
  }

  function renderConfirmSummary(rows, options = {}) {
    if (!confirmSummaryModal || !confirmSummaryContent) return false;
    const stats = getReviewStats(currentRows);
    if (stats.pending) return false;
    const providerName = currentProvider.nombre || "Sin proveedor detectado";
    const branchName = $sucursal.val() || "Sucursal no seleccionada";
    const providerMode = currentProvider.proveedorid
      ? "Proveedor registrado"
      : options.createProvider
        ? "Se creara al guardar"
        : "Pendiente";

    confirmSummaryContent.innerHTML = `
      <div class="invf-confirm-summary__row"><span>Sucursal</span><strong>${escapeHtml(branchName)}</strong></div>
      <div class="invf-confirm-summary__row"><span>Proveedor</span><strong>${escapeHtml(providerName)}</strong></div>
      <div class="invf-confirm-summary__row"><span>Estado proveedor</span><strong>${escapeHtml(providerMode)}</strong></div>
      <div class="invf-confirm-summary__row"><span>Productos</span><strong>${stats.products}</strong></div>
      <div class="invf-confirm-summary__row"><span>Total unidades</span><strong>${stats.units}</strong></div>
      <div class="invf-confirm-summary__row"><span>Corregidos / agregados</span><strong>${stats.corrected + stats.added}</strong></div>
    `;
    pendingConfirmOptions = { ...options, skipReview: true };
    confirmSummaryModal.classList.add("is-open");
    confirmSummaryModal.setAttribute("aria-hidden", "false");
    return true;
  }

  function collectRowsForSave() {
    return Array.from(resultadoBody.querySelectorAll("tr[data-index]")).map(tr => {
      const index = Number(tr.dataset.index);
      const qty = Number(tr.querySelector(".invf-qty")?.value || 0);
      return {
        productoid: currentRows[index]?.productoid || null,
        producto: currentRows[index]?.producto || "",
        cantidad: qty,
        codigo_de_barras: currentRows[index]?.codigo_de_barras || "",
        precio_unitario: normalizePriceText(currentRows[index]?.precio_unitario || ""),
        precio_unitario_visible: normalizePriceText(currentRows[index]?.precio_unitario_visible || ""),
        precio_unitario_sin_iva: normalizePriceText(currentRows[index]?.precio_unitario_sin_iva || ""),
        iva_porcentaje: normalizeIvaText(currentRows[index]?.iva_porcentaje || ""),
        precio_incluye_iva: isTruthyFlag(currentRows[index]?.precio_incluye_iva),
        precio_iva_calculado: isTruthyFlag(currentRows[index]?.precio_iva_calculado)
      };
    }).filter(row => (row.productoid || row.producto) && row.cantidad > 0);
  }


  function normalizeAgentBase(url) {
    return String(url || "http://127.0.0.1:8788").trim().replace(/\/+$/, "");
  }

  function agentHeaders(extra = {}) {
    const headers = { ...extra };
    if (window.INVF.agentToken) headers["X-Inventory-Agent-Token"] = window.INVF.agentToken;
    return headers;
  }

  function setMobileStatus(message, kind = "") {
    if (!mobileStatusText) return;
    mobileStatusText.textContent = message;
    mobileStatusText.className = `invf-mobile-status ${kind ? `invf-mobile-status--${kind}` : ""}`;
  }

  function stopMobilePolling() {
    if (mobilePollTimer) {
      clearInterval(mobilePollTimer);
      mobilePollTimer = null;
    }
  }

  function renderMobileFilesList(files = []) {
    if (!mobileFilesList) return;
    if (!files.length) {
      mobileFilesList.innerHTML = "";
      return;
    }
    mobileFilesList.innerHTML = files.map((file, index) => `
      <div class="invf-mobile-file">
        ${file.download_url ? `<img src="${escapeHtml(file.download_url)}" alt="${escapeHtml(file.name || "foto del celular")}">` : ""}
        <div>
          <strong>${index + 1}. ${escapeHtml(file.name || file.id || "foto")}</strong>
          <span>${Math.round((file.size || 0) / 1024)} KB recibidos</span>
        </div>
      </div>
    `).join("");
  }

  function appendFilesToInput(files) {
    if (!files.length) return false;
    if (typeof DataTransfer === "undefined") {
      showAlert("error", "El navegador no permite agregar las fotos automaticamente. Abre el enlace y descarga/sube manualmente.");
      return false;
    }
    const dt = new DataTransfer();
    Array.from(inputFotos.files || []).forEach(file => dt.items.add(file));
    files.forEach(file => dt.items.add(file));
    inputFotos.files = dt.files;
    renderPreviews();
    return true;
  }

  async function importMobileFile(fileInfo) {
    if (!fileInfo?.id || mobileImportedFiles.has(fileInfo.id)) return false;
    const response = await fetch(fileInfo.download_url, { cache: "no-store" });
    if (!response.ok) throw new Error(`No se pudo descargar ${fileInfo.name || "la foto del celular"}.`);
    const blob = await response.blob();
    const filename = fileInfo.name || fileInfo.id || `foto_celular_${Date.now()}.jpg`;
    const file = new File([blob], filename, { type: blob.type || "image/jpeg" });
    if (appendFilesToInput([file])) {
      mobileImportedFiles.add(fileInfo.id);
      return true;
    }
    return false;
  }

  async function pollMobileFiles() {
    if (!mobileSession?.session_id) return;
    const base = normalizeAgentBase(window.INVF.agentUrl);
    try {
      const response = await fetch(`${base}/mobile/session/${encodeURIComponent(mobileSession.session_id)}/files`, {
        method: "GET",
        mode: "cors",
        cache: "no-store",
        headers: agentHeaders()
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) throw new Error(data.error || "No se pudo consultar el celular.");

      const files = Array.isArray(data.files) ? data.files : [];
      renderMobileFilesList(files);

      let imported = 0;
      for (const fileInfo of files) {
        if (await importMobileFile(fileInfo)) imported += 1;
      }
      if (imported > 0) {
        setMobileStatus(`${imported} foto(s) nueva(s) recibida(s) desde el celular.`, "ok");
      } else if (files.length) {
        setMobileStatus(`${files.length} foto(s) recibida(s). Puedes tomar mas o procesar.`, "ok");
      } else {
        setMobileStatus("Esperando fotos del celular...");
      }
    } catch (err) {
      setMobileStatus(err.message || "No se pudo consultar el celular.", "error");
    }
  }

  async function createMobileSession() {
    clearAlert();
    const agentOk = await pingAgent();
    if (!agentOk) {
      showAlert("error", "Abre el agente local antes de conectar el celular.");
      return;
    }

    const base = normalizeAgentBase(window.INVF.agentUrl);
    try {
      if (btnMobileSession) btnMobileSession.disabled = true;
      setMobileStatus("Creando conexion...");
      const response = await fetch(`${base}/mobile/session`, {
        method: "POST",
        mode: "cors",
        cache: "no-store",
        headers: agentHeaders({ "X-Requested-With": "XMLHttpRequest" })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) throw new Error(data.error || "No se pudo crear la conexion.");

      stopMobilePolling();
      mobileSession = data;
      mobileImportedFiles = new Set();
      if (mobileQrBox) mobileQrBox.style.display = "grid";
      if (mobileQrImage) mobileQrImage.src = data.qr_url;
      if (mobileUploadUrl) mobileUploadUrl.value = data.upload_url || "";
      setMobileStatus("Escanea el QR con el celular y envia las fotos.", "ok");
      mobilePollTimer = setInterval(pollMobileFiles, 2500);
      pollMobileFiles();
    } catch (err) {
      showAlert("error", err.message || "No se pudo conectar el celular.");
      setMobileStatus("No se pudo crear la conexion.", "error");
    } finally {
      if (btnMobileSession) btnMobileSession.disabled = false;
    }
  }

  async function copyMobileUrl() {
    const value = mobileUploadUrl?.value || "";
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setMobileStatus("Enlace copiado.", "ok");
    } catch (_err) {
      mobileUploadUrl?.select();
      setMobileStatus("Copia el enlace manualmente.", "error");
    }
  }

  function setAgentStatus(kind, message) {
    if (!agentStatus || !agentStatusText) return;
    agentStatus.className = `invf-agent-status invf-agent-status--${kind}`;
    agentStatusText.textContent = message;
  }

  async function pingAgent() {
    const base = normalizeAgentBase(window.INVF.agentUrl);
    try {
      const response = await fetch(`${base}/ping`, {
        method: "GET",
        mode: "cors",
        cache: "no-store",
        headers: agentHeaders()
      });
      if (!response.ok) throw new Error("El agente respondió con error.");
      const data = await response.json().catch(() => ({}));
      setAgentStatus("ok", data.message || "Conectado y listo en este PC.");
      return true;
    } catch (_err) {
      setAgentStatus("error", "No se pudo conectar con el agente local. Debes abrirlo en este PC antes de procesar fotos.");
      return false;
    }
  }

  async function descargarCatalogoBlob() {
    const response = await fetch(window.INVF.catalogoUrl, {
      method: "GET",
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      cache: "no-store"
    });
    if (!response.ok) throw new Error("No se pudo descargar el catálogo de productos.");
    return await response.blob();
  }

  async function procesarEnAgenteLocal() {
    const base = normalizeAgentBase(window.INVF.agentUrl);
    const catalogoBlob = await descargarCatalogoBlob();

    const fd = new FormData();
    fd.append("catalogo", catalogoBlob, "catalogo_productos.csv");
    Array.from(inputFotos.files || []).forEach(file => fd.append("fotos", file, file.name));

    const response = await fetch(`${base}/inventory/process`, {
      method: "POST",
      mode: "cors",
      headers: agentHeaders(),
      body: fd
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false || data.ok === false) {
      throw new Error(data.error || data.message || "El agente local no pudo procesar las fotos.");
    }
    return data;
  }

  function buildRawOutput(data) {
    const sections = [];
    const provider = extractProviderInfo(data);
    if (provider.nombre || provider.nit || provider.factura || provider.fecha) {
      sections.push([
        "DATOS FACTURA",
        `Proveedor: ${provider.nombre || "No detectado"}`,
        provider.nit ? `NIT: ${provider.nit}` : "",
        provider.factura ? `Factura: ${provider.factura}` : "",
        provider.fecha ? `Fecha: ${provider.fecha}` : ""
      ].filter(Boolean).join("\n"));
    }
    if (data.raw_text) sections.push(`RESULTADO FINAL\n${data.raw_text}`);
    if (data.raw_text_modelo && data.raw_text_modelo !== data.raw_text) {
      sections.push(`RESPUESTA ORIGINAL DEL MODELO\n${data.raw_text_modelo}`);
    }
    if (Array.isArray(data.matching_debug) && data.matching_debug.length) {
      sections.push(`MATCHING CATALOGO\n${JSON.stringify(data.matching_debug, null, 2)}`);
    }
    if (data.ocr_text) sections.push(`OCR GEMINI\n${data.ocr_text}`);
    return sections.join("\n\n---\n\n") || "Sin respuesta de texto.";
  }

  function extractProviderInfo(data) {
    const info = data?.proveedor_factura || {};
    return {
      nombre: String(info.nombre || data?.proveedor_nombre || data?.proveedor || data?.nombre || "").trim(),
      empresa: String(info.empresa || data?.empresa || "").trim(),
      nit: String(info.nit || data?.proveedor_nit || data?.nit || "").trim(),
      factura: String(info.factura || data?.factura_numero || data?.factura || "").trim(),
      fecha: String(info.fecha || data?.factura_fecha || data?.fecha || "").trim(),
      proveedorid: info.proveedorid || data?.proveedorid || null,
      encontrado: isTruthyFlag(info.encontrado || data?.proveedor_encontrado || data?.encontrado),
      create_if_missing: isTruthyFlag(info.create_if_missing || data?.create_if_missing),
      telefono: String(info.telefono || data?.telefono || "").trim(),
      email: String(info.email || data?.email || "").trim(),
      direccion: String(info.direccion || data?.direccion || "").trim()
    };
  }

  async function resolveProviderInfo(provider) {
    const base = extractProviderInfo(provider || {});
    if (!base.nombre || !window.INVF.providerLookupUrl) return base;

    try {
      const response = await fetch(`${window.INVF.providerLookupUrl}?term=${encodeURIComponent(base.nombre)}`, {
        cache: "no-store"
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.found || !data.proveedor) return base;
      const found = extractProviderInfo(data.proveedor);
      return {
        ...base,
        ...found,
        nit: base.nit || found.nit,
        factura: base.factura || found.factura,
        fecha: base.fecha || found.fecha,
        encontrado: true
      };
    } catch (_err) {
      return base;
    }
  }

  function renderProviderInfo(data) {
    currentProvider = extractProviderInfo(data);
    if (!providerInfo) return;

    if (!currentProvider.nombre && !currentProvider.nit && !currentProvider.factura && !currentProvider.fecha) {
      providerInfo.style.display = "none";
      providerInfo.innerHTML = "";
      return;
    }

    const chips = [
      currentProvider.encontrado
        ? `<span>Proveedor registrado</span>`
        : `<span class="invf-provider-chip--warn">Se creara al guardar</span>`,
      currentProvider.nit ? `<span>NIT ${escapeHtml(currentProvider.nit)}</span>` : "",
      currentProvider.factura ? `<span>Factura ${escapeHtml(currentProvider.factura)}</span>` : "",
      currentProvider.fecha ? `<span>${escapeHtml(currentProvider.fecha)}</span>` : ""
    ].filter(Boolean).join("");

    providerInfo.innerHTML = `
      <div class="invf-provider-main">
        <small>Proveedor detectado</small>
        <strong>${escapeHtml(currentProvider.nombre || "Sin nombre detectado")}</strong>
      </div>
      ${chips ? `<div class="invf-provider-chips">${chips}</div>` : ""}
    `;
    providerInfo.style.display = "flex";
    persistDraft();
  }

  function collectProviderPayload({ createIfMissing = false } = {}) {
    return {
      proveedorid: currentProvider.proveedorid || null,
      nombre: String(providerNameInput?.value || currentProvider.nombre || "").trim(),
      empresa: String(providerCompanyInput?.value || currentProvider.empresa || "").trim(),
      telefono: String(providerPhoneInput?.value || currentProvider.telefono || "").trim(),
      email: String(providerEmailInput?.value || currentProvider.email || "").trim(),
      direccion: String(providerAddressInput?.value || currentProvider.direccion || "").trim(),
      nit: currentProvider.nit || "",
      factura: currentProvider.factura || "",
      fecha: currentProvider.fecha || "",
      create_if_missing: createIfMissing
    };
  }

  function openProviderModal(message = "") {
    if (!providerModal) return;
    providerNameInput.value = currentProvider.nombre || "";
    providerCompanyInput.value = currentProvider.empresa || "";
    providerPhoneInput.value = currentProvider.telefono || "";
    providerEmailInput.value = currentProvider.email || "";
    providerAddressInput.value = currentProvider.direccion || "";
    providerModalError.textContent = message || "";
    providerModal.classList.add("is-open");
    providerModal.setAttribute("aria-hidden", "false");
    setTimeout(() => providerNameInput?.focus(), 0);
  }

  function closeProviderModal() {
    if (!providerModal) return;
    providerModal.classList.remove("is-open");
    providerModal.setAttribute("aria-hidden", "true");
    providerModalError.textContent = "";
  }

  function requireProviderBeforeSave() {
    if (currentProvider.proveedorid) return true;
    openProviderModal(currentProvider.nombre ? "" : "Escribe el nombre del proveedor.");
    return false;
  }

  function validateBeforeProcess() {
    clearErrors();
    let ok = true;

    if (!$sucursalId.val()) {
      errorSucursal.textContent = "Selecciona una sucursal válida.";
      ok = false;
    }

    if (!inputFotos.files || !inputFotos.files.length) {
      errorFotos.textContent = "Adjunta al menos una foto.";
      ok = false;
    }

    return ok;
  }

  function renderPreviews() {
    previewGrid.innerHTML = "";
    const files = Array.from(inputFotos.files || []);

    if (!files.length) return;

    files.forEach((file, index) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "invf-preview-card";
      card.dataset.previewTitle = file.name;

      const img = document.createElement("img");
      img.alt = file.name;
      img.src = URL.createObjectURL(file);
      card.dataset.previewSrc = img.src;

      const meta = document.createElement("div");
      meta.className = "invf-preview-meta";
      meta.innerHTML = `<strong>${index + 1}. ${escapeHtml(file.name)}</strong><span>${Math.round(file.size / 1024)} KB</span>`;

      card.appendChild(img);
      card.appendChild(meta);
      previewGrid.appendChild(card);
    });
  }

  function updateConfirmButtonState() {
    btnConfirmar.disabled = !currentRows.length || currentRows.some(row => !row.encontrado);
    updateReviewSummary();
    applyResultFilter();
    persistDraft();
  }

  function setLoading(state) {
    invfLoading.style.display = state ? "flex" : "none";
    btnProcesar.disabled = state;
    if (state) {
      setFlowStep("process");
      btnConfirmar.disabled = true;
    } else {
      updateConfirmButtonState();
    }
  }

  function syncQuantitiesFromTable() {
    resultadoBody.querySelectorAll("tr[data-index]").forEach(tr => {
      const index = Number(tr.dataset.index);
      if (!Number.isInteger(index) || !currentRows[index]) return;
      const qtyInput = tr.querySelector(".invf-qty");
      const priceInput = tr.querySelector(".invf-price");
      currentRows[index].cantidad = parsePositiveQty(qtyInput?.value, currentRows[index].cantidad || 1);
      currentRows[index].precio_unitario = normalizePriceText(priceInput?.value || currentRows[index].precio_unitario || "");
    });
    updateReviewSummary();
    persistDraft();
  }

  function getRowStatusText(row) {
    if (!row.encontrado) return "No existe en BD";
    if (row.agregado_por_barcode) return "Agregado por codigo";
    if (row.reemplazado_por_barcode) return "Reemplazado por código";
    return "Encontrado";
  }

  function buildStatusCell(row, index) {
    const statusKind = getRowStatusKind(row);
    const badgeClass = {
      pending: "invf-badge--danger",
      added: "invf-badge--added",
      corrected: "invf-badge--corrected",
      ok: "invf-badge--ok"
    }[statusKind] || "invf-badge--warn";

    return `
      <div class="invf-status-cell">
        <span class="invf-badge ${badgeClass}">
          ${getRowStatusText(row)}
        </span>
        <button type="button" class="invf-btn invf-btn--ghost invf-btn--mini invf-remove-row" data-index="${index}">
          Quitar
        </button>
      </div>
    `;
  }

  function buildBarcodeCell(row, index) {
    const barcodeSearch = escapeHtml(row.barcode_search || "");
    const barcodeActual = escapeHtml(row.codigo_de_barras || "");
    const barcodePrevio = row.codigo_de_barras_original && row.codigo_de_barras_original !== row.codigo_de_barras
      ? `<small class="invf-current-barcode">Código anterior: ${escapeHtml(row.codigo_de_barras_original)}</small>`
      : "";

    return `
      <div class="invf-barcode-cell">
        <label class="invf-barcode-label" for="invf-barcode-${index}">Buscar por código</label>
        <div class="invf-barcode-wrap">
          <input
            type="text"
            id="invf-barcode-${index}"
            class="invf-barcode"
            data-index="${index}"
            value="${barcodeSearch}"
            placeholder="Escanea o escribe el código"
            autocomplete="off"
          >
          <button type="button" class="invf-btn invf-btn--ghost invf-btn--mini invf-apply-barcode" data-index="${index}">
            Reemplazar
          </button>
        </div>
        <small class="invf-barcode-help">Escanea el código y presiona Enter o el botón Reemplazar.</small>
        <small class="invf-current-barcode">Código actual: ${barcodeActual || "Sin código"}</small>
        ${barcodePrevio}
      </div>
    `;
  }

  function buildProductCell(row) {
    const original = row.original_producto && row.original_producto !== row.producto
      ? `<small class="invf-previous-product">Antes: ${escapeHtml(row.original_producto)}</small>`
      : "";

    return `
      <div class="invf-producto">${escapeHtml(row.producto || "")}</div>
      ${row.productoid ? `<small>ID ${row.productoid}</small>` : ""}
      ${original}
    `;
  }

  function buildPriceCell(row, index) {
    const iva = normalizeIvaText(row.iva_porcentaje);
    const visible = normalizePriceText(row.precio_unitario_visible || row.precio_unitario_sin_iva || "");
    const calculado = isTruthyFlag(row.precio_iva_calculado);
    let nota = "";

    if (calculado && iva && visible) {
      nota = `IVA ${iva}% aplicado sobre ${visible}`;
    } else if (iva) {
      nota = iva === "0" ? "IVA 0%" : `IVA ${iva}% detectado`;
    }

    return `
      <div class="invf-price-cell">
        <input
          type="text"
          class="invf-price"
          value="${escapeHtml(row.precio_unitario || "")}"
          data-index="${index}"
          placeholder="Sin precio"
          autocomplete="off"
        >
        ${nota ? `<small class="invf-price-note">${escapeHtml(nota)}</small>` : ""}
      </div>
    `;
  }

  function renderTable(rows) {
    currentRows = Array.isArray(rows) ? rows.map(row => ({
      ...row,
      producto: row?.producto || row?.nombre || "",
      productoid: row?.productoid || null,
      codigo_de_barras: row?.codigo_de_barras || row?.barcode || "",
      precio_unitario: normalizePriceText(row?.precio_unitario || row?.precio || ""),
      precio_unitario_visible: normalizePriceText(row?.precio_unitario_visible || ""),
      precio_unitario_sin_iva: normalizePriceText(row?.precio_unitario_sin_iva || ""),
      iva_porcentaje: normalizeIvaText(row?.iva_porcentaje || row?.iva || ""),
      precio_incluye_iva: isTruthyFlag(row?.precio_incluye_iva),
      precio_iva_calculado: isTruthyFlag(row?.precio_iva_calculado),
      encontrado: row?.encontrado === true || Boolean(row?.productoid),
      reemplazado_por_barcode: row?.reemplazado_por_barcode === true,
      barcode_search: "",
      codigo_de_barras_original: row?.codigo_de_barras || row?.barcode || ""
    })) : [];
    resultadoBody.innerHTML = "";

    if (!currentRows.length) {
      resultadoBody.innerHTML = '<tr class="invf-empty-row"><td colspan="5">No se detectaron productos.</td></tr>';
      btnConfirmar.disabled = true;
      updateReviewSummary();
      setFlowStep("upload");
      return;
    }

    currentRows.forEach((row, index) => {
      const tr = document.createElement("tr");
      tr.dataset.index = index;
      tr.className = `invf-result-row is-${getRowStatusKind(row)}`;
      tr.innerHTML = `
        <td>${buildProductCell(row)}</td>
        <td>${buildBarcodeCell(row, index)}</td>
        <td>
          <input type="number" min="1" class="invf-qty" value="${row.cantidad || 1}" data-index="${index}">
        </td>
        <td>${buildPriceCell(row, index)}</td>
        <td>${buildStatusCell(row, index)}</td>
      `;
      resultadoBody.appendChild(tr);
    });

    setupBarcodeAutocompletes();
    updateConfirmButtonState();
  }

  function updateRowVisual(index) {
    const row = currentRows[index];
    const tr = resultadoBody.querySelector(`tr[data-index="${index}"]`);
    if (!row || !tr) return;

    tr.className = `invf-result-row is-${getRowStatusKind(row)}`;
    const tds = tr.querySelectorAll("td");
    if (tds[0]) tds[0].innerHTML = buildProductCell(row);
    if (tds[1]) tds[1].innerHTML = buildBarcodeCell(row, index);
    if (tds[3]) tds[3].innerHTML = buildPriceCell(row, index);
    if (tds[4]) {
      tds[4].innerHTML = buildStatusCell(row, index);
    }

    setupBarcodeAutocompletes();
    updateConfirmButtonState();
  }

  function applyBarcodeResult(index, item, barcodeTyped) {
    const row = currentRows[index];
    if (!row || !item) return;

    if (!row.original_producto) {
      row.original_producto = row.producto || "";
    }

    row.producto = item.nombre || item.text || row.producto;
    row.productoid = item.id || row.productoid;
    row.barcode_search = (barcodeTyped || item.barcode || "").trim();
    row.codigo_de_barras_original = row.codigo_de_barras_original || row.codigo_de_barras || "";
    row.codigo_de_barras = (item.barcode || barcodeTyped || "").trim();
    row.precio_unitario = normalizePriceText(row.precio_unitario || "");
    row.encontrado = true;
    row.reemplazado_por_barcode = true;

    updateRowVisual(index);
    showAlert("success", `Fila ${index + 1} reemplazada por "${row.producto}" usando el código de barras.`);
  }

  async function obtenerProductoPorCodigo(barcode, options = {}) {
    const raw = String(barcode || "").trim();
    if (!raw) {
      throw new Error("Escribe o escanea un codigo de barras antes de aplicar.");
    }

    const response = await fetch(`${window.INVF.barcodeSearchUrl}?term=${encodeURIComponent(raw)}&page=1`, {
      cache: "no-store"
    });
    const data = await response.json().catch(() => ({ results: [] }));
    const results = Array.isArray(data.results) ? data.results : [];

    if (!response.ok || !results.length) {
      throw new Error("No se encontro ningun producto con ese codigo de barras.");
    }

    const normalized = normalizeBarcode(raw);
    let selected = results.find(item => normalizeBarcode(item.barcode) === normalized);

    if (!selected && options.requireExact) {
      throw new Error("No se encontro coincidencia exacta para ese codigo de barras.");
    }

    return selected || results[0];
  }

  async function buscarProductoPorCodigo(index, barcode, options = {}) {
    const raw = String(barcode || "").trim();
    if (!raw) {
      showAlert("error", "Escribe o escanea un código de barras antes de aplicar.");
      return false;
    }

    try {
      const selectedProducto = await obtenerProductoPorCodigo(raw, options);
      applyBarcodeResult(index, selectedProducto, raw);
      return true;

      const response = await fetch(`${window.INVF.barcodeSearchUrl}?term=${encodeURIComponent(raw)}&page=1`, {
        cache: "no-store"
      });
      const data = await response.json().catch(() => ({ results: [] }));
      const results = Array.isArray(data.results) ? data.results : [];

      if (!response.ok || !results.length) {
        throw new Error("No se encontró ningún producto con ese código de barras.");
      }

      const normalized = raw.replace(/\s+/g, "").toLowerCase();
      let selected = results.find(item => String(item.barcode || "").replace(/\s+/g, "").toLowerCase() === normalized);

      if (!selected && options.requireExact) {
        throw new Error("No se encontró coincidencia exacta para ese código de barras.");
      }

      if (!selected) {
        selected = results[0];
      }

      applyBarcodeResult(index, selected, raw);
      return true;
    } catch (err) {
      showAlert("error", err.message || "No se pudo buscar el código de barras.");
      return false;
    }
  }

  function findExistingRowIndex(product, barcodeTyped) {
    const productId = product?.id ? String(product.id) : "";
    const barcode = normalizeBarcode(product?.barcode || barcodeTyped);

    return currentRows.findIndex(row => {
      const rowProductId = row?.productoid ? String(row.productoid) : "";
      if (productId && rowProductId && productId === rowProductId) return true;
      return barcode && normalizeBarcode(row?.codigo_de_barras) === barcode;
    });
  }

  function addOrIncreaseProductFromScan(product, quantity, barcodeTyped) {
    if (!product) return false;

    syncQuantitiesFromTable();
    const qty = parsePositiveQty(quantity, 1);
    const existingIndex = findExistingRowIndex(product, barcodeTyped);

    if (existingIndex >= 0) {
      const row = currentRows[existingIndex];
      row.cantidad = parsePositiveQty(row.cantidad, 0) + qty;
      row.encontrado = true;
      row.agregado_por_barcode = row.agregado_por_barcode || false;
      renderTable(currentRows);
      showAlert("success", `Se sumaron ${qty} unidad(es) a "${row.producto}".`);
      return true;
    }

    const barcode = (product.barcode || barcodeTyped || "").trim();
    currentRows.push({
      producto: product.nombre || product.text || "",
      original_producto: product.nombre || product.text || "",
      cantidad: qty,
      productoid: product.id || null,
      codigo_de_barras: barcode,
      codigo_de_barras_original: barcode,
      precio_unitario: "",
      precio_unitario_visible: "",
      precio_unitario_sin_iva: "",
      iva_porcentaje: "",
      precio_incluye_iva: false,
      precio_iva_calculado: false,
      encontrado: true,
      reemplazado_por_barcode: false,
      agregado_por_barcode: true,
      barcode_search: ""
    });
    renderTable(currentRows);
    showAlert("success", `Producto agregado: "${product.nombre || product.text}" x ${qty}.`);
    return true;
  }

  async function agregarProductoEscaneado(productFromAutocomplete = null) {
    const raw = String(scanAddBarcode?.value || "").trim();
    const qty = parsePositiveQty(scanAddQty?.value, 1);

    if (!raw && !productFromAutocomplete) {
      showAlert("error", "Escanea o escribe un codigo de barras.");
      scanAddBarcode?.focus();
      return false;
    }

    try {
      if (btnScanAdd) btnScanAdd.disabled = true;
      const product = productFromAutocomplete || await obtenerProductoPorCodigo(raw, { requireExact: true });
      addOrIncreaseProductFromScan(product, qty, raw || product.barcode || "");
      if (scanAddBarcode) {
        scanAddBarcode.value = "";
        scanAddBarcode.focus();
      }
      if (scanAddQty) scanAddQty.value = "1";
      return true;
    } catch (err) {
      showAlert("error", err.message || "No se pudo agregar el producto escaneado.");
      scanAddBarcode?.focus();
      return false;
    } finally {
      if (btnScanAdd) btnScanAdd.disabled = false;
    }
  }

  function setupScanAddAutocomplete() {
    if (!scanAddBarcode) return;
    const $input = $(scanAddBarcode);
    if ($input.data("uiAutocomplete")) return;

    $input.autocomplete({
      minLength: 1,
      delay: 0,
      source: function (request, response) {
        const term = (request.term || "").trim();
        if (!term) {
          response([]);
          return;
        }

        fetch(`${window.INVF.barcodeSearchUrl}?term=${encodeURIComponent(term)}&page=1`, { cache: "no-store" })
          .then(r => r.ok ? r.json() : { results: [] })
          .then(data => {
            response((data.results || []).map(item => ({
              label: `${item.barcode || "Sin codigo"} - ${item.text}`,
              value: item.barcode || request.term,
              id: item.id,
              nombre: item.nombre || item.text,
              text: item.text,
              barcode: item.barcode || ""
            })));
          })
          .catch(() => response([]));
      },
      select: function (_event, ui) {
        this.value = ui.item.barcode || ui.item.value || "";
        agregarProductoEscaneado(ui.item);
        return false;
      }
    });
  }

  function setupBarcodeAutocompletes() {
    $(".invf-barcode").each(function () {
      const $input = $(this);
      if ($input.data("uiAutocomplete")) return;

      $input.autocomplete({
        minLength: 1,
        delay: 0,
        source: function (request, response) {
          const term = (request.term || "").trim();
          if (!term) {
            response([]);
            return;
          }

          fetch(`${window.INVF.barcodeSearchUrl}?term=${encodeURIComponent(term)}&page=1`, { cache: "no-store" })
            .then(r => r.ok ? r.json() : { results: [] })
            .then(data => {
              response((data.results || []).map(item => ({
                label: `${item.barcode || "Sin código"} · ${item.text}`,
                value: item.barcode || request.term,
                id: item.id,
                text: item.text,
                barcode: item.barcode || ""
              })));
            })
            .catch(() => response([]));
        },
        select: function (_event, ui) {
          const index = Number(this.dataset.index);
          this.value = ui.item.barcode || ui.item.value || "";
          applyBarcodeResult(index, ui.item, this.value);
          return false;
        }
      });
    });
  }

  async function procesarFotos() {
    clearAlert();
    if (!validateBeforeProcess()) return;

    const agentOk = await pingAgent();
    if (!agentOk) {
      showAlert("error", "No se detectó el agente local. Ábrelo en este PC y vuelve a intentar.");
      return;
    }

    try {
      setLoading(true);
      const data = await procesarEnAgenteLocal();
      const provider = await resolveProviderInfo(data);
      data.proveedor_factura = provider;
      renderProviderInfo(provider);
      rawResponse.textContent = buildRawOutput(data);
      currentFilter = "all";
      renderTable(data.rows || []);
      showAlert("success", "Proceso completado en el agente local. Revisa la tabla y, si hace falta, reemplaza productos por código de barras antes de confirmar.");
    } catch (err) {
      renderProviderInfo({});
      renderTable([]);
      rawResponse.textContent = "Sin respuesta.";
      showAlert("error", err.message || "Error procesando las fotos.");
    } finally {
      setLoading(false);
    }
  }

  async function confirmarInventario(options = {}) {
    clearAlert();
    if (!$sucursalId.val()) {
      showAlert("error", "Debes seleccionar una sucursal válida.");
      return;
    }

    if (!options.createProvider && !requireProviderBeforeSave()) {
      return;
    }

    syncQuantitiesFromTable();
    const rows = collectRowsForSave();

    if (!rows.length) {
      showAlert("error", "No hay productos válidos para guardar.");
      return;
    }

    if (!options.skipReview && renderConfirmSummary(rows, options)) {
      return;
    }

    if (currentRows.some(row => !row.encontrado)) {
      showAlert("error", "Todas las filas deben quedar encontradas antes de confirmar. Puedes corregirlas con código de barras.");
      return;
    }

    const fd = new FormData();
    fd.append("csrfmiddlewaretoken", getCSRFToken());
    fd.append("sucursal_id", $sucursalId.val());
    fd.append("items_json", JSON.stringify(rows));
    fd.append("proveedor_json", JSON.stringify(collectProviderPayload({
      createIfMissing: Boolean(options.createProvider)
    })));

    try {
      btnConfirmar.disabled = true;
      const response = await fetch(window.INVF.confirmarUrl, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: fd
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok || !data.success) {
        if (data.needs_provider) {
          if (data.proveedor_factura) {
            currentProvider = {
              ...currentProvider,
              ...extractProviderInfo(data.proveedor_factura)
            };
          }
          openProviderModal(data.error || "Confirma los datos del proveedor.");
          btnConfirmar.disabled = false;
          return;
        }
        throw new Error(data.error || "No se pudo actualizar el inventario.");
      }

      clearSavedDraft();
      setFlowStep("confirm");
      showAlert("success", data.message || "Inventario actualizado correctamente.");
      setTimeout(() => {
        if (data.redirect_url) window.location.href = data.redirect_url;
      }, 1200);
    } catch (err) {
      btnConfirmar.disabled = false;
      showAlert("error", err.message || "Error actualizando inventario.");
    }
  }

  function resetAll() {
    clearAlert();
    clearErrors();
    currentRows = [];
    currentProvider = {};
    currentFilter = "all";
    clearSavedDraft();
    stopMobilePolling();
    mobileSession = null;
    mobileImportedFiles = new Set();
    inputFotos.value = "";
    previewGrid.innerHTML = "";
    if (scanAddBarcode) scanAddBarcode.value = "";
    if (scanAddQty) scanAddQty.value = "1";
    if (mobileQrBox) mobileQrBox.style.display = "none";
    if (mobileQrImage) mobileQrImage.removeAttribute("src");
    if (mobileUploadUrl) mobileUploadUrl.value = "";
    if (mobileFilesList) mobileFilesList.innerHTML = "";
    setMobileStatus("Esperando fotos del celular...");
    renderProviderInfo({});
    resultadoBody.innerHTML = '<tr class="invf-empty-row"><td colspan="5">Todavía no hay resultados.</td></tr>';
    rawResponse.textContent = "Sin respuesta todavía.";
    btnConfirmar.disabled = true;
    updateReviewSummary();
    setFlowStep("upload");
  }

  $sucursal.autocomplete({
    minLength: 0,
    delay: 0,
    source: function (request, response) {
      const term = (request.term || "").trim();
      fetch(`${window.INVF.sucursalAutocompleteUrl}?term=${encodeURIComponent(term)}&page=1`, { cache: "no-store" })
        .then(r => r.ok ? r.json() : { results: [] })
        .then(data => {
          response((data.results || []).map(item => ({
            label: item.text,
            value: item.text,
            id: item.id
          })));
        })
        .catch(() => response([]));
    },
    select: function (_event, ui) {
      $sucursal.val(ui.item.value);
      $sucursalId.val(String(ui.item.id));
      return false;
    },
    change: function (_event, ui) {
      if (!ui.item) $sucursalId.val("");
    }
  }).on("focus", function () {
    $(this).autocomplete("search", this.value || "");
  });

  resultadoBody?.addEventListener("click", async function (event) {
    const removeBtn = event.target.closest(".invf-remove-row");
    if (removeBtn) {
      const index = Number(removeBtn.dataset.index);
      if (Number.isInteger(index) && index >= 0) {
        syncQuantitiesFromTable();
        currentRows.splice(index, 1);
        renderTable(currentRows);
        showAlert("success", "Fila quitada de la revision. No se sumara al inventario.");
      }
      return;
    }

    const btn = event.target.closest(".invf-apply-barcode");
    if (!btn) return;

    const index = Number(btn.dataset.index);
    const input = resultadoBody.querySelector(`.invf-barcode[data-index="${index}"]`);
    if (!input) return;

    btn.disabled = true;
    await buscarProductoPorCodigo(index, input.value, { requireExact: false });
    btn.disabled = false;
  });

  resultadoBody?.addEventListener("keydown", async function (event) {
    const input = event.target.closest(".invf-barcode");
    if (!input || event.key !== "Enter") return;

    event.preventDefault();
    await buscarProductoPorCodigo(Number(input.dataset.index), input.value, { requireExact: true });
  });

  resultadoBody?.addEventListener("input", function (event) {
    const input = event.target.closest(".invf-qty, .invf-price");
    if (!input) return;
    const index = Number(input.dataset.index);
    if (Number.isInteger(index) && currentRows[index]) {
      if (input.classList.contains("invf-qty")) {
        currentRows[index].cantidad = parsePositiveQty(input.value, currentRows[index].cantidad || 1);
      } else {
        currentRows[index].precio_unitario = normalizePriceText(input.value);
      }
      updateConfirmButtonState();
    }
  });

  resultFilters.forEach(btn => {
    btn.addEventListener("click", function () {
      currentFilter = btn.dataset.resultFilter || "all";
      applyResultFilter();
    });
  });

  previewGrid?.addEventListener("click", function (event) {
    const card = event.target.closest(".invf-preview-card");
    if (!card) return;
    openPhotoPreview(card.dataset.previewSrc, card.dataset.previewTitle);
  });

  btnScanAdd?.addEventListener("click", function () {
    agregarProductoEscaneado();
  });

  scanAddBarcode?.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    event.stopPropagation();
    agregarProductoEscaneado();
  });

  scanAddQty?.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    event.stopPropagation();
    agregarProductoEscaneado();
  });

  providerModal?.addEventListener("click", function (event) {
    if (event.target.closest("[data-provider-modal-close]")) {
      closeProviderModal();
    }
  });

  photoPreviewModal?.addEventListener("click", function (event) {
    if (event.target.closest("[data-photo-preview-close]")) {
      closePhotoPreview();
    }
  });

  confirmSummaryModal?.addEventListener("click", function (event) {
    if (event.target.closest("[data-confirm-summary-close]")) {
      closeConfirmSummary();
    }
  });

  btnConfirmSummarySave?.addEventListener("click", async function () {
    const options = pendingConfirmOptions || { skipReview: true };
    if (confirmSummaryModal) {
      confirmSummaryModal.classList.remove("is-open");
      confirmSummaryModal.setAttribute("aria-hidden", "true");
    }
    pendingConfirmOptions = null;
    await confirmarInventario(options);
  });

  btnRestoreDraft?.addEventListener("click", restoreDraft);
  btnDiscardDraft?.addEventListener("click", function () {
    clearSavedDraft();
    showAlert("success", "Borrador descartado.");
  });

  btnProviderSave?.addEventListener("click", async function () {
    const payload = collectProviderPayload({ createIfMissing: true });
    if (!payload.nombre) {
      providerModalError.textContent = "El nombre del proveedor es obligatorio.";
      providerNameInput?.focus();
      return;
    }
    currentProvider = {
      ...currentProvider,
      ...payload,
      create_if_missing: true
    };
    renderProviderInfo(currentProvider);
    closeProviderModal();
    await confirmarInventario({ createProvider: true });
  });

  btnMobileSession?.addEventListener("click", createMobileSession);
  btnCopyMobileUrl?.addEventListener("click", copyMobileUrl);
  setupScanAddAutocomplete();
  inputFotos?.addEventListener("change", renderPreviews);
  setFlowStep("upload");
  updateReviewSummary();
  showDraftNoticeIfNeeded();
  pingAgent();
  btnProcesar?.addEventListener("click", procesarFotos);
  btnConfirmar?.addEventListener("click", confirmarInventario);
  btnLimpiar?.addEventListener("click", resetAll);
})();
