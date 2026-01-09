$(function(){
    "use strict";
    const table = $("#detalle-pedido").DataTable({
      paging:     false,
      info:       false,
      searching:  true,
      responsive: true,
      dom:        't',          // sólo cuerpo de tabla
      language:   { emptyTable: "" }
    });
    $("#buscador-detalles").on("keyup", function(){
      table.search(this.value).draw();
    });
  });