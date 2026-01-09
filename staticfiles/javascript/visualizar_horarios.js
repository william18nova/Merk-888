/*  visualizar_horarios.js
    · DataTable
    · Autocomplete sucursal "ultra-rápido" (filtro local + fetch en paralelo)
    · ENTER selecciona SIEMPRE la 1ª opción y envía
    · Eliminación vía AJAX
*/
$(function () {
  "use strict";

  /* ---------- DataTable ---------- */
  const $tbl = $("#horariosTable");
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

  /* ---------- eliminar horario ---------- */
  $tbl.on("click",".btn-eliminar",function(){
    const $btn = $(this), id = $btn.data("h-id");
    if(!id || !confirm("¿Eliminar este horario?")) return;

    $.post(eliminarHorarioUrl.replace("0", id),{
      csrfmiddlewaretoken:$("input[name=csrfmiddlewaretoken]").val()
    }).done(res=>{
      if(res.success && dt){ dt.row($btn.closest("tr")).remove().draw(false); }
      flash(res.success,res.message);
    }).fail(()=> flash(false,"Error al eliminar el horario."));
  });

  /* ---------- Autocomplete Sucursal ultra-rápido ---------- */
  const $inp = $("#id_sucursal_autocomplete"),
        $hid = $("#id_sucursal"),
        $box = $("#sucursal-autocomplete-results");

  // estado + caché + control de concurrencia
  let page=1, more=true;
  let snapshot=[], fullSnapshot=[];         // lista actual y lista "vacía" (term="")
  const cache=new Map();                    // key -> {items, has_more}
  let version=0;                            // invalida respuestas viejas
  let currentXhr=null;

  const norm = s => (s||"").toString()
    .normalize("NFD").replace(/[\u0300-\u036f]/g,"")
    .toLowerCase().replace(/\s+/g," ").trim();

  const render = (items)=>{
    if(!items.length){
      $box.html('<div class="autocomplete-no-result">Sin resultados</div>').show();
      return;
    }
    $box.html(items.map(r=>`<div class="autocomplete-option" data-id="${r.id}">${r.text}</div>`).join("")).show();
  };

  // filtro instantáneo (prioriza "empieza por" y luego "contiene")
  const instantFilter = (term, base)=>{
    const t=norm(term);
    if(!t) return (base||[]).slice(0,40);
    const starts=[], contains=[];
    for(const r of base||[]){
      const n=norm(r.text);
      if(n.startsWith(t)) starts.push(r);
      else if(n.includes(t)) contains.push(r);
    }
    return starts.concat(contains).slice(0,40);
  };

  function fetchPage(term, pg, myVersion){
    const params=$.param({term,page:pg});
    const key=`${sucursalAutocompleteUrl}?${params}`;

    // cache hit
    if(cache.has(key)){
      const data=cache.get(key);
      const items=data.items;
      if(pg===1) snapshot = items.slice();
      else snapshot = snapshot.concat(items);
      if(term==="") { if(pg===1) fullSnapshot=items.slice(); else fullSnapshot=fullSnapshot.concat(items); }
      more = !!data.has_more;
      if(myVersion===version){
        const base = term==="" ? fullSnapshot : snapshot;
        render(instantFilter($inp.val(), base));
      }
      return $.Deferred().resolve().promise();
    }

    // abortar request anterior
    try{ currentXhr && currentXhr.abort(); }catch(_){}
    currentXhr = $.ajax({
      url: sucursalAutocompleteUrl,
      data: { term, page: pg },
      dataType: "json"
    }).done(data=>{
      const items = (data.results||[]).map(r=>({id:r.id,text:r.text}));
      cache.set(key,{items,has_more:!!data.has_more});
      if(pg===1) snapshot = items.slice();
      else snapshot = snapshot.concat(items);
      if(term==="") { if(pg===1) fullSnapshot=items.slice(); else fullSnapshot=fullSnapshot.concat(items); }
      more = !!data.has_more;
      if(myVersion===version){
        const base = term==="" ? fullSnapshot : snapshot;
        render(instantFilter($inp.val(), base));
      }
    }).always(()=>{ currentXhr=null; });

    return currentXhr;
  }

  // refresco casi instantáneo: pinta con filtro local y en paralelo trae del server
  const deb = (fn,ms=60)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
  const refresh = deb(()=>{
    const term = $.trim($inp.val());
    page=1; more=true; version++;

    // pinta al instante
    const base = term==="" ? fullSnapshot : snapshot;
    render(instantFilter(term, base));

    // pide red y repinta cuando llegue (si sigue vigente)
    fetchPage(term, 1, version);
  }, 50);

  // input: reacciona al escribir o borrar
  $inp.on("input", ()=>{
    $hid.val("");
    refresh();
  });

  // focus: si no hay datos, trae página 1; si hay, muestra al toque
  $inp.on("focus", ()=>{
    const term=$.trim($inp.val());
    if((term==="" && fullSnapshot.length) || (term!=="" && snapshot.length)){
      const base = term==="" ? fullSnapshot : snapshot;
      render(instantFilter(term, base));
    }else{
      page=1; more=true; version++; fetchPage(term,1,version);
    }
  });

  // scroll infinito
  $box.on("scroll", function(){
    if(!more || currentXhr) return;
    if(this.scrollTop + this.clientHeight >= this.scrollHeight - 6){
      page += 1; fetchPage($.trim($inp.val()), page, version);
    }
  });

  // click opción → set + submit
  function selectAndSubmit(id, text){
    $inp.val(text); $hid.val(id);
    $box.hide().empty();
    $("#sucursalForm").submit();
  }
  $box.on("click",".autocomplete-option",function(){
    selectAndSubmit($(this).data("id"), $(this).text());
  });

  // ENTER: seleccionar SIEMPRE la 1ª opción visible (o la 1ª del filtro local),
  // y si no hay datos aún, hace fetch rápido y selecciona al llegar.
  $inp.on("keydown", function(e){
    if(e.key!=="Enter") return;
    e.preventDefault();

    // 1) si hay opciones renderizadas, usa la 1ª
    const $firstDom = $box.find(".autocomplete-option").first();
    if($firstDom.length){
      return selectAndSubmit($firstDom.data("id"), $firstDom.text());
    }

    // 2) intenta con el filtro local (snapshot / fullSnapshot)
    const term = $.trim($inp.val());
    const base = term==="" ? fullSnapshot : snapshot;
    const list = instantFilter(term, base);
    if(list.length){
      return selectAndSubmit(list[0].id, list[0].text);
    }

    // 3) si todavía no hay datos, fetch y luego selecciona
    const myV = ++version; page=1; more=true;
    fetchPage(term,1,myV).done(()=>{
      if(myV!==version) return; // respuesta vieja
      const base2 = term==="" ? fullSnapshot : snapshot;
      const list2 = instantFilter($.trim($inp.val()), base2);
      if(list2.length){
        selectAndSubmit(list2[0].id, list2[0].text);
      }
    });
  });

  // click fuera → cerrar
  $(document).on("click", e=>{
    if(!$(e.target).closest("#id_sucursal_autocomplete, #sucursal-autocomplete-results").length){
      $box.hide();
    }
  });
});
