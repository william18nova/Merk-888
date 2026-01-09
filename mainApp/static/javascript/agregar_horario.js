/* static/javascript/agregar_horario.js */
(() => {
  "use strict";

  /* ───── refs / helpers ───── */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form = $("#horarioForm");
  const err  = $("#error-message");
  const ok   = $("#success-message");

  const sucInp = $("#id_sucursal_autocomplete");
  const sucHid = $("#id_sucursalid");
  const sucBox = $("#sucursal-autocomplete-results");

  const dayBtns  = $$(".day-button");
  const diaInput = $("#id_dia_semana");
  const apInput  = $("#id_horaapertura");
  const ciInput  = $("#id_horacierre");

  const tabla    = $("#horariosTabla");
  const hidJson  = $("#id_horarios");
  const addBtn   = $("#btn-add-horario");            // ⬅️ botón Agregar horario

  const iconErr = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iconOk  = t => `<i class="fas fa-check-circle"></i> ${t}`;

  const csrftoken =
    document.cookie.split(";").map(c=>c.trim())
      .find(c=>c.startsWith("csrftoken="))?.split("=")[1] || "";

  const show = (el,html)=>{ el.innerHTML = html; el.style.display = "block"; };
  const hide = el        =>{ el.style.display = "none"; el.innerHTML = ""; };

  function resetUI(){
    [err, ok].forEach(hide);
    $$(".field-error").forEach(h => { h.innerHTML=""; h.style.display="none"; });
    $$(".input-error").forEach(i => i.classList.remove("input-error"));
  }
  function fieldErr(f,msg){
    const d = $(`#error-id_${f}`);
    if(d){ d.innerHTML = iconErr(msg); d.style.display="block"; }
    const inputMap = { sucursalid:sucInp, dia_semana:diaInput, horaapertura:apInput, horacierre:ciInput };
    inputMap[f]?.classList.add("input-error");
  }

  /* ───── orden de foco para Enter ───── */
  const focusOrder = [sucInp, apInput, ciInput];
  function focusNext(fromEl){
    const list = focusOrder.filter(Boolean);
    const i = list.indexOf(fromEl);
    if(i > -1 && i < list.length - 1){
      list[i+1].focus();
      if(list[i+1].select) list[i+1].select();
    }
  }

  /* ───── Autocomplete sucursal: rápido y estable ───── */
  const acState = { term:"", page:1, more:true, loading:false };
  const acCache = new Map();    // key -> {results, has_more}
  let   acVersion = 0;          // invalida respuestas antiguas
  let   acController = null;    // aborta fetch previo

  const debounce = (fn,ms=80)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };

  function renderList(items){
    if(!items.length){
      sucBox.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
    }else{
      sucBox.innerHTML = items.map(r=>`<div class="autocomplete-option" data-id="${r.id}">${r.text}</div>`).join("");
    }
    sucBox.style.display = "block";
  }

  async function fetchPageSafely(term, page, version){
    const key = `${term}::${page}`;
    if(acCache.has(key)){
      const data = acCache.get(key);
      if(version === acVersion){
        if(page === 1) renderList(data.results);
        else {
          const prev = [...sucBox.querySelectorAll(".autocomplete-option")]
            .map(n=>({id:n.dataset.id, text:n.textContent}));
          renderList(prev.concat(data.results));
        }
      }
      acState.more = !!data.has_more;
      return;
    }

    try{ acController?.abort(); }catch(_){}
    acController = new AbortController();

    const params = new URLSearchParams({ term, page });
    const res    = await fetch(`${sucursalAutocompleteUrl}?${params}`, { signal: acController.signal }).catch(()=>null);
    if(!res) return;

    const data    = await res.json();
    const results = (data.results || []).map(r=>({ id:r.id, text:r.text }));
    acCache.set(key, { results, has_more: !!data.has_more });

    if(version !== acVersion) return;

    if(page === 1) renderList(results);
    else {
      const prev = [...sucBox.querySelectorAll(".autocomplete-option")]
        .map(n=>({id:n.dataset.id,text:n.textContent}));
      renderList(prev.concat(results));
    }
    acState.more = !!data.has_more;
  }

  const refreshAC = debounce(async ()=>{
    const term = sucInp.value.trim();          // si queda vacío → traer página 1 “completa”
    acState.term = term; acState.page = 1; acState.more = true;
    const myVersion = ++acVersion;
    await fetchPageSafely(term, 1, myVersion);
  }, 60);

  // input
  sucInp.addEventListener("input", ()=>{
    sucHid.value = "";
    refreshAC();
  });

  // focus
  sucInp.addEventListener("focus", ()=>{
    if(sucBox.style.display !== "block"){
      const myVersion = ++acVersion;
      fetchPageSafely(sucInp.value.trim(), 1, myVersion);
    }
  });

  // scroll infinito
  sucBox.addEventListener("scroll", ()=>{
    if(acState.loading || !acState.more) return;
    if(sucBox.scrollTop + sucBox.clientHeight >= sucBox.scrollHeight - 6){
      acState.page += 1;
      const myVersion = acVersion;
      fetchPageSafely(acState.term, acState.page, myVersion);
    }
  });

  // click opción
  sucBox.addEventListener("click", e=>{
    const opt = e.target.closest(".autocomplete-option");
    if(!opt) return;
    sucInp.value = opt.textContent;
    sucHid.value = opt.dataset.id;
    sucBox.innerHTML = ""; sucBox.style.display = "none";
    focusNext(sucInp);
  });

  // ENTER en autocomplete → toma 1ª opción y avanza
  sucInp.addEventListener("keydown", async (e)=>{
    if(e.key !== "Enter") return;
    e.preventDefault();

    if(sucBox.style.display !== "block"){
      const myVersion = ++acVersion;
      await fetchPageSafely(sucInp.value.trim(), 1, myVersion);
    }
    const first = sucBox.querySelector(".autocomplete-option");
    if(first){
      sucInp.value = first.textContent;
      sucHid.value = first.dataset.id;
    }
    sucBox.innerHTML = ""; sucBox.style.display = "none";
    focusNext(sucInp);
  });

  // cerrar al click fuera
  document.addEventListener("click", e=>{
    if(!sucInp.contains(e.target) && !sucBox.contains(e.target)){
      sucBox.style.display = "none";
    }
  });

  /* ───── Selector de días ───── */
  dayBtns.forEach(btn=>{
    btn.addEventListener("click", ()=>{
      const d = btn.dataset.day;
      let dias = diaInput.value ? diaInput.value.split(",") : [];
      if(dias.includes(d)){ dias = dias.filter(x=>x!==d); btn.classList.remove("active"); }
      else { dias.push(d); btn.classList.add("active"); }
      diaInput.value = dias.join(",");
    });
  });

  /* ───── ENTER en inputs:
         - si es Hora de Cierre → clic al botón Agregar
         - en otros → pasar al siguiente input ───── */
  [apInput, ciInput].forEach(inp=>{
    if(!inp) return;
    inp.addEventListener("keydown", e=>{
      if(e.key !== "Enter") return;
      e.preventDefault();
      if(inp === ciInput && addBtn){
        addBtn.click();                       // ✅ equivalente a pulsar “Agregar horario”
      } else {
        focusNext(inp);
      }
    });
  });

  /* ───── Agregar horario temporal ───── */
  addBtn.addEventListener("click", ()=>{
    resetUI();
    const diasSel = (diaInput.value || "").split(",").filter(Boolean);
    const ap = apInput.value, ci = ciInput.value;

    let bad=false;
    if(!sucHid.value){ fieldErr("sucursalid","Seleccione sucursal."); bad=true; }
    if(!diasSel.length){ fieldErr("dia_semana","Seleccione al menos un día."); bad=true; }
    if(!ap){ fieldErr("horaapertura","Indique apertura."); bad=true; }
    if(!ci){ fieldErr("horacierre","Indique cierre."); bad=true; }
    if(ap && ci && ap >= ci){ fieldErr("horacierre","Cierre debe ser mayor que apertura."); bad=true; }
    if(bad) return;

    diasSel.forEach(day=>{
      const exists = [...tabla.querySelectorAll("tr")].some(tr=>{
        const tds = tr.querySelectorAll("td");
        return tds[0]?.textContent === day &&
               tds[1]?.querySelector("input")?.value === ap &&
               tds[2]?.querySelector("input")?.value === ci;
      });
      if(exists) return;

      const row = document.createElement("tr");
      row.innerHTML = `
        <td data-label="Día">${day}</td>
        <td data-label="Apertura"><input type="time" value="${ap}" readonly></td>
        <td data-label="Cierre"><input type="time" value="${ci}" readonly></td>
        <td data-label="Acciones">
          <button type="button" class="btn-eliminar"><i class="fas fa-trash"></i></button>
        </td>`;
      tabla.appendChild(row);

      // desactivar botón del día usado
      dayBtns.forEach(b=>{
        if(b.dataset.day === day){ b.disabled = true; b.classList.remove("active"); }
      });
    });

    diaInput.value = ""; apInput.value = ""; ciInput.value = "";
    apInput.focus();                               // vuelve al flujo natural
  });

  /* ───── Eliminar fila ───── */
  tabla.addEventListener("click", e=>{
    const btn = e.target.closest(".btn-eliminar");
    if(!btn) return;
    const tr = btn.closest("tr");
    const day = tr.querySelector("td")?.textContent || "";
    tr.remove();
    dayBtns.forEach(b=>{ if(b.dataset.day === day) b.disabled = false; });
  });

  /* ───── Submit final ───── */
  form.addEventListener("submit", async ev=>{
    ev.preventDefault();
    resetUI();

    if(!sucHid.value){ fieldErr("sucursalid","Seleccione sucursal."); return; }

    const rows = [...tabla.querySelectorAll("tr")];
    if(!rows.length){ fieldErr("dia_semana","Agregue al menos un horario."); return; }

    const horarios = rows.map(r=>{
      const tds = r.querySelectorAll("td");
      return {
        dia: tds[0].textContent,
        horaapertura: tds[1].querySelector("input").value,
        horacierre  : tds[2].querySelector("input").value
      };
    });

    hidJson.value = JSON.stringify(horarios);

    try{
      const resp = await fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": csrftoken, "Accept": "application/json" },
        body: new FormData(form)
      });
      const data = await resp.json();
      if(data.success){
        show(ok, iconOk("Horarios guardados."));
        form.reset(); tabla.innerHTML = "";
        dayBtns.forEach(b=>{ b.disabled=false; b.classList.remove("active"); });

        // limpiar autocomplete
        sucHid.value = ""; sucInp.value = "";
        sucBox.innerHTML = ""; sucBox.style.display = "none";
        acCache.clear(); acVersion++; // invalida respuestas viejas
      }else{
        const errs = JSON.parse(data.errors || "{}");
        Object.entries(errs).forEach(([f,arr]) => arr.forEach(e => fieldErr(f, e.message)));
      }
    }catch(ex){
      console.error(ex);
      show(err, iconErr("Error de red."));
    }
  });

})();
