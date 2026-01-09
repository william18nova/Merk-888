/*  static/javascript/visualizar_productos.js  */
$(function () {
  "use strict";

  const table = $("#productosTable").DataTable({
    processing : true,
    serverSide : true,
    ajax       : { url: window.productosDataUrl, type: "GET" },

    columns: [
      { data: "productoid" },
      { data: "nombre" },
      { data: "descripcion" },
      { data: "precio" },
      { data: "precio_anterior" },   // ✅ NUEVO
      { data: "categoria" },
      { data: "codigo_de_barras" },
      { data: "iva" },
      { data: "impuesto_consumo" },
      { data: "icui" },
      { data: "ibua" },
      { data: "rentabilidad" },
      { data: "acciones", orderable: false, searchable: false }
    ],

    paging      : true,
    pageLength  : 25,
    lengthMenu  : [[10, 25, 50, 100, -1], [10, 25, 50, 100, "Todos"]],
    deferRender : true,
    searching   : true,
    info        : true,
    responsive  : true,
    searchDelay : 250,
    columnDefs  : [{ targets: "no-sort", orderable: false }],
    language    : {
      search      : "",
      lengthMenu  : "Mostrar _MENU_ productos",
      zeroRecords : "No se encontraron productos",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ productos",
      infoEmpty   : "Mostrando 0 a 0 de 0 productos",
      paginate    : { first:"Primero", last:"Último", next:"Siguiente", previous:"Anterior" },
      processing  : "Cargando..."
    },
    stateSave: true,

    createdRow: function (row, data) {
      const labels = [
        "ID",
        "Nombre",
        "Descripción",
        "Precio",
        "Precio anterior",     // ✅ NUEVO
        "Categoría",
        "Cód. Barras",
        "IVA",
        "Imp. Consumo",
        "ICUI",
        "IBUA",
        "Rentabilidad",
        "Acciones"
      ];
      $(row).find("td").each(function (i) {
        $(this).attr("data-label", labels[i]);
      });
      $(row).find("td").last().addClass("actions-cell");
    }
  });

  $("#buscador-productos").on("input", function () {
    table.search(this.value).draw();
  });

  $("#productosTable_filter").hide();

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === (name + "=")) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }
  const csrftoken = getCookie("csrftoken");

  $("#productosTable").on("click", ".btn.borrar", function () {
    const $btn   = $(this);
    const nombre = $btn.data("nombre");
    const url    = $btn.data("url");

    if (!url) return;

    if (confirm(`¿Desea eliminar el producto «${nombre}»?`)) {
      $.ajax({
        url     : url,
        type    : "POST",
        headers : { "X-CSRFToken": csrftoken },
        success : function () { table.ajax.reload(null, false); },
        error   : function (xhr) {
          console.error("Error al eliminar producto:", xhr.status, xhr.responseText);
          alert("Ocurrió un error al eliminar el producto.");
        }
      });
    }
  });

  const flash = sessionStorage.getItem("flash-producto");
  if (flash) {
    $(".container h2").after(`
      <div class="messages">
        <div class="alert alert-success">${flash}</div>
      </div>
    `);
    sessionStorage.removeItem("flash-producto");
  }

  (function barcodeScannerDetector(){
    const CFG = { minChars: 8, gapMs: 60, finishKeys: ['Enter','Tab'], debug: false };
    const $input = $("#buscador-productos");

    function setBarcodeValue(code){
      $input.val(code);
      $input.trigger("input");
      table.search(code).draw();
    }

    let buf = "", first = 0, last = 0, idleTimer = null;

    function reset(){
      buf = ""; first = 0; last = 0;
      if (idleTimer){ clearTimeout(idleTimer); idleTimer = null; }
    }

    function handleFinish(){
      const span = last - first;
      const fastEnough = buf && span < buf.length * (CFG.gapMs + 10);
      if (fastEnough && buf.length >= CFG.minChars){
        const code = buf; reset(); setBarcodeValue(code); return true;
      }
      reset(); return false;
    }

    document.addEventListener("keydown", function(e){
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }

      const t = Date.now();

      if (CFG.finishKeys.includes(e.key)){
        if (handleFinish()){
          e.preventDefault();
          e.stopImmediatePropagation();
        }
        return;
      }

      if (e.key && e.key.length === 1){
        if (buf && (t - last) > CFG.gapMs) { buf = ""; first = t; }
        if (!buf) first = t;
        buf += e.key; last = t;

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => { handleFinish(); }, CFG.gapMs * 5);

        if (document.activeElement !== $input[0]) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }
      } else {
        if (e.key !== "Shift") reset();
      }
    }, true);

    document.addEventListener("paste", e => {
      const txt = (e.clipboardData || window.clipboardData)?.getData("text") || "";
      const val = txt.trim();
      if (val && val.length >= CFG.minChars){
        e.preventDefault();
        setBarcodeValue(val);
      }
    }, true);
  })();
});
