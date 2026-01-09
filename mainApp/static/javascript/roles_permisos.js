/* roles_permisos.js – autocompletes (rol, permiso), DataTable y guardado */
(() => {
  "use strict";

  /* ========= Helpers ========= */
  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);

  async function fetchJSON(url, opts = {}) {
    const r = await fetch(url, opts);
    const txt = await r.text();
    try { return JSON.parse(txt); }
    catch { const e = new Error("Respuesta no JSON"); e.payload = txt; throw e; }
  }

  function getCSRF() {
    return document.cookie.split(";").map(c=>c.trim()).find(c=>c.startsWith("csrftoken="))?.split("=")[1] || "";
  }

  const debounce = (fn, ms=120) => { let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; };

  /* ========= DataTable ========= */
  const dt = $('#permisos-list').DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{
      search:"Buscar:", zeroRecords:"No se encontraron permisos",
      emptyTable:"No hay permisos agregados"
    }
  });

  const COL_LABELS = ["Permiso", "Acciones"];
  function setDataLabels($row){
    $row.find("td").each((i,td)=>td.setAttribute("data-label", COL_LABELS[i]||""));
  }
  dt.on("draw", ()=>$('#permisos-list tbody tr').each(function(){ setDataLabels($(this)); }));

  /* ========= DOM ========= */
  const dom = {
    form:$id("rolPermisoForm"),
    rolInp:$id("id_rol_autocomplete"),
    rolHid:$id("id_rol"),
    rolBox:$id("rol-autocomplete-results"),

    perInp:$id("id_permiso_autocomplete"),
    perHid:$id("id_permisoid"),
    perBox:$id("permiso-autocomplete-results"),

    btnAdd:$id("agregarPermisoBtn"),
    tmp:$id("id_permisos_temp"),

    alertErr:$id("error-message"),
    alertOk :$id("success-message"),
  };

  /* ========= Estado ========= */
  const state = {
    rol:{ page:1, term:"", cache:Object.create(null), more:true, loading:false, aborter:null, reqId:0, basePage:[], baseHasMore:true, activeIndex:-1, pool:[] },
    per:{ page:1, term:"", cache:Object.create(null), more:true, loading:false, aborter:null, reqId:0, basePage:[], baseHasMore:true, activeIndex:-1, pool:[] },
    items:[],                    // [{permisoId, permisoName}]
    excludedIds: new Set()       // espejo rápido de items para exclusión instantánea
  };

  /* ========= UI helpers ========= */
  const UI = {
    clearAlerts(){ [dom.alertErr,dom.alertOk].forEach(a=>{a.style.display="none";a.innerHTML="";}); },
    ok(m){ dom.alertOk.innerHTML=`<i class="fas fa-check-circle"></i> ${m}`; dom.alertOk.style.display="block"; },
    err(m){ dom.alertErr.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${m}`; dom.alertErr.style.display="block"; },
    clearErrors(){ $qsa(".field-error").forEach(d=>{d.classList.remove("visible");d.innerHTML="";}); $qsa(".input-error").forEach(i=>i.classList.remove("input-error")); },
    fieldError(field,msg){
      const box=$qs(`#error-id_${field}`); if(box){ box.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${msg}`; box.classList.add("visible"); }
      ({rol:dom.rolInp, permisoid:dom.perInp}[field]||null)?.classList.add("input-error");
    }
  };

  /* ========= Utilidades exclusión/repintado rápido ========= */
  function syncExcluded() {
    state.excludedIds = new Set(state.items.map(i => String(i.permisoId)));
  }
  function removeOptionFromOpenList(box, idToRemove) {
    if (box.style.display === "none") return;
    const opt = box.querySelector(`.autocomplete-option[data-id="${idToRemove}"]`);
    if (opt) opt.remove();
    if (!box.querySelector(".autocomplete-option")) {
      box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
    }
  }
  function addOptionBackToOpenList(box, optionObj) {
    if (box.style.display === "none") return;
    const t = (dom.perInp.value || "").trim().toLowerCase();
    if (!optionObj.text.toLowerCase().includes(t)) return;
    if (box.querySelector(`.autocomplete-option[data-id="${optionObj.id}"]`)) return;
    const d = document.createElement("div");
    d.className = "autocomplete-option";
    d.dataset.id = optionObj.id;
    d.textContent = optionObj.text;
    const empty = box.querySelector(".autocomplete-no-result");
    if (empty) box.innerHTML = "";
    box.appendChild(d);
  }

  /* ========= Scoring/filtrado local (instantáneo) ========= */
  const norm = s => (s||"")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g,"")
    .trim();

  function scoreCandidate(termN, textN){
    if (!termN) return 1;
    const idx = textN.indexOf(termN);
    if (idx === 0) return 3;
    if (idx > 0 && /[\s_\-./]/.test(textN[idx-1])) return 2;
    return idx >= 0 ? 1.5 - Math.min(idx/100, 1.49) : 0;
  }

  function filterSortLocal(kind, term){
    const S = state[kind];
    const termN = norm(term);
    const exclude = kind === "per" ? state.excludedIds : null;

    const base = S.pool.length ? S.pool : S.basePage;
    let arr = base;

    if (exclude) arr = arr.filter(x => !exclude.has(String(x.id)));
    if (!termN) return arr.slice(0, 50);

    const withScore = [];
    for (const it of arr){
      const sc = scoreCandidate(termN, norm(it.text));
      if (sc > 0) withScore.push([sc, it]);
    }
    withScore.sort((a,b)=> b[0]-a[0] || String(a[1].text).localeCompare(String(b[1].text)));
    return withScore.slice(0,50).map(x=>x[1]);
  }

  /* ========= Autocomplete genérico (instantáneo al tipear) ========= */
  function makeAutocomplete(kind){
    const S = state[kind];
    const cfg = (kind==="rol") ? {
      inp:dom.rolInp, hid:dom.rolHid, box:dom.rolBox,
      url:(term,page)=>`${rolAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}`
    } : {
      inp:dom.perInp, hid:dom.perHid, box:dom.perBox,
      url:(term,page)=>{
        const ex = [...state.excludedIds].join(",");
        return `${permisoAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}&excluded=${ex}`;
      }
    };

    const hasSelection = () => !!cfg.hid.value;
    const isLastAC     = () => kind === "per";

    const open =()=>{ if (hasSelection()) return; cfg.box.style.display="block"; };
    const close=()=>{ cfg.box.style.display="none"; S.activeIndex=-1; };

    const paint = (list, replace=true)=>{
      requestAnimationFrame(()=>{
        const frag=document.createDocumentFragment();
        list.forEach(r=>{
          if (kind==="per" && state.excludedIds.has(String(r.id))) return;
          const d=document.createElement("div");
          d.className="autocomplete-option";
          d.dataset.id=r.id;
          d.textContent=r.text;
          frag.appendChild(d);
        });
        if(replace) cfg.box.replaceChildren(frag); else cfg.box.appendChild(frag);
        if (!cfg.box.children.length) cfg.box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
        open();
      });
    };

    // Fusiona resultados en el pool local
    function mergeIntoPool(results){
      if (!Array.isArray(results) || !results.length) return;
      const seen = new Set(S.pool.map(x=>String(x.id)));
      for(const r of results){
        const id = String(r.id);
        if (!seen.has(id)) { S.pool.push(r); seen.add(id); }
      }
    }

    async function fetchPage(term,page){
      const key=`${term}::${page}::${kind==='per' ? [...state.excludedIds].join(',') : ''}`;
      if(S.cache[key]) return S.cache[key];
      const ctrl = new AbortController();
      if (S.aborter) { try { S.aborter.abort(); } catch(_){} }
      S.aborter = ctrl;
      const data = await fetchJSON(cfg.url(term,page), {signal: ctrl.signal});
      S.cache[key]=data;
      return data;
    }

    async function server({reset=true}={}){
      if(S.loading||!S.more) return;
      const myReq=++S.reqId, termAt=S.term, pageAt=S.page;
      S.loading=true;
      try{
        const data=await fetchPage(termAt,pageAt);
        if(myReq!==S.reqId || termAt!==S.term || pageAt!==S.page) return;

        const raw = data.results||[];
        mergeIntoPool(raw);
        if(reset){
          const list = filterSortLocal(kind, S.term);
          paint(list, true);
        }
        S.more=!!data.has_more;
        if (!S.more && reset && !cfg.box.children.length) {
          cfg.box.innerHTML=`<div class="autocomplete-no-result">Sin resultados</div>`;
          open();
        }
      }catch(e){
        if (e.name !== "AbortError") console.error("Autocomplete error:", e);
      }finally{
        S.loading=false;
      }
    }
    const serverDebounced = debounce(()=>server({reset:true}), 120);

    function live(term, doFetch=true){
      const list = filterSortLocal(kind, term);
      paint(list, true);
      if (doFetch){
        S.page=1; S.more=true;
        serverDebounced();
      }
    }

    // ======== Seleccionar primera opción y avanzar ========
    function selectFirstAndAdvance(){
      if (hasSelection()) { advanceAfterSelect(); return true; }
      let first = cfg.box.querySelector(".autocomplete-option");
      if (!first){
        const local = filterSortLocal(kind, S.term);
        if (!local.length) return false;
        cfg.inp.value = local[0].text;
        cfg.hid.value = String(local[0].id);
      } else {
        first.click();
      }
      close();
      setTimeout(advanceAfterSelect, 0);
      return true;
    }

    function advanceAfterSelect(){
      if (isLastAC()) {
        // último AC (permiso): agrega a la tabla
        addPermissionToTable(true); // true = viene de Enter, reabrir AC
      } else {
        // foco al siguiente campo (permiso) y abre sugerencias
        dom.perInp.focus();
        state.per.term = dom.perInp.value.trim();
        live(state.per.term, true);
      }
    }

    // Input (instantáneo)
    cfg.inp.addEventListener("input",()=>{
      cfg.hid.value="";              // limpiar selección -> permite abrir
      S.term=cfg.inp.value.trim();
      live(S.term, true);
    });

    // Focus
    cfg.inp.addEventListener("focus",()=>{
      if (hasSelection()) { close(); return; }
      S.term=cfg.inp.value.trim();
      live(S.term, true);
    });

    // Navegación teclado (+ Enter = primera opción y avanzar)
    cfg.inp.addEventListener("keydown", (e)=>{
      if (e.key === "Enter") {
        e.preventDefault();
        selectFirstAndAdvance();
        return;
      }

      if (cfg.box.style.display === "none") return;
      const opts = [...cfg.box.querySelectorAll(".autocomplete-option")];
      if (!opts.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault(); S.activeIndex = (S.activeIndex + 1) % opts.length;
      } else if (e.key === "ArrowUp") {
        e.preventDefault(); S.activeIndex = (S.activeIndex - 1 + opts.length) % opts.length;
      } else if (e.key === "Escape") {
        close();
      }
      opts.forEach((el,i)=>el.classList.toggle("is-active", i===S.activeIndex));
    });

    // Escucha cambios de items (para permisos): repintado instantáneo
    if (kind === "per") {
      const refreshFast = () => {
        if (cfg.box.style.display !== "none") {
          live(S.term, false);
        }
      };
      document.addEventListener("rolespermisos:items-changed", refreshFast);

      // 🔥 Reabrir el AC de permisos mostrando restantes (tras agregar con Enter)
      document.addEventListener("rolespermisos:reopen-per", () => {
        cfg.hid.value = "";
        cfg.inp.value = "";
        S.term = "";
        live("", true);     // pinta al instante y trae server
        cfg.inp.focus();    // deja foco para seguir agregando
      });
    }

    // Scroll infinito
    cfg.box.addEventListener("scroll",()=>{
      if(cfg.box.scrollTop + cfg.box.clientHeight >= cfg.box.scrollHeight - 4 && S.more && !S.loading){
        S.page++; server({reset:false});
      }
    });

    // Click opción -> cierra y fija selección
    cfg.box.addEventListener("click", e=>{
      const opt=e.target.closest(".autocomplete-option"); if(!opt) return;
      cfg.inp.value=opt.textContent;
      cfg.hid.value=String(opt.dataset.id);
      close();
    });

    // Cerrar por click fuera
    document.addEventListener("click", e=>{
      if(!cfg.inp.contains(e.target) && !cfg.box.contains(e.target)) close();
    });

    // Prefetch base
    (async function prefetchBase(){
      try{
        const data=await fetchPage("",1);
        S.basePage=data.results||[];
        S.baseHasMore=!!data.has_more;
        S.pool = [...S.basePage];
      }catch(_){}
    })();
  }
  makeAutocomplete("rol");
  makeAutocomplete("per");

  /* ========= Disparador de cambio de items ========= */
  function dispatchItemsChanged(){
    document.dispatchEvent(new CustomEvent("rolespermisos:items-changed"));
  }

  /* ========= Agregar permiso a la lista (reutilizable desde Enter) ========= */
  function addPermissionToTable(fromEnter=false){
    UI.clearAlerts(); UI.clearErrors();

    const rid = dom.rolHid.value.trim();
    const pid = dom.perHid.value.trim();
    const pname = dom.perInp.value.trim();

    let bad=false;
    if(!rid){ UI.fieldError("rol", "Debe seleccionar un rol."); bad=true; }
    if(!pid){ UI.fieldError("permisoid", "Debe seleccionar un permiso."); bad=true; }
    if(bad) return;

    if(state.items.some(i=>String(i.permisoId)===String(pid))){
      UI.fieldError("permisoid","El permiso ya está en la lista."); return;
    }

    state.items.push({permisoId:pid, permisoName:pname});
    syncExcluded();

    const node = dt.row.add([
      pname,
      `<button type="button" class="btn-eliminar" data-perm-id="${pid}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();
    setDataLabels($(node));

    // limpiar selección y cerrar dropdown actual
    dom.perInp.value=""; dom.perHid.value="";
    removeOptionFromOpenList(dom.perBox, pid);
    dom.perBox.style.display="none";

    state.per.cache = Object.create(null);
    state.per.pool = state.per.pool.filter(x => String(x.id) !== String(pid));
    dispatchItemsChanged();

    // 🔥 si vino por Enter, reabre inmediatamente mostrando restantes
    if (fromEnter) {
      document.dispatchEvent(new CustomEvent("rolespermisos:reopen-per"));
    } else {
      // comportamiento normal: dejar foco en permiso
      dom.perInp.focus();
    }
  }

  // Botón Agregar
  dom.btnAdd.addEventListener("click", () => addPermissionToTable(false));

  /* ========= Eliminar fila ========= */
  const tbodyPerm = $qs("#permisos-body") || $qs("#permisos-list tbody");
  tbodyPerm?.addEventListener("click", e=>{
    const btn=e.target.closest(".btn-eliminar"); if(!btn) return;
    const pid=btn.dataset.permId;
    const row = btn.closest("tr");
    const name = row?.querySelector("td")?.textContent?.trim() || "";

    dt.row(row).remove().draw(false);
    state.items = state.items.filter(i=>String(i.permisoId)!==String(pid));
    syncExcluded();

    if (name && !state.per.pool.some(x => String(x.id) === String(pid))) {
      state.per.pool.push({ id: pid, text: name });
    }

    state.per.cache = Object.create(null);
    if (name) addOptionBackToOpenList(dom.perBox, { id: pid, text: name });

    dispatchItemsChanged();
  });

  /* ========= Submit ========= */
  dom.form.addEventListener("submit", async ev=>{
    ev.preventDefault(); UI.clearAlerts(); UI.clearErrors();

    if(!state.items.length){
      UI.err("Debe agregar al menos un permiso.");
      return;
    }
    dom.tmp.value = JSON.stringify(state.items);

    try{
      const data = await fetchJSON(dom.form.action, {
        method:"POST",
        headers:{ "X-CSRFToken": getCSRF(), Accept:"application/json" },
        body:new FormData(dom.form)
      });

      if(data.success){
        UI.ok("Permisos asociados correctamente.");
        dom.perInp.value=""; dom.perHid.value="";
        state.items=[]; syncExcluded();
        dt.clear().draw();
        state.per.cache = Object.create(null);
        state.per.pool = [...state.per.basePage];
        dispatchItemsChanged();
      }else{
        const errs = JSON.parse(data.errors || "{}");
        for(const [field,arr] of Object.entries(errs)) arr.forEach(e=>UI.fieldError(field, e.message||e));
      }
    }catch(err){
      console.error("Error submit:", err.payload || err);
      UI.err("Error interno del servidor. Revisa logs.");
    }
  });
})();
