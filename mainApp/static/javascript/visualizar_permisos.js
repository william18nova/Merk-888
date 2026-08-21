/* Tabla de consulta del catálogo de permisos. */
$(function () {
  "use strict";

  $("#permisosTable").DataTable({
    paging    : true,
    searching : true,
    info      : true,
    responsive: true,
    language  : {
      search      : "",
      zeroRecords : "No se encontraron permisos",
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
});
