// static/javascript/ventas_producto_rango.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  /* ================= UI refs ================= */
  const $sucursalSel    = $("#id_sucursal");
  const $sucursalHid    = $("#id_sucursalid");     // opcional
  const $sucursalNombre = $("#sucursalNombre");

  const $pidHid = $("#id_productoid");

  const $inpNom = $("#producto_busqueda_nombre");
  const $inpBar = $("#producto_busqueda_barras");
  const $inpId  = $("#producto_busqueda_id");

  const $desde = $("#id_desde");
  const $hasta = $("#id_hasta");

  const $btnConsultar = $("#btnConsultar");

  const $errBox = $("#error-message");
  const $okBox  = $("#success-message");

  const $resultCard = $("#resultCard");

  /* ================= Helpers UI ================= */
  function showErr(msg){
    $okBox.hide().text("");
    $errBox.html(`<i class="fas fa-exclamation-circle"></i> ${msg}`).show();
  }
  function showOk(msg){
    $errBox.hide().text("");
    $okBox.html(`<i class="fas fa-check-circle"></i> ${msg}`).show();
  }
  function clearMsgs(){
    $errBox.hide().text("");
    $okBox.hide().text("");
  }

  function onlyDigits(s){ return String(s || "").replace(/\D+/g, ""); }

  /* ================= Sucursal selector (FIX) ================= */
  function getSucursalId(){
    return String($sucursalSel.val() || "").trim();
  }

  function syncSucursalUI(){
    const sid = getSucursalId();
    const name = sid ? $sucursalSel.find("option:selected").text().trim() : "—";

    // Hidden solo como espejo (no es la fuente de verdad)
    if ($sucursalHid.length) $sucursalHid.val(sid);

    $sucursalNombre.text(name);

    // al cambiar sucursal, ocultamos resultados previos
    $resultCard.hide();

    // limpia mensajes (evita “Debe seleccionar sucursal” pegado)
    clearMsgs();
  }

  $sucursalSel.on("change", syncSucursalUI);
  syncSucursalUI(); // inicial

  /* ================= Datepicker (rango) ================= */
  $desde.datepicker({
    dateFormat: "yy-mm-dd",
    changeMonth: true,
    changeYear: true,
    onSelect: function(val){
      $hasta.datepicker("option", "minDate", val);
    }
  });

  $hasta.datepicker({
    dateFormat: "yy-mm-dd",
    changeMonth: true,
    changeYear: true,
    onSelect: function(val){
      $desde.datepicker("option", "maxDate", val);
    }
  });

  function pad2(n){ return String(n).padStart(2, "0"); }
  function isoDate(d){
    return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`;
  }
  function setRangeDays(days){
    const end = new Date();
    const start = new Date();
    if (days !== 0) start.setDate(start.getDate() - (days - 1));
    $desde.val(isoDate(start));
    $hasta.val(isoDate(end));
  }

  $(".quick-btn").on("click", function(){
    const q = String($(this).data("q"));
    if (q === "hoy") setRangeDays(0);
    else setRangeDays(parseInt(q, 10));
  });

  // Default: últimos 7 días
  if (!$desde.val() && !$hasta.val()) setRangeDays(7);

  /* ================= DataTable (por día) ================= */
  const dt = $("#ventasDiaTable").DataTable({
    paging: false,
    searching: false,
    info: false,
    ordering: true,
    order: [[0, "asc"]],
    language: { emptyTable: "Sin resultados en el rango seleccionado." }
  });

  function clearDaily(){
    dt.clear().draw(false);
  }

  function setSelectedProduct({id, nombre, barcode}){
    $pidHid.val(id);
    if (nombre)  $inpNom.val(nombre);
    if (barcode) $inpBar.val(barcode);
    $inpId.val(String(id));
  }

  /* ================= Autocomplete ================= */
  function makeAC($input, urlBuilder, mode){
    const cache = new Map();
    let acAbort = null;

    $input.autocomplete({
      minLength: 1,
      delay: 0,
      autoFocus: true,
      appendTo: "body",
      source: function(req, resp){
        const term = (req.term || "").trim();
        if (!term) return resp([]);

        const key = mode + "::" + term;
        if (cache.has(key)) return resp(cache.get(key));

        try { if (acAbort) acAbort.abort(); } catch {}
        acAbort = new AbortController();

        fetch(urlBuilder(term), { cache: "no-store", signal: acAbort.signal })
          .then(r => r.ok ? r.json() : { results: [] })
          .then(d => {
            const arr = (d.results || []).map(x => ({
              id: x.id,
              label: mode === "barras"
                ? `${(x.barcode || x.text || "")} — ${x.text || ""}`
                : mode === "id"
                  ? `#${x.id} — ${x.text || ""}`
                  : (x.text || ""),
              value: mode === "barras"
                ? String(x.barcode || "")
                : mode === "id"
                  ? String(x.id)
                  : String(x.text || ""),
              text: x.text || "",
              barcode: x.barcode || ""
            }));

            cache.set(key, arr);

            // limita cache
            if (cache.size > 300){
              const first = cache.keys().next().value;
              cache.delete(first);
            }

            resp(arr);
          })
          .catch(() => resp([]));
      },
      select: function(_e, ui){
        if (!ui || !ui.item) return false;
        setSelectedProduct({ id: ui.item.id, nombre: ui.item.text, barcode: ui.item.barcode });
        return false;
      }
    });
  }

  makeAC($inpNom, (term) => `${productoNombreUrl}?term=${encodeURIComponent(term)}&page=1`, "nombre");
  makeAC($inpBar, (term) => `${productoBarrasUrl}?term=${encodeURIComponent(term)}&page=1`, "barras");
  makeAC($inpId,  (term) => `${productoIdUrl}?term=${encodeURIComponent(onlyDigits(term))}&page=1`, "id");

  $inpId.on("input", function(){
    const d = onlyDigits(this.value);
    if (this.value !== d) this.value = d;
  });

  /* ================= Auto-pick barras ================= */
  let pendingPick = null;

  $inpBar.on("autocompleteresponse", function(_e, ui){
    if (!pendingPick) return;

    const curVal = String(($inpBar.val() || "").trim());
    if (curVal !== pendingPick.value) return;

    const list = (ui && Array.isArray(ui.content)) ? ui.content : [];
    pendingPick = null;

    if (!list.length) return;

    const exact = list.find(it => String(it.barcode || it.value || "") === curVal);
    const item = exact || list[0];

    try { $inpBar.autocomplete("close"); } catch {}
    setSelectedProduct({ id: item.id, nombre: item.text, barcode: item.barcode });
  });

  function openBarAutocompleteAndPickFirst(){
    const v = String(($inpBar.val() || "").trim());
    if (!v) return;
    pendingPick = { value: v };
    try { $inpBar.autocomplete("close"); } catch {}
    $inpBar.autocomplete("search", v);
  }

  /* ================= Consultar stats ================= */
  function moneyLike(s){
    return String(s ?? "0");
  }

  function paintResult(data){
    const p  = data.product || {};
    const st = data.stats   || {};
    const rg = data.range   || {};

    $("#r_nombre").text(p.nombre || "Producto");
    $("#r_id").text(p.id ?? "-");
    $("#r_bar").text(p.codigo_de_barras || "-");

    $("#k_veces").text(st.ventas_distintas ?? 0);
    $("#k_unidades").text(st.unidades ?? 0);
    $("#k_ingresos").text(moneyLike(st.ingresos));

    $("#r_desde").text(rg.desde || "-");
    $("#r_hasta").text(rg.hasta || "-");

    clearDaily();
    (data.daily || []).forEach(row => {
      dt.row.add([row.fecha, row.ventas, row.unidades]);
    });
    dt.draw(false);

    $resultCard.show();
  }

  function validate(){
    // ✅ fuente de verdad: el SELECT, no el hidden
    const sid = getSucursalId();
    if (!sid) return "Debe seleccionar una sucursal.";

    const pid = ($pidHid.val() || "").trim();
    if (!pid) return "Debe seleccionar un producto.";

    const d1 = ($desde.val() || "").trim();
    const d2 = ($hasta.val() || "").trim();
    if (!d1 || !d2) return "Debe seleccionar el rango de fechas (desde y hasta).";

    return null;
  }

  let loading = false;
  $btnConsultar.on("click", function(){
    if (loading) return;

    clearMsgs();

    const err = validate();
    if (err) return showErr(err);

    const sucursal_id = getSucursalId(); // ✅ de nuevo: del SELECT
    const pid   = ($pidHid.val() || "").trim();
    const desde = ($desde.val() || "").trim();
    const hasta = ($hasta.val() || "").trim();

    const url = statsUrl + "?" + new URLSearchParams({
      sucursal_id,
      productoid: pid,
      desde,
      hasta,
      _ts: Date.now()
    });

    loading = true;
    $btnConsultar.prop("disabled", true).text("Consultando…");

    fetch(url, { cache: "no-store" })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
      .then(data => {
        if (!data || !data.success) {
          showErr(data?.error || "No se pudo consultar.");
          return;
        }
        showOk("Consulta exitosa.");
        paintResult(data);
      })
      .catch(e => showErr(e?.error || "Error consultando ventas."))
      .finally(() => {
        loading = false;
        $btnConsultar.prop("disabled", false).text("Consultar ventas");
      });
  });

  /* =============================================================================
     📷 Scanner (igual que tu inventario)
  ============================================================================= */
  const $btnScan  = $("#btnScanBarcode");
  const $overlay  = $("#barcodeScannerOverlay");
  const $btnClose = $("#btnCloseScanner");
  const $btnTorch = $("#btnToggleTorch");
  const $status   = $("#scannerStatus");
  const videoEl   = document.getElementById("barcodeVideo");

  const isSecure = window.isSecureContext || location.hostname === "localhost";

  let stream = null;
  let running = false;
  let detector = null;
  let zxingReader = null;
  let rafId = 0;
  let lastCode = "";
  let lastAt = 0;

  function showOverlay(show){
    $overlay.css("display", show ? "flex" : "none");
    document.body.style.overflow = show ? "hidden" : "";
  }

  function stopStream(){
    running = false;

    try { if (rafId) cancelAnimationFrame(rafId); } catch {}
    rafId = 0;

    detector = null;

    try { if (zxingReader) zxingReader.reset(); } catch {}
    zxingReader = null;

    try { videoEl.pause(); } catch {}
    try { videoEl.srcObject = null; } catch {}

    if (stream){
      try { stream.getTracks().forEach(t => t.stop()); } catch {}
      stream = null;
    }

    $btnTorch.hide().data("on", 0).text("Linterna");
  }

  async function loadZXing(){
    if (window.ZXing?.BrowserMultiFormatReader) return window.ZXing;
    return await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js";
      s.async = true;
      s.onload = () => resolve(window.ZXing);
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  async function toggleTorch(){
    if (!stream) return;
    const track = stream.getVideoTracks()[0];
    const caps = track.getCapabilities?.();
    if (!caps || !caps.torch) return;

    const on = Number($btnTorch.data("on") || 0) === 1;
    try{
      await track.applyConstraints({ advanced: [{ torch: !on }] });
      $btnTorch.data("on", on ? 0 : 1);
      $btnTorch.text(on ? "Linterna" : "Linterna ✓");
    }catch(e){
      console.warn("torch error:", e);
    }
  }

  function acceptCode(raw){
    const now = Date.now();
    if (!raw) return false;

    if (raw === lastCode && (now - lastAt) < 1200) return false;
    lastCode = raw; lastAt = now;

    $status.text(`Detectado: ${raw}`);

    stopStream();
    showOverlay(false);

    $inpBar.val(String(raw)).trigger("input");
    openBarAutocompleteAndPickFirst();
    return true;
  }

  async function startCamera(){
    if (!isSecure){
      alert("La cámara requiere HTTPS (o localhost).");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia){
      alert("Este navegador no soporta cámara.");
      return;
    }

    showOverlay(true);
    $status.text("Iniciando cámara…");

    try{
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false
      });

      videoEl.srcObject = stream;
      await videoEl.play();

      const track = stream.getVideoTracks()[0];
      const caps = track.getCapabilities?.();
      if (caps?.torch){
        $btnTorch.show().data("on", 0).text("Linterna");
      } else {
        $btnTorch.hide();
      }

      running = true;
      $status.text("Apunta el código dentro del cuadro…");

      if ("BarcodeDetector" in window){
        detector = new window.BarcodeDetector({
          formats: ["ean_13","ean_8","code_128","code_39","upc_a","upc_e","itf","qr_code"]
        });

        const loop = async () => {
          if (!running) return;
          try{
            const barcodes = await detector.detect(videoEl);
            if (barcodes && barcodes.length){
              const raw = barcodes[0]?.rawValue;
              if (raw && acceptCode(raw)) return;
            }
          }catch{}
          rafId = requestAnimationFrame(loop);
        };
        rafId = requestAnimationFrame(loop);
        return;
      }

      const ZXing = await loadZXing();
      zxingReader = new ZXing.BrowserMultiFormatReader();
      zxingReader.decodeFromVideoElementContinuously(videoEl, (result) => {
        if (!running) return;
        if (result){
          acceptCode(result.getText());
        }
      });

    }catch(err){
      console.error(err);
      stopStream();
      showOverlay(false);
      alert("No se pudo acceder a la cámara. Revisa permisos del navegador.");
    }
  }

  $btnScan.on("click", startCamera);
  $btnTorch.on("click", toggleTorch);
  $btnClose.on("click", function(){ stopStream(); showOverlay(false); });
  $overlay.on("click", function(e){ if (e.target === this){ stopStream(); showOverlay(false); }});
  $(document).on("keydown", function(e){
    if (e.key === "Escape" && $overlay.css("display") !== "none"){
      stopStream();
      showOverlay(false);
    }
  });

});
