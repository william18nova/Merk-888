/* static/javascript/visualizar_sucursales.js */
$(function () {
  "use strict";

  /* ───────── DATATABLE & BUSCADOR ───────── */
  const table = $("#sucursalesTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    language  : {
      search      : "",
      zeroRecords : "No se encontraron sucursales",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ sucursales",
      infoEmpty   : "Mostrando 0 a 0 de 0 sucursales",
      paginate    : { first:"Primero", last:"Último", next:"Siguiente", previous:"Anterior" }
    }
  });

  $("#buscador-sucursales").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───────── ELIMINAR SUCURSAL ───────── */
  $("#sucursalesTable").on("click", ".btn.borrar", function (e) {
    e.preventDefault();
    const $btn = $(this);
    if (confirm(`¿Desea eliminar la sucursal «${$btn.data("sucursal-nombre")}»?`)) {
      $(`#eliminar-form-${$btn.data("sucursal-id")}`).submit();
    }
  });

  /* ───────── FLASH-MESSAGE DESDE sessionStorage ───────── */
  const flash = sessionStorage.getItem("flash-sucursal");
  if (flash) {
    const $alert = $(`
      <div class="messages">
        <div class="alert alert-success">
          <i class="fas fa-check-circle"></i> ${flash}
        </div>
      </div>`);

    /* ⬇️  insertar inmediatamente DESPUÉS del <h2> */
    $(".container h2").after($alert);
    sessionStorage.removeItem("flash-sucursal");
  }
});
