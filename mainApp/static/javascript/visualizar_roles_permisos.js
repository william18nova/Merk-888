/* visualizar_roles_permisos.js
   — DataTable responsivo
   — Autocomplete de roles (SWR: local/caché + servidor, SOLO roles con permisos)
   — Quitar permiso vía AJAX (papelera) con alert verde/rojo en .messages
   — Botón “Editar permisos del rol” que aparece al elegir rol
*/
$(function () {
  "use strict";

  /* ---------- DataTable ---------- */
  const $tbl = $("#permisosTable");
  const dt   = $tbl.length ? $tbl.DataTable({
    paging:true, searching:true, info:true, responsive:true,
    language:{
      search:"", zeroRecords:"Sin permisos",
      info:"Mostrando _START_ a _END_ de _TOTAL_",
      paginate:{ first:"Prim.", last:"Últ.", next:"Sig.", previous:"Ant." }
    },
    columnDefs:[{ targets:"no-sort", orderable:false }]
  }) : null;

  /* ---------- contenedor de alerts ---------- */
  const $messages = $(".messages");

  function showAlert(ok, msg){
    const $alert = $("<div>")
      .addClass("alert")
      .addClass(ok ? "alert-success" : "alert-error")
      .html(`<i class="fas ${ok ? "fa-check-circle" : "fa-exclamation-circle"}"></i> ${msg}`);
    $messages.empty().append($alert);
  }

  /* ---------- eliminar relación rol-permiso ---------- */
  $tbl.on("click",".btn-eliminar",function(){
    const $btn = $(this), id = $btn.data("rp-id");
    if(!id || !confirm("¿Quitar este permiso del rol?")) return;

    $.post(eliminarRolPermUrl.replace("0", id),{
      csrfmiddlewaretoken:$("input[name=csrfmiddlewaretoken]").val()
    }).done(res=>{
      if(res.success && dt){ 
        dt.row($btn.closest("tr")).remove().draw(false); 
      }
      showAlert(res.success, res.message || (res.success ? "Eliminado" : "Error"));
    }).fail(()=> showAlert(false,"Error al eliminar el permiso."));
  });

  /* ---------- autocomplete de rol (SWR) ---------- */
  const $inp  = $("#id_rol_autocomplete"),
        $hid  = $("#id_rol"),
        $box  = $("#rol-autocomplete-results"),
        $form = $("#rolForm"),
        $editWrap = $("#edit-btn-wrap"),
        $editBtn  = $("#edit-btn");

  let page = 1, term = "", loading = false, more = true, reqId=0, xhr=null;
  let basePage=[], baseHasMore=true;
  const cache = Object.create(null);

  const openBox  = ()=>{ $box.show(); };
  const closeBox = ()=>{ $box.hide(); };
  const clearBox = ()=>{ $box.empty(); };

  function paint(list, replace=true){
    window.requestAnimationFrame(()=>{
      if(replace) $box.empty();
      const frag = document.createDocumentFragment();
      list.forEach(r=>{
        const d = document.createElement("div");
        d.className="autocomplete-option";
        d.dataset.id=r.id; d.textContent=r.text;
        frag.appendChild(d);
      });
      $box[0].appendChild(frag);
      openBox();
    });
  }

  function fetchPage(q, p, {replace=true} = {}){
    const key = `${q}::${p}`;
    // pinta caché al toque
    if(cache[key]){
      const data = cache[key];
      paint(data.results || [], replace);
      more = !!data.has_more;
    }
    if(xhr && xhr.readyState !== 4){ try{ xhr.abort(); }catch(e){} }
    const myReq = ++reqId; loading = true;

    xhr = $.getJSON(`${rolAutocompleteUrl}?term=${encodeURIComponent(q)}&page=${p}`)
      .done(data=>{
        cache[key] = data || {results:[], has_more:false};
        // evita race conditions
        if(myReq !== reqId || q !== term || p !== page) return;
        if(!replace && p>1){ paint(data.results || [], false); }
        else { clearBox(); paint(data.results || [], true); }
        more = !!data.has_more;
      })
      .always(()=>{ loading = false; });
  }

  function instantSearch(){
    const key = `${term}::1`; let painted=false;
    if(cache[key]?.results){
      clearBox(); paint(cache[key].results, true);
      more = !!cache[key].has_more; painted = true;
    }else if(basePage.length){
      const t = term.toLowerCase();
      const local = basePage.filter(r => r.text.toLowerCase().includes(t)).slice(0,50);
      clearBox(); paint(local, true);
      more = baseHasMore; painted = true;
    }
    page = 1; more = true;
    fetchPage(term, page, {replace:true});
    if(!painted) openBox();
  }

  $inp.on("input", ()=>{
    $hid.val("");
    term = $.trim($inp.val());
    if(!term){
      // ocultar botón si se borra el rol
      $editWrap.hide();
      $editBtn.attr("href", "#");

      page=1; more=baseHasMore;
      if(basePage.length){ clearBox(); paint(basePage, true); }
      fetchPage("", 1, {replace:true});
      return;
    }
    instantSearch();
  });

  $inp.on("focus", ()=>{
    term = $.trim($inp.val());
    page = 1; more = true;
    if(!term){
      if(basePage.length){ clearBox(); paint(basePage, true); }
      fetchPage("", 1, {replace:true});
    }else{
      instantSearch();
    }
  });

  $box.on("scroll", function(){
    if(this.scrollTop + this.clientHeight >= this.scrollHeight - 4 && more && !loading){
      page += 1; fetchPage(term, page, {replace:false});
    }
  });

  // click en una opción del autocomplete → setea ID, muestra botón, envía form
  $box.on("click", ".autocomplete-option", function(){
    const id  = $(this).data("id");
    const txt = $(this).text();

    $inp.val(txt);
    $hid.val(id);
    closeBox();

    // muestra y programa el botón
    $editBtn.attr("href", editarRolPermUrl.replace("0", id));
    $editWrap.show();

    $form.trigger("submit");
  });

  $(document).on("click", e=>{
    if(!$(e.target).closest("#id_rol_autocomplete, #rol-autocomplete-results").length) closeBox();
  });

  // Enter → toma primera opción
  $inp.on("keydown", e=>{
    if(e.key === "Enter"){
      e.preventDefault();
      const $first = $box.find(".autocomplete-option").first();
      if($first.length){
        const id  = $first.data("id");
        const txt = $first.text();

        $inp.val(txt);
        $hid.val(id);
        closeBox();

        // muestra y programa el botón
        $editBtn.attr("href", editarRolPermUrl.replace("0", id));
        $editWrap.show();

        $form.trigger("submit");
      }
    }
  });

  // Prefetch base (página 1 sin término)
  (function prefetchBase(){
    const key = `::1`;
    if(cache[key]){
      basePage    = cache[key].results || [];
      baseHasMore = !!cache[key].has_more;
      return;
    }
    $.getJSON(`${rolAutocompleteUrl}?term=&page=1`).done(data=>{
      cache[key] = data || {results:[], has_more:false};
      basePage    = cache[key].results || [];
      baseHasMore = !!cache[key].has_more;
    });
  })();
});
