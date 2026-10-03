"""Lista explícita: una URL nueva no se habilita offline por accidente.

Esta primera fase permite consultar las vistas originales con una transacción
PostgreSQL de solo lectura. No convierte formularios en operaciones sincronizadas.
"""
READ_ROUTES = frozenset("""
home visualizar_sucursales visualizar_categorias visualizar_productos productos_datatable
visualizar_inventarios visualizar_proveedores visualizar_productos_precios_proveedores
visualizar_puntos_pago visualizar_empleados visualizar_clientes
visualizar_ventas ventas_datatable ver_venta visualizar_cambios
visualizar_pedidos ver_pedido pedidos_pagados
visualizar_horarios visualizar_horarios_cajas
calendario_empleados calendario_empleados_datos mi_horario mi_horario_datos
turnos_caja_dashboard api_turnos_caja_list api_turno_caja_detail
turnos_caja_admin api_admin_turno_detail
ventas_diarias ventas_diarias_stats metricas_negocio metricas_negocio_data
pagos_editar_lista reporte_ventas_producto
producto_snapshot producto_autocomplete producto_autocomplete_id
producto_autocomplete_codigo producto_autocomplete_barras producto_autocomplete_global
cliente_autocomplete sucursal_autocomplete puntopago_autocomplete
categoria_autocomplete producto_inventario_autocomplete
sucursal_con_inventario_autocomplete proveedor_con_productos_autocomplete
sucursal_ventas_autocomplete puntopago_ventas_autocomplete
turno_caja_puntopago_ac turno_caja_cajero_ac
local_runtime_status
""".split())

AUTH_ROUTES = frozenset({"login", "logout"})

MODULES = (
    ("Productos y categorías", "visualizar_productos"),
    ("Inventarios", "visualizar_inventarios"),
    ("Clientes", "visualizar_clientes"),
    ("Proveedores y pedidos", "visualizar_proveedores"),
    ("Ventas y devoluciones", "visualizar_ventas"),
    ("Pagos registrados", "pagos_editar_lista"),
    ("Turnos de caja", "turnos_caja_dashboard"),
    ("Empleados y horarios", "calendario_empleados"),
    ("Métricas", "metricas_negocio"),
)
