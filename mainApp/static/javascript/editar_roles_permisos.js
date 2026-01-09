/* editar_roles_permisos.js
   — ALTAS en buffer (state.items) y BAJAS en buffer (state.toRemove)
   — Borrar = SOLO visual + buffer; reaparece en autocomplete al instante
   — Autocomplete excluye: en tabla + en buffer; incluye pending_remove
*/
(() => {
  "use strict";

  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);

  const dt = $('#permisos-list').DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{ search:"Buscar:", zeroRecords:"No hay permisos", emptyTable:"No hay permisos" },
    columnDefs:[{ targets:"no-sort", orderable:false }]
  });

  const COL_LABELS = ["Permiso","Acciones"];
  function setDataLabels($row){
    $row.find("td").each((i,td)=>td.setAttribute("data-label", COL_LABELS[i]||""));
  }
  dt.on("draw", ()=>$('#permisos-list tbody tr').each(function(){ setDataLabels($(this)); }));

  const dom = {
    form:$id("rolPermisoForm"),
    perInp:$id("id_permiso_autocomplete"),
    perHid:$id("id_permisoid"),
    perBox:$id("permiso-autocomplete-results"),
    btnAdd:$id("agregarPermisoBtn"),
    tbody:$id("permisos-body"),
    tmpAdd:$id("id_permisos_temp"),
    tmpDel:$id("id_permisos_borrar"),
    alertOk:$id("success-message"),
    alertErr:$id("error-message"),
  };

  const state = {
    items:[],       // ALTAS: [{permisoId, permisoName}]
    toRemove:[],    // BAJAS: [permisoId,...] (solo marca; DB al guardar)
    cache:Object.create(null),
    basePage:[], baseHasMore:true,
    page:1, term:"", loading:false, more:true, reqId:0, xhr:null
  };

  const UI = {
    clearAlerts(){ [dom.alertOk,dom.alertErr].forEach(b=>{b.style.display="none";b.innerHTML="";}); },
    ok(m){ dom.alertOk.innerHTML=`<i class="fas fa-check-circle"></i> ${m}`; dom.alertOk.style.display="block"; },
    err(m){ dom.alertErr.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${m}`; dom.alertErr.style.display="block"; },
    clearErrors(){
      $qsa(".field-error").forEach(d=>{d.classList.remove("visible");d.innerHTML="";});
      $qsa(".input-error").forEach(i=>i.classList.remove("input-error"));
    },
    fieldError(field,msg){
      const box=$qs(`#error-id_${field}`);
      if(box){ box.innerHTML=`<i class="fas fa-exclamation-circle"></i> ${msg}`; box.classList.add("visible"); }
      if(field==="permisoid"){ dom.perInp.classList.add("input-error"); }
    }
  };

  // IDs a excluir del autocomplete (los que están en la tabla + los agregados en buffer)
  function currentPermIds(){
    const fromTable = Array.from(dom.tbody.querySelectorAll("td[data-perm-id]"))
      .map(td => td.getAttribute("data-perm-id"))
      .filter(Boolean);
    const fromTmp   = state.items.map(x=>String(x.permisoId));
    return Array.from(new Set([...fromTable, ...fromTmp]));
  }
  const pendingRemoveIds = () => state.toRemove.map(String);

  function paint(list, replace=true){
    requestAnimationFrame(()=>{
      if(replace) dom.perBox.innerHTML="";
      const frag=document.createDocumentFragment();
      (list||[]).forEach(r=>{
        const d=document.createElement("div");
        d.className="autocomplete-option";
        d.dataset.id=r.id; d.textContent=r.text;
        frag.appendChild(d);
      });
      dom.perBox.appendChild(frag);
      dom.perBox.style.display="block";
    });
  }

  function fetchPage(q,p,{replace=true}={}){
    const excluded = currentPermIds().join(",");
    const pend     = pendingRemoveIds().join(",");
    const key = `${q}::${p}::${excluded}::${pend}`;
    if(state.cache[key]){
      const data = state.cache[key];
      paint(data.results||[], replace);
      state.more = !!data.has_more;
    }
    if(state.xhr && state.xhr.readyState !== 4){ try{ state.xhr.abort(); }catch(e){} }
    const myReq = ++state.reqId; state.loading = true;

    const url =
      `${permisoAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}` +
      `&excluded=${encodeURIComponent(excluded)}` +
      `&pending_remove=${encodeURIComponent(pend)}` +
      `&rol_id=${encodeURIComponent(currentRolId)}`;

    state.xhr = $.getJSON(url)
      .done(data=>{
        state.cache[key] = data || {results:[], has_more:false};
        if(myReq !== state.reqId || q !== state.term || p !== state.page) return;
        if(!replace && p>1){ paint(data.results||[], false); }
        else { dom.perBox.innerHTML=""; paint(data.results||[], true); }
        state.more = !!data.has_more;
      })
      .always(()=>{ state.loading=false; });
  }

  function instant(){
    const excluded = currentPermIds().join(",");
    const pend     = pendingRemoveIds().join(",");
    const key = `${state.term}::1::${excluded}::${pend}`;
    let painted=false;

    if(state.cache[key]?.results){
      dom.perBox.innerHTML=""; paint(state.cache[key].results,true);
      state.more = !!state.cache[key].has_more; painted=true;
    }else if(state.basePage.length && !state.term){
      // filtra base por excluded (pendiente remove NO se excluye)
      const exclSet = new Set(currentPermIds().map(String));
      const local = state.basePage.filter(r => !exclSet.has(String(r.id)));
      dom.perBox.innerHTML=""; paint(local,true);
      state.more = state.baseHasMore; painted=true;
    }
    state.page=1; state.more=true;
    fetchPage(state.term, state.page, {replace:true});
    if(!painted) dom.perBox.style.display="block";
  }

  function refreshAutocompleteNow(){
    // invalida caché y fuerza una consulta fresca con los nuevos excluded/pending_remove
    state.cache = Object.create(null);
    if (document.activeElement === dom.perInp) {
      if (state.term) instant();
      else { dom.perBox.innerHTML=""; fetchPage("", 1, {replace:true}); }
    }
  }

  dom.perInp.addEventListener("input", ()=>{
    dom.perHid.value="";
    state.term = dom.perInp.value.trim();
    if(!state.term){
      state.page=1; state.more=state.baseHasMore;
      if(state.basePage.length){
        const exclSet = new Set(currentPermIds().map(String));
        paint(state.basePage.filter(r => !exclSet.has(String(r.id))), true);
      }
      fetchPage("",1,{replace:true});
      return;
    }
    instant();
  });

  dom.perInp.addEventListener("focus", ()=>{
    state.term = dom.perInp.value.trim();
    state.page = 1; state.more = true;
    if(!state.term){
      if(state.basePage.length){
        const exclSet = new Set(currentPermIds().map(String));
        paint(state.basePage.filter(r => !exclSet.has(String(r.id))), true);
      }
      fetchPage("",1,{replace:true});
    }else{
      instant();
    }
  });

  dom.perBox.addEventListener("scroll", ()=>{
    if(dom.perBox.scrollTop + dom.perBox.clientHeight >= dom.perBox.scrollHeight - 4 &&
       state.more && !state.loading){
      state.page += 1; fetchPage(state.term, state.page, {replace:false});
    }
  });

  dom.perBox.addEventListener("click", e=>{
    const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
    dom.perInp.value = opt.textContent;
    dom.perHid.value = opt.dataset.id;
    dom.perBox.style.display = "none";
  });

  document.addEventListener("click", e=>{
    if(!dom.perInp.contains(e.target) && !dom.perBox.contains(e.target)){
      dom.perBox.style.display = "none";
    }
  });

  // Prefetch base (página 1; incluye pending_remove)
  (function prefetchBase(){
    const excl = currentPermIds().join(",");
    const pend = pendingRemoveIds().join(",");
    const key = `::1::${excl}::${pend}`;
    if(state.cache[key]){
      state.basePage    = state.cache[key].results||[];
      state.baseHasMore = !!state.cache[key].has_more;
      return;
    }
    const url =
      `${permisoAutocompleteUrl}?term=&page=1` +
      `&excluded=${encodeURIComponent(excl)}` +
      `&pending_remove=${encodeURIComponent(pend)}` +
      `&rol_id=${encodeURIComponent(currentRolId)}`;
    $.getJSON(url).done(data=>{
      state.cache[key]   = data || {results:[], has_more:false};
      state.basePage     = state.cache[key].results||[];
      state.baseHasMore  = !!state.cache[key].has_more;
    });
  })();

  // Agregar (buffer)
  dom.btnAdd.addEventListener("click", ()=>{
    UI.clearAlerts(); UI.clearErrors();

    const pid   = dom.perHid.value.trim();
    const pname = dom.perInp.value.trim();

    if(!pid){ UI.fieldError("permisoid","Debe seleccionar un permiso."); return; }
    if(currentPermIds().includes(pid)){
      UI.fieldError("permisoid","Ese permiso ya está en la lista."); return;
    }

    state.items.push({permisoId:pid, permisoName:pname});
    // Si estaba marcado para borrar y lo vuelven a agregar, quitarlo de toRemove
    state.toRemove = state.toRemove.filter(x => String(x) !== String(pid));

    const node = dt.row.add([
      `<span>${pname}</span>`,
      `<button type="button" class="btn-eliminar" data-rp-id="">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();
    node.querySelector("td:first-child").setAttribute("data-perm-id", pid);
    setDataLabels($(node));

    dom.perInp.value = ""; dom.perHid.value = "";
    dom.perBox.style.display = "none";

    refreshAutocompleteNow();
  });

  // Borrar (solo visual + buffer) — delegación sobre la tabla
  $('#permisos-list').on('click', '.btn-eliminar', function (e) {
    e.preventDefault();
    const $row = $(this).closest('tr');
    const rowEl = $row.get(0);
    const td    = rowEl.querySelector('td[data-perm-id]');
    const pid   = td ? String(td.getAttribute('data-perm-id')) : "";
    const rid   = this.getAttribute('data-rp-id'); // si venía de DB

    if (!pid) return;

    if (rid) {
      if (!state.toRemove.includes(pid)) state.toRemove.push(pid);
      state.items = state.items.filter(x => String(x.permisoId) !== pid);
    } else {
      state.items = state.items.filter(x => String(x.permisoId) !== pid);
    }

    dt.row($row).remove().draw(false);

    // ahora debe REAPARECER en el autocomplete:
    refreshAutocompleteNow();
  });

  // Guardar cambios (POST: altas + bajas) y redirigir
  dom.form.addEventListener("submit", async (ev)=>{
    ev.preventDefault(); UI.clearAlerts(); UI.clearErrors();

    dom.tmpAdd.value = JSON.stringify(state.items || []);
    dom.tmpDel.value = JSON.stringify(state.toRemove || []);

    try{
      const r = await fetch(dom.form.action, {
        method:"POST",
        headers:{ "X-Requested-With":"XMLHttpRequest", "Accept":"application/json" },
        body: new FormData(dom.form)
      });

      let data;
      try{ data = await r.json(); }
      catch(_){
        const t = await r.text();
        UI.err("Error interno del servidor (no JSON). Revisa logs.");
        console.error("Respuesta no JSON:", t);
        return;
      }

      if(data.success){
        const msg = `Cambios guardados correctamente. (+${data.created||0}, −${data.deleted||0})`;
        sessionStorage.setItem("flash-rp", msg);
        const url = data.redirect_url || (typeof visualizarUrl !== "undefined" ? visualizarUrl : "/");
        window.location.href = url;
      }else{
        const errs = data.errors || {};
        if(errs.__all__){ UI.err((errs.__all__||[]).map(e=>e.message || e).join("<br>")); }
        if(errs.permisoid){ UI.fieldError("permisoid", (errs.permisoid[0]||{}).message || "Permiso inválido."); }
      }
    }catch(err){
      console.error(err); UI.err("Ocurrió un error inesperado.");
    }
  });
})();
