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

  const table = $("#ventasTable").DataTable({
    processing : true,
    serverSide : true,
    ajax       : { url: ventasDataUrl, type: "GET" },

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
});
