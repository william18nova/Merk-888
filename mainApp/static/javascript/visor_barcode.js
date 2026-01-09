// static/javascript/visor_barcode.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  const $inp   = $("#vb_barcode");  // input "Escanea aquí"
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
  $clear.on("click", function(){
    resetScanBuffer();
    hideErr();
    $inp.val("");
    paintEmpty();
    forceFocus();
  });

  /* ================= Init ================= */
  paintEmpty();

  // foco inicial + refresco periódico por si algo externo lo roba
  setTimeout(forceFocus, 30);
  setInterval(forceFocus, 900);
});
