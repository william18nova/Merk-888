(function(){
  "use strict";

  // ---------- helpers UI ----------
  const $ = (s,root=document)=>root.querySelector(s);
  const $$= (s,root=document)=>Array.from(root.querySelectorAll(s));

  const flashOK  = $("#flash-ok");
  const flashERR = $("#flash-err");
  const showOK = (m)=>{ flashOK.textContent=m; flashOK.style.display="block"; flashERR.style.display="none"; };
  const showERR= (m)=>{ flashERR.textContent=m; flashERR.style.display="block"; flashOK.style.display="none"; };
  const clearFlash= ()=>{ flashOK.style.display="none"; flashERR.style.display="none"; };

  const kpiCantidad=$("#kpiCantidad"), kpiTotal=$("#kpiTotal");
  const fmt = n => new Intl.NumberFormat("es-CO",{style:"currency",currency:"COP",maximumFractionDigits:0}).format(parseFloat(n||0));

  // ---------- DOM ----------
  const acSucInp  = $("#ac_sucursal"),
        acSucBox  = $("#box_sucursal"),
        sucHid    = $("#sucursal_id");

  const acPPInp   = $("#ac_puntopago"),
        acPPBox   = $("#box_puntopago"),
        ppHid     = $("#puntopago_id"),
        btnClearPP= $("#clear_pp");

  const fechaInp  = $("#fecha");
  const btnConsultar = $("#btnConsultar");

  // ---------- Autocomplete base ----------
  function AC({inp, box, url, extraParams=()=>({})}){
    const state = {term:"", page:1, more:true, loading:false, reqId:0, cache:Object.create(null)};
    function render(list, replace=true){
      requestAnimationFrame(()=>{
        if(replace) box.innerHTML="";
        const frag=document.createDocumentFragment();
        (list||[]).forEach(r=>{
          const d=document.createElement("div");
          d.className="ac-option"; d.dataset.id=r.id; d.textContent=r.text;
          frag.appendChild(d);
        });
        box.appendChild(frag); box.style.display="block";
      });
    }
    function fetchPage(q,p,{replace=true}={}){
      const params = new URLSearchParams({ term:q, page:String(p), ...extraParams() });
      const key = params.toString();
      if(state.cache[key]){
        render(state.cache[key].results, replace);
        state.more = !!state.cache[key].has_more;
      }
      const myReq = ++state.reqId; state.loading=true;
      fetch(`${url}?${params}`)
        .then(r=>r.json())
        .then(data=>{
          state.cache[key]=data||{results:[],has_more:false};
          if(myReq!==state.reqId) return;
          render(data.results, replace);
          state.more = !!data.has_more;
        })
        .finally(()=> state.loading=false);
    }
    function instant(){
      state.page=1; state.more=true;
      fetchPage(state.term, state.page, {replace:true});
      box.style.display="block";
    }
    inp.addEventListener("input", ()=>{
      const v=inp.value.trim();
      state.term=v;
      instant();
    });
    inp.addEventListener("focus", ()=>{
      if(!box.innerHTML) instant(); else box.style.display="block";
    });
    box.addEventListener("scroll", ()=>{
      if(box.scrollTop + box.clientHeight >= box.scrollHeight-4 &&
         state.more && !state.loading){
        state.page+=1; fetchPage(state.term, state.page, {replace:false});
      }
    });
    document.addEventListener("click",(e)=>{
      if(!inp.contains(e.target) && !box.contains(e.target)) box.style.display="none";
    });
    return { fetchPage, box };
  }

  // --- AC Sucursal ---
  const acSuc = AC({
    inp: acSucInp, box: acSucBox, url: urlAcSucursal,
    extraParams: ()=>({ fecha: fechaInp.value || "" })
  });
  acSucBox.addEventListener("click", (e)=>{
    const opt = e.target.closest(".ac-option"); if(!opt) return;
    acSucInp.value=opt.textContent; sucHid.value=opt.dataset.id;
    acSucBox.style.display="none";
    // habilitar AC punto de pago
    acPPInp.disabled=false; btnClearPP.disabled=false;
    // limpiar PP seleccionado
    acPPInp.value=""; ppHid.value="";
    acPP.box.innerHTML="";
  });

  // --- AC Punto de pago ---
  const acPP = AC({
    inp: acPPInp, box: acPPBox, url: urlAcPuntoPago,
    extraParams: ()=>({ sucursal_id: sucHid.value || "", fecha: fechaInp.value || "" })
  });
  acPPBox.addEventListener("click", (e)=>{
    const opt = e.target.closest(".ac-option"); if(!opt) return;
    acPPInp.value=opt.textContent; ppHid.value=opt.dataset.id;
    acPPBox.style.display="none";
  });
  btnClearPP.addEventListener("click", ()=>{
    acPPInp.value=""; ppHid.value="";
    acPP.box.innerHTML="";
  });

  // --- Consultar ---
  btnConsultar.addEventListener("click", ()=>{
    clearFlash();
    if(!sucHid.value){
      showERR("Selecciona una sucursal.");
      return;
    }
    const fd = new FormData();
    fd.append("csrfmiddlewaretoken", CSRF_TOKEN);
    fd.append("sucursal_id", sucHid.value);
    if(ppHid.value) fd.append("puntopago_id", ppHid.value);
    if(fechaInp.value) fd.append("fecha", fechaInp.value);

    fetch(urlResumen, { method:"POST", body:fd })
      .then(r=>r.json())
      .then(data=>{
        if(!data.success){ showERR(data.message || "Sin datos."); return; }
        kpiCantidad.textContent = data.cantidad;
        kpiTotal.textContent    = fmt(data.total);
        showOK("Resumen actualizado.");
      })
      .catch(()=> showERR("Error consultando el resumen."));
  });

})();
