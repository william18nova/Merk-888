/* static/javascript/editar_horario.js
   ─────────────────────────────────────────
   · Autocomplete sucursal ultra-rápido (filtro local + fetch en paralelo)
   · ENTER:
       - en autocomplete: selecciona la 1ª opción y avanza
       - en inputs: pasa al siguiente
       - en Hora de Cierre: clic a “Agregar horario”
   · Selector de días
   · Tabla editable
   · Envío AJAX (JSON) + flashes
*/
(() => {
  "use strict";

  /* ══════════════ refs ══════════════ */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form  = $("#form-editar-horarios");
  const tabla = $("#tabla-horarios");

  const sucInp = $("#id_sucursal_autocomplete");
  const sucHid = $("#id_sucursalid");
  const box    = $("#sucursal-autocomplete-results");

  const dayBtns = $$(".day-button");
  const apInp   = $("#horaapertura");
  const ciInp   = $("#horacierre");
  const addBtn  = $("#btn-agregar-horario");

  const err = $("#error-message");
  const ok  = $("#success-message");
  const csrftoken = document.querySelector("[name=csrfmiddlewaretoken]").value;

  /* ══════════════ helpers UI ══════════════ */
  const iconErr = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iconOk  = t => `<i class="fas fa-check-circle"></i> ${t}`;
  const show    = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide    = el            => { el.style.display = "none"; el.innerHTML = ""; };

  function resetUI () {
    [err, ok].forEach(hide);
    $$(".field-error").forEach(h => { h.style.display="none"; h.innerHTML=""; });
  }
  function fieldErr (field, msg) {
    const div = $(`#error-id_${field}`);
    div ? show(div, iconErr(msg)) : show(err, iconErr(msg));
  }

  /* ══════════════ foco con ENTER ══════════════ */
  const focusOrder = [sucInp, apInp, ciInp];
  function focusNext(fromEl){
    const list = focusOrder.filter(Boolean);
    const i = list.indexOf(fromEl);
    if(i > -1 && i < list.length - 1){
      list[i+1].focus();
      if(typeof list[i+1].select === "function") list[i+1].select();
    }
  }

  /* ══════════════ ordenar filas existentes (Lun → Dom) ══════════════ */
  const order = ["Lun","Mar","Mie","Jue","Vie","Sab","Dom"];
  [...tabla.querySelectorAll("tr[data-dia]")]
    .sort((a, b) => order.indexOf(a.dataset.dia) - order.indexOf(b.dataset.dia))
    .forEach(tr => tabla.appendChild(tr));

  /* ══════════════ AUTOCOMPLETE ultra-rápido ══════════════ */
  // estado + caché + control de concurrencia
  const acState = { term:"", page:1, more:true };
  const acCache = new Map();           // key -> {results, has_more}
  let   acVersion = 0;                 // invalida respuestas antiguas
  let   controller = null;             // AbortController

  // snapshots para filtro local instantáneo
  let snapshot = [];    // resultados de la búsqueda actual
  let fullSnap = [];    // primera página de term === "" (lista base)

  // recordar última selección (para no borrar hidden si coincide)
  let currentSelection = {
    text : sucInp?.value.trim() || "",
    id   : sucHid?.value.trim() || ""
  };

  const norm = s => (s||"").toString()
    .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
    .toLowerCase().replace(/\s+/g," ").trim();

  const render = items => {
    if(!items.length){
      box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
    } else {
      box.innerHTML = items.map(r =>
        `<div class="autocomplete-option" data-id="${r.id}">${r.text}</div>`
      ).join("");
    }
    box.style.display = "block";
  };

  const instantFilter = (term, base) => {
    const t = norm(term);
    if(!t) return (base||[]).slice(0,40);
    const starts=[], contains=[];
    for(const r of (base||[])){
      const n = norm(r.text);
      if(n.startsWith(t)) starts.push(r);
      else if(n.includes(t)) contains.push(r);
    }
    return starts.concat(contains).slice(0,40);
  };

  const debounce = (fn, ms=60) => { let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; };

  async function fetchPageSafely(term, page, version){
    const key = `${term}::${page}`;
    if(acCache.has(key)){
      const data = acCache.get(key);
      if(page===1) snapshot = data.results.slice();
      else snapshot = snapshot.concat(data.results);
      if(term==="" ){ if(page===1) fullSnap=data.results.slice(); else fullSnap=fullSnap.concat(data.results); }
      acState.more = !!data.has_more;
      if(version===acVersion){
        const base = term==="" ? fullSnap : snapshot;
        render(instantFilter(sucInp.value.trim(), base));
      }
      return;
    }

    try{ controller?.abort(); }catch(_){}
    controller = new AbortController();

    const params = new URLSearchParams({ term, page });
    const res = await fetch(`${sucursalAutocompleteUrl}?${params}`, { signal: controller.signal }).catch(()=>null);
    if(!res) return;

    const data = await res.json();
    const results = (data.results||[]).map(r=>({id:r.id, text:r.text}));
    acCache.set(key, { results, has_more:!!data.has_more });

    if(version!==acVersion) return; // respuesta vieja
    if(page===1) snapshot = results.slice(); else snapshot = snapshot.concat(results);
    if(term==="" ){ if(page===1) fullSnap=results.slice(); else fullSnap=fullSnap.concat(results); }
    acState.more = !!data.has_more;

    const base = term==="" ? fullSnap : snapshot;
    render(instantFilter(sucInp.value.trim(), base));
  }

  const refreshAC = debounce(()=>{
    const term = sucInp.value.trim();
    acState.term = term; acState.page = 1; acState.more = true;
    const myV = ++acVersion;

    // pintar instantáneo con lo que tengamos
    const base = term==="" ? fullSnap : snapshot;
    render(instantFilter(term, base));

    // pedir red y repintar al llegar (si sigue vigente)
    fetchPageSafely(term, 1, myV);
  }, 50);

  // input: limpiar hidden si ya no coincide con la última selección
  sucInp.addEventListener("input", ()=>{
    if(sucInp.value.trim() !== currentSelection.text){
      sucHid.value = "";
    }
    refreshAC();
  });

  // focus: mostrar al toque; si no hay datos aún, cargar página 1
  sucInp.addEventListener("focus", ()=>{
    const term = sucInp.value.trim();
    if((term==="" && fullSnap.length) || (term!=="" && snapshot.length)){
      const base = term==="" ? fullSnap : snapshot;
      render(instantFilter(term, base));
    }else{
      acState.term = term; acState.page = 1; acState.more = true;
      const myV = ++acVersion;
      fetchPageSafely(term, 1, myV);
    }
  });

  // scroll infinito
  box.addEventListener("scroll", ()=>{
    if(!acState.more || !box.style.display || box.style.display==="none") return;
    if(box.scrollTop + box.clientHeight >= box.scrollHeight - 6){
      const myV = acVersion;
      acState.page += 1;
      fetchPageSafely(acState.term, acState.page, myV);
    }
  });

  // selección por click
  box.addEventListener("click", e=>{
    const opt = e.target.closest(".autocomplete-option");
    if(!opt) return;
    sucInp.value = opt.textContent;
    sucHid.value = opt.dataset.id;
    currentSelection = { text:sucInp.value.trim(), id:sucHid.value.trim() };
    box.innerHTML = ""; box.style.display = "none";
    focusNext(sucInp);
  });

  // ENTER en autocomplete: seleccionar 1ª opción y avanzar
  sucInp.addEventListener("keydown", async (e)=>{
    if(e.key!=="Enter") return;
    e.preventDefault();

    // 1) si ya hay opciones pintadas
    const firstDom = box.querySelector(".autocomplete-option");
    if(firstDom){
      sucInp.value = firstDom.textContent;
      sucHid.value = firstDom.dataset.id;
      currentSelection = { text:sucInp.value.trim(), id:sucHid.value.trim() };
      box.innerHTML = ""; box.style.display = "none";
      return focusNext(sucInp);
    }

    // 2) intenta con filtro local
    const term = sucInp.value.trim();
    const base = term==="" ? fullSnap : snapshot;
    const list = instantFilter(term, base);
    if(list.length){
      sucInp.value = list[0].text;
      sucHid.value = list[0].id;
      currentSelection = { text:sucInp.value.trim(), id:sucHid.value.trim() };
      box.innerHTML = ""; box.style.display = "none";
      return focusNext(sucInp);
    }

    // 3) si aún no hay datos, fetch rápido y seleccionar al llegar
    const myV = ++acVersion;
    await fetchPageSafely(term, 1, myV);
    const again = box.querySelector(".autocomplete-option");
    if(again){
      sucInp.value = again.textContent;
      sucHid.value = again.dataset.id;
      currentSelection = { text:sucInp.value.trim(), id:sucHid.value.trim() };
    }
    box.innerHTML = ""; box.style.display = "none";
    focusNext(sucInp);
  });

  // cerrar dropdown al click fuera
  document.addEventListener("click", e=>{
    if(!sucInp.contains(e.target) && !box.contains(e.target)){
      box.style.display = "none";
    }
  });

  /* ══════════════ selector de días ══════════════ */
  dayBtns.forEach(btn => {
    if (tabla.querySelector(`tr[data-dia="${btn.dataset.day}"]`)) {
      btn.disabled = true;
    }
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });

  /* ══════════════ ENTER en inputs normales ══════════════
     - en hora de CIERRE → clic a “Agregar horario”
     - en los demás → foco al siguiente
  */
  [apInp, ciInp].forEach(inp=>{
    if(!inp) return;
    inp.addEventListener("keydown", e=>{
      if(e.key!=="Enter") return;
      e.preventDefault();
      if(inp===ciInp && addBtn){ addBtn.click(); }
      else { focusNext(inp); }
    });
  });

  /* ══════════════ agregar fila ══════════════ */
  $("#btn-agregar-horario").addEventListener("click", () => {
    resetUI();

    if (!sucHid.value.trim()) fieldErr("sucursalid", "Seleccione sucursal.");
    const dias = [...dayBtns]
      .filter(b => b.classList.contains("active"))
      .map(b => b.dataset.day);
    if (!dias.length)      fieldErr("dia_semana", "Seleccione al menos un día.");
    if (!apInp.value)      fieldErr("horaapertura", "Indique apertura.");
    if (!ciInp.value)      fieldErr("horacierre", "Indique cierre.");
    if (apInp.value && ciInp.value && apInp.value >= ciInp.value) {
      fieldErr("horacierre", "Cierre > apertura.");
      return;
    }
    if (!sucHid.value.trim() || !dias.length || !apInp.value || !ciInp.value) {
      return;
    }

    dias.forEach(d => {
      tabla.insertAdjacentHTML("beforeend", `
        <tr data-dia="${d}">
          <td data-label="Día">${d}</td>
          <td data-label="Apertura"><input type="time" value="${apInp.value}" readonly></td>
          <td data-label="Cierre"><input type="time" value="${ciInp.value}" readonly></td>
          <td data-label="Acciones">
            <button type="button" class="btn-eliminar"><i class="fas fa-trash"></i></button>
          </td>
        </tr>`);
      const b = dayBtns.find(x => x.dataset.day === d);
      if (b) { b.disabled = true; b.classList.remove("active"); }
    });

    apInp.value = "";
    ciInp.value = "";
    apInp.focus(); // flujo natural
  });

  /* ══════════════ eliminar fila ══════════════ */
  tabla.addEventListener("click", e => {
    const btn = e.target.closest("button.btn-eliminar");
    if (!btn) return;

    const tr  = btn.closest("tr");
    const dia = tr.dataset.dia;
    tr.remove();

    if (!tabla.querySelector(`tr[data-dia="${dia}"]`)) {
      const b = [...dayBtns].find(x => x.dataset.day === dia);
      if (b) { b.disabled = false; b.classList.remove("active"); }
    }
  });

  /* ══════════════ submit ══════════════ */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    if (!sucHid.value.trim()) {
      fieldErr("sucursalid", "Seleccione una sucursal de la lista.");
      return;
    }
    const rows = [...tabla.querySelectorAll("tr[data-dia]")];
    if (!rows.length) {
      fieldErr("dia_semana", "No hay horarios en la tabla.");
      return;
    }

    const horarios = rows.map(r => ({
      dia         : r.dataset.dia,
      horaapertura: r.querySelectorAll("input")[0].value,
      horacierre  : r.querySelectorAll("input")[1].value
    }));

    try {
      const res = await fetch(form.action, {
        method     : "POST",
        credentials: "same-origin",
        headers    : {
          "Content-Type": "application/json",
          "X-CSRFToken" : csrftoken,
          "Accept"      : "application/json"
        },
        body: JSON.stringify({ sucursalid: sucHid.value, horarios })
      });

      if (!res.ok) {
        show(err, iconErr(`Error al guardar (HTTP ${res.status}).`));
        return;
      }

      const data = await res.json();
      if (data.success) {
        show(ok, iconOk("Horarios guardados."));
        setTimeout(() => location.href = "/visualizar_horarios/", 800);
      } else if (data.errors) {
        const errs = JSON.parse(data.errors);
        Object.entries(errs).forEach(([f, arr]) =>
          arr.forEach(e => fieldErr(f, e.message))
        );
      } else {
        show(err, iconErr(data.error || "Error desconocido."));
      }

    } catch (e) {
      console.error("fetch fail:", e);
      show(err, iconErr("Error de red o de parseo."));
    }
  });
})();
