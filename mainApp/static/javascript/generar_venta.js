// static/javascript/generar_venta.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  /* ================== URLs inyectadas ================== */
  const SUCURSAL_URL    = window.sucursalAutocompleteUrl;
  const PUNTOPAGO_URL   = window.puntopagoAutocompleteUrl;
  const CLIENTE_URL     = window.clienteAutocompleteUrl;
  const PRODUCTO_URL    = window.productoAutocompleteUrl;
  const AC_CODIGO_URL   = window.productoAutocompleteCodigoUrl || PRODUCTO_URL;
  const AC_BARRAS_URL   = window.productoAutocompleteBarrasUrl || PRODUCTO_URL;
  const PRODUCTO_ID_URL = window.productoAutocompleteIdUrl || ""; // ✅ AC solo por ID
  const VERIFICAR_URL   = window.verificarProductoUrl;
  const POR_COD_URL     = window.buscarProductoPorCodigoUrl;
  const SNAPSHOT_URL    = window.productoSnapshotUrl || "/api/productos/snapshot/";
  const NEQUI_FEATURE_KEY = "nequi_api_recepcion";
  const NEQUI_DISPONIBLES_URL = window.nequiNotificacionesDisponiblesUrl || "";
  let nequiApiEnabled = window.nequiApiEnabled !== false && !!NEQUI_DISPONIBLES_URL;
  const CARRITO_LIMPIO_AUDIT_URL = window.carritoLimpioAuditUrl || "";
  const CARRITO_AUDIT_ROLE = String(window.carritoAuditRoleName || "")
    .toLowerCase()
    .replace(/[\s_-]+/g, " ")
    .trim();
  const CARRITO_LIMPIO_AUDIT_DISABLED = CARRITO_AUDIT_ROLE === "web master" || CARRITO_AUDIT_ROLE === "webmaster";

  /* ================== Agente local ================== */
  const POS_AGENT_URL   = (window.POS_AGENT_URL || "http://127.0.0.1:8787").replace(/\/+$/,'');
  const POS_AGENT_TOKEN = (window.POS_AGENT_TOKEN || "").trim();
  const IMPRIMIR_FACTURA_URL = String(window.imprimirFacturaUrl || "").trim();

  /* ================== Selectores ================== */
  const $inpCliente = $("#cliente_busqueda");
  const $inpNombre  = $("#producto_busqueda_nombre");
  const $inpId      = $("#producto_busqueda_id"); // ✅ input independiente ID
  const $inpCode    = $("#codigo_o_barras");
  const $pid        = $("#producto_id");
  const $btnAgregarBolsa = $(".btn-agregar-bolsa");
  const $promoInfo  = $("#promo-bolsas-info");

  // (si no existen, no rompen)
  const $cantidad   = $("#cantidad");
  const $agregar    = $("#agregar-producto");

  const $tbody      = $("#detalle-productos tbody");
  const $totalEl    = $("#total");
  const $buscarCart = $("#buscar-detalles");
  const $btnVaciar  = $("#vaciar-carrito");

  // ✅ pagos mixto
  const $hidPagos     = $("#pagos");      // hidden input name="pagos"
  const $hidMedioPago = $("#medio_pago"); // compat (efectivo/tarjeta/transferencia/mixto)
  const $hidEmpleadoPassword = $("#empleado_password");
  const $hidMerk2888Password = $("#codigo_descuento_merk2888");
  const $hidNequiNotification = $("#nequi_notificacion_id");

  function isClienteBusquedaElement(el) {
    return !!($inpCliente && $inpCliente.length && el === $inpCliente[0]);
  }

  // ✅ Modal refs (para total en vivo)
  const $modal      = $("#myModal");
  const $modalTotal = $("#modal-total");

  /* ================== Helpers modal state (NEW) ================== */
  function isModalOpen() {
    return !!($modal && $modal.length && $modal.is(":visible"));
  }

  // ✅ Bloquea confirmación por Enter cuando un escáner mete Enter al final
  let modalConfirmBlockUntil = 0;
  function blockModalConfirmFor(ms = 350) {
    const until = Date.now() + (ms | 0);
    if (until > modalConfirmBlockUntil) modalConfirmBlockUntil = until;
  }
  function isModalConfirmBlocked() {
    return Date.now() < modalConfirmBlockUntil;
  }

  /* ================== CSRF / Ajax ================== */
  function getCSRF() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  $.ajaxSetup({
    beforeSend: (xhr, settings) => {
      if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type)) {
        const t = getCSRF();
        if (t) xhr.setRequestHeader("X-CSRFToken", t);
      }
    },
    cache: true,
  });

  /* ================== Utils ================== */
  const hasDecimalPart = (n) => {
    const value = Number(n);
    return Number.isFinite(value) && Math.abs(value - Math.trunc(value)) > 0.000001;
  };

  const money = (n) => {
    const value = Number(n) || 0;
    const fractionDigits = hasDecimalPart(value) ? 2 : 0;
    return new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency: "COP",
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(value);
  };

  const roundAccountAmount = (n) => {
    const value = Number(n) || 0;
    if (!Number.isFinite(value) || value === 0) return 0;
    return value > 0
      ? Math.ceil(value - 0.000001)
      : Math.floor(value + 0.000001);
  };

  const onlyDigits = (s) => String(s||"").replace(/\D+/g, "");
  const norm = (s)=> (s||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();

  if (window.Promise && !Promise.prototype.finally) {
    Promise.prototype.finally = function (onFinally) {
      const P = this.constructor || Promise;
      return this.then(
        (value) => P.resolve(typeof onFinally === "function" ? onFinally() : onFinally).then(() => value),
        (reason) => P.resolve(typeof onFinally === "function" ? onFinally() : onFinally).then(() => { throw reason; })
      );
    };
  }

  function asNativePromise(value) {
    return Promise.resolve(value);
  }

  const normalizeUnits = (s) => {
    let x = norm(s);
    x = x.replace(/\bx\s*(\d+)\b/g, "x$1");
    x = x.replace(/(\d+(?:[.,]\d+)?)\s*(ml|g|gr|kg|l|lt|oz)\b/g, (m, a, u) => `${a.replace(",", ".")}${u}`);
    x = x.replace(/\s+/g, " ").trim();
    return x;
  };

  const onlyName = (s) => {
    s = String(s || "").trim();
    s = s.replace(/^[\s•·\-\u2013\u2014:|.,;]+/, "");
    let m;
    const rx = /^\s*(?:\[\s*)?([A-Za-z0-9._-]{3,}|\d{6,})(?:\s*\])?\s*(?:-|–|—|:|\|)\s*(.*)$/;
    while ((m = s.match(rx))) s = (m[2] || "").trim();
    const m2 = s.match(/^\s*\d{6,}\s+(.+)$/);
    if (m2) s = m2[1].trim();
    s = s.replace(/^[\s•·\-\u2013\u2014:|.,;]+/, "");
    return s;
  };

  const escapeHTML = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  function safeNumber(x){
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }

  // ✅ admite "1,25" -> 1.25 ; solo >0
  function parseAmt(v){
    const n = parseFloat(String(v||"").trim().replace(",", "."));
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  // ✅ 2 decimales
  function to2(n){
    const x = Number(n);
    if (!Number.isFinite(x)) return "0.00";
    return (Math.round(x * 100) / 100).toFixed(2);
  }

  function now(){ return Date.now(); }

  function classifyQuery(term){
    const raw = String(term || "").trim();
    const digits = onlyDigits(raw);
    const hasLetters = /[a-záéíóúñ]/i.test(raw);
    const compact = raw.replace(/\s+/g,"");
    const isPureDigits = digits.length > 0 && digits.length === compact.length;
    const isBarcodeLike = isPureDigits && digits.length >= 6;
    return { raw, digits, hasLetters, isPureDigits, isBarcodeLike };
  }

  /* ================== Estado persistido ================== */
  const serverSucursalID = String(
    window.ventaSucursalIdServidor || $("#sucursal_id").val() || ""
  ).match(/\d+/)?.[0] || "";
  const serverPuntoID = String(
    window.ventaPuntoPagoIdServidor || $("#puntopago_id").val() || ""
  ).match(/\d+/)?.[0] || "";
  let sucursalID = serverSucursalID;
  const hasSucursal = () => /^\d+$/.test(String(sucursalID || ""));

  (function bootstrapLockedSucursalAndPunto(){
    const domSid = String($("#sucursal_id").val() || "").match(/\d+/)?.[0] || "";
    if (domSid) {
      sucursalID = domSid;

      try {
        localStorage.setItem("sucursalID", sucursalID);
        localStorage.setItem("sucursalName", $("#sucursal_autocomplete").val() || "");
      } catch {}

    }

    const domPp = String($("#puntopago_id").val() || "").match(/\d+/)?.[0] || "";
    const domPpName = $("#puntopago_autocomplete").val() || "";
    if (domPp) {
      try {
        localStorage.setItem("puntopagoID", domPp);
        localStorage.setItem("puntopagoName", domPpName);
        localStorage.setItem("puntopagoSucursalID", sucursalID || domSid || "");
      } catch {}

    }
  })();

  /* ================== Estado venta ================== */
  const productos  = []; // ["12","99"...]
  const cantidades = []; // [ 1, -2, ... ]
  let runningTotal = 0;
  let lastAddedPid = null;
  let selectedClientLabel = "";
  let selectedEmployeeClient = {
    isEmployee: false,
    employeeName: "",
    documento: "",
    employeeHasUser: false,
    employeeIsWebMaster: false,
    isMerk2888: false
  };
  const cartAuditSessionItems = new Map();
  let cartClearNoticeDay = "";
  let cartClearNoticeCount = 0;
  let cartClearNoticeTimer = null;

  /* ================== Borrador local recuperable ==================
     Es solamente una ayuda de interfaz: la venta sigue siendo creada y
     validada exclusivamente por el servidor. Nunca se guardan contraseñas,
     claves de un solo uso, tokens ni la notificación seleccionada de Nequi. */
  const SALE_DRAFT_VERSION = 2;
  const SALE_DRAFT_PREFIX = `nova:venta-draft:v${SALE_DRAFT_VERSION}`;
  const SALE_DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
  const SALE_DRAFT_MAX_ITEMS = 500;
  const SALE_DRAFT_SAVE_DELAY_MS = 220;
  const saleDraftUserID = String(window.ventaUsuarioId || "").match(/^\d+$/)?.[0] || "";
  const saleDraftTurnID = String(window.ventaTurnoId || "sin_turno").match(/^\d+$/)?.[0] || "sin_turno";
  function createSaleDraftID(){
    try { return crypto.randomUUID(); } catch (_) {}
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  const saleDraftTabID = createSaleDraftID();
  const saleDraftLifecycle = typeof window.createSaleDraftLifecycle === "function"
    ? window.createSaleDraftLifecycle({ locks: navigator.locks, tabID: saleDraftTabID })
    : null;
  let saleDraftActiveID = saleDraftTabID;
  let saleDraftPageHidden = false;
  let saleDraftRecoveryBusy = false;
  let saleDraftAvailabilityRefreshing = false;
  let saleDraftAvailabilityAgain = false;
  let saleDraftRecoverableKeys = new Set();
  let saleDraftRecoveredFrom = [];
  let saleDraftAutosaveReady = false;
  let saleDraftRestoring = false;
  let saleDraftSaveTimer = null;
  let pendingSaleDraft = null;
  let saleDraftPaymentState = null;
  let saleDraftSubmissionPending = false;
  let saleDraftSaleConfirmed = false;
  let saleDraftSubmittedKey = "";
  let saleDraftLastStorageKey = "";
  let saleDraftOwnershipLost = false;
  let saleDraftValidationPending = false;
  let saleDraftValidationError = "";
  let saleDraftManagerSignature = "";
  let saleDraftLastAnnouncedCount = 0;
  const saleDraftInvalidProductIds = new Set();

  function saleDraftScope(){
    const pointId = String($("#puntopago_id").val() || "").match(/^\d+$/)?.[0] || "sin_punto";
    return {
      userId: saleDraftUserID,
      sucursalId: String($("#sucursal_id").val() || sucursalID || "").match(/^\d+$/)?.[0] || "",
      puntoPagoId: pointId,
      turnoId: saleDraftTurnID,
    };
  }

  function saleDraftScopeStorageKey(pointId = null){
    const scope = saleDraftScope();
    if (!scope.userId || !scope.sucursalId) return "";
    const normalizedPoint = pointId === null
      ? scope.puntoPagoId
      : (String(pointId || "").match(/^\d+$/)?.[0] || "sin_punto");
    return `${SALE_DRAFT_PREFIX}:u${scope.userId}:s${scope.sucursalId}:p${normalizedPoint}:t${scope.turnoId}`;
  }

  function saleDraftStorageKey(){
    const scopeKey = saleDraftScopeStorageKey();
    return scopeKey ? `${scopeKey}:d${saleDraftActiveID}` : "";
  }

  function saleDraftStorageKeyForPoint(pointId){
    const scopeKey = saleDraftScopeStorageKey(pointId);
    return scopeKey ? `${scopeKey}:d${saleDraftActiveID}` : "";
  }

  function saleDraftIDFromStorageKey(key){
    const scopeKey = saleDraftScopeStorageKey();
    const prefix = scopeKey ? `${scopeKey}:d` : "";
    if (!prefix || !String(key || "").startsWith(prefix)) return "";
    const candidate = String(key).slice(prefix.length);
    return /^[a-zA-Z0-9_-]{1,100}$/.test(candidate) ? candidate : "";
  }

  function isSaleDraftKeyForCurrentScope(key){
    const scopeKey = saleDraftScopeStorageKey();
    const candidate = String(key || "");
    return !!scopeKey && (candidate === scopeKey || candidate.startsWith(`${scopeKey}:d`));
  }

  function hasStoredSaleDraft(key){
    if (!key) return false;
    try {
      const raw = JSON.parse(localStorage.getItem(key) || "null");
      const updatedAt = Number(raw?.updated_at);
      return !!(
        raw
        && raw.version === SALE_DRAFT_VERSION
        && Array.isArray(raw.items)
        && raw.items.length
        && Number.isFinite(updatedAt)
        && Date.now() - updatedAt <= SALE_DRAFT_TTL_MS
      );
    } catch (_) {
      return false;
    }
  }

  function sanitizeDraftText(value, maxLength = 220){
    return String(value || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, maxLength);
  }

  function sanitizeSaleDraftItem(raw){
    const productoId = String(raw?.producto_id || "").trim();
    const cantidad = Number(raw?.cantidad);
    const precio = Number(raw?.precio);
    if (!/^\d+$/.test(productoId)) return null;
    if (!Number.isInteger(cantidad) || cantidad === 0 || Math.abs(cantidad) > 1000000) return null;
    if (!Number.isFinite(precio) || precio < 0 || precio > 1000000000000) return null;
    return {
      producto_id: productoId,
      nombre: sanitizeDraftText(raw?.nombre || `Producto ${productoId}`),
      cantidad,
      precio,
      codigo_barras: sanitizeDraftText(raw?.codigo_barras || "", 80),
    };
  }

  function sanitizeSaleDraftClient(raw){
    const id = String(raw?.id || "").trim();
    if (!/^\d+$/.test(id)) return null;
    return {
      id,
      // El autocomplete incluye el documento entre paréntesis. El borrador
      // conserva únicamente el nombre visible y vuelve a consultar el cliente.
      name: sanitizeDraftText(raw?.name || raw?.label || "")
        .replace(/\s*\([^)]*\)\s*$/, "")
        .trim(),
    };
  }

  function sanitizeSaleDraftPayment(raw){
    if (!raw || typeof raw !== "object") return null;
    const seen = new Set();
    const methods = [];
    for (const entry of Array.isArray(raw.methods) ? raw.methods : []) {
      const code = String(entry?.code || "").trim().toLowerCase();
      if (!/^[a-z0-9_]{1,40}$/.test(code) || seen.has(code)) continue;
      seen.add(code);
      const amount = Number(entry?.amount);
      methods.push({
        code,
        amount: Number.isFinite(amount) && amount > 0 && amount <= 1000000000000
          ? to2(amount)
          : "",
      });
    }
    if (!methods.length) return null;
    return { mixed: !!raw.mixed && methods.length > 1, methods };
  }

  function captureSaleDraftItems(){
    const items = [];
    for (let index = 0; index < productos.length; index += 1) {
      const productoId = String(productos[index] || "").trim();
      if (!/^\d+$/.test(productoId)) continue;
      const $row = $tbody.find(`tr[data-pid='${productoId}']`).first();
      const cached = productCache.get(productoId) || {};
      const item = sanitizeSaleDraftItem({
        producto_id: productoId,
        nombre: $row.find(".cart-product-name").first().text() || cached.nombre || `Producto ${productoId}`,
        cantidad: Number(cantidades[index]),
        precio: Number($row.data("price")) || Number(cached.price) || 0,
        codigo_barras: cached.barcode || "",
      });
      if (item) items.push(item);
    }
    return items;
  }

  function captureSaleDraftClient(){
    const id = String($("#cliente_id").val() || "").trim();
    if (!/^\d+$/.test(id)) return null;
    return sanitizeSaleDraftClient({
      id,
      name: $inpCliente.val() || selectedClientLabel,
    });
  }

  function captureSaleDraftPayment(){
    const methods = $modal.find(".pm-check:checked").not("#mix-mode").map(function(){
      const code = String(this.value || "").trim().toLowerCase();
      const $check = $(this);
      const $row = $check.closest(".mix-row, .pm-row, .payment-row, .form-check");
      const amount = $row.find(`.pm-amt[data-medio='${code}'], .pm-amt`).first().val();
      return { code, amount: String(amount || "") };
    }).get();
    return sanitizeSaleDraftPayment({
      mixed: !!$("#mix-mode").prop("checked"),
      methods,
    });
  }

  function rememberSaleDraftPaymentUi(){
    saleDraftPaymentState = captureSaleDraftPayment();
    scheduleSaleDraftSave();
  }

  function updateSaleDraftStatus(message, state = ""){
    const $status = $("#venta-draft-save-status");
    if (!$status.length) return;
    $status.removeClass("is-saved is-error");
    if (state) $status.addClass(`is-${state}`);
    $status.text(message || "El carrito se guarda automáticamente durante 24 horas en este equipo.");
  }

  function refreshSaleDraftGenerateButton(){
    const $button = $("#generar-venta");
    if (!$button.length) return;
    const serverEnabled = String($button.attr("data-server-enabled") || "0") === "1";
    const blocked = (
      !serverEnabled
      || saleDraftValidationPending
      || !!saleDraftValidationError
      || saleDraftInvalidProductIds.size > 0
    );
    $button.prop("disabled", blocked);
    if (blocked) $button.attr("aria-disabled", "true");
    else $button.removeAttr("aria-disabled");
  }

  function guardSaleDraftEditing(){
    return true;
  }

  function setSaleDraftValidation({ pending = false, error = "" } = {}){
    saleDraftValidationPending = !!pending;
    saleDraftValidationError = String(error || "");
    $("#venta-draft-retry-validation").prop("hidden", !saleDraftValidationError);
    refreshSaleDraftGenerateButton();
  }

  function saleDraftReadyToCharge({ notify = true } = {}){
    if (saleDraftValidationPending) {
      if (notify) alert("La venta recuperada todavía está validando productos y precios actuales.");
      return false;
    }
    if (saleDraftValidationError || saleDraftInvalidProductIds.size) {
      if (notify) alert(saleDraftValidationError || "Hay productos recuperados que todavía no fueron validados.");
      return false;
    }
    return true;
  }

  function closeSaleDraftPanel({ focusToggle = false } = {}){
    const $toggle = $("#venta-draft-toggle");
    $("#venta-draft-panel").prop("hidden", true);
    $toggle.attr("aria-expanded", "false");
    if (focusToggle && $toggle.is(":visible")) $toggle.trigger("focus");
  }

  function hideSaleDraftNotice(){
    pendingSaleDraft = null;
    saleDraftManagerSignature = "";
    saleDraftLastAnnouncedCount = 0;
    $("#venta-draft-center").prop("hidden", true);
    $("#venta-draft-count").text("0");
    $("#venta-draft-list").empty();
    closeSaleDraftPanel();
  }

  function removeSaleDraftByKey(key){
    if (!key) return false;
    try {
      localStorage.removeItem(key);
      return true;
    } catch (_) {
      return false;
    }
  }

  function cleanupExpiredSaleDrafts(){
    try {
      const now = Date.now();
      const keys = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key && key.startsWith("nova:venta-draft:")) keys.push(key);
      }
      for (const key of keys) {
        let raw = null;
        try { raw = JSON.parse(localStorage.getItem(key) || "null"); } catch (_) {}
        const updatedAt = Number(raw?.updated_at);
        if (
          !raw
          || !Number.isFinite(updatedAt)
          || updatedAt <= 0
          || now - updatedAt > SALE_DRAFT_TTL_MS
          || updatedAt - now > 5 * 60 * 1000
        ) removeSaleDraftByKey(key);
      }
    } catch (_) {}
  }

  function clearSaleDraftForCurrentScope(keyOverride = ""){
    if (saleDraftSaveTimer) clearTimeout(saleDraftSaveTimer);
    saleDraftSaveTimer = null;
    const key = keyOverride || saleDraftStorageKey();
    removeConsumedSaleDraftSources(readSaleDraftByKey(key));
    const removed = removeSaleDraftByKey(key);
    if (key && saleDraftLastStorageKey === key) saleDraftLastStorageKey = "";
    saleDraftInvalidProductIds.clear();
    setSaleDraftValidation();
    queueMicrotask(offerSaleDraftForCurrentScope);
    return removed;
  }

  function readSaleDraftByKey(key){
    if (!key) return null;
    let raw;
    try {
      raw = JSON.parse(localStorage.getItem(key) || "null");
    } catch (_) {
      removeSaleDraftByKey(key);
      return null;
    }
    if (!raw || raw.version !== SALE_DRAFT_VERSION) {
      if (raw) removeSaleDraftByKey(key);
      return null;
    }

    const scope = saleDraftScope();
    const updatedAt = Number(raw.updated_at);
    const createdAt = Number(raw.created_at);
    if (
      String(raw.user_id || "") !== scope.userId
      || String(raw.sucursal_id || "") !== scope.sucursalId
      || String(raw.puntopago_id || "") !== scope.puntoPagoId
      || String(raw.turno_id || "") !== scope.turnoId
      || !Number.isFinite(updatedAt)
      || updatedAt <= 0
      || Date.now() - updatedAt > SALE_DRAFT_TTL_MS
      || updatedAt - Date.now() > 5 * 60 * 1000
    ) {
      removeSaleDraftByKey(key);
      return null;
    }

    if (!Array.isArray(raw.items) || raw.items.length > SALE_DRAFT_MAX_ITEMS) {
      removeSaleDraftByKey(key);
      return null;
    }
    const items = raw.items
      .map(sanitizeSaleDraftItem)
      .filter(Boolean);
    if (!items.length) {
      removeSaleDraftByKey(key);
      return null;
    }

    return {
      version: SALE_DRAFT_VERSION,
      draft_id: sanitizeDraftText(raw.draft_id || saleDraftIDFromStorageKey(key), 100),
      storage_key: key,
      user_id: scope.userId,
      sucursal_id: scope.sucursalId,
      puntopago_id: scope.puntoPagoId,
      turno_id: scope.turnoId,
      owner_tab_id: sanitizeDraftText(raw.owner_tab_id || "", 100),
      presence_version: raw.presence_version === 1 ? 1 : 0,
      recovered_from: (Array.isArray(raw.recovered_from) ? raw.recovered_from : [raw.recovered_from])
        .filter((source) => typeof source === "string" && source !== key && isSaleDraftKeyForCurrentScope(source))
        .slice(0, 500),
      created_at: Number.isFinite(createdAt) && createdAt > 0 ? createdAt : updatedAt,
      updated_at: updatedAt,
      status: raw.status === "submission_pending" ? "submission_pending" : "active",
      items,
      client: sanitizeSaleDraftClient(raw.client),
      payment: sanitizeSaleDraftPayment(raw.payment),
    };
  }

  function listSaleDraftsForCurrentScope(){
    const scopeKey = saleDraftScopeStorageKey();
    if (!scopeKey) return [];
    const keys = [];
    try {
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (isSaleDraftKeyForCurrentScope(key)) keys.push(key);
      }
    } catch (_) {
      return [];
    }

    return keys
      .map(readSaleDraftByKey)
      .filter(Boolean)
      .sort((left, right) => {
        const leftUncertain = left.status === "submission_pending" ? 1 : 0;
        const rightUncertain = right.status === "submission_pending" ? 1 : 0;
        return rightUncertain - leftUncertain || right.updated_at - left.updated_at;
      });
  }

  function readSaleDraftForCurrentScope(){
    const drafts = listSaleDraftsForCurrentScope();
    if (!drafts.length) return null;
    return { ...drafts[0], pending_count: drafts.length };
  }

  function removeConsumedSaleDraftSources(draft){
    for (const key of draft?.recovered_from || []) {
      if (isSaleDraftKeyForCurrentScope(key) && key !== draft.storage_key) removeSaleDraftByKey(key);
    }
  }

  function persistSaleDraftNow(){
    if (!saleDraftAutosaveReady || saleDraftRestoring || saleDraftPageHidden) return false;
    let key = saleDraftStorageKey();
    if (!key) return false;

    if (saleDraftOwnershipLost) {
      updateSaleDraftStatus(
        "Otra pestaña tomó control de esta venta. Recarga la página antes de continuar.",
        "error",
      );
      return false;
    }

    if (saleDraftSaleConfirmed) {
      removeConsumedSaleDraftSources(readSaleDraftByKey(key));
      removeSaleDraftByKey(key);
      return true;
    }

    if (productos.length > SALE_DRAFT_MAX_ITEMS) {
      updateSaleDraftStatus(
        `No se guardó el borrador: supera el límite de ${SALE_DRAFT_MAX_ITEMS} productos distintos.`,
        "error",
      );
      return false;
    }
    const items = captureSaleDraftItems();
    if (
      items.length
      && pendingSaleDraft
      && pendingSaleDraft.storage_key === key
    ) {
      // Si el cajero empieza otra venta sin recuperar la ofrecida, el carrito
      // nuevo recibe su propia clave y jamás sobrescribe el pendiente.
      saleDraftActiveID = createSaleDraftID();
      key = saleDraftStorageKey();
      saleDraftOwnershipLost = false;
    }
    if (!items.length) {
      if (pendingSaleDraft?.storage_key === key && !saleDraftSaleConfirmed) {
        queueMicrotask(offerSaleDraftForCurrentScope);
        return true;
      }
      removeConsumedSaleDraftSources(readSaleDraftByKey(key));
      removeSaleDraftByKey(key);
      if (saleDraftLastStorageKey === key) saleDraftLastStorageKey = "";
      saleDraftRecoveredFrom = [];
      saleDraftInvalidProductIds.clear();
      setSaleDraftValidation();
      updateSaleDraftStatus(
        pendingSaleDraft
          ? "La venta pendiente sigue guardada. Puedes facturar otra normalmente."
          : "El carrito se guarda automáticamente durante 24 horas en este equipo."
      );
      queueMicrotask(offerSaleDraftForCurrentScope);
      return true;
    }

    let createdAt = Date.now();
    let previous = null;
    try {
      previous = JSON.parse(localStorage.getItem(key) || "null");
      if (previous && Number.isFinite(Number(previous.created_at))) {
        createdAt = Number(previous.created_at);
      }
    } catch (_) {}

    const previousOwner = sanitizeDraftText(previous?.owner_tab_id || "", 100);
    if (
      previous
      && Array.isArray(previous.items)
      && previous.items.length
      && previousOwner
      && previousOwner !== saleDraftTabID
    ) {
      const conflict = "Otra pestaña tiene un borrador activo para esta caja. Recarga la página y elige cuál recuperar antes de continuar.";
      setSaleDraftValidation({ error: conflict });
      updateSaleDraftStatus(conflict, "error");
      return false;
    }

    saleDraftPaymentState = captureSaleDraftPayment() || saleDraftPaymentState;
    const scope = saleDraftScope();
    const payload = {
      version: SALE_DRAFT_VERSION,
      draft_id: saleDraftActiveID,
      user_id: scope.userId,
      sucursal_id: scope.sucursalId,
      puntopago_id: scope.puntoPagoId,
      turno_id: scope.turnoId,
      owner_tab_id: saleDraftTabID,
      presence_version: saleDraftLifecycle?.held ? 1 : 0,
      recovered_from: saleDraftRecoveredFrom,
      created_at: createdAt,
      updated_at: Date.now(),
      status: saleDraftSubmissionPending ? "submission_pending" : "active",
      items,
      client: captureSaleDraftClient(),
      payment: sanitizeSaleDraftPayment(saleDraftPaymentState),
    };

    try {
      localStorage.setItem(key, JSON.stringify(payload));
      saleDraftOwnershipLost = false;
      saleDraftLastStorageKey = key;
      const stamp = new Date(payload.updated_at).toLocaleTimeString("es-CO", {
        hour: "2-digit",
        minute: "2-digit",
      });
      updateSaleDraftStatus(`Borrador guardado en este equipo a las ${stamp}.`, "saved");
      queueMicrotask(offerSaleDraftForCurrentScope);
      return true;
    } catch (_) {
      updateSaleDraftStatus("No se pudo guardar el borrador en este navegador.", "error");
      return false;
    }
  }

  function scheduleSaleDraftSave(){
    if (!saleDraftAutosaveReady || saleDraftRestoring) return;
    if (saleDraftSaveTimer) clearTimeout(saleDraftSaveTimer);
    saleDraftSaveTimer = setTimeout(() => {
      saleDraftSaveTimer = null;
      persistSaleDraftNow();
    }, SALE_DRAFT_SAVE_DELAY_MS);
  }

  function listManagedSaleDrafts(){
    const activeKey = productos.length ? saleDraftStorageKey() : "";
    return listSaleDraftsForCurrentScope().filter(
      (draft) => draft.storage_key !== activeKey && saleDraftRecoverableKeys.has(draft.storage_key),
    );
  }

  function findManagedSaleDraft(storageKey){
    const key = String(storageKey || "");
    if (!key) return null;
    return listManagedSaleDrafts().find((draft) => draft.storage_key === key) || null;
  }

  function renderSaleDraftManager(){
    if (!saleDraftAutosaveReady || saleDraftRestoring) return;
    const drafts = listManagedSaleDrafts();
    if (!drafts.length) {
      hideSaleDraftNotice();
      return;
    }

    pendingSaleDraft = drafts[0];
    const count = drafts.length;
    const hasWarning = drafts.some((draft) => draft.status === "submission_pending");
    const signature = drafts
      .map((draft) => `${draft.storage_key}:${draft.updated_at}:${draft.status}`)
      .join("|");
    const $center = $("#venta-draft-center");
    const $toggle = $("#venta-draft-toggle");
    $("#venta-draft-count").text(String(count));
    $toggle
      .toggleClass("has-warning", hasWarning)
      .attr(
        "aria-label",
        `${count} ${count === 1 ? "venta pendiente" : "ventas pendientes"}`,
      );
    $center.prop("hidden", false);

    if (signature !== saleDraftManagerSignature) {
      const $list = $("#venta-draft-list").empty();
      for (const draft of drafts) {
        const uncertain = draft.status === "submission_pending";
        const itemCount = draft.items.length;
        const total = draft.items.reduce(
          (sum, item) => sum + Number(item.cantidad || 0) * Number(item.precio || 0),
          0,
        );
        const when = new Date(draft.updated_at).toLocaleString("es-CO", {
          dateStyle: "short",
          timeStyle: "short",
        });
        const productNames = draft.items
          .slice(0, 2)
          .map((item) => item.nombre || `Producto ${item.producto_id}`)
          .join(", ");
        const remaining = Math.max(0, itemCount - 2);
        const clientName = draft.client?.name ? ` · ${draft.client.name}` : "";

        const $item = $("<article>", {
          class: `venta-draft-item${uncertain ? " is-warning" : ""}`,
          role: "listitem",
        });
        const $copy = $("<div>", { class: "venta-draft-item-copy" });
        const $title = $("<span>", { class: "venta-draft-item-title" });
        $title.append(
          $("<i>", {
            class: uncertain ? "fa-solid fa-triangle-exclamation" : "fa-solid fa-cart-shopping",
            "aria-hidden": "true",
          }),
          $("<span>").text(uncertain ? "Confirmación pendiente" : `Guardada ${when}`),
        );
        $copy.append(
          $title,
          $("<small>", { class: "venta-draft-item-meta" }).text(
            `${itemCount} ${itemCount === 1 ? "producto" : "productos"} · ${money(total)}${clientName}`,
          ),
          $("<small>", { class: "venta-draft-item-products" }).text(
            `${productNames}${remaining ? ` y ${remaining} más` : ""}`,
          ),
        );

        const $actions = $("<div>", { class: "venta-draft-item-actions" });
        const $restore = $("<button>", {
          type: "button",
          class: "venta-draft-item-action js-draft-restore",
          title: "Recuperar productos como una venta nueva",
          "aria-label": `Copiar los productos de la venta guardada ${when} a una venta nueva`,
        }).attr("data-draft-key", draft.storage_key).append(
          $("<i>", { class: "fa-solid fa-rotate-left", "aria-hidden": "true" }),
        );
        const $discard = $("<button>", {
          type: "button",
          class: "venta-draft-item-action is-danger js-draft-discard",
          title: "Descartar esta venta",
          "aria-label": `Descartar venta guardada ${when}`,
        }).attr("data-draft-key", draft.storage_key).append(
          $("<i>", { class: "fa-solid fa-trash-can", "aria-hidden": "true" }),
        );
        $actions.append($restore, $discard);
        $item.append($copy, $actions);
        $list.append($item);
      }
      saleDraftManagerSignature = signature;
    }

    if (saleDraftLastAnnouncedCount !== count) {
      $("#venta-draft-live").text(
        `${count} ${count === 1 ? "venta pendiente guardada" : "ventas pendientes guardadas"}.`,
      );
      saleDraftLastAnnouncedCount = count;
    }
  }

  function offerSaleDraftForCurrentScope(){
    renderSaleDraftManager();
    void refreshSaleDraftAvailability();
  }

  async function refreshSaleDraftAvailability(){
    if (saleDraftPageHidden || !saleDraftAutosaveReady) return;
    if (saleDraftAvailabilityRefreshing) { saleDraftAvailabilityAgain = true; return; }
    saleDraftAvailabilityRefreshing = true;
    try {
      const owners = await saleDraftLifecycle?.openOwners();
      if (!owners || saleDraftPageHidden) return;
      const drafts = listSaleDraftsForCurrentScope();
      // Una transferencia interrumpida después de guardar el destino no debe
      // ofrecer también el origen. El respaldo nuevo contiene todos los datos.
      const copiedSources = new Set(drafts.flatMap((draft) => draft.recovered_from));
      saleDraftRecoverableKeys = new Set(drafts.filter((draft) => (
        !owners.has(draft.owner_tab_id)
        && draft.owner_tab_id !== saleDraftTabID
        && !copiedSources.has(draft.storage_key)
      )).map((draft) => draft.storage_key));
      renderSaleDraftManager();
    } catch (_) {
      // Si no podemos comprobar las pestañas, no arriesgar una recuperación
      // duplicada. Esto no bloquea las ventas normales ni su respaldo local.
      saleDraftRecoverableKeys.clear();
      renderSaleDraftManager();
    } finally {
      saleDraftAvailabilityRefreshing = false;
      if (saleDraftAvailabilityAgain) {
        saleDraftAvailabilityAgain = false;
        queueMicrotask(refreshSaleDraftAvailability);
      }
    }
  }

  async function resumeSaleDraftPage(){
    const ready = await saleDraftLifecycle?.start();
    if (!ready) return;
    if (saleDraftPageHidden && productos.length) {
      const draft = readSaleDraftByKey(saleDraftStorageKey());
      if (!draft || draft.owner_tab_id !== saleDraftTabID) {
        // No revivir una venta ya recuperada/facturada al volver con Atrás.
        clearCartAndTotals();
        saleDraftActiveID = createSaleDraftID();
        saleDraftLastStorageKey = "";
        saleDraftRecoveredFrom = [];
        saleDraftOwnershipLost = false;
        setSaleDraftValidation();
        updateSaleDraftStatus("Ese respaldo ya se recuperó o se descartó. Puedes iniciar otra venta.");
      }
    }
    saleDraftPageHidden = false;
    persistSaleDraftNow();
    offerSaleDraftForCurrentScope();
  }

  function suspendSaleDraftPage(){
    try { persistSaleDraftNow(); } catch (_) {}
    saleDraftPageHidden = true;
    saleDraftLifecycle?.stop();
  }

  function continueWithNewSale(){
    const pendingCount = listManagedSaleDrafts().length;
    if (!productos.length) {
      saleDraftActiveID = createSaleDraftID();
      saleDraftLastStorageKey = "";
      saleDraftRecoveredFrom = [];
      saleDraftOwnershipLost = false;
    }
    closeSaleDraftPanel(true);
    renderSaleDraftManager();
    updateSaleDraftStatus(
      pendingCount > 1
        ? `${pendingCount} ventas pendientes siguen guardadas. Puedes facturar normalmente.`
        : "La venta pendiente sigue guardada. Puedes facturar normalmente.",
      "saved",
    );
    queueMicrotask(() => {
      if ($inpCode?.length && $inpCode.is(":visible")) $inpCode.trigger("focus");
      else if ($inpNombre?.length) $inpNombre.trigger("focus");
    });
  }

  function applySaleDraftPaymentState(raw){
    const payment = sanitizeSaleDraftPayment(raw);
    if (!payment) return false;
    const available = [];
    for (const entry of payment.methods) {
      const $check = $modal.find(`.pm-check[value='${entry.code}']`).not("#mix-mode").first();
      if (!$check.length || $check.prop("disabled")) continue;
      $check.prop("checked", true);
      available.push({ ...entry, $check });
    }
    if (!available.length) return false;

    const mixed = payment.mixed && available.length > 1;
    $("#mix-mode").prop("checked", mixed);
    if (!mixed && available.length > 1) {
      available.slice(1).forEach(({ $check }) => $check.prop("checked", false));
    }
    if (mixed) {
      for (const entry of available) {
        const $row = entry.$check.closest(".mix-row, .pm-row, .payment-row, .form-check");
        const $amount = $row.find(`.pm-amt[data-medio='${entry.code}'], .pm-amt`).first();
        if ($amount.length && entry.amount) $amount.val(entry.amount);
      }
    }
    // Una asociación concreta de Nequi puede quedar obsoleta o ser consumida;
    // por eso siempre se exige escogerla de nuevo.
    $hidNequiNotification.val("");
    return true;
  }

  async function mapSaleDraftWithConcurrency(items, worker, concurrency = 6){
    const results = new Array(items.length);
    let nextIndex = 0;
    const runners = Array.from(
      { length: Math.min(Math.max(1, concurrency), Math.max(1, items.length)) },
      async () => {
        while (nextIndex < items.length) {
          const index = nextIndex;
          nextIndex += 1;
          results[index] = await worker(items[index], index);
        }
      },
    );
    await Promise.all(runners);
    return results;
  }

  function verifyRestoredSaleDraftItem(item){
    return asNativePromise($.post(VERIFICAR_URL, {
      producto_id: item.producto_id,
      cantidad: item.cantidad,
      sucursal_id: sucursalID,
      _ts: Date.now(),
    })).then((response) => ({ item, response }));
  }

  async function revalidateRestoredSaleDraft(draft){
    const items = (Array.isArray(draft?.items) ? draft.items : captureSaleDraftItems())
      .map(sanitizeSaleDraftItem)
      .filter(Boolean);
    if (!items.length) {
      saleDraftInvalidProductIds.clear();
      setSaleDraftValidation();
      return true;
    }

    saleDraftInvalidProductIds.clear();
    setSaleDraftValidation({ pending: true });
    updateSaleDraftStatus("Venta recuperada. Validando existencias, cantidades, precios y cliente actuales…");

    try {
      const productResults = await mapSaleDraftWithConcurrency(
        items,
        verifyRestoredSaleDraftItem,
        6,
      );
      const restoredClient = draft?.client
        ? await fetchSaleDraftClientById(draft.client.id)
        : null;

      const invalidItems = [];
      const validItems = [];
      for (const result of productResults) {
        const item = result.item;
        const response = result.response || {};
        const price = Number(response.precio_unitario);
        const available = Number(response.cantidad_disponible);
        const insufficient = item.cantidad > 0 && (
          !Number.isFinite(available) || available < item.cantidad
        );
        if (!response.exists || insufficient || !Number.isFinite(price) || price < 0) {
          invalidItems.push(item);
        } else {
          validItems.push({ item, response, price });
        }
      }

      saleDraftRestoring = true;
      try {
        for (const { item, response, price } of validItems) {
          const pid = String(item.producto_id);
          const $row = $tbody.find(`tr[data-pid='${pid}']`).first();
          if (!$row.length) continue;
          updateCache(pid, response);
          const liveName = sanitizeDraftText(response.nombre || item.nombre || `Producto ${pid}`);
          $row.find(".cart-product-name").first().text(liveName);
          setRowPriceUI($row, price);
          $row.find(".subtotal-cell").text(money(price * item.cantidad));
          $row.data("counted", true);
        }
        for (const item of invalidItems) {
          saleDraftInvalidProductIds.add(String(item.producto_id));
          removeRowByPid(item.producto_id);
        }

        if (draft?.client) {
          if (restoredClient) applySelectedClientItem(restoredClient);
          else clearSelectedClient();
        }
        enforceTotalIntegrity();
        syncHiddenFieldsNow();
      } finally {
        saleDraftRestoring = false;
      }

      const removedCount = invalidItems.length;
      saleDraftInvalidProductIds.clear();
      setSaleDraftValidation();
      persistSaleDraftNow();
      if (removedCount) {
        updateSaleDraftStatus(
          `Venta recuperada y actualizada. Se quitaron ${removedCount} producto(s) inexistentes o sin cantidad suficiente.`,
          "error",
        );
      } else if (draft?.client && !restoredClient) {
        updateSaleDraftStatus("Venta recuperada. El cliente guardado ya no existe y se quitó de la venta.", "error");
      } else {
        updateSaleDraftStatus("Productos recuperados en una venta nueva e independiente, con precios y existencias actuales.", "saved");
      }
      return true;
    } catch (_) {
      const message = "No se pudo validar la venta recuperada. Revisa la conexión y pulsa Revalidar antes de cobrar.";
      setSaleDraftValidation({ error: message });
      updateSaleDraftStatus(message, "error");
      return false;
    }
  }

  async function restorePendingSaleDraft(draftOverride = null){
    if (saleSubmitting || confirmSubmitting || saleDraftSaleConfirmed || saleDraftRestoring || saleDraftValidationPending) return;
    if (saleDraftRecoveryBusy || saleDraftPageHidden) return;
    if (productos.length) {
      alert("Para recuperar, abre una pestaña con el carrito vacío. Tu venta actual no se cambiará.");
      return;
    }
    const selectedDraft = draftOverride || pendingSaleDraft;
    if (!selectedDraft) return;
    const sourceKey = String(selectedDraft.storage_key || "");
    const selected = readSaleDraftByKey(sourceKey);
    if (!selected) { offerSaleDraftForCurrentScope(); return; }
    if (!saleDraftLifecycle?.supported) {
      alert("Este navegador no permite comprobar qué ventas siguen abiertas. Usa un navegador actualizado con HTTPS o localhost para recuperar sin duplicarlas.");
      return;
    }
    saleDraftRecoveryBusy = true;
    try {
      const recovered = await saleDraftLifecycle.withClosedDraft(selected, () => {
        // Releer dentro del bloqueo: otra pestaña pudo consumirlo primero.
        const draft = readSaleDraftByKey(sourceKey);
        if (!draft || draft.owner_tab_id !== selected.owner_tab_id) return false;
        if (listSaleDraftsForCurrentScope().some((other) => other.recovered_from.includes(sourceKey))) return false;
        if (productos.length || saleDraftPageHidden || saleSubmitting || confirmSubmitting || saleDraftSaleConfirmed || saleDraftValidationPending) return false;
        return loadClosedSaleDraft(draft);
      });
      if (recovered === false) {
        updateSaleDraftStatus("Ese respaldo sigue abierto en otra pestaña o ya fue recuperado.");
      }
    } catch (_) {
      updateSaleDraftStatus("No se pudo recuperar con seguridad. El respaldo no se ha descartado.", "error");
    } finally {
      saleDraftRecoveryBusy = false;
      offerSaleDraftForCurrentScope();
    }
  }

  function loadClosedSaleDraft(draft){
    const sourceKey = draft.storage_key;
    // Compatibilidad con respaldos anteriores: aún no tenían señal de vida.
    // Solo el usuario puede confirmar que cerró sus pestañas de la versión vieja.
    if (!draft.presence_version && !confirm("Este respaldo es de una versión anterior. ¿Confirmas que cerraste la pestaña que tenía esta venta? No lo recuperes si sigue abierta.")) return;
    if (
      draft.status === "submission_pending"
      && !confirm("Esta venta tiene un cobro sin confirmar y pudo haberse registrado. Revisa primero Visualizar ventas. ¿Confirmaste que NO está facturada y quieres copiar sus productos a una venta nueva?")
    ) return;

    // Recuperar equivale a agregar los productos en otro carrito. Nunca tomar
    // el identificador, la propiedad ni los pagos del borrador de origen.
    saleDraftActiveID = createSaleDraftID();
    saleDraftLastStorageKey = "";
    saleDraftSubmittedKey = "";
    saleDraftSaleConfirmed = false;
    pendingSaleDraft = null;

    saleDraftRestoring = true;
    saleDraftOwnershipLost = false;
    if (saleDraftSaveTimer) clearTimeout(saleDraftSaveTimer);
    saleDraftSaveTimer = null;
    try {
      clearCartAndTotals();
      saleDraftRecoveredFrom = [...new Set([sourceKey, ...draft.recovered_from])].slice(0, 500);
      saleDraftPaymentState = null;
      saleDraftSubmissionPending = false;
      selectedClientLabel = "";
      selectedEmployeeClient = {
        isEmployee: false,
        employeeName: "",
        documento: "",
        employeeHasUser: false,
        employeeIsWebMaster: false,
        isMerk2888: false,
      };
      $("#cliente_id").val("");
      $inpCliente.val("");

      for (const item of draft.items) {
        updateCache(item.producto_id, {
          nombre: item.nombre,
          barcode: item.codigo_barras,
          precio_unitario: item.precio,
        });
        insertOrUpdateRowInstant(
          item.producto_id,
          item.cantidad,
          `Producto ${item.producto_id}`,
          item.precio,
        );
        const $row = $tbody.find(`tr[data-pid='${item.producto_id}']`).first();
        $row.find(".cart-product-name").first().text(item.nombre || `Producto ${item.producto_id}`);
        rememberCartAuditRow($row);
      }

      if (draft.client) {
        selectedClientLabel = draft.client.name;
        selectedEmployeeClient = {
          isEmployee: false,
          employeeName: "",
          documento: "",
          employeeHasUser: false,
          employeeIsWebMaster: false,
          isMerk2888: false,
        };
        $("#cliente_id").val(draft.client.id);
        $inpCliente.val(draft.client.name);
      }

      // Las credenciales siempre quedan vacías y deben autorizarse otra vez.
      $("#employee-password-input").val("");
      $hidEmpleadoPassword.val("");
      $("#merk2888-password-input").val("");
      $hidMerk2888Password.val("");
      $hidNequiNotification.val("");
      refreshEmployeeDiscountUI();
      enforceTotalIntegrity();
      syncHiddenFieldsNow();
      lastAddedPid = draft.items.length
        ? String(draft.items[draft.items.length - 1].producto_id)
        : null;
      closeSaleDraftPanel();
    } finally {
      saleDraftRestoring = false;
    }

    // Primero guardar íntegramente el carrito nuevo, luego consumir el origen.
    // recovered_from evita ofrecer ambos si el navegador termina entre escrituras.
    const saved = persistSaleDraftNow();
    if (!saved || !removeSaleDraftByKey(sourceKey)) {
      if (saved) removeSaleDraftByKey(saleDraftStorageKey());
      clearCartAndTotals();
      saleDraftRecoveredFrom = [];
      saleDraftLastStorageKey = "";
      alert("No se pudo completar la recuperación. El respaldo original sigue guardado; inténtalo de nuevo.");
      return;
    }
    removeConsumedSaleDraftSources(draft);
    saleDraftRecoverableKeys.delete(sourceKey);
    saleDraftManagerSignature = "";
    offerSaleDraftForCurrentScope();
    void revalidateRestoredSaleDraft(draft);
    return true;
  }

  async function discardPendingSaleDraft(draftOverride = null){
    const draft = draftOverride || pendingSaleDraft;
    if (!draft) return;
    if (!confirm("¿Descartar este borrador local? Esto no elimina ninguna venta ya registrada.")) return;
    try {
      await saleDraftLifecycle?.withClosedDraft(draft, () => {
        const current = readSaleDraftByKey(draft.storage_key);
        if (!current || current.owner_tab_id !== draft.owner_tab_id) return false;
        if (!current.presence_version && !confirm("¿Confirmas que la pestaña de este respaldo antiguo está cerrada?")) return false;
        removeConsumedSaleDraftSources(current);
        if (!removeSaleDraftByKey(draft.storage_key)) return false;
        saleDraftRecoverableKeys.delete(draft.storage_key);
        pendingSaleDraft = null;
        saleDraftManagerSignature = "";
        updateSaleDraftStatus("Venta pendiente descartada. Tu venta actual no cambió.");
        return true;
      });
    } catch (_) { updateSaleDraftStatus("No se pudo descartar el respaldo.", "error"); }
    offerSaleDraftForCurrentScope();
  }

  const PROMO_BAG_21 = "7318";
  const PROMO_BAG_8001 = "8001";
  const PROMO_BLOCK_COP = 11000;

  const defer = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 150 }) : setTimeout(fn, 0));
  function syncHiddenFieldsNow() {
    // ✅ Para cerrar venta rápido y seguro: antes del submit no esperamos al idle callback.
    $("#productos").val(JSON.stringify(productos));
    $("#cantidades").val(JSON.stringify(cantidades));
  }
  function syncHiddenFields() {
    defer(syncHiddenFieldsNow);
  }

  function isEmployeeClientSelected() {
    return !!(selectedEmployeeClient.isEmployee && String($("#cliente_id").val() || "").trim());
  }

  function isWebMasterEmployeeClientSelected() {
    return !!(
      isEmployeeClientSelected()
      && selectedEmployeeClient.employeeIsWebMaster
    );
  }

  function isMerk2888ClientSelected() {
    return !!(
      selectedEmployeeClient.isMerk2888
      && String($("#cliente_id").val() || "").trim()
    );
  }

  function employeeDiscountAmount() {
    const base = roundAccountAmount(runningTotal);
    if (base <= 0) return 0;
    if (isMerk2888ClientSelected()) return base;
    if (!isEmployeeClientSelected()) return 0;
    if (isWebMasterEmployeeClientSelected()) return base;
    return roundAccountAmount(base * 0.10);
  }

  function saleTotalForPayment() {
    const base = roundAccountAmount(runningTotal);
    if (base <= 0) return base;
    return roundAccountAmount(base - employeeDiscountAmount());
  }

  function refreshEmployeeDiscountUI() {
    const active = isEmployeeClientSelected();
    const merk2888Active = isMerk2888ClientSelected();
    const $box = $("#employee-discount-auth");
    const $merkBox = $("#merk2888-discount-auth");
    const $summary = $("#employee-discount-summary");
    const discount = employeeDiscountAmount();
    const totalWithDiscount = saleTotalForPayment();

    $box.toggle(active && !merk2888Active);
    $merkBox.toggle(merk2888Active);
    $totalEl.text(money(totalWithDiscount));
    if (!active) {
      $("#employee-password-input").val("");
      $hidEmpleadoPassword.val("");
    }
    if (!merk2888Active) {
      $("#merk2888-password-input").val("");
      $hidMerk2888Password.val("");
    } else {
      $("#merk2888-discount-summary").text(
        `Beneficio del 100% (${money(discount)}). Total a pagar: ${money(0)}.`
      );
    }

    if (!active || merk2888Active) return;

    const employeeName = selectedEmployeeClient.employeeName || "Empleado";
    const webMasterBenefit = isWebMasterEmployeeClientSelected();
    $box.find("strong").first().text(
      webMasterBenefit ? "Beneficio Web Master" : "Descuento de empleado"
    );
    if (webMasterBenefit) {
      $summary.text(`${employeeName}: beneficio Web Master del 100% (${money(discount)}). Total a pagar: ${money(0)}.`);
    } else {
      $summary.text(`${employeeName}: descuento 10% (${money(discount)}). Total con descuento: ${money(totalWithDiscount)}.`);
    }
  }

  function setTotal(v) {
    const safe = Number(v);
    runningTotal = Number.isFinite(safe) ? safe : 0;
    $totalEl.text(money(saleTotalForPayment()));

    if ($modal && $modal.length && $modal.is(":visible") && $modalTotal && $modalTotal.length) {
      refreshEmployeeDiscountUI();
      $modalTotal.text(money(saleTotalForPayment()));
    }
    syncHiddenFields();
    scheduleSaleDraftSave();
  }
  function addToTotal(delta) {
    setTotal((Number(runningTotal) || 0) + (Number(delta) || 0));
  }

  function computeBagPromoBreakdown(rows){
    const rowByPid = new Map(rows.map(r => [String(r.pid || ""), r]));
    const bag21 = rowByPid.get(PROMO_BAG_21);
    const bag8001 = rowByPid.get(PROMO_BAG_8001);

    const price21 = Math.max(0, safeNumber(bag21?.price));
    const price8001 = Math.max(0, safeNumber(bag8001?.price));

    let remaining21 = Math.max(0, Math.trunc(safeNumber(bag21?.qty)));
    let remaining8001 = Math.max(0, Math.trunc(safeNumber(bag8001?.qty)));

    const baseTotal = rows.reduce((sum, r) => {
      const pid = String(r.pid || "");
      if (pid === PROMO_BAG_21 || pid === PROMO_BAG_8001) return sum;
      const qty = Math.trunc(safeNumber(r.qty));
      const price = safeNumber(r.price);
      if (qty <= 0 || price <= 0) return sum;
      return sum + (qty * price);
    }, 0);

    const blocksGranted = Math.max(0, Math.floor(baseTotal / PROMO_BLOCK_COP));
    let blocks = blocksGranted;
    let free21 = 0;
    let free8001 = 0;

    while (blocks > 0 && (remaining21 > 0 || remaining8001 > 0)) {
      const value21 = (remaining21 > 0 && price21 > 0) ? (Math.min(2, remaining21) * price21) : -1;
      const value8001 = (remaining8001 > 0 && price8001 > 0) ? price8001 : -1;
      if (value21 <= 0 && value8001 <= 0) break;
      if (value8001 > value21) {
        free8001 += 1;
        remaining8001 -= 1;
      } else {
        const take21 = Math.min(2, remaining21);
        free21 += take21;
        remaining21 -= take21;
      }
      blocks -= 1;
    }

    return {
      baseTotal,
      blocksGranted,
      free21,
      free8001,
      discount: (free21 * price21) + (free8001 * price8001),
    };
  }

  function applyPromoUiAndComputeTotal({ updateUI = true } = {}) {
    const rows = [];
    $tbody.find("tr").each(function(){
      const $r = $(this);
      const pid = String($r.data("pid") || "");
      const qtyRaw = $r.attr("data-qty") || $r.find(".qty-input").val() || "0";
      const qty = parseInt(String(qtyRaw).trim(), 10);
      const price = safeNumber($r.data("price"));
      rows.push({ $r, pid, qty: Number.isFinite(qty) ? qty : 0, price });
    });

    const promo = computeBagPromoBreakdown(rows);
    let total = 0;

    for (const row of rows) {
      const qty = Math.trunc(safeNumber(row.qty));
      const price = safeNumber(row.price);
      let freeQty = 0;
      let subtotal = 0;

      if (price > 0) {
        if (row.pid === PROMO_BAG_21 && qty > 0) {
          freeQty = Math.min(qty, promo.free21);
          subtotal = (qty - freeQty) * price;
        } else if (row.pid === PROMO_BAG_8001 && qty > 0) {
          freeQty = Math.min(qty, promo.free8001);
          subtotal = (qty - freeQty) * price;
        } else {
          subtotal = qty * price;
        }
      }

      total += subtotal;

      if (updateUI) {
        row.$r.attr("data-free-qty", freeQty);
        row.$r.find(".promo-bolsa-badge").remove();
        row.$r.removeClass("promo-free-line promo-partial-line");
        row.$r.find(".subtotal-cell").text(price > 0 ? money(subtotal) : "…");

        if ((row.pid === PROMO_BAG_21 || row.pid === PROMO_BAG_8001) && freeQty > 0) {
          const isAllFree = qty > 0 && freeQty >= qty;
          const badgeText = isAllFree ? "Gratis" : `Gratis: ${freeQty}`;
          row.$r.children("td").first().append(` <small class="promo-bolsa-badge">${badgeText}</small>`);
          row.$r.addClass(isAllFree ? "promo-free-line" : "promo-partial-line");
        }
      }
    }

    if (updateUI && $promoInfo.length) {
      const chunks = [];
      if (promo.blocksGranted > 0) chunks.push(`Bloques disponibles: ${promo.blocksGranted}`);
      if (promo.free21 > 0) chunks.push(`Bolsa grande gratis: ${promo.free21}`);
      if (promo.free8001 > 0) chunks.push(`Bolsa de cuero de vaca gratis: ${promo.free8001}`);
      if (promo.discount > 0) chunks.push(`Descuento aplicado: ${money(promo.discount)}`);
      $promoInfo.text(chunks.length ? chunks.join(" • ") : "Promo bolsas: por cada $11.000 en productos distintos a bolsas, llevas hasta 2 bolsas grandes o 1 bolsa de cuero de vaca gratis.");
    }

    return total;
  }

  function computeDOMTotal() {
    return applyPromoUiAndComputeTotal({ updateUI: false });
  }
  function enforceTotalIntegrity() {
    const dom = applyPromoUiAndComputeTotal({ updateUI: true });
    if (!Number.isFinite(dom)) return;
    setTotal(dom);
  }

  function throttle(fn, ms=60){
    let t=0, lastArgs=null, lastThis=null, timer=null;
    return function(...args){
      const ts=Date.now(); lastArgs=args; lastThis=this;
      const run=()=>{ timer=null; t=ts; fn.apply(lastThis,lastArgs); };
      if (!t || ts-t>=ms){ run(); } else { if (!timer) timer=setTimeout(run, ms-(ts-t)); }
    };
  }

  // ✅ throttle para funciones async (devuelve Promise)
  function throttleAsync(fn, ms=60){
    let lastExec = 0;
    let timer = null;
    let lastArgs = null;
    let pending = [];

    async function exec(){
      timer = null;
      lastExec = Date.now();
      const p = pending.slice();
      pending = [];
      try {
        const res = await fn.apply(null, lastArgs || []);
        p.forEach(x => x.resolve(res));
      } catch (err){
        p.forEach(x => x.reject(err));
      }
    }

    return function(...args){
      lastArgs = args;
      return new Promise((resolve, reject) => {
        pending.push({ resolve, reject });
        const nowTs = Date.now();
        const wait = Math.max(0, ms - (nowTs - lastExec));

        if (!timer){
          if (wait === 0) exec();
          else timer = setTimeout(exec, wait);
        }
      });
    };
  }

  const enforceTotalIntegritySoft = throttle(enforceTotalIntegrity, 150);

  /* ================== Modal open/close helpers ================== */
  function openModal(){
    $modal.addClass("is-open").show();
    $("body").addClass("modal-open");
  }
  function closeModal(){
    try { stopNequiAutoRefresh(); } catch (_) {}
    $("#employee-password-input").val("");
    $hidEmpleadoPassword.val("");
    $("#merk2888-password-input").val("");
    $hidMerk2888Password.val("");
    $modal.removeClass("is-open").hide();
    $("body").removeClass("modal-open");
  }

  function showFastSaleToast(message, ms = 1400) {
    // ✅ Reemplaza el alert de éxito cuando se busca máxima velocidad en caja.
    // No bloquea el foco ni detiene el siguiente escaneo.
    try {
      let el = document.getElementById("venta-fast-toast");
      if (!el) {
        el = document.createElement("div");
        el.id = "venta-fast-toast";
        el.style.cssText = [
          "position:fixed",
          "right:18px",
          "bottom:18px",
          "z-index:99999",
          "max-width:340px",
          "padding:12px 14px",
          "border-radius:14px",
          "background:#153060",
          "color:#fff",
          "font:600 14px/1.35 system-ui,-apple-system,Segoe UI,Arial",
          "box-shadow:0 10px 30px rgba(0,0,0,.25)",
          "opacity:0",
          "transform:translateY(10px)",
          "transition:opacity .16s ease, transform .16s ease",
          "pointer-events:none",
          "white-space:pre-line"
        ].join(";");
        document.body.appendChild(el);
      }
      el.textContent = message || "Venta registrada";
      clearTimeout(el._hideTimer);
      requestAnimationFrame(() => {
        el.style.opacity = "1";
        el.style.transform = "translateY(0)";
      });
      el._hideTimer = setTimeout(() => {
        el.style.opacity = "0";
        el.style.transform = "translateY(10px)";
      }, Math.max(400, ms | 0));
    } catch (_) {}
  }

  function numericIdFromValue(value) {
    const match = String(value || "").match(/\d+/);
    return match ? match[0] : "";
  }

  function cartAuditItemKey(item) {
    const pid = String(item?.producto_id || item?.pid || "").trim();
    if (pid) return `pid:${pid}`;
    return `name:${String(item?.nombre || "").trim().toLowerCase()}`;
  }

  function rememberCartAuditItem(item) {
    if (!item || (!item.producto_id && !item.nombre)) return;
    const key = cartAuditItemKey(item);
    if (!key || key === "name:") return;
    cartAuditSessionItems.set(key, {
      producto_id: String(item.producto_id || "").trim(),
      nombre: String(item.nombre || "").trim(),
      cantidad: Number(item.cantidad) || 0,
      precio: Number(item.precio) || 0,
      subtotal: Number(item.subtotal) || 0,
      codigo_barras: String(item.codigo_barras || item.barcode || "").trim(),
    });
  }

  function rememberCartAuditRow($row) {
    if (!$row || !$row.length) return;
    const pid = String($row.data("pid") || "").trim();
    const cached = pid ? (productCache.get(pid) || {}) : {};
    const qty = Number($row.attr("data-qty")) || Number($row.find(".qty-input").val()) || 0;
    const price = Number($row.data("price")) || Number(cached.price) || 0;
    const visibleName = $row.find(".cart-product-name").first().text();
    const name = String(visibleName || cached.nombre || cached.name || "").trim();
    rememberCartAuditItem({
      producto_id: pid,
      nombre: name,
      cantidad: qty,
      precio: price,
      subtotal: price * qty,
      codigo_barras: cached.barcode || cached.codigo_de_barras || "",
    });
  }

  function resetCartAuditSession() {
    cartAuditSessionItems.clear();
  }

  function buildCartClearAuditPayload() {
    const rows = $tbody.find("tr").toArray();

    for (const row of rows) {
      rememberCartAuditRow($(row));
    }

    const items = Array.from(cartAuditSessionItems.values());

    return {
      items,
      subtotal: roundAccountAmount(runningTotal),
      descuento: employeeDiscountAmount(),
      total: saleTotalForPayment(),
      cliente_id: numericIdFromValue($("#cliente_id").val()),
      cliente_nombre: ($inpCliente.val() || selectedClientLabel || "").trim(),
      sucursal_id: numericIdFromValue($("#sucursal_id").val() || sucursalID),
      sucursal_nombre: ($("#sucursal_autocomplete").val() || localStorage.getItem("sucursalName") || "").trim(),
      puntopago_id: numericIdFromValue($("#puntopago_id").val()),
      puntopago_nombre: ($("#puntopago_autocomplete").val() || "").trim(),
      enviado_en: new Date().toISOString(),
    };
  }

  function showCartClearAuditNotice(result, failed = false) {
    const notice = document.getElementById("venta-carrito-audit-notice");
    const message = document.getElementById("venta-carrito-audit-message");
    if (!notice || !message) return;
    if (failed) {
      message.textContent = "Carrito vaciado. No se pudo confirmar el conteo diario; revisa la conexión.";
    } else {
      if (!result?.success || result.ignored) return;
      const count = Number(result.daily_count);
      const day = String(result.audit_day || "");
      if (!Number.isSafeInteger(count) || count < 1 || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
      // Una respuesta lenta de otro vaciado no debe retroceder el contador.
      if (day < cartClearNoticeDay || (day === cartClearNoticeDay && count < cartClearNoticeCount)) return;
      cartClearNoticeCount = day === cartClearNoticeDay ? Math.max(count, cartClearNoticeCount) : count;
      cartClearNoticeDay = day;
      message.textContent = `Venta no facturada #${cartClearNoticeCount} del día`;
    }
    notice.classList.toggle("is-warning", failed);
    notice.hidden = false;
    clearTimeout(cartClearNoticeTimer);
    cartClearNoticeTimer = setTimeout(() => {
      notice.hidden = true;
      message.textContent = "";
      cartClearNoticeTimer = null;
    }, 2000);
  }

  function sendCartClearAudit(payload, options = {}) {
    if (CARRITO_LIMPIO_AUDIT_DISABLED) return;
    if (!CARRITO_LIMPIO_AUDIT_URL || !payload || !Array.isArray(payload.items) || !payload.items.length) return;

    const csrf = getCSRF();
    const body = JSON.stringify({
      ...payload,
      motivo: options.beacon ? "cierre_sin_borrador" : "carrito_vaciado",
    });

    if (options.beacon && navigator.sendBeacon && window.FormData) {
      try {
        const fd = new FormData();
        fd.append("csrfmiddlewaretoken", csrf);
        fd.append("payload", body);
        if (navigator.sendBeacon(CARRITO_LIMPIO_AUDIT_URL, fd)) return;
      } catch (_) {}
    }

    try {
      return fetch(CARRITO_LIMPIO_AUDIT_URL, {
        method: "POST",
        credentials: "same-origin",
        keepalive: !!options.keepalive && body.length < 60000,
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrf,
        },
        body,
      }).then(async response => {
        if (!response.ok) throw new Error("No se pudo registrar el vaciado");
        const result = await response.json();
        if (!result.success) throw new Error("Registro de vaciado no confirmado");
        if (!options.beacon) showCartClearAuditNotice(result);
      }).catch(() => {
        if (!options.beacon) showCartClearAuditNotice(null, true);
      });
    } catch (_) {
      if (!options.beacon) showCartClearAuditNotice(null, true);
    }
  }

  function removeRowByPidWithAuditIfEmpty(pid) {
    const willEmpty = productos.length <= 1;
    const payload = willEmpty ? buildCartClearAuditPayload() : null;
    const removed = removeRowByPid(pid);

    if (removed && willEmpty && productos.length === 0 && payload) {
      sendCartClearAudit(payload);
      resetCartAuditSession();
    }

    return removed;
  }

  function clearCartAndTotals() {
    productos.length = 0;
    cantidades.length = 0;
    $tbody.empty();
    $("#productos").val("[]");
    $("#cantidades").val("[]");
    setTotal(0);

    // ✅ limpiar pagos SIEMPRE
    $hidMedioPago.val("");
    $hidPagos.val("");
    $hidMerk2888Password.val("");
    $("#merk2888-password-input").val("");
    $hidNequiNotification.val("");
    resetNequiPaymentState({ clearCache: false });
    $modal.find(".pm-check").prop("checked", false);
    $modal.find(".pm-amt").val("").prop("disabled", true);
    $("#mix-mode").prop("checked", false);
    saleDraftPaymentState = null;

    $("#monto-recibido").val("");
    $("#cambio").text("");
    closeModal();
    lastAddedPid = null;
    resetCartAuditSession();
  }

  // ✅ LIMPIEZA COMPLETA POST-VENTA (SIN RECARGA)
  function resetAfterSaleFast() {
    // carrito + total + pagos + modal
    clearCartAndTotals();
    saleDraftActiveID = createSaleDraftID();
    saleDraftLastStorageKey = "";
    saleDraftRecoveredFrom = [];
    resetNequiPaymentState({ clearCache: true });

    // cliente
    try { $("#cliente_id").val(""); } catch {}
    $inpCliente.val("");
    selectedClientLabel = "";
    selectedEmployeeClient = {
      isEmployee: false,
      employeeName: "",
      documento: "",
      employeeHasUser: false,
      employeeIsWebMaster: false,
      isMerk2888: false
    };
    $("#employee-password-input").val("");
    $hidEmpleadoPassword.val("");
    $("#merk2888-password-input").val("");
    $hidMerk2888Password.val("");
    refreshEmployeeDiscountUI();
    saleDraftSubmissionPending = false;

    // producto inputs + pid
    $inpNombre.val("");
    if ($inpId && $inpId.length) $inpId.val("");
    $inpCode.val("");
    $pid.val("");

    // cantidad principal
    if ($cantidad && $cantidad.length) $cantidad.val("1");

    // filtro tabla
    if ($buscarCart && $buscarCart.length) $buscarCart.val("");

    // re-habilitar botones de agregar (si ya hay producto seleccionado, se habilitarán con setProductFields)
    if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
    if ($agregar && $agregar.length)  $agregar.prop("disabled", true);

    // cerrar autocompletes abiertos
    try { $inpNombre.autocomplete("close"); } catch(_){}
    try { $inpCode.autocomplete("close"); } catch(_){}
    try { if ($inpId && $inpId.length) $inpId.autocomplete("close"); } catch(_){}

    // foco rápido para siguiente venta (prioridad: barras)
    queueMicrotask(() => {
      offerSaleDraftForCurrentScope();
      if ($inpCode && $inpCode.length && $inpCode.is(":visible")) {
        $inpCode.focus(); $inpCode[0]?.select?.();
      } else if ($inpNombre && $inpNombre.length) {
        $inpNombre.focus(); $inpNombre[0]?.select?.();
      }
    });
  }

  window.addEventListener("pageshow", (e) => {
    if ($tbody.find("tr").length === 0) setTotal(0);
    else enforceTotalIntegritySoft();
    if (e.persisted) {
      void resumeSaleDraftPage();
    }
  });

  let catalogPollTimer = null;
  function stopCatalogPolling(){
    if (catalogPollTimer) clearInterval(catalogPollTimer);
    catalogPollTimer = null;
  }
  window.addEventListener("beforeunload", () => {
    let cached = false;
    try {
      cached = persistSaleDraftNow();
      // Conserva el registro anterior como respaldo únicamente si el navegador
      // no permitió guardar el borrador local.
      if (productos.length && !cached) {
        sendCartClearAudit(buildCartClearAuditPayload(), {
          beacon: true,
          keepalive: true,
        });
      }
    } catch (_) {}
    try { stopCatalogPolling(); } catch (_){ }
  });

  window.addEventListener("pagehide", () => {
    suspendSaleDraftPage();
  });

  /* ================== Helpers: focus qty row ================== */
  function focusQtyOfRow($row){
    if (!$row || !$row.length) return false;
    const $q = $row.find(".qty-input");
    if (!$q.length) return false;
    $q.focus();
    $q[0]?.select?.();
    return true;
  }
  function focusQtySmart(){
    if (lastAddedPid) {
      const $r = $tbody.find(`tr[data-pid='${String(lastAddedPid)}']`);
      if ($r.length && focusQtyOfRow($r)) return true;
    }
    const $first = $tbody.find("tr:visible").first();
    if ($first.length && focusQtyOfRow($first)) {
      lastAddedPid = String($first.data("pid") || "") || null;
      return true;
    }
    if ($cantidad && $cantidad.length) {
      $cantidad.focus();
      $cantidad[0]?.select?.();
      return true;
    }
    return false;
  }
  function refreshLastAddedPidAfterRemoval(removedPid){
    const rp = String(removedPid || "");
    if (rp && String(lastAddedPid || "") === rp) lastAddedPid = null;
    if (lastAddedPid) {
      const $r = $tbody.find(`tr[data-pid='${String(lastAddedPid)}']`);
      if ($r.length) return;
      lastAddedPid = null;
    }
    const $first = $tbody.find("tr:visible").first();
    lastAddedPid = $first.length ? String($first.data("pid") || "") : null;
  }

  /* ================== Cache producto ================== */
  const productCache = new Map(); // pid -> {nombre, barcode, price, stock, ts}
  const barcodeIndex = new Map(); // barcode normalizado -> pid unico
  const barcodePidSets = new Map(); // barcode normalizado -> Set(pid)
  const nameIndex    = new Map(); // name(lc) -> pid

  function syncBarcodeIndexKey(codeKey) {
    if (!codeKey) return;
    const set = barcodePidSets.get(codeKey);
    if (!set || set.size === 0) {
      barcodePidSets.delete(codeKey);
      barcodeIndex.delete(codeKey);
      return;
    }
    if (set.size === 1) {
      barcodeIndex.set(codeKey, set.values().next().value);
      return;
    }
    barcodeIndex.delete(codeKey);
  }

  function addBarcodePidIndex(code, pid) {
    const codeKey = onlyDigits(code);
    const pidKey = String(pid || "").trim();
    if (!codeKey || !pidKey) return;
    if (!barcodePidSets.has(codeKey)) barcodePidSets.set(codeKey, new Set());
    barcodePidSets.get(codeKey).add(pidKey);
    syncBarcodeIndexKey(codeKey);
  }

  function removeBarcodePidIndex(code, pid) {
    const codeKey = onlyDigits(code);
    const pidKey = String(pid || "").trim();
    if (!codeKey || !pidKey) return;
    const set = barcodePidSets.get(codeKey);
    if (set) set.delete(pidKey);
    syncBarcodeIndexKey(codeKey);
  }

  function isBarcodeLocallyAmbiguous(code) {
    const codeKey = onlyDigits(code);
    const set = codeKey ? barcodePidSets.get(codeKey) : null;
    return !!(set && set.size > 1);
  }

  // ✅ Fast-path seguro para escáner:
  // usa SOLO coincidencia exacta y única de código de barras ya cargada en snapshot/cache.
  // Si no hay certeza local, retorna null y se mantiene el camino original por servidor.
  function getLocalExactBarcodeProduct(code) {
    if (!FAST_BARCODE_LOCAL || !hasSucursal()) return null;

    const clean = onlyDigits(code);
    if (!clean) return null;
    if (isBarcodeLocallyAmbiguous(clean)) return null;

    const idx = preIndex.get(sucursalID);
    if (idx && Array.isArray(idx.codes)) {
      let found = null;

      for (const c of idx.codes) {
        if (!c || c.nbarcode !== clean) continue;

        const ref = idx.map.get(String(c.id));
        if (!ref) continue;

        if (found && String(found.id) !== String(ref.id)) return null;

        found = {
          id: ref.id,
          name: ref.name || c.label || `Producto ${ref.id}`,
          barcode: ref.barcode || clean,
          price: ref.price ?? c.price,
          stock: ref.stock ?? c.stock,
        };
      }

      if (found) {
        updateCache(found.id, {
          nombre: found.name,
          barcode: found.barcode,
          precio_unitario: found.price,
          cantidad_disponible: found.stock,
        });
        return found;
      }
    }

    const cachedPid = barcodeIndex.get(clean);
    const cached = cachedPid ? productCache.get(String(cachedPid)) : null;
    const cachedBarcode = onlyDigits(String(cached?.barcode || ""));

    if (cachedPid && cached && cachedBarcode === clean) {
      return {
        id: String(cachedPid),
        name: cached.nombre || `Producto ${cachedPid}`,
        barcode: cached.barcode || clean,
        price: cached.price || 0,
        stock: cached.stock,
      };
    }

    return null;
  }

  function updateCache(pid, data = {}) {
    const key = String(pid);
    const prev = productCache.get(key) || {};

    if (prev.barcode) {
      removeBarcodePidIndex(prev.barcode, key);
    }
    if (prev.nombre) {
      const oldN = String(prev.nombre).toLowerCase();
      if (nameIndex.get(oldN) === key) nameIndex.delete(oldN);
    }

    const normalizePrice = (v) => {
      const n = Number(v);
      return Number.isFinite(n) && n > 0 ? n : undefined;
    };

    const rec = {
      nombre: onlyName(data.nombre ?? prev.nombre ?? ""),
      barcode: data.codigo_de_barras ?? data.barcode ?? prev.barcode ?? "",
      price: normalizePrice(data.precio_unitario ?? data.price ?? prev.price),
      stock: data.cantidad_disponible ?? data.stock ?? prev.stock,
      ts: data.ts || now(),
    };

    productCache.set(key, rec);

    if (rec.barcode) addBarcodePidIndex(rec.barcode, key);
    if (rec.nombre)  nameIndex.set(String(rec.nombre).toLowerCase(), key);

    return rec;
  }

  /* ================== Catálogo Snapshot L1 ================== */
  const catalogBySucursal = new Map();
  const preIndex = new Map(); // sid -> {names:[...], codes:[...], ids:[...], map: Map(id->ref)}
  const CATALOG_TTL_MS = 5 * 60 * 1000;

  function hydrateFromCatalog(items){
    for (const p of items) updateCache(p.id, { nombre:p.name, barcode:p.barcode, precio_unitario:p.price, cantidad_disponible:p.stock });
  }

  function buildPreIndexFor(sid, items){
    const idx = { names: [], codes: [], ids: [], map:new Map() };
    for (const p of items) {
      const id = p.id;
      const idStr = String(id);

      const rawName = (p.name || "").toString();
      const nnameU = normalizeUnits(rawName);
      const toks = nnameU ? nnameU.split(/\s+/).filter(Boolean) : [];

      const barcodeRaw = (p.barcode || "").toString();
      const nbarcode = barcodeRaw ? onlyDigits(barcodeRaw) : "";

      idx.names.push({ id, nnameU, toks, label: rawName || "", price: p.price, stock: p.stock, barcode: barcodeRaw || "" });
      idx.codes.push({ id, nbarcode, label: barcodeRaw || rawName || "", price: p.price, stock: p.stock });
      idx.ids.push({ id, idStr, label: idStr, name: rawName || "", barcode: barcodeRaw || "", price: p.price, stock: p.stock });

      idx.map.set(String(id), { id, name: rawName || "", barcode: barcodeRaw || "", price: p.price, stock: p.stock });
    }
    idx.names.sort((a,b) => (a.nnameU < b.nnameU ? -1 : a.nnameU > b.nnameU ? 1 : 0));
    idx.codes.sort((a,b) => (a.nbarcode < b.nbarcode ? -1 : a.nbarcode > b.nbarcode ? 1 : 0));
    idx.ids.sort((a,b)=> (a.idStr < b.idStr ? -1 : a.idStr > b.idStr ? 1 : 0));
    preIndex.set(sid, idx);
  }

  function loadCatalogFromLocalStorage(sid) {
    try {
      const raw = localStorage.getItem(`catalog_${sid}`);
      const ts  = Number(localStorage.getItem(`catalog_${sid}_ts`)||0);
      if (!raw) return false;
      if (now()-ts > CATALOG_TTL_MS) return false;
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return false;
      catalogBySucursal.set(sid, arr);
      hydrateFromCatalog(arr);
      buildPreIndexFor(sid, arr);
      return true;
    } catch { return false; }
  }

  async function fetchCatalogSnapshot(sid) {
    const url = SNAPSHOT_URL + "?" + new URLSearchParams({ sucursal_id: sid, _ts: Date.now() });
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("snapshot HTTP " + r.status);
    const d = await r.json();
    const items = Array.isArray(d.results) ? d.results : [];
    catalogBySucursal.set(sid, items);
    try {
      localStorage.setItem(`catalog_${sid}`, JSON.stringify(items));
      localStorage.setItem(`catalog_${sid}_ts`, String(now()));
    } catch {}
    hydrateFromCatalog(items);
    buildPreIndexFor(sid, items);
    return items;
  }

  async function ensureCatalog(sid, {force=false}={}) {
    if (!sid) return [];
    if (!force && catalogBySucursal.has(sid)) return catalogBySucursal.get(sid) || [];
    if (!force && loadCatalogFromLocalStorage(sid)) return catalogBySucursal.get(sid) || [];
    try { return await fetchCatalogSnapshot(sid); }
    catch { return catalogBySucursal.get(sid) || []; }
  }

  async function ensureProductCachedById(pid) {
    const key = String(pid || "").trim();
    if (!key || !hasSucursal()) return null;

    const rec = productCache.get(key);
    if (rec && rec.nombre) return { id: key, name: rec.nombre, barcode: rec.barcode || "", price: rec.price || 0, stock: rec.stock };

    const items = await ensureCatalog(sucursalID, { force: true });
    const found = (items || []).find(p => String(p.id) === key);
    if (found) {
      const hydrated = updateCache(key, { nombre: found.name, barcode: found.barcode, precio_unitario: found.price, cantidad_disponible: found.stock });
      return { id: key, name: hydrated.nombre || found.name || `Producto ${key}`, barcode: hydrated.barcode || found.barcode || "", price: hydrated.price || found.price || 0, stock: hydrated.stock ?? found.stock };
    }

    const r = await $.post(VERIFICAR_URL, { producto_id: key, cantidad: 1, sucursal_id: sucursalID, _ts: Date.now() }).catch(() => null);
    if (r && r.exists) {
      const hydrated = updateCache(key, r);
      return { id: key, name: hydrated.nombre || r.nombre || `Producto ${key}`, barcode: hydrated.barcode || r.codigo_de_barras || "", price: hydrated.price || r.precio_unitario || 0, stock: hydrated.stock ?? r.cantidad_disponible };
    }

    return null;
  }

  /* ================== Ranking local (LRU + scoring) ================== */
  class LRU {
    constructor(max=200){ this.max=max; this.map=new Map(); }
    get(k){ if(!this.map.has(k)) return null; const v=this.map.get(k); this.map.delete(k); this.map.set(k,v); return v; }
    set(k,v){ if(this.map.has(k)) this.map.delete(k); this.map.set(k,v); if(this.map.size>this.max){ const f=this.map.keys().next().value; this.map.delete(f);} }
  }
  const termCacheName = new LRU(240);
  const termCacheCode = new LRU(240);
  const termCacheId   = new LRU(240);

  let pickBoost = Object.create(null);
  let pickSaveTimer = null;

  function loadPickBoost(sid){
    pickBoost = Object.create(null);
    try {
      const raw = localStorage.getItem(`pick_boost_${sid}`) || "";
      const obj = raw ? JSON.parse(raw) : null;
      if (obj && typeof obj === "object") pickBoost = obj;
    } catch {}
  }

  function bumpPick(pid){
    if (!hasSucursal() || !pid) return;
    const k = String(pid);
    pickBoost[k] = (pickBoost[k] || 0) + 1;
    if (pickSaveTimer) clearTimeout(pickSaveTimer);
    pickSaveTimer = setTimeout(() => {
      try { localStorage.setItem(`pick_boost_${sucursalID}`, JSON.stringify(pickBoost)); } catch {}
    }, 400);
  }

  function pushTopK(arr, item, score, K=40){
    if (score <= 0) return;
    const rec = { item, score };
    if (arr.length < K) { arr.push(rec); return; }
    let minI = 0, minS = arr[0].score;
    for (let i=1;i<arr.length;i++){
      if (arr[i].score < minS) { minS = arr[i].score; minI = i; }
    }
    if (score <= minS) return;
    arr[minI] = rec;
  }

  function isStrongToken(t){
    if (!t) return false;
    return /\d/.test(t) || /^x\d+/.test(t) || /\d+(ml|g|gr|kg|l|lt|oz)$/.test(t);
  }

  function scoreName(qU, qTokens, strongTokens, cand){
    const s = cand.nnameU;
    if (!s) return 0;
    if (s === qU) return 2600 + (pickBoost[String(cand.id)] || 0) * 7;

    let score = 0;
    const pos = s.indexOf(qU);
    if (pos === 0) score += 1500;
    else if (pos > 0) score += 850;
    if (pos >= 0) score += Math.max(0, 160 - pos * 6);

    if (qTokens.length) {
      let hits = 0, strongHits = 0;
      for (let i=0;i<qTokens.length;i++){
        const t = qTokens[i];
        if (!t) continue;
        const strong = isStrongToken(t);
        let found = false;

        for (let j=0;j<cand.toks.length;j++){
          const ct = cand.toks[j];
          if (ct === t) { score += strong ? 240 : 170; hits++; if (strong) strongHits++; found=true; break; }
          if (ct.startsWith(t)) { score += strong ? 170 : 120; hits++; if (strong) strongHits++; found=true; break; }
        }
        if (!found) score -= (strong ? 190 : 70);
      }
      if (hits) score += hits * 45;
      if (hits === qTokens.length) score += 260;
      if (strongTokens.length && strongHits === strongTokens.length) score += 360;
    }

    const diff = Math.abs((s.length || 0) - (qU.length || 0));
    score += Math.max(0, 90 - diff);

    score += (pickBoost[String(cand.id)] || 0) * 7;

    const st = Number(cand.stock) || 0;
    if (st > 0) score += Math.min(80, st / 2);

    return score;
  }

  function scoreCode(qDigits, candCode){
    const s = candCode.nbarcode || "";
    if (!s || !qDigits) return 0;
    if (s === qDigits) return 2400 + (pickBoost[String(candCode.id)] || 0) * 7;
    const pos = s.indexOf(qDigits);
    if (pos === 0) return 1700 + Math.max(0, 130 - qDigits.length * 2) + (pickBoost[String(candCode.id)] || 0) * 7;
    if (pos > 0) return 1000 + Math.max(0, 70 - pos * 5) + (pickBoost[String(candCode.id)] || 0) * 7;
    return 0;
  }

  function rankNameLocal(term, idx, limit=40){
    const qU = normalizeUnits(term);
    if (!qU) return [];
    const qTokens = qU.split(/\s+/).filter(Boolean).slice(0, 6);
    const strongTokens = qTokens.filter(isStrongToken);

    const top = [];
    for (let i=0;i<idx.names.length;i++){
      const c = idx.names[i];
      if (qTokens.length) {
        const t0 = qTokens[0];
        if (t0 && c.nnameU.indexOf(t0) === -1) continue;
      } else {
        if (c.nnameU.indexOf(qU) === -1) continue;
      }

      if (strongTokens.length) {
        let ok = false;
        for (let k=0;k<strongTokens.length;k++){
          const st = strongTokens[k];
          if (st && c.nnameU.indexOf(st) !== -1) { ok = true; break; }
        }
        if (!ok) continue;
      }

      const sc = scoreName(qU, qTokens, strongTokens, c);
      pushTopK(top, c, sc, limit);
    }

    top.sort((a,b) => b.score - a.score);
    return top.map(({item:c}) => ({ id:c.id, name:c.label, barcode:c.barcode, price:c.price, stock:c.stock }));
  }

  function rankCodeLocal(term, idx, limit=40){
    const info = classifyQuery(term);
    if (!info.isBarcodeLike) return rankNameLocal(term, idx, limit);

    const qDigits = info.digits;
    const top = [];
    for (let i=0;i<idx.codes.length;i++){
      const c = idx.codes[i];
      if (!c.nbarcode) continue;
      if (c.nbarcode.indexOf(qDigits) === -1) continue;
      const sc = scoreCode(qDigits, c);
      pushTopK(top, c, sc, limit);
    }

    top.sort((a,b) => b.score - a.score);
    return top.map(({item:c}) => {
      const ref = idx.map.get(String(c.id));
      const barcode = ref?.barcode || c.label || "";
      const name = ref?.name || "";
      return { id:c.id, name, barcode, price: ref?.price ?? c.price, stock: ref?.stock ?? c.stock };
    });
  }

  function rankIdLocal(term, idx, limit=40){
    const q = onlyDigits(term);
    if (!q) return [];
    const top = [];
    for (let i=0;i<idx.ids.length;i++){
      const c = idx.ids[i];
      if (!c.idStr) continue;
      if (c.idStr === q) {
        top.push({ item:c, score: 3000 + (pickBoost[String(c.id)] || 0) * 7 });
        continue;
      }
      if (c.idStr.startsWith(q)) {
        const sc = 1800 + Math.max(0, 120 - (c.idStr.length - q.length) * 10) + (pickBoost[String(c.id)] || 0) * 7;
        pushTopK(top, c, sc, limit);
      }
    }
    top.sort((a,b)=> b.score - a.score);
    return top.map(({item:c}) => ({ id:c.id, name:c.name, barcode:c.barcode, price:c.price, stock:c.stock }));
  }

  function buildLocalSmart(term, idx, limit=40){
    const t = (term || "").trim();
    const info = classifyQuery(t);
    const locals = [];
    const seen = new Set();

    if (info.isPureDigits && idx) {
      const ref = idx.map.get(String(info.digits));
      if (ref) { locals.push({ id: ref.id, name: ref.name, barcode: ref.barcode, price: ref.price, stock: ref.stock }); seen.add(String(ref.id)); }
    }

    if (idx && info.isBarcodeLike) {
      const byCode = rankCodeLocal(t, idx, limit);
      for (const it of byCode) { const k=String(it.id); if(seen.has(k)) continue; locals.push(it); seen.add(k); if(locals.length>=limit) break; }
    }

    if (idx && locals.length < limit) {
      const byName = rankNameLocal(t, idx, limit);
      for (const it of byName) { const k=String(it.id); if(seen.has(k)) continue; locals.push(it); seen.add(k); if(locals.length>=limit) break; }
    }

    return locals.slice(0, limit);
  }

  /* ================== Precio en vivo ================== */
  function ensureLivePrice(pid) {
    return asNativePromise($.post(VERIFICAR_URL, { producto_id: pid, cantidad: 1, sucursal_id: sucursalID, _ts: Date.now() }))
      .then((r) => {
        if (!r || !r.exists) return null;
        const rec = updateCache(pid, r);
        const price = Number(rec.price ?? r.precio_unitario ?? r.precio);
        if (!Number.isFinite(price) || price <= 0) return null;
        return price;
      })
      .catch(() => null);
  }

  function setRowPriceUI($row, price){
    $row.attr("data-price", price).data("price", price);
    $row.removeClass("pending-price");
    $row.find(".price-cell").text(money(price));
    rememberCartAuditRow($row);
  }

  let repricingMode = false;

  function refreshRowPriceIfNeeded($row) {
    const pid = String($row.data("pid") || "");
    return ensureLivePrice(pid).then((live) => {
      if (!Number.isFinite(live) || live <= 0) return false;

      const old = Number($row.data("price")) || 0;
      const qty = Number($row.attr("data-qty")) || Number($row.find(".qty-input").val()) || 0;

      setRowPriceUI($row, live);
      $row.find(".subtotal-cell").text(money(live * qty));

      if (!$row.data("counted")) {
        if (!repricingMode) addToTotal(live * qty);
        $row.data("counted", true);
      } else if (old && old !== live) {
        if (!repricingMode) addToTotal((live - old) * qty);
      }

      if (!repricingMode) enforceTotalIntegritySoft();
      return true;
    });
  }

  /* ================== Debounce verificación precio ================== */
  const verifyTimers = new Map(); // pid -> timer
  function scheduleVerifyRowPrice($row, delay=220){
    const pid = String($row.data("pid") || "");
    if (!pid) return;
    const prev = verifyTimers.get(pid);
    if (prev) clearTimeout(prev);
    const t = setTimeout(() => {
      verifyTimers.delete(pid);
      const $still = $tbody.find(`tr[data-pid='${pid}']`);
      if ($still.length) refreshRowPriceIfNeeded($still);
    }, Math.max(0, delay|0));
    verifyTimers.set(pid, t);
  }

  /* ================== Inserción instantánea ================== */
  function buildRowHTML(pid, qty, name, cachedPrice) {
    const hasPrice = Number.isFinite(cachedPrice) && cachedPrice > 0;
    const subtotalTxt = hasPrice ? money(cachedPrice * qty) : "…";
    const priceTxt    = hasPrice ? money(cachedPrice) : "—";
    const pendingCls  = hasPrice ? "" : "pending-price";

    return (
      `<tr data-pid="${pid}" data-price="${hasPrice ? cachedPrice : 0}" data-qty="${qty}" class="${pendingCls}">
         <td class="cart-product-cell">
           <div class="cart-product-info">
             <span class="cart-product-name">${escapeHTML(onlyName(name))}</span>
             <small class="cart-product-id">ID ${escapeHTML(pid)}</small>
           </div>
         </td>
         <td><input type="number" class="qty-input" step="1" inputmode="numeric" value="${qty}" /></td>
         <td class="price-cell">${priceTxt}</td>
         <td class="subtotal-cell">${subtotalTxt}</td>
         <td class="text-center">
           <button class="btn btn-chip-danger eliminar-producto" title="Eliminar" aria-label="Eliminar producto">
             <i class="fas fa-trash-alt" aria-hidden="true"></i>
           </button>
         </td>
       </tr>`
    );
  }

  function removeRowByPid(pid){
    if (!guardSaleDraftEditing() || (saleSubmitting && !saleDraftRestoring)) return false;
    const key = String(pid);
    const $r = $tbody.find(`tr[data-pid='${key}']`);
    if (!$r.length) return false;

    rememberCartAuditRow($r);

    const idx = productos.indexOf(key);
    const price = Number($r.data("price")) || 0;
    const qty = Number($r.attr("data-qty")) || Number($r.find(".qty-input").val()) || 0;

    if ($r.data("counted")) addToTotal(-(price * qty));
    if (idx > -1) { productos.splice(idx, 1); cantidades.splice(idx, 1); }
    $r.remove();
    enforceTotalIntegritySoft();
    refreshLastAddedPidAfterRemoval(key);
    return true;
  }

  function insertOrUpdateRowInstant(pid, qty, name, cachedPrice) {
    if (!guardSaleDraftEditing() || saleSubmitting) return false;
    const key = String(pid);
    const idx = productos.indexOf(key);
    const hasPrice = Number.isFinite(cachedPrice) && cachedPrice > 0;

    if (idx > -1) {
      const prevQty = Number(cantidades[idx]) || 0;
      const newQty = prevQty + qty;

      if (newQty === 0) {
        removeRowByPid(pid);
        return;
      }

      cantidades[idx] = newQty;

      const $r = $tbody.find(`tr[data-pid='${pid}']`);
      $r.attr("data-qty", newQty);

      const $qin = $r.find(".qty-input");
      if ($qin.length) $qin[0].value = newQty;

      const price = Number($r.data("price")) || 0;
      if (price > 0) {
        $r.find(".subtotal-cell").text(money(price * newQty));
        addToTotal(price * qty);
        enforceTotalIntegritySoft();
      } else {
        scheduleVerifyRowPrice($r, 120);
      }
      rememberCartAuditRow($r);
    } else {
      if (qty === 0) return;

      productos.push(key);
      cantidades.push(qty);

      const cached = productCache.get(key) || {};
      const nm = cached.nombre || name || `Producto ${pid}`;
      const html = buildRowHTML(pid, qty, nm, cachedPrice);

      const tmpl = document.createElement("tbody");
      tmpl.innerHTML = html.trim();
      const row = tmpl.firstChild;
      $tbody[0].insertBefore(row, $tbody[0].firstChild || null);

      if (hasPrice) { addToTotal(cachedPrice * qty); $(row).data("counted", true); }
      else { $(row).data("counted", false); scheduleVerifyRowPrice($(row), 120); }

      enforceTotalIntegritySoft();
      rememberCartAuditRow($(row));
    }
    return true;
  }

  /* ================== Agregado con “burst last-only” ================== */
  const lastAddGuard = { pid: null, ts: 0 };
  const scannerPushGuard = { code: "", ts: 0 };
  const barcodeAutoAddGuard = { code: "", pid: "", ts: 0 };
  const suppressedBarcodeAC = new Map();
  const activeBarcodeResolves = new Map();
  let barcodeResolveSeq = 0;
  const autoProductAddLocks = new Map();
  const AUTO_ADD_PID_LOCK_MS = 1200;
  const AUTO_ADD_TERM_LOCK_MS = 4500;
  const AUTO_ADD_BARCODE_LOCK_MS = 4500;

  // ✅ Permite escanear dos veces el MISMO producto sin esperar casi nada.
  // Antes el mismo barcode podía quedar bloqueado ~900 ms o más para evitar dobles agregados
  // fantasma. Ahora el bloqueo largo se conserva para autocompletes, pero el escáner físico
  // usa una ventana corta que solo bloquea duplicados del mismo disparo.
  const LAST_ADD_GUARD_MS = Math.max(40, Number(window.LAST_ADD_GUARD_MS ?? 120));
  const SCANNER_REPEAT_LOCK_MS = Math.max(70, Number(window.SCANNER_REPEAT_LOCK_MS ?? 140));
  const SCANNER_SUPPRESS_AC_MS = Math.max(SCANNER_REPEAT_LOCK_MS, Number(window.SCANNER_SUPPRESS_AC_MS ?? 240));

  // ✅ Teclas que NO deben afectar el detector de escáner.
  // Algunos lectores genéricos (especialmente en modo ALT+NumPad o "auto-toggle NumLock")
  // emiten estas teclas como ruido alrededor de los dígitos reales del barcode.
  // Si las dejamos pasar al `resetAll()`, borran el buffer y se pierden dígitos.
  const SCANNER_ARTIFACT_KEYS = new Set([
    "Alt", "AltGraph", "Control", "Meta", "Shift",
    "NumLock", "CapsLock", "ScrollLock", "Pause",
    "Insert", "Delete", "Home", "End", "PageUp", "PageDown",
    "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
    "ContextMenu", "Dead", "Process", "Unidentified",
    "F1","F2","F3","F4","F5","F6","F7","F8","F9","F10","F11","F12",
  ]);

  const BARCODE_RESOLVE_BLOCKED = "__BARCODE_RESOLVE_BLOCKED__";

  // ✅ OPTIMIZACIÓN SCANNER:
  // - FAST_BARCODE_LOCAL=true usa el snapshot/cache local cuando el código de barras es exacto y único.
  // - FAST_SCANNER_BURST_MS=0 elimina la espera artificial antes de pintar el producto en carrito.
  // Puedes desactivar el fast-path desde el template con: window.FAST_BARCODE_LOCAL = false;
  const FAST_BARCODE_LOCAL = window.FAST_BARCODE_LOCAL !== false;
  const FAST_SCANNER_BURST_MS = Math.max(0, Number(window.FAST_SCANNER_BURST_MS ?? 0));

  // ✅ OPTIMIZACIÓN CIERRE DE VENTA / FACTURA:
  // - Se mantiene el alert con total/cambio, pero se lanza DESPUÉS de iniciar el envío a impresión.
  // - FAST_PRINT_FIRE_AND_FORGET=true manda /print sin await, sin AbortController y sin bloquear caja.
  // - FAST_PRINT_KICK_AFTER_MS=0: el cajón abre al mismo momento en que inicia /print.
  // - FAST_ALERT_BEFORE_RESET muestra el alert apenas arranca la impresión; limpia al cerrar el alert.
  // Puedes ajustar desde el template si algún día necesitas otro comportamiento:
  //   window.FAST_PRINT_FIRE_AND_FORGET = true;
  //   window.FAST_PRINT_KICK_AFTER_MS = 0;
  //   window.FAST_SALE_SUCCESS_ALERT = true;
  //   window.FAST_ALERT_BEFORE_RESET = true;
  const FAST_SALE_PRINT_WAIT_MS = Math.max(0, Number(window.FAST_SALE_PRINT_WAIT_MS ?? 0));
  const FAST_SALE_SUCCESS_ALERT = window.FAST_SALE_SUCCESS_ALERT !== false;
  const FAST_PRINT_FIRE_AND_FORGET = window.FAST_PRINT_FIRE_AND_FORGET !== false;
  const FAST_PRINT_KICK_AFTER_MS = Math.max(0, Number(window.FAST_PRINT_KICK_AFTER_MS ?? 0));
  const FAST_ALERT_BEFORE_RESET = window.FAST_ALERT_BEFORE_RESET !== false;
  const FAST_SUBMIT_VERIFY_PENDING_PRICES = window.FAST_SUBMIT_VERIFY_PENDING_PRICES === true;

  // ✅ ULTRA POS AGENT:
  // Evita headers personalizados y JSON tradicional para reducir preflight CORS.
  // Modo seguro por defecto: detecta /ping-fast; si existe, usa /print-fast y /kick-fast.
  // Para forzarlo desde el template: window.FAST_POS_ULTRA_FORCE = true;
  const FAST_POS_ULTRA_ENABLED = window.FAST_POS_ULTRA_ENABLED !== false;
  const FAST_POS_ULTRA_FORCE = window.FAST_POS_ULTRA_FORCE === true;
  const FAST_POS_PRINT_ENDPOINT = window.FAST_POS_PRINT_ENDPOINT || "/print-fast";
  const FAST_POS_KICK_ENDPOINT  = window.FAST_POS_KICK_ENDPOINT  || "/kick-fast";
  const FAST_POS_PING_ENDPOINT  = window.FAST_POS_PING_ENDPOINT  || "/ping-fast";

  // ✅ Recency tracking para evitar autopick fantasma cuando la red llega tarde
  //    o cuando el usuario re-enfoca un input con texto viejo. Solo se actualiza
  //    en eventos de tipeo real (no en focus).
  const lastUserInputTS = new WeakMap();
  const AUTO_PICK_RECENCY_MS = 1500;

  function cleanupAutoProductAddLocks(ts = now()) {
    for (const [key, until] of autoProductAddLocks.entries()) {
      if (Number(until) <= ts) autoProductAddLocks.delete(key);
    }
  }

  function autoAddQtyKey(qty) {
    const n = Number(qty);
    if (!Number.isFinite(n) || n === 0) return "1";
    return String(n);
  }

  function autoAddTermKey(term) {
    return normalizeUnits(String(term || "")).replace(/\s+/g, " ").trim();
  }

  function barcodeDigitsForStrictTerm(term) {
    const info = classifyQuery(term);
    return info.isBarcodeLike ? info.digits : "";
  }

  function itemMatchesExactBarcode(item, digits) {
    if (!digits || !item) return false;
    return onlyDigits(String(item.barcode || item.codigo_de_barras || "")) === digits;
  }

  function exactBarcodeItemsForTerm(term, items) {
    const digits = barcodeDigitsForStrictTerm(term);
    if (!digits) return Array.isArray(items) ? items : [];
    return (items || []).filter(item => itemMatchesExactBarcode(item, digits));
  }

  function uniqueExactBarcodeItemsForTerm(term, items) {
    const digits = barcodeDigitsForStrictTerm(term);
    const exact = exactBarcodeItemsForTerm(term, items);
    if (!digits) return exact;

    const pids = new Set(exact.map(item => String(item.id || "")));
    if (pids.size > 1) {
      flashScanError("Codigo de barras duplicado en inventario: " + digits);
      return [];
    }
    return exact;
  }

  function reserveAutoProductAdd(pid, qty = 1, ctx = {}) {
    const productKey = String(pid || "").trim();
    if (!productKey) return false;

    const ts = now();
    cleanupAutoProductAddLocks(ts);

    const qtyKey = autoAddQtyKey(qty);
    const termKey = autoAddTermKey(ctx.term || "");
    const barcodeKey = onlyDigits(ctx.barcode || "");
    const ttlOrDefault = (value, fallback) => {
      const n = Number(value);
      return Number.isFinite(n) ? Math.max(0, n) : fallback;
    };

    const pidTtl = ttlOrDefault(ctx.pidTtlMs, AUTO_ADD_PID_LOCK_MS);
    const termTtl = ttlOrDefault(ctx.termTtlMs, AUTO_ADD_TERM_LOCK_MS);
    const barcodeTtl = ttlOrDefault(ctx.barcodeTtlMs, AUTO_ADD_BARCODE_LOCK_MS);
    const keys = [];

    if (pidTtl > 0) keys.push({ key: `pid:${productKey}:qty:${qtyKey}`, ttl: pidTtl });
    if (termKey && termTtl > 0) keys.push({ key: `term:${termKey}:pid:${productKey}:qty:${qtyKey}`, ttl: termTtl });
    if (barcodeKey && barcodeTtl > 0) keys.push({ key: `barcode:${barcodeKey}:qty:${qtyKey}`, ttl: barcodeTtl });

    const blocked = keys.find(({ key }) => Number(autoProductAddLocks.get(key) || 0) > ts);
    if (blocked) {
      console.debug("[AC ADD GUARD] Doble agregado bloqueado", {
        pid: productKey,
        qty: qtyKey,
        source: ctx.source || "unknown",
        key: blocked.key,
      });
      return false;
    }

    for (const { key, ttl } of keys) autoProductAddLocks.set(key, ts + ttl);
    return true;
  }

  function closeProductAutocompleteMenus() {
    try { $inpNombre.autocomplete("close"); } catch (_){}
    try { $inpCode.autocomplete("close"); } catch (_){}
    try { if ($inpId && $inpId.length) $inpId.autocomplete("close"); } catch (_){}
  }

  function beginBarcodeResolve(code) {
    const key = onlyDigits(code);
    if (!key) return 0;
    const seq = ++barcodeResolveSeq;
    activeBarcodeResolves.set(key, seq);
    return seq;
  }

  function endBarcodeResolve(code, seq) {
    const key = onlyDigits(code);
    if (key && activeBarcodeResolves.get(key) === seq) activeBarcodeResolves.delete(key);
  }

  function isBarcodeResolveActive(code) {
    const key = onlyDigits(code);
    return !!(key && activeBarcodeResolves.has(key));
  }

  function suppressBarcodeAutocompleteAdd(code, ms = 1200) {
    const key = onlyDigits(code);
    if (!key) return;
    suppressedBarcodeAC.set(key, now() + Math.max(0, ms | 0));
  }

  function isBarcodeAutocompleteSuppressed(code) {
    const key = onlyDigits(code);
    if (!key) return false;

    const until = Number(suppressedBarcodeAC.get(key) || 0);
    if (!until) return false;

    if (now() > until) {
      suppressedBarcodeAC.delete(key);
      return false;
    }

    return true;
  }

  function rememberBarcodeAutoAdd(code, pid) {
    barcodeAutoAddGuard.code = onlyDigits(code);
    barcodeAutoAddGuard.pid = String(pid || "");
    barcodeAutoAddGuard.ts = now();
  }

  function wasRecentlyAutoAddedByBarcode(code, pid, windowMs = AUTO_ADD_BARCODE_LOCK_MS) {
    const key = onlyDigits(code);
    return !!(
      key &&
      barcodeAutoAddGuard.code === key &&
      String(barcodeAutoAddGuard.pid) === String(pid || "") &&
      now() - barcodeAutoAddGuard.ts < windowMs
    );
  }

  function isDuplicateScannerPush(code, windowMs = 140) {
    const key = onlyDigits(code);
    const ts = now();
    if (key && scannerPushGuard.code === key && ts - scannerPushGuard.ts < windowMs) return true;
    scannerPushGuard.code = key;
    scannerPushGuard.ts = ts;
    return false;
  }

  /* ================== ✅ CHECK DIGIT VALIDATOR (anti-misread) ==================
     Valida el dígito verificador para los formatos retail estándar.
     - true  → checksum correcto (lectura plausible)
     - false → checksum incorrecto (mala lectura casi seguro)
     - null  → longitud no estándar / no podemos validar (códigos internos, etc.)
     Atrapa la mayoría de los errores de un solo dígito en escáneres láser/CCD/cámara.
  */
  function validateBarcodeChecksum(digits) {
    const d = String(digits || "");
    if (!/^\d+$/.test(d)) return null;

    const computeCheck = (data, weights) => {
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        sum += (+data[i]) * weights[i % weights.length];
      }
      return (10 - (sum % 10)) % 10;
    };

    if (d.length === 13) {
      // EAN-13: pesos 1,3,1,3,... desde la izquierda sobre los primeros 12
      return computeCheck(d.slice(0, 12), [1, 3]) === +d[12];
    }
    if (d.length === 12) {
      // UPC-A: pesos 3,1,3,1,... sobre los primeros 11
      return computeCheck(d.slice(0, 11), [3, 1]) === +d[11];
    }
    if (d.length === 8) {
      // EAN-8: pesos 3,1,3,1,... sobre los primeros 7
      return computeCheck(d.slice(0, 7), [3, 1]) === +d[7];
    }
    if (d.length === 14) {
      // ITF-14 (cajas master): pesos 3,1,... sobre los primeros 13
      return computeCheck(d.slice(0, 13), [3, 1]) === +d[13];
    }
    return null; // longitud no estándar: no rechazamos, pero tampoco confirmamos
  }

  // ✅ Feedback visual breve cuando se rechaza un scan por checksum
  function flashScanError(message) {
    try {
      const el = ($inpCode && $inpCode.length) ? $inpCode[0] : null;
      if (el) {
        const prevOutline = el.style.outline;
        const prevBg = el.style.backgroundColor;
        const prevTrans = el.style.transition;
        el.style.transition = "outline 120ms ease, background-color 120ms ease";
        el.style.outline = "2px solid #e53935";
        el.style.backgroundColor = "#ffebee";
        setTimeout(() => {
          el.style.outline = prevOutline;
          el.style.backgroundColor = prevBg;
          el.style.transition = prevTrans;
        }, 700);
      }
    } catch (_){}
    if (message) console.warn("[BARCODE GUARD]", message);
  }

  function addToCartGuarded(pid, qty = 1) {
    const ts = now();
    // ✅ Solo bloquea duplicados del MISMO disparo; permite repetir escaneo rápido del mismo producto.
    if (String(lastAddGuard.pid) === String(pid) && (ts - lastAddGuard.ts) < LAST_ADD_GUARD_MS) return;
    lastAddGuard.pid = String(pid);
    lastAddGuard.ts  = ts;
    addToCart(pid, qty);
  }
  const burstAdd = { timer: null, last: null, windowMs: FAST_SCANNER_BURST_MS };
  function addToCartLastOnly(pid, qty = 1) {
    if (!pid || qty === 0) return;

    const payload = { pid: String(pid), qty: Number(qty) || 1 };
    burstAdd.last = payload;

    if (burstAdd.timer) { clearTimeout(burstAdd.timer); burstAdd.timer = null; }

    // ✅ Antes siempre esperaba 60 ms. Ahora, por defecto, agrega en el mismo ciclo.
    // El lastAddGuard sigue evitando doble agregado accidental del mismo pid.
    if ((Number(burstAdd.windowMs) || 0) <= 0) {
      addToCartGuarded(payload.pid, payload.qty);
      return;
    }

    burstAdd.timer = setTimeout(() => {
      burstAdd.timer = null;
      const { pid: p, qty: q } = burstAdd.last || {};
      addToCartGuarded(p, q);
    }, burstAdd.windowMs);
  }

  function addAutoProductToCartOnce(pid, qty = 1, ctx = {}) {
    if (!reserveAutoProductAdd(pid, qty, ctx)) return false;
    addToCartLastOnly(pid, qty);
    return true;
  }

  function addToCart(pid, qty = 1) {
    if (!pid || qty === 0) return;

    const key    = String(pid);
    const cached = productCache.get(key) || {};
    const name   = cached.nombre || `Producto ${pid}`;
    const cPrice = Number(cached.price) || 0;

    insertOrUpdateRowInstant(pid, qty, name, cPrice);

    queueMicrotask(() => {
      const $r = $tbody.find(`tr[data-pid='${pid}']`);
      if ($r.length) scheduleVerifyRowPrice($r, 90);
    });

    lastAddedPid = key;

    $inpNombre.val("");
    if ($inpId && $inpId.length) $inpId.val("");
    $inpCode.val("");
    $pid.val("");
    if ($cantidad && $cantidad.length) $cantidad.val("1");

    // ✅ NO robar foco si el modal está abierto
    queueMicrotask(() => {
      if (isModalOpen()) return;
      if ($inpCode.is(":visible")) { $inpCode.focus(); $inpCode[0]?.select?.(); }
    });
  }

  /* ================== Resolutores rápidos ================== */
  // ✅ BARCODE GUARD: este resolver SOLO devuelve un pid si el producto resultante
  //    tiene EXACTAMENTE el mismo código de barras que se le pidió resolver.
  //    Si la cache está stale o el servidor devuelve un producto con barcode
  //    distinto, retornamos null y el camino del scanner aborta el agregado.
  function resolveByBarcode(code) {
    if (!code) return Promise.resolve(null);
    const cleanCode = onlyDigits(String(code));
    if (!cleanCode) return Promise.resolve(null);

    // 1) Fast-path local: si el snapshot/cache ya tiene un match exacto y único,
    //    no esperamos la red para pintar el producto en el carrito.
    const localFast = getLocalExactBarcodeProduct(cleanCode);
    if (localFast) {
      setProductFields({
        nombre: localFast.name,
        pid: localFast.id,
        barcode: localFast.barcode || cleanCode,
        focusQty: false,
      });
      return Promise.resolve(localFast.id);
    }

    // 2) Si la cache local está ambigua o no tiene certeza, se conserva el camino original por servidor.
    if (isBarcodeLocallyAmbiguous(cleanCode)) {
      console.warn("[BARCODE GUARD] Codigo de barras ambiguo en cache local; se exige validacion del servidor", cleanCode);
    }

    // 3) Consultar servidor con validación estricta de la respuesta
    const params = { codigo_de_barras: cleanCode, sucursal_id: sucursalID, _ts: Date.now() };
    return asNativePromise($.getJSON(POR_COD_URL, params))
      .then((r) => {
        if (r && r.ambiguous) {
          flashScanError(r.error || ("Codigo de barras duplicado en inventario: " + cleanCode));
          return BARCODE_RESOLVE_BLOCKED;
        }
        if (!r || !r.exists) return null;
        const p = r.producto || {};
        const serverBarcodeDigits = onlyDigits(String(p.codigo_de_barras || ""));
        // ✅ El servidor DEBE devolver un producto cuyo barcode coincida con el
        //    solicitado. Cualquier otra cosa es un bug y se rechaza.
        if (!serverBarcodeDigits || serverBarcodeDigits !== cleanCode) {
          console.warn("[BARCODE GUARD] Servidor devolvió producto con barcode distinto", {
            requested: cleanCode,
            returned: serverBarcodeDigits,
            pid: p.id,
          });
          return null;
        }
        updateCache(p.id, { nombre:p.nombre, barcode:p.codigo_de_barras, precio_unitario:p.precio, cantidad_disponible:p.stock });
        setProductFields({ nombre: p.nombre, pid: p.id, barcode: p.codigo_de_barras, focusQty: false });
        return p.id;
      })
      .catch(() => null);
  }

  function setProductFields({
    nombre,
    pid,
    barcode,
    updateNameInput = true,
    updateCodeInput = true,
    focusQty = true
  }) {
    if (updateNameInput && nombre != null)  $inpNombre.val(onlyName(nombre));
    if (pid != null) {
      $pid.val(pid);
      if ($inpId && $inpId.length) $inpId.val(String(pid));
    }
    if (updateCodeInput && barcode != null) $inpCode.val(barcode);

    if ($pid.val()) {
      if ($cantidad && $cantidad.length) $cantidad.prop("disabled", false);
      if ($agregar && $agregar.length)  $agregar.prop("disabled", false);

      // ✅ NO robar foco si el modal está abierto
      queueMicrotask(()=> {
        if (!focusQty) return;
        if (isModalOpen()) return;
        if ($cantidad && $cantidad.length && $cantidad.is(":visible")) { $cantidad.focus().select(); }
      });
    }
  }

  /* ================== Infra autocomplete ================== */
  function attachAltEnterBypass(inputEl) {
    if (!inputEl) return;
    inputEl.addEventListener("keydown", function(e){
      if (e.key === "Enter" && e.altKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        $(inputEl).data("skipAcSelectOnce", true);
        try { $(inputEl).autocomplete("close"); } catch (_){}
      }
    }, true);
  }

  function blockNavOpenWhenEmpty($inp, minChars) {
    $inp.on("keydown", function(e){
      const navKeys = ["ArrowDown","ArrowUp","PageDown","PageUp","Home","End"];
      if (!navKeys.includes(e.key)) return;
      const v = this.value || "";
      if (v.length < minChars) {
        try { $inp.autocomplete("close"); } catch {}
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      }
    });
  }

  function createAC({
    $inp,
    sourceFn,
    onSelect,
    openIfEmpty=false,
    enableInstantSearch=true,
    minChars=1,
    onEnterFallback=null,
    enterTermKey=null,
    preferEnterFallback=null
  }) {
    attachAltEnterBypass($inp[0]);

    const getEnterTermKey = (value) => {
      if (typeof enterTermKey === "function") return String(enterTermKey(value) || "");
      return String(value || "").trim();
    };

    function consumeEnter(evt){
      evt.preventDefault();
      evt.stopPropagation();
      evt.stopImmediatePropagation();
    }

    function commitActiveAutocompleteItem(evt){
      const inst = $inp.autocomplete("instance");
      if (!inst) return false;

      const $menu = inst.menu && inst.menu.element ? inst.menu.element : $();
      const menuVisible = !!($menu.length && $menu.is(":visible"));
      if (!menuVisible) return false;

      let $active = inst.menu && inst.menu.active && inst.menu.active.length
        ? inst.menu.active
        : $menu.find(".ui-state-active, .ui-menu-item-wrapper.ui-state-active").first();

      if (!$active || !$active.length) return false;

      const $activeLi = $active.is("li") ? $active : $active.closest("li");
      const $activeWrapper = $active.hasClass("ui-menu-item-wrapper")
        ? $active
        : $active.find(".ui-menu-item-wrapper").first();

      let item = null;

      try { item = $activeLi.data("ui-autocomplete-item"); } catch (_) {}
      if (!item) {
        try { item = $activeWrapper.data("ui-autocomplete-item"); } catch (_) {}
      }

      // respaldo: toma el primer item visible si por alguna razón no quedó activo
      if (!item) {
        const $firstLi = $menu.find("li").has(".ui-menu-item-wrapper").first();
        try { item = $firstLi.data("ui-autocomplete-item"); } catch (_) {}
      }

      if (!item) return false;

      consumeEnter(evt);

      try { $inp.autocomplete("close"); } catch (_) {}
      onSelect?.(item);
      return true;
    }

    function commitFallbackAutocompleteItem(evt){
      if (typeof onEnterFallback !== "function") return false;

      const term = String($inp.val() || "").trim();
      if (!term || (term.length < minChars && !openIfEmpty)) return false;

      const termKey = getEnterTermKey(term);
      if (!termKey) return false;

      if ($inp.data("enterFallbackPendingKey") === termKey) {
        consumeEnter(evt);
        return true;
      }

      let result = null;
      try { result = onEnterFallback(term); } catch (_) { return false; }
      if (!result) return false;

      consumeEnter(evt);
      $inp.data("enterFallbackPendingKey", termKey);

      Promise.resolve(result)
        .then((item) => {
          if (getEnterTermKey($inp.val() || "") !== termKey) return;

          if (!item) {
            try { $inp.autocomplete("search", term); } catch (_) {}
            return;
          }

          try { $inp.autocomplete("close"); } catch (_) {}
          onSelect?.(item);
        })
        .catch(() => {
          if (getEnterTermKey($inp.val() || "") === termKey) {
            try { $inp.autocomplete("search", term); } catch (_) {}
          }
        })
        .finally(() => {
          if ($inp.data("enterFallbackPendingKey") === termKey) {
            $inp.removeData("enterFallbackPendingKey");
          }
        });

      return true;
    }

    $inp.on("keydown.autocompleteEnterFix", function(e){
      if (e.key !== "Enter" || e.altKey || e.ctrlKey || e.metaKey) return;
      if ($inp.data("skipAcSelectOnce")) { $inp.data("skipAcSelectOnce", false); return; }
      if (typeof preferEnterFallback === "function" && preferEnterFallback($inp.val() || "")) {
        if (commitFallbackAutocompleteItem(e)) return;
      }
      if (commitActiveAutocompleteItem(e)) return;
      commitFallbackAutocompleteItem(e);
    });

    $inp.autocomplete({
      minLength: minChars,
      delay: 0,
      autoFocus: true,
      appendTo: "body",
      position:{ my:"left top+6", at:"left bottom", collision:"flipfit" },
      source: sourceFn,
      open(){ $inp.autocomplete("widget").css("z-index", 3000); },
      select(_e, ui){
        if ($inp.data("skipAcSelectOnce")) { $inp.data("skipAcSelectOnce", false); return false; }
        if (!ui || !ui.item) return false;
        onSelect?.(ui.item);
        return false;
      }
    });

    $inp.on("focus", function(){
      const v = this.value || "";
      if (v.length < minChars && !openIfEmpty) { try { $inp.autocomplete("close"); } catch {} return; }
      $inp.autocomplete("search", v);
    });

    if (enableInstantSearch) {
      let raf = null;
      $inp.on("input", function(){
        // ✅ marcar tipeo real del usuario para gating del autopick
        lastUserInputTS.set($inp[0], now());
        const v = this.value || "";
        if (v.length < minChars && !openIfEmpty) { try { $inp.autocomplete("close"); } catch {} return; }
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(()=> $inp.autocomplete("search", v));
      });
    }

    blockNavOpenWhenEmpty($inp, openIfEmpty ? 0 : minChars);
  }

  function applyPriceTemplate($inp, {mode="name"} = {}) {
    const inst = $inp.autocomplete("instance");
    if (!inst) return;
    inst._renderItem = function(ul, item) {
      const name = (item.name || item.label || item.value || "").toString();
      const productId = String(item.id ?? "").trim();
      const idBadge = productId
        ? `<span class="ac-product-id">ID ${productId}</span>`
        : "";
      let left = `<span class="ac-name">${name}</span> ${idBadge}`;

      if (mode === "code" && item.barcode) {
        left = `<span class="ac-code">${item.label}</span><span class="ac-sep"> — </span><span class="ac-name">${name}</span> ${idBadge}`;
      } else if (mode === "id") {
        left = `${idBadge}<span class="ac-sep"> — </span><span class="ac-name">${onlyName(item.name || "")}</span>`;
      }

      const priceNum = Number(item.price);
      const right = (Number.isFinite(priceNum) && priceNum > 0) ? `<span class="ac-price">${money(priceNum)}</span>` : "";
      const $li = $("<li>");
      const $content = $(
        `<div class="ac-row">
           <div class="ac-left">${left}</div>
           <div class="ac-right">${right}</div>
         </div>`
      );
      return $li.append($content).appendTo(ul);
    };
  }

  (function injectACStyles(){
    const css =
`.ui-autocomplete .ac-row{display:flex;align-items:center;justify-content:space-between;gap:.75rem;max-width:72ch}
.ui-autocomplete .ac-left{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ui-autocomplete .ac-code{opacity:.85}
.ui-autocomplete .ac-name{font-weight:500}
.ui-autocomplete .ac-product-id{display:inline-flex;align-items:center;margin-left:.45rem;padding:.1rem .42rem;border-radius:999px;background:#e8f1ff;color:#244f96;font-size:.72rem;font-weight:800;white-space:nowrap}
.ui-autocomplete .ac-left>.ac-product-id:first-child{margin-left:0}
.ui-autocomplete .ac-price{opacity:.85}
.pending-price .price-cell{opacity:.6}
.pending-price .subtotal-cell{opacity:.6}
#myModal[data-loading-prices="1"] #confirmar-pago{opacity:.7;pointer-events:none}
.promo-bolsa-badge{display:inline-block;margin-left:.4rem;padding:.1rem .45rem;border-radius:999px;background:rgba(77,166,255,.12);font-size:.75rem;font-weight:700;color:#153060}
.promo-free-line .subtotal-cell,.promo-partial-line .subtotal-cell{font-weight:700}`;
    const id = "ac-price-style";
    if (!document.getElementById(id)) {
      const tag = document.createElement("style");
      tag.id = id; tag.textContent = css; document.head.appendChild(tag);
    }
  })();

  /* ============ Búsquedas ultra-rápidas (red) ============ */
  const netSearchName = throttleAsync(async (term, signal) => {
    const tU = normalizeUnits(term);
    const url = PRODUCTO_URL + "?" + new URLSearchParams({ term: tU || term, sucursal_id: sucursalID, limit: 40, _ts: Date.now() });
    const r = await fetch(url, { signal, cache: "no-store" }).catch(()=>null);
    if (!r || !r.ok) return [];
    const d = await r.json().catch(()=>({results:[]}));
    return (d.results||[]).map(p => ({
      id:p.id,
      name:p.text,
      barcode:(p.barcode||p.codigo_de_barras||""),
      price:p.precio,
      stock:p.stock
    }));
  }, 45);

  const netSearchCode = throttleAsync(async (term, signal) => {
    const info = classifyQuery(term);
    if (info.isBarcodeLike) {
      const dBar = await fetch(AC_BARRAS_URL + "?" + new URLSearchParams({
        term: info.digits,
        sucursal_id: sucursalID,
        limit: 25,
        exact: "1",
        _ts: Date.now()
      }), { signal, cache: "no-store" })
        .then(r=> r && r.ok ? r.json() : {results:[]}).catch(()=>({results:[]}));

      return (dBar.results || []).map(p => ({
        id: p.id,
        name: (p.text || p.nombre || ""),
        barcode: (p.barcode || p.codigo_de_barras || ""),
        price: p.precio,
        stock: p.stock
      }));
    }

    const [dCod, dBar] = await Promise.all([
      fetch(AC_CODIGO_URL + "?" + new URLSearchParams({ term, sucursal_id: sucursalID, limit: 25, _ts: Date.now() }), { signal, cache: "no-store" })
        .then(r=> r && r.ok ? r.json() : {results:[]}).catch(()=>({results:[]})),
      fetch(AC_BARRAS_URL + "?" + new URLSearchParams({ term, sucursal_id: sucursalID, limit: 25, _ts: Date.now() }), { signal, cache: "no-store" })
        .then(r=> r && r.ok ? r.json() : {results:[]}).catch(()=>({results:[]})),
    ]);
    const net = [];
    const seen = new Set();
    const push = (p) => {
      const id = p.id;
      const barcode = (p.barcode || p.codigo_de_barras || "");
      const name = (p.text || p.nombre || "");
      const k = String(id)+"::"+barcode;
      if (seen.has(k)) return;
      seen.add(k);
      net.push({ id, name, barcode, price:p.precio, stock:p.stock });
    };
    (dBar.results||[]).forEach(push);
    (dCod.results||[]).forEach(push);
    return net;
  }, 45);

  const netSearchId = throttleAsync(async (term, signal) => {
    const t = onlyDigits(term);
    if (!t || !PRODUCTO_ID_URL) return [];
    const url = PRODUCTO_ID_URL + "?" + new URLSearchParams({ term: t, sucursal_id: sucursalID, limit: 40, _ts: Date.now() });
    const r = await fetch(url, { signal, cache: "no-store" }).catch(()=>null);
    if (!r || !r.ok) return [];
    const d = await r.json().catch(()=>({results:[]}));
    return (d.results||[]).map(p => ({
      id: p.id,
      name: p.text || p.nombre || "",
      barcode: (p.barcode || p.codigo_de_barras || ""),
      price: p.precio,
      stock: p.stock
    }));
  }, 45);

  let inflightNameAC = null;
  let inflightCodeAC = null;
  let inflightIdAC   = null;
  let autoPickGuardTS = 0;

  function maybeAutoPickBarcode(term, items, $input){
    const info = classifyQuery(term);
    if (!info.isBarcodeLike || !hasSucursal() || !Array.isArray(items) || items.length !== 1) return;
    if (isBarcodeAutocompleteSuppressed(info.digits)) return;
    if (isBarcodeResolveActive(info.digits)) return;

    // ✅ FIX escaneo: solo auto-agregar si el CÓDIGO DE BARRAS del candidato coincide
    //    EXACTAMENTE con los dígitos escaneados. Esto evita que un match parcial
    //    (substring/prefijo) o una coincidencia accidental con un ID de producto
    //    agregue el producto equivocado al carrito.
    //    Si no es match exacto, dejamos que el menú de sugerencias se muestre.
    const item = items[0];
    const itemDigits = onlyDigits(String(item.barcode || ""));
    if (!itemDigits || itemDigits !== info.digits) return;

    // ✅ FIX agregado fantasma:
    //  1) Solo permitir autopick si el input que originó la búsqueda existe y
    //     todavía tiene el foco (si el usuario ya se movió a otro campo, no
    //     agregamos un producto a sus espaldas).
    //  2) Solo permitir autopick si hubo un evento de tipeo real (input) en ese
    //     campo dentro de AUTO_PICK_RECENCY_MS. Esto evita que un focus, un
    //     re-search interno o una respuesta de red tardía dispare un agregado
    //     "unos instantes después".
    const inputEl = ($input && $input.length) ? $input[0] : null;
    if (!inputEl) return;
    if (document.activeElement !== inputEl) return;
    const lastInputTS = Number(lastUserInputTS.get(inputEl) || 0);
    if (!lastInputTS || (now() - lastInputTS) > AUTO_PICK_RECENCY_MS) return;

    const ts = Date.now();
    if (ts - autoPickGuardTS < 250) return;
    if (String(lastAddGuard.pid) === String(item.id) && (ts - lastAddGuard.ts) < 1200) return;
    autoPickGuardTS = ts;

    const added = addProductFromAutocomplete(item, {
      source: "barcode-autopick",
      $input: $inpCode,
      term,
      barcode: item.barcode || info.digits,
    });
    if (!added) return;

    try { $inpCode.autocomplete("close"); } catch {}
    try { $inpNombre.autocomplete("close"); } catch {}
  }

  function toACItems(raw, {labelMode="name"} = {}) {
    return (raw || []).map(p => {
      const barcode = p.barcode || "";
      const name = p.name || "";
      const lbl = labelMode === "code"
        ? (barcode || name || String(p.id))
        : labelMode === "id"
          ? String(p.id)
          : (name || barcode || String(p.id));
      return { id:p.id, name, barcode, label: lbl, value: lbl, price:p.price, stock:p.stock };
    });
  }

  function addStrictBarcodeFromAutocomplete(item, digits, { source = "barcode-ac" } = {}) {
    const clean = onlyDigits(digits);
    if (!clean || !itemMatchesExactBarcode(item, clean)) {
      flashScanError("Producto bloqueado: no coincide con el codigo escaneado " + clean);
      closeProductAutocompleteMenus();
      return false;
    }

    closeProductAutocompleteMenus();
    const resolveSeq = beginBarcodeResolve(clean);
    resolveByBarcode(clean).then(pid => {
      if (pid === BARCODE_RESOLVE_BLOCKED) return;
      if (!pid) {
        flashScanError("Codigo de barras no encontrado: " + clean);
        return;
      }
      if (String(pid) !== String(item.id)) {
        flashScanError("Producto bloqueado: el servidor resolvio otro producto para " + clean);
        return;
      }

      const finalRec = productCache.get(String(pid));
      const finalBarcodeDigits = onlyDigits(String(finalRec?.barcode || ""));
      if (!finalBarcodeDigits || finalBarcodeDigits !== clean) {
        flashScanError("Producto bloqueado: la validacion final no coincide con " + clean);
        return;
      }

      if (wasRecentlyAutoAddedByBarcode(clean, pid)) return;
      suppressBarcodeAutocompleteAdd(clean, 900);
      if (addAutoProductToCartOnce(pid, 1, {
        source,
        term: clean,
        barcode: clean,
        pidTtlMs: 900,
        termTtlMs: 900,
        barcodeTtlMs: 900,
      })) {
        rememberBarcodeAutoAdd(clean, pid);
      }
    }).finally(() => {
      endBarcodeResolve(clean, resolveSeq);
    });

    return true;
  }

  function addProductFromAutocomplete(item, { source = "product-ac", $input = null, term = "", barcode = "" } = {}) {
    if (!item || item.id == null) return false;

    const intentTerm = String(
      term ||
      ($input && $input.length ? $input.val() : "") ||
      item.label ||
      item.value ||
      item.name ||
      ""
    );
    const intentBarcode = barcode || item.barcode || "";
    const strictDigits = barcodeDigitsForStrictTerm(intentTerm);
    if (strictDigits && !itemMatchesExactBarcode({ barcode: intentBarcode }, strictDigits)) {
      flashScanError("Producto bloqueado: no coincide con el codigo escaneado " + strictDigits);
      closeProductAutocompleteMenus();
      return false;
    }
    if (strictDigits) {
      return addStrictBarcodeFromAutocomplete({ ...item, barcode: intentBarcode }, strictDigits, { source });
    }

    if (!addAutoProductToCartOnce(item.id, 1, {
      source,
      term: intentTerm,
      barcode: intentBarcode,
    })) {
      closeProductAutocompleteMenus();
      return false;
    }

    updateCache(item.id, {
      nombre: item.name,
      barcode: item.barcode,
      precio_unitario: item.price,
      cantidad_disponible: item.stock,
    });
    setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode || "" });
    bumpPick(item.id);
    closeProductAutocompleteMenus();
    return true;
  }

  function productEnterLabelMode(mode){
    return mode === "id" ? "id" : mode === "code" ? "code" : "name";
  }

  function productEnterKey(mode){
    return function(value){
      const term = String(value || "").trim();
      if (mode === "id") return onlyDigits(term);
      if (mode === "code") {
        const info = classifyQuery(term);
        return info.isPureDigits ? info.digits : normalizeUnits(term);
      }
      return normalizeUnits(term);
    };
  }

  function productRawToACItem(raw, mode){
    if (!raw || raw.id == null) return null;
    return toACItems([raw], { labelMode: productEnterLabelMode(mode) })[0] || null;
  }

  function cachedProductToACItem(pid, mode){
    const key = String(pid || "").trim();
    if (!key) return null;

    const idx = preIndex.get(sucursalID);
    if (idx && !idx.map.has(key)) return null;

    const rec = productCache.get(key);
    if (!rec) return null;

    return productRawToACItem({
      id: key,
      name: rec.nombre || "",
      barcode: rec.barcode || "",
      price: rec.price,
      stock: rec.stock
    }, mode);
  }

  function indexRefToACItem(ref, mode){
    if (!ref || ref.id == null) return null;
    return productRawToACItem({
      id: ref.id,
      name: ref.name || "",
      barcode: ref.barcode || "",
      price: ref.price,
      stock: ref.stock
    }, mode);
  }

  function exactBarcodeItemFromIndex(digits, idx, mode){
    if (!digits || !idx || !Array.isArray(idx.codes)) return null;
    const exact = idx.codes.find(c => c.nbarcode && c.nbarcode === digits);
    if (!exact) return null;

    const ref = idx.map.get(String(exact.id));
    return productRawToACItem({
      id: exact.id,
      name: ref?.name || "",
      barcode: ref?.barcode || exact.label || digits,
      price: ref?.price ?? exact.price,
      stock: ref?.stock ?? exact.stock
    }, mode);
  }

  function pickProductLocalForEnter(term, mode){
    const clean = String(term || "").trim();
    if (!clean || !hasSucursal()) return null;

    const idx = preIndex.get(sucursalID);

    if (mode === "id") {
      const digits = onlyDigits(clean);
      if (!digits) return null;
      if (idx) {
        const exact = idx.map.get(String(digits));
        if (exact) return indexRefToACItem(exact, mode);
        return productRawToACItem(rankIdLocal(digits, idx, 1)[0], mode);
      }
      return cachedProductToACItem(digits, mode);
    }

    if (mode === "code") {
      const info = classifyQuery(clean);
      if (info.digits) {
        const exactFromIndex = exactBarcodeItemFromIndex(info.digits, idx, mode);
        if (exactFromIndex) return exactFromIndex;

        const exactCachedPid = barcodeIndex.get(info.digits);
        const exactCached = cachedProductToACItem(exactCachedPid, mode);
        if (exactCached) return exactCached;
      }

      if (info.isBarcodeLike) return null;

      if (idx) {
        const ranked = buildLocalSmart(clean, idx, 1)[0];
        return productRawToACItem(ranked, mode);
      }

      const cachedPid = productCache.has(String(clean)) ? String(clean) : "";
      return cachedProductToACItem(cachedPid, mode);
    }

    const exactNamePid = nameIndex.get(onlyName(clean).toLowerCase());
    const exactName = cachedProductToACItem(exactNamePid, mode);
    if (exactName) return exactName;

    if (idx) return productRawToACItem(buildLocalSmart(clean, idx, 1)[0], mode);
    return null;
  }

  async function pickProductForEnter(term, mode){
    const termInfo = classifyQuery(term);
    const strictBarcodeEnter = mode !== "id" && termInfo.isBarcodeLike;
    const local = strictBarcodeEnter ? null : pickProductLocalForEnter(term, mode);
    if (local) return local;
    if (!hasSucursal()) return null;

    if (!strictBarcodeEnter) {
      try {
        await ensureCatalog(sucursalID);
        const afterCatalog = pickProductLocalForEnter(term, mode);
        if (afterCatalog) return afterCatalog;
      } catch {}
    }

    try {
      const controller = new AbortController();
      const raw = strictBarcodeEnter
        ? await netSearchCode(term, controller.signal)
        : mode === "id"
        ? await netSearchId(term, controller.signal)
        : mode === "code"
          ? await netSearchCode(term, controller.signal)
          : await netSearchName(term, controller.signal);

      const first = Array.isArray(raw) ? raw[0] : null;
      if (!first) return null;

      let selected = first;
      if (mode === "code" || strictBarcodeEnter) {
        const info = classifyQuery(term);
        const digits = info.digits;
        const exactItems = uniqueExactBarcodeItemsForTerm(term, raw);
        const exact = exactItems.find(item => {
          const itemDigits = onlyDigits(String(item?.barcode || ""));
          return itemDigits && itemDigits === digits;
        });
        if (exact) selected = exact;
        else if (info.isBarcodeLike) return null;
      }

      updateCache(selected.id, {
        nombre: selected.name,
        barcode: selected.barcode,
        precio_unitario: selected.price,
        cantidad_disponible: selected.stock
      });

      return productRawToACItem(selected, mode);
    } catch {
      return null;
    }
  }

  function sourceSmartFactory({ cacheLRU, labelMode }) {
    // ✅ Determinar el input que originó esta búsqueda para gating de autopick
    const $sourceInput = (labelMode === "code") ? $inpCode : $inpNombre;
    return function(req, resp){
      (async ()=>{
        const term = (req.term||"").trim();
        const qU = normalizeUnits(term);
        if (!qU || !hasSucursal()) { resp([]); return; }

        const info = classifyQuery(term);
        const strictBarcodeLookup = info.isBarcodeLike;
        const cacheKey = `${sucursalID}|smart|${labelMode}|${qU}|${info.digits}`;
        if (!strictBarcodeLookup) {
          const cached = cacheLRU.get(cacheKey);
          if (cached) { resp(cached); maybeAutoPickBarcode(term, cached, $sourceInput); return; }
        }

        const idx = preIndex.get(sucursalID);
        let locals = [];
        if (idx && !strictBarcodeLookup) {
          const rawLocal = (labelMode === "code" && info.isBarcodeLike)
            ? rankCodeLocal(term, idx, 40)
            : buildLocalSmart(term, idx, 40);
          locals = toACItems(rawLocal, { labelMode });
          for (const it of locals) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });
        }

        resp(locals);
        if (!strictBarcodeLookup) cacheLRU.set(cacheKey, locals);
        maybeAutoPickBarcode(term, locals, $sourceInput);

        try {
          const useCode = info.isBarcodeLike;
          const controllerKey = (labelMode === "code") ? "code" : "name";

          if (controllerKey === "name") { inflightNameAC?.abort?.(); inflightNameAC = new AbortController(); }
          else { inflightCodeAC?.abort?.(); inflightCodeAC = new AbortController(); }

          const signal = (controllerKey === "name") ? inflightNameAC.signal : inflightCodeAC.signal;
          const netRaw = useCode ? await netSearchCode(term, signal) : await netSearchName(term, signal);
          if (!Array.isArray(netRaw) || !netRaw.length) return;

          let netItems = toACItems(netRaw, { labelMode });
          if (strictBarcodeLookup) {
            netItems = uniqueExactBarcodeItemsForTerm(term, netItems);
            if (!netItems.length) {
              const current = (labelMode === "code")
                ? normalizeUnits(String($inpCode.val()||""))
                : normalizeUnits(String($inpNombre.val()||""));
              if (current === qU) resp([]);
              return;
            }
          }
          for (const it of netItems) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });

          const seen = new Set(locals.map(x=>String(x.id)+"::"+(x.barcode||"")));
          const merged = locals.slice();
          for (const it of netItems) {
            const k = String(it.id)+"::"+(it.barcode||"");
            if (!seen.has(k)) merged.push(it);
            if (merged.length >= 40) break;
          }

          if (!strictBarcodeLookup) cacheLRU.set(cacheKey, merged);

          const current = (labelMode === "code")
            ? normalizeUnits(String($inpCode.val()||""))
            : normalizeUnits(String($inpNombre.val()||""));

          if (current === qU) {
            resp(merged);
            // El gating dentro de maybeAutoPickBarcode (foco + recencia) impide
            // que esta llamada diferida agregue un producto si el usuario ya
            // se movió de campo o dejó de tipear.
            maybeAutoPickBarcode(term, merged, $sourceInput);
          }
        } catch {}
      })();
    };
  }

  function sourceIdFactory(){
    return function(req, resp){
      (async ()=>{
        const termRaw = (req && typeof req.term === "string") ? req.term : "";
        const term = onlyDigits(termRaw);
        if (!term || !hasSucursal()) { resp([]); return; }

        const cacheKey = `${sucursalID}|id|${term}`;
        const cached = termCacheId.get(cacheKey);
        if (cached) { resp(cached); return; }

        const idx = preIndex.get(sucursalID);
        let locals = [];
        if (idx) {
          const rawLocal = rankIdLocal(term, idx, 40);
          locals = toACItems(rawLocal, { labelMode:"id" });
          for (const it of locals) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });
        }

        resp(locals);
        termCacheId.set(cacheKey, locals);

        try {
          inflightIdAC?.abort?.();
          inflightIdAC = new AbortController();
          const netRaw = await netSearchId(term, inflightIdAC.signal);
          if (!Array.isArray(netRaw) || !netRaw.length) return;

          const netItems = toACItems(netRaw, { labelMode:"id" });
          for (const it of netItems) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });

          const seen = new Set(locals.map(x=>String(x.id)));
          const merged = locals.slice();
          for (const it of netItems) {
            const k = String(it.id);
            if (!seen.has(k)) merged.push(it);
            if (merged.length >= 40) break;
          }

          termCacheId.set(cacheKey, merged);

          const current = onlyDigits(String($inpId.val()||""));
          if (current === term) resp(merged);
        } catch {}
      })();
    };
  }

  /* ================== Crear AC producto (Nombre / Código) ================== */
  createAC({
    $inp: $inpNombre,
    minChars: 1,
    openIfEmpty: false,
    sourceFn: sourceSmartFactory({ cacheLRU: termCacheName, labelMode: "name" }),
    onEnterFallback: (term) => pickProductForEnter(term, "name"),
    enterTermKey: productEnterKey("name"),
    onSelect: (item) => {
      addProductFromAutocomplete(item, { source: "name-ac", $input: $inpNombre });
    }
  });
  applyPriceTemplate($inpNombre, { mode: "name" });

  createAC({
    $inp: $inpCode,
    minChars: 1,
    openIfEmpty: false,
    sourceFn: sourceSmartFactory({ cacheLRU: termCacheCode, labelMode: "code" }),
    onEnterFallback: (term) => pickProductForEnter(term, "code"),
    enterTermKey: productEnterKey("code"),
    preferEnterFallback: (term) => classifyQuery(term).isBarcodeLike,
    onSelect: (item) => {
      addProductFromAutocomplete(item, { source: "code-ac", $input: $inpCode, barcode: item.barcode || "" });
    }
  });
  applyPriceTemplate($inpCode, { mode: "code" });

  /* ================== AC independiente SOLO POR ID ================== */
  if ($inpId && $inpId.length) {
    createAC({
      $inp: $inpId,
      minChars: 1,
      openIfEmpty: false,
      sourceFn: sourceIdFactory(),
      onEnterFallback: (term) => pickProductForEnter(term, "id"),
      enterTermKey: productEnterKey("id"),
      onSelect: (item) => {
        addProductFromAutocomplete(item, { source: "id-ac", $input: $inpId, barcode: item.barcode || "" });
      }
    });
    applyPriceTemplate($inpId, { mode: "id" });

    $inpId.on("input", function(){
      const d = onlyDigits(this.value);
      if (this.value !== d) this.value = d;
      if (d && hasSucursal()) {
        const idx = preIndex.get(sucursalID);
        const ref = idx?.map?.get(String(d));
        if (ref) setProductFields({ nombre: ref.name, pid: ref.id, barcode: ref.barcode });
      }
    });
  }

  /* ================== LIVE SNAPSHOT SYNC (precio/barcode AC) ================== */
  const catalogSigBySucursal = new Map();

  function buildCatalogSignature(items) {
    const parts = [];
    for (let i = 0; i < items.length; i++) {
      const p = items[i] || {};
      parts.push([p.id, (p.price ?? ""), (p.barcode ?? ""), (p.name ?? "")].join("|"));
    }
    return parts.join("||");
  }

  function initCatalogSignature(sid) {
    try {
      const items = catalogBySucursal.get(sid) || [];
      catalogSigBySucursal.set(String(sid), buildCatalogSignature(items));
    } catch {}
  }

  function applySnapshotIfChanged(sid, items) {
    sid = String(sid || "");
    if (!sid) return false;
    if (!Array.isArray(items)) items = [];

    const newSig = buildCatalogSignature(items);
    const oldSig = catalogSigBySucursal.get(sid);
    if (oldSig && oldSig === newSig) return false;

    catalogSigBySucursal.set(sid, newSig);

    catalogBySucursal.set(sid, items);
    hydrateFromCatalog(items);
    buildPreIndexFor(sid, items);

    try {
      localStorage.setItem(`catalog_${sid}`, JSON.stringify(items));
      localStorage.setItem(`catalog_${sid}_ts`, String(now()));
    } catch {}

    try { termCacheName.map.clear(); } catch {}
    try { termCacheCode.map.clear(); } catch {}
    try { termCacheId.map.clear(); } catch {}

    queueMicrotask(() => {
      try { const w = $inpNombre.autocomplete("widget"); if (w && w.is(":visible")) $inpNombre.autocomplete("search", $inpNombre.val() || ""); } catch (_){}
      try { const w = $inpCode.autocomplete("widget"); if (w && w.is(":visible")) $inpCode.autocomplete("search", $inpCode.val() || ""); } catch (_){}
      try {
        if ($inpId && $inpId.length) {
          const w = $inpId.autocomplete("widget");
          if (w && w.is(":visible")) $inpId.autocomplete("search", $inpId.val() || "");
        }
      } catch (_){}
    });

    return true;
  }

  async function fetchSnapshotNoStore(sid) {
    const url = SNAPSHOT_URL + "?" + new URLSearchParams({ sucursal_id: sid, _ts: Date.now() });
    const r = await fetch(url, { cache: "no-store" }).catch(() => null);
    if (!r || !r.ok) return null;
    const d = await r.json().catch(() => null);
    const items = (d && Array.isArray(d.results)) ? d.results : [];
    return items;
  }

  function startCatalogPolling(sid, { intervalMs = 2500 } = {}) {
    stopCatalogPolling();
    const pollSid = String(sid || "");
    if (!pollSid) return;

    async function tick() {
      if (!hasSucursal()) return;
      if (String(sucursalID) !== String(pollSid)) return;
      if (document.visibilityState !== "visible") return;

      const items = await fetchSnapshotNoStore(pollSid);
      if (!items) return;
      applySnapshotIfChanged(pollSid, items);
    }

    tick();
    catalogPollTimer = setInterval(tick, Math.max(900, intervalMs|0));
  }

  document.addEventListener("visibilitychange", () => {
    if (!hasSucursal()) return;
    if (document.visibilityState === "visible") startCatalogPolling(sucursalID, { intervalMs: 2500 });
  });

  /* ================== Prefill sucursal/punto ================== */
  if (sucursalID) {
    const serverSucursalName = String(
      window.ventaSucursalNombreServidor
      || $("#sucursal_autocomplete").val()
      || ""
    );
    $("#sucursal_autocomplete").val(serverSucursalName);
    $("#sucursal_id").val(sucursalID);
    loadPickBoost(sucursalID);

    ensureCatalog(sucursalID).then(() => {
      initCatalogSignature(sucursalID);
      startCatalogPolling(sucursalID, { intervalMs: 2500 });
    });
  }

  /* ================== AC Sucursal / Punto ================== */
  async function fetchJSON(url){ try{ const r=await fetch(url, { cache: "no-store" }); if(!r.ok) return null; return await r.json(); } catch { return null; } }
  async function fetchAny(baseUrl, paramsList) {
    for (const p of paramsList) {
      const qs = new URLSearchParams(p);
      const data = await fetchJSON(baseUrl + "?" + qs.toString());
      const arr = (data && data.results) || [];
      if (Array.isArray(arr) && arr.length) return arr;
    }
    return [];
  }
  const LS_SUC = "ac_sucursales_cache";
  const LS_PP  = (sid)=>`ac_puntos_cache_${sid||"none"}`;

  createAC({
    $inp: $("#sucursal_autocomplete"),
    minChars: 0,
    openIfEmpty: true,
    sourceFn: function(req, resp){
      (async ()=>{
        const term = (req && typeof req.term === "string") ? req.term : "";
        const results = await fetchAny(SUCURSAL_URL, [
          { term: term || "", limit: 50 },
          { limit: 50 },
          { term: " ", limit: 50 }
        ]);
        let items = results.map(r=>({ id:r.id, label:r.text, value:r.text, name:r.text }));
        if (!items.length) {
          try { items = JSON.parse(localStorage.getItem(LS_SUC) || "[]"); } catch { items = []; }
        } else {
          try { localStorage.setItem(LS_SUC, JSON.stringify(items)); } catch {}
        }
        resp(items);
      })();
    },
    onSelect: async ({ id, label }) => {
      if (!guardSaleDraftEditing() || saleSubmitting) return;
      if (productos.length) {
        alert("Vacía o finaliza el carrito antes de cambiar de sucursal.");
        return;
      }
      try { persistSaleDraftNow(); } catch (_) {}
      stopCatalogPolling();

      sucursalID = String(id).match(/\d+/)?.[0] || "";
      $("#sucursal_id").val(sucursalID);
      $("#sucursal_autocomplete").val(label);
      try {
        localStorage.setItem("sucursalID", sucursalID);
        localStorage.setItem("sucursalName", label);
      } catch (_) {}

      let ppSuc = "";
      try { ppSuc = localStorage.getItem("puntopagoSucursalID") || ""; } catch (_) {}
      if (ppSuc && ppSuc !== String(sucursalID)) {
        $("#puntopago_autocomplete").val(""); $("#puntopago_id").val("");
        try {
          localStorage.removeItem("puntopagoID");
          localStorage.removeItem("puntopagoName");
          localStorage.removeItem("puntopagoSucursalID");
        } catch (_) {}
      }

      if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
      if ($agregar && $agregar.length)  $agregar.prop("disabled", true);

      loadPickBoost(sucursalID);

      await ensureCatalog(sucursalID, { force:true });
      initCatalogSignature(sucursalID);
      startCatalogPolling(sucursalID, { intervalMs: 2500 });
      queueMicrotask(() => {
        if (productos.length) scheduleSaleDraftSave();
        else offerSaleDraftForCurrentScope();
      });
    }
  });

  createAC({
    $inp: $("#puntopago_autocomplete"),
    minChars: 0,
    openIfEmpty: true,
    sourceFn: function(req, resp){
      (async ()=>{
        if (!hasSucursal()) { resp([]); return; }
        const term = (req && typeof req.term === "string") ? req.term : "";
        const results = await fetchAny(PUNTOPAGO_URL, [
          { term: term || "", sucursal_id: sucursalID, limit: 50 },
          { sucursal_id: sucursalID, limit: 50 },
          { term: " ", sucursal_id: sucursalID, limit: 50 }
        ]);
        let items = results.map(r=>({ id:r.id, label:r.text, value:r.text, name:r.text }));
        const key = LS_PP(sucursalID);
        if (!items.length) {
          try { items = JSON.parse(localStorage.getItem(key) || "[]"); } catch { items = []; }
        } else {
          try { localStorage.setItem(key, JSON.stringify(items)); } catch {}
        }
        resp(items);
      })();
    },
    onSelect: ({ id, label }) => {
      if (!guardSaleDraftEditing() || saleSubmitting) return;
      const previousDraftKey = saleDraftLastStorageKey || saleDraftStorageKey();
      if (productos.length && !persistSaleDraftNow()) return;
      const destinationDraftKey = saleDraftStorageKeyForPoint(id);
      if (
        productos.length
        && destinationDraftKey !== previousDraftKey
        && hasStoredSaleDraft(destinationDraftKey)
      ) {
        alert("Ese punto de pago ya tiene otra venta pendiente. Recupérala o descártala antes de mover este carrito.");
        return;
      }
      $("#puntopago_autocomplete").val(label);
      $("#puntopago_id").val(id);
      try {
        localStorage.setItem("puntopagoID", id);
        localStorage.setItem("puntopagoName", label);
        localStorage.setItem("puntopagoSucursalID", sucursalID || "");
      } catch (_) {}
      queueMicrotask(() => {
        if (productos.length) {
          const saved = persistSaleDraftNow();
          const currentDraftKey = saleDraftStorageKey();
          if (saved && previousDraftKey && previousDraftKey !== currentDraftKey) {
            removeSaleDraftByKey(previousDraftKey);
          }
        }
        else offerSaleDraftForCurrentScope();
      });
    }
  });

  // El punto de pago es una decisión de la sesión actual. Si el cajero edita
  // o borra el texto, el identificador deja de ser válido de inmediato. No se
  // restaura automáticamente desde localStorage porque el mismo navegador
  // puede ser usado después por otro cajero.
  $("#puntopago_autocomplete").on("input", () => {
    if (saleSubmitting) return;
    try { persistSaleDraftNow(); } catch (_) {}
    $("#puntopago_id").val("");
    try {
      localStorage.removeItem("puntopagoID");
      localStorage.removeItem("puntopagoName");
      localStorage.removeItem("puntopagoSucursalID");
    } catch {}
  });

  /* ================== Cliente ================== */
  const CLIENTE_AC_LIMIT = 12;
  const clienteAcCache = new Map();
  let clienteAcActive = null;
  let clienteAcSeq = 0;

  function clienteTermKey(term) {
    return String(term || "").trim().toLowerCase();
  }

  function mapClienteAc(c) {
    return {
      id: c.id,
      label: c.text,
      value: c.text,
      name: c.text,
      documento: c.documento || "",
      isEmployee: !!(c.is_employee || c.isEmployee),
      employeeName: c.employee_name || c.employeeName || "",
      employeeHasUser: !!(c.employee_has_user || c.employeeHasUser),
      employeeIsWebMaster: !!(
        c.employee_is_web_master || c.employeeIsWebMaster
      ),
      isMerk2888: !!(c.is_merk2888 || c.isMerk2888)
    };
  }

  function clearSelectedClient(){
    selectedClientLabel = "";
    selectedEmployeeClient = {
      isEmployee: false,
      employeeName: "",
      documento: "",
      employeeHasUser: false,
      employeeIsWebMaster: false,
      isMerk2888: false,
    };
    $("#cliente_id").val("");
    $inpCliente.val("");
    $("#employee-password-input").val("");
    $hidEmpleadoPassword.val("");
    $("#merk2888-password-input").val("");
    $hidMerk2888Password.val("");
    refreshEmployeeDiscountUI();
  }

  function applySelectedClientItem(item){
    if (!item || !/^\d+$/.test(String(item.id || ""))) {
      clearSelectedClient();
      return false;
    }
    const label = item.label || item.value || item.name || "";
    selectedClientLabel = label;
    selectedEmployeeClient = {
      isEmployee: !!item.isEmployee,
      employeeName: item.employeeName || label,
      documento: item.documento || "",
      employeeHasUser: !!item.employeeHasUser,
      employeeIsWebMaster: !!item.employeeIsWebMaster,
      isMerk2888: !!item.isMerk2888,
    };
    $inpCliente.val(label);
    $("#cliente_id").val(item.id);
    $("#employee-password-input").val("");
    $hidEmpleadoPassword.val("");
    $("#merk2888-password-input").val("");
    $hidMerk2888Password.val("");
    refreshEmployeeDiscountUI();
    return true;
  }

  async function fetchSaleDraftClientById(clientId){
    const id = String(clientId || "").trim();
    if (!/^\d+$/.test(id)) return null;
    const response = await fetch(
      CLIENTE_URL + "?" + new URLSearchParams({ cliente_id: id, limit: 1 }),
      { credentials: "same-origin", cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const raw = (Array.isArray(data?.results) ? data.results : [])
      .find((row) => String(row?.id || "") === id);
    return raw ? mapClienteAc(raw) : null;
  }

  function setClienteCache(key, items) {
    if (!key) return;
    clienteAcCache.set(key, items);
    if (clienteAcCache.size > 80) {
      const firstKey = clienteAcCache.keys().next().value;
      if (firstKey) clienteAcCache.delete(firstKey);
    }
  }

  function cachedClienteMatches(term) {
    const key = clienteTermKey(term);
    if (!key) return [];
    if (clienteAcCache.has(key)) return clienteAcCache.get(key) || [];

    let bestKey = "";
    for (const cacheKey of clienteAcCache.keys()) {
      if (key.startsWith(cacheKey) && cacheKey.length > bestKey.length) {
        bestKey = cacheKey;
      }
    }

    const source = bestKey ? (clienteAcCache.get(bestKey) || []) : [];
    if (!source.length) return [];

    return source.filter((item) => {
      const haystack = `${item.label || ""} ${item.documento || ""}`.toLowerCase();
      return haystack.includes(key);
    });
  }

  function cancelClienteAcRequest() {
    if (!clienteAcActive) return;
    try { clienteAcActive.controller?.abort?.(); } catch (_) {}
    if (!clienteAcActive.responded && typeof clienteAcActive.resp === "function") {
      try { clienteAcActive.resp([]); } catch (_) {}
    }
    clienteAcActive = null;
  }

  createAC({
    $inp: $inpCliente,
    minChars: 1,
    openIfEmpty: false,
    sourceFn: function(req, resp){
      (async ()=>{
        const term = (req && typeof req.term === "string") ? req.term : "";
        const key = clienteTermKey(term);
        cancelClienteAcRequest();
        const seq = ++clienteAcSeq;

        const exactCached = clienteAcCache.get(key);
        if (exactCached) {
          resp(exactCached);
          return;
        }

        let responded = false;
        const cached = cachedClienteMatches(term);
        if (cached.length) {
          resp(cached);
          responded = true;
        }

        const controller = window.AbortController ? new AbortController() : null;
        clienteAcActive = { controller, resp, responded };

        const d = await fetch(CLIENTE_URL + "?" + new URLSearchParams({ term, limit: CLIENTE_AC_LIMIT }), {
          cache: "default",
          signal: controller ? controller.signal : undefined
        })
          .then(r=> r.ok ? r.json() : {results:[]})
          .catch((err)=>{
            if (err && err.name === "AbortError") return null;
            return {results:[]};
          });

        if (seq !== clienteAcSeq || !d) return;

        const items = (d.results || []).map(mapClienteAc);
        setClienteCache(key, items);

        if (responded) {
          clienteAcActive = null;
          if (clienteTermKey($inp.val()) === key) {
            requestAnimationFrame(() => {
              try { $inp.autocomplete("search", term); } catch (_) {}
            });
          }
          return;
        }

        if (clienteAcActive) clienteAcActive.responded = true;
        resp(items);
        clienteAcActive = null;
      })();
    },
    onSelect: (item) => {
      if (!guardSaleDraftEditing() || saleSubmitting) return;
      applySelectedClientItem(item);
      scheduleSaleDraftSave();
    }
  });

  $inpCliente.on("input.employeeDiscount", function(){
    if (saleSubmitting) {
      this.value = selectedClientLabel;
      return;
    }
    if (String(this.value || "") !== selectedClientLabel) {
      selectedClientLabel = "";
      selectedEmployeeClient = {
        isEmployee: false,
        employeeName: "",
        documento: "",
        employeeHasUser: false,
        employeeIsWebMaster: false,
        isMerk2888: false
      };
      $("#cliente_id").val("");
      $("#employee-password-input").val("");
      $hidEmpleadoPassword.val("");
      $("#merk2888-password-input").val("");
      $hidMerk2888Password.val("");
      refreshEmployeeDiscountUI();
      scheduleSaleDraftSave();
    }
  });

  /* ================== UX: inputs vinculados ================== */
  $inpNombre.on("input", function(){
    const nm=$.trim(this.value);
    if (nm) {
      const recPid = nameIndex.get(onlyName(nm).toLowerCase());
      if (recPid) {
        setProductFields({
          nombre:nm,
          pid:recPid,
          updateNameInput:false,
          focusQty:false
        });
      } else {
        $pid.val("");
        if ($inpId && $inpId.length) $inpId.val("");
        if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
        if ($agregar && $agregar.length)  $agregar.prop("disabled", true);
      }
    } else {
      $pid.val("");
      if ($inpId && $inpId.length) $inpId.val("");
      if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
      if ($agregar && $agregar.length)  $agregar.prop("disabled", true);
      try { $inpNombre.autocomplete("close"); } catch {}
    }
  });

  $inpCode.on("input", function(){
    const v=$.trim(this.value);
    if (v) {
      const digits = onlyDigits(v);
      if (/^\d{6,}$/.test(digits)) {
        const pid = barcodeIndex.get(digits);
        if (pid) setProductFields({ nombre:productCache.get(String(pid))?.nombre, pid, barcode:digits });
      } else {
        const rec = productCache.get(String(v));
        if (rec) setProductFields({ nombre:rec.nombre, pid:v, barcode:rec.barcode });
      }
    } else { try { $inpCode.autocomplete("close"); } catch {} }
  });

  /* ================== Cantidad principal (#cantidad): (si existe) ================== */
  function normalizeQtyOnCommit(el){
    const raw = String(el.value || "").trim();
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || n === 0) el.value = "1";
    else el.value = String(n);
    return el.value;
  }
  function clampQtyAnySign(x){
    const n = parseInt(String(x).trim(), 10);
    if (!Number.isFinite(n) || n === 0) return 1;
    return n;
  }

  if ($cantidad && $cantidad.length) {
    $cantidad
      .on("keydown", function(e){
        const ok = ["Backspace","Delete","ArrowLeft","ArrowRight","Tab","Home","End","-"].includes(e.key);
        if (ok) return;
        if (e.ctrlKey || e.metaKey) return;
        if (!/^\d$/.test(e.key)) e.preventDefault();
      })
      .on("blur", function(){ normalizeQtyOnCommit(this); });
  }

  /* ================== Qty instant update (carrito) ================== */
  function getBestLocalPrice(pid, $row){
    let price = Number($row.data("price")) || 0;
    if (price > 0) return price;

    const cached = productCache.get(String(pid)) || {};
    const cp = Number(cached.price) || 0;
    if (cp > 0) { setRowPriceUI($row, cp); return cp; }
    return 0;
  }

  function applyQtyInstant($row, newQty){
    if (!guardSaleDraftEditing() || saleSubmitting) return false;
    const pid = String($row.data("pid") || "");
    if (!pid) return;

    const oldQty = Number($row.attr("data-qty")) || Number($row.find(".qty-input").val()) || 0;
    const wasCounted = !!$row.data("counted");

    if (newQty === 0) {
      removeRowByPidWithAuditIfEmpty(pid);
      return;
    }

    $row.attr("data-qty", newQty);

    const i = productos.indexOf(pid);
    if (i > -1) cantidades[i] = newQty;

    const price = getBestLocalPrice(pid, $row);

    if (price > 0) {
      $row.find(".subtotal-cell").text(money(price * newQty));

      if (!wasCounted) { addToTotal(price * newQty); $row.data("counted", true); }
      else {
        const delta = price * (newQty - oldQty);
        if (delta) addToTotal(delta);
      }
      enforceTotalIntegritySoft();
    } else {
      $row.addClass("pending-price");
      $row.find(".subtotal-cell").text("…");
      scheduleVerifyRowPrice($row, 140);
    }
    rememberCartAuditRow($row);
    return true;
  }

  function sanitizeRowQtyInput(el){
    const raw = String(el.value || "");
    const cleaned = raw
      .replace(/[^\d-]/g, "")
      .replace(/(?!^)-/g, "");
    el.value = cleaned;
    return cleaned;
  }

  function commitRowQtyInput(el){
    const raw = String(el.value || "").trim();
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n)) { el.value = "1"; return 1; }
    el.value = String(n);
    return n;
  }

  $tbody.on("input change", ".qty-input", function () {
    const $row = $(this).closest("tr");
    const v = sanitizeRowQtyInput(this);
    if (v === "" || v === "-") return;
    const n = parseInt(v, 10);
    if (!Number.isFinite(n)) return;
    applyQtyInstant($row, n);
    scheduleVerifyRowPrice($row, 220);
  });

  $tbody.on("blur", ".qty-input", function () {
    const $row = $(this).closest("tr");
    const n = commitRowQtyInput(this);
    applyQtyInstant($row, n);
    scheduleVerifyRowPrice($row, 0);
  });

  $tbody.on("keydown", ".qty-input", function (e) {
    const ok = ["Backspace","Delete","ArrowLeft","ArrowRight","Tab","Home","End","Enter","-"].includes(e.key);
    if (!ok && !e.ctrlKey && !e.metaKey && !/^\d$/.test(e.key)) e.preventDefault();

    if (e.key === "Enter") {
      e.preventDefault();
      const $row = $(this).closest("tr");
      const n = commitRowQtyInput(this);
      applyQtyInstant($row, n);
      scheduleVerifyRowPrice($row, 0);

      this.blur();
      queueMicrotask(() => {
        if (isModalOpen()) return;
        if ($inpNombre.is(":visible")) { $inpNombre.focus(); $inpNombre[0]?.select?.(); }
        const v = $inpNombre.val() || "";
        if (v.length >= 1) { try { $inpNombre.autocomplete("search", v); } catch {} }
      });
    }
  });

  /* ================== Botones/agregado (si existen) ================== */
  if ($agregar && $agregar.length) {
    $agregar.off("click").on("click", () => {
      const pid = $pid.val();
      const qty = clampQtyAnySign($cantidad.val());
      $cantidad.val(String(qty));
      if (!pid || qty === 0) return;
      addToCartLastOnly(pid, qty);
    });
  }

  if ($cantidad && $cantidad.length) {
    $cantidad.off("keydown.confirm").on("keydown.confirm", function (e) {
      if (e.key === "Enter" && $agregar && $agregar.length && !$agregar.prop("disabled")) {
        e.preventDefault();
        const committed = normalizeQtyOnCommit(this);
        const qty = clampQtyAnySign(committed);
        const pid = $pid.val();
        if (!pid || qty === 0) return;

        addToCartLastOnly(pid, qty);

        this.blur();
        queueMicrotask(() => {
          if (isModalOpen()) return;
          if ($inpNombre.is(":visible")) { $inpNombre.focus(); $inpNombre[0]?.select?.(); }
          const v = $inpNombre.val() || "";
          if (v.length >= 1) { try { $inpNombre.autocomplete("search", v); } catch {} }
        });
      }
    });
  }

  $tbody.on("click", ".eliminar-producto", function () {
    const $row = $(this).closest("tr");
    const pid = String($row.data("pid") || "");
    removeRowByPidWithAuditIfEmpty(pid);
  });

  if ($btnAgregarBolsa.length) {
    $btnAgregarBolsa.off("click.bolsasPromo").on("click.bolsasPromo", async function () {
      const $btn = $(this);
      const pid = String($btn.data("bolsa-id") || "").trim();
      if (!pid) return;
      if (!hasSucursal()) {
        alert("No hay sucursal activa para esta venta.");
        return;
      }
      $btn.prop("disabled", true).attr("aria-disabled", "true");
      try {
        const prod = await ensureProductCachedById(pid);
        if (!prod) {
          alert(`No se pudo cargar el producto ID ${pid} para esta sucursal. Verifica que exista en inventario.`);
          return;
        }
        setProductFields({ nombre: prod.name, pid, barcode: prod.barcode || "" });
        addToCartLastOnly(pid, 1);
        queueMicrotask(() => enforceTotalIntegrity());
      } finally {
        $btn.prop("disabled", false).removeAttr("aria-disabled");
      }
    });
  }

  // ✅ Vaciar carrito (limpia pagos también)
  $btnVaciar.on("click", function(){
    if (!guardSaleDraftEditing() || saleSubmitting) return;
    if (!productos.length) return;
    if (!confirm("¿Vaciar todo el carrito?")) return;

    try {
      sendCartClearAudit(buildCartClearAuditPayload());
    } catch (_) {}

    productos.length = 0;
    cantidades.length = 0;
    $tbody.empty();
    setTotal(0);
    lastAddedPid = null;

    $hidMedioPago.val("");
    $hidPagos.val("");
    saleDraftPaymentState = null;
    clearSaleDraftForCurrentScope();
    resetCartAuditSession();
  });

  $buscarCart.on("keyup", function () {
    const t = ($(this).val() || "").toLowerCase();
    const rows = $tbody.find("tr");
    for (let i=0;i<rows.length;i++){
      const el = rows[i];
      const show = el.textContent.toLowerCase().includes(t);
      el.style.display = show ? "" : "none";
    }
  });

  /* ================== ✅ REPRICING OPTIMIZADO (POOL 8 + total 1x) ================== */
  async function repriceAllRowsAndRecalcTotalPooled(concurrency = 8) {
    const rows = $tbody.find("tr").toArray();
    if (!rows.length) { setTotal(0); return true; }

    for (const tr of rows) {
      const $row = $(tr);
      const $qin = $row.find(".qty-input");
      if ($qin.length) {
        const n = commitRowQtyInput($qin[0]);
        $row.attr("data-qty", n);
      }
      $row.data("counted", false);
      $row.addClass("pending-price");
    }

    let idx = 0;
    async function worker() {
      while (idx < rows.length) {
        const tr = rows[idx++];
        const $row = $(tr);
        await refreshRowPriceIfNeeded($row);
      }
    }

    await Promise.all(
      Array.from({ length: Math.min(concurrency, rows.length) }, worker)
    );

    let newTotal = 0;
    for (const tr of rows) {
      const $row = $(tr);
      const counted = !!$row.data("counted");
      if (!counted) continue;

      const price = Number($row.data("price")) || 0;
      const qty   = Number($row.attr("data-qty")) || 0;
      if (price > 0 && qty !== 0) newTotal += price * qty;
    }

    setTotal(newTotal);
    return true;
  }

  /* ================== ✅ Modal de pago (MIXTO / NO-MIXTO) ================== */
  const $efOptions  = $("#efectivo-options");
  const $amountIn   = $("#monto-recibido");
  const $changeOut  = $("#cambio");
  const $mixMode    = $("#mix-mode");
  const $pendingOut = $("#monto-pendiente");

  const REPRICE_ON_MODAL = false;
  const $nequiPanel = $("#nequi-payment-panel");
  const $nequiList = $("#nequi-payment-list");
  const $nequiStatus = $("#nequi-payment-status");
  const $nequiSelected = $("#nequi-selected-payment");
  const $nequiRefresh = $("#nequi-refresh-payments");
  const NEQUI_AUTO_REFRESH_MS = 650;
  let nequiPaymentsCache = [];
  let nequiPaymentsLoaded = false;
  let nequiPaymentsLoading = false;
  let nequiSilentLoading = false;
  let nequiAutoRefreshTimer = null;
  let nequiLastFetchController = null;
  let nequiKnownIds = new Set();
  let nequiNewIds = new Set();
  let nequiLastRefreshAt = 0;
  let selectedNequiPayment = null;

  function isMixtoUI(){
    return !!($mixMode && $mixMode.length && $mixMode.prop("checked"));
  }

  function showMixError(msg){
    const $e = $modal.find("#mix-error");
    $e.text(msg || "");
    $e.toggle(!!msg);
  }

  function ensureMixUIExists(){
    const hasChecks = $modal.find(".pm-check").length > 0;
    const hasAmts   = $modal.find(".pm-amt").length > 0;
    const hasMix    = $modal.find("#mix-mode").length > 0;
    const hasPend   = $modal.find("#monto-pendiente").length > 0;
    if (!hasChecks || !hasAmts || !hasMix || !hasPend) {
      console.warn("[PAGOS] Faltan elementos en el modal (pm-check/pm-amt/mix-mode/monto-pendiente). Revisa modal_venta.html.");
    }
  }

  function getCheckedMedios(){
    return $modal.find(".pm-check:checked").not("#mix-mode")
      .map(function(){ return this.value; }).get();
  }

  function rowForCheck($check){
    let $row = $check.closest(".mix-row");
    if ($row.length) return $row;
    $row = $check.closest(".pm-row, .payment-row, .form-check");
    if ($row.length) return $row;
    return $check.parent();
  }

  function amtInputFor(medio, $check){
    if ($check && $check.length){
      const $row = rowForCheck($check);
      let $amt = $row.find(`.pm-amt[data-medio='${medio}']`).first();
      if ($amt.length) return $amt;
      $amt = $row.find(".pm-amt").first();
      if ($amt.length) return $amt;
    }
    let $amt = $modal.find(`.pm-amt[data-medio='${medio}']`).first();
    if ($amt.length) return $amt;
    $amt = $modal.find(`input.pm-amt[name='monto_${medio}']`).first();
    return $amt;
  }

  function sumMixtoSelectedAmounts({excludeMedio=null} = {}){
    const medios = getCheckedMedios();
    let sum = 0;
    for (const m of medios) {
      if (excludeMedio && String(m) === String(excludeMedio)) continue;
      const $chk = $modal.find(`.pm-check[value='${m}']`).first();
      sum += parseAmt(amtInputFor(m, $chk).val());
    }
    return sum;
  }

  function computePaidSoFar(){
    const total = saleTotalForPayment();
    const medios = getCheckedMedios();
    if (!medios.length) return 0;

    if (!isMixtoUI()) {
      return (medios.length === 1) ? total : 0;
    }
    return sumMixtoSelectedAmounts();
  }

  function refreshPendingUI(){
    const total = saleTotalForPayment();
    const paid  = safeNumber(computePaidSoFar());
    const diff  = total - paid;

    if (!$pendingOut.length) return;

    if (isMixtoUI() && paid > total && Math.abs(diff) > 0.01) {
      $pendingOut.text(`Sobra por asignar: ${money(Math.abs(diff))}`);
      return;
    }
    $pendingOut.text(`Falta por pagar: ${money(Math.max(0, diff))}`);
  }

  function nequiPaymentAmountNeeded(){
    if (!getCheckedMedios().includes("nequi")) return 0;
    if (isMixtoUI()){
      const $chk = $modal.find(".pm-check[value='nequi']").first();
      return parseAmt(amtInputFor("nequi", $chk).val());
    }
    return saleTotalForPayment();
  }

  function nequiDisplayName(item){
    const shortName = [item?.nombre, item?.segundo_nombre].filter(Boolean).join(" ").trim();
    return shortName || item?.remitente || "Sin nombre";
  }

  function renderNequiSelected(){
    if (!$nequiSelected.length) return;
    $nequiSelected.empty();

    if (!selectedNequiPayment){
      $nequiSelected.prop("hidden", true);
      return;
    }

    const amount = Number(selectedNequiPayment.monto_num || 0);
    const needed = nequiPaymentAmountNeeded();
    const ok = needed <= 0 || amount + 0.01 >= needed;

    const $label = $("<span>").text(`Seleccionado: ${selectedNequiPayment.monto_label || money(amount)} de ${nequiDisplayName(selectedNequiPayment)}`);
    const $state = $("<strong>").text(ok ? "Cubre el pago" : `No cubre, faltan ${money(Math.max(0, needed - amount))}`);
    const $clear = $("<button>", {
      type: "button",
      id: "nequi-clear-payment",
      class: "nequi-mini-btn",
      text: "Quitar"
    });

    $nequiSelected
      .toggleClass("is-low", !ok)
      .append($label, $state, $clear)
      .prop("hidden", false);
  }

  function renderNequiPaymentList(){
    if (!$nequiList.length) return;

    $nequiList.empty();
    const needed = nequiPaymentAmountNeeded();

    if (nequiPaymentsLoading && !nequiSilentLoading){
      $nequiStatus.text("Cargando pagos recibidos por Nequi...");
      return;
    }

    if (!nequiPaymentsCache.length){
      $nequiStatus.text("No hay pagos recibidos disponibles por ahora. Puedes cerrar sin asociar uno.");
      return;
    }

    for (const item of nequiPaymentsCache){
      const amount = Number(item.monto_num || 0);
      const id = String(item.id || "");
      const selected = selectedNequiPayment && String(selectedNequiPayment.id) === String(item.id);
      const ok = needed <= 0 || amount + 0.01 >= needed;

      const $btn = $("<button>", {
        type: "button",
        class: `nequi-payment-item${selected ? " is-selected" : ""}${ok ? "" : " is-low"}${nequiNewIds.has(id) ? " is-new" : ""}`,
        "data-id": item.id
      });
      const $top = $("<div>", { class: "nequi-payment-item-top" });
      const $name = $("<strong>").text(`${item.monto_label || money(amount)} · ${nequiDisplayName(item)}`);
      const $badge = $("<span>", { class: "nequi-payment-badge" }).text(ok ? "Cubre" : "Menor al pago");
      const $meta = $("<span>", { class: "nequi-payment-meta" }).text([item.fecha, item.hora].filter(Boolean).join(" · "));
      const $text = $("<small>").text(item.texto || "");

      $top.append($name, $badge);
      $btn.append($top, $meta);
      if (item.texto) $btn.append($text);
      $nequiList.append($btn);
    }

    $nequiStatus.text(
      needed > 0
        ? `En vivo: si asocias un pago recibido, debe cubrir ${money(needed)}.`
        : "En vivo: seleccionar un pago recibido es opcional."
    );
  }

  function markNewNequiPayments(items){
    const nextIds = new Set((items || []).map((item) => String(item.id || "")).filter(Boolean));
    if (nequiPaymentsLoaded && nequiKnownIds.size){
      for (const id of nextIds){
        if (!nequiKnownIds.has(id)){
          nequiNewIds.add(id);
          setTimeout(() => {
            nequiNewIds.delete(id);
            renderNequiPaymentList();
          }, 4500);
        }
      }
    }
    nequiKnownIds = nextIds;
  }

  function resetNequiPaymentState({ clearCache = false } = {}){
    selectedNequiPayment = null;
    $hidNequiNotification.val("");
    if (clearCache){
      nequiPaymentsCache = [];
      nequiPaymentsLoaded = false;
      nequiKnownIds = new Set();
      nequiNewIds = new Set();
      nequiLastRefreshAt = 0;
    }
    renderNequiSelected();
    renderNequiPaymentList();
  }

  function disableNequiLinking(message = ""){
    nequiApiEnabled = false;
    stopNequiAutoRefresh();
    resetNequiPaymentState({ clearCache: true });
    $nequiPanel.prop("hidden", true);
    if (message) showMixError(`${message} La venta puede continuar como Nequi no vinculada.`);
  }

  function shouldAutoRefreshNequi(){
    return nequiApiEnabled
      && !!$nequiPanel.length
      && !!NEQUI_DISPONIBLES_URL
      && isModalOpen()
      && getCheckedMedios().includes("nequi");
  }

  function queueNequiAutoRefresh(){
    if (nequiAutoRefreshTimer || !shouldAutoRefreshNequi()) return;
    nequiAutoRefreshTimer = setTimeout(async () => {
      nequiAutoRefreshTimer = null;
      if (!shouldAutoRefreshNequi()) return;
      await loadNequiPayments(true, { silent: true });
      queueNequiAutoRefresh();
    }, NEQUI_AUTO_REFRESH_MS);
  }

  function startNequiAutoRefresh(){
    if (!nequiApiEnabled || !$nequiPanel.length || !NEQUI_DISPONIBLES_URL) return;
    if (nequiAutoRefreshTimer) clearTimeout(nequiAutoRefreshTimer);
    nequiAutoRefreshTimer = null;
    queueNequiAutoRefresh();
  }

  function stopNequiAutoRefresh(){
    if (nequiAutoRefreshTimer) clearTimeout(nequiAutoRefreshTimer);
    nequiAutoRefreshTimer = null;
    if (nequiLastFetchController) {
      try { nequiLastFetchController.abort(); } catch (_) {}
      nequiLastFetchController = null;
    }
  }

  async function loadNequiPayments(force = false, { silent = false } = {}){
    if (!nequiApiEnabled || !$nequiPanel.length || !NEQUI_DISPONIBLES_URL) return;
    if (nequiPaymentsLoading) return;
    if (nequiPaymentsLoaded && !force){
      renderNequiPaymentList();
      return;
    }

    nequiPaymentsLoading = true;
    nequiSilentLoading = !!silent;
    if (!silent) $nequiStatus.text("Cargando pagos recibidos por Nequi...");

    try {
      if (nequiLastFetchController) nequiLastFetchController.abort();
      nequiLastFetchController = new AbortController();
      const response = await fetch(NEQUI_DISPONIBLES_URL, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: nequiLastFetchController.signal
      });
      const data = await response.json();
      if (data?.feature_disabled === NEQUI_FEATURE_KEY){
        disableNequiLinking(data.error || "La vinculación con Nequi está desactivada.");
        return;
      }
      if (!response.ok || !data.success) throw new Error(data.error || "No se pudieron cargar los pagos recibidos.");

      const items = Array.isArray(data.items) ? data.items : [];
      markNewNequiPayments(items);
      nequiPaymentsCache = items;
      nequiPaymentsLoaded = true;
      nequiLastRefreshAt = Date.now();

      if (selectedNequiPayment && !nequiPaymentsCache.some((item) => String(item.id) === String(selectedNequiPayment.id))){
        selectedNequiPayment = null;
        $hidNequiNotification.val("");
        showMixError("El pago recibido por Nequi seleccionado ya no esta disponible.");
      }
    } catch (err) {
      if (err?.name === "AbortError") return;
      if (!silent) $nequiStatus.text("No se pudieron cargar los pagos recibidos por Nequi. Puedes cerrar sin asociar uno.");
    } finally {
      nequiLastFetchController = null;
      nequiPaymentsLoading = false;
      nequiSilentLoading = false;
      renderNequiSelected();
      renderNequiPaymentList();
    }
  }

  function refreshNequiPanel(){
    if (!$nequiPanel.length) return;
    const active = nequiApiEnabled && getCheckedMedios().includes("nequi");
    $nequiPanel.prop("hidden", !active);

    if (!active){
      stopNequiAutoRefresh();
      resetNequiPaymentState({ clearCache: false });
      return;
    }

    startNequiAutoRefresh();
    if (!nequiPaymentsLoading) {
      loadNequiPayments(true, { silent: nequiPaymentsLoaded });
    }
    renderNequiSelected();
    renderNequiPaymentList();
  }

  function selectNequiPayment(item){
    selectedNequiPayment = item || null;
    $hidNequiNotification.val(selectedNequiPayment ? selectedNequiPayment.id : "");
    showMixError("");
    renderNequiSelected();
    renderNequiPaymentList();

    const error = validateSelectedNequiPayment();
    if (error) showMixError(error);
  }

  function validateSelectedNequiPayment(){
    if (!getCheckedMedios().includes("nequi") || !selectedNequiPayment) return "";
    const needed = nequiPaymentAmountNeeded();
    const amount = Number(selectedNequiPayment.monto_num || 0);
    if (needed > 0 && amount + 0.01 < needed){
      return `El pago recibido por Nequi seleccionado (${money(amount)}) no cubre el pago Nequi (${money(needed)}).`;
    }
    return "";
  }

  function refreshEfectivoUI(){
    const hasEf = getCheckedMedios().includes("efectivo");

    if (isMixtoUI()){
      $modal.attr("data-mixto","1");
      $efOptions.hide();
      $amountIn.val("");
      $changeOut.text("");
      $amountIn.attr("placeholder", "");
      return;
    } else {
      $modal.attr("data-mixto","0");
    }

    $efOptions.toggle(hasEf);

    if (!hasEf){
      $amountIn.val("");
      $changeOut.text("");
      $amountIn.attr("placeholder", "");
      return;
    }

    const efMonto = saleTotalForPayment();
    $amountIn.attr("placeholder", money(efMonto || 0));

    const raw = ($amountIn.val() || "").trim();
    const recibido = raw === "" ? efMonto : parseAmt(raw);
    const cambio = recibido - efMonto;

    $changeOut.text(cambio >= 0 ? `Cambio: ${money(cambio)}` : "");
  }

  function setAmtVisibility($amt, show){
    if (!$amt || !$amt.length) return;
    $amt.toggle(!!show);
    if (show) $amt.css("display", "");
  }

  function updateRowUI($check){
    const medio = String($check.val() || "");
    const mixto = isMixtoUI();
    const on    = $check.prop("checked");

    const $row = rowForCheck($check);
    const $amt = amtInputFor(medio, $check);

    if (mixto){
      if ($amt && $amt.length){
        $row.toggleClass("show-amt", on);
        $amt.prop("disabled", !on);
        setAmtVisibility($amt, on);

        if (!on) {
          $amt.val("");
        } else {
          const total = saleTotalForPayment();
          const already = sumMixtoSelectedAmounts({ excludeMedio: medio });
          const faltante = Math.max(0, total - already);
          if (parseAmt($amt.val()) <= 0) $amt.val(to2(faltante || 0));
          queueMicrotask(()=>{ try{ $amt[0]?.setSelectionRange?.(0, String($amt.val()||"").length); } catch{} });
        }
      } else {
        $row.toggleClass("show-amt", false);
      }
      return;
    }

    $row.toggleClass("show-amt", false);
    if ($amt && $amt.length){
      $amt.prop("disabled", true);
      $amt.val("");
      setAmtVisibility($amt, false);
    }
  }

  function applyModeRules(){
    const mixto = isMixtoUI();
    $modal.attr("data-mixto", mixto ? "1" : "0");

    if (!mixto){
      const checked = getCheckedMedios();
      if (checked.length > 1){
        const keep = checked.includes("efectivo") ? "efectivo" : checked[0];
        $modal.find(".pm-check").not("#mix-mode").prop("checked", false);
        $modal.find(`.pm-check[value='${keep}']`).prop("checked", true);
      }
    }

    $modal.find(".pm-check").not("#mix-mode").each(function(){
      updateRowUI($(this));
    });

    refreshEfectivoUI();
    refreshPendingUI();
    refreshNequiPanel();
  }

  $modal.on("change", "#mix-mode", function(){
    showMixError("");

    if (isMixtoUI()){
      const medios = getCheckedMedios();
      if (medios.length === 1){
        const m = medios[0];
        const $chk = $modal.find(`.pm-check[value='${m}']`).first();
        const $amt = amtInputFor(m, $chk);
        if ($amt.length){
          $amt.prop("disabled", false);
          setAmtVisibility($amt, true);
          rowForCheck($chk).addClass("show-amt");
          if (parseAmt($amt.val()) <= 0) $amt.val(to2(saleTotalForPayment() || 0));
        }
      }
    } else {
      $modal.find(".pm-amt").val("").prop("disabled", true).each(function(){ setAmtVisibility($(this), false); });
      $modal.find(".mix-row, .pm-row, .payment-row").removeClass("show-amt");
    }

    applyModeRules();
    rememberSaleDraftPaymentUi();
  });

  function buildPagosJSONOrError(){
    const total = saleTotalForPayment();

    if (total <= 0) return { pagos: [] };

    const medios = getCheckedMedios();
    if (!medios.length) return { error: "Seleccione al menos un medio de pago." };

    const mixto = isMixtoUI();

    if (!mixto){
      if (medios.length !== 1) return { error: "Seleccione solo un medio de pago (o active Pago mixto)." };
      const m = medios[0];
      const pagos = [{ medio_pago: m, monto: to2(total) }];

      if (m === "efectivo"){
        const raw = ($amountIn.val() || "").trim();
        const recibido = raw === "" ? total : parseAmt(raw);
        if (recibido < total) return { error: "Monto recibido en efectivo insuficiente." };
        if (raw === "") $amountIn.val(to2(total));
      }
      const nequiError = validateSelectedNequiPayment();
      if (nequiError) return { error: nequiError };
      return { pagos };
    }

    let pagos = [];
    let suma = 0;

    for (const m of medios){
      const $chk = $modal.find(`.pm-check[value='${m}']`).first();
      const $amt = amtInputFor(m, $chk);
      const amt = parseAmt($amt.val());
      if (amt <= 0) return { error: `Monto inválido para ${String(m).replaceAll("_"," ")}.` };
      suma += amt;
      pagos.push({ medio_pago: m, monto: to2(amt) });
    }

    const diff = total - suma;
    if (Math.abs(diff) > 0.01) {
      return { error: `La suma de pagos (${money(suma)}) debe ser igual al total (${money(total)}).` };
    }
    if (Math.abs(diff) > 0 && pagos.length) {
      const last = pagos[pagos.length - 1];
      last.monto = to2(parseAmt(last.monto) + diff);
    }
    const nequiError = validateSelectedNequiPayment();
    if (nequiError) return { error: nequiError };
    return { pagos };
  }

  $nequiRefresh.on("click", function(){
    loadNequiPayments(true);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) persistSaleDraftNow();
    if (!document.hidden && shouldAutoRefreshNequi()){
      loadNequiPayments(true, { silent: true });
      startNequiAutoRefresh();
    }
  });

  $modal.on("click", ".nequi-payment-item", function(e){
    e.preventDefault();
    e.stopPropagation();
    const id = String($(this).data("id") || "");
    const item = nequiPaymentsCache.find((row) => String(row.id) === id);
    if (item) selectNequiPayment(item);
  });

  $modal.on("click", "#nequi-clear-payment", function(e){
    e.preventDefault();
    e.stopPropagation();
    resetNequiPaymentState({ clearCache: false });
  });

  const confirmPagoGuard = { ts: 0 };
  function triggerConfirmPago(){
    // ✅ si un escáner acaba de meter Enter, NO confirmar
    if (isModalConfirmBlocked()) return;

    const t = Date.now();
    if (t - confirmPagoGuard.ts < 250) return;
    confirmPagoGuard.ts = t;
    $("#confirmar-pago").trigger("click");
  }

  function getDigitFromAltEvent(e){
    const oe = e.originalEvent || e;
    const code = String(oe.code || "");
    if (/^Digit[0-9]$/.test(code))  return Number(code.replace("Digit",""));
    if (/^Numpad[0-9]$/.test(code)) return Number(code.replace("Numpad",""));

    const k = String(oe.key || "");
    if (/^[0-9]$/.test(k)) return Number(k);
    return null;
  }

  /* ================== ✅ MODAL INSTANT / o BYPASS si total <= 0 ================== */
  let confirmSubmitting = false;
  $("#generar-venta").off("click").on("click", () => {
    if (!saleDraftReadyToCharge()) return;
    if (saleSubmitting) return;
    if (!productos.length) { alert("Agregue productos."); return; }
    if (!hasSucursal() || !$("#puntopago_id").val()) { alert("Seleccione sucursal y punto de pago."); return; }

    enforceTotalIntegrity();

    if (
      safeNumber(runningTotal) <= 0
      && !isEmployeeClientSelected()
      && !isMerk2888ClientSelected()
    ) {
      $hidPagos.val("[]");
      $hidMedioPago.val("");
      $hidEmpleadoPassword.val("");
      $hidMerk2888Password.val("");
      $("#venta-form").trigger("submit");
      return;
    }

    ensureMixUIExists();
    showMixError("");
    refreshEmployeeDiscountUI();

    $("#modal-total").text(money(saleTotalForPayment()));

    const rememberedPayment = sanitizeSaleDraftPayment(saleDraftPaymentState);

    $hidPagos.val("");
    $hidMedioPago.val("");
    resetNequiPaymentState({ clearCache: true });

    $modal.find(".pm-check").not("#mix-mode").prop("checked", false);
    $modal.find(".pm-amt").val("").prop("disabled", true).each(function(){ setAmtVisibility($(this), false); });
    $modal.find(".mix-row, .pm-row, .payment-row").removeClass("show-amt");

    $amountIn.val("");
    $changeOut.text("");

    if ($mixMode.length) $mixMode.prop("checked", false);
    $modal.attr("data-mixto","0");

    const specialMerk2888Mode = isMerk2888ClientSelected();
    $modal.toggleClass("is-merk2888-sale", specialMerk2888Mode);
    $modal.find(".pending-banner, .payment-help, .mix-grid")
      .toggle(!specialMerk2888Mode);
    $modal.find(".pm-check").prop("disabled", specialMerk2888Mode);
    $mixMode.prop("disabled", specialMerk2888Mode);
    $amountIn.prop("disabled", specialMerk2888Mode);
    $("#mpago-title").text(
      specialMerk2888Mode ? "Autorizar beneficio" : "Pagos"
    );

    const paymentWasRestored = !specialMerk2888Mode
      && applySaleDraftPaymentState(rememberedPayment);
    if (!specialMerk2888Mode && !paymentWasRestored) {
      $modal.find(".pm-check[value='efectivo']").prop("checked", true);
    }

    confirmSubmitting = false;
    const $btnConfirm = $("#confirmar-pago");
    $btnConfirm.text(
      specialMerk2888Mode
        ? "APLICAR BENEFICIO Y REGISTRAR"
        : "CONFIRMAR PAGO"
    );
    $modal.attr("data-loading-prices","0");
    $btnConfirm.prop("disabled", false);

    // ✅ quitar bloqueo viejo
    modalConfirmBlockUntil = 0;

    applyModeRules();
    openModal();

    // ✅ Calienta el POS Agent mientras el cajero digita/valida el efectivo.
    // Así, al confirmar pago, /print sale con menos latencia.
    agentPingFast();

    queueMicrotask(() => {
      if (specialMerk2888Mode) {
        $("#merk2888-password-input").focus();
      } else if (!isMixtoUI() && $amountIn.is(":visible")) {
        $amountIn.focus();
        $amountIn[0]?.select?.();
      }
    });

    if (REPRICE_ON_MODAL) {
      $btnConfirm.prop("disabled", true);
      $modal.attr("data-loading-prices","1");
      requestAnimationFrame(() => {
        repricingMode = true;
        repriceAllRowsAndRecalcTotalPooled(8)
          .then(() => {
            $("#modal-total").text(money(saleTotalForPayment()));
            applyModeRules();
          })
          .catch(() => {})
          .finally(() => {
            repricingMode = false;
            $modal.attr("data-loading-prices","0");
            $btnConfirm.prop("disabled", false);
            queueMicrotask(() => {
              if (specialMerk2888Mode) {
                $("#merk2888-password-input").focus();
              } else if (!isMixtoUI() && $amountIn.is(":visible")) {
                $amountIn.focus();
                $amountIn[0]?.select?.();
              }
            });
          });
      });
    }
  });

  $(".close").on("click", closeModal);
  $(window).on("click", (e) => { if ($modal.length && e.target === $modal[0]) closeModal(); });

  /* =======================================================================================
     ✅ Scanner guard dentro del MODAL
     - Detecta burst tipo escáner, consume teclas y BLOQUEA confirmación.
     - ✅ IMPORTANTE: NO agrega al carrito ni toca inputs de la venta mientras el modal esté abierto.
     ======================================================================================= */
  (function scannerGuardInsideModal() {
    const MIN_CHARS = 8;
    const GAP_MS = 80;
    const SCAN_AVG_MS = 45;

    let buf = "";
    let first = 0;
    let last = 0;
    let scanning = false;
    let originEl = null;
    let originStartValue = "";

    let idleTimer = null;
    let finalizeTimer = null;

    function reset() {
      buf = "";
      first = 0;
      last = 0;
      scanning = false;
      originEl = null;
      originStartValue = "";
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (finalizeTimer) { clearTimeout(finalizeTimer); finalizeTimer = null; }
    }

    function looksLikeScanner(t) {
      return !!buf
        && buf.length >= MIN_CHARS
        && (t - first) <= Math.max(130, buf.length * SCAN_AVG_MS)
        && (t - last) <= GAP_MS * 2;
    }

    function rememberOrigin() {
      originEl = document.activeElement;
      originStartValue = (originEl && typeof originEl.value === "string") ? originEl.value : "";
    }

    function restoreOriginIfNeeded() {
      if (!originEl || typeof originEl.value !== "string") return;
      if (originEl.value === originStartValue) return;
      try {
        originEl.value = originStartValue;
        $(originEl).trigger("input");
      } catch (_) {}
    }

    function markActivity() {
      // más largo para cubrir Enter/Tab + un frame extra
      blockModalConfirmFor(900);
    }

    function finalize(_code) {
      // ✅ Modal = bloqueo total: no agregues productos
      restoreOriginIfNeeded();
      markActivity();
      reset();
    }

    document.addEventListener("keydown", function (e) {
      if (!isModalOpen()) { reset(); return; }

      // ✅ Algunos lectores emiten Alt/NumLock/CapsLock y otros artefactos como
      // parte del protocolo de transmisión (ALT+NumPad toggle). Ignorar sin resetear.
      if (SCANNER_ARTIFACT_KEYS.has(e.key)) return;

      // Si el usuario usa atajos reales (Ctrl+algo / Meta+algo), asumimos NO escáner
      if (e.ctrlKey || e.metaKey) { reset(); return; }

      const t = Date.now();

      if (e.key === "Enter" || e.key === "Tab") {
        if (looksLikeScanner(t)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          e.stopPropagation();

          finalize(buf);
          return;
        }
        reset();
        return;
      }

      if (e.key && e.key.length === 1) {
        if (!/^\d$/.test(e.key)) { reset(); return; }

        // Construcción de buffer con timing
        if (!buf) {
          rememberOrigin();
          buf = e.key;
          first = t;
          last = t;
          scanning = false;
        } else {
          if ((t - last) > GAP_MS) {
            // corte: no era escáner continuo
            rememberOrigin();
            buf = e.key;
            first = t;
            last = t;
            scanning = false;
          } else {
            buf += e.key;
            last = t;
          }
        }

        // heurística: si va muy rápido, es escáner
        if (!scanning && looksLikeScanner(t)) {
          scanning = true;
          restoreOriginIfNeeded();
        }

        if (scanning) {
          // NO dejes que el escáner escriba dentro de inputs del modal
          e.preventDefault();
          e.stopImmediatePropagation();
          e.stopPropagation();
          markActivity();
        }

        // timers
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => reset(), GAP_MS * 7);

        if (finalizeTimer) clearTimeout(finalizeTimer);
        if (scanning && buf.length >= MIN_CHARS) {
          // por si el escáner NO manda Enter: finaliza al quedar inactivo un instante
          finalizeTimer = setTimeout(() => finalize(buf), GAP_MS * 6);
        }

        return;
      }

      if (e.key !== "Shift") reset();
    }, true);
  })();

  $(document).on("keydown", function (e) {
    if (!$modal.is(":visible")) return;

    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      closeModal();
      return;
    }

    if (e.key === "Enter" && !e.altKey && !e.ctrlKey && !e.metaKey) {
      // ✅ Si un escáner acaba de mandar Enter, NO confirmar
      if (isModalConfirmBlocked()) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        return;
      }
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      triggerConfirmPago();
      return;
    }
  });

  $modal.on("change", ".pm-check", function(){
    if (this.id === "mix-mode") return;

    const mixto = isMixtoUI();
    if (!mixto && this.checked){
      $modal.find(".pm-check").not(this).not("#mix-mode").prop("checked", false);
    }

    showMixError("");
    applyModeRules();

    const medio = this.value;
    if (mixto && this.checked){
      const $amt = amtInputFor(medio, $(this));
      queueMicrotask(()=>{ if ($amt.length) { $amt.focus(); $amt[0]?.select?.(); } });
    } else if (!mixto && this.checked && medio === "efectivo") {
      queueMicrotask(()=>{ if ($amountIn.is(":visible")) { $amountIn.focus(); $amountIn[0]?.select?.(); } });
    }
    rememberSaleDraftPaymentUi();
  });

  $modal.on("input", ".pm-amt", function(){
    showMixError("");
    refreshEfectivoUI();
    refreshPendingUI();
    renderNequiSelected();
    renderNequiPaymentList();
    rememberSaleDraftPaymentUi();
  });

  $(document).on("click", ".mix-row", function (e) {
    if (!$modal.is(":visible")) return;
    if ($(e.target).is("input")) return;

    const $chk = $(this).find(".pm-check").not("#mix-mode").first();
    if (!$chk.length) return;

    const mixto = isMixtoUI();
    const next = !$chk.prop("checked");

    if (!mixto && next) {
      $modal.find(".pm-check").not("#mix-mode").prop("checked", false);
      $chk.prop("checked", true).trigger("change");
    } else {
      $chk.prop("checked", next).trigger("change");
    }
  });

  $amountIn.on("input", function () {
    refreshEfectivoUI();
  });

  $amountIn.on("keydown", function (e) {
    if (e.key === "Enter") {
      // ✅ Si un escáner acaba de mandar Enter, NO confirmar
      if (isModalConfirmBlocked()) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        return;
      }
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      triggerConfirmPago();
    }
  });

  $(document).on("keydown", function (e) {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;

    if ($("#myModal").is(":visible")) {
      // ✅ bloqueo por escáner
      if (isModalConfirmBlocked() && e.key === "Enter") {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        return;
      }

      const d = getDigitFromAltEvent(e);
      if (d !== null) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();

        if (d === 6) {
          if ($mixMode && $mixMode.length) {
            $mixMode.prop("checked", !$mixMode.prop("checked")).trigger("change");
          }
          return;
        }

        const $checks = $modal.find(".pm-check").filter(":enabled").not("#mix-mode");
        const idx0 = (d === 0) ? 9 : (d - 1);
        const $target = $checks.eq(idx0);
        if ($target.length) $target.prop("checked", !$target.prop("checked")).trigger("change");
        return;
      }

      if ((e.originalEvent?.key === "Enter") || e.key === "Enter") {
        // ✅ bloqueo por escáner
        if (isModalConfirmBlocked()) {
          e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
          return;
        }
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        triggerConfirmPago();
        return;
      }
    }

    const isAltSpace = e.altKey && !e.ctrlKey && !e.metaKey && (e.code === "Space" || e.key === " ");
    if (isAltSpace) {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      if ($("#myModal").is(":visible")) triggerConfirmPago();
      else $("#generar-venta").trigger("click");
      return;
    }

    const isAltEnter = e.key === "Enter" && e.altKey && !e.ctrlKey && !e.metaKey;
    if (isAltEnter) {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      if ($("#myModal").is(":visible")) triggerConfirmPago();
      else $("#generar-venta").trigger("click");
    }
  });

  /* ================== CLICK CONFIRM (MIXTO / NO MIXTO) ================== */
  $(document).off("click.confirmPago").on("click.confirmPago", "#confirmar-pago", function (e) {
    e.preventDefault();
    if (confirmSubmitting) return;

    showMixError("");
    if (REPRICE_ON_MODAL && $modal.attr("data-loading-prices") === "1") return;

    if (isMerk2888ClientSelected()) {
      const code = String($("#merk2888-password-input").val() || "").trim();
      if (!/^\d{8}$/.test(code)) {
        showMixError("Escribe la clave vigente de 8 dígitos para aplicar el beneficio merk2888.");
        $("#merk2888-password-input").focus();
        return;
      }
      $hidMerk2888Password.val(code);
      $hidEmpleadoPassword.val("");
    } else if (isEmployeeClientSelected()) {
      const pass = String($("#employee-password-input").val() || "").trim();
      if (!pass) {
        const benefitName = isWebMasterEmployeeClientSelected()
          ? "el beneficio Web Master"
          : "el descuento";
        showMixError(`El empleado comprador debe escribir su contrasena para autorizar ${benefitName}.`);
        $("#employee-password-input").focus();
        return;
      }
      $hidEmpleadoPassword.val(pass);
      $hidMerk2888Password.val("");
    } else {
      $hidEmpleadoPassword.val("");
      $hidMerk2888Password.val("");
    }

    const built = buildPagosJSONOrError();
    if (built.error) { showMixError(built.error); return; }

    confirmSubmitting = true;
    const $btn = $("#confirmar-pago");
    $btn.prop("disabled", true);

    const pagos = built.pagos || [];
    $hidPagos.val(JSON.stringify(pagos));

    const medioCompat = (pagos.length >= 2) ? "mixto" : (pagos[0]?.medio_pago || "");
    $hidMedioPago.val(medioCompat);

    // ✅ Primero dispara el submit para que el POST de venta arranque antes de cualquier trabajo visual.
    $("#venta-form").trigger("submit");
    queueMicrotask(closeModal);
  });

  /* ================== POS Agent helpers ================== */
  const POS_AGENT_HEADERS_JSON = POS_AGENT_TOKEN
    ? { "Content-Type": "application/json", "X-Pos-Agent-Token": POS_AGENT_TOKEN }
    : null;
  const POS_AGENT_HEADERS_TOKEN = POS_AGENT_TOKEN
    ? { "X-Pos-Agent-Token": POS_AGENT_TOKEN }
    : null;

  function fireAndForgetFetch(url, options) {
    try {
      const p = fetch(url, options);
      if (p && typeof p.catch === "function") p.catch(() => {});
      return p;
    } catch (_) {
      return Promise.resolve();
    }
  }

  function normalizePrintOperatingSystem(value) {
    return String(value || "").trim().toLowerCase() === "linux"
      ? "linux"
      : "windows";
  }

  function normalizePrintPaperSize(value) {
    return String(value || "").trim().toLowerCase() === "pequena"
      ? "pequena"
      : "grande";
  }

  const ESCPOS_FULL_CUT_COMMAND = "\x1d\x56\x41\x00";

  function normalizePrintAutoCut(value) {
    if (value === undefined || value === null || value === "") return true;
    if (typeof value === "boolean") return value;
    return ["1", "true", "yes", "on"].includes(
      String(value).trim().toLowerCase(),
    );
  }

  function buildPosAgentPrintPayload(text, autoCut = true) {
    const shouldCut = normalizePrintAutoCut(autoCut);
    let printableText = String(text || "");

    // El comando se incluye en el mismo trabajo RAW para que los agentes
    // existentes puedan cortar aunque todavía no interpreten el campo `cut`.
    if (shouldCut && !printableText.endsWith(ESCPOS_FULL_CUT_COMMAND)) {
      printableText += ESCPOS_FULL_CUT_COMMAND;
    }

    return {
      text: printableText,
      cut: shouldCut,
      cut_command_embedded: shouldCut,
    };
  }

  async function printViaLinuxServer(ventaId, paperSize, { openDrawer = false, printToken = "" } = {}) {
    if (!IMPRIMIR_FACTURA_URL) {
      throw new Error("La ruta de impresion Linux no esta configurada.");
    }
    if (!ventaId) {
      throw new Error("No se recibio el ID de la venta para imprimir.");
    }

    const csrf = getCSRF()
      || document.querySelector("input[name='csrfmiddlewaretoken']")?.value
      || "";
    const body = new URLSearchParams({
      csrfmiddlewaretoken: csrf,
      venta_id: String(ventaId),
      paper_size: normalizePrintPaperSize(paperSize),
      open_drawer: openDrawer ? "1" : "0"
    });
    if (printToken) body.set("print_token", String(printToken));

    const response = await fetch(IMPRIMIR_FACTURA_URL, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-CSRFToken": csrf,
        "X-Requested-With": "XMLHttpRequest"
      },
      body
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) {
      throw new Error(data.error || "No se pudo imprimir la factura en Linux.");
    }
    return data;
  }

  let posAgentUltraReady = !!FAST_POS_ULTRA_FORCE;
  let posAgentUltraDetecting = false;

  function buildUltraPayload(payloadObj = {}) {
    return JSON.stringify({
      token: POS_AGENT_TOKEN,
      ...payloadObj
    });
  }

  // ✅ Envío más liviano para POS Agent compatible con /print-fast y /kick-fast.
  // Usa text/plain y token en body para evitar el header X-Pos-Agent-Token, que puede causar preflight OPTIONS.
  function agentPostUltra(path, payloadObj = {}) {
    if (!POS_AGENT_TOKEN || !FAST_POS_ULTRA_ENABLED) return false;

    const url = POS_AGENT_URL + path;
    const payload = buildUltraPayload(payloadObj);

    try {
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "text/plain;charset=UTF-8" });
        if (navigator.sendBeacon(url, blob)) return true;
      }
    } catch (_) {}

    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("Content-Type", "text/plain;charset=UTF-8");
      xhr.send(payload);
      return true;
    } catch (_) {
      return false;
    }
  }

  function agentPrintUltra(text, { autoCut = true } = {}) {
    return agentPostUltra(
      FAST_POS_PRINT_ENDPOINT,
      buildPosAgentPrintPayload(text, autoCut),
    );
  }

  function agentKickUltra() {
    return agentPostUltra(FAST_POS_KICK_ENDPOINT, {});
  }

  function agentDetectUltraFast() {
    if (!POS_AGENT_TOKEN || !FAST_POS_ULTRA_ENABLED || FAST_POS_ULTRA_FORCE || posAgentUltraDetecting || posAgentUltraReady) return;
    posAgentUltraDetecting = true;

    try {
      fetch(POS_AGENT_URL + FAST_POS_PING_ENDPOINT, {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: buildUltraPayload({ ping: true })
      })
        .then(r => {
          posAgentUltraReady = !!(r && (r.ok || r.status === 204));
        })
        .catch(() => {
          posAgentUltraReady = false;
        })
        .finally(() => {
          posAgentUltraDetecting = false;
        });
    } catch (_) {
      posAgentUltraDetecting = false;
      posAgentUltraReady = false;
    }
  }

  // ✅ Versión más rápida: inicia el POST /print y devuelve inmediatamente.
  // No usa await ni AbortController, para no cancelar impresiones lentas ni meter esperas.
  function agentPrintFast(text, { autoCut = true } = {}) {
    if (!POS_AGENT_TOKEN) return Promise.resolve();
    return fireAndForgetFetch(POS_AGENT_URL + "/print", {
      method: "POST",
      keepalive: true,
      headers: POS_AGENT_HEADERS_JSON,
      body: JSON.stringify(buildPosAgentPrintPayload(text, autoCut))
    });
  }

  function agentKickFast() {
    if (!POS_AGENT_TOKEN) return Promise.resolve();
    return fireAndForgetFetch(POS_AGENT_URL + "/kick", {
      method: "POST",
      keepalive: true,
      headers: POS_AGENT_HEADERS_TOKEN
    });
  }

  function agentPingFast() {
    if (!POS_AGENT_TOKEN) return Promise.resolve();

    // ✅ Detecta en segundo plano si el POS Agent soporta modo ultra sin headers personalizados.
    agentDetectUltraFast();

    return fireAndForgetFetch(POS_AGENT_URL + "/ping", {
      method: "GET",
      keepalive: true,
      headers: POS_AGENT_HEADERS_TOKEN
    });
  }

  async function agentPrintSafe(text, { timeout = 700, autoCut = true } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(POS_AGENT_URL + "/print", {
        method: "POST",
        keepalive: true,
        headers: POS_AGENT_HEADERS_JSON,
        body: JSON.stringify(buildPosAgentPrintPayload(text, autoCut)),
        signal: ctrl.signal
      });
    } catch (_) {}
    finally { clearTimeout(t); }
  }

  async function agentKickSafe({ timeout = 450 } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(POS_AGENT_URL + "/kick", {
        method: "POST",
        keepalive: true,
        headers: POS_AGENT_HEADERS_TOKEN,
        signal: ctrl.signal
      });
    } catch (_) {}
    finally { clearTimeout(t); }
  }

  (function agentWarmup(){
    agentPingFast();
  })();

  function settleWithDeadline(proms, maxWaitMs=250){
    return Promise.race([
      Promise.allSettled(proms),
      new Promise(res => setTimeout(res, maxWaitMs))
    ]);
  }

  /* ================== Submit ================== */
  let saleSubmitting = false;
  const formEl = document.getElementById("venta-form");
  const formAction = formEl ? String($(formEl).attr("action") || "") : "";

  function readPagosFromHidden(){
    try { return JSON.parse($hidPagos.val() || "[]"); } catch { return []; }
  }
  function isMixtoFromPagos(pagos){
    if (Array.isArray(pagos) && pagos.length >= 2) return true;
    const m = String($hidMedioPago.val() || "").toLowerCase().trim();
    return m === "mixto";
  }

  function buildSaleSuccessMessage(response, total, changeMessage = ""){
    const nequiMessage = response && response.nequi_payment
      ? `\nPago Nequi: ${response.nequi_linked ? "✅ VINCULADO" : "⚠️ NO VINCULADO"}`
      : "";
    const specialMessage = response && response.merk2888_free_sale
      ? "\nBeneficio merk2888: 100% aplicado. La clave quedó consumida."
      : "";

    return `✅ Venta registrada
Total: ${money(total)}${changeMessage}${specialMessage}${nequiMessage}`;
  }

  $("#venta-form").off("submit").on("submit", function (e) {
    e.preventDefault();
    if (!saleDraftReadyToCharge()) return;
    if (saleSubmitting) return;
    saleSubmitting = true;
    saleDraftSubmittedKey = saleDraftStorageKey();
    saleDraftSubmissionPending = true;
    persistSaleDraftNow();

    if (FAST_SUBMIT_VERIFY_PENDING_PRICES) {
      const $bad = $tbody.find("tr").filter((_, tr) => {
        const p = Number($(tr).data("price"));
        const counted = $(tr).data("counted");
        return !counted || !Number.isFinite(p) || p <= 0;
      });
      if ($bad.length) for (const tr of $bad.toArray()) scheduleVerifyRowPrice($(tr), 0);
    }

    // ✅ Asegura productos/cantidades actualizados sin esperar requestIdleCallback.
    syncHiddenFieldsNow();
    if (!isEmployeeClientSelected()) {
      $hidEmpleadoPassword.val("");
    }
    if (!isMerk2888ClientSelected()) {
      $hidMerk2888Password.val("");
    }

    const form = this;
    const fd = new FormData(form);
    const body = new URLSearchParams(fd);

    const $submitBtn = $(form).find("button[type='submit'], input[type='submit']").first();
    if ($submitBtn.length) $submitBtn.prop("disabled", true);

    fetch(formAction || $(form).attr("action"), {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-CSRFToken": getCSRF(),
        "X-Requested-With": "XMLHttpRequest"
      },
      body
    })
    .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP "+r.status)))
    .then(async (r) => {
      if (!r || !r.success) {
        saleSubmitting = false;
        confirmSubmitting = false;
        saleDraftSubmissionPending = false;
        persistSaleDraftNow();
        $hidMerk2888Password.val("");
        $("#merk2888-password-input").val("");
        if ($submitBtn.length) $submitBtn.prop("disabled", false);
        alert((r && r.error) || "Error");
        if (r && r.configuration_changed && r.redirect_url) {
          window.location.assign(String(r.redirect_url));
        }
        return;
      }

      // La confirmación del servidor es la única señal que permite borrar el
      // borrador. Se hace antes de imprimir/mostrar alertas para que cerrar la
      // pestaña en ese intervalo nunca ofrezca repetir una venta ya registrada.
      saleDraftSaleConfirmed = true;
      saleDraftSubmissionPending = false;
      clearSaleDraftForCurrentScope(saleDraftSubmittedKey);
      saleDraftSubmittedKey = "";

      const pagos = readPagosFromHidden();
      const hasServerTotal = r.sale_total !== undefined
        && r.sale_total !== null
        && String(r.sale_total).trim() !== "";
      const totalNum = hasServerTotal
        ? safeNumber(r.sale_total)
        : ((r.web_master_free_sale || r.merk2888_free_sale) ? 0 : saleTotalForPayment());

      const esMixto = isMixtoFromPagos(pagos);
      const ef = (pagos || []).find(p => String(p.medio_pago || "").toLowerCase() === "efectivo");
      let recibidoEfectivo = totalNum;
      if (totalNum > 0 && ef && !esMixto) {
        const raw = ($("#monto-recibido").val() || "").trim();
        recibidoEfectivo = raw === "" ? totalNum : parseAmt(raw);
      }
      const cambio = (totalNum > 0 && ef && !esMixto)
        ? Math.max(0, recibidoEfectivo - totalNum)
        : 0;

      // ✅ Mandar factura y, solo si hay valor por cobrar, abrir el cajón.
      // En modo ultra usa /print-fast y /kick-fast con text/plain + sendBeacon/XHR, sin Promises ni headers personalizados.
      let printJobs = [];
      const printOperatingSystem = normalizePrintOperatingSystem(r.print_operating_system);
      const printPaperSize = normalizePrintPaperSize(r.print_paper_size);
      const printAutoCut = normalizePrintAutoCut(r.print_auto_cut);
      const feedLines = printPaperSize === "pequena" ? 4 : 13;
      const receiptText = (r.receipt_text || "Factura\n\n") + "\n".repeat(feedLines);
      const shouldKickCashDrawer = (
        totalNum > 0
        && !!ef
        && safeNumber(ef.monto) > 0
      );

      if (printOperatingSystem === "linux") {
        const printJob = printViaLinuxServer(r.venta_id, printPaperSize, {
          openDrawer: shouldKickCashDrawer && !!r.print_token,
          printToken: r.print_token || ""
        });
        printJobs.push(printJob);
        printJob.catch((error) => {
          // La venta ya fue confirmada: un fallo de impresion nunca debe
          // provocar que se envie nuevamente el formulario de venta.
          console.error("[IMPRESION_LINUX]", error);
          window.setTimeout(() => {
            showFastSaleToast(
              `Venta #${r.venta_id} registrada, pero no se pudo imprimir: ${error?.message || "error desconocido"}`,
              5000
            );
          }, 0);
        });
      } else if (FAST_PRINT_FIRE_AND_FORGET) {
        const useUltra = FAST_POS_ULTRA_ENABLED && (FAST_POS_ULTRA_FORCE || posAgentUltraReady);

        if (useUltra) {
          agentPrintUltra(receiptText, { autoCut: printAutoCut });
          if (shouldKickCashDrawer) agentKickUltra();
        } else {
          const printJob = agentPrintFast(receiptText, { autoCut: printAutoCut });
          printJobs.push(printJob);
          if (shouldKickCashDrawer) printJobs.push(agentKickFast());

          // ✅ Si aún no se detectó el modo ultra, deja la detección corriendo para la próxima venta.
          agentDetectUltraFast();
        }
      } else {
        printJobs = [agentPrintSafe(receiptText, {
          timeout: 650,
          autoCut: printAutoCut,
        })];
        if (shouldKickCashDrawer) {
          printJobs.push(agentKickSafe({ timeout: 300 }));
        }
      }

      if (printJobs.length) {
        if (FAST_SALE_PRINT_WAIT_MS > 0) {
          try { await settleWithDeadline(printJobs, FAST_SALE_PRINT_WAIT_MS); } catch (_) {}
        } else {
          Promise.allSettled(printJobs).catch(() => {});
        }
      }

      const msgCambio = (totalNum > 0 && ef && !esMixto) ? `
Cambio: ${money(cambio)}` : "";
      const okMsg = buildSaleSuccessMessage(r, totalNum, msgCambio);

      const finishSaleUi = () => {
        // ✅ SIN RECARGAR: limpiar TODO para la siguiente venta.
        resetAfterSaleFast();
        saleDraftSaleConfirmed = false;
        saleSubmitting = false;
        confirmSubmitting = false;
        if ($submitBtn.length) $submitBtn.prop("disabled", false);
      };

      // ✅ Máxima velocidad percibida:
      // 1) /print ya fue iniciado arriba.
      // 2) Se muestra el alert con cambio en el primer tick libre.
      // 3) La limpieza se hace al cerrar el alert, porque mientras el alert está abierto no se puede vender.
      if (FAST_SALE_SUCCESS_ALERT && FAST_ALERT_BEFORE_RESET) {
        saleSubmitting = false;
        confirmSubmitting = false;
        if ($submitBtn.length) $submitBtn.prop("disabled", false);
        setTimeout(() => {
          try { alert(okMsg); }
          finally { finishSaleUi(); }
        }, 0);
      } else if (FAST_SALE_SUCCESS_ALERT) {
        finishSaleUi();
        setTimeout(() => alert(okMsg), 0);
      } else {
        finishSaleUi();
        showFastSaleToast(okMsg);
      }
    })
    .catch(() => {
      saleSubmitting = false;
      confirmSubmitting = false;
      // Un corte de red no permite saber si el servidor alcanzó a confirmar la
      // venta. Se conserva con advertencia para evitar reenvíos duplicados.
      saleDraftSubmissionPending = true;
      persistSaleDraftNow();
      updateSaleDraftStatus(
        "Venta sin confirmación de red: revisa Visualizar ventas antes de reintentar.",
        "error",
      );
      $hidMerk2888Password.val("");
      $("#merk2888-password-input").val("");
      if ($submitBtn.length) $submitBtn.prop("disabled", false);
      alert(
        isMerk2888ClientSelected()
          ? "No se recibió la confirmación de la venta. Antes de reintentar, revisa Visualizar ventas: la clave pudo haberse consumido correctamente."
          : "No se recibió la confirmación de la venta. Antes de reintentar, revisa Visualizar ventas para evitar registrarla dos veces."
      );
    });
  });

  /* ================== Atajos Ctrl + 0..4 ================== */
  $(document).on("keydown", function (e) {
    if ($("#myModal").is(":visible")) return;
    if (!e.ctrlKey || e.altKey || e.metaKey) return;
    const focusAndSelect = ($el) => { if ($el && $el.length) { $el.focus(); $el[0]?.select?.(); } };
    switch (e.key) {
      case "0": e.preventDefault(); focusAndSelect($inpCliente); break;
      case "1": e.preventDefault(); focusAndSelect($inpNombre); break;
      case "2": e.preventDefault(); focusAndSelect($inpCode); break;
      case "3": e.preventDefault(); focusAndSelect($buscarCart); break;
      case "4": e.preventDefault(); focusQtySmart(); break;
      default: break;
    }
  });

  /* ================== Atajos Alt + 0..4 ================== */
  (function setupAltShortcuts(){
    const focusAndSelect = ($el) => { if ($el && $el.length) { $el.focus(); $el[0]?.select?.(); } };
    $(document).on("keydown", function (e) {
      if ($("#myModal").is(":visible")) return;
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const k = e.key; if (!/^[0-4]$/.test(k)) return;
      e.preventDefault(); e.stopPropagation();
      switch (k) {
        case "0": focusAndSelect($inpCliente); break;
        case "1": focusAndSelect($inpNombre); break;
        case "2": focusAndSelect($inpCode); break;
        case "3": focusAndSelect($buscarCart); break;
        case "4": focusQtySmart(); break;
        default: break;
      }
    });
  })();

  /* ================== ESC: eliminar primer item visible ================== */
  $(document).on("keydown", function (e) {
    if ($("#myModal").is(":visible")) return;
    if (e.key !== "Escape") return;
    const $first = $tbody.find("tr:visible").first();
    if (!$first.length) return;
    e.preventDefault(); e.stopPropagation();
    const pid = String($first.data("pid") || "");
    removeRowByPidWithAuditIfEmpty(pid);
  });

  /* ================== ✅ SCANNER GUARD: qty-guard => code ================== */
  function isQtyElement(el){
    if (!el) return false;
    return ($cantidad && $cantidad.length && el === $cantidad[0]) || (el.classList && el.classList.contains("qty-input"));
  }

  // ✅ BLOQUEO TOTAL: si el modal está abierto, NO se agrega al carrito, NO se escribe en inputs de venta
  function pushCodeIntoCodeInputAndAdd(code){
    // ✅ si modal abierto: consumir y bloquear confirmación, pero NO hacer nada más
    if (isModalOpen()) {
      blockModalConfirmFor(900);
      return;
    }

    const clean = onlyDigits(code);
    if (!clean) return;
    if (isDuplicateScannerPush(clean)) return;

    // ✅ ANTI-MISREAD: si el formato es estándar (EAN/UPC/ITF) y el dígito
    //    verificador NO cuadra, el escáner leyó mal. Rechazamos sin tocar el
    //    carrito y avisamos visualmente al cajero.
    const checksumValid = validateBarcodeChecksum(clean);
    if (checksumValid === false) {
      flashScanError("Checksum inválido — posible mala lectura del escáner: " + clean);
      return;
    }

    suppressBarcodeAutocompleteAdd(clean, 1200);

    $inpCode.val(clean);
    try { $inpCode.autocomplete("close"); } catch (_){}
    try { $inpNombre.autocomplete("close"); } catch (_){}
    try { if ($inpId && $inpId.length) $inpId.autocomplete("close"); } catch (_){}

    queueMicrotask(() => {
      if ($inpCode.is(":visible")) { $inpCode.focus(); $inpCode[0]?.select?.(); }
    });

    if (!hasSucursal()) return;

    // ✅ Camino ultrarrápido: si el código está en el snapshot/cache local como match exacto y único,
    //    se agrega inmediatamente. Si no hay certeza local, sigue el flujo original con servidor.
    const localFast = getLocalExactBarcodeProduct(clean);
    if (localFast) {
      setProductFields({
        nombre: localFast.name,
        pid: localFast.id,
        barcode: localFast.barcode || clean,
        focusQty: false,
      });

      if (!wasRecentlyAutoAddedByBarcode(clean, localFast.id, SCANNER_REPEAT_LOCK_MS)) {
        suppressBarcodeAutocompleteAdd(clean, SCANNER_SUPPRESS_AC_MS);
        if (addAutoProductToCartOnce(localFast.id, 1, {
          source: "scanner-local-fast",
          term: clean,
          barcode: clean,
          pidTtlMs: SCANNER_REPEAT_LOCK_MS,
          termTtlMs: SCANNER_REPEAT_LOCK_MS,
          barcodeTtlMs: SCANNER_REPEAT_LOCK_MS,
        })) {
          rememberBarcodeAutoAdd(clean, localFast.id);
        }
      }
      return;
    }

    const resolveSeq = beginBarcodeResolve(clean);
    resolveByBarcode(clean).then(pid => {
      if (pid === BARCODE_RESOLVE_BLOCKED) return;
      if (!pid) {
        flashScanError("Codigo de barras no encontrado: " + clean);
        return;
      }

      // ✅ BARCODE GUARD (3ra capa): verificación final antes de mandar al carrito.
      //    Aunque resolveByBarcode ya valida cache+servidor, hacemos una última
      //    confirmación contra la cache local. Si por cualquier motivo el pid
      //    resuelto NO tiene este barcode en cache, abortamos: jamás se agregará
      //    un producto cuyo barcode no coincida exactamente con el escaneado.
      const finalRec = productCache.get(String(pid));
      const finalBarcodeDigits = onlyDigits(String(finalRec?.barcode || ""));
      if (!finalBarcodeDigits || finalBarcodeDigits !== clean) {
        console.warn("[BARCODE GUARD] Abort: pid resuelto no tiene el barcode escaneado en cache", {
          scanned: clean,
          pid,
          cachedBarcode: finalBarcodeDigits,
        });
        return;
      }

      if (wasRecentlyAutoAddedByBarcode(clean, pid, SCANNER_REPEAT_LOCK_MS)) return;
      suppressBarcodeAutocompleteAdd(clean, SCANNER_SUPPRESS_AC_MS);
      if (addAutoProductToCartOnce(pid, 1, {
        source: "scanner",
        term: clean,
        barcode: clean,
        pidTtlMs: SCANNER_REPEAT_LOCK_MS,
        termTtlMs: SCANNER_REPEAT_LOCK_MS,
        barcodeTtlMs: SCANNER_REPEAT_LOCK_MS,
      })) {
        rememberBarcodeAutoAdd(clean, pid);
      }
    }).finally(() => {
      endBarcodeResolve(clean, resolveSeq);
    });
  }

  /* =======================================================================================
     ✅ ESCÁNER CÁMARA UNIVERSAL (BarcodeDetector + ZXing fallback) — iPhone/Safari OK
     ======================================================================================= */
  let camStream = null;
  let camRunning = false;

  let camDetector = null;

  let zxingReader = null;
  let zxingLoaded = false;
  let camFallbackTimer = 0;

  function isSecureContextForCamera() {
    const h = location.hostname;
    const isLocal = (h === "localhost" || h === "127.0.0.1");
    return !!(window.isSecureContext || isLocal);
  }

  function hasGetUserMedia() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function isIOSLike() {
    const ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/i.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function barcodeTextFromResult(result) {
    if (!result) return "";
    if (typeof result.getText === "function") return String(result.getText() || "").trim();
    return String(result.text || result.rawValue || "").trim();
  }

  function waitForVideoReady(video) {
    if (!video) return Promise.resolve();
    if (video.readyState >= 2 && video.videoWidth > 0) return Promise.resolve();

    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        video.removeEventListener("loadedmetadata", finish);
        video.removeEventListener("canplay", finish);
        resolve();
      };

      video.addEventListener("loadedmetadata", finish, { once: true });
      video.addEventListener("canplay", finish, { once: true });
      setTimeout(finish, 900);
    });
  }

  function acceptCameraCode(raw, hint) {
    const text = String(raw || "").trim();
    if (!text || !camRunning) return false;
    if (hint) hint.textContent = "Detectado: " + text;
    stopCameraScanner();
    pushCodeIntoCodeInputAndAdd(text);
    return true;
  }

  function ensureCamUI() {
    if (!document.getElementById("btn-scan-cam")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = "btn-scan-cam";
      btn.className = "btn btn-chip";
      btn.innerHTML = "📷";

      const ref = document.getElementById("codigo_o_barras");
      if (ref && ref.parentElement) ref.parentElement.appendChild(btn);
      else document.body.appendChild(btn);
    }

    if (!document.getElementById("cam-scan-overlay")) {
      const overlay = document.createElement("div");
      overlay.id = "cam-scan-overlay";
      overlay.style.cssText = `
        position:fixed; inset:0; background:rgba(0,0,0,.75); z-index:99999;
        display:none; align-items:center; justify-content:center; padding:16px;
      `;

      const box = document.createElement("div");
      box.style.cssText = `
        width:min(560px, 92vw); background:#0b1220; border-radius:14px;
        overflow:hidden; box-shadow:0 18px 50px rgba(0,0,0,.45);
      `;

      const header = document.createElement("div");
      header.style.cssText = `
        display:flex; align-items:center; justify-content:space-between;
        padding:10px 12px; color:#fff; font-weight:600;
        background:rgba(255,255,255,.06);
      `;
      header.innerHTML = `<span>Escanear con cámara</span>`;

      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.id = "cam-scan-close";
      closeBtn.textContent = "✕";
      closeBtn.style.cssText = `
        border:0; background:transparent; color:#fff; font-size:18px; cursor:pointer;
        padding:6px 10px; border-radius:10px;
      `;
      header.appendChild(closeBtn);

      const video = document.createElement("video");
      video.id = "cam-scan-video";
      video.setAttribute("playsinline", "true");
      video.setAttribute("webkit-playsinline", "true");
      video.setAttribute("muted", "true");
      video.setAttribute("autoplay", "true");
      video.setAttribute("x-webkit-airplay", "deny");
      video.muted = true;
      video.autoplay = true;
      video.style.cssText = `
        width:100%; height:auto; background:#000; display:block;
      `;

      const hint = document.createElement("div");
      hint.id = "cam-scan-hint";
      hint.style.cssText = `
        color:#cbd5e1; font-size:13px; padding:10px 12px;
      `;
      hint.textContent = "Apunta al código de barras…";

      box.appendChild(header);
      box.appendChild(video);
      box.appendChild(hint);
      overlay.appendChild(box);
      document.body.appendChild(overlay);

      closeBtn.addEventListener("click", stopCameraScanner);
      overlay.addEventListener("click", (e) => { if (e.target === overlay) stopCameraScanner(); });
    }
  }

  async function getBarcodeDetector() {
    if (camDetector) return camDetector;
    if (!("BarcodeDetector" in window)) return null;

    try {
      const formats = await window.BarcodeDetector.getSupportedFormats?.();
      const wanted = ["ean_13","ean_8","code_128","code_39","upc_a","upc_e","itf","codabar","qr_code"];
      const use = Array.isArray(formats) && formats.length ? formats.filter(f => wanted.includes(f)) : wanted;
      if (!use.length) return null;
      camDetector = new window.BarcodeDetector({ formats: use });
      return camDetector;
    } catch {
      camDetector = new window.BarcodeDetector();
      return camDetector;
    }
  }

  function loadZXing() {
    if (zxingLoaded) return Promise.resolve(true);

    return new Promise((resolve) => {
      const existing = document.getElementById("zxing-cdn");
      if (window.ZXing?.BrowserMultiFormatReader) {
        zxingLoaded = true;
        resolve(true);
        return;
      }

      if (existing) {
        let settled = false;
        const finish = (ok) => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          zxingLoaded = !!ok;
          resolve(zxingLoaded);
        };
        const timeout = setTimeout(() => {
          finish(!!window.ZXing?.BrowserMultiFormatReader);
        }, 5000);

        existing.addEventListener("load", () => {
          finish(!!window.ZXing?.BrowserMultiFormatReader);
        }, { once: true });

        existing.addEventListener("error", () => {
          finish(false);
        }, { once: true });
        return;
      }

      const s = document.createElement("script");
      s.id = "zxing-cdn";
      s.src = "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js";
      s.async = true;

      s.onload = () => { zxingLoaded = true; resolve(true); };
      s.onerror = () => { zxingLoaded = false; resolve(false); };

      document.head.appendChild(s);
    });
  }

  async function getZXingReader() {
    if (zxingReader) return zxingReader;
    const ok = await loadZXing();
    if (!ok) return null;

    const ZXing = window.ZXing;
    if (!ZXing) return null;

    try {
      zxingReader = new ZXing.BrowserMultiFormatReader();
      return zxingReader;
    } catch {
      return null;
    }
  }

  async function openCameraStream() {
    const attempts = [
      {
        audio: false,
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      },
      {
        audio: false,
        video: {
          facingMode: "environment",
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
      },
      { audio: false, video: true },
    ];

    let lastError = null;
    for (const constraints of attempts) {
      try {
        camStream = await navigator.mediaDevices.getUserMedia(constraints);
        return camStream;
      } catch (err) {
        lastError = err;
      }
    }

    throw lastError || new Error("No se pudo abrir la camara.");
  }

  async function startWithBarcodeDetector(video, hint) {
    if (isIOSLike()) return false;

    const detector = await getBarcodeDetector();
    if (!detector) return false;

    const tick = async () => {
      if (!camRunning) return;

      try {
        const codes = await detector.detect(video);
        if (codes && codes.length) {
          const raw = (codes[0].rawValue || "").trim();
          if (acceptCameraCode(raw, hint)) return;
        }
      } catch (_){}

      requestAnimationFrame(tick);
    };

    requestAnimationFrame(tick);
    return true;
  }

  async function startWithZXing(video, hint) {
    const reader = await getZXingReader();
    if (!reader) return false;

    if (typeof reader.decodeFromVideoElementContinuously === "function") {
      try {
        if (hint) hint.textContent = "Apunta al codigo de barras. En iPhone puede tardar unos segundos...";
        await Promise.resolve(reader.decodeFromVideoElementContinuously(video, (result) => {
          if (!camRunning || !result) return;
          acceptCameraCode(barcodeTextFromResult(result), hint);
        }));
        return true;
      } catch (err) {
        console.warn("[CAM] ZXing continuous error:", err);
      }
    }

    const loop = async () => {
      if (!camRunning) return;
      try {
        const result = await reader.decodeOnceFromVideoElement(video);
        if (acceptCameraCode(barcodeTextFromResult(result), hint)) return;
      } catch (_) {}
      requestAnimationFrame(loop);
    };

    requestAnimationFrame(loop);
    return true;
  }

  async function startCameraScanner() {
    // ✅ si está el modal abierto, no hagas nada (evita conflictos de pago)
    if (isModalOpen()) {
      blockModalConfirmFor(900);
      return;
    }

    ensureCamUI();

    const $overlay = $("#cam-scan-overlay");
    const video = document.getElementById("cam-scan-video");
    const hint  = document.getElementById("cam-scan-hint");

    if (!isSecureContextForCamera()) {
      alert("La cámara solo funciona en HTTPS o localhost.");
      return;
    }
    if (!hasGetUserMedia()) {
      alert("Este navegador no permite acceso a cámara (getUserMedia no disponible).");
      return;
    }

    camRunning = true;
    $overlay.css("display", "flex");
    if (hint) hint.textContent = "Solicitando permiso de cámara...";

    try {
      await openCameraStream();
    } catch (err) {
      console.warn("[CAM] getUserMedia error:", err);
      stopCameraScanner();
      alert("No se pudo abrir la cámara. En iPhone usa Safari/HTTPS y permite el acceso a Cámara.");
      return;
    }

    video.srcObject = camStream;
    try { await video.play(); } catch (err) { console.warn("[CAM] video.play error:", err); }
    await waitForVideoReady(video);
    if (hint) hint.textContent = "Apunta al codigo de barras...";

    if (isIOSLike()) {
      const okIOS = await startWithZXing(video, hint);
      if (okIOS) return;
    }

    const okBD = await startWithBarcodeDetector(video, hint);
    if (okBD) {
      camFallbackTimer = setTimeout(() => {
        if (!camRunning) return;
        startWithZXing(video, hint).catch((err) => console.warn("[CAM] ZXing fallback error:", err));
      }, 1400);
      return;
    }

    const okZX = await startWithZXing(video, hint);
    if (okZX) return;

    stopCameraScanner();
    alert("No se pudo iniciar el escáner en este dispositivo.");
  }

  function stopCameraScanner() {
    camRunning = false;

    if (camFallbackTimer) {
      clearTimeout(camFallbackTimer);
      camFallbackTimer = 0;
    }

    $("#cam-scan-overlay").hide();

    try {
      const video = document.getElementById("cam-scan-video");
      if (video) {
        try { video.pause(); } catch (_){}
        video.srcObject = null;
      }
    } catch (_){}

    if (camStream) {
      try { camStream.getTracks().forEach(t => t.stop()); } catch (_){}
      camStream = null;
    }

    try { zxingReader?.reset?.(); } catch(_){}
  }

  $(document).off("click.scanCam").on("click.scanCam", "#btn-scan-cam", function(){
    startCameraScanner();
  });

  $(document).on("keydown", function(e){
    if ($("#myModal").is(":visible")) return;
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "7") {
      e.preventDefault(); e.stopPropagation();
      startCameraScanner();
    }
  });

  function commitCurrentQtyLikeEnterIfNeeded(originEl){
    if ($cantidad && $cantidad.length && originEl === $cantidad[0]) {
      const committed = normalizeQtyOnCommit($cantidad[0]);
      const qty = clampQtyAnySign(committed);
      const pid = $pid.val();
      if (pid && $agregar && $agregar.length && !$agregar.prop("disabled")) addToCartLastOnly(pid, qty);
      return;
    }
    if (originEl && originEl.classList && originEl.classList.contains("qty-input")) {
      const n = commitRowQtyInput(originEl);
      const $row = $(originEl).closest("tr");
      applyQtyInstant($row, n);
      scheduleVerifyRowPrice($row, 0);
    }
  }

  (function scannerDetectorWithQtyGuard() {
    const MIN_CHARS = 8;
    const GAP_MS = 80;

    let buf = "";
    let first = 0;
    let last = 0;
    let idleTimer = null;
    let finalizeTimer = null;

    let scanning = false;
    let originEl = null;
    let originStartValue = "";

    function isCodeInput(el) {
      return !!($inpCode && $inpCode.length && el === $inpCode[0]);
    }

    function restoreOriginIfNeeded() {
      if (!originEl || isCodeInput(originEl)) return;
      try {
        if (typeof originEl.value === "string") originEl.value = originStartValue;
      } catch (_) {}
    }

    function focusCodeInputWith(value, { search = false } = {}) {
      const clean = onlyDigits(value);
      if (!clean || !$inpCode || !$inpCode.length || !$inpCode.is(":visible")) return;

      try { if (document.activeElement !== $inpCode[0]) $inpCode.focus(); } catch (_) {}
      try { $inpCode.val(clean); } catch (_) {}
      try { $inpCode[0]?.setSelectionRange?.(clean.length, clean.length); } catch (_) {}

      // Mientras entra la ráfaga solo llenamos el input. Al finalizar sí se resuelve/agrega.
      if (search) {
        try { $inpCode.autocomplete("search", clean); } catch (_) {}
      }
    }

    function resetAll(){
      buf = "";
      first = 0;
      last = 0;
      scanning = false;
      originEl = null;
      originStartValue = "";
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (finalizeTimer) { clearTimeout(finalizeTimer); finalizeTimer = null; }
    }

    function finalize(code){
      const c = onlyDigits(code || "");
      if (!c) { resetAll(); return; }

      const wasQty = isQtyElement(originEl);

      if (wasQty) {
        restoreOriginIfNeeded();
        commitCurrentQtyLikeEnterIfNeeded(originEl);
      } else {
        restoreOriginIfNeeded();
      }

      focusCodeInputWith(c, { search: false });
      pushCodeIntoCodeInputAndAdd(c);
      resetAll();
    }

    function scheduleAutoFinalize(){
      if (finalizeTimer) clearTimeout(finalizeTimer);
      // Algunos escáneres no mandan Enter/Tab. Finalizamos rápido al terminar la ráfaga.
      finalizeTimer = setTimeout(() => {
        if (scanning && buf.length >= MIN_CHARS) finalize(buf);
        else resetAll();
      }, GAP_MS * 4);
    }

    document.addEventListener("keydown", function (e) {
      // ✅ si el modal está abierto, NO uses este detector (lo maneja el guard del modal)
      if (isModalOpen()) { resetAll(); return; }

      // ✅ Algunos lectores emiten Alt/NumLock/CapsLock/Pause y otros como artefacto
      // del modo emulación (ALT+NumPad toggle). Ignorar sin tocar el buffer.
      if (SCANNER_ARTIFACT_KEYS.has(e.key)) return;

      // Solo reseteamos en atajos reales (Ctrl+algo / Meta+algo), no en Alt
      if (e.ctrlKey || e.metaKey) { resetAll(); return; }

      const active = document.activeElement;
      const inQty = isQtyElement(active);
      if (isClienteBusquedaElement(active)) { resetAll(); return; }
      const t = Date.now();

      if (e.key === "Enter" || e.key === "Tab") {
        const fastEnough = buf && (t-first) < buf.length * (GAP_MS+5) && (t-last) < GAP_MS*3;
        if (fastEnough && buf.length >= MIN_CHARS) {
          e.preventDefault();
          e.stopImmediatePropagation();
          finalize(buf);
          return;
        }
        resetAll();
        return;
      }

      if (e.key && e.key.length === 1) {
        const char = e.key;
        const isDigit = /^\d$/.test(char);

        // El lector de códigos de barras en caja debe redirigir principalmente dígitos.
        // Si llega texto/letras, se deja que los autocompletes manuales trabajen normal.
        if (!isDigit) { resetAll(); return; }

        if (originEl && active !== originEl && !scanning) resetAll();

        if (!buf) {
          originEl = active;
          originStartValue = (active && typeof active.value === "string") ? active.value : "";
          first = t;
          last = t;
          buf = char;
        } else {
          if ((t - last) > GAP_MS) {
            resetAll();
            originEl = active;
            originStartValue = (active && typeof active.value === "string") ? active.value : "";
            first = t;
            last = t;
            buf = char;
          } else {
            buf += char;
            last = t;
          }
        }

        // ✅ Restauración del comportamiento perdido:
        // Apenas detectamos una ráfaga de escáner, movemos el foco al autocomplete de código
        // y lo vamos llenando aunque el foco original estuviera en cliente, nombre, cantidad, tabla, etc.
        if (!scanning && buf.length >= 2 && (last - first) <= GAP_MS + 8) {
          scanning = true;
          restoreOriginIfNeeded();
          focusCodeInputWith(buf, { search: false });
        }

        if (scanning) {
          e.preventDefault();
          e.stopImmediatePropagation();
          focusCodeInputWith(buf, { search: false });
          scheduleAutoFinalize();
        }

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => resetAll(), GAP_MS * 8);

        if (scanning && buf.length >= MIN_CHARS && inQty) {
          e.preventDefault();
          e.stopImmediatePropagation();
          finalize(buf);
          return;
        }

        return;
      }

      if (e.key !== "Shift") resetAll();
    }, true);
  })();

  (function globalScannerFallback() {
    const MIN_CHARS = 8, GAP_MS = 80;
    let buf="", first=0, last=0, idleTimer=null;

    function reset(){ buf=""; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} }

    document.addEventListener("keydown", function (e) {
      // ✅ si el modal está abierto, NO uses este fallback (lo maneja el guard del modal)
      if (isModalOpen()) { reset(); return; }

      const active = document.activeElement;
      if (isQtyElement(active) || isClienteBusquedaElement(active)) { reset(); return; }

      // ✅ Ignorar teclas artefacto de lectores genéricos (Alt, NumLock, CapsLock, etc).
      if (SCANNER_ARTIFACT_KEYS.has(e.key)) return;

      // Solo reseteamos en atajos reales (Ctrl+algo / Meta+algo)
      if (e.ctrlKey || e.metaKey) { reset(); return; }
      const t = Date.now();

      if (e.key === "Enter" || e.key === "Tab") {
        const fastEnough = buf && (t-first) < buf.length * (GAP_MS+5) && (t-last) < GAP_MS*3;
        if (fastEnough && buf.length >= MIN_CHARS) {
          e.preventDefault(); e.stopImmediatePropagation();
          const code = buf; reset();
          pushCodeIntoCodeInputAndAdd(code);
          return;
        }
        reset(); return;
      }

      if (e.key && e.key.length === 1) {
        if (buf && (t-last) > GAP_MS) { buf = ""; first = t; }
        if (!buf) first = t;
        buf += e.key; last = t;
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(reset, GAP_MS*5);
      } else {
        if (e.key !== "Shift") reset();
      }
    }, true);
  })();

  /* ================== Init ================== */
  if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
  if ($agregar && $agregar.length)  $agregar.prop("disabled", true);
  if ($tbody.find("tr").length === 0) setTotal(0);
  $("#venta-draft-toggle").on("click", function(){
    const $panel = $("#venta-draft-panel");
    const willOpen = $panel.prop("hidden");
    $panel.prop("hidden", !willOpen);
    $(this).attr("aria-expanded", willOpen ? "true" : "false");
    if (willOpen) queueMicrotask(() => $panel.find(".js-draft-restore").first().trigger("focus"));
  });
  $("#venta-draft-close").on("click", () => closeSaleDraftPanel(true));
  $("#venta-draft-new").on("click", continueWithNewSale);
  $("#venta-draft-list").on("click", ".js-draft-restore", function(){
    const draft = findManagedSaleDraft($(this).attr("data-draft-key"));
    if (!draft) {
      offerSaleDraftForCurrentScope();
      return;
    }
    pendingSaleDraft = draft;
    restorePendingSaleDraft(draft);
  });
  $("#venta-draft-list").on("click", ".js-draft-discard", function(){
    const draft = findManagedSaleDraft($(this).attr("data-draft-key"));
    if (!draft) {
      offerSaleDraftForCurrentScope();
      return;
    }
    pendingSaleDraft = draft;
    discardPendingSaleDraft(draft);
  });
  $(document).on("click.ventaDraftManager", (event) => {
    if (!$(event.target).closest("#venta-draft-center").length) closeSaleDraftPanel();
  });
  $(document).on("keydown.ventaDraftManager", (event) => {
    if (event.key === "Escape" && !$("#venta-draft-panel").prop("hidden")) {
      event.preventDefault();
      closeSaleDraftPanel(true);
    }
  });
  $("#venta-draft-retry-validation").on("click", () => {
    void revalidateRestoredSaleDraft({
      items: captureSaleDraftItems(),
      client: captureSaleDraftClient(),
    });
  });
  saleDraftAutosaveReady = true;
  void resumeSaleDraftPage();
  cleanupExpiredSaleDrafts();
  refreshSaleDraftGenerateButton();
  offerSaleDraftForCurrentScope();
  window.addEventListener("focus", offerSaleDraftForCurrentScope);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) offerSaleDraftForCurrentScope();
  });
  setInterval(() => {
    if (!document.hidden && !saleDraftPageHidden) offerSaleDraftForCurrentScope();
  }, 2000);
  window.addEventListener("storage", (event) => {
    if (!event || !isSaleDraftKeyForCurrentScope(event.key) || saleDraftRestoring) return;
    if (!productos.length) {
      offerSaleDraftForCurrentScope();
      return;
    }
    if (event.key !== saleDraftStorageKey()) {
      offerSaleDraftForCurrentScope();
      return;
    }
    let incoming = null;
    try { incoming = JSON.parse(event.newValue || "null"); } catch (_) {}
    const incomingOwner = sanitizeDraftText(incoming?.owner_tab_id || "", 100);
    if (!incoming || (incomingOwner && incomingOwner !== saleDraftTabID)) {
      const message = "Otra pestaña modificó la venta pendiente de esta caja. Recarga esta página antes de cobrar para evitar sobrescribirla.";
      saleDraftOwnershipLost = true;
      setSaleDraftValidation({ error: message });
      updateSaleDraftStatus(message, "error");
    }
  });
  if (!POS_AGENT_TOKEN) console.warn("[POS_AGENT] Token vacío: el agente podría rechazar (401).");
});
