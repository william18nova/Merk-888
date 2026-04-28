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

  // ====== keyboard-wedge scanner ======
  let scanBuf = "";
  let scanTimer = 0;
  let scanActive = false;

  // Ajustes: mientras más corto, más “scanner-only”
  const SCAN_IDLE_MS = 55;      // si pasan >55ms entre teclas, reinicia
  const MIN_SCAN_LEN = 4;       // longitud mínima para aceptar (evita ruido)

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
    if (bc && bc === lastPaintedBarcode) return;
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

  function forceFocus(){
    // Mantén foco SIEMPRE y el cursor al final
    if (document.activeElement !== $inp[0]) $inp.trigger("focus");
    try{
      const el = $inp[0];
      el.setSelectionRange(el.value.length, el.value.length);
    }catch{}
  }

  // En esta página NO hay teclado/mouse: forzamos foco ante cualquier intento de perderlo
  $(document).on("mousedown pointerdown touchstart", function(){
    // por si alguien toca/clickea: volvemos al input
    setTimeout(forceFocus, 0);
  });

  $(document).on("focusin", function(e){
    if (e.target !== $inp[0]) setTimeout(forceFocus, 0);
  });

  $inp.on("blur", function(){
    setTimeout(forceFocus, 0);
  });

  /* ================= Lookup exacto ================= */
  async function lookupExact(barcode){
    const bc = sanitizeBarcode(barcode);
    if (!bc) return null;

    if (cache.has(bc)) return cache.get(bc);

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
    autoFocus: true,
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

    const exact = list.find(it => String(it.value || "") === pendingPick.value);
    const item = exact || list[0];

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

    hideErr();

    // ✅ SIEMPRE reemplazar el input con el nuevo código (anti-concat total)
    $inp.val(bc);
    forceFocus();

    const p = await lookupExact(bc);
    if (p){
      const finalBc = String(p.codigo_de_barras || bc).trim();
      $inp.val(finalBc);
      forceFocus();
      paintProduct(p);
      return;
    }

    // fallback
    openAutocompletePickFirst();
  }

  /* =============================================================================
     ✅ SCANNER ONLY MODE:
     - siempre focus en input
     - cada escaneo reemplaza por completo (no concatena)
     - usamos un buffer interno y al Enter “commit” el código
  ============================================================================= */
  function resetScanBuffer(){
    scanBuf = "";
    scanActive = false;
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = 0;
  }

  function isPrintableChar(e){
    return e.key && e.key.length === 1;
  }

  // Capturamos global para que aunque el navegador pierda foco, igual llegue al buffer
  document.addEventListener("keydown", function(e){
    // Ignorar combos
    if (e.ctrlKey || e.altKey || e.metaKey) return;

    // Siempre volvemos foco al input (la página es solo lector)
    forceFocus();

    // Enter = fin de escaneo
    if (e.key === "Enter"){
      // Si el lector manda Enter al final (lo normal)
      if (scanActive){
        e.preventDefault();

        const code = sanitizeBarcode(scanBuf);
        resetScanBuffer();

        if (code.length >= MIN_SCAN_LEN){
          // ✅ REEMPLAZA y busca
          handleScanNow(code);
        }
        return;
      }

      // Si por alguna razón no estábamos en scanActive, igual procesamos lo que haya en input
      e.preventDefault();
      const v = sanitizeBarcode($inp.val());
      if (v) handleScanNow(v);
      return;
    }

    // Solo chars imprimibles para buffer del lector
    if (!isPrintableChar(e)) return;

    // Evitar que el navegador escriba/concatene dentro del input
    // (esto es CLAVE para que NUNCA se concatene más de un código)
    e.preventDefault();

    // Empezar burst
    if (!scanActive){
      scanActive = true;
      scanBuf = "";
    }

    scanBuf += e.key;

    // Si hay pausa, reinicia (evita concatenaciones entre scans)
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(() => {
      resetScanBuffer();
      // además limpiamos el input por seguridad
      $inp.val("");
      forceFocus();
    }, SCAN_IDLE_MS);
  }, true);

  // Por si el scanner NO manda Enter (rarísimo), hacemos commit cuando detectamos pausa
  // (si esto te estorba, lo quitamos; pero ayuda a robustez)
  function commitOnIdle(){
    const code = sanitizeBarcode(scanBuf);
    resetScanBuffer();
    if (code.length >= MIN_SCAN_LEN){
      handleScanNow(code);
    }
  }

  // Si el scanner no manda Enter, el timeout de SCAN_IDLE_MS resetea.
  // Para soportar “sin enter”, cambia el reset por commit:
  // (déjalo así si tu lector SÍ manda Enter, que es lo mejor)
  // NOTA: por defecto NO hacemos commit automático, solo reseteo.

  /* ================= Botón limpiar ================= */
  $cam.on("click", function(){
    startCameraScanner();
  });

  $clear.on("click", function(){
    resetScanBuffer();
    hideErr();
    $inp.val("");
    paintEmpty();
    forceFocus();
  });

  window.addEventListener("beforeunload", stopCameraScanner);

  /* ================= Init ================= */
  paintEmpty();

  // foco inicial + refresco periódico por si algo externo lo roba
  setTimeout(forceFocus, 30);
  setInterval(forceFocus, 900);
});
