// static/javascript/generar_venta.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  console.log("⚡ generar_venta.js — AC ultra + snapshot L1 + live price + ✅ allow qty negativo (devolución) + modal MIXTO + POS Agent + submit ultrarrápido + scanner qty-guard + ✅ cámara universal (BarcodeDetector + ZXing fallback)");

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

  /* ================== Agente local ================== */
  const POS_AGENT_URL   = (window.POS_AGENT_URL || "http://127.0.0.1:8787").replace(/\/+$/,'');
  const POS_AGENT_TOKEN = (window.POS_AGENT_TOKEN || "").trim();

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
  const money = (n) =>
    new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP" })
      .format(Number(n) || 0);

  const onlyDigits = (s) => String(s||"").replace(/\D+/g, "");
  const norm = (s)=> (s||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();

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
  let sucursalID = (localStorage.getItem("sucursalID") || "").toString().match(/\d+/)?.[0] || "";
  const savedPunto = {
    id:  localStorage.getItem("puntopagoID") || "",
    name:localStorage.getItem("puntopagoName") || "",
    suc: localStorage.getItem("puntopagoSucursalID") || ""
  };
  const hasSucursal = () => /^\d+$/.test(String(sucursalID || ""));

  (function bootstrapLockedSucursalAndPunto(){
    const domSid = String($("#sucursal_id").val() || "").match(/\d+/)?.[0] || "";
    if (!sucursalID && domSid) {
      sucursalID = domSid;

      try {
        localStorage.setItem("sucursalID", sucursalID);
        localStorage.setItem("sucursalName", $("#sucursal_autocomplete").val() || "");
      } catch {}

      console.log("[BOOT] sucursalID tomado del DOM:", sucursalID);
    }

    const domPp = String($("#puntopago_id").val() || "").match(/\d+/)?.[0] || "";
    const domPpName = $("#puntopago_autocomplete").val() || "";
    if (domPp) {
      try {
        localStorage.setItem("puntopagoID", domPp);
        localStorage.setItem("puntopagoName", domPpName);
        localStorage.setItem("puntopagoSucursalID", sucursalID || domSid || "");
      } catch {}

      console.log("[BOOT] puntopago tomado del DOM:", domPp, domPpName);
    }
  })();

  /* ================== Estado venta ================== */
  const productos  = []; // ["12","99"...]
  const cantidades = []; // [ 1, -2, ... ]
  let runningTotal = 0;
  let lastAddedPid = null;

  const PROMO_BAG_21 = "21";
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

  function setTotal(v) {
    const safe = Number(v);
    runningTotal = Number.isFinite(safe) ? safe : 0;
    $totalEl.text(money(runningTotal));

    if ($modal && $modal.length && $modal.is(":visible") && $modalTotal && $modalTotal.length) {
      $modalTotal.text(money(runningTotal));
    }
    syncHiddenFields();
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

    $("#monto-recibido").val("");
    $("#cambio").text("");
    closeModal();
    lastAddedPid = null;
  }

  // ✅ LIMPIEZA COMPLETA POST-VENTA (SIN RECARGA)
  function resetAfterSaleFast() {
    // carrito + total + pagos + modal
    clearCartAndTotals();

    // cliente
    try { $("#cliente_id").val(""); } catch {}
    $inpCliente.val("");

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
      if ($inpCode && $inpCode.length && $inpCode.is(":visible")) {
        $inpCode.focus(); $inpCode[0]?.select?.();
      } else if ($inpNombre && $inpNombre.length) {
        $inpNombre.focus(); $inpNombre[0]?.select?.();
      }
    });
  }

  window.addEventListener("pageshow", (e) => {
    if (e.persisted) clearCartAndTotals();
    else {
      if ($tbody.find("tr").length === 0) setTotal(0);
      else enforceTotalIntegritySoft();
    }
  });

  let catalogPollTimer = null;
  function stopCatalogPolling(){
    if (catalogPollTimer) clearInterval(catalogPollTimer);
    catalogPollTimer = null;
  }
  window.addEventListener("beforeunload", () => {
    try { clearCartAndTotals(); } catch (_){ }
    try { stopCatalogPolling(); } catch (_){ }
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
    return $.post(VERIFICAR_URL, { producto_id: pid, cantidad: 1, sucursal_id: sucursalID, _ts: Date.now() })
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
         <td>${onlyName(name)}</td>
         <td><input type="number" class="qty-input" step="1" inputmode="numeric" value="${qty}" /></td>
         <td class="price-cell">${priceTxt}</td>
         <td class="subtotal-cell">${subtotalTxt}</td>
         <td class="text-center">
           <button class="btn btn-chip-danger eliminar-producto" title="Eliminar">
             <i class="fas fa-trash-alt"></i><span>Eliminar</span>
           </button>
         </td>
       </tr>`
    );
  }

  function removeRowByPid(pid){
    const key = String(pid);
    const $r = $tbody.find(`tr[data-pid='${key}']`);
    if (!$r.length) return false;

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
    }
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
  // - FAST_PRINT_KICK_AFTER_MS controla cuánto esperar antes de abrir cajón; 0 = paralelo inmediato.
  // Puedes ajustar desde el template si algún día necesitas otro comportamiento:
  //   window.FAST_PRINT_FIRE_AND_FORGET = true;
  //   window.FAST_PRINT_KICK_AFTER_MS = 0;
  //   window.FAST_SALE_SUCCESS_ALERT = true;
  const FAST_SALE_PRINT_WAIT_MS = Math.max(0, Number(window.FAST_SALE_PRINT_WAIT_MS ?? 0));
  const FAST_SALE_SUCCESS_ALERT = window.FAST_SALE_SUCCESS_ALERT !== false;
  const FAST_PRINT_FIRE_AND_FORGET = window.FAST_PRINT_FIRE_AND_FORGET !== false;
  const FAST_PRINT_KICK_AFTER_MS = Math.max(0, Number(window.FAST_PRINT_KICK_AFTER_MS ?? 0));
  const FAST_SUBMIT_VERIFY_PENDING_PRICES = window.FAST_SUBMIT_VERIFY_PENDING_PRICES === true;

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
    if (String(lastAddGuard.pid) === String(pid) && (ts - lastAddGuard.ts) < 250) return;
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
    return $.getJSON(POR_COD_URL, params)
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
      let left = `<span class="ac-name">${name}</span>`;

      if (mode === "code" && item.barcode) {
        left = `<span class="ac-code">${item.label}</span><span class="ac-sep"> — </span><span class="ac-name">${name}</span>`;
      } else if (mode === "id") {
        left = `<span class="ac-code">#${String(item.id)}</span><span class="ac-sep"> — </span><span class="ac-name">${onlyName(item.name || "")}</span>`;
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
    $("#sucursal_autocomplete").val(localStorage.getItem("sucursalName") || "");
    $("#sucursal_id").val(sucursalID);
    loadPickBoost(sucursalID);

    ensureCatalog(sucursalID).then(() => {
      initCatalogSignature(sucursalID);
      startCatalogPolling(sucursalID, { intervalMs: 2500 });
    });
  }

  if (savedPunto.id && savedPunto.suc && savedPunto.suc.toString() === sucursalID) {
    $("#puntopago_autocomplete").val(savedPunto.name || "");
    $("#puntopago_id").val(savedPunto.id);
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
      stopCatalogPolling();

      sucursalID = String(id).match(/\d+/)?.[0] || "";
      $("#sucursal_id").val(sucursalID);
      $("#sucursal_autocomplete").val(label);
      localStorage.setItem("sucursalID", sucursalID);
      localStorage.setItem("sucursalName", label);

      const ppSuc = localStorage.getItem("puntopagoSucursalID");
      if (ppSuc && ppSuc !== String(sucursalID)) {
        $("#puntopago_autocomplete").val(""); $("#puntopago_id").val("");
        localStorage.removeItem("puntopagoID");
        localStorage.removeItem("puntopagoName");
        localStorage.removeItem("puntopagoSucursalID");
      }

      if ($cantidad && $cantidad.length) $cantidad.prop("disabled", true);
      if ($agregar && $agregar.length)  $agregar.prop("disabled", true);

      loadPickBoost(sucursalID);

      await ensureCatalog(sucursalID, { force:true });
      initCatalogSignature(sucursalID);
      startCatalogPolling(sucursalID, { intervalMs: 2500 });
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
      $("#puntopago_autocomplete").val(label);
      $("#puntopago_id").val(id);
      localStorage.setItem("puntopagoID", id);
      localStorage.setItem("puntopagoName", label);
      localStorage.setItem("puntopagoSucursalID", sucursalID || "");
    }
  });

  /* ================== Cliente ================== */
  createAC({
    $inp: $inpCliente,
    minChars: 1,
    openIfEmpty: false,
    sourceFn: function(req, resp){
      (async ()=>{
        const term = (req && typeof req.term === "string") ? req.term : "";
        const d = await fetch(CLIENTE_URL + "?" + new URLSearchParams({ term, _ts: Date.now() }), { cache: "no-store" })
          .then(r=> r.ok ? r.json() : {results:[]})
          .catch(()=>({results:[]}));
        resp((d.results||[]).map(c=>({ id:c.id, label:c.text, value:c.text, name:c.text })));
      })();
    },
    onSelect: ({ id, label }) => { $inpCliente.val(label); $("#cliente_id").val(id); }
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
    const pid = String($row.data("pid") || "");
    if (!pid) return;

    const oldQty = Number($row.attr("data-qty")) || Number($row.find(".qty-input").val()) || 0;
    const wasCounted = !!$row.data("counted");

    if (newQty === 0) {
      removeRowByPid(pid);
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
    removeRowByPid(pid);
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
    if (!productos.length) return;
    if (!confirm("¿Vaciar todo el carrito?")) return;

    productos.length = 0;
    cantidades.length = 0;
    $tbody.empty();
    setTotal(0);
    lastAddedPid = null;

    $hidMedioPago.val("");
    $hidPagos.val("");
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
    const total = safeNumber(runningTotal);
    const medios = getCheckedMedios();
    if (!medios.length) return 0;

    if (!isMixtoUI()) {
      return (medios.length === 1) ? total : 0;
    }
    return sumMixtoSelectedAmounts();
  }

  function refreshPendingUI(){
    const total = safeNumber(runningTotal);
    const paid  = safeNumber(computePaidSoFar());
    const diff  = total - paid;

    if (!$pendingOut.length) return;

    if (isMixtoUI() && paid > total && Math.abs(diff) > 0.01) {
      $pendingOut.text(`Sobra por asignar: ${money(Math.abs(diff))}`);
      return;
    }
    $pendingOut.text(`Falta por pagar: ${money(Math.max(0, diff))}`);
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

    const efMonto = safeNumber(runningTotal);
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
          const total = safeNumber(runningTotal);
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
          if (parseAmt($amt.val()) <= 0) $amt.val(to2(runningTotal || 0));
        }
      }
    } else {
      $modal.find(".pm-amt").val("").prop("disabled", true).each(function(){ setAmtVisibility($(this), false); });
      $modal.find(".mix-row, .pm-row, .payment-row").removeClass("show-amt");
    }

    applyModeRules();
  });

  function buildPagosJSONOrError(){
    const total = safeNumber(runningTotal);

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
    return { pagos };
  }

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
    if (!productos.length) { alert("Agregue productos."); return; }
    if (!hasSucursal() || !$("#puntopago_id").val()) { alert("Seleccione sucursal y punto de pago."); return; }

    enforceTotalIntegrity();

    if (safeNumber(runningTotal) <= 0) {
      $hidPagos.val("[]");
      $hidMedioPago.val("");
      $("#venta-form").trigger("submit");
      return;
    }

    ensureMixUIExists();
    showMixError("");

    $("#modal-total").text(money(runningTotal));

    $hidPagos.val("");
    $hidMedioPago.val("");

    $modal.find(".pm-check").not("#mix-mode").prop("checked", false);
    $modal.find(".pm-amt").val("").prop("disabled", true).each(function(){ setAmtVisibility($(this), false); });
    $modal.find(".mix-row, .pm-row, .payment-row").removeClass("show-amt");

    $amountIn.val("");
    $changeOut.text("");

    if ($mixMode.length) $mixMode.prop("checked", false);
    $modal.attr("data-mixto","0");

    $modal.find(".pm-check[value='efectivo']").prop("checked", true);

    confirmSubmitting = false;
    const $btnConfirm = $("#confirmar-pago");
    $modal.attr("data-loading-prices","0");
    $btnConfirm.prop("disabled", false);

    // ✅ quitar bloqueo viejo
    modalConfirmBlockUntil = 0;

    applyModeRules();
    openModal();

    queueMicrotask(() => {
      if (!isMixtoUI() && $amountIn.is(":visible")) { $amountIn.focus(); $amountIn[0]?.select?.(); }
    });

    if (REPRICE_ON_MODAL) {
      $btnConfirm.prop("disabled", true);
      $modal.attr("data-loading-prices","1");
      requestAnimationFrame(() => {
        repricingMode = true;
        repriceAllRowsAndRecalcTotalPooled(8)
          .then(() => {
            $("#modal-total").text(money(runningTotal));
            applyModeRules();
          })
          .catch(() => {})
          .finally(() => {
            repricingMode = false;
            $modal.attr("data-loading-prices","0");
            $btnConfirm.prop("disabled", false);
            queueMicrotask(() => {
              if (!isMixtoUI() && $amountIn.is(":visible")) { $amountIn.focus(); $amountIn[0]?.select?.(); }
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
    const GAP_MS = 35;

    let buf = "";
    let first = 0;
    let last = 0;
    let scanning = false;

    let idleTimer = null;
    let finalizeTimer = null;

    function reset() {
      buf = "";
      first = 0;
      last = 0;
      scanning = false;
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (finalizeTimer) { clearTimeout(finalizeTimer); finalizeTimer = null; }
    }

    function markActivity() {
      // más largo para cubrir Enter/Tab + un frame extra
      blockModalConfirmFor(900);
    }

    function finalize(_code) {
      // ✅ Modal = bloqueo total: no agregues productos
      markActivity();
      reset();
    }

    document.addEventListener("keydown", function (e) {
      if (!isModalOpen()) { reset(); return; }

      // Si el usuario usa atajos o teclas modificadoras, asumimos NO escáner
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }

      const t = Date.now();

      if (e.key === "Enter" || e.key === "Tab") {
        if (buf || scanning) {
          e.preventDefault();
          e.stopImmediatePropagation();
          e.stopPropagation();

          markActivity();

          const fastEnough = buf && (t-first) < buf.length * (GAP_MS+5) && (t-last) < GAP_MS*3;
          if (fastEnough && buf.length >= MIN_CHARS) finalize(buf);
          else reset();

          return;
        }
        reset();
        return;
      }

      if (e.key && e.key.length === 1) {
        // Construcción de buffer con timing
        if (!buf) {
          buf = e.key;
          first = t;
          last = t;
          scanning = false;
        } else {
          if ((t - last) > GAP_MS) {
            // corte: no era escáner continuo
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
        if (buf.length >= 2 && (t - first) < buf.length * (GAP_MS + 8)) scanning = true;

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
  });

  $modal.on("input", ".pm-amt", function(){
    showMixError("");
    refreshEfectivoUI();
    refreshPendingUI();
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

    const built = buildPagosJSONOrError();
    if (built.error) { showMixError(built.error); return; }

    confirmSubmitting = true;
    const $btn = $("#confirmar-pago");
    $btn.prop("disabled", true);

    const pagos = built.pagos || [];
    $hidPagos.val(JSON.stringify(pagos));

    const medioCompat = (pagos.length >= 2) ? "mixto" : (pagos[0]?.medio_pago || "");
    $hidMedioPago.val(medioCompat);

    closeModal();
    $("#venta-form").trigger("submit");
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

  // ✅ Versión más rápida: inicia el POST /print y devuelve inmediatamente.
  // No usa await ni AbortController, para no cancelar impresiones lentas ni meter esperas.
  function agentPrintFast(text) {
    if (!POS_AGENT_TOKEN) return Promise.resolve();
    return fireAndForgetFetch(POS_AGENT_URL + "/print", {
      method: "POST",
      keepalive: true,
      headers: POS_AGENT_HEADERS_JSON,
      body: JSON.stringify({ text })
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

  async function agentPrintSafe(text, { timeout = 700 } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(POS_AGENT_URL + "/print", {
        method: "POST",
        keepalive: true,
        headers: POS_AGENT_HEADERS_JSON,
        body: JSON.stringify({ text }),
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
    if (!POS_AGENT_TOKEN) return;
    fireAndForgetFetch(POS_AGENT_URL + "/ping", {
      method: "GET",
      keepalive: true,
      headers: POS_AGENT_HEADERS_TOKEN
    });
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

  $("#venta-form").off("submit").on("submit", function (e) {
    e.preventDefault();
    if (saleSubmitting) return;
    saleSubmitting = true;

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
        if ($submitBtn.length) $submitBtn.prop("disabled", false);
        alert((r && r.error) || "Error");
        return;
      }

      const pagos = readPagosFromHidden();
      const totalNum = safeNumber(runningTotal);

      const esMixto = isMixtoFromPagos(pagos);
      const ef = (pagos || []).find(p => String(p.medio_pago || "").toLowerCase() === "efectivo");

      // ✅ capturar recibido ANTES de limpiar inputs
      let recibidoEfectivo = totalNum;
      if (totalNum > 0 && ef && !esMixto) {
        const raw = ($("#monto-recibido").val() || "").trim();
        recibidoEfectivo = (raw === "" ? totalNum : parseAmt(raw));
        if (raw === "") $("#monto-recibido").val(to2(totalNum));
      }

      const cambio = (totalNum > 0 && ef && !esMixto)
        ? Math.max(0, recibidoEfectivo - totalNum)
        : 0;

      let receiptText = (r.receipt_text || "Factura\n\n");

      if (totalNum > 0 && ef && !esMixto) {
        receiptText += `\nRecibido: ${money(recibidoEfectivo)}\nCambio:   ${money(cambio)}\n\n\n\n\n\n\n\n\n\n\n\n\n`;
      } else {
        receiptText += `\n\n\n\n\n\n\n\n\n\n\n\n\n`;
      }

      // ✅ Mandar factura a impresión lo más rápido posible.
      // Primero /print; el cajón se dispara en paralelo o justo después, para no competir con la factura.
      let printJobs = [];
      if (FAST_PRINT_FIRE_AND_FORGET) {
        const printJob = agentPrintFast(receiptText);
        printJobs.push(printJob);

        const kickJob = FAST_PRINT_KICK_AFTER_MS > 0
          ? new Promise((resolve) => setTimeout(() => resolve(agentKickFast()), FAST_PRINT_KICK_AFTER_MS))
          : agentKickFast();
        printJobs.push(kickJob);
      } else {
        printJobs = [
          agentPrintSafe(receiptText, { timeout: 650 }),
          agentKickSafe({ timeout: 300 })
        ];
      }

      if (FAST_SALE_PRINT_WAIT_MS > 0) {
        try { await settleWithDeadline(printJobs, FAST_SALE_PRINT_WAIT_MS); } catch (_) {}
      } else {
        Promise.allSettled(printJobs).catch(() => {});
      }

      // ✅ SIN RECARGAR: limpiar TODO para la siguiente venta inmediatamente.
      resetAfterSaleFast();

      // ✅ permitir siguiente venta inmediatamente
      saleSubmitting = false;
      confirmSubmitting = false;
      if ($submitBtn.length) $submitBtn.prop("disabled", false);

      const msgCambio = (totalNum > 0 && ef && !esMixto) ? `\nCambio: ${money(cambio)}` : "";
      const okMsg = `✅ Venta registrada\nTotal: ${money(totalNum)}${msgCambio}`;

      // ✅ Conserva el alert, pero deja respirar al event loop para que el POST /print arranque primero.
      if (FAST_SALE_SUCCESS_ALERT) setTimeout(() => alert(okMsg), 0);
      else showFastSaleToast(okMsg);
    })
    .catch(() => {
      saleSubmitting = false;
      confirmSubmitting = false;
      if ($submitBtn.length) $submitBtn.prop("disabled", false);
      alert("Error de red");
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
    removeRowByPid(pid);
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

      if (!wasRecentlyAutoAddedByBarcode(clean, localFast.id)) {
        suppressBarcodeAutocompleteAdd(clean, 900);
        if (addAutoProductToCartOnce(localFast.id, 1, {
          source: "scanner-local-fast",
          term: clean,
          barcode: clean,
          pidTtlMs: 900,
          termTtlMs: 900,
          barcodeTtlMs: 900,
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

      if (wasRecentlyAutoAddedByBarcode(clean, pid)) return;
      suppressBarcodeAutocompleteAdd(clean, 900);
      if (addAutoProductToCartOnce(pid, 1, {
        source: "scanner",
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
    const GAP_MS = 35;

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

      if (e.ctrlKey || e.altKey || e.metaKey) { resetAll(); return; }

      const active = document.activeElement;
      const inQty = isQtyElement(active);
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
    const MIN_CHARS = 8, GAP_MS = 35;
    let buf="", first=0, last=0, idleTimer=null;

    function reset(){ buf=""; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} }

    document.addEventListener("keydown", function (e) {
      // ✅ si el modal está abierto, NO uses este fallback (lo maneja el guard del modal)
      if (isModalOpen()) { reset(); return; }

      if (isQtyElement(document.activeElement)) return;
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }
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
  if (!POS_AGENT_TOKEN) console.warn("[POS_AGENT] Token vacío: el agente podría rechazar (401).");
});
