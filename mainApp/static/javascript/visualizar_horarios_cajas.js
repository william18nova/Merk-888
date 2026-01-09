/* visualizar_horarios_cajas.js
   ─────────────────────────────────────────────────────────────
   · DataTable
   · Autocompletes “ultra-rápidos” (filtro local + fetch en paralelo)
   · Enter = seleccionar 1ª opción y enfocar el siguiente autocomplete
   · Infinite scroll + caché por término/parámetros
   · Eliminación AJAX
----------------------------------------------------------------*/
$(function(){
  "use strict";

  /* ───────────────── 1) DataTable ───────────────── */
  const $tbl = $("#horariosTable");
  let dt = null;
  if($tbl.length){
    dt = $tbl.DataTable({
      paging:true, searching:true, info:true, responsive:true,
      language:{
        search:"", zeroRecords:"Sin registros",
        info:"Mostrando _START_ a _END_ de _TOTAL_",
        paginate:{ first:"Prim.", last:"Últ.", next:"Sig.", previous:"Ant." }
      },
      columnDefs:[{ targets:"no-sort", orderable:false }]
    });
  }

  /* ───────────────── 2) Flash helper ─────────────── */
  const $flash = $("<div class='alert' style='display:none'></div>").insertAfter("h2");
  const flash  = (ok,msg)=> $flash
    .removeClass("alert-success alert-error")
    .addClass(ok?"alert-success":"alert-error")
    .text(msg).show();

  /* ─────────────── 3) Eliminar horario ───────────── */
  $tbl.on("click",".btn-eliminar",function(){
    const $btn = $(this), id = $btn.data("id");
    if(!id||!confirm("¿Eliminar este horario?")) return;
    const url = eliminarHorarioUrlPattern.replace("0",id);
    $.post(url,{
      csrfmiddlewaretoken:$("input[name=csrfmiddlewaretoken]").val()
    }).done(res=>{
      if(res.success && dt){
        dt.row($btn.closest("tr")).remove().draw(false);
      }
      flash(res.success, res.message);
    }).fail(()=> flash(false,"Error al eliminar."));
  });

  /* ───────────── 4) Autocomplete ultra-rápido ─────────────
     - Muestra coincidencias locales al teclear/borrar (instantáneo)
     - En paralelo hace fetch y actualiza resultados
     - Infinite scroll + caché por término y parámetros extra
     - Enter = elegir 1ª opción y saltar al siguiente input
  ---------------------------------------------------------------- */
  function setupAutocomplete($inp, $hid, $box, url, onSelect, filterParams, nextSelector){
    let page=1, more=true, loading=false;
    let snapshot = [];          // [{id,text}, ...] “foto” del último fetch
    const cache  = new Map();   // key -> {items, has_more, ts}

    // normalizador simple (acentos + espacios)
    const norm = s => (s||"").toString()
      .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
      .toLowerCase().replace(/\s+/g," ").trim();

    // Render de una lista (prioriza empieza-por, luego contiene)
    function renderList(list){
      if(!list.length){
        $box.html('<div class="autocomplete-no-result">Sin resultados</div>').show();
        return;
      }
      $box.html(list.map(r=>(
        `<div class="autocomplete-option" data-id="${r.id}">${r.text}</div>`
      )).join("")).show();
    }

    // Filtro instantáneo
    function instantFilter(term){
      const t = norm(term);
      if(!t) return snapshot.slice(0, 40);
      const starts=[], contains=[];
      for(const r of snapshot){
        const n = norm(r.text);
        if(n.startsWith(t)) starts.push(r);
        else if(n.includes(t)) contains.push(r);
      }
      return [...starts, ...contains].slice(0, 40);
    }

    // Fetch (con caché por term+params+page)
    async function fetchPage(term, pg){
      const params = { term, page: pg };
      if (filterParams) Object.assign(params, filterParams());
      const key = `${url}?${$.param(params)}`;

      if(cache.has(key)){
        const data = cache.get(key);
        if(pg===1) snapshot = data.items.slice();
        else snapshot = snapshot.concat(data.items);
        more = !!data.has_more;
        return data;
      }

      loading = true;
      try{
        const data = await $.getJSON(url, params);
        const items = (data.results||[]).map(r=>({id:r.id, text:r.text}));
        cache.set(key, { items, has_more: !!data.has_more, ts: Date.now() });
        if(pg===1) snapshot = items.slice();
        else snapshot = snapshot.concat(items);
        more = !!data.has_more;
        return { items, has_more: more };
      } finally { loading=false; }
    }

    const debounce = (fn,ms=60)=>{
      let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); };
    };

    // Refresco (1) pintar local, (2) fetch, (3) re-pintar con nuevos
    const refresh = async ()=>{
      const term = $.trim($inp.val());
      renderList(instantFilter(term));           // instantáneo
      page = 1; more = true;
      await fetchPage(term, 1);                  // red
      renderList(instantFilter($.trim($inp.val())));
    };
    const debRefresh = debounce(refresh, 50);

    // INPUT
    $inp.on("input", ()=>{
      $hid.val("");
      debRefresh();
    });

    // FOCUS
    $inp.on("focus", ()=>{
      const term = $.trim($inp.val());
      if(!snapshot.length){ fetchPage(term, 1).then(()=> renderList(instantFilter(term))); }
      else renderList(instantFilter(term));
    });

    // ENTER = tomar 1ª opción visible y pasar al siguiente
    $inp.on("keydown", function(e){
      if(e.key !== "Enter") return;
      const $first = $box.find(".autocomplete-option").first();
      if($first.length){
        e.preventDefault();
        $first.trigger("click");
        if(nextSelector){
          // pequeño delay para permitir onSelect limpiar/activar el siguiente
          setTimeout(()=>{
            const $n = $(nextSelector);
            $n.prop("disabled", false).focus().select?.();
          }, 0);
        }
      }
    });

    // SCROLL infinito
    $box.on("scroll", async function(){
      if(loading || !more) return;
      if(this.scrollTop + this.clientHeight >= this.scrollHeight - 6){
        page += 1;
        await fetchPage($.trim($inp.val()), page);
        renderList(instantFilter($.trim($inp.val())));
      }
    });

    // CLICK selección
    $box.on("click", ".autocomplete-option", function(){
      const $opt = $(this);
      $inp.val($opt.text());
      $hid.val($opt.data("id"));
      $box.hide().empty();
      if(onSelect) onSelect($opt.data("id"));
    });

    // Cerrar al hacer click fuera
    $(document).on("click", e=>{
      if(!$(e.target).closest($inp).length && !$(e.target).closest($box).length){
        $box.hide();
      }
    });

    // Reset público
    return {
      reset(){
        page=1; more=true; loading=false;
        snapshot=[]; cache.clear();
        $hid.val(""); $inp.val("");
        $box.hide().empty();
      }
    };
  }

  /* ─────────────── 4.1) Sucursal ─────────────── */
  const sucAC = setupAutocomplete(
    $("#id_sucursal_autocomplete"),
    $("#id_sucursal"),
    $("#sucursal-autocomplete-results"),
    sucursalAutocompleteUrl,
    id=>{
      // al elegir sucursal: limpiar y habilitar Punto de Pago
      const $ppInp = $("#id_puntopago_autocomplete");
      $("#id_puntopago").val("");
      $ppInp.val("").prop("disabled",false);
      $("#puntopago-autocomplete-results").hide().empty();
      // reset del AC de Punto de Pago para forzar params nuevos
      ppAC.reset();
    },
    null,
    "#id_puntopago_autocomplete"             // ⬅️ siguiente autocomplete
  );

  /* ─────────────── 4.2) Punto de Pago ─────────── */
  const ppAC = setupAutocomplete(
    $("#id_puntopago_autocomplete"),
    $("#id_punto_pago"),
    $("#puntopago-autocomplete-results"),
    puntopagoAutocompleteUrl,
    // al elegir → enviar filtros
    () => { $("#filtrosForm").submit(); },
    // params dinámicos
    ()=>({ sucursal_id: $("#id_sucursal").val() }),
    null // no hay siguiente; al seleccionar se envía el filtro
  );

});
