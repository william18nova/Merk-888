/*  static/javascript/visualizar_empleados.js  */
$(function () {
  "use strict";

  /* ───────── DataTable + buscador externo ───────── */
  const table = $("#empleadosTable").DataTable({
    paging     : true,
    searching  : true,
    info       : true,
    responsive : true,
    columnDefs : [{ targets: "no-sort", orderable: false }],
    language   : {
      search      : "",                                  // ocultamos barra nativa
      zeroRecords : "No se encontraron empleados",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ empleados",
      infoEmpty   : "Mostrando 0 a 0 de 0 empleados",
      paginate    : {
        first   : "Primero",
        last    : "Último",
        next    : "Siguiente",
        previous: "Anterior"
      }
    }
  });

  $("#buscador-empleados").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───────── eliminar empleado ───────── */
  $("#empleadosTable").on("click", ".btn.borrar", function () {
    const $btn   = $(this);
    const nombre = $btn.data("nombre");
    const $form  = $btn.closest("td").find(".delete-form");

    if (confirm(`¿Desea eliminar al empleado «${nombre}»?`)) {
      $form.submit();
    }
  });

  /* ───────── flash-message tras ADD/EDIT ───────── */
  const flash = sessionStorage.getItem("flash-empleado");
  if (flash) {
    $(".container h2").after(`
      <div class="messages">
        <div class="alert alert-success">${flash}</div>
      </div>
    `);
    sessionStorage.removeItem("flash-empleado");
  }
});
