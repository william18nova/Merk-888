// static/javascript/generar_venta.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  console.log("⚡ generar_venta.js — fast autocompletes + enter picks top + focus chain + scanner");

  /* URLs */
  const SUCURSAL_URL   = window.sucursalAutocompleteUrl;
  const PUNTOPAGO_URL  = window.puntopagoAutocompleteUrl;
  const CLIENTE_URL    = window.clienteAutocompleteUrl;
  const PRODUCTO_URL   = window.productoAutocompleteUrl;
  const AC_CODIGO_URL  = window.productoAutocompleteCodigoUrl || PRODUCTO_URL;
  const AC_BARRAS_URL  = window.productoAutocompleteBarrasUrl || PRODUCTO_URL;
  const VERIFICAR_URL  = window.verificarProductoUrl;
  const POR_COD_URL    = window.buscarProductoPorCodigoUrl;

  /* Selectores */
  const $nombre   = $("#producto_busqueda_nombre");
  const $codigo   = $("#producto_busqueda_codigo");
  const $barras   = $("#producto_busqueda_codigo_barras");
  const $pid      = $("#producto_id");
  const $cantidad = $("#cantidad");
  const $agregar  = $("#agregar-producto");
  const $tbody    = $("#detalle-productos tbody");
  const $totalEl  = $("#total");

  /* Dinero */
  const money = (n) =>
    new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP" }).format(Number(n) || 0);

  /* CSRF */
  $.ajaxSetup({
    beforeSend: (xhr, settings) => {
      if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type)) {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        if (m) xhr.setRequestHeader("X-CSRFToken", m[1]);
      }
    },
    cache: true,
  });

  /* Estado persistido */
  let sucursalID = localStorage.getItem("sucursalID") || "";
  const savedPunto = {
    id:  localStorage.getItem("puntopagoID") || "",
    name:localStorage.getItem("puntopagoName") || "",
    suc: localStorage.getItem("puntopagoSucursalID") || ""
  };

  /* Estado venta */
  const productos = [];
  const cantidades = [];
  let runningTotal = 0;

  /* Cache producto + índices */
  const FRESH_MS = 30000;
  const productCache = new Map();  // pid -> {nombre, barcode, price, stock, ts}
  const barcodeIndex = new Map();  // barcode -> pid
  const nameIndex = new Map();     // nombreLower -> pid
  const now = () => Date.now();
  const isFresh = (ts) => ts && now() - ts < FRESH_MS;

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

  function enableQtyAndAdd(enable) {
    $cantidad.prop("disabled", !enable);
    $agregar.prop("disabled", !enable);
  }
  function maybeFocusQty() {
    if ($pid.val() && $nombre.val() && $codigo.val() && $barras.val()) {
      enableQtyAndAdd(true);
      setTimeout(() => { $cantidad.focus().select(); }, 0);
    }
  }
  function setProductFields({ nombre, pid, barcode }) {
    if (nombre != null) $nombre.val(onlyName(nombre));
    if (pid != null)    $codigo.val(pid);
    if (barcode != null)$barras.val(barcode);
    if (pid != null)    $pid.val(pid);
    maybeFocusQty();
  }

  function updateCache(pid, data = {}) {
    const key = String(pid);
    const prev = productCache.get(key) || {};
    const rec = {
      nombre: onlyName(data.nombre ?? prev.nombre ?? ""),
      barcode: data.codigo_de_barras ?? data.barcode ?? prev.barcode ?? "",
      price: data.precio_unitario ?? prev.price,
      stock: data.cantidad_disponible ?? prev.stock,
      ts: data.ts || now(),
    };
    productCache.set(key, rec);
    if (rec.barcode) barcodeIndex.set(rec.barcode, key);
    if (rec.nombre)  nameIndex.set(rec.nombre.toLowerCase(), key);
    return rec;
  }
  function instantFromPid(pid) {
    const rec = productCache.get(String(pid));
    if (!rec) return false;
    if (!$nombre.val()) $nombre.val(rec.nombre || "");
    if (!$barras.val() && rec.barcode) $barras.val(rec.barcode);
    if (!$codigo.val()) $codigo.val(pid);
    $pid.val(pid);
    enableQtyAndAdd(true);
    maybeFocusQty();
    return true;
  }
  function instantFromBarcode(code) {
    const pid = barcodeIndex.get(code);
    const ok = pid ? instantFromPid(pid) : false;
    if (ok) maybeFocusQty();
    return ok;
  }
  function instantFromName(name) {
    const pid = nameIndex.get((onlyName(name) || "").toLowerCase());
    const ok = pid ? instantFromPid(pid) : false;
    if (ok) maybeFocusQty();
    return ok;
  }

  async function fillFromProductId(pid) {
    if (!pid) return;
    try {
      const r = await $.post(VERIFICAR_URL, { producto_id: pid, cantidad: 1, sucursal_id: sucursalID });
      if (!r || !r.exists) return;
      const rec = updateCache(pid, r);
      setProductFields({ nombre: rec.nombre, pid, barcode: rec.barcode });
      enableQtyAndAdd(true);
    } catch (e) { console.error(e); }
  }
  async function fillFromBarcode(barcode) {
    if (!barcode) return;
    try {
      const r = await $.getJSON(POR_COD_URL, { codigo_de_barras: barcode, sucursal_id: sucursalID });
      if (!r || !r.exists) return alert("Código de barras no encontrado.");
      const p = r.producto || {};
      setProductFields({ nombre: p.nombre, pid: p.id, barcode });
      enableQtyAndAdd(true);
      await fillFromProductId(p.id);
    } catch (e) { console.error(e); }
  }

  /* ============== FAST AUTOCOMPLETE (filtro local + remoto + ENTER top + focus chain) ============== */

  // Normalización + ranking
  const norm = s => (s||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();
  const tokens = q => norm(q).split(/\s+/).filter(Boolean);
  const matchesAll = (text, q) => { const t=norm(text), toks=tokens(q); for(const k of toks) if(!t.includes(k)) return false; return true; };
  const score = (text, q) => {
    const t=norm(text), s=norm(q); if(!s) return 1;
    if(t===s) return 1e6; let sc=0;
    if(t.startsWith(s)) sc+=800;
    const idx=t.indexOf(s); if(idx>=0) sc += Math.max(0, 500-idx*4);
    sc += Math.max(0, 150 - Math.abs(t.length-s.length)*6);
    return sc;
  };
  const rankFilter = (arr, q, max=40) =>
    arr.filter(r=>matchesAll(r.label||r.text||r.name||"",q))
       .map(r=>({r,sc:score(r.label||r.text||r.name||"",q)}))
       .sort((a,b)=>b.sc-a.sc || String(a.r.label||a.r.text).localeCompare(String(b.r.label||b.r.text)))
       .slice(0,max).map(x=>x.r);

  // Fetcher abortable + cache
  function makeFetcher(){
    let inflight=null;
    const cache=new Map();
    return {
      async get(url, params){
        const key=url+"::"+JSON.stringify(params||{});
        if(cache.has(key)) return cache.get(key);
        if(inflight) inflight.abort();
        inflight=new AbortController();
        try{
          const res=await fetch(url+"?"+new URLSearchParams(params||{}),{signal:inflight.signal});
          const js=await res.json(); cache.set(key,js); return js;
        }catch(e){ if(e.name!=="AbortError") console.error(e); return {results:[]}; }
      },
      warm(url, params){
        const key=url+"::"+JSON.stringify(params||{});
        if(cache.has(key)) return;
        fetch(url+"?"+new URLSearchParams(params||{})).then(r=>r.json()).then(js=>cache.set(key,js)).catch(()=>{});
      }
    };
  }
  const fetcher = makeFetcher();

  /**
   * createFastAC
   * · respuesta local inmediata con rank
   * · remoto en paralelo (abort)
   * · ENTER siempre elige la opción superior y salta al siguiente input
   * · si input vacío => muestra todo (prefetch)
   */
  function createFastAC({ $inp, url, mapItem, extra=()=>({}), onSelect, nextFocus=null, allowEmpty=true, uniqueBy="id" }){
    let index=[]; const seen=new Set(); let ctxKey="";

    const contextKey=()=>{const e=extra()||{}; return Object.keys(e).sort().map(k=>`${k}:${e[k]}`).join("|");};

    $inp.autocomplete({
      minLength:0, delay:0, autoFocus:true, appendTo:"body",
      position:{ my:"left top+6", at:"left bottom", collision:"flipfit" },
      source: async (req, resp)=>{
        const term=req.term||"";
        const ck=contextKey();
        if(ck!==ctxKey){ ctxKey=ck; index=[]; seen.clear(); }

        if(index.length){ resp(rankFilter(index, term)); }
        else if(!term && allowEmpty){ resp([]); }

        const data=await fetcher.get(url, {term, ...extra()});
        const items=(data.results||[]).map(mapItem).filter(Boolean);
        for(const it of items){
          const key=String(it[uniqueBy] ?? it.id ?? it.value ?? it.label);
          if(!key) continue;
          if(!seen.has(key)){ seen.add(key); index.push(it); }
        }
        if($inp.val()===term) resp(rankFilter(index, term));
      },
      open(){ $inp.autocomplete("widget").css("z-index", 3000); },
      select(_e, ui){
        if(!ui||!ui.item) return false;
        onSelect?.(ui.item);
        setTimeout(()=>{
          if(typeof nextFocus==="string") $(nextFocus).focus().select?.();
          else if(typeof nextFocus==="function") nextFocus();
        }, 0);
        return false;
      }
    });

    // Mostrar todo al enfocar y al quedar vacío
    $inp.on("focus", function(){ $(this).autocomplete("search", this.value || ""); });
    if(allowEmpty){
      $inp.on("input", function(){ if(!this.value) $(this).autocomplete("search",""); });
    }

    // ENTER = elegir siempre la PRIMERA opción y avanzar
    $inp.on("keydown", function(e){
      if(e.key!=="Enter") return;
      const term=this.value||"";
      const ac=$(this).data("ui-autocomplete");
      const $menu=ac && ac.menu && ac.menu.element;
      e.preventDefault();

      if($menu && $menu.is(":visible")){
        const $first=$menu.find("li:visible .ui-menu-item-wrapper").first();
        if($first.length){ $first.trigger("mouseenter").trigger("click"); return; }
      }

      // Sin menú visible: usa índice local
      const localTop = rankFilter(index, term, 1)[0];
      if(localTop){
        onSelect?.(localTop);
        setTimeout(()=>{
          if(typeof nextFocus==="string") $(nextFocus).focus().select?.();
          else if(typeof nextFocus==="function") nextFocus();
        },0);
        return;
      }

      // Fallback remoto
      fetcher.get(url, {term, ...extra()}).then(data=>{
        const items=(data.results||[]).map(mapItem).filter(Boolean);
        if(!items.length) return;
        onSelect?.(items[0]);
        setTimeout(()=>{
          if(typeof nextFocus==="string") $(nextFocus).focus().select?.();
          else if(typeof nextFocus==="function") nextFocus();
        },0);
      });
    });

    // Warm prefetch
    if(allowEmpty){ fetcher.warm(url, {term:"", ...extra()}); }
  }

  /* Autocompletes */

  if (sucursalID) {
    $("#sucursal_autocomplete").val(localStorage.getItem("sucursalName") || "");
    $("#sucursal_id").val(sucursalID);
  }
  if (savedPunto.id && savedPunto.suc && savedPunto.suc === sucursalID) {
    $("#puntopago_autocomplete").val(savedPunto.name || "");
    $("#puntopago_id").val(savedPunto.id);
  }

  // Sucursal
  createFastAC({
    $inp: $("#sucursal_autocomplete"),
    url: SUCURSAL_URL,
    mapItem: r => ({ label:r.text, value:r.text, id:r.id, name:r.text }),
    onSelect: ({ id, label }) => {
      sucursalID = id;
      $("#sucursal_id").val(id);
      $("#sucursal_autocomplete").val(label);
      localStorage.setItem("sucursalID", id);
      localStorage.setItem("sucursalName", label);
      const ppSuc = localStorage.getItem("puntopagoSucursalID");
      if (ppSuc && ppSuc !== String(id)) {
        $("#puntopago_autocomplete").val("");
        $("#puntopago_id").val("");
        localStorage.removeItem("puntopagoID");
        localStorage.removeItem("puntopagoName");
        localStorage.removeItem("puntopagoSucursalID");
      }
      enableQtyAndAdd(false);
    },
    nextFocus: "#puntopago_autocomplete",
    allowEmpty: true
  });

  // Punto de pago
  createFastAC({
    $inp: $("#puntopago_autocomplete"),
    url: PUNTOPAGO_URL,
    extra: () => ({ sucursal_id: sucursalID }),
    mapItem: r => ({ label:r.text, value:r.text, id:r.id, name:r.text }),
    onSelect: ({ id, label }) => {
      $("#puntopago_autocomplete").val(label);
      $("#puntopago_id").val(id);
      localStorage.setItem("puntopagoID", id);
      localStorage.setItem("puntopagoName", label);
      localStorage.setItem("puntopagoSucursalID", sucursalID || "");
    },
    nextFocus: "#cliente_busqueda",
    allowEmpty: true
  });

  // Cliente
  createFastAC({
    $inp: $("#cliente_busqueda"),
    url: CLIENTE_URL,
    mapItem: c => ({ label:c.text, value:c.text, id:c.id, name:c.text }),
    onSelect: ({ id, label }) => {
      $("#cliente_busqueda").val(label);
      $("#cliente_id").val(id);
    },
    nextFocus: "#producto_busqueda_nombre",
    allowEmpty: true
  });

  // Producto por Nombre
  createFastAC({
    $inp: $nombre,
    url: PRODUCTO_URL,
    extra: () => ({ sucursal_id: sucursalID }),
    mapItem: p => ({ label:p.text, value:p.text, id:p.id, name:p.text }),
    onSelect: (item) => {
      updateCache(item.id, { nombre: item.name });
      if (!instantFromName(item.name)) setProductFields({ pid: item.id });
      enableQtyAndAdd(true);
      fillFromProductId(item.id);
    },
    nextFocus: "#cantidad",
    allowEmpty: true
  });

  // Producto por Código (ID)
  createFastAC({
    $inp: $codigo,
    url: AC_CODIGO_URL,
    extra: () => ({ sucursal_id: sucursalID }),
    mapItem: p => ({ label:String(p.id), value:String(p.id), id:p.id, name:p.text||"" }),
    onSelect: (item) => {
      updateCache(item.id, { nombre: item.name });
      instantFromPid(item.id);
      fillFromProductId(item.id);
    },
    nextFocus: "#cantidad",
    allowEmpty: true
  });

  // Producto por Código de Barras
  createFastAC({
    $inp: $barras,
    url: AC_BARRAS_URL,
    extra: () => ({ sucursal_id: sucursalID }),
    mapItem: p => {
      const bc=p.barcode||p.codigo_de_barras||"";
      if(!bc) return null;
      return { label:bc, value:bc, id:p.id, name:p.text||"", barcode:bc };
    },
    onSelect: (item) => {
      updateCache(item.id, { nombre:item.name, barcode:item.barcode });
      instantFromBarcode(item.barcode) || setProductFields({ nombre:item.name, pid:item.id, barcode:item.barcode });
      enableQtyAndAdd(true);
      fillFromProductId(item.id);
    },
    nextFocus: "#cantidad",
    allowEmpty: true
  });

  // Evitar que ENTER del escáner seleccione primer ítem del menú
  (function fixScannerEnter() {
    $barras.autocomplete("option", "autoFocus", false);
    $barras.on("keydown", function (e) {
      if (e.key === "Enter" || e.key === "Tab") {
        const code = $.trim(this.value);
        if (!code) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        $(this).autocomplete("close");
        instantFromBarcode(code);
        fillFromBarcode(code);
      }
    });
  })();

  // Cross-fill live
  $codigo.on("input", function () { const v=$.trim(this.value); if (v) instantFromPid(v); });
  $barras.on("input", function () { const bc=$.trim(this.value); if (bc) instantFromBarcode(bc); });
  $nombre.on("input", function () { const nm=$.trim(this.value); if (nm) instantFromName(nm); });

  // Confirmación con Enter cuando no hay menú visible (nombre/código/código barras)
  function bindEnterConfirm(selector, handler, instant) {
    $(selector).on("keydown", function (e) {
      if (e.key !== "Enter") return;
      const ac = $(this).data("ui-autocomplete");
      const menuVisible = ac && ac.menu && ac.menu.element.is(":visible");
      if (!menuVisible) {
        e.preventDefault();
        const val = $.trim($(this).val());
        if (!val) return;
        if (instant) instant(val);
        handler(val);
        // foco a cantidad si corresponde
        setTimeout(()=> $("#cantidad").focus().select(), 0);
      }
    });
  }
  bindEnterConfirm("#producto_busqueda_codigo", (pid) => fillFromProductId(pid), (pid)=>instantFromPid(pid));
  bindEnterConfirm("#producto_busqueda_codigo_barras", (bc) => fillFromBarcode(bc), (bc)=>instantFromBarcode(bc));
  bindEnterConfirm("#producto_busqueda_nombre", (term) => {
    fetch(PRODUCTO_URL + "?" + new URLSearchParams({ term, sucursal_id: sucursalID }))
      .then(r=>r.json())
      .then(d => {
        const items = d.results || [];
        if (!items.length) return;
        const match = items.find(x => onlyName(x.text||"").toLowerCase() === onlyName(term).toLowerCase()) || items[0];
        if (match && match.id) { instantFromPid(match.id); fillFromProductId(match.id); }
      }).catch(()=>{});
  }, (term)=>instantFromName(term));

  $nombre.add($codigo).add($barras).on("change input", maybeFocusQty);

  /* Quagga */
  $("#btnEscanear").click(() => {
    $("#interactive").show();
    Quagga.init(
      { inputStream:{type:"LiveStream",target:"#interactive",constraints:{facingMode:"environment"}},
        decoder:{readers:["ean_reader"]} },
      (err) => (err ? console.error(err) : Quagga.start())
    );
    Quagga.onDetected(async (data) => {
      Quagga.stop(); $("#interactive").hide();
      const code = data.codeResult.code;
      instantFromBarcode(code);
      await fillFromBarcode(code);
    });
  });

  /* Total + serialización */
  function setTotal(v) {
    runningTotal = v;
    $totalEl.text(money(runningTotal));
    $("#productos").val(JSON.stringify(productos));
    $("#cantidades").val(JSON.stringify(cantidades));
  }
  function addToTotal(delta) { setTotal(runningTotal + (Number(delta) || 0)); }

  /* Agregar al carrito */
  $("#agregar-producto").click(async () => {
    const pid = $pid.val();
    const qty = parseInt($cantidad.val(), 10);
    if (!pid || !qty || qty < 1) return alert("Datos inválidos.");

    const cached = productCache.get(String(pid));
    const doAppend = (nombre, price, qtyAdd) => {
      const idx = productos.indexOf(pid);
      if (idx > -1) {
        cantidades[idx] += qtyAdd;
        const $row = $tbody.find(`tr[data-pid='${pid}']`);
        const newQty = cantidades[idx];
        $row.data("qty", newQty);
        $row.find(".qty-input").val(newQty);
        $row.find("td").eq(3).text(money(price * newQty));
        addToTotal(price * qtyAdd);
      } else {
        productos.push(pid);
        cantidades.push(qtyAdd);
        const subtotal = price * qtyAdd;
        $tbody.prepend(`
          <tr data-pid="${pid}" data-price="${price}" data-qty="${qtyAdd}">
            <td data-id="${pid}">${nombre || ""}</td>
            <td>
              <input type="number" class="qty-input" min="1" value="${qtyAdd}" />
            </td>
            <td>${money(price)}</td>
            <td>${money(subtotal)}</td>
            <td class="text-center">
              <button class="btn btn-danger btn-sm eliminar-producto" title="Eliminar">
                <i class="fas fa-trash-alt"></i>
              </button>
            </td>
          </tr>
        `);
        addToTotal(subtotal);
      }
      // reset
      $nombre.val(""); $codigo.val(""); $barras.val("");
      $pid.val(""); $cantidad.val(1); enableQtyAndAdd(false);
    };

    if (cached && isFresh(cached.ts)) {
      if (cached.stock != null && qty > cached.stock) return alert(`Solo ${cached.stock} disponibles.`);
      const price = Number(cached.price) || 0;
      doAppend(cached.nombre, price, qty);
      return;
    }

    $.post(VERIFICAR_URL, { producto_id: pid, cantidad: qty, sucursal_id: sucursalID }).done((r) => {
      if (!r.exists) return alert("Sin stock/sucursal.");
      if (r.cantidad_disponible < qty) return alert(`Solo ${r.cantidad_disponible} disponibles.`);
      updateCache(pid, r);
      doAppend(onlyName(r.nombre), Number(r.precio_unitario) || 0, qty);
    });
  });

  /* ENTER en cantidad = agregar */
  $cantidad.on("keydown", function (e) {
    if (e.key === "Enter" && !$agregar.prop("disabled")) {
      e.preventDefault();
      $agregar.click();
    }
  });

  /* Editar cantidad inline */
  $tbody.on("input change", ".qty-input", function () {
    const $row  = $(this).closest("tr");
    const pid   = $row.data("pid").toString();
    const price = Number($row.data("price")) || 0;

    let newQty  = parseInt(this.value, 10);
    if (!newQty || newQty < 1) newQty = 1;

    const cached = productCache.get(pid);
    if (cached && cached.stock != null && newQty > cached.stock) {
      newQty = cached.stock;
      this.value = newQty;
      alert(`Solo ${cached.stock} disponibles.`);
    }

    const oldQty = Number($row.data("qty")) || 0;
    if (newQty === oldQty) return;

    $row.data("qty", newQty);
    const i = productos.indexOf(pid);
    if (i > -1) cantidades[i] = newQty;

    $row.find("td").eq(3).text(money(price * newQty));
    addToTotal(price * (newQty - oldQty));
  });

  /* Eliminar fila */
  $tbody.on("click", ".eliminar-producto", function () {
    const $row = $(this).closest("tr");
    const pid = $row.data("pid").toString();
    const idx = productos.indexOf(pid);
    const price = Number($row.data("price")) || 0;
    const qty   = Number($row.data("qty")) || 0;
    addToTotal(-(price * qty));
    if (idx > -1) { productos.splice(idx, 1); cantidades.splice(idx, 1); }
    $row.remove();
  });

  /* Modal de pago */
  const $modal      = $("#myModal");
  const $efOptions  = $("#efectivo-options");
  const $amountIn   = $("#monto-recibido");
  const $changeOut  = $("#cambio");
  const $confirmBtn = $("#confirmar-pago");

  $("#generar-venta").click(() => {
    if (!productos.length) return alert("Agregue productos.");
    if (!sucursalID || !$("#puntopago_id").val()) return alert("Seleccione sucursal y punto de pago.");
    $("input[name='payment_method']").prop("checked", false);
    $efOptions.hide();
    $amountIn.val("");
    $changeOut.text("");
    $modal.show();
  });
  $(".close").click(() => $modal.hide());
  $(window).on("click", (e) => { if (e.target === $modal[0]) $modal.hide(); });

  $(document).on("change", "input[name='payment_method']", function () {
    $efOptions.toggle(this.value === "efectivo");
    $amountIn.val("");
    $changeOut.text("");
  });
  $(document).on("click", ".radio-wrap", function (e) {
    if (e.target.tagName !== "INPUT") {
      $(this).find("input[type=radio]").prop("checked", true).trigger("change");
    }
    $(this).closest(".modal-content").attr("tabindex","-1").focus();
  });
  $amountIn.on("input", function () {
    const received = parseFloat(this.value) || 0;
    const change = received - runningTotal;
    $changeOut.text(change >= 0 ? `Cambio: ${money(change)}` : "");
  }).on("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); $confirmBtn.click(); }
  });
  $confirmBtn.click(() => {
    const m = $("input[name='payment_method']:checked").val();
    if (!m) return alert("Seleccione medio de pago.");
    if (m === "efectivo") {
      const rec = parseFloat($amountIn.val()) || 0;
      if (rec < runningTotal) return alert("Monto recibido insuficiente.");
    }
    $("#medio_pago").val(m);
    $modal.hide();
    $("#venta-form").submit();
  });

  /* submit AJAX */
  $("#venta-form").submit(function (e) {
    e.preventDefault();
    $.post($(this).attr("action"), $(this).serialize())
      .done((r) => {
        if (r.success) {
          alert("✅ ¡Venta generada correctamente!");
          location.reload();
        } else {
          alert(r.error || "Error");
        }
      })
      .fail(() => alert("Error de red"));
  });

  /* filtro carrito */
  $("#buscar-detalles").on("keyup", function () {
    const t = $(this).val().toLowerCase();
    $tbody.find("tr").each(function () { $(this).toggle($(this).text().toLowerCase().includes(t)); });
  });

  /* init */
  enableQtyAndAdd(false);

  /* Detector global de pistola */
  (function globalScannerDetector() {
    const MIN_CHARS = 8, GAP_MS = 35;
    let buf="", first=0, last=0, idleTimer=null;
    function reset(){ buf=""; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} }
    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }
      const t = Date.now();
      if (e.key === "Enter" || e.key === "Tab") {
        const fastEnough = buf && (t-first) < buf.length * (GAP_MS+5) && (t-last) < GAP_MS*3;
        if (fastEnough && buf.length >= MIN_CHARS) {
          e.preventDefault(); e.stopImmediatePropagation();
          const code = buf; reset();
          const $bc = $("#producto_busqueda_codigo_barras");
          $bc.val(code); try { $bc.autocomplete("close"); } catch (_){}
          instantFromBarcode(code);
          fillFromBarcode(code);
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

});
