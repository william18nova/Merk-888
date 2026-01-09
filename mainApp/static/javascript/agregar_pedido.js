/*  static/javascript/agregar_pedido.js
    ──────────────────────────────────────────────────────────────
    Ultra-rápido Autocomplete:
      • Render local inmediato (filtro por tokens + ranking)
      • Petición remota en paralelo (Abort + debounce corto)
      • Warm cache (prefetch en idle) para que “ya esté” al abrir
      • Prefetch del producto tras elegir proveedor
      • Enter = selecciona primera opción visible
      • Tras seleccionar → foco al siguiente input
      • Enter en “cantidad” = click en “Agregar producto”
---------------------------------------------------------------- */
(() => {
  "use strict";

  /* ───────── helpers DOM ───────── */
  const $id = id  => document.getElementById(id);
  const $qs = sel => document.querySelector(sel);

  /* ───────── DataTable ─────────── */
  const HEADERS = ["Producto","Cantidad","Precio U.","Subtotal","Acciones"];
  const dt = $("#detalle-pedido").DataTable({
    paging:false, searching:false, info:false, responsive:true,
    columnDefs:[{targets:4,orderable:false}],
    rowCallback: row =>
      $("td",row).each((i,td)=>td.setAttribute("data-label",HEADERS[i]))
  });

  /* ───────── state ─────────────── */
  const state = {
    detalles : [],
    priceSel : 0,
    caches   : { proveedor:{}, sucursal:{}, producto:{} }
  };

  /* ═════ UI helpers & errores ═════ */
  const ERR_BOX_MAP = {
    detalles:"detalles", producto:"detalles", cantidad:"cantidad",
    proveedor:"proveedor", proveedor_autocomplete:"proveedor",
    sucursal:"sucursal",  sucursal_autocomplete:"sucursal",
    fechaestimadaentrega:"fechaestimadaentrega"
  };
  const INPUT_MAP = {
    detalles:$id("producto-input"), producto:$id("producto-input"),
    cantidad:$id("cantidad-input"),
    proveedor:$id("id_proveedor_autocomplete"),
    proveedor_autocomplete:$id("id_proveedor_autocomplete"),
    sucursal:$id("id_sucursal_autocomplete"),
    sucursal_autocomplete:$id("id_sucursal_autocomplete"),
    fechaestimadaentrega:$id("id_fechaestimadaentrega")
  };

  const UI = {
    ok(msg){
      const b=$id("success-message");
      b.innerHTML=`<i class="fas fa-check-circle"></i> ${msg}`;
      b.style.display="block";
    },
    err(msg){
      const b=$id("error-message");
      b.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${msg}`;
      b.style.display="block";
    },
    clearAlerts(){
      ["success-message","error-message"].forEach(id=>{
        const e=$id(id); e.style.display="none"; e.innerHTML="";
      });
    },
    clearFieldErrors(){
      document.querySelectorAll(".field-error").forEach(b=>{
        b.classList.remove("visible"); b.innerHTML="";
      });
      document.querySelectorAll(".input-error")
        .forEach(i=>i.classList.remove("input-error"));
    },
    fieldError(field,msg){
      const key   = ERR_BOX_MAP[field] || field;
      let   box   = $qs(`#error-id_${key}`);
      const input = INPUT_MAP[field] || INPUT_MAP[key];

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

  /* ═════════════════════════════════════════════════════════════
     AUTOCOMPLETE core (más rápido)
     ═════════════════════════════════════════════════════════════ */
  const CACHE_TTL_MS = 60_000;
  const MAX_LOCAL_RESULTS = 50;
  const IDLE = window.requestIdleCallback || (fn => setTimeout(fn, 1));

  const norm = s => (s||"")
    .toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
    .trim();

  const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tokens = q => norm(q).split(/\s+/).filter(Boolean);

  function matchesAllTokens(text, q){
    const t = norm(text);
    const toks = tokens(q);
    if(!toks.length) return true;
    for(const k of toks){ if(!t.includes(k)) return false; }
    return true;
  }

  function score(text, q){
    const t = norm(text), s = norm(q);
    if(!s) return 1;
    if(t === s) return 1e6;
    let sc = 0;
    if(t.startsWith(s)) sc += 800;
    const re = new RegExp(`(?:^|\\s)${escapeRe(s)}`);
    if(re.test(t)) sc += 650;
    const idx = t.indexOf(s);
    if(idx >= 0) sc += Math.max(0, 500 - idx*4);
    sc += Math.max(0, 150 - Math.abs(t.length - s.length)*6);
    return sc;
  }

  function sortByScore(items, q){
    const filtered = items.filter(it => matchesAllTokens(it.text, q));
    return filtered
      .map(it => ({ it, sc: score(it.text, q) }))
      .sort((a,b)=> b.sc - a.sc || a.it.text.localeCompare(b.it.text))
      .map(x=>x.it);
  }

  function stableQS(obj){
    return Object.entries(obj)
      .filter(([,v]) => v !== undefined && v !== null)
      .sort(([a],[b]) => a.localeCompare(b))
      .map(([k,v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");
  }

  function getCached(cache, key){
    const hit = cache[key];
    if(!hit) return null;
    if(Date.now() - hit.ts > CACHE_TTL_MS){ delete cache[key]; return null; }
    return hit.data;
  }
  function setCached(cache, key, data){
    cache[key] = { ts: Date.now(), data };
  }

  // Warm cache (prefetch) en idle
  async function warmCache(cacheKey, url, paramsList){
    const cache = state.caches[cacheKey] || (state.caches[cacheKey]={});
    for(const params of paramsList){
      const qs = stableQS(params);
      const key = `${url}?${qs}`;
      if(getCached(cache, key)) continue;
      try{
        const res = await fetch(`${url}?${qs}`);
        if(!res.ok) continue;
        const data = await res.json();
        setCached(cache, key, data);
      }catch{ /* ignore */ }
    }
  }

  // Render muy rápido con innerHTML (menos overhead de nodos)
  function fastRender(box, rows){
    if(!rows.length){
      box.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
      box.style.display = "block";
      return;
    }
    let html = "";
    for(const r of rows){
      html += `<div class="autocomplete-option" data-id="${r.id}"${
        r.precio!==undefined?` data-precio="${r.precio}"`:""
      }>${(r.text||"").trimStart()}</div>`;
    }
    box.innerHTML = html;
    box.style.display = "block";
  }

  /* ───── focus helpers (pasar al siguiente input) ───── */
  const isVisible = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
  function moveFocusNext(current, explicitNext){
    let next = explicitNext;
    if(typeof explicitNext === "string") next = document.querySelector(explicitNext);
    if(typeof explicitNext === "function") next = explicitNext();
    if(next && isVisible(next) && !next.disabled){
      next.focus({preventScroll:false});
      if(typeof next.select === "function") next.select();
      return;
    }
    const root = current.closest("form") || document;
    const focusables = Array.from(
      root.querySelectorAll('input, select, textarea, button, [tabindex]:not([tabindex="-1"])')
    ).filter(el => !el.disabled && isVisible(el));
    const idx = focusables.indexOf(current);
    const candidate = focusables[idx+1];
    if(candidate){
      candidate.focus({preventScroll:false});
      if(typeof candidate.select === "function") candidate.select();
    }
  }

  function addAutocomplete({
    inp, hidden, box, url,
    extraParams=()=>({}), before=()=>true, onSelect=null,
    nextFocus=null,
    showAllOnEmpty=true, debounceRemote=70   // 👈 debounce reducido
  }){
    const cache = state.caches[box.dataset.cacheKey] || (state.caches[box.dataset.cacheKey] = {});
    let remotePage=1, remoteMore=true, remoteLoading=false;
    let inflight = null; // AbortController
    let currentTerm = "";
    let indexKey = "";   // contexto (p.ej., proveedor_id)
    let index = [];      // [{id,text,precio?}]
    let lastFiltered = []; // subíndice incremental

    function contextKey(){
      const ep = extraParams() || {};
      const ctx = {...ep}; delete ctx.term; delete ctx.page; delete ctx.page_size;
      return JSON.stringify(ctx);
    }

    function renderLocal(q){
      if(!index.length){
        box.innerHTML = '<div class="autocomplete-no-result">Cargando…</div>';
        box.style.display = "block";
        return;
      }
      // Filtro incremental: si el término actual es extensión del anterior,
      // filtramos desde el último subconjunto
      const base = (lastFiltered.length && q.startsWith(currentTerm)) ? lastFiltered : index;

      const ranked = sortByScore(base, q).slice(0, MAX_LOCAL_RESULTS);
      lastFiltered = ranked;
      fastRender(box, ranked);
    }

    function mergeIntoIndex(results){
      const seen = new Set(index.map(x=>String(x.id)));
      for(const r of results){
        const id = String(r.id);
        if(!seen.has(id)){ index.push(r); seen.add(id); }
      }
    }

    async function fetchRemote(page){
      if(remoteLoading || !before()) return;
      remoteLoading = true;
      inflight?.abort();
      inflight = new AbortController();

      const params = { term: currentTerm, page, page_size: 25, ...extraParams() };
      const qs  = stableQS(params);
      const key = `${url}?${qs}`;

      let data = getCached(cache, key);
      if(!data){
        try{
          const res = await fetch(`${url}?${qs}`, { signal: inflight.signal });
          if(!res.ok) throw new Error(`HTTP ${res.status}`);
          data = await res.json();
          setCached(cache, key, data);
        }catch(err){
          if(err.name === "AbortError"){ remoteLoading=false; return; }
          data = { results:[], has_more:false };
        }
      }

      const ctx = contextKey();
      if(indexKey !== ctx){
        indexKey = ctx; index = []; lastFiltered = []; // resetea si cambia el contexto
      }

      mergeIntoIndex(data.results || []);
      remoteMore    = !!data.has_more;
      remoteLoading = false;

      // re-render rápido con los nuevos datos
      renderLocal(currentTerm);
    }

    let tRemote;
    function scheduleRemote(page=1){
      clearTimeout(tRemote);
      tRemote = setTimeout(()=> fetchRemote(page), debounceRemote);
    }

    // --- helper: seleccionar (click/Enter) y pasar foco ---
    function selectOption(opt){
      if(!opt) return;
      inp.value    = opt.textContent.trimStart();
      hidden.value = opt.dataset.id;
      box.style.display = "none";
      onSelect && onSelect(opt);
      setTimeout(()=> moveFocusNext(inp, nextFocus), 0);
    }

    // Eventos input
    inp.addEventListener("input", ()=>{
      hidden.value = "";
      const newTerm = inp.value.trim();

      // Filtro local inmediato
      const wasPrefix = newTerm.startsWith(currentTerm);
      currentTerm = newTerm;

      const ctx = contextKey();
      if(indexKey !== ctx){ indexKey = ctx; index = []; lastFiltered = []; remotePage=1; remoteMore=true; }

      if(!index.length){
        box.innerHTML = '<div class="autocomplete-no-result">Cargando…</div>';
        box.style.display = "block";
      }else{
        if(wasPrefix){
          // uso incremental
          const ranked = sortByScore(lastFiltered.length?lastFiltered:index, currentTerm).slice(0, MAX_LOCAL_RESULTS);
          lastFiltered = ranked;
          fastRender(box, ranked);
        }else{
          renderLocal(currentTerm);
        }
      }

      // fetch remoto
      if(showAllOnEmpty && currentTerm.length === 0){
        remotePage=1; remoteMore=true; scheduleRemote(1);
        return;
      }
      scheduleRemote(1);
    });

    // Al enfocar: render de lo que haya + fetch inmediato
    inp.addEventListener("focus", ()=>{
      currentTerm = inp.value.trim();
      const ctx = contextKey();
      if(indexKey !== ctx){ indexKey = ctx; index = []; lastFiltered = []; remotePage=1; remoteMore=true; }

      if(index.length){
        renderLocal(currentTerm);
      }else{
        box.innerHTML = '<div class="autocomplete-no-result">Cargando…</div>';
        box.style.display = "block";
      }
      // render instantáneo y fetch muy pronto
      fetchRemote(1);
      // prefetch de la siguiente página (en background) para que el scroll sea inmediato
      IDLE(()=> fetchRemote(2));
    });

    // Scroll infinito
    box.addEventListener("scroll", ()=>{
      const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
      if(nearBottom && remoteMore && !remoteLoading){
        remotePage++; fetchRemote(remotePage);
      }
    });

    // Selección con click
    box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option");
      if(!opt) return;
      selectOption(opt);
    });

    // Teclado: Enter = primera opción visible, Escape = cerrar
    inp.addEventListener("keydown", e=>{
      if(e.key === "Enter"){
        if(box.style.display !== "none"){
          e.preventDefault();
          const first = box.querySelector(".autocomplete-option");
          selectOption(first);
        }
      }
      if(e.key === "Escape"){
        box.style.display = "none";
      }
    });

    // Cerrar al hacer click afuera
    document.addEventListener("click", e=>{
      if(!inp.contains(e.target) && !box.contains(e.target)){
        box.style.display = "none";
      }
    });
  }

  /* ═════ Instancias autocomplete (con nextFocus + warm cache) ═════ */
  addAutocomplete({
    inp:$id("id_proveedor_autocomplete"),
    hidden:$id("id_proveedor"),
    box:(()=>{const b=$id("proveedor-autocomplete-results");
              b.dataset.cacheKey="proveedor";return b;})(),
    url:proveedorAutocompleteUrl,
    onSelect:()=>{
      state.caches.producto={}; state.detalles=[]; dt.clear().draw();
      $id("producto-input").value=""; $id("producto-id").value="";
      state.priceSel=0; UI.clearAlerts(); UI.clearFieldErrors();
      // Prefetch de productos para el proveedor seleccionado (más rápido al abrir)
      const pid = $id("id_proveedor").value.trim();
      if(pid){
        IDLE(()=> warmCache("producto", productoPedidoAutocompleteUrl, [
          {term:"", page:1, page_size:25, proveedor_id:pid, excluded:""},
          {term:"", page:2, page_size:25, proveedor_id:pid, excluded:""}
        ]));
      }
    },
    nextFocus:"#id_sucursal_autocomplete",
    showAllOnEmpty:true,
  });

  addAutocomplete({
    inp:$id("id_sucursal_autocomplete"),
    hidden:$id("id_sucursal"),
    box:(()=>{const b=$id("sucursal-autocomplete-results");
              b.dataset.cacheKey="sucursal";return b;})(),
    url:sucursalAutocompleteUrl,
    nextFocus:"#producto-input",
    showAllOnEmpty:true
  });

  addAutocomplete({
    inp:$id("producto-input"),
    hidden:$id("producto-id"),
    box:(()=>{const b=$id("producto-autocomplete-results");
              b.dataset.cacheKey="producto";return b;})(),
    url:productoPedidoAutocompleteUrl,
    extraParams:()=>({
      proveedor_id:$id("id_proveedor").value.trim(),
      excluded:state.detalles.map(d=>d.productoid).join(",")
    }),
    before:()=>!!$id("id_proveedor").value.trim(),
    onSelect:o=>{ state.priceSel=parseFloat(o.dataset.precio)||0; },
    nextFocus:"#cantidad-input",
    showAllOnEmpty:true
  });

  // Warm cache inicial (proveedor + sucursal) para “cero-latencia” al abrir
  IDLE(()=>{
    warmCache("proveedor", proveedorAutocompleteUrl, [
      {term:"", page:1, page_size:25},
      {term:"", page:2, page_size:25}
    ]);
    warmCache("sucursal", sucursalAutocompleteUrl, [
      {term:"", page:1, page_size:25}
    ]);
  });

  /* ═════ util money ═════ */
  const money = n =>
    new Intl.NumberFormat("es-CO",{style:"currency",currency:"COP"}).format(n);

  function redrawTable(){
    dt.clear(); let total=0;
    state.detalles.forEach(d=>{
      total+=d.subtotal;
      dt.row.add([
        d.producto,
        d.cantidad,
        money(d.precio_unitario),
        money(d.subtotal),
        `<button type="button" class="btn-eliminar" data-id="${d.productoid}">
           <i class="fas fa-trash-alt"></i>
         </button>`
      ]);
    });
    dt.draw(false);
    $qs("#total-valor").textContent = money(total);
  }

  /* ═════ Añadir producto (reutilizable) ═════ */
  function addCurrentProduct(){
    UI.clearAlerts(); UI.clearFieldErrors();
    let valid=true;

    if(!$id("id_proveedor").value.trim()){
      UI.fieldError("proveedor","Seleccione un proveedor."); valid=false;
    }
    if(!$id("id_sucursal").value.trim()){
      UI.fieldError("sucursal","Seleccione una sucursal."); valid=false;
    }
    const pid=$id("producto-id").value.trim();
    if(!pid){ UI.fieldError("producto","Seleccione un producto."); valid=false; }

    const rawQty=$id("cantidad-input").value.trim();
    const qty=parseInt(rawQty,10);
    if(!rawQty || isNaN(qty) || qty<1){
      UI.fieldError("cantidad","Cantidad inválida."); valid=false;
    }
    if(!valid) return;

    const pu=state.priceSel;
    const existing=state.detalles.find(r=>r.productoid===pid);
    if(existing){
      existing.cantidad+=qty;
      existing.subtotal=existing.cantidad*existing.precio_unitario;
    }else{
      state.detalles.push({
        productoid:pid,
        producto:$id("producto-input").value.trim(),
        cantidad:qty,
        precio_unitario:pu,
        subtotal:pu*qty
      });
    }
    state.caches.producto={}; redrawTable();
    $id("producto-input").value=""; $id("producto-id").value="";
    $id("cantidad-input").value="1"; state.priceSel=0;

    // encadenar: vuelve a "producto"
    $id("producto-input").focus();
    $id("producto-input").select?.();
  }

  // Click en botón = agregar
  $id("agregarDetalleBtn").addEventListener("click", addCurrentProduct);

  // ENTER en cantidad = agregar (igual que click)
  $id("cantidad-input").addEventListener("keydown", e=>{
    if(e.key === "Enter"){
      e.preventDefault();
      addCurrentProduct();
    }
  });

  /* ═════ Eliminar fila ═════ */
  $("#detalle-pedido tbody").on("click",".btn-eliminar",function(){
    const pid=this.dataset.id;
    state.detalles=state.detalles.filter(r=>r.productoid!==pid);
    state.caches.producto={};
    dt.row($(this).parents("tr")).remove().draw(false);
    redrawTable();
  });

  /* ═════ Submit form ═════ */
  $id("pedidoForm").addEventListener("submit",async e=>{
    e.preventDefault(); UI.clearAlerts(); UI.clearFieldErrors();
    let valid=true;
    if(!$id("id_proveedor").value.trim()){
      UI.fieldError("proveedor","Seleccione un proveedor."); valid=false;
    }
    if(!$id("id_sucursal").value.trim()){
      UI.fieldError("sucursal","Seleccione una sucursal."); valid=false;
    }
    if(!state.detalles.length){
      UI.fieldError("detalles","Agregue al menos un producto."); valid=false;
    }
    if(!valid) return;

    $id("id_detalles").value = JSON.stringify(state.detalles);

    try{
      const r=await fetch(e.target.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":document.cookie.split(";")
                         .find(c=>c.trim().startsWith("csrftoken=")).split("=")[1],
          "Accept":"application/json"
        },
        body:new FormData(e.target)
      });
      const js=await r.json();
      if(js.success){
        UI.ok(js.message||"Pedido guardado exitosamente.");
        e.target.reset(); state.detalles=[]; dt.clear().draw(); redrawTable();
        state.caches.producto={};
      }else if(js.errors){
        const errs=JSON.parse(js.errors);
        Object.entries(errs)
          .forEach(([f,arr])=>arr.forEach(er=>UI.fieldError(f,er.message)));
      }else UI.err(js.message||("Error desconocido."));
    }catch(err){ console.error(err); UI.err("Error de red."); }
  });

  /* ═════ Date-picker ═════ */
  const fecha=$id("id_fechaestimadaentrega");
  if(fecha && fecha.showPicker){
    ["mousedown","focus"].forEach(evt=>{
      fecha.addEventListener(evt, e=>{ e.preventDefault(); fecha.showPicker(); });
    });
  }

})();
