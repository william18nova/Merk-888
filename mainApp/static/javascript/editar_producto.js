/*  static/javascript/editar_producto.js  */
(() => {
  "use strict";

  /* ───────── helpers ───────── */
  const $  = sel => document.querySelector(sel);
  const $$ = sel => document.querySelectorAll(sel);
  const toKey = (term, page) => `${(term||"").trim().toLowerCase()}|${page}`;

  /* CSRF (cookie → header) */
  const csrftoken =
    (document.cookie.split(";").map(c => c.trim())
      .find(c => c.startsWith("csrftoken=")) || "")
      .split("=")[1] || "";

  /* UI helpers */
  const iconErr = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const show    = (el, html, flex = false) => { if (!el) return; el.innerHTML = html; el.style.display = flex ? "flex" : "block"; };
  const hide    = el => { if (el) { el.style.display = "none"; el.innerHTML = ""; } };

  /* ───────── elementos DOM ───────── */
  const form      = $("#productoForm");
  const errBox    = $("#error-message");
  const okBox     = $("#success-message");

  const catInput  = $("#id_categoria_autocomplete");
  const catHidden = $("#id_categoria");
  const catBox    = $("#categoria-autocomplete-results");

  /* Botón de guardar */
  const submitBtn =
    form?.querySelector('button[type="submit"], .btn-guardar, .btn-guardar-cambios, .btn-agregar-producto');

  /* ───────── reset UI ───────── */
  function resetUI () {
    hide(errBox); hide(okBox);
    $$(".field-error").forEach(div => { div.classList.remove("visible"); hide(div); });
    $$(".input-error").forEach(inp => inp.classList.remove("input-error"));
  }

  /* ───────── pinta errores del backend ───────── */
  function renderErrors (errs = {}) {
    if (errs.__all__) show(errBox, errs.__all__.map(e => iconErr(e.message)).join("<br>"));
    for (const [field, arr] of Object.entries(errs)) {
      if (field === "__all__") continue;
      const div   = $(`#error-id_${field}`);
      const input = $(`#id_${field}`);
      if (div) {
        div.innerHTML = arr.map(e => iconErr(e.message)).join("<br>");
        div.classList.add("visible");
        div.style.display = "block";
      }
      if (input) input.classList.add("input-error");
    }
  }

  /* ───────── util robusto: parsear JSON o mostrar texto de error ───────── */
  async function readJSONorThrow(resp){
    const ct   = resp.headers.get("content-type") || "";
    const body = await resp.text();
    const isJSON = /\bapplication\/json\b/i.test(ct);
    if (!isJSON){
      const snippet = body.slice(0, 280);
      throw new Error(`Servidor devolvió contenido no-JSON (${resp.status}). ${snippet}`);
    }
    try{
      const data = JSON.parse(body);
      if (!resp.ok){
        const msg = (data && (data.detail || data.message)) ? ` ${data.detail || data.message}` : "";
        throw new Error(`Error ${resp.status}.${msg}`);
      }
      return data;
    }catch(_e){
      throw new Error(`Respuesta JSON inválida del servidor (${resp.status}).`);
    }
  }

  /* ───────── Autocomplete «Categoría» (instantáneo + remoto abortable + caché) ───────── */
  const cache = Object.create(null);
  let page = 1, term = "", hasMore = true, loading = false;
  let debounceTimer, ctrl = null;
  let painted = [];

  function drawCats(data, replace = true){
    if (!catBox) return;
    if (replace) catBox.innerHTML = "";
    const rows = data?.results || [];
    if (rows.length){
      const frag = document.createDocumentFragment();
      rows.forEach(o => {
        const opt = document.createElement("div");
        opt.className   = "autocomplete-option";
        opt.textContent = o.text;
        opt.dataset.id  = o.id;
        frag.appendChild(opt);
      });
      catBox.appendChild(frag);
    } else if (replace){
      catBox.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
    }
    catBox.classList.add("visible");
    hasMore = !!data?.has_more;

    painted = Array.from(catBox.querySelectorAll(".autocomplete-option"))
                   .map(n => ({ id:n.dataset.id, text:n.textContent }));
  }

  async function fetchCats(q, p = 1){
    const key = toKey(q, p);

    if (cache[key]) drawCats(cache[key], p === 1);

    try { ctrl?.abort(); } catch(_){}
    ctrl = typeof AbortController === "function" ? new AbortController() : null;

    loading = true;
    try {
      const url = `${categoriaAutocompleteUrl}?term=${encodeURIComponent(q || "")}&page=${p}`;
      const res = await fetch(url, {
        signal : ctrl?.signal,
        headers: { "Accept":"application/json", "X-Requested-With":"XMLHttpRequest" }
      });
      const data = await readJSONorThrow(res);
      cache[key] = data;
      if (q !== term) return;
      drawCats(data, p === 1);
    } catch (e) {
      console.error("[autocomplete categoria] ", e);
      if (p === 1 && catBox){
        catBox.innerHTML = '<div class="autocomplete-no-result">No se pudieron cargar opciones</div>';
        catBox.classList.add("visible");
      }
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  function localFilterInstant(q){
    const t = (q||"").toLowerCase();
    if (!t){
      const key = toKey("", 1);
      if (cache[key]) drawCats(cache[key], true);
      return;
    }
    if (!painted.length) return;
    const filtered = painted.filter(i => (i.text||"").toLowerCase().includes(t));
    drawCats({ results: filtered, has_more: false }, true);
  }

  function kickFetch(force=false){
    clearTimeout(debounceTimer);
    if (force){ page = 1; hasMore = true; fetchCats(term, 1); }
    else debounceTimer = setTimeout(()=>{ page = 1; hasMore = true; fetchCats(term, 1); }, 120);
  }

  catInput?.addEventListener("input", () => {
    if (catHidden) catHidden.value = "";
    term = catInput.value.trim();
    page = 1; hasMore = true;
    localFilterInstant(term);
    if (!term) kickFetch(true);
    else       kickFetch(false);
  });

  catInput?.addEventListener("focus", () => {
    term = catInput.value.trim();
    page = 1; hasMore = true;
    fetchCats(term, 1);
  });

  catBox?.addEventListener("scroll", () => {
    if (catBox.scrollTop + catBox.clientHeight >= catBox.scrollHeight - 5){
      if (hasMore && !loading){ page++; fetchCats(term, page); }
    }
  });

  catBox?.addEventListener("click", e => {
    const el = e.target.closest(".autocomplete-option");
    if (!el) return;
    catInput.value  = el.textContent;
    if (catHidden) catHidden.value = el.dataset.id;
    catBox.classList.remove("visible");
    catBox.innerHTML = "";
    hasMore = false;
    painted = [];
    if (isLastFocusable(catInput)) submitBtn?.click();
    else                           focusNext(catInput);
  });

  document.addEventListener("click", e => {
    if (!catInput?.contains(e.target) && !catBox?.contains(e.target)) {
      catBox?.classList.remove("visible");
    }
  });

  /* ───────── ENTER: cadena de foco + elegir top en autocomplete ───────── */
  function getFocusable(){
    if (!form) return [];
    return Array.from(form.querySelectorAll('input, select, textarea, button'))
      .filter(el =>
        !el.disabled &&
        (el.type||"").toLowerCase() !== "hidden" &&
        el.offsetParent !== null
      );
  }
  function isLastFocusable(el){
    const list = getFocusable();
    return list.length && list[list.length-1] === el;
  }
  function focusNext(current){
    const list = getFocusable();
    const idx  = list.indexOf(current);
    if (idx >= 0 && idx < list.length - 1){
      const nxt = list[idx+1];
      if (nxt.tagName === 'BUTTON' || (nxt.type||'').toLowerCase() === 'submit'){
        nxt.click();
      } else {
        nxt.focus(); nxt.select?.();
      }
    } else {
      submitBtn?.click();
    }
  }

  form?.addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    const el = e.target;

    if (el === catInput) {
      e.preventDefault();
      const first = catBox?.querySelector(".autocomplete-option");
      if (first){
        catInput.value  = first.textContent;
        if (catHidden) catHidden.value = first.dataset.id;
      }
      catBox?.classList.remove("visible");
      if (catBox) catBox.innerHTML = "";
      painted = [];

      if (isLastFocusable(catInput)) submitBtn?.click();
      else                           focusNext(catInput);
      return;
    }

    if (["INPUT","SELECT","TEXTAREA"].includes(el.tagName)) {
      e.preventDefault();
      focusNext(el);
    }
  });

  /* ───────── envío del formulario ───────── */
  form?.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    if (!catHidden?.value) {
      const fld = $("#error-id_categoria");
      show(fld, iconErr("Este campo es obligatorio."));
      fld?.classList.add("visible");
      fld && (fld.style.display = "block");
      show(errBox, iconErr("Corrige los errores del formulario."));
      return;
    }

    try {
      const resp  = await fetch(form.action, {
        method : "POST",
        headers: {
          "X-CSRFToken"      : csrftoken,
          "X-Requested-With" : "XMLHttpRequest",
          "Accept"           : "application/json",
        },
        body : new FormData(form),
      });

      let data;
      try{
        data = await readJSONorThrow(resp);
      }catch(parseErr){
        console.error(parseErr);
        show(errBox, iconErr("Error del servidor. Intenta nuevamente. Si persiste, contacta al administrador."));
        return;
      }

      if (data.success) {
        sessionStorage.setItem(
          "flash-producto",
          `<i class="fas fa-check-circle"></i> Producto «${data.nombre}» actualizado correctamente.`
        );
        window.location.href = data.redirect_url || window.location.href;
        return;
      }

      renderErrors(typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors);
    } catch (err) {
      console.error(err);
      show(errBox, iconErr("Ocurrió un error inesperado."));
    }
  });

  /* ========= Detector de pistola de códigos — NO bloquea otros campos ========= */
  (function initBarcodeScannerDetector(){
    // ✅ Soporta codigo_de_barras (tu caso real)
    const targetSelector =
      '[data-barcode-target],' +
      '#id_codigo_de_barras,' +
      'input[name="codigo_de_barras"],' +
      'input[id*="codigo_de_barras"],' +
      'input[name*="barcode"], input[id*="barcode"],' +
      'input[name*="barras"], input[id*="barras"]';

    const CFG = {
      minChars   : 6,
      gapMs      : 90,
      finishKeys : ['Enter','Tab','NumpadEnter'],
      acceptCRLF : true,
      debug      : false
    };

    const q = (sel) => document.querySelector(sel);
    const isInput = el => el && el.tagName === 'INPUT' && !el.readOnly && !el.disabled;

    function looksBarcode(el){
      if (!el) return false;
      if (el.hasAttribute('data-barcode-target')) return true;
      const id=(el.id||'').toLowerCase(), nm=(el.name||'').toLowerCase();
      return /barras|barcode|ean|upc|codigo_de_barras/.test(id) || /barras|barcode|ean|upc|codigo_de_barras/.test(nm);
    }

    function resolveTarget(){
      const ae = document.activeElement;
      if (isInput(ae) && looksBarcode(ae)) return ae;
      const el = q(targetSelector);
      if (isInput(el)) return el;
      return null;
    }

    function tryFocusNext(input){
      try { typeof focusNext === 'function' ? focusNext(input) : input.blur(); } catch(_) {}
    }

    function setBarcode(code){
      const input = resolveTarget();
      if (!input){ if (CFG.debug) console.warn('[scanner] no target'); return; }
      try{
        input.value = code;
        input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));
      }catch(_){}
      tryFocusNext(input);
      if (CFG.debug) console.log('[scanner] filled:', code);
    }

    let buf='', first=0, last=0, idleTimer=null;
    const reset = ()=>{ buf=''; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} };

    const isCharKey = (e) => {
      if (e.key && e.key.length === 1) return true;
      if (e.code && e.code.startsWith('Numpad')) return true;
      return false;
    };
    const mapNumpad = (code) => ({
      'Numpad0':'0','Numpad1':'1','Numpad2':'2','Numpad3':'3','Numpad4':'4',
      'Numpad5':'5','Numpad6':'6','Numpad7':'7','Numpad8':'8','Numpad9':'9',
      'NumpadDecimal':'.','NumpadDivide':'/','NumpadMultiply':'*','NumpadSubtract':'-','NumpadAdd':'+'
    }[code] || '');

    function tryFinish(reason=''){
      const span = last - first;
      const fastEnough = buf && span < buf.length * (CFG.gapMs + 15);
      if (CFG.debug) console.log('[scanner] finish?', {buf, len:buf.length, span, reason, fastEnough});
      if (fastEnough && buf.length >= CFG.minChars){
        const code = buf; reset(); setBarcode(code); return true;
      }
      reset(); return false;
    }

    document.addEventListener('keydown', function(e){
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }

      if (CFG.finishKeys.includes(e.key)){
        if (tryFinish('finishKey')){
          e.preventDefault();
          e.stopImmediatePropagation();
        }
        return;
      }

      if (isCharKey(e)){
        const t = Date.now();
        if (buf && (t - last) > CFG.gapMs) { buf=''; first=t; }
        if (!buf) first=t;

        let ch = e.key.length === 1 ? e.key : '';
        if (!ch && e.code && e.code.startsWith('Numpad')) ch = mapNumpad(e.code);
        if (!ch) return;

        buf += ch; last=t;

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(()=>{ tryFinish('idle'); }, CFG.gapMs*5);
      } else {
        if (e.key !== 'Shift') reset();
      }
    }, true);

    document.addEventListener('paste', (e)=>{
      const txt=(e.clipboardData||window.clipboardData)?.getData('text')||'';
      const val=(txt||'').trim();
      if (val && val.length >= CFG.minChars){
        e.preventDefault(); setBarcode(val);
      }
    }, true);
  })();

})();
