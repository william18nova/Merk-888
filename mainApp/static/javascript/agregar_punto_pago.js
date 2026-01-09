/*  static/javascript/agregar_punto_pago.js
    — Autocomplete instantáneo (SWR) con reqId anti‑stale
    — Render en rAF + caché agresiva + filtro local
    — Si input vacío → lista inicial (página 1) *sin mostrar al cargar*
    — Scroll infinito, teclado, DataTable, submit AJAX
    — Enter: siguiente campo / autocomplete toma 1ª opción / “Dinero en Caja” & último → Agregar
    — Tabla: edición inline de “Dinero en Caja” que sincroniza con state.items
----------------------------------------------------------------*/
(() => {
  "use strict";

  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);

  const dom = {
    form   : $id("puntoPagoForm"),
    sucInp : $id("id_sucursal_autocomplete"),
    sucHid : $id("id_sucursal"),
    sucBox : $id("sucursal-autocomplete-results"),
    nomInp : $id("id_nombre"),
    desInp : $id("id_descripcion"),
    cajaInp: $id("id_dinerocaja"),
    btnAdd : $id("agregarPuntoPagoBtn"),
    tbody  : $id("puntos-pago-body"),
    hidden : $id("id_puntos_temp"),
    alertErr: $id("error-message"),
    alertOk : $id("success-message"),
  };

  const getCsrf = () =>
    document.cookie.split(";").map(c=>c.trim())
      .find(c=>c.startsWith("csrftoken="))?.split("=")[1] || "";

  /* ───── DataTable ───── */
  const COL_LABELS = ["Nombre", "Descripción", "Dinero en Caja", "Acciones"];
  const dt = $("#puntos-pago-list").DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{
      search:"Buscar:",
      zeroRecords:"No se encontraron resultados",
      emptyTable:"No hay puntos de pago para mostrar"
    }
  });
  function setDataLabels($row){
    $row.find("td").each((i,td)=>td.setAttribute("data-label", COL_LABELS[i]||""));
  }

  /* ───── Estado general (tabla temporal) ───── */
  const state = { items: [] };

  /* ───── Normalización dinero (string → string con 2 decimales) ───── */
  function normMoney(v){
    if (v == null) return "0.00";
    // Aceptar coma o punto, y filtrar caracteres
    const s = String(v).replace(/[^\d.,-]/g,"").replace(",",".").trim();
    let num = parseFloat(s);
    if (isNaN(num) || num < 0) num = 0;
    // Limitar a 13 dígitos enteros (para evitar overflow en DB decimal(15,2))
    if (Math.floor(num) > 9999999999999) num = 9999999999999;
    return num.toFixed(2);
  }

  /* ───── Estado autocomplete ───── */
  const suc = {
    page: 1, term: "", loading:false, more:true, aborter:null,
    activeIndex:-1, reqId: 0,
    cache: Object.create(null),     // `${term}::${page}` -> {results,has_more}
    basePage: [],                   // cat. para pintar al instante (término vacío)
    baseHasMore: true,
  };

  /* ───── Helpers UI ───── */
  const UI = {
    clearAlerts(){
      [dom.alertErr, dom.alertOk].forEach(a=>{ a.style.display="none"; a.innerHTML=""; });
    },
    ok(msg){
      dom.alertOk.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`;
      dom.alertOk.style.display="block";
    },
    err(msg){
      dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
      dom.alertErr.style.display="block";
    },
    clearFieldErr(){
      document.querySelectorAll(".field-error").forEach(d=>{ d.classList.remove("visible"); d.innerHTML=""; });
      document.querySelectorAll(".input-error").forEach(i=>i.classList.remove("input-error"));
    },
    fErr(field,msg){
      const box = $qs(`#error-id_${field}`);
      if(box){ box.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`; box.classList.add("visible"); }
      ({sucursal:dom.sucInp, nombre:dom.nomInp}[field]||null)?.classList.add("input-error");
    }
  };

  /* ───── Render opciones ───── */
  const openBox  = ()=>{ dom.sucBox.style.display="block"; };
  const closeBox = ()=>{ dom.sucBox.style.display="none"; suc.activeIndex=-1; };
  const clearBox = ()=>{ dom.sucBox.innerHTML=""; suc.activeIndex=-1; };

  function paintOptions(list, replace=true){
    requestAnimationFrame(()=>{
      const frag = document.createDocumentFragment();
      list.forEach(r=>{
        const d = document.createElement("div");
        d.className = "autocomplete-option";
        d.dataset.id = r.id;
        d.textContent = r.text;
        frag.appendChild(d);
      });
      if(replace) dom.sucBox.replaceChildren(frag);
      else dom.sucBox.appendChild(frag);
      openBox();
    });
  }

  function highlight(i){
    const opts = dom.sucBox.querySelectorAll(".autocomplete-option");
    opts.forEach(o=>o.classList.remove("is-active"));
    if(i>=0 && i<opts.length){
      const el = opts[i];
      el.classList.add("is-active");
      suc.activeIndex = i;
      const top = el.offsetTop, bottom = top + el.offsetHeight;
      if (top < dom.sucBox.scrollTop) dom.sucBox.scrollTop = top;
      else if (bottom > dom.sucBox.scrollTop + dom.sucBox.clientHeight)
        dom.sucBox.scrollTop = bottom - dom.sucBox.clientHeight;
    }
  }

  /* ───── Red + caché ───── */
  async function fetchPage(term, page, {signal}={}){
    const key = `${term}::${page}`;
    if(suc.cache[key]) return suc.cache[key];
    const url = `${sucursalAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}`;
    const r = await fetch(url, {signal});
    const data = await r.json();
    suc.cache[key] = data;
    return data;
  }

  async function serverSearch({reset=true}={}){
    if(suc.loading || !suc.more) return;

    if(suc.aborter){ try{suc.aborter.abort();}catch(_){/**/} }
    suc.aborter = new AbortController();
    const myReq = ++suc.reqId;
    const termAtReq = suc.term;
    const pageAtReq = suc.page;

    suc.loading = true;
    try{
      const data = await fetchPage(termAtReq, pageAtReq, {signal:suc.aborter.signal});
      if (myReq !== suc.reqId || termAtReq !== suc.term || pageAtReq !== suc.page) return;

      if(reset){ clearBox(); }
      if(data.results?.length){
        paintOptions(data.results, /*replace*/ reset);
        suc.more = data.has_more;
      }else{
        suc.more = false;
        if(reset){
          requestAnimationFrame(()=>{
            if (myReq === suc.reqId && termAtReq === suc.term) {
              dom.sucBox.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
              openBox();
            }
          });
        }
      }
    }catch(_){ /* abortada o red */ }
    finally{ suc.loading=false; }
  }

  /* ───── Búsqueda instantánea (local → remoto) ───── */
  function instantSearch(){
    const key1 = `${suc.term}::1`;
    let painted = false;

    if(suc.cache[key1]?.results){
      paintOptions(suc.cache[key1].results, true);
      painted = true;
    }else if(suc.basePage.length){
      const t = suc.term.toLowerCase();
      const local = suc.basePage.filter(r =>
        r.text.toLowerCase().includes(t) || r.text.toLowerCase().startsWith(t)
      ).slice(0,50);
      paintOptions(local, true);
      painted = true;
    }

    suc.page = 1; suc.more = true;
    serverSearch({reset:true});

    if(!painted){ requestAnimationFrame(openBox); }
  }

  /* ───── Eventos Autocomplete ───── */
  dom.sucInp.addEventListener("input", ()=>{
    dom.sucHid.value = "";
    suc.term = dom.sucInp.value.trim();
    suc.activeIndex = -1;

    if(!suc.term){
      suc.page = 1; suc.more = suc.baseHasMore;
      if(suc.basePage.length){ paintOptions(suc.basePage, true); dom.sucBox.scrollTop = 0; }
      serverSearch({reset:true});
      return;
    }
    instantSearch();
  });

  dom.sucInp.addEventListener("focus", ()=>{
    suc.term = dom.sucInp.value.trim();
    suc.activeIndex = -1;
    if(!suc.term){
      if(suc.basePage.length){ paintOptions(suc.basePage, true); }
      suc.page = 1; suc.more = true;
      serverSearch({reset:true});
    }else{
      instantSearch();
    }
  });

  dom.sucBox.addEventListener("scroll", ()=>{
    if(dom.sucBox.scrollTop + dom.sucBox.clientHeight >= dom.sucBox.scrollHeight - 4 &&
       suc.more && !suc.loading){
      suc.page++;
      serverSearch({reset:false});
    }
  });

  dom.sucBox.addEventListener("click", e=>{
    const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
    dom.sucInp.value = opt.textContent;
    dom.sucHid.value = opt.dataset.id;
    closeBox();
  });

  document.addEventListener("click", e=>{
    if(!dom.sucInp.contains(e.target) && !dom.sucBox.contains(e.target)) closeBox();
  });

  dom.sucInp.addEventListener("keydown", e=>{
    const opts = dom.sucBox.querySelectorAll(".autocomplete-option");
    if(!opts.length) return;
    if(e.key === "ArrowDown"){ e.preventDefault(); highlight((suc.activeIndex+1)%opts.length); }
    else if(e.key === "ArrowUp"){ e.preventDefault(); highlight((suc.activeIndex-1+opts.length)%opts.length); }
    else if(e.key === "Enter" && suc.activeIndex>=0){
      e.preventDefault();
      const el = opts[suc.activeIndex];
      dom.sucInp.value = el.textContent; dom.sucHid.value = el.dataset.id; closeBox();
    }else if(e.key === "Escape"){ closeBox(); }
  });

  /* ───── Prefetch base (página 1, término vacío) ───── */
  (async function prefetchBase(){
    try{
      const data = await fetchPage("", 1);
      suc.basePage = data.results || [];
      suc.baseHasMore = !!data.has_more;
    }catch(_){}
  })();

  /* ───── Agregar punto de pago (tabla temporal) ───── */
  function renderRow(nombre, descr, caja){
    // celda dinero editable
    const moneyInput =
      `<input type="text" class="tbl-caja" inputmode="decimal"
              value="${normMoney(caja)}" title="Dinero en caja"
              style="width:120px;text-align:right">`;

    const newRowNode = dt.row.add([
      nombre,
      descr,
      moneyInput,
      `<button type="button" class="btn-eliminar" data-nombre="${nombre}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();

    setDataLabels($(newRowNode));
  }

  dom.btnAdd.addEventListener("click", ()=>{
    UI.clearAlerts(); UI.clearFieldErr();

    const sid    = dom.sucHid.value.trim();
    const nombre = dom.nomInp.value.trim();
    const descr  = dom.desInp.value.trim();
    const caja   = normMoney(dom.cajaInp.value.trim() || "0");

    let bad=false;
    if(!sid){ UI.fErr("sucursal","Debe seleccionar una sucursal."); bad=true; }
    if(!nombre){ UI.fErr("nombre","El nombre es obligatorio."); bad=true; }
    if(bad) return;

    if(state.items.some(i=>i.nombre.toLowerCase()===nombre.toLowerCase())){
      UI.fErr("nombre","Ese nombre ya está en la lista."); return;
    }

    state.items.push({nombre, descripcion:descr, dinerocaja:caja});
    renderRow(nombre, descr, caja);

    dom.nomInp.value=""; dom.desInp.value=""; dom.cajaInp.value="";
    dom.nomInp.focus();
  });

  /* ───── Tabla: edición inline de “Dinero en Caja” ───── */
  // Delegamos a <tbody> para que funcione con filas nuevas
  dom.tbody.addEventListener("input", (e)=>{
    const inp = e.target.closest(".tbl-caja"); if(!inp) return;
    // No formateamos en cada pulsación, solo limpiamos caracteres raros
    inp.value = inp.value.replace(/[^\d.,-]/g,"").replace(",",".");
  });

  dom.tbody.addEventListener("blur", (e)=>{
    const inp = e.target.closest(".tbl-caja"); if(!inp) return;
    // Al salir del input: normalizar a 2 decimales y sincronizar state
    const val = normMoney(inp.value);
    inp.value = val;

    // Buscar el nombre de la fila (1ª columna del mismo <tr>)
    const tr = inp.closest("tr");
    const rowData = dt.row(tr).data(); // [nombre, descr, htmlCaja, acciones]
    if (!rowData) return;
    const nombre = String($(tr).find("td").eq(0).text()).trim().toLowerCase();

    const item = state.items.find(i => i.nombre.toLowerCase() === nombre);
    if (item) item.dinerocaja = val;
  }, true);

  // Enter dentro del input de la tabla -> pasa al siguiente input de la fila (si lo hubiera)
  dom.tbody.addEventListener("keydown", (e)=>{
    const inp = e.target.closest(".tbl-caja"); if(!inp) return;
    if (e.key === "Enter"){
      e.preventDefault();
      inp.blur();
    }
  });

  /* ───── Eliminar en tabla temporal ───── */
  dom.tbody.addEventListener("click", e=>{
    const btn = e.target.closest(".btn-eliminar"); if(!btn) return;
    const nombre = btn.dataset.nombre.toLowerCase();
    dt.row(btn.closest("tr")).remove().draw(false);
    state.items = state.items.filter(i=>i.nombre.toLowerCase()!==nombre);
  });

  /* ───── Submit ───── */
  dom.form.addEventListener("submit", async ev=>{
    ev.preventDefault(); UI.clearAlerts(); UI.clearFieldErr();
    if(!state.items.length){ UI.err("Debe agregar al menos un punto de pago."); return; }

    // Asegurar que lo que está en la tabla (posibles ediciones) esté en state
    // Recorremos inputs visibles y pisamos valores
    dom.tbody.querySelectorAll(".tbl-caja").forEach(inp=>{
      const tr = inp.closest("tr");
      const nombre = String($(tr).find("td").eq(0).text()).trim().toLowerCase();
      const item = state.items.find(i => i.nombre.toLowerCase() === nombre);
      if (item) item.dinerocaja = normMoney(inp.value);
    });

    dom.hidden.value = JSON.stringify(state.items);

    try{
      const r = await fetch(dom.form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken": getCsrf(),
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json"
        },
        body:new FormData(dom.form)
      });

      let data, isJson = false;
      const ct = r.headers.get("content-type") || "";
      if (ct.includes("application/json")) { data = await r.json(); isJson = true; }
      else { data = await r.text(); }

      if(isJson && data.success){
        UI.ok("Puntos de pago agregados exitosamente.");
        dom.form.reset(); dt.clear().draw(); state.items=[]; closeBox();
        // limpia caché y refresca base
        suc.cache = Object.create(null);
        (async()=>{ try{
          const fresh = await fetchPage("", 1);
          suc.basePage = fresh.results||[]; suc.baseHasMore = !!fresh.has_more;
        }catch(_){}})();
      }else if(isJson){
        const errs = JSON.parse(data.errors||"{}");
        for(const [f,arr] of Object.entries(errs)) arr.forEach(e=>UI.fErr(f,e.message));
      }else{
        console.error("Respuesta no JSON:", data);
        UI.err("Ocurrió un error inesperado. (Respuesta no válida del servidor)");
      }
    }catch(err){
      console.error(err);
      UI.err("Ocurrió un error inesperado.");
    }
  });

  /* ───── Enter → siguiente / autocomplete / “Dinero en Caja” (form) → Agregar ───── */
  (function setupEnterNavigation(){
    if (!dom.form) return;

    function visible(el){ return !!(el && el.offsetParent !== null); }

    function getFocusables(){
      return Array.from(
        dom.form.querySelectorAll(
          'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
        )
      ).filter(visible);
    }

    function selectFirstAutocomplete(){
      const first = dom.sucBox && dom.sucBox.style.display !== "none"
        ? dom.sucBox.querySelector(".autocomplete-option")
        : null;
      if (first) {
        dom.sucInp.value = first.textContent;
        dom.sucHid.value = first.dataset.id || "";
        dom.sucBox.style.display = "none";
        return true;
      }
      return false;
    }

    dom.form.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;

      const t = e.target;
      if (t.tagName === "TEXTAREA" && e.shiftKey) return;

      e.preventDefault();

      // ① Si estamos en “Dinero en Caja” del FORM → Agregar
      if (t === dom.cajaInp) { dom.btnAdd?.click(); return; }

      const focusables = getFocusables();
      const idx = focusables.indexOf(t);
      const isLast = idx === focusables.length - 1;

      // ② Autocomplete sucursal → toma 1ª opción si hay
      if (t === dom.sucInp) { selectFirstAutocomplete(); }

      // ③ Último campo → Agregar
      if (isLast) { dom.btnAdd?.click(); return; }

      // ④ Enfoca siguiente campo
      const next = focusables[idx + 1];
      if (next) {
        next.focus();
        if (typeof next.select === "function" && /^(INPUT|TEXTAREA)$/.test(next.tagName)) {
          try { next.select(); } catch {}
        }
      }
    });
  })();

})();
