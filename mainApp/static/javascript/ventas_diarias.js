// static/javascript/ventas_diarias.js
(function(){
  "use strict";

  const $ = sel => document.querySelector(sel);
  const money = v => new Intl.NumberFormat('es-CO',{
    style:'currency', currency:'COP', maximumFractionDigits:0
  }).format(v || 0);

  const flash = (ok,msg)=>{
    const box = $("#flash");
    box.className = "alert " + (ok ? "alert-success" : "alert-error");
    box.textContent = msg;
    box.style.display = "block";
    setTimeout(()=> box.style.display="none", 3000);
  };

  // Elements
  const sucInp  = $("#sucursal_ac"), sucHid = $("#sucursal_id"), sucBox = $("#sucursal_box");
  const ppInp   = $("#pp_ac"),       ppHid  = $("#pp_id"),       ppBox  = $("#pp_box");
  const fecha   = $("#fecha");
  const fechaH  = $("#fecha_hasta");                    // ✅ rango de fechas (opcional)
  const hDesde  = $("#hora_desde");
  const hHasta  = $("#hora_hasta");
  const modo    = $("#modo");
  const mVentas = $("#m-ventas"), mTotal = $("#m-total");

  // ---- AUTOCOMPLETE (simple + paginado + ítems "fijos" arriba) ----
  function setupAutocomplete(inp, hid, box, url, extraParamsFn, prependFn){
    let page=1, more=true, loading=false, term="", cache=Object.create(null), req=0;

    function renderPrepend(){
      if (!prependFn) return;
      const items = prependFn() || [];
      items.forEach(r => {
        const d = document.createElement("div");
        d.className = "ac-opt ac-opt-special";
        d.dataset.id = r.id;
        // dataset.label = lo que se pondrá en el input al seleccionar
        d.dataset.label = r.label || r.text || "";
        d.innerHTML = r.html || (r.text || "");
        box.appendChild(d);
      });
    }

    function render(list, replace=true){
      if(replace) {
        box.innerHTML="";
        renderPrepend();
      }
      const frag=document.createDocumentFragment();
      list.forEach(r=>{
        const d=document.createElement("div");
        d.className="ac-opt";
        d.dataset.id=r.id;
        d.textContent=r.text;
        frag.appendChild(d);
      });
      box.appendChild(frag);
      box.style.display="block";
    }

    function fetchPage(q, p, replace=true){
      const extra = extraParamsFn ? extraParamsFn() : {};
      const qs = new URLSearchParams({ term:q || "", page:String(p), ...extra }).toString();
      const key = qs;

      if(cache[key]){
        render(cache[key].results || [], replace);
        more = !!cache[key].has_more;
        return;
      }

      const my = ++req;
      loading = true;

      fetch(`${url}?${qs}`)
        .then(r=>r.json())
        .then(data=>{
          cache[key] = data || {results:[], has_more:false};
          if(my !== req) return;
          render((data.results || []), replace);
          more = !!data.has_more;
        })
        .catch(()=>{})
        .finally(()=> loading=false);
    }

    inp.addEventListener("input", ()=>{
      hid.value="";
      term = inp.value.trim();
      page=1; more=true;
      fetchPage(term, page, true);
    });

    inp.addEventListener("focus", ()=>{
      page=1; more=true;
      fetchPage(inp.value.trim(), page, true);
    });

    box.addEventListener("click", e=>{
      const opt=e.target.closest(".ac-opt"); if(!opt) return;
      // Para ítems "especiales" (ej. "Todos los puntos") usamos dataset.label como texto visible
      inp.value = opt.dataset.label || opt.textContent;
      hid.value = opt.dataset.id;
      box.style.display="none";
      inp.dispatchEvent(new CustomEvent("ac:selected"));
    });

    box.addEventListener("scroll", ()=>{
      if(box.scrollTop + box.clientHeight >= box.scrollHeight - 4 && more && !loading){
        page += 1;
        fetchPage(term, page, false);
      }
    });

    document.addEventListener("click", e=>{
      if(!inp.contains(e.target) && !box.contains(e.target)) box.style.display="none";
    });
  }

  // Sucursal
  setupAutocomplete(sucInp, sucHid, sucBox, sucUrl, null);

  // ✅ Punto de pago (depende de sucursal) + ítem "Todos los puntos" inyectado arriba
  setupAutocomplete(
    ppInp, ppHid, ppBox, ppUrl,
    ()=>({ sucursal_id: sucHid.value || "" }),
    // prependFn: solo muestra "Todos" si ya hay sucursal seleccionada
    ()=>{
      if (!sucHid.value) return [];
      return [{
        id: "ALL",
        label: "Todos los puntos de pago",
        html: '<i class="fa-solid fa-layer-group" style="margin-right:8px;color:#2a4f9a"></i>'
              + '<strong>Todos los puntos de pago</strong>'
              + '<small style="display:block;font-size:12px;color:#5a6b85;margin-top:2px">Suma las ventas de toda la sucursal</small>'
      }];
    }
  );

  // al elegir sucursal → habilita PP y limpia datos
  sucInp.addEventListener("ac:selected", ()=>{
    ppInp.disabled = false;
    ppInp.value = "";
    ppHid.value = "";
    mVentas.textContent = "—"; mTotal.textContent = "—";
  });

  // ---- Fetch stats ----
  function maybeFetch(){
    const sid = sucHid.value;
    const pid = ppHid.value;                              // numérico o "ALL"
    const f   = fecha.value;
    const fTo = (fechaH && fechaH.value) ? fechaH.value : "";
    const m   = (modo.value || "TOTAL");

    if(!(sid && pid && f)) return;

    // Validación cliente: fecha_hasta no puede ser menor que fecha
    if (fTo && fTo < f) {
      flash(false, "La 'Fecha hasta' no puede ser menor que la 'Fecha desde'.");
      return;
    }

    const qs = new URLSearchParams({
      sucursal_id: sid,
      puntopago_id: pid,                     // numérico o "ALL"
      fecha: f,
      fecha_hasta: fTo,                       // vacío = un solo día
      modo: m,
      // intervalo cerrado (si están vacías, backend ignora o completa)
      hora_desde: (hDesde?.value || ""),     // HH:MM o HH:MM:SS
      hora_hasta: (hHasta?.value || "")
    }).toString();

    fetch(`${statsUrl}?${qs}`)
      .then(r=>r.json())
      .then(data=>{
        if(!data.success){
          flash(false, data.error || "Error");
          return;
        }
        mVentas.textContent = String(data.num_ventas || 0);
        mTotal.textContent  = money(data.total_vendido || 0);
      })
      .catch(()=> flash(false,"Error de red"));
  }

  ppInp.addEventListener("ac:selected", maybeFetch);
  fecha.addEventListener("change", maybeFetch);
  if (fechaH) fechaH.addEventListener("change", maybeFetch);
  modo.addEventListener("change", maybeFetch);
  if(hDesde) hDesde.addEventListener("change", maybeFetch);
  if(hHasta) hHasta.addEventListener("change", maybeFetch);
})();
