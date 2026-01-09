/*  editar_precios_proveedor.js
    ────────────────────────────────────────────────────────────────
    • Autocomplete instantáneo (SWR) + anti-stale (AbortController + reqId)
    • Si el input está vacío y TIENE FOCO → muestra lista base (todas)
    • Cierre robusto de cajas (blur, focus a otro control, clic fuera)
    • Vaciar input + sin foco → limpiar/ocultar caja
    • Mantiene selectedProvId visible; reset de productos al cambiar proveedor
    • Enter: siguiente / toma 1ª opción / agregar si es precio
*/
(() => {
  "use strict";

  /* Helpers */
  const $id  = id  => document.getElementById(id);
  const $qs  = sel => document.querySelector(sel);
  const $qsa = sel => document.querySelectorAll(sel);
  const isInside = (root, el) => root && el && (root === el || root.contains(el));

  /* Refs DOM */
  const dom = {
    form      : $id("preciosForm"),
    provInp   : $id("id_proveedor_autocomplete"),
    provHid   : $id("id_proveedor"),
    provBox   : $id("proveedor-autocomplete-results"),
    prodInp   : $id("id_producto_autocomplete"),
    prodHid   : $id("id_productoid"),
    prodBox   : $id("producto-autocomplete-results"),
    priceInp  : $id("id_precio"),
    btnAdd    : $id("agregarProductoBtn"),
    tbody     : $id("productos-body"),
    hidden    : $id("id_precios_temp"),
    alertErr  : $id("error-message"),
    alertOk   : $id("success-message"),
  };

  /* DataTable */
  const dt = $("#productos-list").DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{
      search:"Buscar:",
      zeroRecords:"No se encontraron resultados",
      emptyTable:"No hay productos para mostrar"
    }
  });

  /* State */
  const state = { items: [] };
  let selectedProvId = dom.provHid.value || "";

  /* UI helpers */
  const UI = {
    clrAlerts(){ [dom.alertErr, dom.alertOk].forEach(el=>{ el.style.display="none"; el.innerHTML=""; }); },
    ok(msg){ dom.alertOk.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`; dom.alertOk.style.display="block"; },
    err(msg){ dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`; dom.alertErr.style.display="block"; },
    clrFieldErr(){
      $qsa(".field-error").forEach(d=>{ d.classList.remove("visible"); d.innerHTML=""; });
      $qsa(".input-error").forEach(i=>i.classList.remove("input-error"));
    },
    fErr(field,msg){
      const box = $qs(`#error-id_${field}`);
      if(box){
        box.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
        box.classList.add("visible");
      }
      const map = { proveedor:dom.provInp, productoid:dom.prodInp, precio:dom.priceInp };
      (map[field]||null)?.classList.add("input-error");
    }
  };

  /* ===== factory de autocomplete (SWR + anti-stale) ===== */
  function makeAuto(kind){
    const ac = {
      page:1, term:"", loading:false, more:true,
      aborter:null, reqId:0,
      cache:Object.create(null),
      basePage:[], baseHasMore:true,
      box: kind==="prov" ? dom.provBox : dom.prodBox,
      inp: kind==="prov" ? dom.provInp : dom.prodInp,
      hid: kind==="prov" ? dom.provHid : dom.prodHid,
      open(){ this.box.style.display="block"; },
      close(){ this.box.style.display="none"; },
      clearBox(){ this.box.innerHTML=""; },
      showLoading(){
        this.box.innerHTML = '<div class="autocomplete-no-result">Cargando…</div>';
        this.open();
      },
      cancel(){ try{ this.aborter?.abort(); }catch(_){/*noop*/} this.aborter=null; }
    };

    const buildUrl = (p) => {
      if (kind === "prov") {
        const cur = selectedProvId ? `&current=${selectedProvId}` : "";
        return `${proveedorAutocompleteUrl}?${p}${cur}`;
      }
      const exc = state.items.map(i=>i.productId).join(",");
      const productoURL =
        (typeof productoPreciosAutocompleteUrl !== "undefined" && productoPreciosAutocompleteUrl)
          ? productoPreciosAutocompleteUrl
          : (typeof productoAutocompleteUrl !== "undefined" ? productoAutocompleteUrl : "");
      return `${productoURL}?${p}&excluded=${exc}`;
    };

    function paint(list, replace=true){
      requestAnimationFrame(()=>{
        const frag = document.createDocumentFragment();
        list.forEach(r=>{
          const d=document.createElement("div");
          d.className="autocomplete-option";
          d.dataset.id=r.id;
          d.textContent=r.text;
          frag.appendChild(d);
        });
        if(replace) ac.box.replaceChildren(frag);
        else ac.box.appendChild(frag);
        ac.open();
      });
    }

    async function fetchPage(term, page){
      const key = `${term}::${page}`;
      if(ac.cache[key]) return ac.cache[key];
      const qs  = new URLSearchParams({term, page});
      const url = buildUrl(qs);
      if(!url) return {results:[], has_more:false};
      const r   = await fetch(url, { signal: ac.aborter?.signal });
      const j   = await r.json();
      ac.cache[key]=j;
      return j;
    }

    async function serverSearch({reset=true}={}){
      if(ac.loading || !ac.more) return;

      // anti-stale
      ac.cancel();
      ac.aborter = new AbortController();
      const myReq = ++ac.reqId;
      const termAtReq = ac.term, pageAtReq = ac.page;

      ac.loading = true;
      try{
        const data = await fetchPage(termAtReq, pageAtReq);
        if(myReq !== ac.reqId || termAtReq !== ac.term || pageAtReq !== ac.page) return;

        if(reset) ac.clearBox();
        if(data.results?.length){
          paint(data.results, reset);
          ac.more = !!data.has_more;
        }else{
          ac.more = false;
          if(reset){
            ac.box.innerHTML = '<div class="autocomplete-no-result">No se encontraron resultados</div>';
            ac.open();
          }
        }
      }catch(_){/* abort/red */}
      finally{ ac.loading=false; }
    }

    function instant(){
      const key1 = `${ac.term}::1`;
      let painted = false;

      if(ac.cache[key1]?.results){
        paint(ac.cache[key1].results, true);
        painted = true;
      }else if(ac.basePage.length){
        const t = ac.term.toLowerCase();
        const local = ac.basePage
          .filter(r => r.text.toLowerCase().includes(t) || r.text.toLowerCase().startsWith(t))
          .slice(0,50);
        paint(local, true);
        painted = true;
      }

      ac.page = 1; ac.more = true;
      serverSearch({reset:true});
      if(!painted) requestAnimationFrame(()=>ac.open());
    }

    /* ==== eventos ===== */
    let blurTimer = null;

    const onInput = ()=>{
      ac.term = ac.inp.value.trim();

      // Si está vacío:
      if(ac.term === ""){
        // Con FOCO → mostrar base (todas)
        if (document.activeElement === ac.inp) {
          ac.page = 1; ac.more = true;
          if(ac.basePage.length){
            paint(ac.basePage, true);
            ac.box.scrollTop = 0;
          }else{
            ac.showLoading();
          }
          serverSearch({reset:true}); // revalida base
        }else{
          // Sin foco → limpiar y ocultar
          if(kind==="prod") ac.hid.value = "";
          ac.cancel();
          ac.clearBox();
          ac.close();
          ac.page = 1; ac.more = true;
        }
        return;
      }

      // Hay término → respuesta instantánea + revalidación
      if(kind==="prod") ac.hid.value = "";
      instant();
    };

    const onFocus = ()=>{
      ac.term = ac.inp.value.trim();
      ac.page = 1; ac.more = true;

      if(ac.term === ""){
        // Mostrar base en foco vacío
        if(ac.basePage.length){ paint(ac.basePage, true); }
        else ac.showLoading();
        serverSearch({reset:true});
      }else{
        instant();
      }
    };

    const onBlur = ()=>{
      clearTimeout(blurTimer);
      blurTimer = setTimeout(()=>{ ac.close(); }, 130);
    };

    const onScroll = ()=>{
      if(ac.box.scrollTop + ac.box.clientHeight >= ac.box.scrollHeight - 4 && ac.more && !ac.loading){
        ac.page++; serverSearch({reset:false});
      }
    };

    const onClick = (e)=>{
      const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
      ac.inp.value = opt.textContent;

      if(kind==="prov"){
        selectedProvId    = opt.dataset.id;
        dom.provHid.value = selectedProvId;

        // Cambió proveedor: reset total del autocomplete de productos
        try{
          acProd.cache       = Object.create(null);
          acProd.basePage    = [];
          acProd.baseHasMore = true;
          acProd.term        = "";
          acProd.page        = 1;
          acProd.more        = true;
          dom.prodInp.value  = "";
          dom.prodHid.value  = "";
          acProd.clearBox(); acProd.close();
        }catch(_){}
      }else{
        ac.hid.value = opt.dataset.id;
      }
      ac.close();
    };

    // Cerrar si haces foco en otro control
    document.addEventListener("focusin", (ev)=>{
      const t = ev.target;
      if(!isInside(ac.inp, t) && !isInside(ac.box, t)) ac.close();
    });

    // Cerrar si clic fuera
    document.addEventListener("click", (ev)=>{
      const t = ev.target;
      if(!isInside(ac.inp, t) && !isInside(ac.box, t)) ac.close();
    });

    ac.inp.addEventListener("input", onInput);
    ac.inp.addEventListener("focus", onFocus);
    ac.inp.addEventListener("blur", onBlur);
    ac.box.addEventListener("scroll", onScroll);
    ac.box.addEventListener("click", onClick);

    // Prefetch base (no muestra; sólo cachea)
    (async function prefetchBase(){
      try{
        const data = await fetchPage("", 1);
        ac.basePage = data.results || [];
        ac.baseHasMore = !!data.has_more;
      }catch(_){}
    })();

    return ac;
  }

  // Instancias
  const acProv = makeAuto("prov");
  const acProd = makeAuto("prod");

  /* ---------- precarga productos ---------- */
  if (Array.isArray(existingProducts)){
    existingProducts.forEach(p=>{
      state.items.push({productId:String(p.productId), productName:p.productName, price:String(p.price)});
      dt.row.add([
        p.productName,
        `<input type="number" class="price-input" value="${p.price}" step="0.01" min="0.01" style="width:80px;">`,
        `<button type="button" class="btn-eliminar" data-product-id="${p.productId}">
           <i class="fas fa-trash-alt"></i>
         </button>`
      ]).draw(false);
    });
  }

  /* ---------- agregar / actualizar fila ---------- */
  dom.btnAdd.addEventListener("click",()=>{
    UI.clrAlerts(); UI.clrFieldErr();

    const pid   = dom.prodHid.value.trim();
    const pname = dom.prodInp.value.trim();
    const price = dom.priceInp.value.trim();
    const provId= selectedProvId;

    let bad=false;
    if(!provId){ UI.fErr("proveedor","Debe seleccionar un proveedor."); bad=true; }
    if(!pid){   UI.fErr("productoid","Debe seleccionar un producto."); bad=true; }
    if(!price || parseFloat(price)<=0){ UI.fErr("precio","El precio debe ser mayor que 0."); bad=true; }
    if(bad) return;

    const idx = state.items.findIndex(i=>i.productId===pid);
    if(idx>=0){
      state.items[idx].price = price;
      dt.rows().every(function(){
        const btn=this.node().querySelector(".btn-eliminar");
        if(btn?.dataset.productId===pid)
          this.node().querySelector(".price-input").value = price;
      });
    }else{
      state.items.push({productId:pid,productName:pname,price});
      dt.row.add([
        pname,
        `<input type="number" class="price-input" value="${price}" step="0.01" min="0.01" style="width:80px;">`,
        `<button type="button" class="btn-eliminar" data-product-id="${pid}">
           <i class="fas fa-trash-alt"></i>
         </button>`
      ]).draw(false);
    }
    dom.prodInp.value  = "";
    dom.prodHid.value  = "";
    dom.priceInp.value = "";
  });

  /* eliminar fila */
  dom.tbody.addEventListener("click",e=>{
    const btn=e.target.closest(".btn-eliminar"); if(!btn) return;
    const pid=btn.dataset.productId;
    dt.row(btn.closest("tr")).remove().draw(false);
    state.items = state.items.filter(i=>i.productId!==pid);
  });

  /* ---------- ENTER → siguiente / seleccionar / agregar ---------- */
  (function setupEnterNav(){
    if(!dom.form) return;

    const visible = el => !!(el && el.offsetParent !== null);
    const getFocusables = () =>
      Array.from(dom.form.querySelectorAll('input:not([type="hidden"]):not([disabled])'))
           .filter(visible);

    const pickFirstFrom = (box)=>{
      if(!box || box.style.display==="none") return null;
      return box.querySelector(".autocomplete-option");
    };

    dom.form.addEventListener("keydown", (e)=>{
      if(e.key !== "Enter") return;

      const t = e.target;
      e.preventDefault();

      if (t === dom.priceInp){ dom.btnAdd?.click(); return; }

      if (t === dom.provInp || t === dom.prodInp){
        const isProv = (t === dom.provInp);
        const box    = isProv ? acProv.box : acProd.box;
        const first  = pickFirstFrom(box);

        if(first){
          const text = first.textContent;
          const id   = first.dataset.id || "";
          t.value    = text;

          if(isProv){
            selectedProvId    = id;
            dom.provHid.value = id;

            // Al cambiar proveedor se limpia productos
            try{
              acProd.cache       = Object.create(null);
              acProd.basePage    = [];
              acProd.baseHasMore = true;
              acProd.term        = "";
              acProd.page        = 1;
              acProd.more        = true;
              dom.prodInp.value  = "";
              dom.prodHid.value  = "";
              acProd.clearBox(); acProd.close();
            }catch(_){}
          }else{
            dom.prodHid.value = id;
          }
          box.style.display = "none";
        }
      }

      const focusables = getFocusables();
      const idx = focusables.indexOf(t);
      const next = focusables[idx+1] || null;

      if (next){
        next.focus();
        if (typeof next.select === "function"){ try{ next.select(); }catch(_){} }
      }else{
        dom.btnAdd?.click();
      }
    });
  })();

  /* ---------- submit ---------- */
  dom.form.addEventListener("submit",async ev=>{
    ev.preventDefault(); UI.clrAlerts(); UI.clrFieldErr();

    // sync precios desde inputs
    dt.rows().every(function(){
      const pid=this.node().querySelector(".btn-eliminar")?.dataset.productId;
      if(!pid) return;
      const val=this.node().querySelector(".price-input")?.value.trim();
      const item=state.items.find(i=>i.productId===pid);
      if(item) item.price = val;
    });

    dom.hidden.value = JSON.stringify(state.items);

    try{
      const r=await fetch(dom.form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":document.cookie.split(";").find(c=>c.trim().startsWith("csrftoken="))?.split("=")[1]||"",
          Accept:"application/json"
        },
        body:new FormData(dom.form)
      });
      const data=await r.json();
      if(data.success){
        window.location.href = data.redirect_url;
      }else{
        const errs = JSON.parse(data.errors||"{}");
        for(const [field,arr] of Object.entries(errs))
          arr.forEach(e=>UI.fErr(field,e.message));
      }
    }catch(err){
      console.error(err);
      UI.err("Ocurrió un error inesperado al guardar.");
    }
  });
})();
