/*  agregar_precios_proveedor.js
    — Autocomplete ultrarrápido (SWR + reqId anti-stale + abort)
    — Pinta instantáneo (cache/local) y refresca en segundo plano
    — Scroll infinito, DataTable responsive, validaciones
    — <td data-label="..."> para títulos en móvil
    — Navegación con Enter (incluye selección de 1ª opción en autocompletes)
----------------------------------------------------------------*/
(() => {
  "use strict";

  /* Helpers DOM */
  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);

  /* DataTable */
  const dt = $('#productos-list').DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{
      search:"Buscar:", zeroRecords:"No se encontraron resultados",
      emptyTable:"No hay productos para mostrar"
    }
  });

  /* === data-label para móvil === */
  const COL_LABELS = ["Producto", "Precio", "Acciones"];
  function setDataLabels($row){
    $row.find("td").each((i,td)=>td.setAttribute("data-label", COL_LABELS[i]||""));
  }
  dt.on("draw", ()=>$('#productos-list tbody tr').each(function(){ setDataLabels($(this)); }));

  /* Refs */
  const dom = {
    form:$id("preciosForm"),
    provInp:$id("id_proveedor_autocomplete"),
    provHid:$id("id_proveedor"),
    provBox:$id("proveedor-autocomplete-results"),

    prodInp:$id("id_producto_autocomplete"),
    prodHid:$id("id_productoid"),
    prodBox:$id("producto-autocomplete-results"),

    priceInp:$id("id_precio"),
    btnAdd:$id("agregarProductoBtn"),
    tbody:$id("productos-body"),

    alertErr:$id("error-message"),
    alertOk :$id("success-message"),
  };

  /* Estado global */
  const state = {
    prov:{ page:1, term:"", loading:false, more:true, aborter:null, reqId:0, cache:Object.create(null), basePage:[], baseHasMore:true },
    prod:{ page:1, term:"", loading:false, more:true, aborter:null, reqId:0, cache:Object.create(null), basePage:[], baseHasMore:true },
    items:[] // [{productId, productName, price}]
  };

  /* UI helpers */
  const UI = {
    clearAlerts(){ [dom.alertErr, dom.alertOk].forEach(a=>{a.style.display="none";a.innerHTML="";}); },
    ok(msg){ dom.alertOk.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`; dom.alertOk.style.display="block"; },
    err(msg){ dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`; dom.alertErr.style.display="block"; },
    clearErrors(){ $qsa(".field-error").forEach(d=>{d.classList.remove("visible");d.innerHTML="";}); $qsa(".input-error").forEach(i=>i.classList.remove("input-error")); },
    fieldError(field,msg){
      const box=$qs(`#error-id_${field}`); if(box){ box.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${msg}`; box.classList.add("visible"); }
      ({proveedor:dom.provInp, productoid:dom.prodInp, precio:dom.priceInp}[field]||null)?.classList.add("input-error");
    }
  };

  /* ======== Autocomplete instantáneo (SWR) ======== */
  function makeAutocomplete(kind){
    const S = state[kind];
    const cfg = (kind==="prov") ? {
      inp:dom.provInp, hid:dom.provHid, box:dom.provBox,
      url:(term,page)=>`${proveedorAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}`
    } : {
      inp:dom.prodInp, hid:dom.prodHid, box:dom.prodBox,
      url:(term,page)=>{
        const ex = state.items.map(i=>i.productId).join(",");
        return `${productoAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}&excluded=${ex}`;
      }
    };

    const open =()=>{ cfg.box.style.display="block"; };
    const close=()=>{ cfg.box.style.display="none"; S.activeIndex=-1; };
    const paint = (list, replace=true)=>{
      requestAnimationFrame(()=>{
        const frag=document.createDocumentFragment();
        list.forEach(r=>{
          const d=document.createElement("div");
          d.className="autocomplete-option";
          d.dataset.id=r.id; d.textContent=r.text;
          frag.appendChild(d);
        });
        if(replace) cfg.box.replaceChildren(frag); else cfg.box.appendChild(frag);
        open();
      });
    };

    async function fetchPage(term,page){
      const key=`${term}::${page}`;
      if(S.cache[key]) return S.cache[key];
      const url = cfg.url(term,page);
      const r   = await fetch(url,{ signal:S.aborter?.signal });
      const j   = await r.json();
      S.cache[key]=j;
      return j;
    }

    async function serverSearch({reset=true}={}){
      if(S.loading || !S.more) return;

      if(S.aborter){ try{ S.aborter.abort(); }catch(_){/* no-op */} }
      S.aborter = new AbortController();
      const myReq = ++S.reqId;
      const termAtReq = S.term;
      const pageAtReq = S.page;

      S.loading=true;
      try{
        const data = await fetchPage(termAtReq,pageAtReq);
        if(myReq!==S.reqId || termAtReq!==S.term || pageAtReq!==S.page) return;

        if(reset) cfg.box.innerHTML="";
        if(data.results?.length){
          paint(data.results, /*replace*/ reset);
          S.more = !!data.has_more;
        }else{
          S.more=false;
          if(reset){
            requestAnimationFrame(()=>{
              if(myReq===S.reqId && termAtReq===S.term){
                cfg.box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
                open();
              }
            });
          }
        }
      }catch(_){ /* abortado u error */ }
      finally{ S.loading=false; }
    }

    function instant(){
      const key1 = `${S.term}::1`;
      let painted=false;

      if(S.cache[key1]?.results){
        paint(S.cache[key1].results,true);
        painted=true;
      }else if(S.basePage.length){
        const t=S.term.toLowerCase();
        const local = S.basePage.filter(r => r.text.toLowerCase().includes(t) || r.text.toLowerCase().startsWith(t)).slice(0,50);
        paint(local,true);
        painted=true;
      }

      S.page=1; S.more=true;
      serverSearch({reset:true});
      if(!painted) requestAnimationFrame(open);
    }

    cfg.inp.addEventListener("input", ()=>{
      cfg.hid.value="";
      S.term = cfg.inp.value.trim();
      if(!S.term){
        S.page=1; S.more=S.baseHasMore;
        if(S.basePage.length){ paint(S.basePage,true); cfg.box.scrollTop=0; }
        serverSearch({reset:true});
        return;
      }
      instant();
    });

    cfg.inp.addEventListener("focus", ()=>{
      S.term = cfg.inp.value.trim();
      S.page=1; S.more=true;
      if(!S.term){
        if(S.basePage.length){ paint(S.basePage,true); }
        serverSearch({reset:true});
      }else{
        instant();
      }
    });

    cfg.box.addEventListener("scroll", ()=>{
      if(cfg.box.scrollTop + cfg.box.clientHeight >= cfg.box.scrollHeight - 4 && S.more && !S.loading){
        S.page++; serverSearch({reset:false});
      }
    });

    cfg.box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
      cfg.inp.value = opt.textContent;
      cfg.hid.value = opt.dataset.id;
      close();
    });

    document.addEventListener("click", e=>{
      if(!cfg.inp.contains(e.target) && !cfg.box.contains(e.target)) close();
    });

    (async function prefetchBase(){
      try{
        const data = await fetchPage("",1);
        S.basePage = data.results||[];
        S.baseHasMore = !!data.has_more;
      }catch(_){}
    })();
  }
  makeAutocomplete("prov");
  makeAutocomplete("prod");

  /* ========= ENTER → siguiente / seleccionar 1ª opción / agregar ========= */
  (function setupEnterNavigation(){
    if(!dom.form) return;

    const isVisible = el => !!(el && el.offsetParent !== null);

    const getFocusables = () =>
      Array.from(
        dom.form.querySelectorAll(
          'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
        )
      ).filter(isVisible);

    // Selecciona la 1ª opción visible del autocomplete indicado
    function selectFirstAutocomplete(forInput){
      let box, hid, inp;
      if (forInput === dom.provInp){ box = dom.provBox; hid = dom.provHid; inp = dom.provInp; }
      else if (forInput === dom.prodInp){ box = dom.prodBox; hid = dom.prodHid; inp = dom.prodInp; }
      else return false;

      if (box && box.style.display !== "none"){
        const first = box.querySelector(".autocomplete-option");
        if(first){
          inp.value = first.textContent;
          hid.value = first.dataset.id || "";
          box.style.display = "none";
          return true;
        }
      }
      return false;
    }

    dom.form.addEventListener("keydown", (e)=>{
      if(e.key !== "Enter") return;

      const t = e.target;

      // Permitir salto de línea con Shift+Enter en TEXTAREA
      if (t.tagName === "TEXTAREA" && e.shiftKey) return;

      e.preventDefault();

      // Caso especial: si es el input de Precio → Agregar
      if (t === dom.priceInp){
        dom.btnAdd?.click();
        return;
      }

      const focusables = getFocusables();
      const idx = focusables.indexOf(t);
      const isLast = idx === focusables.length - 1;

      // Si es un autocomplete, intenta tomar la 1ª opción
      if (t === dom.provInp || t === dom.prodInp){
        selectFirstAutocomplete(t);
      }

      // Si es el último campo (por si cambia el orden), también Agregar
      if (isLast){
        dom.btnAdd?.click();
        return;
      }

      // Enfoca el siguiente campo
      const next = focusables[idx + 1];
      if(next){
        next.focus();
        if(typeof next.select === "function" && /^(INPUT|TEXTAREA)$/.test(next.tagName)){
          try{ next.select(); }catch{}
        }
      }
    });
  })();

  /* Agregar fila */
  dom.btnAdd.addEventListener("click", ()=>{
    UI.clearAlerts(); UI.clearErrors();

    const pid=dom.prodHid.value.trim(), pname=dom.prodInp.value.trim();
    const price=dom.priceInp.value.trim(), provId=dom.provHid.value.trim();

    let bad=false;
    if(!provId){ UI.fieldError("proveedor","Debe seleccionar un proveedor."); bad=true; }
    if(!pid){   UI.fieldError("productoid","Debe seleccionar un producto."); bad=true; }
    if(!price || parseFloat(price)<=0){ UI.fieldError("precio","El precio debe ser mayor que 0."); bad=true; }
    if(bad) return;

    if(state.items.some(i=>i.productId===pid)){
      UI.fieldError("productoid","Este producto ya está en la lista."); return;
    }

    state.items.push({productId:pid,productName:pname,price});
    const node = dt.row.add([
      pname,
      `<input type="number" class="price-input" value="${price}" step="0.01" min="0.01" style="width:80px;">`,
      `<button type="button" class="btn-eliminar" data-product-id="${pid}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();
    setDataLabels($(node));

    dom.prodInp.value=""; dom.prodHid.value=""; dom.priceInp.value="";
  });

  /* Eliminar fila */
  dom.tbody.addEventListener("click", e=>{
    const btn=e.target.closest(".btn-eliminar"); if(!btn) return;
    const pid=btn.dataset.productId;
    dt.row(btn.closest("tr")).remove().draw(false);
    state.items = state.items.filter(i=>i.productId!==pid);
  });

  /* Submit */
  dom.form.addEventListener("submit", async ev=>{
    ev.preventDefault(); UI.clearAlerts(); UI.clearErrors();

    if(!state.items.length){ UI.err("Debe agregar al menos un producto."); return; }

    dt.rows().every(function(){
      const [prod,priceCell]=this.node().querySelectorAll("td");
      const item=state.items.find(i=>i.productName===prod.textContent.trim());
      const inp=priceCell.querySelector(".price-input");
      if(item && inp) item.price = inp.value.trim();
    });

    $id("id_precios_temp").value = JSON.stringify(state.items);

    try{
      const r = await fetch(dom.form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":document.cookie.split(";").map(c=>c.trim()).find(c=>c.startsWith("csrftoken="))?.split("=")[1]||"",
          Accept:"application/json"
        },
        body:new FormData(dom.form)
      });
      const data = await r.json();
      if(data.success){
        UI.ok("Productos y precios agregados exitosamente.");
        dom.form.reset(); state.items=[]; dt.clear().draw();
        [dom.provBox,dom.prodBox].forEach(b=>b.style.display="none");
        state.prov.cache=Object.create(null);
        state.prod.cache=Object.create(null);
      }else{
        const errs=JSON.parse(data.errors||"{}");
        for(const [field,arr] of Object.entries(errs)) arr.forEach(e=>UI.fieldError(field,e.message));
      }
    }catch(err){ console.error(err); UI.err("Ocurrió un error inesperado."); }
  });

})();
