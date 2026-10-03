/* Adaptador de transporte. El carrito, buscadores y modal son los originales. */
(() => {
  'use strict';
  const session = JSON.parse(document.getElementById('hybrid-sale-context').textContent);
  const djangoRuntime = Boolean(session.django_runtime);
  const token = djangoRuntime ? session.csrf_token : document.querySelector('meta[name="local-token"]').content;
  const endpoint = path => djangoRuntime ? path.replace(/^\/api\//, '/local/sales/api/') : path;
  const $ = window.jQuery;
  if (djangoRuntime) {
    const lookups = {productoAutocompleteUrl:'producto_autocomplete', productoAutocompleteCodigoUrl:'producto_autocomplete_codigo', productoAutocompleteIdUrl:'producto_autocomplete_id',
      productoAutocompleteBarrasUrl:'producto_autocomplete_barras', productoSnapshotUrl:'producto_snapshot',
      buscarProductoPorCodigoUrl:'buscar_producto_por_codigo', verificarProductoUrl:'verificar_producto',
      clienteAutocompleteUrl:'cliente_autocomplete', sucursalAutocompleteUrl:'sucursal_autocomplete',
      puntopagoAutocompleteUrl:'puntopago_autocomplete'};
    for (const [key, name] of Object.entries(lookups)) window[key] = '/local/sales/read/' + name + '/';
    $('.hybrid-sale-status a').attr('href', '/local/estado/');
  }
  window.ventaUsuarioId = String(session.user_id);
  window.ventaTurnoId = String(session.turn_id);
  window.ventaSucursalIdServidor = String(session.branch_id);
  window.ventaPuntoPagoIdServidor = String(session.point_id);
  window.ventaSucursalNombreServidor = session.branch;
  window.ventaPuntoPagoNombreServidor = session.point;
  window.carritoLimpioAuditUrl = ''; // Nunca simular un registro de auditoría remoto.
  window.POS_AGENT_TOKEN = '';
  window.FAST_SALE_SUCCESS_ALERT = false;
  $('#sucursal_id').val(session.branch_id);
  $('#puntopago_id').val(session.point_id);
  $('#sucursal_autocomplete').val(session.branch);
  $('#puntopago_autocomplete').val(session.point);
  $('#cliente_busqueda').prop('disabled', true).attr('placeholder', 'Venta local sin cliente asociado');
  if (!djangoRuntime) $('#mix-mode').prop('disabled', true).closest('.mix-row').hide();
  $('.payment-help').text(djangoRuntime ? 'Confirma solo dinero recibido. Nequi y bancos se registran manualmente: el POS local no verifica transferencias.' : 'Recibe el efectivo y confirma. Si no hay internet, la venta queda guardada en este equipo.');
  $('#btn-scan-cam').attr('title', 'Cámara nativa si este navegador la admite; lector USB disponible sin conexión');

  const request = async (path, data) => {
    const response = await fetch(endpoint(path), data === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json', [djangoRuntime ? 'X-CSRFToken' : 'X-Local-Token']: token},
      body: JSON.stringify({...data, session_id: session.session_id}),
    });
    let result;
    try { result = await response.json(); } catch (_) { throw Error('El equipo no devolvió una confirmación válida.'); }
    if (!response.ok) {
      const error = new Error(result.error || 'No se pudo completar.');
      error.status = response.status;
      throw error;
    }
    return result;
  };
  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const label = document.getElementById('hybrid-connection');
    try {
      const state = await request('/api/status');
      const same = state.unlocked && state.session?.session_id === session.session_id;
      const expired = !same || Date.parse(state.session.expires_at) <= Date.now();
      const enabled = same && state.ready && !expired && !state.conflicts;
      $('#generar-venta').attr('data-server-enabled', enabled ? '1' : '0');
      if (!enabled) $('#generar-venta').prop('disabled', true);
      window.dispatchEvent(new Event('nova-hybrid-state'));
      label.textContent = !same ? 'Sesión bloqueada o cambiada · abre Conexión y sesión'
        : expired ? 'Autorización vencida · conecta y renueva la sesión'
        : state.session.closing ? (state.session.closed ? 'Turno cerrado · inicia otra sesión antes de vender' : 'Cierre declarado · esperando conciliación')
        : state.conflicts ? `${state.conflicts} venta(s) requieren revisión · no repitas el cobro`
        : state.online ? (state.pending ? `Conectado · ${state.pending} por sincronizar` : 'Conectado · ventas sincronizadas')
        : `Sin conexión · ${state.pending} venta(s) guardadas en este equipo`;
      label.dataset.state = enabled ? (state.online ? 'online' : 'offline') : 'blocked';
    } catch (_) {
      label.textContent = 'No responde el motor local · conserva el carrito y revisa Nova POS';
      label.dataset.state = 'blocked';
      $('#generar-venta').attr('data-server-enabled', '0').prop('disabled', true);
    } finally { refreshing = false; }
  }

  const operation = async id => {
    // Leer bajo el bloqueo del servidor: si el POST continúa en curso, esperar
    // su resultado antes de decidir que no existe. Nunca consultar la nube aquí.
    try { return await request('/api/operation/' + encodeURIComponent(id)); }
    catch (e) { if (e.status === 404) return null; throw e; }
  };

  window.NovaHybridSale = {
    token,
    async draftWasRegistered(id) { return Boolean(await operation(id)); },
    async submit(body, id, total) {
      try {
        const pagos = JSON.parse(body.get('pagos') || '[]');
        if (body.get('cliente_id') || body.get('nequi_notificacion_id') || body.get('empleado_password')
            || body.get('codigo_descuento_merk2888') || (!djangoRuntime && (pagos.length !== 1 || pagos[0].medio_pago !== 'efectivo'))) {
          return {success: false, error: 'No se admiten clientes, descuentos personales ni vinculación automática Nequi en esta etapa local.'};
        }
        if (!Number.isFinite(Number(total)) || Math.round(pagos.reduce((sum,p)=>sum+Number(p.monto),0)*100) !== Math.round(Number(total)*100)) {
          return {success: false, error: 'Los medios asignados no coinciden con el total. Revisa el pago.'};
        }
        const products = JSON.parse(body.get('productos') || '[]');
        const quantities = JSON.parse(body.get('cantidades') || '[]');
        if (products.length !== quantities.length) return {success: false, error: 'Revisa las cantidades del carrito.'};
        const payload = {operation_id: id, expected_total: Number(total).toFixed(2),
          cash_received: body.get('efectivo_recibido') || String(total),
          items: products.map((p, i) => ({id: Number(p), quantity: Number(quantities[i])}))};
        if (djangoRuntime) payload.payments = pagos;
        let row;
        try { row = await request('/api/checkout', payload); }
        catch (error) {
          if (error.status && error.status < 500) return {success: false, error: error.message};
          // Respuesta local perdida después del commit: misma referencia, sin
          // pedirle al cajero que vuelva a registrar ni que cambie de pantalla.
          row = await operation(id);
          if (!row) row = await request('/api/checkout', payload);
        }
        refresh();
        if (row.state === 'conflict') return {success:false,error:row.error || 'Operación en revisión. No repitas el cobro.'};
        return {success: true, hybrid_state: row.state, venta_id: row.sale_id || row.id,
          operation_id: row.id, sale_total: total, receipt_text: row.receipt};
      } catch (error) {
        refresh();
        throw error;
      }
    },
  };

  const dialog = document.getElementById('hybrid-history');
  document.getElementById('hybrid-history-toggle').addEventListener('click', async () => {
    const list = document.getElementById('hybrid-history-list');
    list.textContent = 'Consultando…';
    dialog.showModal();
    try {
      const rows = await request('/api/history');
      list.replaceChildren();
      if (!rows.length) list.textContent = 'Todavía no hay ventas en esta sesión.';
      for (const row of rows) {
        const details = document.createElement('details'), summary = document.createElement('summary'), receipt = document.createElement('pre');
        summary.textContent = (row.sale_id ? `Venta #${row.sale_id}` : `Venta local ${row.id.slice(0, 8)}`)
          + ' · ' + ({accepted: 'Sincronizada', pending: 'Pendiente de sincronizar', conflict: 'Requiere revisión'}[row.state] || row.state);
        receipt.textContent = row.receipt + (row.error ? '\n' + row.error : '');
        details.append(summary, receipt); list.append(details);
      }
    } catch (error) { list.textContent = error.message; }
  });
  document.getElementById('hybrid-history-close').addEventListener('click', () => dialog.close());
  if (!djangoRuntime) $('.nv__logout-form').on('submit', async e => {
    e.preventDefault();
    try { await request('/api/lock', {}); location.assign('/'); }
    catch (error) { alert(error.message); }
  });
  $('.nv__brand').attr('href', '/generar_venta/');
  refresh();
  setInterval(refresh, 4000);
})();
