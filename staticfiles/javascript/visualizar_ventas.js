/* visualizar_ventas.js */
$(function () {
  "use strict";

  // Inicializa DataTable con buscador externo
  const table = $("#ventasTable").DataTable({
    paging:     true,
    searching:  true,
    info:       true,
    responsive: true,
    language: {
      search:       "",
      zeroRecords:  "No se encontraron ventas",
      info:         "Mostrando _START_ a _END_ de _TOTAL_ ventas",
      infoEmpty:    "Mostrando 0 a 0 de 0 ventas",
      paginate: {
        first:    "Primero",
        last:     "Último",
        next:     "Siguiente",
        previous: "Anterior"
      }
    }
  });

  // Vincula buscador externo
  $("#buscador-ventas").on("keyup", function () {
    table.search(this.value).draw();
  });

  // Navegar al detalle al hacer click en la fila
  $("#ventasTable tbody").on("click", "tr.clickable-row", function () {
    const id = $(this).data("id");
    if (id) {
      window.location.href = verVentaUrl.replace("0", id);
    }
  });
});
