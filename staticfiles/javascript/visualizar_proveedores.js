/* visualizar_proveedores.js */
$(function () {
  "use strict";

  /* ───────── DataTable + buscador externo ───────── */
  const table = $("#proveedoresTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    columnDefs: [{ targets:"no-sort", orderable:false }],
    language  : {
      search      : "",
      zeroRecords : "No se encontraron proveedores",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ proveedores",
      infoEmpty   : "Mostrando 0 a 0 de 0 proveedores",
      paginate    : { first:"Primero", last:"Último",
                      next:"Siguiente", previous:"Anterior" }
    }
  });

  $("#buscador-proveedores").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───────── eliminar proveedor ───────── */
  $("#proveedoresTable").on("click", ".btn.borrar", function (e) {
    e.preventDefault();
    const $btn = $(this);
    if (confirm(`¿Desea eliminar al proveedor «${$btn.data("prov-nombre")}»?`)) {
      $(`#eliminar-prov-${$btn.data("prov-id")}`).submit();
    }
  });

  /* ───────── flash message vía sessionStorage ───────── */
  const flash = sessionStorage.getItem("flash-prov");
  if (flash) {
    $(".container h2").after(`
      <div class="messages">
        <div class="alert alert-success">
          <i class="fas fa-check-circle"></i> ${flash}
        </div>
      </div>`);
    sessionStorage.removeItem("flash-prov");
  }
});
