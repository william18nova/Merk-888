/*  visualizar_roles.js
    ────────────────────────────────────────────────
    • DataTable responsive + castellano
    • Eliminación con confirmación vía formulario oculto
--------------------------------------------------*/
$(function () {
  "use strict";

  /* ───── DataTable ───── */
  const table = $("#rolesTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    columnDefs: [{ targets: "no-sort", orderable: false }],
    language  : {
      search      : "",
      zeroRecords : "No se encontraron roles",
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

  /* ───── Eliminar rol ───── */
  $("#rolesTable").on("click", ".btn.borrar", function (e) {
    e.preventDefault();
    const $btn  = $(this);
    const id    = $btn.data("id");
    const nombre= $btn.data("nombre");

    if (confirm(`¿Eliminar el rol «${nombre}»?`)) {
      $(`#eliminar-form-${id}`)[0].submit();
    }
  });
});
