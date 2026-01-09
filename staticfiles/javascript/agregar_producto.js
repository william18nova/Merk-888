/* static/javascript/agregar_producto.js */
(() => {
  "use strict";

  /* ---------- helpers ---------- */
  const $   = sel => document.querySelector(sel);
  const $$  = sel => document.querySelectorAll(sel);
  const csrftoken =
        document.cookie.split(";").map(c => c.trim())
        .find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";

  const icon = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const show = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide = el         => { el.style.display = "none"; el.innerHTML = ""; };

  /* ---------- elementos ---------- */
  const form   = $("#productoForm");
  const okBox  = $("#success-message");
  const errBox = $("#error-message");

  const catInput   = $("#id_categoria_autocomplete");
  const catHidden  = $("#id_categoria");
  const catResult  = $("#categoria-autocomplete-results");
  const btnSubmit  = $(".btn-agregar-producto");

  /* ========= 1) AUTOCOMPLETE (instantáneo) ========= */
  let page=1, term="", more=true, busy=false;
  let debounce;

  function resetAutocomplete(){
    catResult.innerHTML = ""; catResult.classList.remove("visible");
    page=1; more=true; busy=false;
  }

  function drawCats(data, replace=true){
    if (replace) catResult.innerHTML="";
    const rows = (data.results || []);
    if (rows.length){
      const frag=document.createDocumentFragment();
      rows.forEach(o=>{
        const div=document.createElement("div");
        div.className="autocomplete-option";
        div.dataset.id=o.id; div.textContent=o.text;
        frag.appendChild(div);
      });
      catResult.appendChild(frag);
    }else if (replace){
      catResult.innerHTML = '<div class="autocomplete-no-result">Sin resultados</div>';
    }
    catResult.classList.add("visible");
    more = !!data.has_more;
  }

  async function fetchCats(){
    if (busy || !more) return;
    busy = true;
    try{
      const r = await fetch(`${categoriaAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}`);
      const data = await r.json();
      drawCats(data, page===1);
    }catch{ /* ignore */ }
    busy = false;
  }

  const kickFetch = (force=false)=>{
    clearTimeout(debounce);
    if(force){ page=1; more=true; fetchCats(); }
    else debounce=setTimeout(()=>{ page=1; more=true; fetchCats(); },110);
  };

  catInput.addEventListener("input", e=>{
    term = e.target.value.trim(); page=1; more=true; catHidden.value="";
    if (!term){
      // si se borra todo, muestra la primera página (lista completa) para feedback
      kickFetch(true);
      return;
    }
    // respuesta local instantánea (filtrado sobre lo ya pintado)
    const list = Array.from(catResult.querySelectorAll(".autocomplete-option"))
      .map(n => ({id:n.dataset.id, text:n.textContent}));
    if (list.length){
      const t = term.toLowerCase();
      const filtered = list.filter(i => (i.text||"").toLowerCase().includes(t));
      drawCats({results:filtered, has_more:false}, true);
    }
    kickFetch();
  });

  catInput.addEventListener("focus", ()=>{
    term = catInput.value.trim(); page=1; more=true;
    fetchCats();
  });

  catResult.addEventListener("scroll", ()=>{
    if (catResult.scrollTop + catResult.clientHeight >= catResult.scrollHeight - 5){
      if (more && !busy){ page++; fetchCats(); }
    }
  });

  catResult.addEventListener("click", e=>{
    const opt = e.target.closest(".autocomplete-option");
    if (!opt) return;
    catInput.value  = opt.textContent;
    catHidden.value = opt.dataset.id;
    resetAutocomplete();
    focusNext(catInput);
  });

  document.addEventListener("click", e=>{
    if (!catInput.contains(e.target) && !catResult.contains(e.target))
      resetAutocomplete();
  });

  /* ========= 2) ENTER: cadena de foco + elegir top en autocomplete ========= */
  function getFocusable(){
    return Array.from(
      form.querySelectorAll('input, select, textarea, button')
    ).filter(el=>{
      if (el.disabled || el.getAttribute('type') === 'hidden') return false;
      return true;
    });
  }
  function focusNext(current){
    const list=getFocusable();
    const idx=list.indexOf(current);
    if (idx>=0 && idx<list.length-1){ list[idx+1].focus(); list[idx+1].select?.(); }
    else if (idx===list.length-1){ btnSubmit?.click(); }
  }

  form.addEventListener("keydown", e=>{
    if (e.key!=="Enter") return;
    const el=e.target;
    // En el autocomplete de categoría: elige la primera opción visible
    if (el===catInput){
      e.preventDefault();
      const first = catResult.querySelector(".autocomplete-option");
      if (first){
        catInput.value  = first.textContent;
        catHidden.value = first.dataset.id;
      }
      resetAutocomplete();
      focusNext(catInput);
      return;
    }
    // En cualquier otro input/select/textarea: salta al siguiente
    if (el.tagName==="INPUT" || el.tagName==="SELECT" || el.tagName==="TEXTAREA"){
      e.preventDefault();
      focusNext(el);
    }
  });

  /* ========= 3) SUBMIT ========= */
  form.addEventListener("submit", async ev=>{
    ev.preventDefault();

    [okBox, errBox].forEach(hide);
    $$(".field-error").forEach(div=>{ div.innerHTML=""; div.classList.remove("visible"); });
    $$(".input-error").forEach(i=>i.classList.remove("input-error"));

    if (!catHidden.value){
      show($("#error-id_categoria"), icon("Selecciona una categoría"));
      $("#error-id_categoria").classList.add("visible");
      show(errBox, icon("Por favor corrige los errores."));
      return;
    }

    try{
      const resp = await fetch(form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":csrftoken,
          "X-Requested-With":"XMLHttpRequest",
          "Accept":"application/json"
        },
        body:new FormData(form)
      });
      const data = await resp.json();

      if (data.success){
        show(okBox,'<i class="fas fa-check-circle"></i> Producto agregado correctamente.');
        okBox.style.display="flex";
        form.reset(); resetAutocomplete();
        return;
      }
      renderErrors( typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors );

    }catch(err){
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

  function renderErrors(errors){
    if (errors.__all__) show(errBox, errors.__all__.map(e=>icon(e.message)).join("<br>"));
    Object.entries(errors).forEach(([field,msgs])=>{
      if (field==="__all__") return;
      const div   = $("#error-id_"+field);
      const input = $("#id_"+field);
      if (div){
        div.innerHTML = msgs.map(e=>icon(e.message)).join("<br>");
        div.classList.add("visible");
      }
      if (input) input.classList.add("input-error");
    });
  }

  /* ========= 4) Detector global de pistola (robusto, inspirado en generar_venta.js) ========= */
  (function barcodeScannerDetector(){
    const CFG = {
      candidates: [
        '[data-barcode-target]',
        '#id_codigo_barras','#id_cod_barras','#id_codbarras',
        'input[name="codigo_barras"]','input[name="cod_barras"]','input[name="codbarras"]',
        'input[id*="codigo_barras"]','input[id*="cod_barras"]',
        'input[name*="barcode"]','input[id*="barcode"]',
        'input[name*="barras"]','input[id*="barras"]'
      ],
      minChars: 8,
      gapMs: 60,
      finishKeys: ['Enter','Tab'],
      debug: false
    };

    const isInput = el => el && el.tagName === 'INPUT' && !el.readOnly && !el.disabled;
    const looksBarcode = el => {
      const id=(el?.id||'').toLowerCase(), nm=(el?.name||'').toLowerCase();
      return el?.hasAttribute?.('data-barcode-target') ||
             id.includes('barras') || id.includes('barcode') || id.includes('ean') || id.includes('upc') ||
             nm.includes('barras') || nm.includes('barcode') || nm.includes('ean') || nm.includes('upc');
    };

    function resolveTarget(){
      const ae = document.activeElement;
      if (isInput(ae) && looksBarcode(ae)) return ae;
      for (const sel of CFG.candidates){
        const el = document.querySelector(sel);
        if (isInput(el)) return el;
      }
      return null;
    }

    function focusNextSafe(input){
      try{ typeof focusNext === 'function' ? focusNext(input) : input.blur(); }catch(_){}
    }

    function setBarcodeValue(code){
      const input = resolveTarget();
      if (!input) { if (CFG.debug) console.warn('[scanner] No target input'); return; }
      input.value = code;
      input.dispatchEvent(new Event('input',  {bubbles:true}));
      input.dispatchEvent(new Event('change', {bubbles:true}));
      focusNextSafe(input);
      if (CFG.debug) console.log('[scanner] filled:', code);
    }

    let buf = '', first = 0, last = 0, idleTimer = null;
    const reset = ()=>{ buf=''; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} };

    const isCharKey = (e) => {
      if (e.key && e.key.length === 1) return true;
      if (e.code && e.code.startsWith('Numpad')) return true;
      return false;
    };

    function handleFinish(byKey=false){
      const span = last - first;
      const fastEnough = buf && span < buf.length * (CFG.gapMs + 10);
      if (CFG.debug) console.log('[scanner] finish', {buf, len:buf.length, span, byKey, fastEnough});
      if (fastEnough && buf.length >= CFG.minChars){
        const code = buf; reset();
        setBarcodeValue(code);
        return true;
      }
      reset();
      return false;
    }

    document.addEventListener('keydown', function (e){
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }

      const t = Date.now();

      if (CFG.finishKeys.includes(e.key)){
        if (handleFinish(true)){ e.preventDefault(); e.stopImmediatePropagation(); }
        return;
      }

      if (isCharKey(e)){
        if (buf && (t - last) > CFG.gapMs) { buf = ''; first = t; }
        if (!buf) first = t;

        // char mapping (numpad)
        let ch = e.key.length === 1 ? e.key : '';
        if (!ch && e.code && e.code.startsWith('Numpad')){
          const map = {
            'Numpad0':'0','Numpad1':'1','Numpad2':'2','Numpad3':'3','Numpad4':'4',
            'Numpad5':'5','Numpad6':'6','Numpad7':'7','Numpad8':'8','Numpad9':'9',
            'NumpadDecimal':'.','NumpadDivide':'/','NumpadMultiply':'*','NumpadSubtract':'-','NumpadAdd':'+'
          };
          ch = map[e.code] || '';
        }
        if (!ch) return;

        buf += ch; last = t;

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(()=>{ handleFinish(false); }, CFG.gapMs * 5);

        // si el foco no está en el input de barras, evita “ensuciar” otros campos
        const target = resolveTarget();
        if (document.activeElement !== target) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }
      }else{
        if (e.key !== 'Shift') reset();
      }
    }, true);

    document.addEventListener('paste', (e)=>{
      const txt = (e.clipboardData||window.clipboardData)?.getData('text') || '';
      const val = (txt||'').trim();
      if (val && val.length >= CFG.minChars) {
        e.preventDefault();
        setBarcodeValue(val);
      }
    }, true);
  })();

})();
