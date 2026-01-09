/*  static/javascript/agregar_inventario.js
    • Autocomplete instantáneo + scroll infinito + caché
    • Muestra opciones SOLO si el input está enfocado
    • Enter: avanza, elige 1ª opción en autocomplete, y al final agrega
------------------------------------------------------------------*/
(() => {
  "use strict";

  /* ======== helpers generales ======== */
  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);

  const hasAbort = typeof window.AbortController === "function";

  // ✅ URLs (robusto: funciona si están en window.* o como const global)
  const SUCURSAL_URL =
    (typeof window.sucursalAutocompleteUrl !== "undefined" && window.sucursalAutocompleteUrl) ? window.sucursalAutocompleteUrl :
    (typeof sucursalAutocompleteUrl !== "undefined" && sucursalAutocompleteUrl) ? sucursalAutocompleteUrl :
    null;

  const PRODUCTO_URL =
    (typeof window.productoAutocompleteUrl !== "undefined" && window.productoAutocompleteUrl) ? window.productoAutocompleteUrl :
    (typeof productoAutocompleteUrl !== "undefined" && productoAutocompleteUrl) ? productoAutocompleteUrl :
    null;

  if (!SUCURSAL_URL || !PRODUCTO_URL) {
    console.error("❌ Faltan URLs del autocomplete. Revisa sucursalAutocompleteUrl / productoAutocompleteUrl (window o global).");
  }

  // Normaliza: quita acentos y pasa a minúscula
  const norm = (s) => (s || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

  /* ======== DataTable de la lista ======== */
  const dataTable = window.jQuery('#productos-list').DataTable({
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

  const COL_LABELS = ["Producto", "Cantidad", "Acciones"];
  function setDataLabels($row){
    window.jQuery('td', $row).each(function(i){
      this.setAttribute('data-label', COL_LABELS[i] || "");
    });
  }
  window.jQuery('#productos-list').on('draw.dt', function(){
    window.jQuery('#productos-list tbody tr').each(function(){
      setDataLabels(window.jQuery(this));
    });
  });

  /* ======== refs DOM ======== */
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
    alertOk   : $id('success-message'),
  };

  const state = {
    suc : { page:1, term:'', loading:false, more:true, ctrl:null, focused:false },
    prd : { page:1, term:'', loading:false, more:true, ctrl:null, focused:false },
    items : [] // [{ productId, productName, cantidad }]
  };

  /* ======== UI helpers ======== */
  const UI = {
    clearAlerts(){
      [dom.alertOk, dom.alertErr].forEach(a=>{
        if (!a) return;
        a.style.display='none';
        a.innerHTML='';
      });
    },
    ok(msg){
      dom.alertOk.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`;
      dom.alertOk.style.display='block';
    },
    err(msg){
      dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
      dom.alertErr.style.display='block';
    },
    clearFieldErrors(){
      $qsa('.field-error').forEach(d=>{ d.classList.remove('visible'); d.textContent=''; });
      $qsa('.input-error').forEach(inp=>inp.classList.remove('input-error'));
    },
    fieldError(field, msg){
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

  /* ======== caché en memoria ======== */
  const cacheSucursal = Object.create(null);
  const cacheProducto = Object.create(null);
  const cacheKey = (term, page, extra="") => `${(term||"").trim().toLowerCase()}|${page}|${extra}`;

  const debounce = (fn, ms=120) => { let t; return (...args)=>{ clearTimeout(t); t=setTimeout(()=>fn(...args),ms); }; };
  const abortCtrl = c => { try{ c?.abort(); }catch(_){} };

  const hideBox = el => { if (!el) return; el.style.display='none'; };
  const showBox = el => { if (!el) return; el.style.display='block'; };

  function hideAllExcept(kind){
    if (kind === 'suc'){
      hideBox(dom.prdBox); abortCtrl(state.prd.ctrl); state.prd.focused = false;
    } else if (kind === 'prd'){
      hideBox(dom.sucBox); abortCtrl(state.suc.ctrl); state.suc.focused = false;
    } else {
      hideBox(dom.sucBox); hideBox(dom.prdBox);
      state.suc.focused = state.prd.focused = false;
    }
  }

  /* ======== ENTER NAV ======== */
  const NAV_ORDER = ['suc','prd','qty'];
  const getByKey = key => key==='suc' ? dom.sucInp : key==='prd' ? dom.prdInp : dom.qtyInp;

  function focusNext(fromKey){
    const idx = NAV_ORDER.indexOf(fromKey);
    if (idx < 0) return;
    if (idx < NAV_ORDER.length - 1){
      getByKey(NAV_ORDER[idx + 1]).focus();
    } else {
      dom.btnAdd.click();
    }
  }

  function selectFirstAndAdvance(kind){
    const box = (kind === 'suc') ? dom.sucBox : dom.prdBox;
    const inp = (kind === 'suc') ? dom.sucInp : dom.prdInp;
    const hid = (kind === 'suc') ? dom.sucHid : dom.prdHid;

    const first = box?.querySelector('.autocomplete-option');
    if (first){
      inp.value = first.textContent;
      hid.value = first.dataset.id;
    }
    hideBox(box);
    focusNext(kind);
  }

  function Autocomplete(kind){
    const cfg = (kind === 'suc') ? {
      inp : dom.sucInp, hid : dom.sucHid, box : dom.sucBox,
      state : state.suc, cache : cacheSucursal,
      url: (term,page)=> `${SUCURSAL_URL}?term=${encodeURIComponent(term)}&page=${page}`,
      extraKey: () => ""
    } : {
      inp : dom.prdInp, hid : dom.prdHid, box : dom.prdBox,
      state : state.prd, cache : cacheProducto,
      url: (term,page)=>{
        const excluded = state.items.length ? `&excluded=${state.items.map(i=>i.productId).join(',')}` : '';
        return `${PRODUCTO_URL}?term=${encodeURIComponent(term)}&page=${page}${excluded}`;
      },
      extraKey: () => state.items.length ? state.items.map(i=>i.productId).join(',') : ""
    };

    if (!cfg.inp || !cfg.box) return;

    const canShow = () => cfg.state.focused && document.activeElement === cfg.inp;

    function draw(items){
      if (!canShow()) return;

      cfg.box.innerHTML = "";

      if (!items.length){
        cfg.box.innerHTML = `<div class="autocomplete-no-result">No se encontraron resultados</div>`;
        showBox(cfg.box);
        return;
      }

      const frag = document.createDocumentFragment();
      items.forEach(r=>{
        const div = document.createElement('div');
        div.className = 'autocomplete-option';
        div.dataset.id = r.id;
        div.textContent = r.text;
        frag.appendChild(div);
      });
      cfg.box.appendChild(frag);
      showBox(cfg.box);
    }

    async function fetchPage(page=1){
      const term = cfg.state.term;
      const key = cacheKey(term, page, cfg.extraKey());

      if (cfg.cache[key] && canShow()){
        draw(cfg.cache[key].results || []);
      }

      if (hasAbort){
        abortCtrl(cfg.state.ctrl);
        cfg.state.ctrl = new AbortController();
      } else cfg.state.ctrl = null;

      try{
        cfg.state.loading = true;
        const resp = await fetch(cfg.url(term, page), hasAbort ? { signal: cfg.state.ctrl.signal } : undefined);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        if (term !== cfg.state.term) return;

        cfg.cache[key] = data;
        cfg.state.more = !!data.has_more;
        cfg.state.page = page;

        if (!canShow()) return;
        draw((data.results || []).slice(0));
      }catch(e){
        if (e.name !== 'AbortError') console.error(e);
      }finally{
        cfg.state.loading = false;
      }
    }

    const kickFetch = debounce(()=> fetchPage(1), 120);

    cfg.inp.addEventListener('input', ()=>{
      cfg.hid.value = '';
      cfg.state.term = cfg.inp.value;
      kickFetch();
    });

    cfg.inp.addEventListener('focus', ()=>{
      hideAllExcept(kind);
      cfg.state.focused = true;
      cfg.state.term = cfg.inp.value;
      fetchPage(1); // term vacío también debe traer
    });

    cfg.inp.addEventListener('blur', ()=>{
      cfg.state.focused = false;
      setTimeout(()=> hideBox(cfg.box), 140);
    });

    cfg.inp.addEventListener('keydown', (e)=>{
      if (e.key === 'Escape'){
        cfg.state.focused = false;
        hideBox(cfg.box);
      }
    });

    cfg.inp.addEventListener('keydown', (e)=>{
      if (e.key !== 'Enter') return;
      e.preventDefault();
      selectFirstAndAdvance(kind);
    });

    cfg.box.addEventListener('mousedown', e=> e.preventDefault());

    cfg.box.addEventListener('click', e=>{
      const opt = e.target.closest('.autocomplete-option');
      if (!opt) return;
      cfg.inp.value = opt.textContent;
      cfg.hid.value = opt.dataset.id;
      hideBox(cfg.box);
      focusNext(kind);
    });
  }

  Autocomplete('suc');
  Autocomplete('prd');

  dom.qtyInp.addEventListener('keydown', (e)=>{
    if (e.key === 'Enter'){ e.preventDefault(); dom.btnAdd.click(); }
  });

  dom.form.addEventListener('keydown', (e)=>{
    if (e.key === 'Enter' && e.target !== dom.qtyInp && e.target !== dom.sucInp && e.target !== dom.prdInp){
      e.preventDefault();
      const key =
        (e.target === dom.sucInp) ? 'suc' :
        (e.target === dom.prdInp) ? 'prd' :
        (e.target === dom.qtyInp) ? 'qty' : null;
      if (key) focusNext(key);
    }
  });

  /* ======== Agregar a la tabla ======== */
  dom.btnAdd.addEventListener('click', ()=>{
    UI.clearAlerts(); UI.clearFieldErrors();

    const sid = dom.sucHid.value.trim();
    const pid = dom.prdHid.value.trim();
    const qty = dom.qtyInp.value.trim();
    const pname = dom.prdInp.value.trim();

    let bad=false;
    if (!sid){ UI.fieldError('sucursal','Debe seleccionar una sucursal.'); bad=true; }
    if (!pid){ UI.fieldError('productoid','Debe seleccionar un producto.'); bad=true; }
    if (!qty || qty<=0){ UI.fieldError('cantidad','Cantidad debe ser mayor que 0.'); bad=true; }
    if (bad) return;

    if (state.items.some(i=>i.productId === pid)){
      UI.fieldError('productoid','Este producto ya está en la lista.'); return;
    }

    state.items.push({ productId:pid, productName:pname, cantidad:qty });

    const newRowNode = dataTable.row.add([
      pname,
      `<input type="number" class="qty-input" min="1" value="${qty}">`,
      `<button type="button" class="btn-eliminar" data-product-id="${pid}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();

    setDataLabels(window.jQuery(newRowNode));

    dom.prdInp.value=''; dom.prdHid.value='';
    dom.qtyInp.value='';
    dom.prdInp.focus();
  });

  dom.rowsWrap.addEventListener('click', e=>{
    const btn = e.target.closest('.btn-eliminar');
    if (!btn) return;
    const pid = btn.dataset.productId;
    state.items = state.items.filter(i=>i.productId!==pid);
    dataTable.row(btn.closest('tr')).remove().draw(false);
  });

  dom.form.addEventListener('submit', async ev=>{
    ev.preventDefault();
    UI.clearAlerts(); UI.clearFieldErrors();

    if (!state.items.length){
      UI.err('Debe agregar al menos un producto.');
      return;
    }

    dataTable.rows().every(function(){
      const tds = this.node().querySelectorAll('td');
      const prodCell = tds[0];
      const qtyCell  = tds[1];
      const item = state.items.find(i=>i.productName === prodCell.textContent.trim());
      const inp  = qtyCell.querySelector('.qty-input');
      if (item && inp) item.cantidad = inp.value.trim();
    });

    $id('id_inventarios_temp').value = JSON.stringify(state.items);

    try{
      const csrftoken = document.cookie.split(';').find(c=>c.trim().startsWith('csrftoken='))?.split('=')[1] || '';
      const resp = await fetch(dom.form.action,{
        method : 'POST',
        headers: {
          'X-CSRFToken': csrftoken,
          'Accept'     : 'application/json'
        },
        body: new FormData(dom.form)
      });

      const data = await resp.json();

      if (data.success){
        UI.ok('Inventario creado exitosamente.');
        dom.form.reset();
        state.items = [];
        dataTable.clear().draw();

        dom.sucInp.value=''; dom.sucHid.value='';
        dom.prdInp.value=''; dom.prdHid.value='';
        hideAllExcept(null);
        dom.sucInp.focus();
      } else {
        const errs = JSON.parse(data.errors || '{}');
        Object.entries(errs).forEach(([field, arr])=>{
          (arr || []).forEach(e=> UI.fieldError(field, e.message));
        });
      }
    }catch(err){
      console.error(err);
      UI.err('Ocurrió un error inesperado.');
    }
  });

  window.jQuery('#productos-list').trigger('draw.dt');
})();
