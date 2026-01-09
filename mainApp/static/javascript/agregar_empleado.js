/*  static/javascript/agregar_empleado.js — instantáneo + vacío = sugerencias iniciales + anti-cruce + Enter/Click-nav + errores en español
------------------------------------------------------------------------ */
(() => {
  "use strict";

  /* ───────── helpers ───────── */
  const $  = (q) => document.querySelector(q);
  const $$ = (q) => document.querySelectorAll(q);

  const csrftoken =
    (document.cookie.split(";").map((c) => c.trim())
      .find((c) => c.indexOf("csrftoken=") === 0) || "")
      .split("=")[1] || "";

  const iconErr = (txt) => '<i class="fas fa-exclamation-circle"></i> ' + txt;
  const show    = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide    = (el) => { el.style.display = "none"; el.innerHTML = ""; };

  /* ───────── refs DOM ───────── */
  const form   = $("#empleadoForm");
  const errBox = $("#error-message");
  const okBox  = $("#success-message");

  const usrInp = $("#id_usuario_autocomplete");
  const usrHid = $("#id_usuarioid");
  const usrBox = $("#usuario-autocomplete-results");

  const sucInp = $("#id_sucursal_autocomplete");
  const sucHid = $("#id_sucursalid");
  const sucBox = $("#sucursal-autocomplete-results");

  const addBtn = $(".btn-agregar-empleado");

  /* ───────── UI reset ───────── */
  function resetUI () {
    hide(errBox); hide(okBox);
    $$(".field-error").forEach((d) => {
      d.classList.remove("visible");
      d.innerHTML = "";
      d.style.display = "none";
    });
    $$(".input-error").forEach((i) => i.classList.remove("input-error"));
  }

  function fieldErr(name, msg){
    const div = $("#error-id_" + name);
    const inp = name === "usuarioid" ? usrInp : (name === "sucursalid" ? sucInp : $("#id_" + name));
    if (div){
      div.innerHTML     = iconErr(msg);
      div.classList.add("visible");
      div.style.display = "block";
    }
    if (inp) inp.classList.add("input-error");
  }

  /* ───────── herramientas de foco ───────── */
  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent === null) return false;
    const cs = window.getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  function isFocusable(el){
    if (!el || el.disabled) return false;
    if (el.tagName === "INPUT" && el.type === "hidden") return false;
    return isVisible(el);
  }
  function focusablesInForm(){
    const nodes = form.querySelectorAll("input, select, textarea");
    const arr = [];
    for (let i=0; i<nodes.length; i++){
      if (isFocusable(nodes[i])) arr.push(nodes[i]);
    }
    return arr;
  }
  function focusNext(fromEl){
    const f = focusablesInForm();
    const idx = f.indexOf(fromEl);
    if (idx === -1) return;
    if (idx < f.length - 1){
      f[idx+1].focus();
    } else {
      if (addBtn && typeof addBtn.click === "function") addBtn.click();
      else if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }
  }

  /* ───────── Cache LRU ───────── */
  function LRU(limit){
    this.limit = typeof limit === "number" ? limit : 80;
    this.map = new Map();
  }
  LRU.prototype.get = function(k){
    if (!this.map.has(k)) return undefined;
    const v = this.map.get(k);
    this.map.delete(k);
    this.map.set(k, v);
    return v;
  };
  LRU.prototype.set = function(k, v){
    if (this.map.has(k)) this.map.delete(k);
    this.map.set(k, v);
    if (this.map.size > this.limit){
      const oldest = this.map.keys().next().value;
      this.map.delete(oldest);
    }
  };
  LRU.prototype.clear = function(){ this.map.clear(); };
  LRU.prototype.pagesForTerm = function(term){
    if (!term) return [];
    const prefix = term + "__";
    const out = [];
    this.map.forEach((v, k) => {
      if (String(k).indexOf(prefix) === 0) out.push(v);
    });
    return out;
  };

  const cacheUsr = new LRU(80);
  const cacheSuc = new LRU(80);

  /* ───────── Pintado ───────── */
  function renderList(box, items, clear){
    if (clear) box.innerHTML = "";
    if (!items || !items.length) return;
    let html = "";
    for (let i=0; i<items.length; i++){
      const r = items[i];
      const id = String(r.id).replace(/"/g, "&quot;");
      html += '<div class="autocomplete-option" data-id="' + id + '">' + r.text + '</div>';
    }
    box.insertAdjacentHTML("beforeend", html);
    box.classList.add("visible");
    box.style.display = "block";
  }

  function localFilter(cache, term){
    const t = (term || "").toLowerCase();
    if (!t){
      const emptyPageOnly = cache.get("__empty__1");
      return emptyPageOnly && emptyPageOnly.results ? emptyPageOnly.results.slice(0, 20) : [];
    }
    const exactPages = cache.pagesForTerm(term);
    const emptyPage  = cache.get("__empty__1");
    let pool = [];
    for (let i=0; i<exactPages.length; i++){
      const p = exactPages[i];
      if (p && p.results) pool = pool.concat(p.results);
    }
    if (emptyPage && emptyPage.results) pool = pool.concat(emptyPage.results);
    const uniq = new Map();
    for (let j=0; j<pool.length; j++){
      const r = pool[j];
      if (!r || !r.text) continue;
      if (r.text.toLowerCase().indexOf(t) !== -1 && !uniq.has(r.id)){
        uniq.set(r.id, r);
        if (uniq.size >= 20) break;
      }
    }
    return Array.from(uniq.values());
  }

  let AUTOS = [];
  function closeAllExcept(current){
    for (let i=0; i<AUTOS.length; i++){
      const inst = AUTOS[i];
      if (inst !== current){
        inst.st.active = false;
        inst.box.classList.remove("visible");
        inst.box.style.display = "none";
      }
    }
  }

  /* ───────── Autocomplete ───────── */
  function makeAuto(cfg){
    const inp   = cfg.inp;
    const hid   = cfg.hid;
    const box   = cfg.box;
    const url   = cfg.url;
    const cache = cfg.cache;
    const warmup = cfg.warmup !== false;

    const st = {
      page: 1, term: "", more: true, busy: false,
      debounceMs: 90, rafScrollScheduled: false, tmr: null,
      ctrl: null, lastReqKey: "", active: false
    };

    function showInitialSuggestions(){
      const initial = cache.get("__empty__1");
      if (initial && initial.results && initial.results.length){
        if (!st.active) return;
        renderList(box, initial.results, true);
      } else {
        fetchData("", 1, { force: true, ifActiveOnly: true });
      }
    }

    function instantPaint(){
      if (!st.active) return;
      if (!st.term){
        showInitialSuggestions();
        return;
      }
      const items = localFilter(cache, st.term);
      if (items.length){
        renderList(box, items, true);
      } else {
        box.innerHTML = "";
        box.style.display = "none";
      }
    }

    function fetchData(q, p, opts){
      if (typeof p === "undefined") p = 1;
      opts = opts || {};
      const force = !!opts.force;
      const ifActiveOnly = (opts.ifActiveOnly !== false);

      if (!force && (st.busy || (!st.more && p > 1))) return;
      st.busy = true;

      const key = q + "__" + p;
      const cached = cache.get(key);
      if (cached && !force){
        if (ifActiveOnly && st.active && p === 1) renderList(box, cached.results || [], true);
        st.more = !!(cached && cached.has_more);
        st.busy = false;
        return Promise.resolve();
      }

      if (st.ctrl && typeof st.ctrl.abort === "function") st.ctrl.abort();
      st.ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
      st.lastReqKey = key;

      const fetchOpts = {
        headers: { "Accept": "application/json" },
        cache: "no-store"
      };
      if (st.ctrl) fetchOpts.signal = st.ctrl.signal;

      return fetch(url + "?term=" + encodeURIComponent(q) + "&page=" + p, fetchOpts)
        .then((r) => {
          if (!r.ok) throw new Error("Error de red (" + r.status + ")");
          return r.json();
        })
        .then((j) => {
          cache.set(key, j);
          if (!q && p === 1) cache.set("__empty__1", j);
          if (st.lastReqKey === key && st.active){
            const results = j && j.results ? j.results : [];
            if (p === 1 && (!results || !results.length)){
              if (q){
                box.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
                box.classList.add("visible");
                box.style.display = "block";
              } else {
                box.innerHTML = "";
                box.style.display = "none";
              }
            } else {
              renderList(box, results, p === 1);
            }
            st.more = !!(j && j.has_more);
          }
        })
        .catch((e) => {
          if (e && e.name !== "AbortError"){
            console.error(e);
            show(errBox, iconErr("Error al cargar las opciones. Intente de nuevo."));
          }
        })
        .finally(() => { st.busy = false; });
    }

    function debouncedSearch(){
      if (st.tmr) clearTimeout(st.tmr);
      st.tmr = setTimeout(() => {
        st.page = 1; st.more = true;
        instantPaint();
        fetchData(st.term, 1, { ifActiveOnly: true });
      }, st.debounceMs);
    }

    // Selección común (usada por Enter y por click)
    function selectOption(opt){
      if (!opt) return;
      inp.value = opt.textContent;
      hid.value = opt.getAttribute("data-id") || "";
      box.classList.remove("visible");
      box.style.display = "none";
      // limpiar posible error visual del campo
      const errId = (hid === usrHid) ? "usuarioid" : (hid === sucHid ? "sucursalid" : "");
      if (errId){
        const div = $("#error-id_" + errId);
        if (div){ div.classList.remove("visible"); div.style.display = "none"; div.innerHTML = ""; }
        inp.classList.remove("input-error");
      }
      if (hid === usrHid) cacheUsr.clear();   // tras elegir usuario, refrescar cache de usuarios
      // avanzar al próximo campo (un tick para no interferir con el pointerup)
      setTimeout(() => focusNext(inp), 0);
    }

    /* ── Eventos de entrada ── */
    inp.addEventListener("focus", () => {
      st.active = true;
      closeAllExcept(instanceAPI);
      st.term = (inp.value || "").trim();
      st.page = 1; st.more = true;
      if (!st.term){
        showInitialSuggestions();
        fetchData("", 1, { force: true, ifActiveOnly: true });
      } else {
        instantPaint();
        fetchData(st.term, 1, { ifActiveOnly: true });
      }
    });

    inp.addEventListener("blur", () => { st.active = false; });

    inp.addEventListener("input", () => {
      hid.value  = "";
      st.term    = (inp.value || "").trim();
      if (!st.term){
        st.page = 1; st.more = true; st.active = true;
        showInitialSuggestions();
        fetchData("", 1, { force: true, ifActiveOnly: true });
        return;
      }
      debouncedSearch();
    }, { passive: true });

    // Enter: selecciona primera opción y sigue
    inp.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const firstOpt = box.querySelector(".autocomplete-option");
      if (isVisible(box) && firstOpt){
        selectOption(firstOpt);
        return;
      }
      focusNext(inp);
    });

    // CLICK/TOUCH en opciones (soporta mouse, touch y stylus)
    const pickWithPointer = (e) => {
      const opt = e.target.closest(".autocomplete-option");
      if (!opt) return;
      // impedir que el mousedown provoque blur antes de tiempo
      e.preventDefault();
      selectOption(opt);
    };
    // pointerdown cubre mouse+touch; mousedown es fallback para navegadores viejos
    box.addEventListener("pointerdown", pickWithPointer);
    box.addEventListener("mousedown",  pickWithPointer);
    // como refuerzo, también en click por compatibilidad (no pasa nada si ya se seleccionó)
    box.addEventListener("click", (e) => {
      const opt = e.target.closest(".autocomplete-option");
      if (opt) selectOption(opt);
    });

    const instanceAPI = { st: st, box: box };
    AUTOS.push(instanceAPI);

    if (warmup){ fetchData("", 1, { force: true, ifActiveOnly: false }); }
  }

  makeAuto({inp: usrInp, hid: usrHid, box: usrBox, url: usuarioAutocompleteUrl,  cache: cacheUsr, warmup: true});
  makeAuto({inp: sucInp, hid: sucHid, box: sucBox, url: sucursalAutocompleteUrl, cache: cacheSuc, warmup: true});

  /* ───────── Enter en inputs normales ───────── */
  form.addEventListener("keydown", function(e){
    if (e.key !== "Enter" || e.isComposing) return;
    const t = e.target;
    if (t && t.tagName === "TEXTAREA") return;
    if (t === usrInp || t === sucInp) return;
    e.preventDefault();
    focusNext(t);
  });

  /* ───────── submit ───────── */
  form.addEventListener("submit", function(ev){
    ev.preventDefault();
    resetUI();

    let bad = false;
    if (!usrHid.value){ fieldErr("usuarioid",  "Debe seleccionar un usuario.");  bad = true; }
    if (!sucHid.value){ fieldErr("sucursalid", "Debe seleccionar una sucursal."); bad = true; }
    if (bad) return;

    fetch(form.action, {
      method : "POST",
      headers: {
        "X-CSRFToken"     : csrftoken,
        "X-Requested-With": "XMLHttpRequest",
        "Accept"          : "application/json"
      },
      body : new FormData(form)
    })
    .then((r) => r.json())
    .then((data) => {
      if (data.success){
        show(okBox, '<i class="fas fa-check-circle"></i> Empleado agregado correctamente.');
        form.reset();
        usrHid.value = ""; sucHid.value = "";
        cacheUsr.clear();
      } else {
        const errs = (typeof data.errors === "string") ? JSON.parse(data.errors) : data.errors;
        Object.keys(errs).forEach((f) => {
          errs[f].forEach((e) => fieldErr(f, e.message || "Error en el campo."));
        });
      }
    })
    .catch((err) => {
      console.error(err);
      show(errBox, iconErr("Ocurrió un error inesperado al guardar. Inténtelo de nuevo."));
    });
  });
})();
