/*  visualizar_clientes.js
    ───────────────────────────────────────────────────────
    · DataTable responsive, castellano
    · Eliminación con confirmación
--------------------------------------------------------*/
$(function () {
  "use strict";

  /* ═════ DataTable ═════ */
  const table = $("#clientesTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    columnDefs: [{ targets: "no-sort", orderable: false }],
    language  : {
      search      : "",
      zeroRecords : "No se encontraron clientes",
      info        : "Mostrando _START_ a _END_ de _TOTAL_",
      infoEmpty   : "Mostrando 0 a 0 de 0",
      paginate    : {
        first   : "Primero",
        last    : "Último",
        next    : "Siguiente",
        previous: "Anterior"
      }
    }
  });

  /* ═════ eliminar ═════ */
  $("#clientesTable").on("click", ".btn.borrar", function (e) {
    e.preventDefault();
    const $btn    = $(this);
    const id      = $btn.data("id");
    const nombre  = $btn.data("nombre");

    if (confirm(`¿Eliminar al cliente «${nombre}»?`)) {
      document.getElementById(`eliminar-form-${id}`).submit();
    }
  });
});
