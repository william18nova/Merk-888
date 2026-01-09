/* ventas_resumen_puntopago.js
   - Autocomplete Punto de Pago (SWR: pinta caché/base y revalida)
   - Selector de fecha
   - Fetch a /api/ventas_resumen/ y pinta tarjetas
*/
(function(){
  "use strict";

  const $ = (s, p=document) => p.querySelector(s);
  const $$ = (s, p=document) => Array.from(p.querySelectorAll(s));

  const inp = $("#id_pp_autocomplete");
  const hid = $("#id_puntopago");
  const box = $("#pp-autocomplete-results");
  const inpDate = $("#id_fecha");
  const alertBox = $("#vrp-alert");
  const countEl = $("#vrp-count");
  const totalEl = $("#vrp-total");

  // Estado autocomplete
  let page=1, term="", more=true, loading=false, reqId=0, xhr=null;
  let cache = Object.create(null), basePage=[], baseHasMore=true;

  function showAlert(ok, msg){
    alertBox.className = "vrp-alert " + (ok ? "is-ok" : "is-err");
    alertBox.textContent = msg;
    alertBox.style.display = "block";
    setTimeout(()=> alertBox.style.display="none", 3000);
  }

  function paint(list, replace=true){
    requestAnimationFrame(()=>{
      if(replace) box.innerHTML = "";
      const frag = document.createDocumentFragment();
      list.forEach(r=>{
        const d = document.createElement("div");
        d.className="vrp-opt";
        d.dataset.id=r.id;
        d.textContent=r.text;
        frag.appendChild(d);
      });
      box.appendChild(frag);
      box.style.display="block";
    });
  }

  function fetchPage(q, p, {replace=true}={}){
    const key = `${q}::${p}`;
    if(cache[key]){
      const d=cache[key];
      paint(d.results||[], replace);
      more = !!d.has_more;
    }
    if(xhr && xhr.readyState !== 4){ try{ xhr.abort(); }catch(e){} }
    const myReq = ++reqId; loading=true;

    xhr = fetch(`${ppAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}`)
      .then(r=>r.json())
      .then(data=>{
        cache[key] = data || {results:[], has_more:false};
        if(myReq !== reqId || q !== term || p !== page) return;
        if(!replace && p>1) paint(data.results||[], false);
        else { box.innerHTML=""; paint(data.results||[], true); }
        more = !!data.has_more;
      })
      .catch(()=>{})
      .finally(()=> loading=false);
  }

  function instant(){
    const key = `${term}::1`; let painted=false;
    if(cache[key]?.results){
      box.innerHTML=""; paint(cache[key].results, true);
      more = !!cache[key].has_more; painted=true;
    }else if(basePage.length){
      const t=term.toLowerCase();
      const local=basePage.filter(r=> r.text.toLowerCase().includes(t)).slice(0,50);
      box.innerHTML=""; paint(local, true);
      more = baseHasMore; painted=true;
    }
    page=1; more=true; fetchPage(term, page, {replace:true});
    if(!painted) box.style.display="block";
  }

  // Eventos
  inp.addEventListener("input", ()=>{
    hid.value="";
    term = inp.value.trim();
    if(!term){
      page=1; more=baseHasMore;
      if(basePage.length){ box.innerHTML=""; paint(basePage, true); }
      fetchPage("",1,{replace:true});
      return;
    }
    instant();
  });
  inp.addEventListener("focus", ()=>{
    term = inp.value.trim(); page=1; more=true;
    if(!term){
      if(basePage.length){ box.innerHTML=""; paint(basePage,true); }
      fetchPage("",1,{replace:true});
    }else instant();
  });
  box.addEventListener("scroll", function(){
    if(this.scrollTop + this.clientHeight >= this.scrollHeight - 4 && more && !loading){
      page += 1; fetchPage(term, page, {replace:false});
    }
  });
  box.addEventListener("click", (e)=>{
    const opt = e.target.closest(".vrp-opt"); if(!opt) return;
    inp.value = opt.textContent;
    hid.value = opt.dataset.id;
    box.style.display="none";
    refresh();
  });
  document.addEventListener("click", e=>{
    if(!inp.contains(e.target) && !box.contains(e.target)) box.style.display="none";
  });
  inp.addEventListener("keydown", e=>{
    if(e.key==="Enter"){
      e.preventDefault();
      const first = box.querySelector(".vrp-opt");
      if(first){
        inp.value = first.textContent;
        hid.value = first.dataset.id;
        box.style.display="none";
        refresh();
      }
    }
  });

  // Prefetch base 1a página
  (function prefetch(){
    const key="::1";
    fetch(`${ppAutocompleteUrl}?term=&page=1`)
      .then(r=>r.json())
      .then(d=>{
        cache[key]=d||{results:[],has_more:false};
        basePage=cache[key].results||[];
        baseHasMore=!!cache[key].has_more;
      })
      .catch(()=>{});
  })();

  // REFRESH resumen
  function refresh(){
    const pid = hid.value.trim();
    const date = inpDate.value;
    if(!pid || !date) return;

    fetch(`${ventasResumenApi}?puntopago=${encodeURIComponent(pid)}&date=${encodeURIComponent(date)}`)
      .then(async r=>{
        let data;
        try { data = await r.json(); } catch(_){ data=null; }
        if(!data || !data.success){ throw new Error(data?.message || "Error"); }
        countEl.textContent = data.count;
        totalEl.textContent = data.total;
      })
      .catch(err=>{
        countEl.textContent = "—";
        totalEl.textContent = "—";
        showAlert(false, err.message || "No se pudo cargar el resumen.");
      });
  }

  inpDate.addEventListener("change", refresh);
  // Primer render si ya hay valores por defecto
  refresh();
})();
