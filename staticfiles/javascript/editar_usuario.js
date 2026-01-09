/*  static/javascript/editar_usuario.js
    — Autocomplete instantáneo + Enter‑nav (siguiente / seleccionar / enviar)
------------------------------------------------------------------------ */
(() => {
  "use strict";

  /* ---------- helpers ---------- */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const csrftoken =
    (document.cookie.split(";").map(c => c.trim())
      .find(c => c.indexOf("csrftoken=") === 0) || "")
      .split("=")[1] || "";

  const iconErr = txt => '<i class="fas fa-exclamation-circle"></i> ' + txt;
  const iconOk  = txt => '<i class="fas fa-check-circle"></i> ' + txt;

  const show  = (el, html, flex = false) => { el.innerHTML = html; el.style.display = flex ? "flex" : "block"; };
  const hide  = (el) => { el.style.display = "none"; el.innerHTML = ""; };

  /* ---------- elementos ---------- */
  const form   = $("#usuarioForm");
  const okBox  = $("#success-message");
  const errBox = $("#error-message");

  const rolInput   = $("#id_rol_autocomplete");
  const rolHidden  = $("#id_rolid");
  const rolResult  = $("#rol-autocomplete-results");

  const userInput  = $("#id_nombreusuario");
  const passInput  = $("#id_contraseña");
  const confInput  = $("#id_confirmar_contraseña");

  /* ──────────── 1.  LIMPIAR UI ──────────── */
  const resetUI = () => {
    hide(errBox); hide(okBox);
    $$(".field-error").forEach(d => { d.innerHTML = ""; d.classList.remove("visible"); d.style.display = "none"; });
    $$(".input-error").forEach(i => i.classList.remove("input-error"));
  };

  const renderErrors = (errs = {}) => {
    if (errs.__all__)
      show(errBox, errs.__all__.map(e => iconErr(e.message)).join("<br>"));

    for (const [field,msgs] of Object.entries(errs)) {
      if (field === "__all__") continue;
      const div   = $(`#error-id_${field}`);
      const input = $(`#id_${field}`);
      if (div){
        div.innerHTML = msgs.map(e => iconErr(e.message)).join("<br>");
        div.classList.add("visible"); div.style.display = "block";
      }
      if (input) input.classList.add("input-error");
    }
  };

  /* ──────────── 2.  AUTOCOMPLETE ROL — instantáneo ──────────── */

  // LRU mini‑cache
  function LRU(limit){ this.limit = limit || 80; this.map = new Map(); }
  LRU.prototype.get = function(k){ if(!this.map.has(k)) return; const v=this.map.get(k); this.map.delete(k); this.map.set(k,v); return v; };
  LRU.prototype.set = function(k,v){ if(this.map.has(k)) this.map.delete(k); this.map.set(k,v); if(this.map.size>this.limit){ const o=this.map.keys().next().value; this.map.delete(o);} };
  const cache = new LRU(80);

  const st = {
    term:"", page:1, more:true, busy:false,
    debounceMs:90, tmr:null, ctrl:null, lastKey:"", active:false
  };

  function resetBox(){
    rolResult.innerHTML = "";
    rolResult.classList.remove("visible");
    rolResult.style.display = "none";
    st.page = 1; st.more = true; st.busy = false;
  }

  function render(list, clear){
    if (clear) rolResult.innerHTML = "";
    if (!list || !list.length){
      if (clear){
        rolResult.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
        rolResult.classList.add("visible");
        rolResult.style.display = "block";
      }
      return;
    }
    let html = "";
    for (let i=0;i<list.length;i++){
      const r = list[i];
      html += '<div class="autocomplete-option" data-id="' + String(r.id).replace(/"/g,'&quot;') + '">' + r.text + '</div>';
    }
    rolResult.insertAdjacentHTML("beforeend", html);
    rolResult.classList.add("visible");
    rolResult.style.display = "block";
  }

  // Filtro local
  function localFilter(term){
    const t = (term || "").toLowerCase();
    if (!t){
      const first = cache.get("__empty__1");
      return first && first.results ? first.results.slice(0,20) : [];
    }
    const pool = [];
    const initial = cache.get("__empty__1");
    if (initial?.results) pool.push(...initial.results);
    cache.map.forEach((v,k)=>{ if (typeof k==="string" && k.indexOf(term + "__")===0 && v?.results) pool.push(...v.results); });

    const uniq = new Map();
    for (let i=0;i<pool.length;i++){
      const it = pool[i];
      if (it?.text && it.text.toLowerCase().indexOf(t) !== -1 && !uniq.has(it.id)){
        uniq.set(it.id, it);
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

    if (st.ctrl?.abort) st.ctrl.abort();
    st.ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    st.lastKey = key;

    const opts = { headers:{ "Accept":"application/json" }, cache:"no-store" };
    if (st.ctrl) opts.signal = st.ctrl.signal;

    fetch(rolAutocompleteUrl + "?term=" + encodeURIComponent(q) + "&page=" + p, opts)
      .then(r => { if(!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
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
      st.page = 1; st.more = true;
      const items = localFilter(st.term);
      if (items.length) render(items, true);
      else if (!st.term) resetBox();
      fetchData(st.term, 1);
    }, st.debounceMs);
  }

  // Entradas del usuario
  rolInput.addEventListener("focus", () => {
    st.active = true;
    st.term = (rolInput.value || "").trim();
    st.page = 1; st.more = true;
    if (!st.term) showInitial();
    else { render(localFilter(st.term), true); fetchData(st.term, 1); }
  });

  rolInput.addEventListener("input", () => {
    rolHidden.value = "";
    st.term = (rolInput.value || "").trim();

    if (!st.term){
      st.page = 1; st.more = true;
      showInitial();
      fetchData("",1,{force:true});
      return;
    }
    debounced();
  }, { passive:true });

  // Scroll infinito
  rolResult.addEventListener("scroll", () => {
    if (rolResult.scrollTop + rolResult.clientHeight >= rolResult.scrollHeight - 6){
      if (st.more && !st.busy){ st.page++; fetchData(st.term, st.page); }
    }
  }, { passive:true });

  // Selección con click/tap
  rolResult.addEventListener("pointerdown", e => {
    const opt = e.target.closest(".autocomplete-option");
    if (!opt) return;
    rolInput.value  = opt.textContent;
    rolHidden.value = opt.getAttribute("data-id") || "";
    resetBox();
  });

  // Cerrar si se hace click fuera
  document.addEventListener("pointerdown", e => {
    if (!rolInput.contains(e.target) && !rolResult.contains(e.target)) resetBox();
  }, { passive:true });

  /* ──────────── 3.  ENTER‑NAV (siguiente / seleccionar / enviar) ──────────── */

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

  // Enter en AUTOCOMPLETE (Rol): elegir 1ª opción y pasar / enviar
  rolInput.addEventListener("keydown", (e)=>{
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    const first = rolResult.querySelector(".autocomplete-option");
    if (isVisible(rolResult) && first){
      rolInput.value  = first.textContent;
      rolHidden.value = first.getAttribute("data-id") || "";
      resetBox();
    }
    focusNext(rolInput);  // si es el último, esto enviará el form
  });

  // Enter en otros inputs → siguiente / enviar
  form.addEventListener("keydown", (e)=>{
    if (e.key !== "Enter" || e.isComposing) return;
    const t = e.target;
    if (t === rolInput) return;     // lo maneja el listener anterior
    if (t && t.tagName === "TEXTAREA") return; // permitir salto de línea si algún día hay textarea
    e.preventDefault();
    focusNext(t);
  });

  /* ──────────── 4.  SUBMIT AJAX ──────────── */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    /* validación mínima (front) */
    let frontErr = false;
    if (!rolHidden.value){
      renderErrors({rolid:[{message:"Debe seleccionar un rol."}]});
      frontErr = true;
    }
    if (!userInput.value.trim()){
      renderErrors({nombreusuario:[{message:"El nombre de usuario es obligatorio."}]});
      frontErr = true;
    }
    if ((passInput?.value || confInput?.value) && passInput.value !== confInput.value){
      renderErrors({confirmar_contraseña:[{message:"Las contraseñas no coinciden."}]});
      frontErr = true;
    }
    if (frontErr) return;

    try{
      const resp = await fetch(form.action, {
        method:"POST",
        headers:{
          "X-CSRFToken"      : csrftoken,
          "X-Requested-With" : "XMLHttpRequest",
          "Accept"           : "application/json",
        },
        body: new FormData(form),
      });
      const data = await resp.json();

      if (data.success){
        sessionStorage.setItem(
          "flash-usuario",
          iconOk(`Usuario «${data.nombre}» actualizado correctamente.`)
        );
        window.location.href = data.redirect_url;
        return;
      }
      renderErrors(typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors);

    }catch(err){
      console.error(err);
      show(errBox, iconErr("Ocurrió un error inesperado."));
    }
  });
})();
