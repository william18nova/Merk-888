// static/javascript/roles_permisos_bulk.js
(() => {
  "use strict";

  const $id = (id) => document.getElementById(id);
  const $qs = (s) => document.querySelector(s);

  const dom = {
    form: $id("rpForm"),
    ok: $id("success-message"),
    err: $id("error-message"),

    rolInp: $id("id_rol_autocomplete"),
    rolBox: $id("rol-autocomplete-results"),
    rolChips: $id("roles-chips"),
    rolHidden: $id("id_roles_ids"),
    clearRoles: $id("clear-roles"),

    perInp: $id("id_permiso_autocomplete"),
    perBox: $id("permiso-autocomplete-results"),
    perChips: $id("permisos-chips"),
    perHidden: $id("id_permisos_ids"),
    clearPerms: $id("clear-permisos"),

    btnSubmit: $id("btn-asociar"),
  };

  const state = {
    roles: [],         // [{id, text}]
    permisos: [],      // [{id, text}]
    rol:  { term:"", page:1, more:true, cache:Object.create(null), aborter:null, reqId:0, base:[], baseMore:true },
    perm: { term:"", page:1, more:true, cache:Object.create(null), aborter:null, reqId:0, base:[], baseMore:true },
  };

  const UI = {
    ok(msg){ dom.ok.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`; dom.ok.style.display="block"; },
    err(msg){ dom.err.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`; dom.err.style.display="block"; },
    clearAlerts(){ dom.ok.style.display="none"; dom.ok.innerHTML=""; dom.err.style.display="none"; dom.err.innerHTML=""; },
    fieldError(id, msg){
      const el = $id(`error-id_${id}`);
      if(el){ el.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`; el.classList.add("visible"); }
    },
    clearFieldErrors(){
      document.querySelectorAll(".field-error").forEach(e=>{e.classList.remove("visible"); e.innerHTML="";});
    },
    paintChips(kind){
      const target = (kind==="rol") ? dom.rolChips : dom.perChips;
      const list   = (kind==="rol") ? state.roles  : state.permisos;
      target.innerHTML = "";
      list.forEach(item=>{
        const chip = document.createElement("button");
        chip.type="button";
        chip.className="chip";
        chip.dataset.id=item.id;
        chip.innerHTML = `<span>${item.text}</span><i class="fas fa-times"></i>`;
        target.appendChild(chip);
      });
      // hidden
      if(kind==="rol"){
        dom.rolHidden.value = JSON.stringify(list.map(x=>x.id));
      }else{
        dom.perHidden.value = JSON.stringify(list.map(x=>x.id));
      }
    }
  };

  // ===== Autocomplete (reutiliza patrón rápido) =====
  function makeAuto(kind){
    const S = (kind==="rol") ? state.rol : state.perm;
    const cfg = (kind==="rol") ? {
      inp: dom.rolInp,
      box: dom.rolBox,
      url: (t,p) => `${rolAutocompleteUrl}?term=${encodeURIComponent(t)}&page=${p}`,
      add: (id,text) => {
        if(!state.roles.some(r=>r.id===id)){
          state.roles.push({id, text}); UI.paintChips("rol");
        }
      }
    } : {
      inp: dom.perInp,
      box: dom.perBox,
      url: (t,p) => `${permisoAutocompleteUrl}?term=${encodeURIComponent(t)}&page=${p}`,
      add: (id,text) => {
        if(!state.permisos.some(r=>r.id===id)){
          state.permisos.push({id, text}); UI.paintChips("perm");
        }
      }
    };

    const open = ()=>{ cfg.box.style.display="block"; };
    const close= ()=>{ cfg.box.style.display="none"; };

    const paint = (results, replace=true) => {
      requestAnimationFrame(()=>{
        const frag=document.createDocumentFragment();
        for(const r of (results||[])){
          const d=document.createElement("div");
          d.className="autocomplete-option";
          d.dataset.id=r.id; d.textContent=r.text;
          frag.appendChild(d);
        }
        if(replace) cfg.box.replaceChildren(frag); else cfg.box.appendChild(frag);
        open();
      });
    };

    async function fetchPage(term, page){
      const key=`${term}::${page}`;
      if(S.cache[key]) return S.cache[key];
      const r = await fetch(cfg.url(term,page), { signal: S.aborter?.signal });
      const j = await r.json();
      S.cache[key]=j; return j;
    }

    async function search({reset=true}={}){
      if(S.aborter){ try{S.aborter.abort();}catch{} }
      S.aborter = new AbortController();
      const rid = ++S.reqId;
      const t=S.term, p=S.page;
      try{
        const data = await fetchPage(t,p);
        if(rid!==S.reqId) return;
        S.more = !!data.has_more;
        const res = data.results||[];
        if(reset) cfg.box.innerHTML="";
        if(res.length) paint(res, reset);
        else if(reset){
          cfg.box.innerHTML = `<div class="autocomplete-no-result">Sin resultados</div>`;
          open();
        }
      }catch(_){}
    }

    function instant(){
      const key = `${S.term}::1`;
      if(S.cache[key]?.results) paint(S.cache[key].results, true);
      else if(S.base.length){
        const lo = S.term.toLowerCase();
        const local = S.base.filter(r => r.text.toLowerCase().includes(lo)).slice(0,50);
        paint(local, true);
      }
      S.page=1; S.more=true; search({reset:true});
    }

    cfg.inp.addEventListener("input", ()=>{
      UI.clearAlerts(); UI.clearFieldErrors();
      S.term = cfg.inp.value.trim();
      if(!S.term){
        S.page=1; S.more=S.baseMore;
        if(S.base.length){ paint(S.base, true); }
        search({reset:true});
      }else{
        instant();
      }
    });

    cfg.inp.addEventListener("focus", ()=>{
      S.term = cfg.inp.value.trim();
      S.page=1; S.more=true;
      if(!S.term){ if(S.base.length){ paint(S.base,true); } search({reset:true}); }
      else{ instant(); }
    });

    cfg.box.addEventListener("scroll", ()=>{
      if(cfg.box.scrollTop + cfg.box.clientHeight >= cfg.box.scrollHeight - 4 && S.more){
        S.page++; search({reset:false});
      }
    });

    cfg.box.addEventListener("click", e=>{
      const opt = e.target.closest(".autocomplete-option"); if(!opt) return;
      const id   = parseInt(opt.dataset.id,10);
      const text = opt.textContent;
      cfg.add(id, text);
      cfg.inp.value=""; close();
      cfg.inp.focus();
    });

    document.addEventListener("click", e=>{
      if(!cfg.inp.contains(e.target) && !cfg.box.contains(e.target)) close();
    });

    (async function prefetch(){
      try{
        const data = await fetchPage("",1);
        S.base = data.results||[]; S.baseMore = !!data.has_more;
      }catch(_){}
    })();
  }
  makeAuto("rol");
  makeAuto("perm");

  // Chips remove
  dom.rolChips.addEventListener("click", e=>{
    const chip = e.target.closest(".chip"); if(!chip) return;
    const id = parseInt(chip.dataset.id,10);
    state.roles = state.roles.filter(x=>x.id!==id);
    UI.paintChips("rol");
  });
  dom.perChips.addEventListener("click", e=>{
    const chip = e.target.closest(".chip"); if(!chip) return;
    const id = parseInt(chip.dataset.id,10);
    state.permisos = state.permisos.filter(x=>x.id!==id);
    UI.paintChips("perm");
  });

  dom.clearRoles.addEventListener("click", ()=>{ state.roles=[]; UI.paintChips("rol"); });
  dom.clearPerms.addEventListener("click", ()=>{ state.permisos=[]; UI.paintChips("perm"); });

  // Enter → seleccionar 1ª opción del autocomplete / mover foco
  dom.form.addEventListener("keydown", (e)=>{
    if(e.key!=="Enter") return;
    const t=e.target;
    if(t===dom.rolInp || t===dom.perInp){
      e.preventDefault();
      const box = (t===dom.rolInp)?dom.rolBox:dom.perBox;
      const first = box.querySelector(".autocomplete-option");
      if(first){
        first.click();
      }else{
        // pasa al siguiente control
        const focusables = Array.from(dom.form.querySelectorAll('input:not([type="hidden"]),button')).filter(el=>el.offsetParent!==null);
        const idx = focusables.indexOf(t);
        if(focusables[idx+1]) focusables[idx+1].focus();
      }
    }
  });

  // Submit AJAX
  dom.form.addEventListener("submit", async (ev)=>{
    ev.preventDefault();
    UI.clearAlerts(); UI.clearFieldErrors();

    dom.rolHidden.value    = JSON.stringify(state.roles.map(x=>x.id));
    dom.perHidden.value    = JSON.stringify(state.permisos.map(x=>x.id));

    try{
      const r = await fetch(dom.form.action, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": document.cookie.split(";").map(c=>c.trim()).find(c=>c.startsWith("csrftoken="))?.split("=")[1]||"",
          "Accept": "application/json"
        },
        body: new FormData(dom.form)
      });
      const data = await r.json();
      if(data.success){
        UI.ok(data.created ? `Se crearon ${data.created} asociaciones.` : "No había asociaciones nuevas.");
      }else{
        const errs = JSON.parse(data.errors||"{}");
        if(errs.__all__){ UI.err(errs.__all__.map(e=>e.message).join("<br>")); }
        if(errs.rol_autocomplete){ UI.fieldError('rol_autocomplete', errs.rol_autocomplete.map(e=>e.message).join('<br>')); }
        if(errs.permiso_autocomplete){ UI.fieldError('permiso_autocomplete', errs.permiso_autocomplete.map(e=>e.message).join('<br>')); }
      }
    }catch(err){
      console.error(err);
      UI.err("Ocurrió un error inesperado.");
    }
  });

})();
