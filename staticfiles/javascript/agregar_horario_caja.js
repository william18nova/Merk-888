/* static/javascript/agregar_horario_caja.js
   ─────────────────────────────────────────────────────────────
   · Autocompletes ultra-rápidos (filtro local + fetch en paralelo)
   · Enter = seleccionar primera/activa y pasar al siguiente campo
   · En el ÚLTIMO input (Hora de cierre) → Enter = “Agregar Horario Temporal”
   · Scroll infinito + cache por término/params
   · Validaciones + tabla temporal + envío AJAX
----------------------------------------------------------------*/
(() => {
  "use strict";

  /* ───────── Shorthands ───────── */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form     = $("#form-agregar-horario");
  const err      = $("#error-message");
  const ok       = $("#success-message");

  const sucInp   = $("#id_sucursal_autocomplete");
  const sucHid   = $("#id_sucursalid");
  const sucBox   = $("#sucursal-autocomplete-results");

  const ppInp    = $("#id_puntopago_autocomplete");
  const ppHid    = $("#id_puntopagoid");
  const ppBox    = $("#puntopago-autocomplete-results");

  const dayBtns  = $$(".day-button");
  const diaInp   = $("#id_dia_semana");
  const apInp    = $("#id_horaapertura");
  const ciInp    = $("#id_horacierre");

  const addBtn   = $("#btn-agregar-temporal");
  const tempBody = $("#horarios-temp-body");
  let tempItems  = [];

  const csrftoken = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const iconErr   = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iconOk    = t => `<i class="fas fa-check-circle"></i> ${t}`;
  const show      = (el,html) => { el.innerHTML=html; el.style.display="block"; };
  const hide      = el          => { el.innerHTML=""; el.style.display="none"; };

  function resetUI(){
    [err, ok].forEach(hide);
    $$(".field-error").forEach(hide);
  }
  function fieldErr(f,m){
    const d = $(`#error-id_${f}`);
    d ? show(d, iconErr(m)) : show(err, iconErr(m));
  }

  /* ───────── Focus helpers ───────── */
  const FOCUSABLE = "input:not([type='hidden']):not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled])";
  function focusNext(fromEl){
    const list = Array.from(form.querySelectorAll(FOCUSABLE))
      .filter(el => {
        const cs = getComputedStyle(el);
        if (cs.display === "none" || cs.visibility === "hidden") return false;
        const r = el.getBoundingClientRect();
        return r.width || r.height;
      });
    const idx  = list.indexOf(fromEl);
    const next = list[idx + 1];

    // Si lo siguiente es el botón “Agregar Horario Temporal”, ejecutar click
    if (!next || next === addBtn) {
      addBtn?.click();
      return;
    }
    next.focus();
    if (next.select) next.select();
  }

  /* ───────── Ultra-fast Autocomplete ─────────
     Muestra INSTANTÁNEAMENTE coincidencias locales mientras hace fetch.
     Enter/Tab selecciona la opción activa o la primera visible y avanza.
  -------------------------------------------------------------------- */
  function makeAutocomplete({ inp, hid, box, url, extraParams, onSelectNext }){
    const cache = new Map();   // key -> {items, has_more, ts}
    let snapshot = [];         // [{id,text}, ...]
    let loading  = false;
    let more     = true;
    let page     = 1;
    let lastKey  = "";
    let active   = -1;         // índice activo para teclado

    const norm = s => (s||"")
      .toString()
      .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
      .toLowerCase().replace(/\s+/g," ").trim();

    const renderList = (items) => {
      box.innerHTML = items.map((r,i)=>
        `<div class="autocomplete-option${i===active?' active':''}" data-id="${r.id}">${r.text}</div>`
      ).join("");
      box.style.display = items.length ? "block" : "none";
    };

    const immediateFilter = (term) => {
      const t = norm(term);
      if (!t) return snapshot.slice(0, 30);
      const starts = [], contains = [];
      snapshot.forEach(r=>{
        const n = norm(r.text);
        if (n.startsWith(t)) starts.push(r);
        else if (n.includes(t)) contains.push(r);
      });
      return [...starts, ...contains].slice(0, 30);
    };

    async function fetchPage(term, pg = 1){
      const paramsObj = { term, page: pg, ...(extraParams ? extraParams() : {}) };
      const qs = new URLSearchParams(paramsObj);
      const key = `${url}?${qs}`;
      lastKey = key;

      if (cache.has(key)){
        const data = cache.get(key);
        if (pg === 1) snapshot = data.items.slice(); else snapshot.push(...data.items);
        more = data.has_more;
        return { items: data.items, has_more: data.has_more };
      }

      loading = true;
      try{
        const res = await fetch(key);
        const data = await res.json();
        const items = (data.results || []).map(r => ({ id: r.id, text: r.text }));
        cache.set(key, { items, has_more: !!data.has_more, ts: Date.now() });
        if (key !== lastKey) return { items: [], has_more: false };
        if (pg === 1) snapshot = items.slice(); else snapshot.push(...items);
        more = !!data.has_more;
        return { items, has_more: more };
      } finally {
        loading = false;
      }
    }

    const debounce = (fn, ms=80) => {
      let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); };
    };

    async function refresh(term){
      active = -1;
      const local = immediateFilter(term);       // 1) instantáneo
      renderList(local);
      page = 1; more = true;                     // 2) fetch server
      const { items } = await fetchPage(term, 1);
      if (norm(inp.value) !== norm(term)) return;
      const merged = immediateFilter(term);      // 3) re-filtra con data fresca
      renderList(merged);
    }
    const debouncedRefresh = debounce(refresh, 60);

    inp.addEventListener("input", ()=>{
      hid.value = "";
      debouncedRefresh(inp.value);
    });
    inp.addEventListener("focus", ()=>{
      debouncedRefresh(inp.value);
    });

    // Scroll infinito
    box.addEventListener("scroll", async ()=>{
      if (loading || !more) return;
      const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 6;
      if (!nearBottom) return;
      page += 1;
      const currentTerm = inp.value;
      await fetchPage(currentTerm, page);
      const merged = immediateFilter(currentTerm);
      renderList(merged);
    });

    // Click selección
    box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option");
      if (!opt) return;
      selectOption(opt);
    });

    function selectOption(optEl){
      inp.value = optEl.textContent.trim();
      hid.value = optEl.dataset.id || "";
      box.innerHTML = "";
      box.style.display = "none";
      active = -1;
      onSelectNext && onSelectNext(inp); // pasar al siguiente campo
    }

    // Teclado
    inp.addEventListener("keydown", (e)=>{
      const visible = box.style.display === "block";
      if (!visible && (e.key === "ArrowDown" || e.key === "Enter" || e.key === "Tab")){
        debouncedRefresh(inp.value);
      }
      if (!visible) return;

      const opts = Array.from(box.querySelectorAll(".autocomplete-option"));
      if (!opts.length) return;

      if (e.key === "ArrowDown"){
        e.preventDefault();
        active = (active + 1) % opts.length;
        opts.forEach((el,i)=> el.classList.toggle("active", i===active));
        const el = opts[active];
        const top = el.offsetTop, bottom = top + el.offsetHeight;
        if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
        if (top < box.scrollTop) box.scrollTop = top;
      } else if (e.key === "ArrowUp"){
        e.preventDefault();
        active = (active - 1 + opts.length) % opts.length;
        opts.forEach((el,i)=> el.classList.toggle("active", i===active));
        const el = opts[active];
        const top = el.offsetTop, bottom = top + el.offsetHeight;
        if (top < box.scrollTop) box.scrollTop = top;
        if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
      } else if (e.key === "Enter" || e.key === "Tab"){
        e.preventDefault();
        const chosen = (active >= 0 ? opts[active] : opts[0]);
        if (chosen) selectOption(chosen);
      } else if (e.key === "Escape"){
        e.preventDefault();
        box.style.display = "none";
        active = -1;
      }
    });

    // Cerrar al click fuera
    document.addEventListener("click", e=>{
      if (!inp.contains(e.target) && !box.contains(e.target)){
        box.style.display = "none";
        active = -1;
      }
    });
  }

  /* ───────── Instancias de Autocomplete ───────── */

  // 1) Sucursal (solo sucursales con puntos sin horario) → al elegir, pasa a Punto Pago
  makeAutocomplete({
    inp:  sucInp,
    hid:  sucHid,
    box:  sucBox,
    url:  sucursalAutocompleteUrl,
    onSelectNext: () => {
      ppInp.value = ""; ppHid.value = "";
      ppInp.focus(); ppInp.select?.();
    }
  });

  // 2) Punto de Pago (filtrado por sucursal elegida) → al elegir, pasa a Hora Apertura
  makeAutocomplete({
    inp:  ppInp,
    hid:  ppHid,
    box:  ppBox,
    url:  puntopagoAutocompleteUrl,
    extraParams: () => ({ sucursal_id: sucHid.value }),
    onSelectNext: () => { apInp.focus(); }
  });

  /* ───────── Selector de días ───────── */
  dayBtns.forEach(btn=>{
    btn.addEventListener("click", ()=>{
      const d    = btn.dataset.day;
      let dias   = diaInp.value ? diaInp.value.split(",") : [];
      if(dias.includes(d)){
        dias = dias.filter(x=>x!==d);
        btn.classList.remove("active");
      } else {
        dias.push(d);
        btn.classList.add("active");
      }
      diaInp.value = dias.join(",");
    });
  });

  /* ───────── Agregar fila temporal ───────── */
  addBtn.addEventListener("click", ()=>{
    resetUI();
    if(!sucHid.value){ fieldErr("sucursalid","Seleccione sucursal."); return; }
    if(!ppHid.value) { fieldErr("puntopagoid","Seleccione punto de pago."); return; }

    const dias = diaInp.value.split(",").filter(d=>d);
    if(!dias.length){ fieldErr("dia_semana","Seleccione al menos un día."); return; }
    if(!apInp.value){ fieldErr("horaapertura","Indique hora de apertura."); return; }
    if(!ciInp.value){ fieldErr("horacierre","Indique hora de cierre.");    return; }
    if(apInp.value >= ciInp.value){
      fieldErr("horacierre","La hora de cierre debe ser mayor."); return;
    }

    dias.forEach(d=>{
      if(!tempItems.some(x=> x.dia===d && x.horaapertura===apInp.value && x.horacierre===ciInp.value )){
        tempItems.push({ dia:d, horaapertura:apInp.value, horacierre:ciInp.value });
        tempBody.insertAdjacentHTML("beforeend", `
          <tr data-dia="${d}">
            <td data-label="Día">${d}</td>
            <td data-label="Apertura"><input type="time" value="${apInp.value}" readonly></td>
            <td data-label="Cierre"><input type="time" value="${ciInp.value}" readonly></td>
            <td data-label="Acciones">
              <button type="button" class="btn-eliminar"><i class="fas fa-trash"></i></button>
            </td>
          </tr>`);
        const b = [...dayBtns].find(b=>b.dataset.day===d);
        if (b) b.disabled = true;
      }
    });

    // reset campos de entrada para permitir cargar más
    diaInp.value = "";
    apInp.value  = "";
    ciInp.value  = "";
    // vuelve a enfocar días (o apertura)
    apInp.focus();
  });

  /* ───────── Eliminar fila temporal ───────── */
  tempBody.addEventListener("click", e=>{
    if(!e.target.closest(".btn-eliminar")) return;
    const tr = e.target.closest("tr");
    const d  = tr.dataset.dia;
    tempItems = tempItems.filter(x=> x.dia!==d);
    tr.remove();
    const btn = [...dayBtns].find(b=>b.dataset.day===d);
    if (btn) btn.disabled = false;
  });

  /* ───────── Envío final ───────── */
  form.addEventListener("submit", async ev=>{
    ev.preventDefault();
    resetUI();

    if(!sucHid.value){ fieldErr("sucursalid","Seleccione sucursal."); return; }
    if(!ppHid.value) { fieldErr("puntopagoid","Seleccione punto de pago."); return; }
    if(!tempItems.length){ fieldErr("dia_semana","Agregue al menos un horario."); return; }

    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken":  csrftoken,
          "Accept":       "application/json"
        },
        body: new URLSearchParams({
          sucursal_autocomplete:   sucInp.value,
          sucursalid:              sucHid.value,
          puntopago_autocomplete:  ppInp.value,
          puntopagoid:             ppHid.value,
          horarios:                JSON.stringify(tempItems)
        })
      });

      if (!res.ok) {
        show(err, iconErr(`Error HTTP ${res.status}`));
        return;
      }
      const data = await res.json();
      if (data.success) {
        show(ok, iconOk("Horario(s) guardado(s)."));
        setTimeout(()=> location.reload(), 700);
      } else {
        const errs = JSON.parse(data.errors || "{}");
        Object.entries(errs).forEach(([f,arr])=> arr.forEach(e=> fieldErr(f, e.message)));
      }
    } catch {
      show(err, iconErr("Error de red."));
    }
  });

  /* ───────── Enter para avanzar; en el último input → Agregar ───────── */
  // Caso específico: en Hora de cierre (último input), Enter = click en Agregar
  ciInp.addEventListener("keydown", (e)=>{
    if (e.key === "Enter") {
      e.preventDefault();
      addBtn?.click();
    }
  });

  // Regla general: Enter avanza, salvo si dropdown de autocomplete está abierto
  form.addEventListener("keydown", (e)=>{
    if (e.key !== "Enter") return;
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    const dropOpen =
      (t === sucInp && sucBox.style.display === "block") ||
      (t === ppInp  && ppBox.style.display  === "block");
    if (dropOpen) return;
    if (t.tagName === "TEXTAREA") return;

    // Si estamos en el último input, ya lo maneja el listener de ciInp.
    if (t === ciInp) return;

    e.preventDefault();
    focusNext(t);
  }, { capture: true });

})();
