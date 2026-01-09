/*  static/javascript/editar_inventario.js
    ───────────────────────────────────────────────
    Variante "Editar" con autocompletes instantáneos + navegación con ENTER:
      • Filtro local inmediato + fetch rápido (debounce)
      • Caché por término/página (productos incluyen excluded)
      • AbortController (si existe) para cancelar peticiones previas
      • Solo muestra el dropdown del autocomplete enfocado
      • ENTER:
          - en autocomplete: selecciona 1ª opción y pasa al siguiente input
          - en input normal: pasa al siguiente input
          - en último input: actúa como “Agregar Producto”
      • Mantiene precarga de filas, agregar/eliminar y submit con redirect
------------------------------------------------------------------*/
(() => {
  "use strict";

  /* ───────── helpers ───────── */
  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);
  const hasAbort = typeof window.AbortController === "function";

  /* ───────── refs y estado base ───────── */
  const dom = {
    form      : $id('inventarioForm'),
    sucInp    : $id('id_sucursal_autocomplete'),
    sucHid    : $id('id_sucursal'),
    sucBox    : $id('sucursal-autocomplete-results'),

    prdInp    : $id('id_producto_autocomplete'),
    prdHid    : $id('id_productoid'),
    prdBox    : $id('producto-autocomplete-results'),

    qtyInp    : $id('id_cantidad'),
    btnAdd    : $id('agregarProductoBtn'),
    rowsWrap  : $id('productos-body'),

    alertErr  : $id('error-message'),
  };

  const state = {
    suc : { page:1, term:'', loading:false, more:true, list:[], ctrl:null },
    prd : { page:1, term:'', loading:false, more:true, list:[], ctrl:null },
    items : [] // [{ productId, productName, cantidad }]
  };

  /* ───────── 1) Precarga filas existentes ───────── */
  $qsa('#productos-body tr').forEach(tr=>{
    const pid  = tr.dataset.productId;
    const name = tr.children[0].textContent.trim();
    const qty  = tr.querySelector('.qty-input')?.value.trim() || '1';
    state.items.push({ productId: pid, productName: name, cantidad: qty });
  });

  /* ───────── 2) DataTable ───────── */
  const dataTable = $('#productos-list').DataTable({
    paging    : false,
    searching : true,
    info      : false,
    responsive: true,
    language  : {
      search      : 'Buscar:',
      zeroRecords : 'No se encontraron resultados',
      emptyTable  : 'No hay productos para mostrar'
    }
  });

  /* ───────── 3) UI helpers ───────── */
  const UI = {
    clearAlerts(){
      if (dom.alertErr) { dom.alertErr.style.display='none'; dom.alertErr.innerHTML=''; }
      $qsa('.field-error').forEach(d=>{ d.classList.remove('visible'); d.textContent=''; });
      $qsa('.input-error').forEach(i=>i.classList.remove('input-error'));
    },
    err(msg){
      dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
      dom.alertErr.style.display='block';
    },
    fieldError(field,msg){
      const box = $qs(`#error-id_${field}`);
      if (box){
        box.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
        box.classList.add('visible');
      }
      const map = { sucursal: dom.sucInp, productoid: dom.prdInp, cantidad: dom.qtyInp };
      const input = map[field] || $qs(`#id_${field}`);
      if (input) input.classList.add('input-error');
    }
  };

  /* ───────── 4) Caché ───────── */
  const cacheSucursal = Object.create(null);
  const cacheProducto = Object.create(null);
  const cacheKey = (term, page, extra="") => `${(term||"").trim().toLowerCase()}|${page}|${extra}`;

  /* ───────── 5) debounce ───────── */
  const debounce = (fn, ms=80) => { let t; return (...args)=>{ clearTimeout(t); t=setTimeout(()=>fn(...args),ms); }; };

  /* ───────── 6) Autocomplete instantáneo ───────── */
  const autos = {}; // guardamos instancias para usarlas con ENTER

  function Autocomplete(kind){
    const cfg = (kind==='suc') ? {
      inp:dom.sucInp, hid:dom.sucHid, box:dom.sucBox,
      state:state.suc, cache:cacheSucursal,
      buildUrl:(term,page)=> `${sucursalAutocompleteUrl}?current_sucursal_id=${encodeURIComponent(window.currentSucursalId||"")}&term=${encodeURIComponent(term)}&page=${page}`,
      extraKey: () => ""              // no extra para sucursal
    } : {
      inp:dom.prdInp, hid:dom.prdHid, box:dom.prdBox,
      state:state.prd, cache:cacheProducto,
      buildUrl:(term,page)=>{
        const excluded = state.items.length ? `&excluded=${state.items.map(i=>i.productId).join(',')}` : '';
        return `${productoAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}${excluded}`;
      },
      extraKey: () => state.items.length ? state.items.map(i=>i.productId).join(',') : ""
    };

    // utilidad para pintar la lista
    const drawItems = (items, replace=true) => {
      if (replace) cfg.box.innerHTML = "";
      if (items && items.length){
        const frag = document.createDocumentFragment();
        items.forEach(r=>{
          const div = document.createElement('div');
          div.className = 'autocomplete-option';
          div.dataset.id = r.id;
          div.textContent = r.text;
          frag.appendChild(div);
        });
        cfg.box.appendChild(frag);
      }else{
        cfg.box.innerHTML = '<div class="autocomplete-no-result">No se encontraron resultados</div>';
      }
      if (document.activeElement === cfg.inp){ cfg.box.style.display='block'; }
    };

    // filtro local instantáneo
    const immediateFilter = () => {
      const term = cfg.state.term.trim().toLowerCase();
      if (!term){
        if (cfg.state.list.length){ drawItems(cfg.state.list, true); }
        else cfg.box.style.display='none';
        return;
      }
      if (!cfg.state.list.length) return;
      const filtered = cfg.state.list.filter(r => r.text.toLowerCase().includes(term));
      drawItems(filtered, true);
    };

    // fetch con caché + abort
    const fetchPage = async (page=1) => {
      const term  = cfg.state.term;
      const extra = cfg.extraKey();
      const key   = cacheKey(term, page, extra);

      // pintar desde caché para feedback inmediato
      if (cfg.cache[key]){
        const data = cfg.cache[key];
        if (page === 1) cfg.state.list = data.results.slice(0);
        drawItems(data.results, page === 1);
        cfg.state.more = !!data.has_more;
      }

      // cancelar petición anterior
      if (hasAbort){
        try{ cfg.state.ctrl?.abort(); }catch(_e){}
        cfg.state.ctrl = new AbortController();
      }else{
        cfg.state.ctrl = null;
      }

      try{
        cfg.state.loading = true;
        const resp = await fetch(cfg.buildUrl(term, page), hasAbort ? { signal: cfg.state.ctrl.signal } : undefined);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        cfg.cache[key] = data;

        // si cambió el término mientras esperábamos, no pintes
        if (term !== cfg.state.term) return;

        if (page === 1) cfg.state.list = data.results.slice(0);
        drawItems(data.results, page === 1);
        cfg.state.more = !!data.has_more;
      }catch(e){
        if (e.name !== 'AbortError') console.error(e);
      }finally{
        cfg.state.loading = false;
      }
    };

    const kickFetch = debounce(()=>{ cfg.state.page=1; cfg.state.more=true; fetchPage(1); }, 80);

    /* eventos del input */
    cfg.inp.addEventListener('input', ()=>{
      cfg.hid.value = '';
      cfg.state.term = cfg.inp.value;
      immediateFilter();   // respuesta inmediata
      kickFetch();         // revalidación al servidor
    });

    cfg.inp.addEventListener('focus', ()=>{
      // ocultar el otro dropdown si existe
      [dom.sucBox, dom.prdBox].forEach(b=>{ if (b !== cfg.box) b.style.display='none'; });

      cfg.state.term = cfg.inp.value;
      if (cfg.state.list.length) drawItems(cfg.state.list, true); else cfg.box.style.display='block';
      cfg.state.page=1; cfg.state.more=true;
      fetchPage(1);
    });

    // cerrar si sale del input (pero permite click en opciones)
    cfg.inp.addEventListener('blur', ()=>{
      setTimeout(()=>{ if (!cfg.box.matches(':hover')) cfg.box.style.display='none'; }, 120);
    });

    // scroll infinito
    cfg.box.addEventListener('scroll', ()=>{
      if (cfg.box.scrollTop + cfg.box.clientHeight >= cfg.box.scrollHeight - 4){
        if (cfg.state.more && !cfg.state.loading){
          cfg.state.page += 1; fetchPage(cfg.state.page);
        }
      }
    });

    // seleccionar opción
    function selectOption(el){
      if (!el) return false;
      cfg.inp.value = el.textContent;
      cfg.hid.value = el.dataset.id || "";
      cfg.box.style.display='none';
      return true;
    }
    cfg.box.addEventListener('click', e=> selectOption(e.target.closest('.autocomplete-option')));

    // click fuera: cierra
    document.addEventListener('click', e=>{
      if (!cfg.inp.contains(e.target) && !cfg.box.contains(e.target)){
        cfg.box.style.display='none';
      }
    });

    // API pública para ENTER
    function selectFirstVisible(){
      const first = cfg.box.querySelector('.autocomplete-option');
      if (first) return selectOption(first);
      // fallback a lista en memoria
      if (cfg.state.list.length){
        cfg.inp.value = cfg.state.list[0].text;
        cfg.hid.value = cfg.state.list[0].id;
        cfg.box.style.display='none';
        return true;
      }
      return false;
    }

    const api = { ...cfg, selectFirstVisible };
    autos[kind] = api;
    return api;
  }

  const autoSuc = Autocomplete('suc');
  const autoPrd = Autocomplete('prd');

  /* ───────── 7) ENTER: navegación entre campos ───────── */
  const focusOrder = [ dom.sucInp, dom.prdInp, dom.qtyInp ].filter(Boolean);

  function focusNext(fromEl){
    const idx = focusOrder.indexOf(fromEl);
    const isLast = (idx === focusOrder.length - 1);
    if (isLast){
      dom.btnAdd?.click();
    }else{
      const next = focusOrder[idx + 1];
      next?.focus();
      // coloca el cursor al final
      if (next?.setSelectionRange) {
        const len = next.value.length;
        next.setSelectionRange(len, len);
      }
    }
  }

  function handleEnterFor(el, e){
    if (e.key !== 'Enter') return;
    e.preventDefault();

    if (el === dom.sucInp){
      // seleccionar 1ª opción de sucursal y pasar
      autoSuc.selectFirstVisible();
      focusNext(el);
      return;
    }
    if (el === dom.prdInp){
      // seleccionar 1ª opción de producto y pasar
      autoPrd.selectFirstVisible();
      focusNext(el);
      return;
    }
    // input normal (cantidad)
    focusNext(el);
  }

  focusOrder.forEach(inp=>{
    inp?.addEventListener('keydown', e => handleEnterFor(inp, e));
  });

  /* ───────── 8) Agregar fila ───────── */
  dom.btnAdd.addEventListener('click', ()=>{
    UI.clearAlerts();

    const sid=dom.sucHid.value.trim();
    const pid=dom.prdHid.value.trim();
    const qty=dom.qtyInp.value.trim();
    const pname=dom.prdInp.value.trim();

    let bad=false;
    if (!sid){ UI.fieldError('sucursal','Debe seleccionar una sucursal.'); bad=true; }
    if (!pid){ UI.fieldError('productoid','Debe seleccionar un producto.'); bad=true; }
    if (!qty || qty<=0){ UI.fieldError('cantidad','Cantidad debe ser mayor que 0.'); bad=true; }
    if (bad) return;

    if (state.items.some(i=>i.productId===pid)){
      UI.fieldError('productoid','Este producto ya está en la lista.'); return;
    }

    state.items.push({ productId:pid, productName:pname, cantidad:qty });

    dataTable.row.add([
      pname,
      `<input type="number" class="qty-input" min="1" value="${qty}">`,
      `<button type="button" class="btn-eliminar" data-product-id="${pid}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false);

    dom.prdInp.value=''; dom.prdHid.value=''; dom.qtyInp.value='';
    dom.prdInp.focus();
  });

  /* ───────── 9) Eliminar fila ───────── */
  dom.rowsWrap.addEventListener('click',e=>{
    const btn=e.target.closest('.btn-eliminar'); if (!btn) return;
    const pid=btn.dataset.productId;
    state.items=state.items.filter(i=>i.productId!==pid);
    dataTable.row(btn.closest('tr')).remove().draw(false);
  });

  /* ───────── 10) Submit ───────── */
  dom.form.addEventListener('submit',async ev=>{
    ev.preventDefault();
    UI.clearAlerts();

    if (!state.items.length){
      UI.err('Debe agregar al menos un producto.'); return;
    }

    /* sincronizar cantidades editadas */
    dataTable.rows().every(function(){
      const [prod, qtyCell] = this.node().querySelectorAll('td');
      const item = state.items.find(i=>i.productName===prod.textContent.trim());
      const inp  = qtyCell.querySelector('.qty-input');
      if (item && inp) item.cantidad = inp.value.trim();
    });

    $id('id_inventarios_temp').value = JSON.stringify(state.items);

    try{
      const resp = await fetch(dom.form.action,{
        method:'POST',
        headers:{
          'X-CSRFToken': document.cookie.split(';').find(c=>c.trim().startsWith('csrftoken='))?.split('=')[1] || '',
          'Accept':'application/json'
        },
        body:new FormData(dom.form)
      });
      const data = await resp.json();

      if (data.success){
        window.location.href = data.redirect_url || window.location.href;
      }else{
        const errs = JSON.parse(data.errors || '{}');
        Object.entries(errs).forEach(([field, arr])=>{
          arr.forEach(e=>UI.fieldError(field, e.message));
        });
      }
    }catch(err){
      console.error(err);
      UI.err('Ocurrió un error inesperado.');
    }
  });
})();
