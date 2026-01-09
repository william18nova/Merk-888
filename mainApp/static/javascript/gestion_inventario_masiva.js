// static/javascript/gestion_inventario_masiva.js
$(function () {
  "use strict";
  const $ = window.jQuery;

  /* ================= CSRF ================= */
  function getCSRF() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  function showErr(msg){
    $("#success-message").hide().text("");
    $("#error-message").html(`<i class="fas fa-exclamation-circle"></i> ${msg}`).show();
  }
  function showOk(msg){
    $("#error-message").hide().text("");
    $("#success-message").html(`<i class="fas fa-check-circle"></i> ${msg}`).show();
  }

  const $sucAC = $("#sucursal_autocomplete");
  const $sucId = $("#sucursal_id");

  const $inpNom = $("#producto_busqueda_nombre");
  const $inpBar = $("#producto_busqueda_barras");
  const $inpId  = $("#producto_busqueda_id");

  const $tbody  = $("#tabla-body");

  function onlyDigits(s){ return String(s||"").replace(/\D+/g, ""); }

  function requireSucursal(){
    const sid = ($sucId.val() || "").trim();
    if (!sid) {
      showErr("Selecciona una sucursal primero.");
      $sucAC.trigger("focus");
      return null;
    }
    return sid;
  }

  /* ================= DataTable ================= */
  const dt = $("#tabla-masivo").DataTable({
    paging: false,
    searching: false,
    info: false,
    ordering: false,
    deferRender: true,
    responsive: true,
    language: { emptyTable: "Selecciona una sucursal y agrega productos…" }
  });

  function rowExists(productId){
    return $tbody.find(`tr[data-product-id="${productId}"]`).length > 0;
  }

  function escapeHtml(str){
    return String(str||"")
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;")
      .replaceAll('"',"&quot;")
      .replaceAll("'","&#039;");
  }

  function computeNuevoTotal($tr){
    const inv = Number($tr.find(".inv_qty").val() || 0);
    const ing = Number($tr.find(".ingresado").val() || 0);
    const total = (Number.isFinite(inv) ? inv : 0) + (Number.isFinite(ing) ? ing : 0);
    $tr.find(".nuevo_total").val(String(total));
  }

  function addRow(p, invQty){
    const pid = String(p.id);

    if (rowExists(pid)){
      showOk("Ese producto ya está en la tabla.");
      return;
    }

    const rowHtml = `
      <tr data-product-id="${pid}">
        <td><input class="form-control pid" value="${pid}" readonly></td>

        <td><input class="form-control p_nombre" value="${escapeHtml(p.nombre || "")}"></td>
        <td><input class="form-control p_desc" value="${escapeHtml(p.descripcion || "")}"></td>
        <td><input class="form-control p_barras" value="${escapeHtml(p.codigo_de_barras || "")}"></td>
        <td><input class="form-control p_categoria" value="${escapeHtml(p.categoria_id || "")}" inputmode="numeric"></td>

        <td><input class="form-control p_precio" value="${escapeHtml(p.precio || "0")}" inputmode="decimal"></td>
        <td><input class="form-control p_precio_anterior" value="${escapeHtml(p.precio_anterior || "")}" inputmode="decimal"></td>
        <td><input class="form-control p_iva" value="${escapeHtml(p.iva || "0")}" inputmode="decimal"></td>

        <td><input class="form-control p_imp_consumo" value="${escapeHtml(p.impuesto_consumo || "0")}" inputmode="decimal"></td>
        <td><input class="form-control p_icui" value="${escapeHtml(p.icui || "0")}" inputmode="decimal"></td>
        <td><input class="form-control p_ibua" value="${escapeHtml(p.ibua || "0")}" inputmode="decimal"></td>
        <td><input class="form-control p_rentabilidad" value="${escapeHtml(p.rentabilidad || "0")}" inputmode="decimal"></td>

        <td><input class="form-control inv_qty" value="${Number(invQty || 0)}" readonly></td>
        <td><input class="form-control ingresado" type="number" step="1" value="0"></td>
        <td><input class="form-control nuevo_total" value="${Number(invQty || 0)}" readonly></td>

        <td>
          <button type="button" class="btn-eliminar" title="Quitar fila">
            <i class="fas fa-trash-alt"></i>
          </button>
        </td>
      </tr>
    `;

    dt.row.add($(rowHtml)).draw(false);

    const $tr = $tbody.find(`tr[data-product-id="${pid}"]`);
    $tr.on("input", ".ingresado", function(){ computeNuevoTotal($tr); });
  }

  /* ================= Limpiar UI (manteniendo sucursal) ================= */
  function clearProductsUI(){
    dt.clear().draw(false);

    // limpiar inputs de búsqueda de producto
    $inpNom.val("");
    $inpBar.val("");
    $inpId.val("");

    // cerrar autocompletes si están abiertos (evita “pegados”)
    try { $inpNom.autocomplete("close"); } catch {}
    try { $inpBar.autocomplete("close"); } catch {}
    try { $inpId.autocomplete("close"); } catch {}

    // foco rápido para seguir agregando
    setTimeout(() => $inpNom.trigger("focus"), 0);
  }

  // ✅ “limpiar página” pero CONSERVAR sucursal (texto + hidden id)
  function resetPageKeepSucursal({ keepMessages=false } = {}){
    clearProductsUI();

    if (!keepMessages){
      $("#error-message").hide().text("");
      $("#success-message").hide().text("");
    }

    // Mantiene sucursal: no tocamos $sucAC ni $sucId
  }

  /* ================= Autocomplete Sucursal ================= */
  $sucAC.autocomplete({
    minLength: 1,
    delay: 0,
    autoFocus: true,
    appendTo: "body",
    source: function(req, resp){
      const term = (req.term || "").trim();
      fetch(`${SUCURSAL_URL}?term=${encodeURIComponent(term)}&page=1`, {cache:"no-store"})
        .then(r => r.ok ? r.json() : {results:[]})
        .then(d => resp((d.results || []).map(x => ({ id:x.id, label:x.text, value:x.text }))))
        .catch(() => resp([]));
    },
    select: function(_e, ui){
      if (!ui || !ui.item) return false;

      // ✅ guardar ID y dejar el texto elegido tal cual
      $sucId.val(String(ui.item.id));
      $sucAC.val(String(ui.item.label || ui.item.value || ""));

      showOk("Sucursal seleccionada. Ahora agrega productos.");

      // ✅ limpiar SOLO productos (tabla + buscadores), pero conservar sucursal
      resetPageKeepSucursal({ keepMessages:true });

      return false;
    },
    change: function(_e, ui){
      // ✅ si el usuario escribió algo y NO eligió una opción válida -> limpiar sucursal_id
      if (!ui || !ui.item){
        $sucId.val("");
      }
    }
  });

  /* ================= 3 Autocomplete Producto ================= */
  function makeAC($input, urlBuilder, mode){
    $input.autocomplete({
      minLength: 1,
      delay: 0,
      autoFocus: true,
      appendTo: "body",
      source: function(req, resp){
        const term = (req.term || "").trim();
        if (!term) return resp([]);

        fetch(urlBuilder(term), {cache:"no-store"})
          .then(r => r.ok ? r.json() : {results:[]})
          .then(d => {
            resp((d.results || []).map(x => ({
              id: x.id,
              label: mode === "barras" ? `${(x.barcode||x.text||"")} — ${x.text||""}` :
                     mode === "id"     ? `#${x.id} — ${x.text||""}` :
                                         (x.text||""),
              value: mode === "barras" ? String(x.barcode||"") :
                     mode === "id"     ? String(x.id) :
                                         String(x.text||""),
              text: x.text || "",
              barcode: x.barcode || ""
            })));
          })
          .catch(() => resp([]));
      },
      select: function(_e, ui){
        if (!ui || !ui.item) return false;

        const sid = requireSucursal();
        if (!sid) return false;

        fetch(`${PROD_DETALLE_URL}?sucursal_id=${encodeURIComponent(sid)}&productoid=${encodeURIComponent(ui.item.id)}`, {cache:"no-store"})
          .then(r => r.ok ? r.json() : Promise.reject())
          .then(d => {
            if (!d || !d.success) { showErr("No se pudo cargar el producto."); return; }
            addRow(d.product, d.inventario?.cantidad ?? 0);
            showOk("Producto agregado a la tabla.");
            $input.val("");
          })
          .catch(() => showErr("Error de red cargando producto."));
        return false;
      }
    });
  }

  makeAC($inpNom, (term) => `${PROD_NOMBRE_URL}?term=${encodeURIComponent(term)}&page=1`, "nombre");
  makeAC($inpBar, (term) => `${PROD_BARRAS_URL}?term=${encodeURIComponent(term)}&page=1`, "barras");
  makeAC($inpId,  (term) => `${PROD_ID_URL}?term=${encodeURIComponent(onlyDigits(term))}&page=1`, "id");

  $inpId.on("input", function(){
    const d = onlyDigits(this.value);
    if (this.value !== d) this.value = d;
  });

  /* ================= Quitar fila ================= */
  $tbody.on("click", ".btn-eliminar", function(){
    const $tr = $(this).closest("tr");
    dt.row($tr).remove().draw(false);
  });

  /* ================= Guardar Todo ================= */
  $("#btnGuardarTodo").on("click", function(){
    const sid = requireSucursal();
    if (!sid) return;

    const rows = [];
    $tbody.find("tr").each(function(){
      const $tr = $(this);
      const pid = String($tr.data("product-id"));

      rows.push({
        productId: pid,
        ingresado: $tr.find(".ingresado").val(), // puede ser negativo/0
        producto: {
          nombre: $tr.find(".p_nombre").val(),
          descripcion: $tr.find(".p_desc").val(),
          codigo_de_barras: $tr.find(".p_barras").val(),
          categoria_id: $tr.find(".p_categoria").val(),

          precio: $tr.find(".p_precio").val(),
          precio_anterior: $tr.find(".p_precio_anterior").val(),
          iva: $tr.find(".p_iva").val(),

          impuesto_consumo: $tr.find(".p_imp_consumo").val(),
          icui: $tr.find(".p_icui").val(),
          ibua: $tr.find(".p_ibua").val(),
          rentabilidad: $tr.find(".p_rentabilidad").val(),
        }
      });
    });

    if (!rows.length){
      showErr("No hay productos en la tabla.");
      return;
    }

    const fd = new FormData();
    fd.append("action", "save_rows");
    fd.append("sucursal_id", sid);
    fd.append("payload", JSON.stringify(rows));

    fetch(POST_URL, {
      method: "POST",
      headers: { "X-CSRFToken": getCSRF(), "Accept":"application/json" },
      body: fd
    })
    .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
    .then(d => {
      if (!d || !d.success){
        showErr(d?.error || "No se pudo guardar.");
        return;
      }

      showOk(`✅ Guardado OK. Filas procesadas: ${d.updated}`);

      // ✅ limpiar “la página” (tabla + buscadores), pero conservar sucursal seleccionada
      resetPageKeepSucursal({ keepMessages:true });
    })
    .catch(err => showErr(err?.error || "Error guardando cambios."));
  });

  /* ================= Nuevo Producto (modal) ================= */
  const $modal = $("#modalNuevoProducto");

  $("#btnNuevoProducto").on("click", function(){
    const sid = requireSucursal();
    if (!sid) return;
    $modal.css("display","flex");
    $("#np_nombre").trigger("focus");
  });

  $("#btnCerrarModalProducto").on("click", function(){
    $modal.hide();
  });

  $modal.on("click", function(e){
    if (e.target === this) $modal.hide();
  });

  $("#btnCrearProducto").on("click", function(){
    const sid = requireSucursal();
    if (!sid) return;

    const fd = new FormData();
    fd.append("action", "create_product");
    fd.append("sucursal_id", sid);

    fd.append("nombre", ($("#np_nombre").val() || "").trim());
    fd.append("descripcion", ($("#np_desc").val() || "").trim());
    fd.append("codigo_de_barras", ($("#np_barras").val() || "").trim());
    fd.append("categoria_id", ($("#np_categoria").val() || "").trim());

    fd.append("precio", ($("#np_precio").val() || "").trim());
    fd.append("precio_anterior", ($("#np_precio_anterior").val() || "").trim());
    fd.append("iva", ($("#np_iva").val() || "").trim());

    fd.append("impuesto_consumo", ($("#np_imp_consumo").val() || "0").trim());
    fd.append("icui", ($("#np_icui").val() || "0").trim());
    fd.append("ibua", ($("#np_ibua").val() || "0").trim());
    fd.append("rentabilidad", ($("#np_rentabilidad").val() || "0").trim());

    fd.append("cantidad_inicial", ($("#np_cant").val() || "0").trim());

    fetch(POST_URL, {
      method: "POST",
      headers: { "X-CSRFToken": getCSRF(), "Accept":"application/json" },
      body: fd
    })
    .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
    .then(d => {
      if (!d || !d.success){
        showErr(d?.error || "No se pudo crear.");
        return;
      }

      addRow(d.product, d.inventario?.cantidad ?? 0);
      showOk("✅ Producto creado y agregado.");

      $("#np_nombre,#np_desc,#np_barras,#np_categoria,#np_precio,#np_precio_anterior,#np_iva").val("");
      $("#np_imp_consumo,#np_icui,#np_ibua,#np_rentabilidad").val("0");
      $("#np_cant").val("0");

      $modal.hide();
    })
    .catch(err => showErr(err?.error || "Error creando producto."));
  });

});
