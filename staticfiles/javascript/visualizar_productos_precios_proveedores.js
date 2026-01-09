/*  visualizar_productos_precios_proveedores.js
    — DataTable responsivo
    — Autocomplete proveedor ULTRA-RÁPIDO (SWR + cache + anti-stale)
    — Enter → selecciona 1ª opción
    — Eliminación vía AJAX
------------------------------------------------------------------*/
$(function () {
  "use strict";

  /* ---------- DataTable ---------- */
  const $tbl = $("#preciosTable");
  const dt   = $tbl.length ? $tbl.DataTable({
    paging:true, searching:true, info:true, responsive:true,
    language:{
      search:"", zeroRecords:"Sin registros",
      info:"Mostrando _START_ a _END_ de _TOTAL_",
      paginate:{ first:"Prim.", last:"Últ.", next:"Sig.", previous:"Ant." }
    },
    columnDefs:[{ targets:"no-sort", orderable:false }]
  }) : null;

  /* ---------- flash helper ---------- */
  const $flash = $("<div class='alert' style='display:none'></div>").insertAfter("h2");
  const showFlash = (ok,msg) =>
    $flash.removeClass("alert-success alert-error")
          .addClass(ok?"alert-success":"alert-error")
          .text(msg).show();

  /* ---------- eliminar precio ---------- */
  $tbl.on("click",".btn-eliminar",function(){
    const $btn = $(this), id = $btn.data("precio-id");
    if (!id || !confirm("¿Eliminar este precio?")) return;

    $.post(eliminarPrecioUrl.replace("0", id), {
      csrfmiddlewaretoken: $("input[name=csrfmiddlewaretoken]").val()
    })
    .done(res=>{
      if(res.success && dt){ dt.row($btn.closest("tr")).remove().draw(false); }
      showFlash(res.success, res.message);
    })
    .fail(()=> showFlash(false,"Error al eliminar el precio."));
  });

  /* ---------- autocomplete proveedor (instantáneo) ---------- */
  const $inp = $("#id_proveedor_autocomplete"),
        $hid = $("#id_proveedor"),
        $box = $("#proveedor-autocomplete-results"),
        $form= $("#proveedorForm");

  // Estado y caché
  const ac = {
    page:1, term:"", loading:false, more:true,
    reqId:0, aborter:null,
    cache:Object.create(null),   // `${term}::${page}` -> {results, has_more}
    basePage:[], baseHasMore:true
  };

  const open  = ()=>{ $box.show(); };
  const close = ()=>{ $box.hide(); };
  const clear = ()=>{ $box.empty(); };

  function paint(list, replace=true){
    requestAnimationFrame(()=>{
      const frag = document.createDocumentFragment();
      list.forEach(r=>{
        const d = document.createElement("div");
        d.className = "autocomplete-option";
        d.dataset.id = r.id;
        d.textContent = r.text;
        frag.appendChild(d);
      });
      if(replace) $box[0].replaceChildren(frag);
      else        $box[0].appendChild(frag);
      open();
    });
  }

  async function fetchPage(term, page){
    const key = `${term}::${page}`;
    if (ac.cache[key]) return ac.cache[key];

    const url = `${proveedorAutocompleteUrl}?term=${encodeURIComponent(term)}&page=${page}`;
    const r = await fetch(url, { signal: ac.aborter?.signal });
    const data = await r.json();
    ac.cache[key] = data;
    return data;
  }

  async function serverSearch({reset=true}={}){
    if (ac.loading || !ac.more) return;

    // aborto/anti-stale
    if (ac.aborter) { try{ ac.aborter.abort(); }catch(_){/*no-op*/} }
    ac.aborter = new AbortController();
    const myReq = ++ac.reqId;
    const termAtReq = ac.term;
    const pageAtReq = ac.page;

    ac.loading = true;
    try{
      const data = await fetchPage(termAtReq, pageAtReq);
      // si el término/página cambió o llegó otra respuesta, descartar
      if (myReq !== ac.reqId || termAtReq !== ac.term || pageAtReq !== ac.page) return;

      if (reset) clear();

      if (data.results?.length){
        paint(data.results, /*replace*/ reset);
        ac.more = !!data.has_more;
      }else{
        ac.more = false;
        if (reset){
          $box.html('<div class="autocomplete-no-result">Sin resultados</div>');
          open();
        }
      }
    }catch(_){ /* abortado o red */ }
    finally{ ac.loading = false; }
  }

  // Muestra INSTANTÁNEO: cache 1ª página o filtra basePage localmente
  function instant(){
    const key1 = `${ac.term}::1`;
    let painted = false;

    if (ac.cache[key1]?.results){
      paint(ac.cache[key1].results, true);
      painted = true;
    }else if (ac.basePage.length){
      const t = ac.term.toLowerCase();
      const local = ac.basePage
        .filter(r => r.text.toLowerCase().includes(t) || r.text.toLowerCase().startsWith(t))
        .slice(0, 50);
      paint(local, true);
      painted = true;
    }

    ac.page = 1; ac.more = true;
    serverSearch({reset:true});

    if(!painted) requestAnimationFrame(open);
  }

  // Eventos input/focus/scroll/click
  $inp.on("input", ()=>{
    $hid.val("");
    ac.term = $.trim($inp.val());

    if(!ac.term){
      // término vacío → base instantánea + fetch
      ac.page = 1; ac.more = ac.baseHasMore;
      if(ac.basePage.length){ paint(ac.basePage, true); $box.scrollTop(0); }
      serverSearch({reset:true});
      return;
    }
    instant();
  });

  $inp.on("focus", ()=>{
    ac.term = $.trim($inp.val());
    ac.page = 1; ac.more = true;
    if(!ac.term){
      if(ac.basePage.length){ paint(ac.basePage, true); }
      serverSearch({reset:true});
    }else{
      instant();
    }
  });

  // 👉 Enter → seleccionar primera opción
  $inp.on("keydown", (e)=>{
    if(e.key === "Enter"){
      e.preventDefault();
      const $first = $box.find(".autocomplete-option").first();
      if($first.length){
        $inp.val($first.text());
        $hid.val($first.data("id"));
        close();
        $form.trigger("submit");
      }
    }
  });

  $box.on("scroll", function(){
    if(this.scrollTop + this.clientHeight >= this.scrollHeight - 4 &&
       ac.more && !ac.loading){
      ac.page++; serverSearch({reset:false});
    }
  });

  $box.on("click", ".autocomplete-option", function(){
    $inp.val($(this).text());
    $hid.val($(this).data("id"));
    close();
    $form.trigger("submit");
  });

  $(document).on("click", e=>{
    if(!$(e.target).closest("#id_proveedor_autocomplete, #proveedor-autocomplete-results").length){
      close();
    }
  });

  // Prefetch base (término vacío, página 1) para primer pintado instantáneo
  (async function prefetchBase(){
    try{
      const data = await fetchPage("", 1);
      ac.basePage = data.results || [];
      ac.baseHasMore = !!data.has_more;
      // No abrimos/cerramos aquí: sólo calentamos caché
    }catch(_){}
  })();

});
