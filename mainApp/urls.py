from django.urls import path, include
from . import views
from django.contrib.auth import views as auth_views
from .views import (
                    LoginView,
                    HomePageView,
                    SucursalCreateAJAXView,
                    SucursalListView,
                    SucursalUpdateAJAXView,
                    CategoriaCreateAJAXView,
                    CategoriaListView,
                    CategoriaUpdateAJAXView,
                    ProductoCreateAJAXView,
                    CategoriaAutocompleteView,
                    ProductoListView,
                    ProductoUpdateAJAXView,
                    InventarioCreateAJAXView,
                    SucursalSinInventarioAutocomplete,
                    ProductoAutocomplete,
                    InventarioListView,
                    SucursalInventarioAutocompleteView,
                    EditarInventarioView,
                    SucursalInventarioAutocompleteEditarView,
                    ProveedorCreateView,
                    ProveedorListView,
                    ProveedorUpdateView,
                    PreciosProveedorCreateAJAXView,
                    ProveedorSinPreciosAutocomplete,
                    ProductoExcludingAutocomplete,
                    PreciosProveedorListView,
                    PreciosProveedorUpdateAJAXView,
                    ProveedorConProductosAutocomplete,
                    PuntosPagoCreateAJAXView,
                    SucursalSinPuntoPagoAutocomplete,
                    PuntosPagoListView,
                    SucursalConPuntosAutocomplete,
                    PuntosPagoUpdateAJAXView,
                    SucursalEditarPuntoPagoAutocomplete,
                    RolCreateAJAXView,
                    RolListView,
                    RolUpdateAJAXView,
                    UsuarioCreateAJAXView,
                    RolAutocompleteView,
                    UsuarioListView,
                    UsuarioUpdateAJAXView,
                    EmpleadoCreateAJAXView,
                    UsuarioDisponibleAutocomplete,
                    SucursalAutocomplete,
                    EmpleadoListView,
                    HorarioCreateAJAXView,
                    SucursalSinHorarioAutocomplete,
                    HorariosListView,
                    SucursalConHorariosAutocomplete,
                    HorarioUpdateAJAXView,
                    HorarioCajaCreateAJAXView,
                    SucursalHorarioCajaAutocomplete,
                    PermisoCreateView,

                    )



urlpatterns = [
    path("", LoginView.as_view(), name="login"),

    path('home/', HomePageView.as_view(), name='home'),

    path('agregar_sucursal/', SucursalCreateAJAXView.as_view(), name='agregar_sucursal'),
    path("visualizar_sucursales/", SucursalListView.as_view(), name="visualizar_sucursales"),
    path('sucursales/eliminar/<int:sucursal_id>/', views.eliminar_sucursal, name='eliminar_sucursal'),
    path("sucursales/editar/<int:sucursal_id>/", SucursalUpdateAJAXView.as_view(), name="editar_sucursal", ),

    path("agregar_categoria/", CategoriaCreateAJAXView.as_view(), name="agregar_categoria"),
    path("visualizar_categorias/", CategoriaListView.as_view(), name="visualizar_categorias"),
    path('categorias/eliminar/<int:categoria_id>/', views.eliminar_categoria, name='eliminar_categoria'),
    path("categorias/editar/<int:categoria_id>/", CategoriaUpdateAJAXView.as_view(), name="editar_categoria"),

    path("agregar_producto/", ProductoCreateAJAXView.as_view(), name="agregar_producto"),
    path("categoria_autocomplete/", CategoriaAutocompleteView.as_view(), name="categoria_autocomplete"),
    path('visualizar_productos/',  ProductoListView.as_view(), name='visualizar_productos'),
    path("productos/data/", views.ProductoDataTableView.as_view(), name="productos_datatable"),
    path('productos/eliminar/<int:producto_id>/', views.eliminar_producto, name='eliminar_producto'),
    path("productos/editar/<int:producto_id>/", ProductoUpdateAJAXView.as_view(), name="editar_producto"),


    path('agregar_inventario/', InventarioCreateAJAXView.as_view(), name='agregar_inventario'),
    # Ruta para autocompletar Sucursales sin Inventario

    path("sucursal_inventario/", views.sucursal_sin_inventario_autocomplete, name="sucursal_sin_inventario_autocomplete"),
    # Ruta para autocompletar Productos (por ejemplo)
    path('autocomplete/producto_inventario/', ProductoAutocomplete.as_view(), name='producto_inventario_autocomplete'),
    path('visualizar_inventarios/', InventarioListView.as_view(), name='visualizar_inventarios'),
    path('autocomplete/sucursal_con_inventario/', SucursalInventarioAutocompleteView.as_view(), name='sucursal_con_inventario_autocomplete'),
    path('editar_inventario/<int:sucursal_id>/', EditarInventarioView.as_view(), name='editar_inventario'),
    path('autocomplete/sucursal_inventario_editar/', SucursalInventarioAutocompleteEditarView.as_view(), name='sucursal_inventario_autocomplete'),
    path("inventario/<int:sucursal_id>/item/", views.InventarioItemAjaxView.as_view(),
         name="inventario_item_ajax"),
    path("autocomplete/inventario/producto/nombre/", views.ProductoInventarioBuscarNombreView.as_view(),
         name="producto_inventario_buscar_nombre"),
    path("autocomplete/inventario/producto/barras/", views.ProductoInventarioBuscarBarrasView.as_view(),
         name="producto_inventario_buscar_barras"),
    path("autocomplete/inventario/producto/id/", views.ProductoInventarioBuscarIdView.as_view(),
         name="producto_inventario_buscar_id"),
    path('inventario/eliminar/<int:inventario_id>/', views.eliminar_producto_inventario_view, name='eliminar_producto_inventario'),

    path('agregar_proveedor/', ProveedorCreateView.as_view(), name='agregar_proveedor'),
    path("visualizar_proveedores/", ProveedorListView.as_view(), name="visualizar_proveedores"),
    path('eliminar_proveedor/<int:proveedor_id>/', views.eliminar_proveedor, name='eliminar_proveedor'),
    path("editar_proveedor/<int:proveedor_id>/", ProveedorUpdateView.as_view(), name="editar_proveedor"),


    path("agregar_productos_precios_proveedor/",PreciosProveedorCreateAJAXView.as_view(),name="agregar_productos_precios_proveedor"),
    path("autocomplete/proveedor_precios/", ProveedorSinPreciosAutocomplete.as_view(), name="proveedor_precios_autocomplete"),
    path("autocomplete/producto_precios/", ProductoExcludingAutocomplete.as_view(), name="producto_precios_autocomplete"),
    path("visualizar_precios_proveedor/", PreciosProveedorListView.as_view(), name="visualizar_productos_precios_proveedores"),
    path("autocomplete/proveedor_con_productos/", ProveedorConProductosAutocomplete.as_view(), name="proveedor_con_productos_autocomplete"),
    path('precios_proveedor/eliminar/<int:id>/', views.eliminar_precio_proveedor_view, name='eliminar_precio_proveedor'),
    path("editar_productos_precios_proveedor/<int:proveedor_id>/", PreciosProveedorUpdateAJAXView.as_view(), name="editar_productos_precios_proveedor"),

    path("agregar_punto_pago/", PuntosPagoCreateAJAXView.as_view(), name="agregar_punto_pago"),
    path("autocomplete/sucursal_punto_pago/", SucursalSinPuntoPagoAutocomplete.as_view(), name="sucursal_punto_pago_autocomplete"),
    path("visualizar_puntos_pago/", PuntosPagoListView.as_view(), name="visualizar_puntos_pago"),
    path('eliminar_punto_pago/<int:puntopagoid>/', views.eliminar_punto_pago_view, name='eliminar_punto_pago'),
    path('autocomplete/sucursal_punto_pago_visualizar/',  SucursalConPuntosAutocomplete.as_view(), name='sucursal_punto_pago_visualizar_autocomplete'),
    path("editar-puntos-pago/<int:sucursal_id>/", PuntosPagoUpdateAJAXView.as_view(), name="editar_puntos_pago"),
    path("sucursal_editar_punto_pago_autocomplete/", SucursalEditarPuntoPagoAutocomplete.as_view(), name="sucursal_editar_punto_pago_autocomplete"),

    path("agregar_rol/", RolCreateAJAXView.as_view(), name="agregar_rol"),
    path("visualizar_roles/", RolListView.as_view(), name="visualizar_roles"),
    path("roles/editar/<int:rol_id>/", RolUpdateAJAXView.as_view(), name="editar_rol"),
    path('eliminar_rol/<int:rol_id>/', views.eliminar_rol_view, name='eliminar_rol'),


    path("agregar_usuario/",      UsuarioCreateAJAXView.as_view(), name="agregar_usuario"),
    path("rol_autocomplete/",     RolAutocompleteView.as_view(),   name="rol_autocomplete_usuarios"),
    path("visualizar_usuarios/", UsuarioListView.as_view(), name="visualizar_usuarios"),
    path('eliminar_usuario/<int:usuarioid>/', views.eliminar_usuario_view, name='eliminar_usuario'),
    path("usuarios/editar/<int:usuario_id>/", UsuarioUpdateAJAXView.as_view(), name="editar_usuario"),


    path("agregar_empleado/", EmpleadoCreateAJAXView.as_view(), name="agregar_empleado"),
    path("autocomplete/usuario/",   UsuarioDisponibleAutocomplete.as_view(), name="usuario_autocomplete"),
    path("autocomplete/sucursal/",  SucursalAutocomplete.as_view(), name="sucursal_autocomplete"),
    path("visualizar_empleados/",EmpleadoListView.as_view(), name="visualizar_empleados"),
    path("empleados/editar/<int:empleado_id>/", views.EmpleadoUpdateAJAXView.as_view(), name="editar_empleado"),
    path('eliminar_empleado/<int:empleado_id>/', views.eliminar_empleado_view, name='eliminar_empleado'),

    path("agregar_horario/", HorarioCreateAJAXView.as_view(),name="agregar_horario"),
    path("autocomplete/sucursal_horario/", SucursalSinHorarioAutocomplete.as_view(), name="horarios_sucursal_autocomplete"),
    path("visualizar_horarios/", HorariosListView.as_view(), name="visualizar_horarios"),
    path("autocomplete/sucursal_horario_visualizar/", SucursalConHorariosAutocomplete.as_view(), name="sucursal_horario_visualizar_autocomplete"),
    path("editar_horarios/<int:sucursal_id>/", HorarioUpdateAJAXView.as_view(), name="editar_horarios"),
    path('eliminar_horario/<int:horario_id>/', views.eliminar_horario_view, name='eliminar_horario'),

    path("agregar_horario_caja/",HorarioCajaCreateAJAXView.as_view(),name="agregar_horario_caja"),
    path("autocomplete/caja/sucursal/", views.SucursalAgregarHorarioCajaAutocomplete.as_view(), name="sucursal_horario_caja_autocomplete"),
    path("autocomplete/caja/puntopago/", views.PuntosPagoAgregarHorarioCajaAutocomplete.as_view(), name="puntopago_horario_caja_autocomplete"),
    path("visualizar_horarios_cajas/", views.VisualizarHorariosCajasView.as_view(), name="visualizar_horarios_cajas"),
    path("autocomplete/sucursal_horarios_cajas/", views.SucursalHorarioCajaAutocomplete.as_view(), name="visualizar_horarios_cajas_sucursal_autocomplete"),
    path("autocomplete/puntopago_horarios_cajas/", views.PuntoPagoHorarioCajaAutocomplete.as_view(), name="visualizar_horarios_cajas_puntopago_autocomplete"),
    path('eliminar_horario_caja/<int:horario_id>/', views.eliminar_horario_caja_view, name='eliminar_horario_caja'),
    path("editar_horarios_cajas/<int:puntopagoid>/", views.EditarHorarioCajaView.as_view(), name="editar_horarios_cajas"),
    path("autocomplete/sucursal_caja_editar/", views.SucursalDisponibleCajaAutocomplete.as_view(), name="sucursal_caja_editar_autocomplete"),
    path("autocomplete/puntopago_caja_editar/", views.PuntoCajaDisponibleAutocomplete.as_view(), name="puntopago_caja_editar_autocomplete"),


    path("agregar_cliente/", views.ClienteCreateAJAXView.as_view(), name="agregar_cliente"),
    path("visualizar_clientes/", views.ClienteListView.as_view(), name="visualizar_clientes"),
    path("clientes/editar/<int:cliente_id>/",views.ClienteUpdateAJAXView.as_view(),name="editar_cliente"),
    path('eliminar_cliente/<int:clienteid>/', views.eliminar_cliente, name='eliminar_cliente'),


    path("generar_venta/", views.GenerarVentaView.as_view(), name="generar_venta"),
    path("autocomplete/producto-id/", views.ProductoIdAutocompleteView.as_view(), name="producto_autocomplete_id"),
    path("api/productos/snapshot/", views.ProductoSnapshotView.as_view(), name="producto_snapshot"),
    path("ventas/imprimir/", views.ImprimirFacturaView.as_view(), name="imprimir_factura"),
    path("ventas/abrir-caja/", views.AbrirCajaView.as_view(),      name="abrir_caja"),

    # Autocompletes
    path('autocomplete/sucursal/',   views.SucursalAutocompleteView.as_view(),
         name='sucursal_autocomplete'),

    path('autocomplete/puntopago/',  views.PuntoPagoAutocompleteView.as_view(),
         name='puntopago_autocomplete'),

    path('autocomplete/cliente/',    views.ClienteAutocompleteView.as_view(),
         name='cliente_autocomplete'),

    path('autocomplete/producto/',   views.ProductoAutocompleteView.as_view(),
         name='producto_autocomplete'),

    # AJAX utilitarios
    path("verificar_producto/",       views.VerificarProductoView.as_view(),       name="verificar_producto"),
    path("producto_por_codigo/",      views.BuscarProductoPorCodigoView.as_view(), name="buscar_producto_por_codigo"),
    path('autocomplete/producto-codigo/',  views.ProductoCodigoAutocompleteView.as_view(),  name='producto_autocomplete_codigo'),
    path('autocomplete/producto-barras/',  views.ProductoBarrasAutocompleteView.as_view(),  name='producto_autocomplete_barras'),

    path('visualizar_ventas/', views.VentaListView.as_view(), name='visualizar_ventas'),
    path("ventas/data/", views.VentaDataTableView.as_view(), name="ventas_datatable"),
    #  … otras urls …
    path('ver_venta/<int:venta_id>/', views.VentaDetailView.as_view(), name='ver_venta'),
    path("ventas/ticket-texto/", views.TicketTextoView.as_view(), name="ticket_texto"),
    path("cambios/", views.CambiosListView.as_view(), name="visualizar_cambios"),
    path("ventas/imprimir/", views.ImprimirFacturaView.as_view(), name="imprimir_factura"),



    path("agregar_pedido/", views.PedidoProveedorCreateAJAXView.as_view(), name="agregar_pedido"),
    path("autocomplete/producto_pedido/", views.ProductoPedidoAutocomplete.as_view(), name="producto_pedido_autocomplete"),
    path("visualizar_pedidos/", views.PedidoListView.as_view(), name="visualizar_pedidos"),
    path('eliminar_pedido/<int:pedido_id>/', views.eliminar_pedido, name='eliminar_pedido'),
    path('ver_pedido/<int:pedido_id>/', views.PedidoDetailView.as_view(), name='ver_pedido'),
    path('editar_pedido/<int:pedido_id>/', views.EditarPedidoView.as_view(), name='editar_pedido'),
    path(
      "autocomplete/puntopago/",
      views.PuntoPagoPorSucursalAutocomplete.as_view(),
      name="puntopago_autocomplete"
    ),

     path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),

     path("permisos/agregar/", views.PermisoCreateView.as_view(), name="permiso_agregar"),
     path("visualizar_permisos/", views.PermisoListView.as_view(), name="visualizar_permisos"),
     path("permisos/editar/<int:permiso_id>/",
         views.PermisoUpdateAJAXView.as_view(),
         name="editar_permiso"),
     path("permisos/<int:pk>/eliminar/", views.eliminar_permiso, name="eliminar_permiso"),



    path("roles_permisos/", views.RolPermisoAssignView.as_view(), name="roles_permisos"),
    path("autocomplete/rol/", views.RolAutocomplete.as_view(), name="rol_autocomplete"),
    path("autocomplete/permiso/", views.PermisoAutocomplete.as_view(), name="permiso_autocomplete"),
    path("roles_permisos/visualizar/", views.VisualizarRolesPermisosView.as_view(),
     name="visualizar_roles_permisos"),
    path("autocomplete/rol_con_permisos/", views.RolConPermisosAutocomplete.as_view(),
     name="rol_con_permisos_autocomplete"),
    path("roles_permisos/eliminar/<int:rp_id>/",
     views.eliminar_rol_permiso_view, name="eliminar_rol_permiso"),
    # Editar roles ↔ permisos
     path("roles_permisos/editar/<int:rol_id>/", views.RolesPermisosEditView.as_view(),
         name="editar_roles_permisos"),

     path("autocomplete/permiso_para_rol/", views.PermisoParaRolAutocomplete.as_view(),
         name="permiso_para_rol_autocomplete"),
     # eliminar relación ya existente en visualizar:
     path("roles_permisos/eliminar/<int:rp_id>/", views.eliminar_rol_permiso_view,
         name="eliminar_rol_permiso"),

     path("ventas_diarias/", views.VentasDiariasView.as_view(), name="ventas_diarias"),
     path("autocomplete/sucursal_ventas/", views.SucursalParaVentasAutocomplete.as_view(),
     name="sucursal_ventas_autocomplete"),
     path("autocomplete/puntopago_ventas/", views.PuntoPagoParaVentasAutocomplete.as_view(),
     name="puntopago_ventas_autocomplete"),
     path("ventas/diarias/stats/", views.VentasDiariasStatsView.as_view(), name="ventas_diarias_stats"),


     # --- Reporte: pedidos pagados ---
     path("reportes/pedidos_pagados/", views.PedidosPagadosView.as_view(),
          name="pedidos_pagados"),

     # Autocomplete (solo sucursales con pedidos recibidos/pagados)
     path("autocomplete/sucursal_con_pedidos_pagados/",
          views.SucursalConPedidosPagadosAutocomplete.as_view(),
          name="sucursal_con_pedidos_pagados_autocomplete"),

     # Autocomplete de puntos de pago, filtrado por sucursal
     path("autocomplete/puntopago_con_pedidos_pagados/",
          views.PuntosPagoConPedidosPagadosAutocomplete.as_view(),
          name="puntopago_con_pedidos_pagados_autocomplete"),




     path("turno_caja/", views.TurnoCajaPageView.as_view(), name="turno_caja"),

     path("turnos_caja/recuperar_o_iniciar/",
         views.TurnoCajaRecuperarOIniciarView.as_view(),
         name="turno_recuperar_o_iniciar"),

    path("turno_caja/autocomplete/puntopago/", views.PuntoPagoAutocomplete.as_view(),
         name="turno_caja_puntopago_ac"),
    path("turno_caja/autocomplete/cajero/", views.CajeroAutocomplete.as_view(),
         name="turno_caja_cajero_ac"),

    path("turno_caja/api/iniciar/", views.TurnoCajaIniciarApi.as_view(),
         name="turno_caja_iniciar"),
    path("turno_caja/api/iniciar_cierre/", views.TurnoCajaIniciarCierreApi.as_view(),
         name="turno_caja_iniciar_cierre"),
    path("turno_caja/api/cerrar/", views.TurnoCajaCerrarApi.as_view(),
         name="turno_caja_cerrar"),
     
     path("turnos_caja_dashboard/", views.TurnosCajaDashboardView.as_view(), name="turnos_caja_dashboard"),

    # APIs dashboard
    path("api/turnos_caja/list/", views.TurnosCajaDashboardListAPI.as_view(), name="api_turnos_caja_list"),
    path("api/turnos_caja/<int:turno_id>/", views.TurnoCajaDashboardDetailAPI.as_view(), name="api_turno_caja_detail"),

    path("turnos_caja_admin/", views.TurnosCajaAdminPageView.as_view(), name="turnos_caja_admin"),

    # APIs Admin
    path("api/admin/turnos_caja/<int:turno_id>/", views.TurnoCajaAdminDetailAPI.as_view(), name="api_admin_turno_detail"),
    path("api/admin/turnos_caja/<int:turno_id>/update/", views.TurnoCajaAdminUpdateAPI.as_view(), name="api_admin_turno_update"),
    path("api/admin/turnos_caja/<int:turno_id>/delete/", views.TurnoCajaAdminDeleteAPI.as_view(), name="api_admin_turno_delete"),





    path("a", views.GestionInventarioMasivaView.as_view(), name="inventario_masivo"),

    path("autocomplete/sucursales/", views.SucursalAutocompleteView.as_view(), name="sucursal_autocomplete_simple"),

    path("autocomplete/productos/nombre/", views.ProductoBuscarNombreView.as_view(), name="producto_buscar_nombre_simple"),
    path("autocomplete/productos/barras/", views.ProductoBuscarBarrasView.as_view(), name="producto_buscar_barras_simple"),
    path("autocomplete/productos/id/", views.ProductoBuscarIdView.as_view(), name="producto_buscar_id_simple"),

    path("inventario/producto_detalle/", views.ProductoDetalleInventarioView.as_view(), name="producto_detalle_inventario"),



    path("v", views.VisorProductoBarcodeView.as_view(), name="visor_barcode"),
    path("visor/barcode/buscar/", views.ProductoBuscarBarrasVisorView.as_view(), name="visor_barcode_buscar"),
    path("visor/barcode/lookup/", views.ProductoLookupPorBarrasVisorView.as_view(), name="visor_barcode_lookup"),



    path(
        "reportes/ventas-producto/",
        views.VentasProductoRangoView.as_view(),
        name="reporte_ventas_producto",
    ),

    # ✅ Endpoint stats (sin ID en la URL) -> sucursal_id via GET
    # /ventas/producto/stats/?sucursal_id=1&productoid=123&desde=2026-02-01&hasta=2026-02-16
    path(
        "ventas/producto/stats/",
        views.ProductoVentasStatsAjaxView.as_view(),
        name="producto_ventas_stats",
    ),

    # ✅ DataTables server-side (sin ID en la URL) -> sucursal_id via GET
    # /api/reportes/ventas-producto/?sucursal_id=1&fecha_ini=...&fecha_fin=...
    path(
        "api/reportes/ventas-producto/",
        views.VentasProductoRangoDataView.as_view(),
        name="ventas_producto_data",
    ),
]
