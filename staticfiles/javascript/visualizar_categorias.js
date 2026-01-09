/* static/javascript/visualizar_categorias.js */
$(function () {
  "use strict";

  /* ───── Configurar DataTable + buscador externo ───── */
  const table = $("#categoriasTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    columnDefs: [{ targets: "no-sort", orderable: false }],
    language  : {
      search      : "",
      zeroRecords : "No se encontraron categorías",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ categorías",
      infoEmpty   : "Mostrando 0 a 0 de 0 categorías",
      paginate    : {
        first   : "Primero",
        last    : "Último",
        next    : "Siguiente",
        previous: "Anterior"
      }
    }
  });

  $("#buscador-categorias").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───── Eliminar categoría con confirmación ───── */
  $("#categoriasTable").on("click", ".btn.borrar", function (e) {
    e.preventDefault();
    const $btn = $(this);
    if (confirm(`¿Desea eliminar la categoría «${$btn.data("nombre")}»?`)) {
      $(`#eliminar-form-${$btn.data("id")}`).submit();
    }
  });

  /* ───── Flash-message desde sessionStorage (p.ej. tras editar) ───── */
  const flash = sessionStorage.getItem("flash-categoria");
  if (flash) {
    const $alert = $(`
      <div class="messages">
        <div class="alert alert-success">
          <i class="fas fa-check-circle"></i> ${flash}
        </div>
      </div>`);
    $(".container h2").after($alert);
    sessionStorage.removeItem("flash-categoria");
  }
});
