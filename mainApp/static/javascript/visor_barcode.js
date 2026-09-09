// static/javascript/visor_barcode.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  const $inp   = $("#vb_barcode");  // input "Escanea aquí"
  const $cam   = $("#vb_camera");
  const $clear = $("#vb_clear");
  const $err   = $("#vb-error");

  const $name  = $("#vb_name");
  const $price = $("#vb_price");
  const $old   = $("#vb_old");
  const $pid   = $("#vb_pid");
  const $bar   = $("#vb_bar");
  const $disp  = $("#vb_display");
  const $quantity = $("#vb_quantity");
  const $total = $("#vb_total");
  const $search = $("#vb_search");
  let currentProduct = null;
  let lookupRevision = 0;

  // ====== cache (barcode -> product) ======
  const cache = new Map();

  // ====== abort controllers ======
  let lookupAbort = null;
  let searchAbort = null;

  // ====== state ======
  let pendingPick = null; // { value }
  let lastPaintedBarcode = "";
  let cameraStream = null;
  let cameraRunning = false;
  let cameraDetector = null;
  let cameraFallbackTimer = 0;
  let zxingReader = null;
  let zxingLoaded = false;

  /* ================= Utils ================= */
  function showErr(msg){ $err.text(msg).show(); }
  function hideErr(){ $err.hide().text(""); }

  function moneyCOP(v){
    const n = Number(String(v).replace(",", "."));
    const safe = Number.isFinite(n) ? n : 0;
    return safe.toLocaleString("es-CO", {
      style:"currency", currency:"COP", maximumFractionDigits: 2
    });
  }

  function setLoading(on){
    $disp.toggleClass("is-loading", !!on);
  }

  function pop(){
    $disp.removeClass("pop");
    void $disp[0].offsetWidth;
    $disp.addClass("pop");
  }

  function sanitizeBarcode(s){
    return String(s || "").trim();
  }

  function isSecureContextForCamera(){
    const host = location.hostname;
    return window.isSecureContext || location.protocol === "https:" || host === "localhost" || host === "127.0.0.1";
  }

  function hasCameraApi(){
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function isIOSLike(){
    const ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/i.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function barcodeTextFromResult(result){
    if (!result) return "";
    if (typeof result.getText === "function") return sanitizeBarcode(result.getText());
    return sanitizeBarcode(result.text || result.rawValue || "");
  }

  function waitForVideoReady(video){
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

  function ensureCameraOverlay(){
    if (document.getElementById("vb_camera_overlay")) return;

    const overlay = document.createElement("div");
    overlay.id = "vb_camera_overlay";
    overlay.className = "vb-camera-overlay";
    overlay.innerHTML = `
      <div class="vb-camera-panel">
        <div class="vb-camera-topbar">
          <span><i class="fas fa-camera"></i> Escaner con camara</span>
          <button id="vb_camera_close" type="button" class="vb-camera-close" aria-label="Cerrar camara">
            <i class="fas fa-xmark"></i>
          </button>
        </div>
        <video id="vb_camera_video" playsinline webkit-playsinline muted autoplay x-webkit-airplay="deny"></video>
        <div id="vb_camera_hint" class="vb-camera-hint">Apunta al codigo de barras...</div>
      </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById("vb_camera_close")?.addEventListener("click", stopCameraScanner);
  }

  function setCameraButtonState(on){
    $cam.prop("disabled", !!on).attr("aria-disabled", on ? "true" : "false").toggleClass("is-active", !!on);
  }

  function acceptCameraCode(raw){
    const text = sanitizeBarcode(raw);
    if (!text || !cameraRunning) return false;
    stopCameraScanner();
    handleScanNow(text);
    return true;
  }

  async function loadZXing(){
    if (zxingLoaded || window.ZXing?.BrowserMultiFormatReader) {
      zxingLoaded = true;
      return true;
    }

    return new Promise((resolve) => {
      const existing = document.getElementById("zxing-cdn");
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

        existing.addEventListener("load", () => finish(!!window.ZXing?.BrowserMultiFormatReader), { once: true });
        existing.addEventListener("error", () => finish(false), { once: true });
        return;
      }

      const script = document.createElement("script");
      script.id = "zxing-cdn";
      script.src = "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js";
      script.async = true;
      script.onload = () => {
        zxingLoaded = !!window.ZXing?.BrowserMultiFormatReader;
        resolve(zxingLoaded);
      };
      script.onerror = () => {
        zxingLoaded = false;
        resolve(false);
      };
      document.head.appendChild(script);
    });
  }

  async function getZXingReader(){
    if (zxingReader) return zxingReader;
    const ok = await loadZXing();
    if (!ok || !window.ZXing?.BrowserMultiFormatReader) return null;

    try {
      zxingReader = new window.ZXing.BrowserMultiFormatReader();
      return zxingReader;
    } catch {
      return null;
    }
  }

  async function getBarcodeDetector(){
    if (cameraDetector) return cameraDetector;
    if (!("BarcodeDetector" in window)) return null;

    try {
      const formats = await window.BarcodeDetector.getSupportedFormats?.();
      const wanted = ["ean_13","ean_8","code_128","code_39","upc_a","upc_e","itf","codabar","qr_code"];
      const use = Array.isArray(formats) && formats.length ? formats.filter(f => wanted.includes(f)) : wanted;
      if (!use.length) return null;
      cameraDetector = new window.BarcodeDetector({ formats: use });
      return cameraDetector;
    } catch {
      return null;
    }
  }

  async function openCameraStream(){
    const attempts = [
      {
        audio: false,
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 }
        }
      },
      {
        audio: false,
        video: {
          facingMode: "environment",
          width: { ideal: 1280 },
          height: { ideal: 720 }
        }
      },
      { audio: false, video: true }
    ];

    let lastError = null;
    for (const constraints of attempts) {
      try {
        cameraStream = await navigator.mediaDevices.getUserMedia(constraints);
        return cameraStream;
      } catch (err) {
        lastError = err;
      }
    }

    throw lastError || new Error("No se pudo abrir la camara.");
  }

  async function startWithBarcodeDetector(video){
    if (isIOSLike()) return false;

    const detector = await getBarcodeDetector();
    if (!detector) return false;

    const tick = async () => {
      if (!cameraRunning) return;
      try {
        const codes = await detector.detect(video);
        if (codes && codes.length && acceptCameraCode(codes[0]?.rawValue || "")) return;
      } catch {}
      requestAnimationFrame(tick);
    };

    requestAnimationFrame(tick);
    return true;
  }

  async function startWithZXing(video){
    const reader = await getZXingReader();
    if (!reader) return false;

    const hint = document.getElementById("vb_camera_hint");
    if (hint) hint.textContent = "Apunta al codigo de barras. En iPhone puede tardar unos segundos...";

    if (typeof reader.decodeFromVideoElementContinuously === "function") {
      try {
        await Promise.resolve(reader.decodeFromVideoElementContinuously(video, (result) => {
          if (!cameraRunning || !result) return;
          acceptCameraCode(barcodeTextFromResult(result));
        }));
        return true;
      } catch {}
    }

    const loop = async () => {
      if (!cameraRunning) return;
      try {
        const result = await reader.decodeOnceFromVideoElement(video);
        if (acceptCameraCode(barcodeTextFromResult(result))) return;
      } catch {}
      requestAnimationFrame(loop);
    };

    requestAnimationFrame(loop);
    return true;
  }

  async function startCameraScanner(){
    if (!$cam.length || cameraRunning) return;

    hideErr();

    if (!isSecureContextForCamera()) {
      showErr("La camara del celular necesita HTTPS. En iPhone abre esta pagina desde Safari con HTTPS.");
      return;
    }

    if (!hasCameraApi()) {
      showErr("Este navegador no permite abrir la camara.");
      return;
    }

    ensureCameraOverlay();
    const overlay = document.getElementById("vb_camera_overlay");
    const video = document.getElementById("vb_camera_video");
    const hint = document.getElementById("vb_camera_hint");

    cameraRunning = true;
    setCameraButtonState(true);
    overlay.style.display = "flex";
    if (hint) hint.textContent = "Solicitando permiso de camara...";

    try {
      await openCameraStream();
    } catch {
      stopCameraScanner();
      showErr("No se pudo abrir la camara. En iPhone usa Safari/HTTPS y permite el acceso a Camara.");
      return;
    }

    video.srcObject = cameraStream;
    try { await video.play(); } catch {}
    await waitForVideoReady(video);

    if (isIOSLike()) {
      const okIOS = await startWithZXing(video);
      if (okIOS) return;
    }

    const okDetector = await startWithBarcodeDetector(video);
    if (okDetector) {
      cameraFallbackTimer = setTimeout(() => {
        if (!cameraRunning) return;
        startWithZXing(video).catch(() => {});
      }, 1400);
      return;
    }

    const okZXing = await startWithZXing(video);
    if (okZXing) return;

    stopCameraScanner();
    showErr("No se pudo iniciar el lector con camara en este dispositivo.");
  }

  function stopCameraScanner(){
    cameraRunning = false;
    setCameraButtonState(false);

    if (cameraFallbackTimer) {
      clearTimeout(cameraFallbackTimer);
      cameraFallbackTimer = 0;
    }

    const overlay = document.getElementById("vb_camera_overlay");
    if (overlay) overlay.style.display = "none";

    try {
      const video = document.getElementById("vb_camera_video");
      if (video) {
        try { video.pause(); } catch {}
        video.srcObject = null;
      }
    } catch {}

    if (cameraStream) {
      try { cameraStream.getTracks().forEach(track => track.stop()); } catch {}
      cameraStream = null;
    }

    try { zxingReader?.reset?.(); } catch {}
    forceFocus();
  }

  function paintEmpty(){
    lookupRevision++;
    currentProduct = null;
    $quantity.val("1").prop("disabled", true);
    $total.text("—");
    lastPaintedBarcode = "";
    $disp.removeClass("has-product");
    $name.text("—");
    $price.text("$0");
    $old.hide().text("");
    $pid.text("ID: —");
    $bar.text("Barras: —");
  }

  function paintProduct(p){
    if (!p) return;

    const bc = String(p.codigo_de_barras || "").trim();
    lookupRevision++;
    currentProduct = p;
    $quantity.val("1").prop("disabled", false);
    updateQuantityTotal();
    lastPaintedBarcode = bc;

    hideErr();
    $disp.addClass("has-product");

    $name.text(p.nombre || "—");
    $price.text(moneyCOP(p.precio));

    const pa = String(p.precio_anterior || "").trim();
    if (pa) $old.text(`Antes: ${moneyCOP(pa)}`).show();
    else $old.hide().text("");

    $pid.text(`ID: ${p.id ?? "—"}`);
    $bar.text(`Barras: ${bc || "—"}`);

    pop();
  }

  function updateQuantityTotal(){
    if (!currentProduct) { $total.text("—"); return; }
    const quantity = String($quantity.val() || "").trim();
    const price = String(currentProduct.precio || "0").match(/^(\d+)(?:\.(\d{1,2}))?$/);
    if (!/^[1-9]\d{0,8}$/.test(quantity) || !price) {
      $total.text("Escribe una cantidad entera mayor que cero");
      return;
    }
    // Centavos enteros: evita errores de coma flotante al multiplicar gramos.
    const cents = (BigInt(price[1]) * 100n + BigInt((price[2] || "").padEnd(2, "0"))) * BigInt(quantity);
    $total.text(`$ ${(cents / 100n).toLocaleString("es-CO")},${String(cents % 100n).padStart(2, "0")}`);
  }
  $quantity.on("input", updateQuantityTotal);

  function forceFocus(){
    // Mantén foco SIEMPRE y el cursor al final
    if (document.activeElement !== $inp[0]) $inp.trigger("focus");
    try{
      const el = $inp[0];
      el.setSelectionRange(el.value.length, el.value.length);
    }catch{}
  }

  // No robar el foco: el usuario puede escribir gramos o consultar por nombre.

  /* ================= Lookup exacto ================= */
  async function lookupExact(barcode){
    const bc = sanitizeBarcode(barcode);
    if (!bc) return null;

    // Consultar de nuevo: el precio puede haber cambiado desde otro equipo.

    try { lookupAbort?.abort(); } catch {}
    lookupAbort = ("AbortController" in window) ? new AbortController() : null;

    setLoading(true);
    try{
      const url = `${VISOR_LOOKUP_URL}?barcode=${encodeURIComponent(bc)}&_ts=${Date.now()}`;
      const r = await fetch(url, { cache:"no-store", signal: lookupAbort?.signal });
      if (!r.ok) return null;

      const d = await r.json();
      if (!d || !d.success || !d.product) return null;

      cache.set(bc, d.product);
      return d.product;
    }catch{
      return null;
    }finally{
      setLoading(false);
    }
  }

  /* ================= Autocomplete fallback (por si lookup exacto no encuentra) ================= */
  function openAutocompletePickFirst(){
    const v = sanitizeBarcode($inp.val());
    if (!v) return;

    pendingPick = { value: v };
    try { $inp.autocomplete("close"); } catch {}
    $inp.autocomplete("search", v);
  }

  $inp.autocomplete({
    minLength: 1,
    delay: 0,
    autoFocus: false,
    appendTo: "body",
    source: function(req, resp){
      const term = sanitizeBarcode(req.term);
      if (!term) return resp([]);

      try { searchAbort?.abort(); } catch {}
      searchAbort = ("AbortController" in window) ? new AbortController() : null;

      fetch(`${VISOR_BARRAS_URL}?term=${encodeURIComponent(term)}&page=1`, {
        cache:"no-store",
        signal: searchAbort?.signal
      })
      .then(r => r.ok ? r.json() : {results:[]})
      .then(d => {
        const arr = (d.results || []).map(x => ({
          id: x.id,
          label: `${(x.barcode || "")} — ${x.text || ""}`,
          value: String(x.barcode || ""),
          product: {
            id: x.id,
            nombre: x.text || "",
            codigo_de_barras: x.barcode || "",
            precio: x.precio || "0",
            precio_anterior: x.precio_anterior || ""
          }
        }));
        resp(arr);
      })
      .catch(() => resp([]));
    },
    select: function(_e, ui){
      if (!ui || !ui.item) return false;

      const bc = String(ui.item.value || "").trim();
      if (bc) cache.set(bc, ui.item.product);

      // ✅ SIEMPRE reemplazar: nada de concatenar
      $inp.val(bc);
      forceFocus();

      paintProduct(ui.item.product);
      $inp[0].select();
      return false;
    }
  });

  $inp.on("autocompleteresponse", function(_e, ui){
    if (!pendingPick) return;

    const cur = sanitizeBarcode($inp.val());
    if (cur !== pendingPick.value) return;

    const list = ui?.content || [];
    if (!list.length){
      showErr(`No encontrado: ${pendingPick.value}`);
      pendingPick = null;
      return;
    }

    const exact = list.filter(it => String(it.value || "") === pendingPick.value);
    if (exact.length !== 1) {
      pendingPick = null;
      showErr("No hay una coincidencia exacta única. Selecciona el producto en la lista.");
      return;
    }
    const item = exact[0];

    pendingPick = null;
    try { $inp.autocomplete("close"); } catch {}

    const bc = String(item.value || "").trim();
    if (bc) cache.set(bc, item.product);

    // ✅ SIEMPRE reemplazar: nada de concatenar
    $inp.val(bc);
    forceFocus();

    paintProduct(item.product);
  });

  /* ================= Acción principal por scan ================= */
  async function handleScanNow(barcode){
    const bc = sanitizeBarcode(barcode);
    if (!bc) return;
    const revision = ++lookupRevision;

    hideErr();

    // ✅ SIEMPRE reemplazar el input con el nuevo código (anti-concat total)
    $inp.val(bc);
    forceFocus();

    const p = await lookupExact(bc);
    if (revision !== lookupRevision) return;
    if (p){
      const finalBc = String(p.codigo_de_barras || bc).trim();
      $inp.val(finalBc);
      forceFocus();
      paintProduct(p);
      $inp[0].select();
      return;
    }

    // fallback
    openAutocompletePickFirst();
  }

  function isPrintableChar(e){
    return e.key && e.key.length === 1;
  }

  // Si el foco está en el fondo, comenzar una nueva lectura; nunca robarlo a otro campo.
  document.addEventListener("keydown", function(e){
    // Ignorar combos
    if (e.ctrlKey || e.altKey || e.metaKey) return;

    // Los campos editables usan entrada normal, sin captura global del lector.
    if (e.target.closest?.("input, textarea, select, button, a, [contenteditable]")) return;
    if (!isPrintableChar(e)) return;
    forceFocus();

    if (isPrintableChar(e)) {
      e.preventDefault();
      $inp.val(e.key).trigger("input");
    }
  }, true);

  $inp.on("input", function(){
    pendingPick = null;
    paintEmpty();
  });
  $inp.on("keydown", function(e){
    if (e.key !== "Enter" || e.isDefaultPrevented()) return;
    e.preventDefault();
    handleScanNow($inp.val());
  });

  if ($search.length && VISOR_CAJERO_URL) {
    let searchController = null;
    $search.on("input", function(){ pendingPick = null; paintEmpty(); });
    $search.autocomplete({
      minLength: 1, delay: 180, autoFocus: false, appendTo: "body",
      source: function(req, resp){
        searchController?.abort();
        searchController = new AbortController();
        fetch(`${VISOR_CAJERO_URL}?term=${encodeURIComponent(req.term)}`, {cache: "no-store", signal: searchController.signal})
          .then(r => { if (!r.ok) throw new Error("No se pudo consultar. Revisa tu sesión e inténtalo de nuevo."); return r.json(); })
          .then(data => resp((data.results || []).map(p => ({
            label: `ID ${p.id} — ${p.text} — ${moneyCOP(p.precio)}`,
            value: p.text,
            product: {id:p.id, nombre:p.text, codigo_de_barras:p.barcode, precio:p.precio, precio_anterior:p.precio_anterior}
          }))))
          .catch(error => { resp([]); if (error.name !== "AbortError") showErr(error.message); });
      },
      select: function(_event, ui){
        pendingPick = null;
        $search.val(ui.item.value);
        $inp.val(ui.item.product.codigo_de_barras || "");
        paintProduct(ui.item.product);
        return false;
      }
    });
  }

  /* ================= Botón limpiar ================= */
  $cam.on("click", function(){
    startCameraScanner();
  });

  $clear.on("click", function(){
    hideErr();
    $inp.val("");
    $search.val("");
    paintEmpty();
    forceFocus();
  });

  window.addEventListener("beforeunload", stopCameraScanner);

  /* ================= Init ================= */
  paintEmpty();

  // Solo foco inicial; no interferir con cantidad, búsqueda ni navegación.
  setTimeout(forceFocus, 30);
});
