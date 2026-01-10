// static/javascript/generar_venta.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  console.log("⚡ generar_venta.js — AC ultra + snapshot L1 + live price + anti-zero + burst last-only + atajos + modal + POS Agent + submit ultrarrápido + ✅ scanner: qty-guard => code + ✅ live snapshot sync (precio/barcode AC) + ✅ pagos MIXTO + ✅ restante en vivo + ✅ AC independiente por ID + ✅ modal instant + ✅ finalize/kick ultra fast + ✅ repricing pooled (8) + ✅ total 1x");

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
  const $cantidad   = $("#cantidad"); // (si no existe, no rompe)
  const $agregar    = $("#agregar-producto"); // (si no existe, no rompe)
  const $tbody      = $("#detalle-productos tbody");
  const $totalEl    = $("#total");
  const $buscarCart = $("#buscar-detalles");
  const $btnVaciar  = $("#vaciar-carrito");

  // ✅ pagos mixto
  const $hidPagos     = $("#pagos");      // input hidden name="pagos"
  const $hidMedioPago = $("#medio_pago"); // compat (efectivo/tarjeta/transferencia/mixto)

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
  const money = (n) => new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP" })
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

  const clampQty = (x) => {
    const n = parseInt(String(x).replace(/\D+/g, ""), 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  };

  const now = () => Date.now();

  function classifyQuery(term){
    const raw = String(term || "").trim();
    const digits = onlyDigits(raw);
    const hasLetters = /[a-záéíóúñ]/i.test(raw);
    const compact = raw.replace(/\s+/g,"");
    const isPureDigits = digits.length > 0 && digits.length === compact.length;
    const isBarcodeLike = isPureDigits && digits.length >= 6;
    return { raw, digits, hasLetters, isPureDigits, isBarcodeLike };
  }

  function safeNumber(x){
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }

  // ✅ mantiene decimales (admite "1,25" -> 1.25)
  function parseAmt(v){
    const n = parseFloat(String(v||"").trim().replace(",", "."));
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  // ✅ siempre 2 decimales "seguros"
  function to2(n){
    const x = Number(n);
    if (!Number.isFinite(x)) return "0.00";
    return (Math.round(x * 100) / 100).toFixed(2);
  }

  /* ================== Estado persistido ================== */
  let sucursalID = (localStorage.getItem("sucursalID") || "").toString().match(/\d+/)?.[0] || "";
  const savedPunto = {
    id:  localStorage.getItem("puntopagoID") || "",
    name:localStorage.getItem("puntopagoName") || "",
    suc: localStorage.getItem("puntopagoSucursalID") || ""
  };
  const hasSucursal = () => /^\d+$/.test(String(sucursalID || ""));

  /* ================== Estado venta ================== */
  const productos  = [];
  const cantidades = [];
  let runningTotal = 0;
  let lastAddedPid = null;

  const defer = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 150 }) : setTimeout(fn, 0));
  function syncHiddenFields() {
    defer(() => {
      $("#productos").val(JSON.stringify(productos));
      $("#cantidades").val(JSON.stringify(cantidades));
    });
  }

  // ✅ Modal refs (para total en vivo)
  const $modal      = $("#myModal");
  const $modalTotal = $("#modal-total");

  function setTotal(v) {
    const safe = Math.max(0, Number(v) || 0);
    runningTotal = safe;
    $totalEl.text(money(safe));

    // ✅ si el modal está abierto, mantener el total en vivo
    if ($modal && $modal.length && $modal.is(":visible") && $modalTotal && $modalTotal.length) {
      $modalTotal.text(money(safe));
    }

    syncHiddenFields();
  }
  function addToTotal(delta) {
    setTotal((Number(runningTotal) || 0) + (Number(delta) || 0));
  }

  function computeDOMTotal() {
    let sum = 0;
    $tbody.find("tr").each(function(){
      const $r = $(this);
      const counted = !!$r.data("counted");
      const price = Number($r.data("price")) || 0;
      const qty   = Number($r.attr("data-qty")) || Number($r.find(".qty-input").val()) || 1;
      if (counted && price > 0 && qty > 0) sum += price * qty;
    });
    return sum;
  }
  function enforceTotalIntegrity() {
    const dom = computeDOMTotal();
    if (!Number.isFinite(dom)) return;
    if (Math.abs(dom - (runningTotal||0)) > 0.0001) setTotal(dom);
  }

  function throttle(fn, ms=60){
    let t=0, lastArgs=null, lastThis=null, timer=null;
    return function(...args){
      const ts=Date.now(); lastArgs=args; lastThis=this;
      const run=()=>{ timer=null; t=ts; fn.apply(lastThis,lastArgs); };
      if (!t || ts-t>=ms){ run(); } else { if (!timer) timer=setTimeout(run, ms-(ts-t)); }
    };
  }
  const enforceTotalIntegritySoft = throttle(enforceTotalIntegrity, 150);

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
    $("#myModal").hide();
    lastAddedPid = null;
  }

  window.addEventListener("pageshow", (e) => {
    if (e.persisted) clearCartAndTotals();
    else {
      if ($tbody.find("tr").length === 0) setTotal(0);
      else enforceTotalIntegritySoft();
    }
  });

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
  const barcodeIndex = new Map(); // barcode -> pid
  const nameIndex    = new Map(); // name(lc) -> pid

  function updateCache(pid, data = {}) {
    const key = String(pid);
    const prev = productCache.get(key) || {};

    if (prev.barcode) {
      const oldB = String(prev.barcode);
      if (barcodeIndex.get(oldB) === key) barcodeIndex.delete(oldB);
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

    if (rec.barcode) barcodeIndex.set(String(rec.barcode), key);
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

  /* ================== Ranking local ================== */
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

  // ✅ IMPORTANTE: durante repricing del modal NO recalculamos total 20 veces
  let repricingMode = false;

  function refreshRowPriceIfNeeded($row) {
    const pid = String($row.data("pid") || "");
    return ensureLivePrice(pid).then((live) => {
      if (!Number.isFinite(live) || live <= 0) return false;

      const old = Number($row.data("price")) || 0;
      const qty = Number($row.attr("data-qty")) || Number($row.find(".qty-input").val()) || 1;

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
         <td><input type="number" class="qty-input" min="1" inputmode="numeric" pattern="\\d*" value="${qty}" /></td>
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

  function insertOrUpdateRowInstant(pid, qty, name, cachedPrice) {
    const key = String(pid);
    const idx = productos.indexOf(key);
    const hasPrice = Number.isFinite(cachedPrice) && cachedPrice > 0;

    if (idx > -1) {
      cantidades[idx] += qty;

      const $r = $tbody.find(`tr[data-pid='${pid}']`);
      const newQty = cantidades[idx];
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
  function addToCartGuarded(pid, qty = 1) {
    const ts = now();
    if (String(lastAddGuard.pid) === String(pid) && (ts - lastAddGuard.ts) < 250) return;
    lastAddGuard.pid = String(pid);
    lastAddGuard.ts  = ts;
    addToCart(pid, qty);
  }
  const burstAdd = { timer: null, last: null, windowMs: 60 };
  function addToCartLastOnly(pid, qty = 1) {
    if (!pid || !qty || qty < 1) return;
    burstAdd.last = { pid: String(pid), qty: Number(qty) || 1 };
    if (burstAdd.timer) clearTimeout(burstAdd.timer);
    burstAdd.timer = setTimeout(() => {
      burstAdd.timer = null;
      const { pid: p, qty: q } = burstAdd.last || {};
      addToCartGuarded(p, q);
    }, burstAdd.windowMs);
  }

  function addToCart(pid, qty = 1) {
    if (!pid || qty < 1) return;

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

    queueMicrotask(() => { if ($inpCode.is(":visible")) { $inpCode.focus(); $inpCode[0]?.select?.(); } });
  }

  /* ================== Resolutores rápidos ================== */
  function resolveByBarcode(code) {
    if (!code) return Promise.resolve(null);
    const cachedPid = barcodeIndex.get(code);
    if (cachedPid) {
      setProductFields({ nombre: productCache.get(String(cachedPid))?.nombre, pid: cachedPid, barcode: code });
      return Promise.resolve(cachedPid);
    }
    const params = { codigo_de_barras: code, sucursal_id: sucursalID, _ts: Date.now() };
    return $.getJSON(POR_COD_URL, params)
      .then((r) => {
        if (!r || !r.exists) return null;
        const p = r.producto || {};
        updateCache(p.id, { nombre:p.nombre, barcode:p.codigo_de_barras, precio_unitario:p.precio, cantidad_disponible:p.stock });
        setProductFields({ nombre: p.nombre, pid: p.id, barcode: p.codigo_de_barras });
        return p.id;
      })
      .catch(() => null);
  }

  function setProductFields({ nombre, pid, barcode }) {
    if (nombre != null)  $inpNombre.val(onlyName(nombre));
    if (pid != null) {
      $pid.val(pid);
      if ($inpId && $inpId.length) $inpId.val(String(pid));
    }
    if (barcode != null) $inpCode.val(barcode);

    if ($pid.val()) {
      if ($cantidad && $cantidad.length) $cantidad.prop("disabled", false);
      if ($agregar && $agregar.length)  $agregar.prop("disabled", false);
      queueMicrotask(()=>{ if ($cantidad && $cantidad.length && $cantidad.is(":visible")) { $cantidad.focus().select(); } });
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

  function createAC({ $inp, sourceFn, onSelect, openIfEmpty=false, enableInstantSearch=true, minChars=1 }) {
    attachAltEnterBypass($inp[0]);
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
#myModal[data-loading-prices="1"] #confirmar-pago{opacity:.7;pointer-events:none}`;
    const id = "ac-price-style";
    if (!document.getElementById(id)) {
      const tag = document.createElement("style");
      tag.id = id; tag.textContent = css; document.head.appendChild(tag);
    }
  })();

  /* ============ Búsquedas ultra-rápidas (red) ============ */
  const netSearchName = throttle(async (term, signal) => {
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

  const netSearchCode = throttle(async (term, signal) => {
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

  const netSearchId = throttle(async (term, signal) => {
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

  function maybeAutoPickBarcode(term, items){
    const info = classifyQuery(term);
    if (!info.isBarcodeLike || !hasSucursal() || !Array.isArray(items) || items.length !== 1) return;
    const ts = Date.now();
    if (ts - autoPickGuardTS < 250) return;
    autoPickGuardTS = ts;

    const item = items[0];
    updateCache(item.id, { nombre:item.name, barcode:item.barcode, precio_unitario:item.price, cantidad_disponible:item.stock });
    setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode || item.label });
    bumpPick(item.id);
    try { $inpCode.autocomplete("close"); } catch {}
    try { $inpNombre.autocomplete("close"); } catch {}
    addToCartLastOnly(item.id, 1);
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

  function sourceSmartFactory({ cacheLRU, labelMode }) {
    return function(req, resp){
      (async ()=>{
        const term = (req.term||"").trim();
        const qU = normalizeUnits(term);
        if (!qU || !hasSucursal()) { resp([]); return; }

        const info = classifyQuery(term);
        const cacheKey = `${sucursalID}|smart|${labelMode}|${qU}|${info.digits}`;
        const cached = cacheLRU.get(cacheKey);
        if (cached) { resp(cached); maybeAutoPickBarcode(term, cached); return; }

        const idx = preIndex.get(sucursalID);
        let locals = [];
        if (idx) {
          const rawLocal = buildLocalSmart(term, idx, 40);
          locals = toACItems(rawLocal, { labelMode });
          for (const it of locals) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });
        }

        resp(locals);
        cacheLRU.set(cacheKey, locals);
        maybeAutoPickBarcode(term, locals);

        try {
          const useCode = info.isBarcodeLike;
          const controllerKey = (labelMode === "code") ? "code" : "name";

          if (controllerKey === "name") { inflightNameAC?.abort?.(); inflightNameAC = new AbortController(); }
          else { inflightCodeAC?.abort?.(); inflightCodeAC = new AbortController(); }

          const signal = (controllerKey === "name") ? inflightNameAC.signal : inflightCodeAC.signal;
          const netRaw = useCode ? await netSearchCode(term, signal) : await netSearchName(term, signal);
          if (!Array.isArray(netRaw) || !netRaw.length) return;

          const netItems = toACItems(netRaw, { labelMode });
          for (const it of netItems) updateCache(it.id, { nombre:it.name, barcode:it.barcode, precio_unitario:it.price, cantidad_disponible:it.stock });

          const seen = new Set(locals.map(x=>String(x.id)+"::"+(x.barcode||"")));
          const merged = locals.slice();
          for (const it of netItems) {
            const k = String(it.id)+"::"+(it.barcode||"");
            if (!seen.has(k)) merged.push(it);
            if (merged.length >= 40) break;
          }

          cacheLRU.set(cacheKey, merged);

          const current = (labelMode === "code")
            ? normalizeUnits(String($inpCode.val()||""))
            : normalizeUnits(String($inpNombre.val()||""));

          if (current === qU) {
            resp(merged);
            maybeAutoPickBarcode(term, merged);
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
    onSelect: (item) => {
      updateCache(item.id, { nombre:item.name, barcode:item.barcode, precio_unitario:item.price, cantidad_disponible:item.stock });
      setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode || item.label });
      bumpPick(item.id);
      addToCartLastOnly(item.id, 1);
    }
  });
  applyPriceTemplate($inpNombre, { mode: "name" });

  createAC({
    $inp: $inpCode,
    minChars: 1,
    openIfEmpty: false,
    sourceFn: sourceSmartFactory({ cacheLRU: termCacheCode, labelMode: "code" }),
    onSelect: (item) => {
      updateCache(item.id, { nombre:item.name, barcode:item.barcode, precio_unitario:item.price, cantidad_disponible:item.stock });
      setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode || item.label });
      bumpPick(item.id);
      addToCartLastOnly(item.id, 1);
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
      onSelect: (item) => {
        updateCache(item.id, { nombre:item.name, barcode:item.barcode, precio_unitario:item.price, cantidad_disponible:item.stock });
        setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode });
        bumpPick(item.id);
        addToCartLastOnly(item.id, 1);
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
  let catalogPollTimer = null;
  let catalogPollSid = null;

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
      try {
        const w = $inpNombre.autocomplete("widget");
        if (w && w.is(":visible")) $inpNombre.autocomplete("search", $inpNombre.val() || "");
      } catch (_){}
      try {
        const w = $inpCode.autocomplete("widget");
        if (w && w.is(":visible")) $inpCode.autocomplete("search", $inpCode.val() || "");
      } catch (_){}
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

  function stopCatalogPolling() {
    if (catalogPollTimer) clearInterval(catalogPollTimer);
    catalogPollTimer = null;
    catalogPollSid = null;
  }

  function startCatalogPolling(sid, { intervalMs = 2500 } = {}) {
    stopCatalogPolling();
    catalogPollSid = String(sid || "");
    if (!catalogPollSid) return;

    async function tick() {
      if (!hasSucursal()) return;
      if (String(sucursalID) !== String(catalogPollSid)) return;
      if (document.visibilityState !== "visible") return;

      const items = await fetchSnapshotNoStore(catalogPollSid);
      if (!items) return;

      applySnapshotIfChanged(catalogPollSid, items);
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

  /* ================== AC Sucursal / Punto (FIX: req.term) ================== */
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

      termCacheName.set(`${sucursalID}|warm|name`, []);
      termCacheCode.set(`${sucursalID}|warm|code`, []);
      termCacheId.set(`${sucursalID}|warm|id`, []);
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

  /* ================== Cliente (FIX: req.term) ================== */
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
      if (recPid) setProductFields({ nombre:nm, pid:recPid });
    } else { try { $inpNombre.autocomplete("close"); } catch {} }
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
  function sanitizeDigitsKeepEmpty(el){
    const raw = String(el.value || "");
    const digits = raw.replace(/\D+/g, "");
    el.value = digits;
    return digits;
  }
  function normalizeQtyOnCommit(el){
    const raw = String(el.value || "").trim();
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || n < 1) el.value = "1";
    return el.value;
  }

  if ($cantidad && $cantidad.length) {
    $cantidad
      .on("keydown", function(e){
        const ok = ["Backspace","Delete","ArrowLeft","ArrowRight","Tab","Home","End"].includes(e.key);
        if (ok) return;
        if (e.ctrlKey || e.metaKey) return;
        if (!/^\d$/.test(e.key)) e.preventDefault();
      })
      .on("input", function(){ sanitizeDigitsKeepEmpty(this); })
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

    const oldQty = clampQty($row.attr("data-qty") || $row.find(".qty-input").val() || 1);
    const wasCounted = !!$row.data("counted");

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
    const digits = raw.replace(/\D+/g, "");
    el.value = digits;
    return digits;
  }
  function commitRowQtyInput(el){
    const raw = String(el.value || "").trim();
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || n < 1) { el.value = "1"; return 1; }
    el.value = String(n);
    return n;
  }

  $tbody.on("input change", ".qty-input", function () {
    const $row = $(this).closest("tr");
    const digits = sanitizeRowQtyInput(this);
    if (!digits) return;
    const n = parseInt(digits, 10);
    if (!Number.isFinite(n) || n < 1) return;
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
    const ok = ["Backspace","Delete","ArrowLeft","ArrowRight","Tab","Home","End","Enter"].includes(e.key);
    if (!ok && !e.ctrlKey && !e.metaKey && !/^\d$/.test(e.key)) e.preventDefault();

    if (e.key === "Enter") {
      e.preventDefault();
      const $row = $(this).closest("tr");
      const n = commitRowQtyInput(this);
      applyQtyInstant($row, n);
      scheduleVerifyRowPrice($row, 0);

      this.blur();
      queueMicrotask(() => {
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
      const qty = clampQty($cantidad.val());
      $cantidad.val(String(qty));
      if (!pid || !qty || qty < 1) return;
      addToCartLastOnly(pid, qty);
    });
  }

  if ($cantidad && $cantidad.length) {
    $cantidad.off("keydown.confirm").on("keydown.confirm", function (e) {
      if (e.key === "Enter" && $agregar && $agregar.length && !$agregar.prop("disabled")) {
        e.preventDefault();

        const committed = normalizeQtyOnCommit(this);
        const qty = clampQty(committed);

        const pid = $pid.val();
        if (!pid || !qty || qty < 1) return;

        addToCartLastOnly(pid, qty);

        this.blur();
        queueMicrotask(() => {
          if ($inpNombre.is(":visible")) { $inpNombre.focus(); $inpNombre[0]?.select?.(); }
          const v = $inpNombre.val() || "";
          if (v.length >= 1) { try { $inpNombre.autocomplete("search", v); } catch {} }
        });
      }
    });
  }

  $tbody.on("click", ".eliminar-producto", function () {
    const $row = $(this).closest("tr");
    const pid = $row.data("pid").toString();
    const idx = productos.indexOf(pid);
    const price = Number($row.data("price")) || 0;
    const qty = clampQty($row.attr("data-qty") || $row.find(".qty-input").val());

    if ($row.data("counted")) addToTotal(-(price * qty));
    if (idx > -1) { productos.splice(idx, 1); cantidades.splice(idx, 1); }
    $row.remove();
    enforceTotalIntegritySoft();
    refreshLastAddedPidAfterRemoval(pid);
  });

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
    const t = $(this).val().toLowerCase();
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

    // normaliza qty (barato) y marca "no contado" (evita sumar 20 veces)
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

    // total final UNA sola vez
    let newTotal = 0;
    for (const tr of rows) {
      const $row = $(tr);
      const counted = !!$row.data("counted");
      if (!counted) continue;

      const price = Number($row.data("price")) || 0;
      const qty   = Number($row.attr("data-qty")) || 1;
      if (price > 0 && qty > 0) newTotal += price * qty;
    }

    setTotal(newTotal);
    return true;
  }

  /* ================== ✅ Modal de pago (MIXTO / NO-MIXTO) + RESTANTE ================== */
  const $efOptions  = $("#efectivo-options");
  const $amountIn   = $("#monto-recibido");
  const $changeOut  = $("#cambio");
  const $mixMode    = $("#mix-mode");
  const $pendingOut = $("#monto-pendiente");

  // ✅ CAMBIO CLAVE: NO recalcular precios al abrir modal
  const REPRICE_ON_MODAL = false;

  function isMixtoUI(){
    return !!($mixMode && $mixMode.length && $mixMode.prop("checked"));
  }

  function openModal(){
    $modal.addClass("is-open").show();
    $("body").addClass("modal-open");
  }
  function closeModal(){
    $modal.removeClass("is-open").hide();
    $("body").removeClass("modal-open");
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
    const medios = getCheckedMedios();
    if (!medios.length) return { error: "Seleccione al menos un medio de pago." };

    const total = safeNumber(runningTotal);
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

  /* ================== ✅ MODAL INSTANT (sin recalcular precios) ================== */
  $("#generar-venta").off("click").on("click", () => {
    if (!productos.length) { alert("Agregue productos."); return; }
    if (!hasSucursal() || !$("#puntopago_id").val()) { alert("Seleccione sucursal y punto de pago."); return; }

    ensureMixUIExists();
    showMixError("");

    // ✅ total coherente (BARATO, sin tocar precios de red)
    enforceTotalIntegrity();

    // UI instantánea
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

    // por defecto: efectivo
    $modal.find(".pm-check[value='efectivo']").prop("checked", true);

    applyModeRules();
    openModal();

    // ✅ CONFIRM INSTANT
    const $btnConfirm = $("#confirmar-pago");
    $modal.attr("data-loading-prices","0");
    $btnConfirm.prop("disabled", false);

    queueMicrotask(() => {
      if (!isMixtoUI() && $amountIn.is(":visible")) { $amountIn.focus(); $amountIn[0]?.select?.(); }
    });

    // (Opcional) si algún día quieres volver a recalcular al abrir:
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
  $(window).on("click", (e) => { if (e.target === $modal[0]) closeModal(); });

  $(document).on("keydown", function (e) {
    if (!$modal.is(":visible")) return;

    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      closeModal();
      return;
    }

    if (e.key === "Enter" && !e.altKey && !e.ctrlKey && !e.metaKey) {
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

    try { $(this).closest(".modal-content").attr("tabindex","-1").focus(); } catch (_){}
  });

  $amountIn.on("input", function () {
    refreshEfectivoUI();
  });

  $amountIn.on("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      triggerConfirmPago();
    }
  });

  $(document).on("keydown", function (e) {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;

    if ($("#myModal").is(":visible")) {
      const d = getDigitFromAltEvent(e);
      if (d !== null) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();

        if (d === 6) {
          if ($mixMode && $mixMode.length) {
            $mixMode.prop("checked", !$mixMode.prop("checked")).trigger("change");
            try { $modal.find(".modal-content").attr("tabindex", "-1").focus(); } catch (_){}
          }
          return;
        }

        // alt + 1..9 selecciona medio, alt+0 el 10 (si existe)
        const $checks = $modal.find(".pm-check").filter(":enabled").not("#mix-mode");
        const idx0 = (d === 0) ? 9 : (d - 1);
        const $target = $checks.eq(idx0);
        if ($target.length) $target.prop("checked", !$target.prop("checked")).trigger("change");
        return;
      }

      if ((e.originalEvent?.key === "Enter") || e.key === "Enter") {
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
  let confirmSubmitting = false;
  $(document).off("click.confirmPago").on("click.confirmPago", "#confirmar-pago", function (e) {
    e.preventDefault();
    if (confirmSubmitting) return;

    showMixError("");

    // ✅ solo bloquea si estuvieras usando repricing en modal
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

    // submit ultra rápido
    queueMicrotask(() => $("#venta-form").trigger("submit"));
  });

  /* ================== Agente local helpers ================== */
  async function agentPrintSafe(text, { timeout = 700 } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(POS_AGENT_URL + "/print", {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json", "X-Pos-Agent-Token": POS_AGENT_TOKEN },
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
        headers: { "X-Pos-Agent-Token": POS_AGENT_TOKEN },
        signal: ctrl.signal
      });
    } catch (_) {}
    finally { clearTimeout(t); }
  }

  // ✅ warmup del agente: baja latencia del primer /print y /kick
  (function agentWarmup(){
    if (!POS_AGENT_TOKEN) return;
    fetch(POS_AGENT_URL + "/ping", {
      method: "GET",
      keepalive: true,
      headers: { "X-Pos-Agent-Token": POS_AGENT_TOKEN }
    }).catch(()=>{});
  })();

  // ✅ espera “un poquito” por el agente, pero no bloquea UX
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

  // ✅ helpers pagos (NO depender del checkbox del modal)
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

    // refresca precios pendientes, pero sin bloquear
    const $bad = $tbody.find("tr").filter((_, tr) => {
      const p = Number($(tr).data("price"));
      const counted = $(tr).data("counted");
      return !counted || !Number.isFinite(p) || p <= 0;
    });
    if ($bad.length) for (const tr of $bad.toArray()) scheduleVerifyRowPrice($(tr), 0);

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
        if ($submitBtn.length) $submitBtn.prop("disabled", false);
        alert((r && r.error) || "Error");
        return;
      }

      const pagos = readPagosFromHidden();
      const totalNum = safeNumber(runningTotal);

      // ✅ cambio correcto SOLO si es efectivo y NO mixto
      let cambio = 0;
      const esMixto = isMixtoFromPagos(pagos);
      const ef = (pagos || []).find(p => String(p.medio_pago || "").toLowerCase() === "efectivo");
      if (ef && !esMixto) {
        const raw = ($amountIn.val() || "").trim();
        const recibido = raw === "" ? totalNum : parseAmt(raw);
        cambio = Math.max(0, recibido - totalNum);
      }

      // limpia UI/estado ya (instantáneo)
      clearCartAndTotals();

      // ✅ caja/impresión lo más rápido posible:
      // - kick + print en paralelo
      // - espera muy poco (deadline) para no frenar UX
      try {
        const receiptText = (r.receipt_text || "Factura\n\n");
        const p1 = agentKickSafe({ timeout: 450 });
        const p2 = agentPrintSafe(receiptText, { timeout: 850 });
        await settleWithDeadline([p1, p2], 250);
      } catch (_) {}

      alert(`✅ Venta registrada\n\nTotal: ${money(totalNum)}\nCambio: ${money(cambio)}`);

      // recarga rápida, pero no tan inmediata para evitar cortar el keepalive
      setTimeout(() => { location.replace(location.href); }, 90);
    })
    .catch(() => {
      saleSubmitting = false;
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
    const price = Number($first.data("price")) || 0;
    const qty = clampQty($first.attr("data-qty") || $first.find(".qty-input").val());
    if ($first.data("counted")) addToTotal(-(price * qty));
    const idx = productos.indexOf(pid);
    if (idx > -1) { productos.splice(idx, 1); cantidades.splice(idx, 1); }
    $first.remove();
    enforceTotalIntegritySoft();
    refreshLastAddedPidAfterRemoval(pid);
  });

  /* ================== ✅ SCANNER GUARD: qty-guard => code ================== */
  function isQtyElement(el){
    if (!el) return false;
    return ($cantidad && $cantidad.length && el === $cantidad[0]) || (el.classList && el.classList.contains("qty-input"));
  }

  function pushCodeIntoCodeInputAndAdd(code){
    const clean = onlyDigits(code);
    if (!clean) return;

    $inpCode.val(clean);
    try { $inpCode.autocomplete("close"); } catch (_){}
    try { $inpNombre.autocomplete("close"); } catch (_){}
    try { if ($inpId && $inpId.length) $inpId.autocomplete("close"); } catch (_){}

    queueMicrotask(() => {
      if ($inpCode.is(":visible")) { $inpCode.focus(); $inpCode[0]?.select?.(); }
      try { $inpCode.autocomplete("search", clean); } catch (_){}
    });

    if (!hasSucursal()) return;
    resolveByBarcode(clean).then(pid => { if (pid) addToCartLastOnly(pid, 1); });
  }

  function commitCurrentQtyLikeEnterIfNeeded(originEl){
    if ($cantidad && $cantidad.length && originEl === $cantidad[0]) {
      const committed = normalizeQtyOnCommit($cantidad[0]);
      const qty = clampQty(committed);
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

    let scanning = false;
    let originEl = null;
    let originStartValue = "";

    function resetAll(){
      buf = "";
      first = 0;
      last = 0;
      scanning = false;
      originEl = null;
      originStartValue = "";
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    }

    function finalize(code){
      const c = String(code || "");
      const wasQty = isQtyElement(originEl);

      if (wasQty) {
        try { if (originEl) originEl.value = originStartValue; } catch (_){}
        commitCurrentQtyLikeEnterIfNeeded(originEl);
      }

      pushCodeIntoCodeInputAndAdd(c);
      resetAll();
    }

    document.addEventListener("keydown", function (e) {
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
        if (originEl && active !== originEl) resetAll();

        if (!buf) {
          originEl = active;
          originStartValue = (active && typeof active.value === "string") ? active.value : "";
          first = t;
          last = t;
          buf = e.key;
        } else {
          if ((t - last) > GAP_MS) {
            resetAll();
            originEl = active;
            originStartValue = (active && typeof active.value === "string") ? active.value : "";
            first = t; last = t;
            buf = e.key;
          } else {
            buf += e.key;
            last = t;
          }
        }

        if (!scanning && inQty && buf.length >= 2) {
          scanning = true;
          try { if (active && typeof active.value === "string") active.value = originStartValue; } catch (_){}
        }

        if (scanning && inQty) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => resetAll(), GAP_MS * 6);

        if (inQty && scanning && buf.length >= MIN_CHARS) {
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
