/*  static/javascript/agregar_usuario.js
    — Autocomplete instantáneo + Enter‑nav (siguiente / seleccionar / enviar)
------------------------------------------------------------------------ */
(() => {
  "use strict";

  /* ----- helpers DOM / CSRF ----- */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);
  const csrftoken =
    (document.cookie.split(";").map(c => c.trim())
      .find(c => c.indexOf("csrftoken=") === 0) || "")
      .split("=")[1] || "";

  const icon   = txt => '<i class="fas fa-exclamation-circle"></i> ' + txt;
  const okIcon = txt => '<i class="fas fa-check-circle"></i> ' + txt;

  const show = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide = el         => { el.style.display = "none"; el.innerHTML = ""; };

  /* ----- refs ----- */
  const form   = $("#usuarioForm"),
        okBox  = $("#success-message"),
        errBox = $("#error-message");

  const rolInp = $("#id_rol_autocomplete"),
        rolHid = $("#id_rolid"),
        rolBox = $("#rol-autocomplete-results");

  /* ====================================================================== */
  /* 1) AUTOCOMPLETE “ROL” — instantáneo                                    */
  /* ====================================================================== */

  // LRU mini‑cache
  function LRU(limit){ this.limit = limit || 80; this.map = new Map(); }
  LRU.prototype.get = function(k){ if(!this.map.has(k)) return; const v=this.map.get(k); this.map.delete(k); this.map.set(k,v); return v; };
  LRU.prototype.set = function(k,v){ if(this.map.has(k)) this.map.delete(k); this.map.set(k,v); if(this.map.size>this.limit){ const o=this.map.keys().next().value; this.map.delete(o);} };

  const cache = new LRU(80);

  const st = {
    term: "", page: 1, more: true, busy: false,
    debounceMs: 90, tmr: null, ctrl: null, lastKey: "", active: false
  };

  function resetBox(){
    rolBox.innerHTML = "";
    rolBox.classList.remove("visible");
    rolBox.style.display = "none";
    st.page = 1; st.more = true; st.busy = false;
  }

  function render(list, clear){
    if (clear) rolBox.innerHTML = "";
    if (!list || !list.length){
      if (clear){
        rolBox.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
        rolBox.classList.add("visible");
        rolBox.style.display = "block";
      }
      return;
    }
    let html = "";
    for (let i=0;i<list.length;i++){
      const r = list[i];
      html += '<div class="autocomplete-option" data-id="' + String(r.id).replace(/"/g,'&quot;') + '">' + r.text + '</div>';
    }
    rolBox.insertAdjacentHTML("beforeend", html);
    rolBox.classList.add("visible");
    rolBox.style.display = "block";
  }

  function localFilter(term){
    const t = (term || "").toLowerCase();
    if (!t){
      const first = cache.get("__empty__1");
      return first && first.results ? first.results.slice(0,20) : [];
    }
    const fromEmpty = (cache.get("__empty__1")?.results) || [];
    const pages = [];
    cache.map.forEach((v,k)=>{ if(typeof k==="string" && k.indexOf(term + "__") === 0) pages.push(v); });
    let pool = fromEmpty.slice();
    for (let i=0;i<pages.length;i++) if (pages[i]?.results) pool = pool.concat(pages[i].results);
    const uniq = new Map();
    for (let j=0;j<pool.length;j++){
      const r = pool[j];
      if (r?.text && r.text.toLowerCase().indexOf(t)!==-1 && !uniq.has(r.id)){
        uniq.set(r.id, r);
        if (uniq.size >= 20) break;
      }
    }
    return Array.from(uniq.values());
  }

  function fetchData(q, p=1, {force=false}={}){
    if (!force && (st.busy || (!st.more && p>1))) return;
    st.busy = true;

    const key = q + "__" + p;
    const cached = cache.get(key);
    if (cached && !force){
      render(cached.results, p===1);
      st.more = !!cached.has_more;
      st.busy = false;
      return;
    }

    if (st.ctrl && st.ctrl.abort) st.ctrl.abort();
    st.ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    st.lastKey = key;

    const opts = { headers:{ "Accept":"application/json" }, cache:"no-store" };
    if (st.ctrl) opts.signal = st.ctrl.signal;

    fetch(rolAutocompleteUrl + "?term=" + encodeURIComponent(q) + "&page=" + p, opts)
      .then(r => { if(!r.ok) throw new Error("Error de red ("+r.status+")"); return r.json(); })
      .then(j => {
        cache.set(key, j);
        if (!q && p===1) cache.set("__empty__1", j);
        if (st.lastKey === key){
          const res = j?.results || [];
          if (p===1 && !res.length){
            if (q){ render([], true); }
            else { resetBox(); }
          } else {
            render(res, p===1);
          }
          st.more = !!j?.has_more;
        }
      })
      .catch(e => { if (e?.name !== "AbortError") console.error(e); })
      .finally(()=> st.busy=false);
  }

  function showInitial(){
    const init = cache.get("__empty__1");
    if (init?.results?.length) render(init.results, true);
    else fetchData("", 1, {force:true});
  }

  function debounced(){
    clearTimeout(st.tmr);
    st.tmr = setTimeout(() => {
      st.page=1; st.more=true;
      const items = localFilter(st.term);           // pinta al instante
      if (items.length) render(items, true);
      else if (!st.term) resetBox();
      fetchData(st.term, 1);                        // actualiza en bg
    }, st.debounceMs);
  }

  rolInp.addEventListener("focus", ()=>{
    st.active = true;
    st.term = (rolInp.value || "").trim();
    st.page = 1; st.more = true;
    if (!st.term){ showInitial(); }
    else { render(localFilter(st.term), true); fetchData(st.term, 1); }
  });

  rolInp.addEventListener("input", ()=>{
    rolHid.value = "";
    st.term = (rolInp.value || "").trim();
    if (!st.term){
      st.page=1; st.more=true;
      showInitial();
      fetchData("",1,{force:true});
      return;
    }
    debounced();
  }, { passive:true });

  rolBox.addEventListener("scroll", ()=>{
    if (rolBox.scrollTop + rolBox.clientHeight >= rolBox.scrollHeight - 6){
      if (st.more && !st.busy){ st.page++; fetchData(st.term, st.page); }
    }
  }, { passive:true });

  rolBox.addEventListener("pointerdown", (e)=>{
    const opt = e.target.closest(".autocomplete-option");
    if (!opt) return;
    rolInp.value = opt.textContent;
    rolHid.value = opt.getAttribute("data-id") || "";
    resetBox();
  });

  document.addEventListener("pointerdown", (e)=>{
    if (!rolInp.contains(e.target) && !rolBox.contains(e.target)) resetBox();
  }, { passive:true });

  /* ====================================================================== */
  /* 2) ENTER‑NAV (siguiente / seleccionar / enviar)                         */
  /* ====================================================================== */
  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent === null) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  function isFocusable(el){
    if (!el || el.disabled) return false;
    if (el.tagName === "INPUT" && el.type === "hidden") return false;
    return isVisible(el);
  }
  function focusables(){
    const nodes = form.querySelectorAll("input, select, textarea, button[type='button']");
    const arr = [];
    for (let i=0;i<nodes.length;i++) if (isFocusable(nodes[i])) arr.push(nodes[i]);
    return arr;
  }
  function submitForm(){
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.submit();
  }
  function focusNext(from){
    const f = focusables();
    const i = f.indexOf(from);
    if (i === -1) return;
    if (i < f.length - 1){
      f[i+1].focus();
      try{ if (f[i+1].select && f[i+1].type === "text") f[i+1].select(); }catch(_){}
    } else {
      submitForm();
    }
  }

  // Enter en el AUTOCOMPLETE de Rol: elegir 1ª opción → siguiente / enviar
  rolInp.addEventListener("keydown", (e)=>{
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    const first = rolBox.querySelector(".autocomplete-option");
    if (isVisible(rolBox) && first){
      rolInp.value = first.textContent;
      rolHid.value = first.getAttribute("data-id") || "";
      resetBox();
    }
    // si es el último campo, esto enviará; si no, avanza
    focusNext(rolInp);
  });

  // Enter en inputs NO‑autocomplete (username, passwords)
  form.addEventListener("keydown", (e)=>{
    if (e.key !== "Enter" || e.isComposing) return;
    const t = e.target;
    if (t === rolInp) return;             // ya manejado
    if (t && t.tagName === "TEXTAREA") return; // permitir salto de línea
    e.preventDefault();
    focusNext(t);
  });

  /* ====================================================================== */
  /* 3) TOGGLE PASSWORD VISIBILITY                                          */
  /* ====================================================================== */
  window.togglePassword = id => {
    const inp  = document.getElementById(id);
    const icon = inp?.nextElementSibling;
    if (!inp) return;
    if (inp.type === "password") {
      inp.type = "text";  icon?.classList.replace("fa-eye", "fa-eye-slash");
    } else {
      inp.type = "password"; icon?.classList.replace("fa-eye-slash", "fa-eye");
    }
  };

  /* ====================================================================== */
  /* 4) SUBMIT (AJAX)                                                       */
  /* ====================================================================== */
  form.addEventListener("submit", async ev=>{
    ev.preventDefault();

    [okBox, errBox].forEach(hide);
    $$(".field-error").forEach(div=>{ div.innerHTML=""; div.classList.remove("visible"); });
    $$(".input-error").forEach(i=>i.classList.remove("input-error"));

    if (!rolHid.value){
      const div = $("#error-id_rolid");
      div.innerHTML = icon("Seleccione un rol.");
      div.classList.add("visible");
      show(errBox, icon("Corrige los errores antes de continuar."));
      rolInp.classList.add("input-error");
      return;
    }

    try{
      const resp = await fetch(form.action,{
        method : "POST",
        headers: { "X-CSRFToken": csrftoken, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
        body   : new FormData(form)
      });
      const data = await resp.json();

      if (data.success){
        show(okBox, okIcon("Usuario creado exitosamente."));
        okBox.style.display="block";
        form.reset();
        resetBox();
        return;
      }
      renderErrors(data.errors);

    }catch(err){
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

  function renderErrors(errorsJSON){
    const errors = typeof errorsJSON === "string" ? JSON.parse(errorsJSON) : errorsJSON;
    if (errors.__all__) show(errBox, errors.__all__.map(e=>icon(e.message)).join("<br>"));
    Object.entries(errors).forEach(([field,arr])=>{
      if (field==="__all__") return;
      const div   = $("#error-id_"+field);
      const input = $("#id_"+field);
      if (div){ div.innerHTML = arr.map(e=>icon(e.message)).join("<br>"); div.classList.add("visible"); }
      if (input){ input.classList.add("input-error"); }
    });
  }
})();
