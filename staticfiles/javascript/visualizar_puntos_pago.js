/*  visualizar_puntos_pago.js
    ─────────────────────────────────────────
    · DataTable responsivo
    · Autocomplete sucursal INSTANTÁNEO (SWR: local→remoto)
      - Filtro local inmediato (basePage) y/o caché
      - Revalidación con servidor con reqId anti-stale
      - Vacío → muestra lista base (página 1)
    · Scroll infinito
    · Eliminación vía AJAX
    · Enter → selecciona la primera opción visible
*/
$(function () {
  "use strict";

  /* ---------- DataTable ---------- */
  const $tbl = $("#puntosPagoTable");
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
  const flash  = (ok,msg)=> $flash.removeClass("alert-success alert-error")
                                  .addClass(ok?"alert-success":"alert-error")
                                  .text(msg).show();

  /* ---------- eliminar punto ---------- */
  $tbl.on("click",".btn-eliminar",function(){
    const $btn = $(this), id = $btn.data("pp-id");
    if(!id || !confirm("¿Eliminar este punto de pago?")) return;

    $.post(eliminarPuntoPagoUrl.replace("0", id),{
      csrfmiddlewaretoken:$("input[name=csrfmiddlewaretoken]").val()
    }).done(res=>{
      if(res.success && dt){ dt.row($btn.closest("tr")).remove().draw(false); }
      flash(res.success,res.message);
    }).fail(()=> flash(false,"Error al eliminar el punto de pago."));
  });

  /* ---------- autocomplete sucursal (instantáneo) ---------- */
  const $inp = $("#id_sucursal_autocomplete"),
        $hid = $("#id_sucursal"),
        $box = $("#sucursal-autocomplete-results"),
        $form = $("#sucursalForm");

  // Estado y caché
  let page = 1, term = "", loading = false, more = true;
  let basePage = [], baseHasMore = true;
  const cache = Object.create(null);
  let reqId = 0;
  let xhr = null;

  const openBox  = ()=>{ $box.show(); };
  const closeBox = ()=>{ $box.hide(); };
  const clearBox = ()=>{ $box.empty(); };

  // Render rápido (rAF)
  function paint(list, replace = true){
    window.requestAnimationFrame(()=>{
      if(replace) $box.empty();
      const frag = document.createDocumentFragment();
      list.forEach(r=>{
        const d = document.createElement("div");
        d.className = "autocomplete-option";
        d.dataset.id = r.id;
        d.textContent = r.text;
        frag.appendChild(d);
      });
      $box[0].appendChild(frag);
      openBox();
    });
  }

  // Fetch con caché + reqId anti-stale
  function fetchPage(q, p, {replace=true} = {}){
    const key = `${q}::${p}`;
    if (cache[key]) {
      paint(cache[key].results || [], replace);
      more = !!cache[key].has_more;
    }
    if (xhr && xhr.readyState !== 4) { try { xhr.abort(); } catch(e) {} }
    const myReq = ++reqId;
    loading = true;
    xhr = $.getJSON(`${sucursalAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}`)
      .done(data=>{
        cache[key] = data || {results:[], has_more:false};
        if (myReq !== reqId || q !== term || p !== page) return;
        if (!replace && p>1) { paint(data.results || [], false); }
        else { clearBox(); paint(data.results || [], true); }
        more = !!data.has_more;
      })
      .always(()=>{ loading = false; });
  }

  // Instantáneo: pinta local/caché y revalida
  function instantSearch(){
    const key = `${term}::1`;
    let painted = false;
    if (cache[key]?.results) {
      clearBox(); paint(cache[key].results, true);
      more = !!cache[key].has_more; painted = true;
    } else if (basePage.length) {
      const t = term.toLowerCase();
      const local = basePage.filter(r => r.text.toLowerCase().includes(t)).slice(0,50);
      clearBox(); paint(local, true);
      more = baseHasMore; painted = true;
    }
    page = 1; more = true;
    fetchPage(term, page, {replace:true});
    if (!painted) { openBox(); }
  }

  /* ---- Eventos ---- */
  $inp.on("input", ()=>{
    $hid.val("");
    term = $.trim($inp.val());
    if (!term) {
      page = 1; more = baseHasMore;
      if (basePage.length) { clearBox(); paint(basePage, true); }
      fetchPage("", 1, {replace:true});
      return;
    }
    instantSearch();
  });

  $inp.on("focus", ()=>{
    term = $.trim($inp.val());
    if (!term) {
      if (basePage.length) { clearBox(); paint(basePage, true); }
      page = 1; more = true; fetchPage("", 1, {replace:true});
    } else { instantSearch(); }
  });

  $box.on("scroll", function(){
    if (this.scrollTop + this.clientHeight >= this.scrollHeight - 4 && more && !loading){
      page += 1; fetchPage(term, page, {replace:false});
    }
  });

  $box.on("click", ".autocomplete-option", function(){
    $inp.val($(this).text());
    $hid.val($(this).data("id"));
    closeBox();
    $form.trigger("submit");
  });

  $(document).on("click", e=>{
    if (!$(e.target).closest("#id_sucursal_autocomplete, #sucursal-autocomplete-results").length) closeBox();
  });

  // ⬅️ Enter → seleccionar la primera opción
  $inp.on("keydown", e=>{
    if(e.key === "Enter"){
      e.preventDefault();
      const $first = $box.find(".autocomplete-option").first();
      if($first.length){
        $inp.val($first.text());
        $hid.val($first.data("id"));
        closeBox();
        $form.trigger("submit");
      }
    }
  });

  // Prefetch base
  (function prefetchBase(){
    const key = `::1`;
    if (cache[key]){ basePage = cache[key].results||[]; baseHasMore=!!cache[key].has_more; return; }
    $.getJSON(`${sucursalAutocompleteUrl}?term=&page=1`).done(data=>{
      cache[key] = data || {results:[], has_more:false};
      basePage = cache[key].results||[];
      baseHasMore = !!cache[key].has_more;
    });
  })();
});
