/* visualizar_ventas.js */
$(function () {
  "use strict";

  // ✅ solo visual: mapa de slugs -> etiqueta bonita
  const MP_LABELS = {
    "banco_caja_social": "Banco Caja Social",
    "Banco_Caja_Social": "Banco Caja Social",  // por si llega así
    "BANCO_CAJA_SOCIAL": "Banco Caja Social",
  };

  function prettyMedioPago(v){
    const s = (v ?? "").toString().trim();
    if (!s) return "—";
    // 1) si está en el mapa, úsalo
    if (MP_LABELS[s]) return MP_LABELS[s];
    // 2) fallback: reemplaza _ por espacio y Title Case
    return s
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase());
  }

  // ✅ Estado del filtro por producto (id seleccionado o término libre)
  const productoFiltro = { id: "", term: "" };
  const advancedFilterSelector = [
    "#filtro-fecha-desde",
    "#filtro-fecha-hasta",
    "#filtro-hora-desde",
    "#filtro-hora-hasta",
    "#filtro-puntopago",
    "#filtro-mediopago",
    "#filtro-empleado",
    "#filtro-cliente",
    "#filtro-total-min",
    "#filtro-total-max",
    "#filtro-venta-id",
    "#filtro-devoluciones"
  ].join(",");

  function debounce(fn, wait){
    let timer = null;
    return function (...args){
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function getAdvancedFilters(){
    return {
      fecha_desde: $("#filtro-fecha-desde").val() || "",
      fecha_hasta: $("#filtro-fecha-hasta").val() || "",
      hora_desde: $("#filtro-hora-desde").val() || "",
      hora_hasta: $("#filtro-hora-hasta").val() || "",
      sucursal_id: $("#filtro-sucursal").val() || "",
      puntopago_id: $("#filtro-puntopago").val() || "",
      mediopago: $("#filtro-mediopago").val() || "",
      empleado_id: $("#filtro-empleado").val() || "",
      cliente_term: ($("#filtro-cliente").val() || "").trim(),
      total_min: $("#filtro-total-min").val() || "",
      total_max: $("#filtro-total-max").val() || "",
      venta_id: $("#filtro-venta-id").val() || "",
      devoluciones: $("#filtro-devoluciones").val() || ""
    };
  }

  function filterPuntosPagoBySucursal(){
    const sucursalId = $("#filtro-sucursal").val() || "";
    const $pp = $("#filtro-puntopago");
    const current = $pp.val();
    let currentStillVisible = !current;

    $pp.find("option").each(function (){
      const $opt = $(this);
      const optSucursal = String($opt.data("sucursal") || "");
      const visible = !$opt.val() || !sucursalId || optSucursal === String(sucursalId);
      $opt.prop("hidden", !visible).prop("disabled", !visible);
      if (visible && $opt.val() === current) currentStillVisible = true;
    });

    if (!currentStillVisible) $pp.val("");
  }

  function updateFilterSummary(){
    let active = 0;
    Object.values(getAdvancedFilters()).forEach(value => {
      if (String(value || "").trim()) active += 1;
    });
    if (productoFiltro.id || productoFiltro.term) active += 1;
    if (($("#buscador-ventas").val() || "").trim()) active += 1;

    $("#ventas-filters-summary").text(
      active ? `${active} filtro${active === 1 ? "" : "s"} activo${active === 1 ? "" : "s"}` : "Sin filtros activos"
    );
  }

  const table = $("#ventasTable").DataTable({
    processing : true,
    serverSide : true,
    ajax       : {
      url: ventasDataUrl,
      type: "GET",
      data: function (d) {
        // Pasar filtro de producto al backend en cada request
        if (productoFiltro.id) {
          d.producto_id = productoFiltro.id;
        } else if (productoFiltro.term) {
          d.producto_term = productoFiltro.term;
        }
        Object.assign(d, getAdvancedFilters());
      }
    },

    columns: [
      { data: "ventaid" },
      { data: "fecha" },
      { data: "hora" },
      { data: "cliente" },
      { data: "empleado" },
      { data: "sucursal" },
      { data: "puntopago" },
      { data: "total" },

      // ✅ aquí se arregla SOLO VISUALMENTE
      {
        data: "mediopago",
        render: function (data, type) {
          // para ordenar/buscar, usa el valor original
          if (type === "sort" || type === "type") return (data ?? "");
          // para mostrar/filtrar, usa el bonito
          return prettyMedioPago(data);
        }
      }
    ],

    paging      : true,
    pageLength  : 25,
    lengthMenu  : [[10, 25, 50, 100, -1], [10, 25, 50, 100, "Todos"]],
    deferRender : true,
    searching   : true,
    info        : true,
    responsive  : true,
    searchDelay : 250,
    order       : [[0, "desc"]],
    language    : {
      search      : "",
      zeroRecords : "No se encontraron ventas",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ ventas",
      infoEmpty   : "Mostrando 0 a 0 de 0 ventas",
      lengthMenu  : "Mostrar _MENU_ ventas",
      paginate    : { first:"Primero", last:"Último", next:"Siguiente", previous:"Anterior" },
      processing  : "Cargando..."
    },
    stateSave: true,

    createdRow: function (row, data) {
      const labels = ["ID","Fecha","Hora","Cliente","Empleado","Sucursal","Punto Pago","Total","Medio Pago"];
      $(row).addClass("clickable-row").attr("data-id", data.ventaid);
      $(row).find("td").each(function (i) { $(this).attr("data-label", labels[i]); });
    }
  });

  $("#buscador-ventas").on("input", function () {
    table.search(this.value).draw();
    updateFilterSummary();
  });
  $("#ventasTable_filter").hide();

  const reloadWithFilters = debounce(function () {
    updateFilterSummary();
    table.ajax.reload();
  }, 250);

  $("#ventas-filters-toggle").on("click", function () {
    const $filters = $(".ventas-filters");
    const isOpen = !$filters.hasClass("is-open");
    $filters.toggleClass("is-open", isOpen);
    $(this).attr("aria-expanded", String(isOpen));
  });

  $(advancedFilterSelector).on("input change", reloadWithFilters);
  $("#filtro-sucursal").on("change", function () {
    filterPuntosPagoBySucursal();
    reloadWithFilters();
  });

  $(".ventas-chip-btn[data-range]").on("click", function () {
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");

    if ($(this).data("range") === "today") {
      const today = `${yyyy}-${mm}-${dd}`;
      $("#filtro-fecha-desde").val(today);
      $("#filtro-fecha-hasta").val(today);
    } else {
      const first = `${yyyy}-${mm}-01`;
      const lastDate = new Date(yyyy, now.getMonth() + 1, 0).getDate();
      $("#filtro-fecha-desde").val(first);
      $("#filtro-fecha-hasta").val(`${yyyy}-${mm}-${String(lastDate).padStart(2, "0")}`);
    }
    updateFilterSummary();
    table.ajax.reload();
  });

  $("#ventasTable tbody").on("click", "tr.clickable-row", function () {
    const id = $(this).data("id");
    if (id) window.location.href = verVentaUrl.replace("0", id);
  });

  /* =========================
     ✅ Filtro por producto
     ========================= */
  const $inpProd  = $("#filtro-producto");
  const $hidProd  = $("#filtro-producto-id");
  const $clearBtn = $("#filtro-producto-clear");
  const $chip     = $("#filtro-producto-chip");

  function applyProductoChip(text) {
    if (text) {
      $chip.text(text).show();
      $clearBtn.show();
    } else {
      $chip.text("").hide();
      $clearBtn.hide();
    }
  }

  function setProductoFiltroById(id, label) {
    productoFiltro.id = String(id || "");
    productoFiltro.term = "";
    $hidProd.val(productoFiltro.id);
    applyProductoChip(label ? `Producto: ${label}` : `Producto #${id}`);
    updateFilterSummary();
    table.ajax.reload();
  }

  function setProductoFiltroByTerm(term) {
    productoFiltro.id = "";
    productoFiltro.term = String(term || "").trim();
    $hidProd.val("");
    applyProductoChip(productoFiltro.term ? `Producto: "${productoFiltro.term}"` : "");
    updateFilterSummary();
    table.ajax.reload();
  }

  function clearProductoFiltro() {
    productoFiltro.id = "";
    productoFiltro.term = "";
    $hidProd.val("");
    $inpProd.val("");
    applyProductoChip("");
    updateFilterSummary();
    table.ajax.reload();
  }

  if (typeof productoAutocompleteUrl === "string" && productoAutocompleteUrl) {
    $inpProd.autocomplete({
      minLength: 1,
      delay: 180,
      source: function (request, response) {
        $.ajax({
          url: productoAutocompleteUrl,
          dataType: "json",
          data: { term: request.term, limit: 15 },
          success: function (data) {
            const items = (data.results || []).map(p => {
              const code = p.barcode ? ` · ${p.barcode}` : "";
              return {
                label: `#${p.id} — ${p.text}${code}`,
                value: p.text,
                id: p.id,
                name: p.text,
                barcode: p.barcode || ""
              };
            });
            response(items);
          },
          error: function () { response([]); }
        });
      },
      select: function (event, ui) {
        event.preventDefault();
        $inpProd.val(ui.item.name);
        setProductoFiltroById(ui.item.id, ui.item.name);
        return false;
      },
      focus: function (event) { event.preventDefault(); }
    });
  }

  // Enter en el input: si no se eligió del menú, filtrar por término libre
  $inpProd.on("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = ($inpProd.val() || "").trim();
      if (!v) { clearProductoFiltro(); return; }
      // si el id ya estaba fijado y el texto coincide, no hacer nada
      if (productoFiltro.id && v === ($chip.text().replace(/^Producto:\s*/, ""))) return;
      setProductoFiltroByTerm(v);
    }
  });

  // Si el usuario borra todo el input manualmente, limpiar filtro
  $inpProd.on("input", function () {
    if (!($inpProd.val() || "").trim() && (productoFiltro.id || productoFiltro.term)) {
      clearProductoFiltro();
    }
  });

  $clearBtn.on("click", clearProductoFiltro);

  $("#limpiar-filtros-ventas").on("click", function () {
    $(advancedFilterSelector).val("");
    $("#filtro-sucursal").val("");
    $("#filtro-puntopago").val("");
    filterPuntosPagoBySucursal();

    productoFiltro.id = "";
    productoFiltro.term = "";
    $hidProd.val("");
    $inpProd.val("");
    applyProductoChip("");

    table.search("");
    $("#buscador-ventas").val("");
    updateFilterSummary();
    table.ajax.reload();
  });

  filterPuntosPagoBySucursal();
  updateFilterSummary();
});
