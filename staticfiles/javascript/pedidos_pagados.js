/* Pedidos pagados – Autocomplete + Stats (SWR cache + reqId + infinite scroll) */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const fmtMoney = (n) => new Intl.NumberFormat('es-CO', { style:'currency', currency:'COP', maximumFractionDigits:0 }).format(Number(n || 0));

  const alertBox = $('#ppg-alert');
  const showOk  = (msg)=>{ alertBox.className='ppg-alert ok'; alertBox.textContent=msg; alertBox.style.display='block'; };
  const showErr = (msg)=>{ alertBox.className='ppg-alert err'; alertBox.textContent=msg; alertBox.style.display='block'; };
  const hideAlert=()=>{ alertBox.style.display='none'; };

  // ---------- DOM refs ----------
  const sucInp = $('#suc-inp'), sucId = $('#suc-id'), sucBox = $('#suc-box');
  const ppInp  = $('#pp-inp'),  ppId  = $('#pp-id'),  ppBox  = $('#pp-box');
  const ppClear= $('#pp-clear');
  const fecha  = $('#fecha');
  const statCount = $('#stat-count'), statTotal = $('#stat-total');

  // ========== GENERIC AUTOCOMPLETE (reusable) ==========
  function makeAutocomplete({ inp, hidden, box, urlBuilder, dependsOnValueFn }) {
    const state = {
      page:1, term:'', more:true, loading:false, reqId:0, xhr:null,
      base:[], baseHasMore:true, cache:Object.create(null)
    };

    const open  =()=> box.style.display='block';
    const close =()=> box.style.display='none';
    const clear =()=> { box.innerHTML=''; };

    function paint(list, replace=true){
      requestAnimationFrame(()=>{
        if(replace) box.innerHTML='';
        const frag = document.createDocumentFragment();
        (list||[]).forEach(r=>{
          const d = document.createElement('div');
          d.className = 'ppg-ac-item';
          d.dataset.id = r.id;
          d.textContent = r.text;
          frag.appendChild(d);
        });
        box.appendChild(frag);
        open();
      });
    }

    function fetchPage(q, p, {replace=true}={}){
      const extraKey = dependsOnValueFn ? `::dep=${dependsOnValueFn()||''}` : '';
      const key = `${q}::${p}${extraKey}`;
      // pinta caché
      if(state.cache[key]){
        paint(state.cache[key].results, replace);
        state.more = !!state.cache[key].has_more;
      }
      // abort prev
      if(state.xhr && state.xhr.readyState !== 4){ try{ state.xhr.abort(); }catch(e){} }
      const myReq = ++state.reqId; state.loading = true;

      const url = urlBuilder(q, p);
      state.xhr = $.getJSON ? $.getJSON(url) : fetch(url).then(r=>r.json());
      (state.xhr.then ? state.xhr : state.xhr.done(v=>v)).then(data=>{
        state.cache[key] = data || {results:[], has_more:false};
        if(myReq !== state.reqId || q !== state.term || p !== state.page) return;
        if(!replace && p>1){ paint(data.results||[], false); }
        else { clear(); paint(data.results||[], true); }
        state.more = !!data.has_more;
      }).catch(()=>{/*noop*/}).finally(()=>{ state.loading=false; });
    }

    function instant(){
      const extraKey = dependsOnValueFn ? `::dep=${dependsOnValueFn()||''}` : '';
      const key = `${state.term}::1${extraKey}`;
      let painted=false;

      if(state.cache[key]?.results){
        clear(); paint(state.cache[key].results,true);
        state.more = !!state.cache[key].has_more; painted=true;
      }else if(state.base.length && !state.term){
        clear(); paint(state.base, true);
        state.more = state.baseHasMore; painted=true;
      }
      state.page=1; state.more=true;
      fetchPage(state.term, state.page, {replace:true});
      if(!painted) open();
    }

    // events
    inp.addEventListener('input', ()=>{
      hidden.value='';
      state.term = inp.value.trim();
      if(!state.term){
        state.page=1; state.more=state.baseHasMore;
        if(state.base.length){ clear(); paint(state.base,true); }
        fetchPage('',1,{replace:true});
        return;
      }
      instant();
    });

    inp.addEventListener('focus', ()=>{
      state.term = inp.value.trim();
      state.page = 1; state.more = true;
      if(!state.term){
        if(state.base.length){ clear(); paint(state.base,true); }
        fetchPage('',1,{replace:true});
      }else{ instant(); }
    });

    box.addEventListener('scroll', ()=>{
      if(box.scrollTop + box.clientHeight >= box.scrollHeight - 4 && state.more && !state.loading){
        state.page += 1; fetchPage(state.term, state.page, {replace:false});
      }
    });

    box.addEventListener('click', (e)=>{
      const it = e.target.closest('.ppg-ac-item'); if(!it) return;
      inp.value = it.textContent;
      hidden.value = it.dataset.id || '';
      close();
      // Avisar a dependientes (p. ej. recargar PP al cambiar sucursal)
      inp.dispatchEvent(new CustomEvent('ac:selected', {bubbles:true}));
      // si se elige, intenta calcular stats si todo está listo
      calcIfReady();
    });

    document.addEventListener('click', (e)=>{
      if(!inp.contains(e.target) && !box.contains(e.target)) close();
    });

    // prefetch base
    (function prefetchBase(){
      const url = urlBuilder('', 1);
      ( $.getJSON ? $.getJSON(url) : fetch(url).then(r=>r.json()) )
        .then(data=>{
          state.cache[`::1${dependsOnValueFn?`::dep=${dependsOnValueFn()||''}`:''}`] = data || {results:[], has_more:false};
          state.base    = (data && data.results) || [];
          state.baseHasMore = !!(data && data.has_more);
        }).catch(()=>{});
    })();

    // expose for manual refresh (e.g. after sucursal change)
    return {
      refreshBase(){
        state.base = []; state.baseHasMore = true; state.cache = Object.create(null);
        const url = urlBuilder('', 1);
        ( $.getJSON ? $.getJSON(url) : fetch(url).then(r=>r.json()) )
          .then(data=>{
            state.cache[`::1${dependsOnValueFn?`::dep=${dependsOnValueFn()||''}`:''}`] = data || {results:[], has_more:false};
            state.base    = (data && data.results) || [];
            state.baseHasMore = !!(data && data.has_more);
          }).catch(()=>{});
      },
      clear(){ inp.value=''; hidden.value=''; }
    };
  }

  // ---------- instantiate autocompletes ----------
  const acSucursal = makeAutocomplete({
    inp: sucInp, hidden:sucId, box:sucBox,
    urlBuilder: (q,p)=> `${sucursalAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}`
  });

  const acPuntoPago = makeAutocomplete({
    inp: ppInp, hidden:ppId, box:ppBox,
    urlBuilder: (q,p)=>{
      const sid = sucId.value || '';
      return `${puntoPagoAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}&sucursal_id=${encodeURIComponent(sid)}`;
    },
    dependsOnValueFn: ()=> sucId.value || ''
  });

  // cuando cambia la sucursal → limpiar PP y refrescar su base
  sucInp.addEventListener('ac:selected', ()=>{
    acPuntoPago.clear();
    acPuntoPago.refreshBase();
  });

  // limpiar PP manualmente
  ppClear.addEventListener('click', ()=>{
    acPuntoPago.clear();
    calcIfReady();
    ppInp.focus();
  });

  // ---------- stats ----------
  function calcIfReady(){
    hideAlert();
    const sid = sucId.value;
    const f   = fecha.value;
    if(!sid || !f){ return; }

    const params = new URLSearchParams({
      sucursal_id: sid,
      fecha: f
    });
    const ppv = ppId.value;
    if(ppv) params.set('puntopago_id', ppv);

    fetch(`${statsApiUrl}?${params.toString()}`,{
      headers:{'Accept':'application/json'}
    }).then(r=>r.json())
      .then(data=>{
        statCount.textContent = (data && (data.count ?? data.num ?? 0)) || 0;
        statTotal.textContent = fmtMoney( (data && (data.total ?? 0)) );
      }).catch(()=>{
        showErr('No se pudieron obtener los datos.');
        statCount.textContent = '—';
        statTotal.textContent = '—';
      });
  }

  // triggers para calcular
  fecha.addEventListener('change', calcIfReady);
  // si ya viene sucursal seteada por servidor
  if(sucId.value){ calcIfReady(); }
})();
