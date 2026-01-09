/*  visualizar_productos.js  */
$(function () {
  "use strict";

  /* ───────── DataTable + buscador externo ───────── */
  const table = $("#productosTable").DataTable({
    paging     : true,
    searching  : true,
    info       : true,
    responsive : true,
    columnDefs : [{ targets: "no-sort", orderable: false }],
    language   : {
      search      : "",                      // ocultamos la barra nativa
      zeroRecords : "No se encontraron productos",
      info        : "Mostrando _START_ a _END_ de _TOTAL_ productos",
      infoEmpty   : "Mostrando 0 a 0 de 0 productos",
      paginate    : {
        first   : "Primero",
        last    : "Último",
        next    : "Siguiente",
        previous: "Anterior"
      }
    }
  });

  $("#buscador-productos").on("keyup", function () {
    table.search(this.value).draw();
  });

  /* ───────── eliminar producto ───────── */
  $("#productosTable").on("click", ".btn.borrar", function () {
    const $btn   = $(this);
    const nombre = $btn.data("nombre");
    const $form  = $btn.closest("td").find(".delete-form");

    if (confirm(`¿Desea eliminar el producto «${nombre}»?`)) {
      $form.submit();
    }
  });

  /* ───────── flash-message tras ADD/EDIT ───────── */
  const flash = sessionStorage.getItem("flash-producto");
  if (flash) {
    $(".container h2").after(`
      <div class="messages">
        <div class="alert alert-success">${flash}</div>
      </div>
    `);
    sessionStorage.removeItem("flash-producto");
  }

  /* ───────── detector de pistola de código de barras ───────── */
  (function barcodeScannerDetector(){
    const CFG = {
      minChars: 8,
      gapMs: 60,
      finishKeys: ['Enter','Tab'],
      debug: false
    };
    const $input = $("#buscador-productos");

    function setBarcodeValue(code){
      $input.val(code);
      $input.trigger("input").trigger("change").trigger("keyup");
      table.search(code).draw();
      if (CFG.debug) console.log("[scanner] code:", code);
    }

    let buf="", first=0, last=0, idleTimer=null;
    function reset(){ buf=""; first=0; last=0; if(idleTimer){clearTimeout(idleTimer); idleTimer=null;} }

    function handleFinish(){
      const span = last - first;
      const fastEnough = buf && span < buf.length * (CFG.gapMs+10);
      if (fastEnough && buf.length >= CFG.minChars){
        const code = buf; reset(); setBarcodeValue(code); return true;
      }
      reset(); return false;
    }

    document.addEventListener("keydown", function(e){
      if (e.ctrlKey || e.altKey || e.metaKey) { reset(); return; }
      const t = Date.now();

      if (CFG.finishKeys.includes(e.key)){
        if (handleFinish()){ e.preventDefault(); e.stopImmediatePropagation(); }
        return;
      }

      if (e.key && e.key.length === 1){
        if (buf && (t-last) > CFG.gapMs) { buf=""; first=t; }
        if (!buf) first=t;
        buf+=e.key; last=t;

        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(()=>{ handleFinish(); }, CFG.gapMs*5);

        // evitar que se escriba en otro campo
        if (document.activeElement !== $input[0]) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }
      }else{
        if (e.key !== "Shift") reset();
      }
    }, true);

    document.addEventListener("paste", e=>{
      const txt=(e.clipboardData||window.clipboardData)?.getData("text")||"";
      const val=txt.trim();
      if(val && val.length>=CFG.minChars){
        e.preventDefault();
        setBarcodeValue(val);
      }
    }, true);
  })();

});
