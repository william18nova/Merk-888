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

  $("#buscador-ventas").on("input", function () { table.search(this.value).draw(); });
  $("#ventasTable_filter").hide();

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
    table.ajax.reload();
  }

  function setProductoFiltroByTerm(term) {
    productoFiltro.id = "";
    productoFiltro.term = String(term || "").trim();
    $hidProd.val("");
    applyProductoChip(productoFiltro.term ? `Producto: "${productoFiltro.term}"` : "");
    table.ajax.reload();
  }

  function clearProductoFiltro() {
    productoFiltro.id = "";
    productoFiltro.term = "";
    $hidProd.val("");
    $inpProd.val("");
    applyProductoChip("");
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
});
