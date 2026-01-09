/* visualizar_cambios.js */
$(function () {
  "use strict";

  // 1) Inicializa DataTable (con Show entries, paginación, etc.)
  const table = $("#cambiosTable").DataTable({
    paging:     true,
    searching:  true,
    responsive: true,
    language: {
      search:       "",
      zeroRecords:  "No se encontraron cambios",
      info:         "Mostrando _START_ a _END_ de _TOTAL_ registros",
      infoEmpty:    "Mostrando 0 a 0 de 0 registros",
      lengthMenu:   "Mostrar _MENU_ registros",
      paginate: {
        first:    "Primero",
        last:     "Último",
        next:     "Siguiente",
        previous: "Anterior"
      }
    }
  });

  // 2) Buscador externo
  $("#buscador-cambios").on("keyup", function () {
    table.search(this.value).draw();
  });

  // 3) Click en fila → navegar a ver_venta
  $("#cambiosTable tbody").on("click", "tr", function (e) {
    if ($(e.target).is("a")) return;  // si clicaste el enlace, no redirijas dos veces
    const id = $(this).data("id");
    if (id) window.location.href = verVentaUrl.replace("0", id);
  });
});
