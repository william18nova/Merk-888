// static/javascript/editar_inventario.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  /* ================= CSRF ================= */
  function getCSRF() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  const isSecure = window.isSecureContext || location.hostname === "localhost";

  /* ================= UI refs ================= */
  const $form     = $("#inventarioForm");
  const $pidHid   = $("#id_productoid");
  const $qtyExact = $("#id_cantidad");
  const $qtyAdd   = $("#id_add_cantidad");

  const $inpNom = $("#producto_busqueda_nombre");
  const $inpBar = $("#producto_busqueda_barras");
  const $inpId  = $("#producto_busqueda_id");

  const $btnUpd = $("#actualizarProductoBtn");

  const $tbody  = $("#productos-body");

  const $errBox  = $("#error-message");
  const $okBox   = $("#success-message");
  const $warnBox = $("#warning-message");

  // Scanner
  const $btnScan  = $("#btnScanBarcode");
  const $overlay  = $("#barcodeScannerOverlay");
  const $btnClose = $("#btnCloseScanner");
  const $btnTorch = $("#btnToggleTorch");
  const $status   = $("#scannerStatus");
  const videoEl   = document.getElementById("barcodeVideo");

  function showErr(msg){
    $okBox.hide().text("");
    $warnBox.hide().text("");
    $errBox.html(`<i class="fas fa-exclamation-circle"></i> ${msg}`).show();
  }
  function showOk(msg){
    $errBox.hide().text("");
    $warnBox.hide().text("");
    $okBox.html(`<i class="fas fa-check-circle"></i> ${msg}`).show();
  }
  function showWarn(msg){
    $errBox.hide().text("");
    $okBox.hide().text("");
    $warnBox.html(`<i class="fas fa-triangle-exclamation"></i> ${msg}`).show();
  }

  function clearFieldErrors(){
    $(".field-error").removeClass("visible").text("");
    $(".input-error").removeClass("input-error");
  }
  function fieldError(field, msg){
    const $box = $(`#error-id_${field}`);
    if ($box.length){
      $box.text(msg).addClass("visible");
    }
    const map = {
      productoid: [$inpNom,$inpBar,$inpId],
      cantidad: [$qtyExact],
      add_cantidad: [$qtyAdd],
      sucursal: [$("#id_sucursal_autocomplete")]
    };
    (map[field] || []).forEach($i => $i.addClass("input-error"));
  }

  function onlyDigits(s){ return String(s||"").replace(/\D+/g, ""); }
  function isIntString(v){
    v = String(v||"").trim();
    return /^-?\d+$/.test(v);
  }

  /* ================= DataTable ================= */
  const dt = $("#productos-list").DataTable({
    paging: false,
    searching: false,
    info: false,
    ordering: false,
    deferRender: true,
    responsive: true,
    language: { emptyTable: "Busca un producto para cargarlo…" }
  });

  /* ================= Estado (solo 1 producto) ================= */
  let current = {
    productId: null,
    inventarioId: null,
    nombre: "",
    barcode: "",
    cantidad: 0,
    alert9000: false
  };

  function clearTable(){
    dt.clear().draw(false);
  }

  function clearAllFieldsAndUI({ keepMessages=false } = {}){
    clearFieldErrors();

    $pidHid.val("");
    $qtyExact.val("");
    $qtyAdd.val("");

    $inpNom.val("").trigger("change");
    $inpBar.val("").trigger("change");
    $inpId.val("").trigger("change");

    try { $inpNom.autocomplete("close"); } catch {}
    try { $inpBar.autocomplete("close"); } catch {}
    try { $inpId.autocomplete("close"); } catch {}

    current = { productId:null, inventarioId:null, nombre:"", barcode:"", cantidad:0, alert9000:false };
    clearTable();

    if (!keepMessages){
      $errBox.hide().text("");
      $okBox.hide().text("");
      $warnBox.hide().text("");
    }

    setTimeout(() => $inpBar.trigger("focus"), 0);
  }

  function renderSingleRow(){
    clearTable();
    if (!current.productId) return;

    const rowHtml = `
      <tr data-product-id="${current.productId}" data-inventario-id="${current.inventarioId || ""}">
        <td>${current.nombre || ("Producto " + current.productId)}</td>
        <td>${current.barcode || ""}</td>
        <td>${String(current.cantidad ?? 0)}</td>
        <td>
          <button type="button" class="btn-eliminar" title="Eliminar">
            <i class="fas fa-trash-alt"></i>
          </button>
        </td>
      </tr>
    `;
    dt.row.add($(rowHtml)).draw(false);
  }

  function setSelectedProduct({id, nombre, barcode}){
    $pidHid.val(id);
    current.productId = String(id);
    current.nombre = nombre || "";
    current.barcode = barcode || "";

    if (nombre) $inpNom.val(nombre);
    if (barcode) $inpBar.val(barcode);
    $inpId.val(String(id));

    loadSingleInventoryItem(id);
  }

  /* ================= Cargar SOLO 1 item (AJAX) ================= */
  function loadSingleInventoryItem(productId){
    clearFieldErrors();
    $errBox.hide(); $okBox.hide(); $warnBox.hide();

    const url = inventarioItemUrl + "?" + new URLSearchParams({ productoid: productId, _ts: Date.now() });

    fetch(url, { cache:"no-store" })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(data => {
        if (!data || !data.success) {
          showErr("No se pudo cargar el producto.");
          return;
        }

        const p = data.product || {};
        current.productId = String(p.id || productId);
        current.nombre = p.nombre || current.nombre;
        current.barcode = p.codigo_de_barras || current.barcode;
        current.inventarioId = data.inventario_id || null;

        const qty = Number(data.cantidad);
        current.cantidad = Number.isFinite(qty) ? qty : 0;

        current.alert9000 = !!data.alert_9000;

        // ✅ mostrar EXACTO (negativo / 0 / positivo)
        $qtyExact.val(String(current.cantidad));
        $qtyAdd.val("");

        renderSingleRow();

        // ✅ aviso si >9000
        if (current.alert9000){
          showWarn("Este producto nunca se ha contado, cuéntelo antes de surtir.");
        }

        setTimeout(() => { $qtyAdd.trigger("focus"); }, 0);
      })
      .catch(() => showErr("Error de red cargando el producto."));
  }

  /* =============================================================================
     Autocomplete ULTRA: cache + abort para que sea instantáneo
  ============================================================================= */
  function makeAC($input, urlBuilder, mode){
    const cache = new Map(); // term -> results[]
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
        if (cache.has(key)){
          return resp(cache.get(key));
        }

        // cancelar request anterior
        try { if (acAbort) acAbort.abort(); } catch {}
        acAbort = new AbortController();

        fetch(urlBuilder(term), { cache:"no-store", signal: acAbort.signal })
          .then(r => r.ok ? r.json() : {results:[]})
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

            // cache pequeño (LRU simple)
            cache.set(key, arr);
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

  /* ================= Auto-pick barras (sin timeout) ================= */
  let pickToken = 0;
  let pendingPick = null;

  $inpBar.on("autocompleteresponse", function(_e, ui){
    if (!pendingPick) return;

    const curVal = String(($inpBar.val() || "").trim());
    if (curVal !== pendingPick.value) return;
    if (!ui || !Array.isArray(ui.content)) return;

    const list = ui.content;
    if (!list.length){
      $status.text(`No encontrado: ${pendingPick.value}`);
      pendingPick = null;
      return;
    }

    const exact = list.find(it => String(it.barcode || it.value || "") === pendingPick.value);
    const item = exact || list[0];

    pendingPick = null;
    try { $inpBar.autocomplete("close"); } catch {}
    setSelectedProduct({ id: item.id, nombre: item.text, barcode: item.barcode });
  });

  function openBarAutocompleteAndPickFirst(){
    const v = String(($inpBar.val() || "").trim());
    if (!v) return;
    pendingPick = { token: ++pickToken, value: v };
    try { $inpBar.autocomplete("close"); } catch {}
    $inpBar.autocomplete("search", v);
  }

  /* =============================================================================
     ✅ Actualizar: si "Añadir cantidad" tiene valor -> suma (DB atómica)
               si no -> set exacto (puede ser negativo / 0)
  ============================================================================= */
  function validateSingle(){
    clearFieldErrors();

    const pid = ($pidHid.val() || "").trim();
    const qtyExact = String($qtyExact.val() || "").trim();
    const qtyAdd   = String($qtyAdd.val() || "").trim();

    let bad = false;
    if (!pid) { fieldError("productoid", "Debe seleccionar un producto."); bad = true; }

    // Si hay add, se usa add; si no hay add, se usa exact
    if (qtyAdd !== ""){
      if (!isIntString(qtyAdd)){ fieldError("add_cantidad", "Añadir cantidad debe ser entero (puede ser negativo o 0)."); bad = true; }
      // regla local de aviso (además del backend)
      if ((current.cantidad || 0) > 9000){
        fieldError("add_cantidad", "Este producto nunca se ha contado, cuéntelo antes de surtir.");
        bad = true;
      }
      if (bad) return null;
      return { pid, qtyExact, qtyAdd, mode:"add" };
    }

    // exact
    if (qtyExact === ""){
      fieldError("cantidad", "Ingrese la cantidad exacta.");
      bad = true;
    } else if (!isIntString(qtyExact)){
      fieldError("cantidad", "Cantidad exacta debe ser entero (puede ser negativo o 0).");
      bad = true;
    }

    if (bad) return null;
    return { pid, qtyExact, qtyAdd:"", mode:"exact" };
  }

  let saving = false;
  $btnUpd.on("click", function(){
    if (saving) return;

    const v = validateSingle();
    if (!v) return;

    saving = true;
    $btnUpd.prop("disabled", true);

    const fd = new FormData();
    fd.append("action", "add_item");
    fd.append("productoid", v.pid);

    if (v.mode === "add"){
      fd.append("add_cantidad", v.qtyAdd);
      // mandamos también cantidad exacta por si quieres, pero el backend suma con add_cantidad
      fd.append("cantidad", v.qtyExact || String(current.cantidad || 0));
    } else {
      fd.append("cantidad", v.qtyExact);
      fd.append("add_cantidad", "");
    }

    fetch($form.attr("action"), {
      method: "POST",
      headers: { "X-CSRFToken": getCSRF(), "Accept":"application/json" },
      body: fd
    })
    .then(async r => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw j;
      return j;
    })
    .then(data => {
      if (!data || !data.success) {
        showErr("No se pudo actualizar.");
        return;
      }

      const newQty = Number(data.new_cantidad);
      if (Number.isFinite(newQty)){
        current.cantidad = newQty;
        $qtyExact.val(String(newQty));
        renderSingleRow();
      }

      $qtyAdd.val(""); // ✅ listo para seguir surtiendo rápido
      showOk("✅ Producto actualizado.");
      setTimeout(() => $qtyAdd.trigger("focus"), 0);
    })
    .catch(err => {
      try {
        const errs = JSON.parse(err.errors || "{}");
        Object.entries(errs).forEach(([field, arr]) => {
          (arr || []).forEach(e => fieldError(field, e.message));
        });
      } catch {
        showErr("Error actualizando el producto.");
      }
    })
    .finally(() => {
      saving = false;
      $btnUpd.prop("disabled", false);
    });
  });

  /* ================= Eliminar (si existe inventario_id) ================= */
  $tbody.on("click", ".btn-eliminar", function(){
    const $tr = $(this).closest("tr");
    const invId = String($tr.data("inventario-id") || "");
    if (!invId) {
      clearAllFieldsAndUI({ keepMessages:false });
      showOk("Producto removido de la vista.");
      return;
    }

    if (!confirm("¿Eliminar este producto del inventario?")) return;

    fetch(`/inventario/item/${invId}/eliminar/`, {
      method: "POST",
      headers: { "X-CSRFToken": getCSRF(), "Accept":"application/json" },
    })
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(d => {
      if (d && d.success){
        clearAllFieldsAndUI({ keepMessages:true });
        showOk(d.message || "Producto eliminado.");
      } else showErr("No se pudo eliminar.");
    })
    .catch(() => showErr("Error de red eliminando el producto."));
  });

  /* ================= Guardar Cambios (reusa el botón actualizar) ================= */
  $form.on("submit", function(e){
    e.preventDefault();
    $btnUpd.trigger("click");
  });

  /* =============================================================================
     📷 Scanner (BarcodeDetector + ZXing fallback) + visor con cuadro
  ============================================================================= */
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

    try { detector = null; } catch {}
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
        video: { facingMode: { ideal:"environment" }, width:{ideal:1280}, height:{ideal:720} },
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
          const raw = result.getText();
          acceptCode(raw);
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

  $btnClose.on("click", function(){
    stopStream();
    showOverlay(false);
  });

  $overlay.on("click", function(e){
    if (e.target === this){
      stopStream();
      showOverlay(false);
    }
  });

  $(document).on("keydown", function(e){
    if (e.key === "Escape" && $overlay.css("display") !== "none"){
      stopStream();
      showOverlay(false);
    }
  });

});
