/* static/javascript/editar_pedido.js
   ─────────────────────────────────────────────────────────────────────────────
   Ultra-rápido Autocomplete con adaptación al escribir y al borrar:
     • Render local inmediato (tokens AND + ranking)
     • Petición remota en paralelo (Abort + debounce corto)
     • Warm cache (prefetch)
     • ENTER = selecciona 1ª opción visible y pasa al siguiente input
     • ENTER en “cantidad” = como click en “Agregar producto”
   Además:
     • Precarga initialDetalles
     • DataTable + edición de cantidad/precio + total dinámico
     • Submit AJAX con validaciones y manejo de errores
-------------------------------------------------------------------------------*/
(() => {
  "use strict";

  /* ───────── helpers DOM ───────── */
  const $id = id => document.getElementById(id);
  const $qs = sel => document.querySelector(sel);
  const money = n => new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP" }).format(n);

  /* ───────── UI helpers (flashes + field errors) ───────── */
  const ERR_BOX_MAP = {
    proveedor:"proveedor", proveedor_autocomplete:"proveedor",
    sucursal:"sucursal",   sucursal_autocomplete:"sucursal",
    producto:"detalles",   cantidad:"cantidad",
    detalles:"detalles",
    estado:"estado",
    monto_pagado:"monto_pagado",
    caja_pagoid:"caja_pago_autocomplete",
  };
  const INPUT_MAP = {
    proveedor:$id("id_proveedor_autocomplete"),
    proveedor_autocomplete:$id("id_proveedor_autocomplete"),
    sucursal:$id("id_sucursal_autocomplete"),
    sucursal_autocomplete:$id("id_sucursal_autocomplete"),
    producto:$id("producto-input"),
    cantidad:$id("cantidad-input"),
    detalles:$id("producto-input"),
    estado:$id("id_estado"),
    monto_pagado:$id("id_monto_pagado"),
    caja_pago_autocomplete:$id("id_caja_pago_autocomplete"),
  };
  const UI = {
    flash(kind, msg){
      const box = $id(`${kind}-message`);
      box.innerHTML = `<i class="fas fa-${kind==="error"?"exclamation":"check"}-circle"></i> ${msg}`;
      box.style.display = "block";
    },
    clearFlashes(){
      ["success","error"].forEach(k => {
        const b = $id(`${k}-message`);
        b.style.display = "none"; b.innerHTML = "";
      });
    },
    clearFieldErrors(){
      document.querySelectorAll(".field-error.visible").forEach(b => {
        b.classList.remove("visible"); b.innerHTML = "";
      });
      document.querySelectorAll(".input-error").forEach(i => i.classList.remove("input-error"));
    },
    fieldError(field, msg){
      const key   = ERR_BOX_MAP[field] || field;
      const input = INPUT_MAP[field]   || INPUT_MAP[key];
      let box     = document.querySelector(`#error-id_${key}`);
      if(!box && input){
        box = document.createElement("div");
        box.id = `error-id_${key}`;
        box.className = "field-error";
        input.parentNode.insertBefore(box, input.nextSibling);
      }
      if(box){
        box.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
        box.classList.add("visible");
      }
      input?.classList.add("input-error");
    }
  };

  /* ───────── DataTable ───────── */
  const HEADERS = ["Producto","Cantidad","Precio U.","Subtotal","Acciones"];
  const dt = $("#detalle-pedido").DataTable({
    paging:false, searching:false, info:false, responsive:true,
    columnDefs:[{targets:4,orderable:false}],
    rowCallback: row => $("td", row).each((i,td)=> td.dataset.label = HEADERS[i])
  });

  /* ───────── Estado global ───────── */
  let detalles = (window.initialDetalles || []).map(d => ({
    ...d, proveedorid: d.proveedorid ?? $id("id_proveedor").value,
  }));
  let precioSel       = 0;
  let proveedorActual = $id("id_proveedor").value || null;
  let proveedorNombre = $id("id_proveedor_autocomplete").value || "";

  function drawTable(){
    dt.clear(); let total = 0;
    detalles.forEach(d=>{
      total += d.subtotal;
      dt.row.add([
        d.producto,
        `<input type="number" class="qty-input" min="1" step="1"
                data-id="${d.detallepedidoid}" value="${d.cantidad}">`,
        `<input type="number" class="price-input" min="0" step="0.01"
                data-id="${d.detallepedidoid}" value="${Number(d.precio_unitario).toFixed(2)}">`,
        money(d.subtotal),
        `<button type="button" class="btn-eliminar" data-id="${d.detallepedidoid}">
           <i class="fas fa-trash-alt"></i>
         </button>`
      ]);
    });
    dt.draw(false);
    $id("total-valor").textContent = money(total);
    $id("id_detalles").value       = JSON.stringify(detalles);
  }
  drawTable();

  /* ───────── Handlers de tabla (qty/price/delete) ───────── */
  $("#detalle-pedido tbody")
    .on("change",".qty-input", function(){
      const id = String(this.dataset.id);
      const v  = parseInt(this.value,10);
      const row = detalles.find(d => String(d.detallepedidoid)===id);
      if(!row) return;
      if(isNaN(v) || v<=0) detalles = detalles.filter(d => String(d.detallepedidoid)!==id);
      else { row.cantidad=v; row.subtotal=v*Number(row.precio_unitario||0); }
      drawTable();
    })
    .on("change",".price-input", function(){
      const id = String(this.dataset.id);
      const raw = (this.value||"").replace(",",".");
      let p = parseFloat(raw); if(isNaN(p)||p<0) p=0;
      const row = detalles.find(d => String(d.detallepedidoid)===id);
      if(!row) return;
      row.precio_unitario=p; row.subtotal=Number(row.cantidad||0)*p;
      drawTable();
    })
    .on("click",".btn-eliminar", function(){
      const id = String(this.dataset.id);
      detalles = detalles.filter(d => String(d.detallepedidoid)!==id);
      drawTable();
    });

  /* ═════════════════════════════════════════════════════════════
     AUTOCOMPLETE ultra-rápido (adaptación al escribir y al borrar)
     ═════════════════════════════════════════════════════════════ */
  const CACHE_TTL_MS = 60_000;
  const MAX_LOCAL_RESULTS = 50;
  const IDLE = window.requestIdleCallback || (fn => setTimeout(fn, 1));

  const norm = s => (s||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();
  const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tokens = q => norm(q).split(/\s+/).filter(Boolean);

  function matchesAllTokens(text, q){
    const t = norm(text), toks = tokens(q);
    if(!toks.length) return true;
    for(const k of toks) if(!t.includes(k)) return false;
    return true;
  }
  function score(text, q){
    const t = norm(text), s = norm(q);
    if(!s) return 1;
    if(t===s) return 1e6;
    let sc=0;
    if(t.startsWith(s)) sc+=800;
    const re = new RegExp(`(?:^|\\s)${escapeRe(s)}`);
    if(re.test(t)) sc+=650;
    const idx = t.indexOf(s);
    if(idx>=0) sc += Math.max(0, 500-idx*4);
    sc += Math.max(0, 150 - Math.abs(t.length-s.length)*6);
    return sc;
  }
  function sortByScore(items, q){
    const filtered = items.filter(it=>matchesAllTokens(it.text,q));
    return filtered
      .map(it=>({it,sc:score(it.text,q)}))
      .sort((a,b)=> b.sc - a.sc || a.it.text.localeCompare(b.it.text))
      .map(x=>x.it);
  }
  function stableQS(obj){
    return Object.entries(obj)
      .filter(([,v])=> v!==undefined && v!==null)
      .sort(([a],[b])=> a.localeCompare(b))
      .map(([k,v])=> `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");
  }
  function getCached(cache, key){
    const hit = cache[key];
    if(!hit) return null;
    if(Date.now()-hit.ts > CACHE_TTL_MS){ delete cache[key]; return null; }
    return hit.data;
  }
  function setCached(cache, key, data){ cache[key] = { ts:Date.now(), data }; }

  function fastRender(box, rows){
    if(!rows.length){
      box.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
      box.style.display = "block"; return;
    }
    let html=""; for(const r of rows){
      html += `<div class="autocomplete-option" data-id="${r.id}"${
        r.precio!==undefined?` data-precio="${r.precio}"`:""
      }>${(r.text||"").trimStart()}</div>`;
    }
    box.innerHTML = html; box.style.display = "block";
  }

  const isVisible = el => !!(el && (el.offsetParent!==null || el.getClientRects().length));
  function moveFocusNext(current, explicitNext){
    let next = explicitNext;
    if(typeof explicitNext==="string") next = document.querySelector(explicitNext);
    if(typeof explicitNext==="function") next = explicitNext();
    if(next && isVisible(next) && !next.disabled){
      next.focus({preventScroll:false}); next.select?.(); return;
    }
    const root = current.closest("form") || document;
    const focusables = Array.from(
      root.querySelectorAll('input,select,textarea,button,[tabindex]:not([tabindex="-1"])')
    ).filter(el=>!el.disabled && isVisible(el));
    const idx = focusables.indexOf(current);
    const cand = focusables[idx+1];
    if(cand){ cand.focus({preventScroll:false}); cand.select?.(); }
  }

  function warmCache(cacheKey, url, paramsList){
    const cache = state.caches[cacheKey] || (state.caches[cacheKey]={});
    (async()=>{
      for(const params of paramsList){
        const qs = stableQS(params);
        const key = `${url}?${qs}`;
        if(getCached(cache,key)) continue;
        try{
          const res = await fetch(`${url}?${qs}`);
          if(!res.ok) continue;
          const data = await res.json();
          setCached(cache,key,data);
        }catch{}
      }
    })();
  }

  const state = { caches:{ proveedor:{}, sucursal:{}, producto:{}, caja:{}} };

  function addAutocomplete({
    inp, hidden, box, url,
    extraParams=()=>({}), before=()=>true, onSelect=null,
    nextFocus=null, showAllOnEmpty=true, debounceRemote=70
  }){
    const cache = state.caches[box.dataset.cacheKey] || (state.caches[box.dataset.cacheKey]={});
    let remotePage=1, remoteMore=true, remoteLoading=false;
    let inflight=null, currentTerm="", prevTerm="", indexKey="", index=[], lastFiltered=[];

    function contextKey(){
      const ep=extraParams()||{}; const ctx={...ep};
      delete ctx.term; delete ctx.page; delete ctx.page_size; delete ctx.excluded;
      return JSON.stringify(ctx);
    }
    function renderLocal(q){
      if(!index.length){
        box.innerHTML='<div class="autocomplete-no-result">Cargando…</div>';
        box.style.display="block"; return;
      }
      // 🔧 Cambio clave: si estamos ESCRIBIENDO MÁS (q empieza por prevTerm) usar lastFiltered;
      // si estamos BORRANDO (no empieza por prevTerm), usar TODO el índice.
      const base = (lastFiltered.length && q.startsWith(prevTerm)) ? lastFiltered : index;
      const ranked = sortByScore(base, q).slice(0,MAX_LOCAL_RESULTS);
      lastFiltered = ranked;
      fastRender(box, ranked);
    }
    function mergeIntoIndex(results){
      const used = new Set(detalles.map(d => String(d.productoid))); // evita duplicados para productos
      const seen = new Set(index.map(x=>String(x.id)));
      for(const r of (results||[])){
        const id = String(r.id);
        if(!seen.has(id)){
          // Para autocompletes de productos, evita ya agregados si el back no filtra
          if(box.dataset.cacheKey==="producto" && used.has(id)) continue;
          index.push(r); seen.add(id);
        }
      }
    }
    async function fetchRemote(page){
      if(remoteLoading || !before()) return;
      remoteLoading=true; inflight?.abort(); inflight = new AbortController();
      const params={ term:currentTerm, page, page_size:25, ...extraParams() };
      const qs = stableQS(params), key = `${url}?${qs}`;
      let data = getCached(cache,key);
      if(!data){
        try{
          const res = await fetch(`${url}?${qs}`, { signal:inflight.signal });
          if(!res.ok) throw new Error(`HTTP ${res.status}`);
          data = await res.json(); setCached(cache,key,data);
        }catch(err){
          if(err.name==="AbortError"){ remoteLoading=false; return; }
          data = {results:[], has_more:false};
        }
      }
      const ctx = contextKey();
      if(indexKey!==ctx){ indexKey=ctx; index=[]; lastFiltered=[]; }
      mergeIntoIndex(data.results);
      remoteMore = !!data.has_more; remoteLoading=false;
      renderLocal(currentTerm);
    }
    let tRemote;
    function scheduleRemote(page=1){ clearTimeout(tRemote); tRemote = setTimeout(()=>fetchRemote(page), debounceRemote); }

    function selectOption(opt){
      if(!opt) return;
      inp.value    = opt.textContent.trimStart();
      hidden.value = opt.dataset.id;
      box.style.display = "none";
      onSelect && onSelect(opt);
      setTimeout(()=> moveFocusNext(inp, nextFocus), 0);
    }

    inp.addEventListener("input", ()=>{
      hidden.value="";
      const newTerm = inp.value.trim();
      prevTerm = currentTerm;          // ⬅️ guardamos el término anterior
      currentTerm = newTerm;

      const ctx = contextKey();
      if(indexKey!==ctx){ indexKey=ctx; index=[]; lastFiltered=[]; remotePage=1; remoteMore=true; }

      // render local inmediato (tanto al escribir como al borrar)
      renderLocal(currentTerm);

      // remoto (primer page). Si vacío y showAllOnEmpty, carga listado base
      if(showAllOnEmpty && currentTerm.length===0){ remotePage=1; remoteMore=true; scheduleRemote(1); return; }
      scheduleRemote(1);
    });

    inp.addEventListener("focus", ()=>{
      prevTerm = "";                              // ⬅️ reinicia referencia
      currentTerm = inp.value.trim();
      const ctx = contextKey();
      if(indexKey!==ctx){ indexKey=ctx; index=[]; lastFiltered=[]; remotePage=1; remoteMore=true; }
      if(index.length) renderLocal(currentTerm);
      else { box.innerHTML='<div class="autocomplete-no-result">Cargando…</div>'; box.style.display="block"; }
      fetchRemote(1); IDLE(()=> fetchRemote(2));
    });

    box.addEventListener("scroll", ()=>{
      const near = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
      if(near && remoteMore && !remoteLoading){ remotePage++; fetchRemote(remotePage); }
    });

    box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
      selectOption(opt);
    });

    inp.addEventListener("keydown", e=>{
      if(e.key==="Enter"){
        if(box.style.display!=="none"){ e.preventDefault(); selectOption(box.querySelector(".autocomplete-option")); }
      }
      if(e.key==="Escape"){ box.style.display="none"; }
    });

    document.addEventListener("click", e=>{
      if(!inp.contains(e.target) && !box.contains(e.target)){ box.style.display="none"; }
    });
  }

  /* ───────── Instancias autocomplete + warm cache ───────── */
  // Proveedor
  addAutocomplete({
    inp:$id("id_proveedor_autocomplete"),
    hidden:$id("id_proveedor"),
    box:(()=>{const b=$id("proveedor-autocomplete-results"); b.dataset.cacheKey="proveedor"; return b;})(),
    url:proveedorAutocompleteUrl,
    onSelect: async opt => {
      const nuevoID = opt.dataset.id, nuevoNombre = opt.textContent.trim();
      if(nuevoID === proveedorActual) return;

      // Verifica cada producto contra el nuevo proveedor
      const checks = await Promise.all(detalles.map(async d=>{
        const qs = new URLSearchParams({ term:d.producto, proveedor_id:nuevoID });
        const js = await (await fetch(`${productoPedidoAutocompleteUrl}?${qs}`)).json();
        return { det:d, ok: js.results.some(r => String(r.id)===String(d.productoid)) };
      }));
      const conservar = checks.filter(c=>c.ok).map(c=>c.det);
      const eliminados = checks.length - conservar.length;

      let proceed = true;
      if(eliminados){
        proceed = confirm(`${eliminados} producto(s) no pertenecen al proveedor seleccionado y se eliminarán. ¿Continuar?`);
      }
      if(!proceed){
        $id("id_proveedor_autocomplete").value = proveedorNombre;
        $id("id_proveedor").value              = proveedorActual;
        return;
      }
      detalles = conservar.map(d => ({ ...d, proveedorid:nuevoID }));
      proveedorActual = nuevoID; proveedorNombre = nuevoNombre;
      $id("producto-input").value = ""; $id("producto-id").value = ""; precioSel = 0;
      drawTable();

      // Prefetch de productos del nuevo proveedor
      if(nuevoID){
        IDLE(()=> warmCache("producto", productoPedidoAutocompleteUrl, [
          {term:"", page:1, page_size:25, proveedor_id:nuevoID, excluded:""},
          {term:"", page:2, page_size:25, proveedor_id:nuevoID, excluded:""},
        ]));
      }
    },
    nextFocus:"#id_sucursal_autocomplete",
    showAllOnEmpty:true
  });

  // Sucursal
  addAutocomplete({
    inp:$id("id_sucursal_autocomplete"),
    hidden:$id("id_sucursal"),
    box:(()=>{const b=$id("sucursal-autocomplete-results"); b.dataset.cacheKey="sucursal"; return b;})(),
    url:sucursalAutocompleteUrl,
    nextFocus:"#producto-input",
    showAllOnEmpty:true
  });

  // Producto (depende de proveedor y excluye seleccionados)
  addAutocomplete({
    inp:$id("producto-input"),
    hidden:$id("producto-id"),
    box:(()=>{const b=$id("producto-autocomplete-results"); b.dataset.cacheKey="producto"; return b;})(),
    url:productoPedidoAutocompleteUrl,
    extraParams:()=>({
      proveedor_id:$id("id_proveedor").value.trim(),
      excluded:detalles.map(d=>d.productoid).join(",")
    }),
    before:()=>!!$id("id_proveedor").value.trim(),
    onSelect:o=>{ precioSel = parseFloat(o.dataset.precio)||0; },
    nextFocus:"#cantidad-input",
    showAllOnEmpty:true
  });

  // Caja de pago (depende de sucursal)
  addAutocomplete({
    inp:$id("id_caja_pago_autocomplete"),
    hidden:$id("id_caja_pagoid"),
    box:(()=>{const b=$id("caja-pago-autocomplete-results"); b.dataset.cacheKey="caja"; return b;})(),
    url:cajaPagoAutocompleteUrl,
    extraParams:()=>({ sucursal_id:$id("id_sucursal").value }),
    nextFocus:"#producto-input",
    showAllOnEmpty:true
  });

  // Warm cache inicial
  IDLE(()=>{
    warmCache("proveedor", proveedorAutocompleteUrl, [{term:"",page:1,page_size:25},{term:"",page:2,page_size:25}]);
    warmCache("sucursal",  sucursalAutocompleteUrl,  [{term:"",page:1,page_size:25}]);
  });

  /* ───────── Agregar producto (click o ENTER en cantidad) ───────── */
  function addCurrentProduct(){
    UI.clearFlashes(); UI.clearFieldErrors();
    let valid=true;
    if(!$id("id_proveedor").value.trim()){ UI.fieldError("proveedor","Seleccione un proveedor."); valid=false; }
    if(!$id("id_sucursal").value.trim()){  UI.fieldError("sucursal","Seleccione una sucursal."); valid=false; }
    const pid = $id("producto-id").value.trim();
    if(!pid){ UI.fieldError("producto","Seleccione un producto."); valid=false; }
    const rawQty = $id("cantidad-input").value.trim();
    const qty = parseInt(rawQty,10);
    if(!rawQty || isNaN(qty) || qty<1){ UI.fieldError("cantidad","Cantidad inválida."); valid=false; }
    if(!valid) return;

    const name = $id("producto-input").value.trim();
    const row  = detalles.find(d=> String(d.productoid)===String(pid));
    if(row){
      row.cantidad += qty;
      row.subtotal = row.cantidad * Number(row.precio_unitario||0);
    }else{
      const precio = Number(precioSel||0);
      detalles.push({
        detallepedidoid:`tmp-${Date.now()}`,
        productoid:pid, proveedorid:proveedorActual,
        producto:name, cantidad:qty,
        precio_unitario:precio,
        subtotal:precio*qty
      });
    }
    $id("producto-input").value=""; $id("producto-id").value="";
    $id("cantidad-input").value="1"; precioSel=0;
    drawTable();

    // volver a “producto” para flujo rápido
    $id("producto-input").focus(); $id("producto-input").select?.();
  }

  $id("agregarDetalleBtn").addEventListener("click", addCurrentProduct);
  $id("cantidad-input").addEventListener("keydown", e=>{
    if(e.key==="Enter"){ e.preventDefault(); addCurrentProduct(); }
  });

  /* ───────── Submit AJAX ───────── */
  $id("pedidoForm").addEventListener("submit", async e=>{
    e.preventDefault();
    UI.clearFlashes(); UI.clearFieldErrors();

    let valid=true;
    if(!$id("id_proveedor").value.trim()){ UI.fieldError("proveedor","Seleccione un proveedor."); valid=false; }
    if(!$id("id_sucursal").value.trim()){  UI.fieldError("sucursal","Seleccione una sucursal."); valid=false; }
    if(!$id("id_estado").value.trim()){    UI.fieldError("estado","Seleccione un estado."); valid=false; }
    if(!detalles.length){                  UI.fieldError("detalles","Agregue al menos un producto."); valid=false; }
    if(!valid) return;

    const fd = new FormData(e.target);
    fd.set("detalles", JSON.stringify(detalles));

    try{
      const res = await fetch(e.target.action, {
        method:"POST",
        headers:{
          "X-CSRFToken": document.cookie.match(/csrftoken=([^;]+)/)[1],
          "Accept":"application/json"
        },
        body: fd
      });

      if(!res.ok){
        const text = await res.text();
        console.error("Error 500 del servidor:", text);
        UI.flash("error","Error interno del servidor (revisa consola).");
        return;
      }

      const js = await res.json();
      if(js.success){
        window.location = visualizarPedidosUrl + "?updated=1";
      }else if(js.errors){
        Object.entries(js.errors).forEach(([f,arr])=> arr.forEach(eo=> UI.fieldError(f, eo.message)));
        UI.flash("error","Corrige los campos indicados.");
      }else{
        UI.flash("error", js.message || "Error al guardar.");
      }
    }catch(err){
      console.error(err);
      UI.flash("error","Error de red.");
    }
  });

})();
