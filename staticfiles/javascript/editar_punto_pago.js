/*  static/javascript/editar_punto_pago.js
    ────────────────────────────────────────────────────────────
    · Autocomplete (scroll-infinito + caché)
    · DataTable card-view ≤ 768 px
    · Edición inline y submit AJAX
    · Limpieza AGRESIVA de errores server-side al cargar
----------------------------------------------------------------*/
(() => {
  "use strict";

  /* ───── helpers ───── */
  const $id  = id => document.getElementById(id);
  const $qs  = s  => document.querySelector(s);
  const $qsa = s  => document.querySelectorAll(s);

  /* ───── refs DOM ───── */
  const dom = {
    form   : $id("puntoPagoForm"),

    sucInp : $id("id_sucursal_autocomplete"),
    sucHid : $id("id_sucursal"),
    sucBox : $id("sucursal-autocomplete-results"),

    nomInp : $id("id_nombre"),
    desInp : $id("id_descripcion"),
    cajaInp: $id("id_dinerocaja"),

    btnAdd : $id("agregarPuntoPagoBtn"),
    table  : $id("puntos-pago-list"),
    tbody  : $id("puntos-pago-body"),
    hidden : $id("id_puntos_temp"),

    alertErr: $id("error-message"),
    alertOk : $id("success-message"),
  };

  /* ───── DataTable ───── */
  const HEADS = ["Nombre","Descripción","Dinero en Caja","Acciones"];
  const dt = $("#puntos-pago-list").DataTable({
    paging:false, searching:true, info:false, responsive:true,
    language:{
      search:"Buscar:",
      zeroRecords:"No se encontraron resultados",
      emptyTable:"No hay puntos de pago para mostrar"
    }
  });
  const setLabels = $row =>
    $row.find("td").each((i,td)=>td.setAttribute("data-label", HEADS[i]||""));

  /* ───── state ───── */
  const state = {
    suc  : { page:1, term:"", loading:false, more:true },
    items: []   // [{id?, nombre, descripcion, dinerocaja}]
  };

  /* ───── UI helpers ───── */
  const UI = {
    clrAlerts(){
      [dom.alertErr, dom.alertOk].forEach(el=>{ el.style.display="none"; el.innerHTML=""; });
    },
    ok(msg){
      dom.alertOk.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`;
      dom.alertOk.style.display="block";
    },
    err(msg){
      dom.alertErr.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
      dom.alertErr.style.display="block";
    },
    clrFieldErr(){
      $qsa(".field-error").forEach(e=>{ e.innerHTML=""; e.classList.remove("visible"); });
      $qsa(".input-error").forEach(i=>i.classList.remove("input-error"));
    },
    fErr(field,msg){
      const box=$qs(`#error-id_${field}`);
      if(box){
        box.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
        box.classList.add("visible");
      }
      const map={sucursal:dom.sucInp, nombre:dom.nomInp};
      (map[field]||null)?.classList.add("input-error");
    }
  };

  /* ───── Limpieza agresiva de errores server-side ───── */
  function clearNombreError() {
    const box = $id("error-id_nombre");
    if (box) { box.textContent = ""; box.classList.remove("visible"); }
    dom.nomInp?.classList.remove("input-error");
    // Si el input está vacío, quita cualquier <ul class="errorlist"> que Django haya dejado
    const ul = dom.nomInp?.parentElement?.querySelector("ul.errorlist");
    if (ul) ul.remove();
  }
  function clearAllErrorsOnLoad() {
    UI.clrFieldErr();
    clearNombreError();
  }
  // Ejecuta la limpieza al cargar y ante cambios en sucursal/nombre
  document.addEventListener("DOMContentLoaded", clearAllErrorsOnLoad);
  clearAllErrorsOnLoad();
  ["input","focus"].forEach(evt => dom.nomInp?.addEventListener(evt, clearNombreError));
  dom.sucInp?.addEventListener("input", () => { dom.sucHid.value = ""; clearNombreError(); });

  // Evitar Enter = submit accidental en los inputs cortos
  ["id_nombre","id_descripcion","id_dinerocaja"].forEach(id=>{
    const el=$id(id);
    el?.addEventListener("keydown",e=>{
      if(e.key==="Enter"){ e.preventDefault(); }
    });
  });

  /* ───── cache + debounce ───── */
  const cSuc = Object.create(null);
  const fetchC = async(url,cache)=>{
    if(cache[url]) return cache[url];
    const r = await fetch(url); const j = await r.json();
    cache[url]=j; return j;
  };
  const debounce = (fn,ms=300)=>{let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);};};

  /* ───── autocomplete sucursal ───── */
  const renderSuc = async()=>{
    const st = state.suc;
    if(st.loading || !st.more) return;
    st.loading = true;

    const qs = new URLSearchParams({term:st.term,page:st.page});
    const data = await fetchC(`${sucursalAutocompleteUrl}&${qs}`, cSuc);

    if(st.page===1) dom.sucBox.innerHTML="";
    if(data.results.length){
      data.results.forEach(r=>{
        const d=document.createElement("div");
        d.className="autocomplete-option"; d.dataset.id=r.id; d.textContent=r.text;
        dom.sucBox.appendChild(d);
      });
      st.more = data.has_more;
    }else if(st.page===1){
      dom.sucBox.innerHTML='<div class="autocomplete-no-result">No se encontraron resultados</div>';
      st.more = false;
    }
    dom.sucBox.style.display="block";
    st.loading = false;
  };
  const debSuc = debounce(()=>{ state.suc.page=1; state.suc.more=true; renderSuc(); });

  dom.sucInp.addEventListener("input",()=>{
    dom.sucHid.value="";
    state.suc.term = dom.sucInp.value.trim();
    debSuc();
  });
  dom.sucInp.addEventListener("focus",()=>{
    state.suc.term = dom.sucInp.value.trim();
    state.suc.page = 1; state.suc.more = true;
    renderSuc();
  });
  dom.sucBox.addEventListener("scroll",()=>{
    if(dom.sucBox.scrollTop+dom.sucBox.clientHeight>=dom.sucBox.scrollHeight-4 && state.suc.more && !state.suc.loading){
      state.suc.page++; renderSuc();
    }
  });
  dom.sucBox.addEventListener("click",e=>{
    const opt=e.target.closest(".autocomplete-option"); if(!opt) return;
    dom.sucInp.value = opt.textContent;
    dom.sucHid.value = opt.dataset.id;
    dom.sucBox.style.display="none";
  });
  document.addEventListener("click",e=>{
    if(!dom.sucInp.contains(e.target)&&!dom.sucBox.contains(e.target)) dom.sucBox.style.display="none";
  });

  /* ───── precarga BD (existingPuntos) ───── */
  (existingPuntos || []).forEach(p=>{
    state.items.push({
      id: p.puntopagoid,
      nombre: p.nombre,
      descripcion: p.descripcion ?? "",
      dinerocaja: p.dinerocaja ?? "0"
    });
    const node = dt.row.add([
      p.nombre,
      `<input type="text"  class="edit-descripcion" data-nombre="${p.nombre}" value="${p.descripcion ?? ""}">`,
      `<input type="number" class="edit-dinerocaja"  data-nombre="${p.nombre}" value="${p.dinerocaja ?? "0"}" min="0" step="0.01">`,
      `<button type="button" class="btn-eliminar" data-nombre="${p.nombre}">
         <i class="fas fa-trash-alt"></i>
       </button>`
    ]).draw(false).node();
    setLabels($(node));
  });

  /* ───── agregar / actualizar punto ───── */
  dom.btnAdd.addEventListener("click", () => {
    UI.clrAlerts(); UI.clrFieldErr(); clearNombreError();

    const sid    = dom.sucHid.value.trim();
    const nombre = dom.nomInp.value.trim();
    const descr  = dom.desInp.value.trim();
    const caja   = (dom.cajaInp.value.trim() || "0");

    let bad=false;
    if(!sid){   UI.fErr("sucursal","Seleccione una sucursal."); bad=true; }
    if(!nombre){UI.fErr("nombre","El nombre es obligatorio.");  bad=true; }
    if(bad) return;

    const idx = state.items.findIndex(
      i => i.nombre.trim().toLowerCase() === nombre.toLowerCase()
    );

    if (idx >= 0) {
      // Actualiza en memoria y en la fila existente
      state.items[idx].descripcion = descr;
      state.items[idx].dinerocaja  = caja;

      const dInp = dom.tbody.querySelector(`.edit-descripcion[data-nombre="${state.items[idx].nombre}"]`);
      const cInp = dom.tbody.querySelector(`.edit-dinerocaja[data-nombre="${state.items[idx].nombre}"]`);
      if (dInp) dInp.value = descr;
      if (cInp) cInp.value = caja;

      UI.ok("Punto de pago actualizado.");
    } else {
      // Alta normal
      state.items.push({ id:null, nombre, descripcion: descr, dinerocaja: caja });
      const node = dt.row.add([
        nombre,
        `<input type="text"  class="edit-descripcion" data-nombre="${nombre}" value="${descr}">`,
        `<input type="number" class="edit-dinerocaja"  data-nombre="${nombre}" value="${caja}" min="0" step="0.01">`,
        `<button type="button" class="btn-eliminar" data-nombre="${nombre}">
           <i class="fas fa-trash-alt"></i>
         </button>`
      ]).draw(false).node();
      setLabels($(node));
      UI.ok("Punto de pago agregado.");
    }

    // Limpiar formulario pequeño
    dom.nomInp.value = "";
    dom.desInp.value = "";
    dom.cajaInp.value = "";
    clearNombreError();
  });

  /* ───── eliminar ───── */
  dom.tbody.addEventListener("click",e=>{
    const btn=e.target.closest(".btn-eliminar"); if(!btn) return;
    const nombre=btn.dataset.nombre.toLowerCase();
    dt.row(btn.closest("tr")).remove().draw(false);
    state.items = state.items.filter(i=>i.nombre.toLowerCase()!==nombre);
  });

  /* ───── edición inline en la tabla ───── */
  dom.tbody.addEventListener("input",e=>{
    const t=e.target;
    if(!t.dataset.nombre) return;
    const nombre=t.dataset.nombre.toLowerCase();
    const idx=state.items.findIndex(i=>i.nombre.toLowerCase()===nombre);
    if(idx<0) return;
    if(t.classList.contains("edit-descripcion")) state.items[idx].descripcion = t.value;
    if(t.classList.contains("edit-dinerocaja"))  state.items[idx].dinerocaja  = t.value;
  });

  /* ───── submit ───── */
  dom.form.addEventListener("submit",async ev=>{
    ev.preventDefault();
    UI.clrAlerts(); UI.clrFieldErr(); clearNombreError();

    if(!dom.sucHid.value.trim()){
      UI.fErr("sucursal","Seleccione una sucursal."); return;
    }
    if(!state.items.length){
      UI.err("Debe agregar al menos un punto de pago."); return;
    }
    dom.hidden.value = JSON.stringify(state.items);

    try{
      const r = await fetch(dom.form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":document.cookie.split(";").find(c=>c.trim().startsWith("csrftoken="))?.split("=")[1]||"",
          Accept:"application/json"
        },
        body:new FormData(dom.form)
      });
      const data = await r.json();

      if(data.success){
        window.location.href = data.redirect_url;
      }else{
        const errs = typeof data.errors === "string" ? JSON.parse(data.errors||"{}") : (data.errors||{});
        Object.entries(errs).forEach(([f,arr])=>
          arr.forEach(e=>{
            if(f==="puntos_temp"){ UI.err(e.message); }
            else                 { UI.fErr(f,e.message); }
          })
        );
      }
    }catch(e){
      console.error(e);
      UI.err("Ocurrió un error inesperado.");
    }
  });
})();
