/*  static/javascript/editar_horario_caja.js
    ────────────────────────────────────────────────────────────────
    · Autocompletes “ultra-rápidos” (filtro local + fetch en paralelo)
    · Siempre muestra el ítem actualmente vinculado (sucursal / punto de pago)
    · ENTER:
        - en autocompletes selecciona la 1ª opción y salta al siguiente input
        - en inputs normales salta al siguiente input
        - en Hora de Cierre (ciInp) equivale a “Agregar horario”
    · Selector de días (bloquea botón si el día ya está en la tabla)
    · Tabla editable (añadir / quitar filas)
    · Envío AJAX (JSON) + flashes
*/
(() => {
  "use strict";

  /* ══════════════ refs rápidas ══════════════ */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form  = $("#form-editar-horarios");
  const tabla = $("#tabla-horarios");

  /* → Autocomplete */
  const sucInp = $("#id_sucursal_autocomplete"),
        sucHid = $("#id_sucursalid"),
        sucBox = $("#sucursal-autocomplete-results");

  const ppInp  = $("#id_puntopago_autocomplete"),
        ppHid  = $("#id_puntopagoid"),
        ppBox  = $("#puntopago-autocomplete-results");

  /* valores actuales (pre-cargados en el form) */
  let currentSucId = sucHid.value;
  let currentPpId  = ppHid.value;

  /* → horario nuevo */
  const dayBtns = $$(".day-button"),
        apInp   = $("#horaapertura"),
        ciInp   = $("#horacierre"),
        addBtn  = $("#btn-agregar-horario");

  /* → flashes / errores */
  const err = $("#error-message"),
        ok  = $("#success-message");

  const csrftoken = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const iconErr   = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iconOk    = t => `<i class="fas fa-check-circle"></i> ${t}`;
  const show      = (el,html)=>{ el.innerHTML = html; el.style.display = "block"; };
  const hide      = el        =>{ el.style.display = "none"; el.innerHTML = ""; };

  const fieldErr = (f,m) => {
    const div = $(`#error-id_${f}`);
    div ? show(div, iconErr(m)) : show(err, iconErr(m));
  };
  const resetUI = () => {
    [err, ok].forEach(hide);
    $$(".field-error").forEach(hide);
  };

  /* ══════════════ orden de foco (para salto con ENTER) ══════════════ */
  const focusOrder = [sucInp, ppInp, apInp, ciInp];
  function focusNext(fromEl){
    const list = focusOrder.filter(Boolean);
    const i = list.indexOf(fromEl);
    if(i > -1 && i < list.length - 1){
      list[i+1].focus();
      if(list[i+1].select) list[i+1].select();
    }
  }

  /* ══════════════ helper → congela / libera los botones de día ══════════════ */
  const order = ["Lun","Mar","Mie","Jue","Vie","Sab","Dom"];
  function syncDayButtons () {
    dayBtns.forEach(b => {
      const d = b.dataset.day;
      const exists = !!tabla.querySelector(`tr[data-dia="${d}"]`);
      b.disabled = exists;
      b.classList.remove("active");
    });
    [...tabla.querySelectorAll("tr[data-dia]")]
      .sort((a,b)=> order.indexOf(a.dataset.dia) - order.indexOf(b.dataset.dia))
      .forEach(tr => tabla.appendChild(tr));
  }
  syncDayButtons();

  /* ══════════════ AUTOCOMPLETE ultra-rápido (local + fetch) ══════════════
     + “includeCurrent”: asegura que el valor actualmente vinculado SIEMPRE
       aparezca en la lista (aunque el backend lo excluya).
  */
  function setupAutocomplete (inp, hid, box, url, extraParams, onSelect, includeCurrent) {
    let page=1, more=true, loading=false;
    let snapshot = [];               // última “foto” del fetch [{id,text}]
    const cache  = new Map();        // key -> {items, has_more, ts}

    const norm = s => (s||"").toString()
      .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
      .toLowerCase().replace(/\s+/g," ").trim();

    function injectCurrent(items){
      if(!includeCurrent) return items;
      const cur = includeCurrent();  // {id, text} | null
      if(!cur || !cur.id) return items;
      const exists = items.some(it => String(it.id) === String(cur.id));
      if(!exists){
        // Lo insertamos al principio para que sea siempre visible.
        return [{ id: cur.id, text: cur.text || inp.value || "" }, ...items];
      }
      return items;
    }

    function renderList(list){
      if(!list.length){
        box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
        box.style.display = "block";
        return;
      }
      box.innerHTML = list.map(r =>
        `<div class="autocomplete-option" data-id="${r.id}">${r.text}</div>`
      ).join("");
      box.style.display = "block";
    }

    function instantFilter(term){
      const t = norm(term);
      const base = snapshot.slice(0); // copia
      const arr  = injectCurrent(base); // garantizar current en la instantánea
      if(!t) return arr.slice(0, 40);
      const starts=[], contains=[];
      for(const r of arr){
        const n = norm(r.text);
        if(n.startsWith(t)) starts.push(r);
        else if(n.includes(t)) contains.push(r);
      }
      return [...starts, ...contains].slice(0, 40);
    }

    async function fetchPage(term, pg){
      const params = new URLSearchParams({ term, page: pg });
      if (extraParams){
        const extra = extraParams();
        Object.keys(extra||{}).forEach(k => params.append(k, extra[k]));
      }
      const key = `${url}?${params.toString()}`;
      if(cache.has(key)){
        const data = cache.get(key);
        const itemsWithCur = injectCurrent(data.items);
        if(pg===1) snapshot = itemsWithCur.slice();
        else snapshot = snapshot.concat(itemsWithCur);
        more = !!data.has_more;
        return data;
      }
      loading = true;
      try{
        const res  = await fetch(`${url}?${params.toString()}`);
        const data = await res.json();
        const items = (data.results||[]).map(r => ({ id:r.id, text:r.text }));
        const itemsWithCur = injectCurrent(items);
        cache.set(key, { items: itemsWithCur, has_more: !!data.has_more, ts: Date.now() });
        if(pg===1) snapshot = itemsWithCur.slice(); else snapshot = snapshot.concat(itemsWithCur);
        more = !!data.has_more;
        return { items: itemsWithCur, has_more: more };
      } finally { loading=false; }
    }

    const debounce = (fn, ms=60) => {
      let t; return (...a) => { clearTimeout(t); t=setTimeout(()=>fn(...a), ms); };
    };

    const refresh = async ()=>{
      const term = inp.value.trim();
      renderList(instantFilter(term));   // instantáneo
      page = 1; more = true;
      await fetchPage(term, 1);          // red
      renderList(instantFilter(inp.value.trim())); // re-pintar
    };
    const debRefresh = debounce(refresh, 50);

    // INPUT → rápido al escribir/borrar
    inp.addEventListener("input", ()=>{
      hid.value = "";
      debRefresh();
    });

    // FOCUS → mostrar lista actual (o fetch inicial)
    inp.addEventListener("focus", ()=>{
      const term = inp.value.trim();
      if(!snapshot.length){
        // si no hay snapshot aún, renderizamos al menos el “current”
        const cur = includeCurrent?.();
        if(cur && cur.id){
          snapshot = injectCurrent([]);
          renderList(instantFilter(term));
        }
        fetchPage(term,1).then(()=> renderList(instantFilter(term)));
      } else {
        renderList(instantFilter(term));
      }
    });

    // ENTER → elegir la primera opción y pasar al siguiente input
    inp.addEventListener("keydown", async (e)=>{
      if(e.key !== "Enter") return;
      e.preventDefault();

      if(box.style.display !== "block"){
        const term = inp.value.trim();
        if(!snapshot.length) await fetchPage(term,1);
        renderList(instantFilter(term));
      }

      const first = box.querySelector(".autocomplete-option");
      if(first){
        inp.value = first.textContent;
        hid.value = first.dataset.id;
        box.innerHTML = ""; box.style.display = "none";
        onSelect && onSelect(first.dataset.id);
        focusNext(inp);
      }else{
        const term = inp.value.trim();
        await fetchPage(term,1);
        const again = box.querySelector(".autocomplete-option");
        if(again){
          inp.value = again.textContent;
          hid.value = again.dataset.id;
          box.innerHTML = ""; box.style.display = "none";
          onSelect && onSelect(again.dataset.id);
        }
        focusNext(inp);
      }
    });

    // SCROLL infinito
    box.addEventListener("scroll", async ()=>{
      if(loading || !more) return;
      if(box.scrollTop + box.clientHeight >= box.scrollHeight - 6){
        page += 1;
        await fetchPage(inp.value.trim(), page);
        renderList(instantFilter(inp.value.trim()));
      }
    });

    // CLICK selección
    box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option");
      if(!opt) return;
      inp.value = opt.textContent;
      hid.value = opt.dataset.id;
      box.innerHTML = ""; box.style.display = "none";
      onSelect && onSelect(opt.dataset.id);
      focusNext(inp);
    });

    // Ocultar al click fuera
    document.addEventListener("click", e=>{
      if(!inp.contains(e.target) && !box.contains(e.target)){
        box.style.display = "none";
      }
    });

    return {
      reset(){
        page=1; more=true; loading=false;
        snapshot=[]; cache.clear();
        // NO borramos hid/inp aquí porque en edición ya hay valores vigentes.
        box.innerHTML=""; box.style.display="none";
      }
    };
  }

  /* → Sucursal (incluir siempre la sucursal actual, si existe) */
  const sucAC = setupAutocomplete(
    sucInp, sucHid, sucBox,
    sucursalAutocompleteUrl,
    () => ({ actual_id: currentSucId }),
    newId => {
      currentSucId = newId;
      // al elegir sucursal: limpiar y reiniciar Punto de Pago
      ppInp.value=""; ppHid.value=""; currentPpId=""; ppBox.innerHTML="";
      ppAC.reset();
    },
    // includeCurrent
    () => currentSucId ? ({ id: currentSucId, text: sucInp.value }) : null
  );

  /* → Punto de Pago (incluir SIEMPRE el que ya está vinculado) */
  const ppAC = setupAutocomplete(
    ppInp, ppHid, ppBox,
    puntopagoAutocompleteUrl,
    () => ({ sucursal_id: currentSucId, actual_id: currentPpId }),
    newId => { currentPpId = newId; },
    // includeCurrent
    () => currentPpId ? ({ id: currentPpId, text: ppInp.value }) : null
  );

  /* ══════════════ ENTER en inputs normales ══════════════
     - En hora de APERTURA → pasa al siguiente
     - En hora de CIERRE   → equivale a click en “Agregar horario”  */
  [apInp, ciInp].forEach(inp=>{
    if(!inp) return;
    inp.addEventListener("keydown", e=>{
      if(e.key !== "Enter") return;
      e.preventDefault();
      if(inp === ciInp && addBtn){
        addBtn.click();                 // ⟵ simula “Agregar horario”
      }else{
        focusNext(inp);                 // ⟵ salta al siguiente input
      }
    });
  });

  /* ══════════════ selector de días ══════════════ */
  dayBtns.forEach(btn=>{
    btn.addEventListener("click",()=>{
      if(btn.disabled) return;
      btn.classList.toggle("active");
    });
  });

  /* ══════════════ Agregar fila ══════════════ */
  addBtn.addEventListener("click",()=>{
    resetUI();

    if(!sucHid.value.trim()) fieldErr("sucursalid","Seleccione sucursal.");
    if(!ppHid.value.trim())  fieldErr("puntopagoid","Seleccione punto de pago.");

    const dias = [...dayBtns].filter(b=>b.classList.contains("active")).map(b=>b.dataset.day);
    if(!dias.length) fieldErr("dia_semana","Seleccione al menos un día.");
    if(!apInp.value) fieldErr("horaapertura","Indique apertura.");
    if(!ciInp.value) fieldErr("horacierre","Indique cierre.");
    if(apInp.value && ciInp.value && apInp.value>=ciInp.value){
      fieldErr("horacierre","Cierre debe ser mayor.");
      return;
    }
    if(!sucHid.value.trim()||!ppHid.value.trim()||!dias.length||!apInp.value||!ciInp.value) return;

    dias.forEach(d=>{
      if(tabla.querySelector(`tr[data-dia="${d}"]`)) return;
      tabla.insertAdjacentHTML("beforeend",`
        <tr data-dia="${d}">
          <td data-label="Día">${d}</td>
          <td data-label="Apertura"><input type="time" value="${apInp.value}" readonly></td>
          <td data-label="Cierre"><input type="time"  value="${ciInp.value}" readonly></td>
          <td data-label="Acciones">
            <button type="button" class="btn-eliminar"><i class="fas fa-trash"></i></button>
          </td>
        </tr>`);
    });
    apInp.value=""; ciInp.value="";
    syncDayButtons();
    apInp.focus();                      // vuelve al flujo natural
  });

  /* ══════════════ eliminar fila ══════════════ */
  tabla.addEventListener("click",e=>{
    const del=e.target.closest(".btn-eliminar");
    if(!del) return;
    del.closest("tr").remove();
    syncDayButtons();
  });

  /* ══════════════ submit AJAX JSON ══════════════ */
  form.addEventListener("submit",async ev=>{
    ev.preventDefault(); resetUI();

    if(!sucHid.value.trim()){ fieldErr("sucursalid","Seleccione sucursal."); return; }
    if(!ppHid.value.trim()){  fieldErr("puntopagoid","Seleccione punto de pago."); return; }

    const rows=[...tabla.querySelectorAll("tr[data-dia]")];
    if(!rows.length){ fieldErr("dia_semana","No hay horarios listados."); return; }

    const horarios=rows.map(r=>({
      dia         : r.dataset.dia,
      horaapertura: r.querySelectorAll("input")[0].value,
      horacierre  : r.querySelectorAll("input")[1].value
    }));

    try{
      const res=await fetch(form.action,{
        method:"POST",
        headers:{
          "Content-Type":"application/json",
          "X-CSRFToken":csrftoken,
          "Accept":"application/json"
        },
        body:JSON.stringify({
          sucursalid : sucHid.value.trim(),
          puntopagoid: ppHid.value.trim(),
          horarios
        })
      });
      if(!res.ok){ show(err,iconErr(`HTTP ${res.status}`)); return; }

      const data=await res.json();
      if(data.success){
        show(ok,iconOk("Horarios actualizados."));
        setTimeout(()=>location.href="/visualizar_horarios_cajas/",800);
      }else if(data.errors){
        const errs=JSON.parse(data.errors);
        Object.entries(errs).forEach(([f,arr])=>arr.forEach(e=>fieldErr(f,e.message)));
      }else{
        show(err,iconErr(data.error||"Error desconocido."));
      }
    }catch(ex){
      console.error(ex);
      show(err,iconErr("Error de red."));
    }
  });

})();
