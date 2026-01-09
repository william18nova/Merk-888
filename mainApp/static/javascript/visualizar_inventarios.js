/*  visualizar_inventarios.js
    – DataTable intact
    – Autocomplete Sucursal instantáneo (filtro local + fetch rápido)
    – Enter selecciona primera opción
    – La selección se mantiene visible y persiste tras recarga
------------------------------------------------------------------*/
$(function () {
  "use strict";

  /* 1) DataTable ------------------------------------------------- */
  const $tblEl = $("#inventariosTable, #inventarios-list");
  const tbl = $tblEl.length ? $tblEl.DataTable({
    paging: true,
    searching: true,
    info: true,
    responsive: true,
    language: {
      search       : "",
      zeroRecords  : "No se encontraron registros",
      info         : "Mostrando _START_ a _END_ de _TOTAL_",
      infoEmpty    : "Mostrando 0 a 0 de 0",
      paginate     : { first:"Primero", last:"Último", next:"Sig.", previous:"Ant." }
    }
  }) : null;

  /* 2) Helpers --------------------------------------------------- */
  const csrf  = $("input[name=csrfmiddlewaretoken]").val();
  const $msg  = $("<div class='alert' style='display:none'></div>").insertAfter("h2");

  const flash = (ok, txt) => $msg
    .removeClass("alert-success alert-error")
    .addClass(ok ? "alert-success" : "alert-error")
    .text(txt)
    .show();

  const debounce = (fn, ms=90) => { let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms);} };

  /* 3) Eliminar -------------------------------------------------- */
  $("#inventariosTable, #inventarios-list").on("click", ".btn-eliminar", function (e) {
    e.preventDefault();
    const $btn = $(this);
    const id   = $btn.data("inventario-id");
    if (!id || !confirm("¿Eliminar este producto del inventario?")) return;

    $.post(eliminarInventarioUrl.replace("0", id),
           { csrfmiddlewaretoken: csrf })
     .done(r => {
        if (r.success && tbl) { tbl.row($btn.closest("tr")).remove().draw(false); }
        flash(r.success, r.message);
     })
     .fail(() => flash(false, "Error al eliminar el registro."));
  });

  /* 4) Autocomplete Sucursal — instantáneo ---------------------- */
  const $inp = $("#id_sucursal_autocomplete");
  const $hid = $("#id_sucursal");
  const $box = $("#sucursal-autocomplete-results");

  const state = {
    term   : "",
    page   : 1,
    more   : true,
    loading: false,
    lastList: [],
    req    : null,
    suppressNextInput: false   // 👈 evita borrar al setear por código
  };
  const cache = Object.create(null);
  const keyFor = (t, p) => `${(t||"").trim().toLowerCase()}|${p||1}`;

  // Restaura selección si venimos de un submit
  (function restoreSelection(){
    const savedLabel = sessionStorage.getItem("suc.autocomplete.label");
    const savedId    = sessionStorage.getItem("suc.autocomplete.id");
    if (savedLabel) {
      $inp.val(savedLabel);
      if (!$hid.val()) $hid.val(savedId || "");
      sessionStorage.removeItem("suc.autocomplete.label");
      sessionStorage.removeItem("suc.autocomplete.id");
    }
  })();

  const renderList = (items) => {
    $box.empty();
    if (!items || !items.length) {
      $box.html('<div class="autocomplete-no-result">Sin resultados</div>');
    } else {
      const frag = document.createDocumentFragment();
      items.forEach(r => {
        const div = document.createElement("div");
        div.className = "autocomplete-option";
        div.dataset.id = r.id;
        div.textContent = r.text;
        frag.appendChild(div);
      });
      $box[0].appendChild(frag);
    }
    if (document.activeElement === $inp[0]) $box.css("display","block");
  };

  const filterLocal = () => {
    const term = state.term.trim().toLowerCase();
    if (!term) {
      if (state.lastList.length) renderList(state.lastList);
      else $box.hide();
      return;
    }
    if (!state.lastList.length) return;
    const filtered = state.lastList.filter(r => r.text.toLowerCase().includes(term));
    renderList(filtered);
  };

  const fetchPage = (page=1) => {
    const term = state.term;
    const cacheKey = keyFor(term, page);

    if (cache[cacheKey]) {
      const data = cache[cacheKey];
      if (page === 1) state.lastList = data.results.slice(0);
      renderList(data.results);
      state.more = !!data.has_more;
    }

    if (state.loading) { try { state.req?.abort(); } catch(_e){} }

    state.loading = true;
    state.req = $.getJSON(sucursalAutocompleteUrl, { term, page })
      .done(data => {
        cache[cacheKey] = data;
        if (term !== state.term) return;
        if (page === 1) state.lastList = data.results.slice(0);
        renderList(data.results);
        state.more = !!data.has_more;
      })
      .always(() => { state.loading = false; });
  };

  const kickFetch = debounce(() => { state.page = 1; state.more = true; fetchPage(1); }, 90);

  // Seleccionar y persistir
  function applySelection(label, id){
    state.suppressNextInput = true;      // 👈 evita limpiar en el siguiente "input"
    $inp.val(label);
    $hid.val(id);
    $box.hide();

    // Persistimos para que tras recarga quede visible
    sessionStorage.setItem("suc.autocomplete.label", label);
    sessionStorage.setItem("suc.autocomplete.id", String(id));

    $("#sucursalForm").trigger("submit");
  }

  /* === Eventos autocomplete === */
  $inp
    .on("input", () => {
      if (state.suppressNextInput) { state.suppressNextInput = false; return; } // no limpiar si fue programático
      $hid.val("");
      state.term = $.trim($inp.val());
      filterLocal();
      kickFetch();
    })
    .on("focus", () => {
      state.term = $.trim($inp.val());
      if (state.lastList.length) renderList(state.lastList);
      else $box.show();
      state.page = 1; state.more = true;
      fetchPage(1);
    })
    .on("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const $first = $box.find(".autocomplete-option").first();
        if ($first.length) {
          applySelection($first.text(), $first.data("id"));
        }
      }
    });

  $box.on("scroll", () => {
    if ($box.scrollTop() + $box.innerHeight() >= $box[0].scrollHeight - 4) {
      if (state.more && !state.loading) {
        state.page += 1;
        fetchPage(state.page);
      }
    }
  });

  $box.on("click", ".autocomplete-option", function () {
    applySelection($(this).text(), $(this).data("id"));
  });

  // Cierra si se hace click fuera
  $(document).on("click", (e) => {
    if (!$(e.target).closest("#id_sucursal_autocomplete, #sucursal-autocomplete-results").length) {
      $box.hide();
    }
  });

  // Cierre suave en blur (permite click en opciones)
  $inp.on("blur", () => {
    setTimeout(() => { if (!$("#sucursal-autocomplete-results:hover").length) $box.hide(); }, 120);
  });
});
