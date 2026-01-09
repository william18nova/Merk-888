/* visualizar_pedidos.js */
$(function () {
  "use strict";

  /* DataTable + buscador externo */
  const table = $("#pedidosTable").DataTable({
    paging     : true,
    searching  : true,
    responsive : true,
    columnDefs : [{ targets:"no-sort", orderable:false }],
    language   : {
      search:"", zeroRecords:"No se encontraron pedidos",
      info:"Mostrando _START_ a _END_ de _TOTAL_ pedidos",
      infoEmpty:"Mostrando 0 a 0 de 0 pedidos",
      paginate:{ first:"Primero", last:"Último",
                 next:"Siguiente", previous:"Anterior" }
    }
  });
  $("#buscador-pedidos").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* flashes */
  function flash(txt,isErr=false){
    $("#success-message,#error-message").hide();
    const $el=isErr?$("#error-message"):$("#success-message");
    $el.text(txt).fadeIn();
    setTimeout(()=>$el.fadeOut(),3000);
  }

  // Ocultar automático si está visible por el servidor
  if($("#success-message").is(":visible")){
    setTimeout(()=>$("#success-message").fadeOut(),3000);
  }

  // Fallback: si ?updated=1 está en la URL, mostrar alert
  const params = new URLSearchParams(window.location.search);
  if (params.get("updated") === "1" && !$("#success-message").is(":visible")) {
    $("#success-message").fadeIn();
    setTimeout(()=>$("#success-message").fadeOut(),3000);
  }

  /* CSRF helper */
  const csrftoken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || "";

  /* eliminar */
  $("#pedidosTable").on("click",".btn.borrar",function (e){
    e.stopPropagation();
    const id=$(this).data("id"), $row=$(this).closest("tr");
    if(!confirm("¿Desea eliminar este pedido?")) return;
    $.ajax({
      url:eliminarPedidoUrl.replace("0",id),
      method:"POST",
      headers:{"X-CSRFToken":csrftoken},
      success:res=>{
        if(res.success){
          table.row($row).remove().draw();
          flash("Pedido eliminado exitosamente.");
        }else flash(res.message||"Error al eliminar.",true);
      },
      error:()=>flash("Error de red.",true)
    });
  });

  /* navegar al detalle */
  $("#pedidosTable tbody").on("click","tr",function (e){
    if($(e.target).closest(".btn").length) return;   // evitamos botones
    const id=$(this).data("id");
    window.location.href = verPedidoUrl.replace("0",id);
  });
});
