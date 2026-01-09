/*  visualizar_usuarios.js  */
$(function () {
  "use strict";

  /* ───────── DataTable + buscador externo ───────── */
  const table = $("#usuariosTable").DataTable({
    paging     : true,
    searching  : true,
    info       : true,
    responsive : true,
    columnDefs : [{ targets: "no-sort", orderable: false }],
    language   : {
      search      : "",                          // ocultamos barra nativa
      zeroRecords : "No se encontraron usuarios",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ usuarios",
      infoEmpty   : "Mostrando 0 a 0 de 0 usuarios",
      paginate    : {
        first   : "Primero",
        last    : "Último",
        next    : "Siguiente",
        previous: "Anterior"
      }
    }
  });

  $("#buscador-usuarios").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───────── eliminar usuario ───────── */
  $("#usuariosTable").on("click", ".btn.borrar", function () {
    const $btn   = $(this);
    const nombre = $btn.data("nombre");
    const $form  = $btn.closest("td").find(".delete-form");

    if (confirm(`¿Desea eliminar el usuario «${nombre}»?`)) {
      $form.submit();
    }
  });

  /* ───────── flash-message tras ADD/EDIT ───────── */
  const flash = sessionStorage.getItem("flash-usuario");
  if (flash) {
    $(".container h2").after(`
      <div class="messages">
        <div class="alert alert-success">${flash}</div>
      </div>
    `);
    sessionStorage.removeItem("flash-usuario");
  }
});
