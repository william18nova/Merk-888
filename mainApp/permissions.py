import re
import unicodedata
from typing import Dict, List, Optional, Set

from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse

from .models import Permiso, Rol, RolPermiso


ADMIN_ROLE_NAMES = {"admin", "administrador", "supervisor"}
PUBLIC_URL_NAMES = {"login", "logout", "visor_barcode", "visor_barcode_buscar", "visor_barcode_lookup"}
ALWAYS_ALLOWED_URL_NAMES = {"home"}


PERMISSION_DEFINITIONS = [
    {
        "code": "sucursales_crear",
        "label": "Agregar sucursal",
        "description": "Permite crear sucursales.",
        "aliases": ["agregar_sucursal", "crear sucursal"],
    },
    {
        "code": "sucursales_ver",
        "label": "Visualizar sucursales",
        "description": "Permite ver el listado de sucursales.",
        "aliases": ["visualizar_sucursales", "ver sucursales"],
    },
    {
        "code": "sucursales_editar",
        "label": "Editar sucursal",
        "description": "Permite modificar sucursales.",
        "aliases": ["editar_sucursal"],
    },
    {
        "code": "sucursales_eliminar",
        "label": "Eliminar sucursal",
        "description": "Permite eliminar sucursales.",
        "aliases": ["eliminar_sucursal"],
    },
    {
        "code": "categorias_crear",
        "label": "Agregar categoria",
        "description": "Permite crear categorias.",
        "aliases": ["agregar_categoria"],
    },
    {
        "code": "categorias_ver",
        "label": "Visualizar categorias",
        "description": "Permite ver categorias.",
        "aliases": ["visualizar_categorias", "ver categorias"],
    },
    {
        "code": "categorias_editar",
        "label": "Editar categoria",
        "description": "Permite modificar categorias.",
        "aliases": ["editar_categoria"],
    },
    {
        "code": "categorias_eliminar",
        "label": "Eliminar categoria",
        "description": "Permite eliminar categorias.",
        "aliases": ["eliminar_categoria"],
    },
    {
        "code": "productos_crear",
        "label": "Agregar producto",
        "description": "Permite crear productos.",
        "aliases": ["agregar_producto"],
    },
    {
        "code": "productos_ver",
        "label": "Visualizar productos",
        "description": "Permite ver productos.",
        "aliases": ["visualizar_productos", "productos_datatable", "ver productos"],
    },
    {
        "code": "productos_editar",
        "label": "Editar producto",
        "description": "Permite modificar productos.",
        "aliases": ["editar_producto"],
    },
    {
        "code": "productos_eliminar",
        "label": "Eliminar producto",
        "description": "Permite eliminar productos.",
        "aliases": ["eliminar_producto"],
    },
    {
        "code": "inventarios_crear",
        "label": "Agregar inventario",
        "description": "Permite crear inventario.",
        "aliases": ["agregar_inventario"],
    },
    {
        "code": "inventarios_ver",
        "label": "Visualizar inventarios",
        "description": "Permite ver inventarios.",
        "aliases": ["visualizar_inventarios", "ver inventarios"],
    },
    {
        "code": "inventarios_editar",
        "label": "Editar inventario",
        "description": "Permite modificar inventarios.",
        "aliases": ["editar_inventario", "inventario_masivo", "gestion inventario masiva"],
    },
    {
        "code": "inventarios_eliminar",
        "label": "Eliminar inventario",
        "description": "Permite eliminar productos de inventario.",
        "aliases": ["eliminar_producto_inventario"],
    },
    {
        "code": "proveedores_crear",
        "label": "Agregar proveedor",
        "description": "Permite crear proveedores.",
        "aliases": ["agregar_proveedor"],
    },
    {
        "code": "proveedores_ver",
        "label": "Visualizar proveedores",
        "description": "Permite ver proveedores.",
        "aliases": ["visualizar_proveedores", "ver proveedores"],
    },
    {
        "code": "proveedores_editar",
        "label": "Editar proveedor",
        "description": "Permite modificar proveedores.",
        "aliases": ["editar_proveedor"],
    },
    {
        "code": "proveedores_eliminar",
        "label": "Eliminar proveedor",
        "description": "Permite eliminar proveedores.",
        "aliases": ["eliminar_proveedor"],
    },
    {
        "code": "precios_proveedor_crear",
        "label": "Agregar precios proveedor",
        "description": "Permite crear precios de proveedor.",
        "aliases": ["agregar_productos_precios_proveedor"],
    },
    {
        "code": "precios_proveedor_ver",
        "label": "Visualizar precios proveedor",
        "description": "Permite ver precios de proveedor.",
        "aliases": ["visualizar_productos_precios_proveedores"],
    },
    {
        "code": "precios_proveedor_editar",
        "label": "Editar precios proveedor",
        "description": "Permite modificar precios de proveedor.",
        "aliases": ["editar_productos_precios_proveedor"],
    },
    {
        "code": "precios_proveedor_eliminar",
        "label": "Eliminar precios proveedor",
        "description": "Permite eliminar precios de proveedor.",
        "aliases": ["eliminar_precio_proveedor"],
    },
    {
        "code": "puntos_pago_crear",
        "label": "Agregar punto de pago",
        "description": "Permite crear puntos de pago.",
        "aliases": ["agregar_punto_pago"],
    },
    {
        "code": "puntos_pago_ver",
        "label": "Visualizar puntos de pago",
        "description": "Permite ver puntos de pago.",
        "aliases": ["visualizar_puntos_pago"],
    },
    {
        "code": "puntos_pago_editar",
        "label": "Editar punto de pago",
        "description": "Permite modificar puntos de pago.",
        "aliases": ["editar_puntos_pago"],
    },
    {
        "code": "puntos_pago_eliminar",
        "label": "Eliminar punto de pago",
        "description": "Permite eliminar puntos de pago.",
        "aliases": ["eliminar_punto_pago"],
    },
    {
        "code": "roles_crear",
        "label": "Agregar rol",
        "description": "Permite crear roles.",
        "aliases": ["agregar_rol"],
    },
    {
        "code": "roles_ver",
        "label": "Visualizar roles",
        "description": "Permite ver roles.",
        "aliases": ["visualizar_roles"],
    },
    {
        "code": "roles_editar",
        "label": "Editar rol",
        "description": "Permite modificar roles.",
        "aliases": ["editar_rol"],
    },
    {
        "code": "roles_eliminar",
        "label": "Eliminar rol",
        "description": "Permite eliminar roles.",
        "aliases": ["eliminar_rol"],
    },
    {
        "code": "usuarios_crear",
        "label": "Agregar usuario",
        "description": "Permite crear usuarios.",
        "aliases": ["agregar_usuario"],
    },
    {
        "code": "usuarios_ver",
        "label": "Visualizar usuarios",
        "description": "Permite ver usuarios.",
        "aliases": ["visualizar_usuarios"],
    },
    {
        "code": "usuarios_editar",
        "label": "Editar usuario",
        "description": "Permite modificar usuarios.",
        "aliases": ["editar_usuario"],
    },
    {
        "code": "usuarios_eliminar",
        "label": "Eliminar usuario",
        "description": "Permite eliminar usuarios.",
        "aliases": ["eliminar_usuario"],
    },
    {
        "code": "empleados_crear",
        "label": "Agregar empleado",
        "description": "Permite crear empleados.",
        "aliases": ["agregar_empleado"],
    },
    {
        "code": "empleados_ver",
        "label": "Visualizar empleados",
        "description": "Permite ver empleados.",
        "aliases": ["visualizar_empleados"],
    },
    {
        "code": "empleados_editar",
        "label": "Editar empleado",
        "description": "Permite modificar empleados.",
        "aliases": ["editar_empleado"],
    },
    {
        "code": "empleados_eliminar",
        "label": "Eliminar empleado",
        "description": "Permite eliminar empleados.",
        "aliases": ["eliminar_empleado"],
    },
    {
        "code": "horarios_crear",
        "label": "Agregar horario",
        "description": "Permite crear horarios.",
        "aliases": ["agregar_horario"],
    },
    {
        "code": "horarios_ver",
        "label": "Visualizar horarios",
        "description": "Permite ver horarios.",
        "aliases": ["visualizar_horarios"],
    },
    {
        "code": "horarios_editar",
        "label": "Editar horario",
        "description": "Permite modificar horarios.",
        "aliases": ["editar_horarios"],
    },
    {
        "code": "horarios_eliminar",
        "label": "Eliminar horario",
        "description": "Permite eliminar horarios.",
        "aliases": ["eliminar_horario"],
    },
    {
        "code": "horarios_caja_crear",
        "label": "Agregar horario de caja",
        "description": "Permite crear horarios de caja.",
        "aliases": ["agregar_horario_caja"],
    },
    {
        "code": "horarios_caja_ver",
        "label": "Visualizar horarios de caja",
        "description": "Permite ver horarios de caja.",
        "aliases": ["visualizar_horarios_cajas"],
    },
    {
        "code": "horarios_caja_editar",
        "label": "Editar horario de caja",
        "description": "Permite modificar horarios de caja.",
        "aliases": ["editar_horarios_cajas"],
    },
    {
        "code": "horarios_caja_eliminar",
        "label": "Eliminar horario de caja",
        "description": "Permite eliminar horarios de caja.",
        "aliases": ["eliminar_horario_caja"],
    },
    {
        "code": "clientes_crear",
        "label": "Agregar cliente",
        "description": "Permite crear clientes.",
        "aliases": ["agregar_cliente"],
    },
    {
        "code": "clientes_ver",
        "label": "Visualizar clientes",
        "description": "Permite ver clientes.",
        "aliases": ["visualizar_clientes"],
    },
    {
        "code": "clientes_editar",
        "label": "Editar cliente",
        "description": "Permite modificar clientes.",
        "aliases": ["editar_cliente"],
    },
    {
        "code": "clientes_eliminar",
        "label": "Eliminar cliente",
        "description": "Permite eliminar clientes.",
        "aliases": ["eliminar_cliente"],
    },
    {
        "code": "ventas_generar",
        "label": "Generar venta",
        "description": "Permite registrar ventas.",
        "aliases": ["generar_venta", "abrir_caja"],
    },
    {
        "code": "ventas_ver",
        "label": "Visualizar ventas",
        "description": "Permite ver ventas y facturas.",
        "aliases": ["visualizar_ventas", "ver_venta", "ventas_datatable"],
    },
    {
        "code": "ventas_cambios",
        "label": "Cambios y devoluciones",
        "description": "Permite ver y gestionar cambios o devoluciones.",
        "aliases": ["visualizar_cambios", "cambios devoluciones"],
    },
    {
        "code": "ventas_diarias",
        "label": "Ventas diarias",
        "description": "Permite consultar ventas diarias.",
        "aliases": ["ventas_diarias", "ventas_diarias_stats"],
    },
    {
        "code": "reportes_ventas_producto",
        "label": "Ventas por producto",
        "description": "Permite consultar ventas por producto.",
        "aliases": ["reporte_ventas_producto", "ventas_producto_data", "producto_ventas_stats"],
    },
    {
        "code": "pedidos_crear",
        "label": "Agregar pedido",
        "description": "Permite crear pedidos a proveedores.",
        "aliases": ["agregar_pedido"],
    },
    {
        "code": "pedidos_ver",
        "label": "Visualizar pedidos",
        "description": "Permite ver pedidos.",
        "aliases": ["visualizar_pedidos", "ver_pedido"],
    },
    {
        "code": "pedidos_editar",
        "label": "Editar pedido",
        "description": "Permite modificar pedidos.",
        "aliases": ["editar_pedido"],
    },
    {
        "code": "pedidos_eliminar",
        "label": "Eliminar pedido",
        "description": "Permite eliminar pedidos.",
        "aliases": ["eliminar_pedido"],
    },
    {
        "code": "reportes_pedidos_pagados",
        "label": "Pedidos pagados",
        "description": "Permite consultar pedidos pagados.",
        "aliases": ["pedidos_pagados"],
    },
    {
        "code": "caja_turno",
        "label": "Turno de caja",
        "description": "Permite operar el turno de caja propio.",
        "aliases": ["turno_caja", "turno_recuperar_o_iniciar"],
    },
    {
        "code": "caja_dashboard",
        "label": "Dashboard turnos",
        "description": "Permite consultar el dashboard de turnos de caja.",
        "aliases": ["turnos_caja_dashboard"],
    },
    {
        "code": "caja_admin",
        "label": "Administrar turnos",
        "description": "Permite editar, cerrar o eliminar turnos desde admin.",
        "aliases": ["turnos_caja_admin", "admin turnos"],
    },
    {
        "code": "seguridad_permisos",
        "label": "Administrar permisos",
        "description": "Permite crear permisos y asignarlos a roles o usuarios.",
        "aliases": ["permiso_agregar", "visualizar_permisos", "roles_permisos", "usuarios_permisos"],
    },
    {
        "code": "visor_barcode",
        "label": "Visor Barcode",
        "description": "Permite usar el visor de codigos de barras.",
        "aliases": ["visor_barcode"],
    },
]


PERMISSION_BY_CODE = {item["code"]: item for item in PERMISSION_DEFINITIONS}


ROUTE_PERMISSIONS = {
    "agregar_sucursal": "sucursales_crear",
    "visualizar_sucursales": "sucursales_ver",
    "editar_sucursal": "sucursales_editar",
    "eliminar_sucursal": "sucursales_eliminar",
    "agregar_categoria": "categorias_crear",
    "visualizar_categorias": "categorias_ver",
    "editar_categoria": "categorias_editar",
    "eliminar_categoria": "categorias_eliminar",
    "agregar_producto": "productos_crear",
    "visualizar_productos": "productos_ver",
    "productos_datatable": "productos_ver",
    "editar_producto": "productos_editar",
    "eliminar_producto": "productos_eliminar",
    "agregar_inventario": "inventarios_crear",
    "visualizar_inventarios": "inventarios_ver",
    "editar_inventario": "inventarios_editar",
    "inventario_masivo": "inventarios_editar",
    "inventario_item_ajax": "inventarios_editar",
    "producto_detalle_inventario": "inventarios_ver",
    "eliminar_producto_inventario": "inventarios_eliminar",
    "agregar_proveedor": "proveedores_crear",
    "visualizar_proveedores": "proveedores_ver",
    "editar_proveedor": "proveedores_editar",
    "eliminar_proveedor": "proveedores_eliminar",
    "agregar_productos_precios_proveedor": "precios_proveedor_crear",
    "visualizar_productos_precios_proveedores": "precios_proveedor_ver",
    "editar_productos_precios_proveedor": "precios_proveedor_editar",
    "eliminar_precio_proveedor": "precios_proveedor_eliminar",
    "agregar_punto_pago": "puntos_pago_crear",
    "visualizar_puntos_pago": "puntos_pago_ver",
    "editar_puntos_pago": "puntos_pago_editar",
    "eliminar_punto_pago": "puntos_pago_eliminar",
    "agregar_rol": "roles_crear",
    "visualizar_roles": "roles_ver",
    "editar_rol": "roles_editar",
    "eliminar_rol": "roles_eliminar",
    "agregar_usuario": "usuarios_crear",
    "visualizar_usuarios": "usuarios_ver",
    "editar_usuario": "usuarios_editar",
    "eliminar_usuario": "usuarios_eliminar",
    "agregar_empleado": "empleados_crear",
    "visualizar_empleados": "empleados_ver",
    "editar_empleado": "empleados_editar",
    "eliminar_empleado": "empleados_eliminar",
    "agregar_horario": "horarios_crear",
    "visualizar_horarios": "horarios_ver",
    "editar_horarios": "horarios_editar",
    "eliminar_horario": "horarios_eliminar",
    "agregar_horario_caja": "horarios_caja_crear",
    "visualizar_horarios_cajas": "horarios_caja_ver",
    "editar_horarios_cajas": "horarios_caja_editar",
    "eliminar_horario_caja": "horarios_caja_eliminar",
    "agregar_cliente": "clientes_crear",
    "visualizar_clientes": "clientes_ver",
    "editar_cliente": "clientes_editar",
    "eliminar_cliente": "clientes_eliminar",
    "generar_venta": "ventas_generar",
    "producto_snapshot": "ventas_generar",
    "verificar_producto": "ventas_generar",
    "buscar_producto_por_codigo": "ventas_generar",
    "abrir_caja": "ventas_generar",
    "imprimir_factura": "ventas_ver",
    "ticket_texto": "ventas_ver",
    "visualizar_ventas": "ventas_ver",
    "ventas_datatable": "ventas_ver",
    "ver_venta": "ventas_ver",
    "visualizar_cambios": "ventas_cambios",
    "ventas_diarias": "ventas_diarias",
    "ventas_diarias_stats": "ventas_diarias",
    "reporte_ventas_producto": "reportes_ventas_producto",
    "ventas_producto_data": "reportes_ventas_producto",
    "producto_ventas_stats": "reportes_ventas_producto",
    "agregar_pedido": "pedidos_crear",
    "visualizar_pedidos": "pedidos_ver",
    "ver_pedido": "pedidos_ver",
    "editar_pedido": "pedidos_editar",
    "eliminar_pedido": "pedidos_eliminar",
    "pedidos_pagados": "reportes_pedidos_pagados",
    "turno_caja": "caja_turno",
    "turno_recuperar_o_iniciar": "caja_turno",
    "turno_caja_iniciar": "caja_turno",
    "turno_caja_iniciar_cierre": "caja_turno",
    "turno_caja_cerrar": "caja_turno",
    "turnos_caja_dashboard": "caja_dashboard",
    "api_turnos_caja_list": "caja_dashboard",
    "api_turno_caja_detail": "caja_dashboard",
    "turnos_caja_admin": "caja_admin",
    "api_admin_turno_detail": "caja_admin",
    "api_admin_turno_update": "caja_admin",
    "api_admin_turno_delete": "caja_admin",
    "permiso_agregar": "seguridad_permisos",
    "visualizar_permisos": "seguridad_permisos",
    "editar_permiso": "seguridad_permisos",
    "eliminar_permiso": "seguridad_permisos",
    "roles_permisos": "seguridad_permisos",
    "visualizar_roles_permisos": "seguridad_permisos",
    "editar_roles_permisos": "seguridad_permisos",
    "eliminar_rol_permiso": "seguridad_permisos",
    "usuarios_permisos": "seguridad_permisos",
    "visor_barcode": "visor_barcode",
    "visor_barcode_buscar": "visor_barcode",
    "visor_barcode_lookup": "visor_barcode",
}


NAV_GROUPS = [
    {"label": "Inicio", "url_name": "home"},
    {
        "label": "Sucursales",
        "children": [
            {"label": "Agregar sucursal", "url_name": "agregar_sucursal"},
            {"label": "Visualizar sucursales", "url_name": "visualizar_sucursales"},
        ],
    },
    {
        "label": "Categorias",
        "children": [
            {"label": "Agregar categoria", "url_name": "agregar_categoria"},
            {"label": "Visualizar categorias", "url_name": "visualizar_categorias"},
        ],
    },
    {
        "label": "Productos",
        "children": [
            {"label": "Agregar producto", "url_name": "agregar_producto"},
            {"label": "Visualizar productos", "url_name": "visualizar_productos"},
        ],
    },
    {
        "label": "Inventarios",
        "children": [
            {"label": "Agregar inventario", "url_name": "agregar_inventario"},
            {"label": "Visualizar inventarios", "url_name": "visualizar_inventarios"},
            {"label": "Inventario masivo", "url_name": "inventario_masivo"},
        ],
    },
    {
        "label": "Proveedores",
        "children": [
            {"label": "Agregar proveedor", "url_name": "agregar_proveedor"},
            {"label": "Visualizar proveedores", "url_name": "visualizar_proveedores"},
        ],
    },
    {
        "label": "Precios Proveedor",
        "children": [
            {"label": "Agregar precios", "url_name": "agregar_productos_precios_proveedor"},
            {"label": "Visualizar precios", "url_name": "visualizar_productos_precios_proveedores"},
        ],
    },
    {
        "label": "Puntos de Pago",
        "children": [
            {"label": "Agregar punto de pago", "url_name": "agregar_punto_pago"},
            {"label": "Visualizar puntos", "url_name": "visualizar_puntos_pago"},
        ],
    },
    {
        "label": "Usuarios",
        "children": [
            {"label": "Agregar usuario", "url_name": "agregar_usuario"},
            {"label": "Visualizar usuarios", "url_name": "visualizar_usuarios"},
        ],
    },
    {
        "label": "Empleados",
        "children": [
            {"label": "Agregar empleado", "url_name": "agregar_empleado"},
            {"label": "Visualizar empleados", "url_name": "visualizar_empleados"},
        ],
    },
    {
        "label": "Horarios",
        "children": [
            {"label": "Agregar horario", "url_name": "agregar_horario"},
            {"label": "Visualizar horarios", "url_name": "visualizar_horarios"},
        ],
    },
    {
        "label": "Horarios de Cajas",
        "children": [
            {"label": "Agregar horario de caja", "url_name": "agregar_horario_caja"},
            {"label": "Visualizar horarios de cajas", "url_name": "visualizar_horarios_cajas"},
        ],
    },
    {
        "label": "Clientes",
        "children": [
            {"label": "Agregar cliente", "url_name": "agregar_cliente"},
            {"label": "Visualizar clientes", "url_name": "visualizar_clientes"},
        ],
    },
    {
        "label": "Ventas",
        "children": [
            {"label": "Generar venta", "url_name": "generar_venta"},
            {"label": "Visualizar ventas", "url_name": "visualizar_ventas"},
            {"label": "Cambios / Devoluciones", "url_name": "visualizar_cambios"},
            {"label": "Ventas diarias", "url_name": "ventas_diarias"},
            {"label": "Ventas por producto", "url_name": "reporte_ventas_producto"},
        ],
    },
    {
        "label": "Pedidos",
        "children": [
            {"label": "Agregar pedido", "url_name": "agregar_pedido"},
            {"label": "Visualizar pedidos", "url_name": "visualizar_pedidos"},
            {"label": "Pedidos pagados", "url_name": "pedidos_pagados"},
        ],
    },
    {
        "label": "Caja",
        "children": [
            {"label": "Turno de caja", "url_name": "turno_caja"},
            {"label": "Dashboard turnos", "url_name": "turnos_caja_dashboard"},
            {"label": "Admin turnos", "url_name": "turnos_caja_admin"},
            {"label": "Abrir caja (ventas)", "url_name": "abrir_caja"},
        ],
    },
    {
        "label": "Seguridad",
        "children": [
            {"label": "Agregar rol", "url_name": "agregar_rol"},
            {"label": "Visualizar roles", "url_name": "visualizar_roles"},
            {"label": "Agregar permiso", "url_name": "permiso_agregar"},
            {"label": "Visualizar permisos", "url_name": "visualizar_permisos"},
            {"label": "Asignar roles-permisos", "url_name": "roles_permisos"},
            {"label": "Visualizar relaciones", "url_name": "visualizar_roles_permisos"},
            {"label": "Permisos por usuario", "url_name": "usuarios_permisos"},
        ],
    },
    {"label": "Visor Barcode", "url_name": "visor_barcode"},
]


def normalize_permission_key(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _permission_keys_for_definition(code: str) -> Set[str]:
    definition = PERMISSION_BY_CODE.get(code, {})
    raw_values = [code, definition.get("label", ""), *definition.get("aliases", [])]
    return {normalize_permission_key(value) for value in raw_values if value}


def _keys_for_db_permission(permission_name: str) -> Set[str]:
    key = normalize_permission_key(permission_name)
    keys = {key} if key else set()
    for definition in PERMISSION_DEFINITIONS:
        if key in _permission_keys_for_definition(definition["code"]):
            keys.add(definition["code"])
            keys.update(_permission_keys_for_definition(definition["code"]))
    return keys


def permission_catalog() -> List[Dict[str, object]]:
    return list(PERMISSION_DEFINITIONS)


def sync_permission_catalog() -> int:
    created = 0
    existing_by_key = {
        normalize_permission_key(permission.nombre): permission
        for permission in Permiso.objects.all()
    }
    for definition in PERMISSION_DEFINITIONS:
        candidates = [definition["label"], definition["code"], *definition.get("aliases", [])]
        existing = None
        for candidate in candidates:
            existing = existing_by_key.get(normalize_permission_key(candidate))
            if existing:
                break
        if existing:
            if not existing.descripcion:
                existing.descripcion = definition["description"]
                existing.save(update_fields=["descripcion"])
            continue
        permission = Permiso.objects.create(
            nombre=definition["label"],
            descripcion=definition["description"],
        )
        existing_by_key[normalize_permission_key(permission.nombre)] = permission
        created += 1
    return created


def grant_all_permissions_to_web_master(role_id: int = 1) -> int:
    sync_permission_catalog()
    role = Rol.objects.filter(pk=role_id).first()
    if not role or normalize_permission_key(role.nombre) != "web_master":
        return 0

    permissions = list(Permiso.objects.all())
    existing_ids = set(
        RolPermiso.objects
        .filter(rol=role)
        .values_list("permiso_id", flat=True)
    )
    missing = [
        RolPermiso(rol=role, permiso=permission)
        for permission in permissions
        if permission.pk not in existing_ids
    ]
    if missing:
        RolPermiso.objects.bulk_create(missing, ignore_conflicts=True)
    return len(missing)


def role_name(user) -> str:
    try:
        return (getattr(getattr(user, "rolid", None), "nombre", "") or "").strip()
    except Exception:
        return ""


def is_permission_admin(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return normalize_permission_key(role_name(user)) in ADMIN_ROLE_NAMES


def _load_permission_state(user) -> Dict[str, Set[str]]:
    cache_name = "_mainapp_permission_state"
    cached = getattr(user, cache_name, None)
    if cached is not None:
        return cached

    state = {"role": set(), "allow": set(), "deny": set()}
    if not getattr(user, "is_authenticated", False):
        setattr(user, cache_name, state)
        return state

    try:
        role_id = getattr(user, "rolid_id", None)
        if role_id:
            role_names = (
                RolPermiso.objects
                .filter(rol_id=role_id)
                .select_related("permiso")
                .values_list("permiso__nombre", flat=True)
            )
            for permission_name in role_names:
                state["role"].update(_keys_for_db_permission(permission_name))
    except DatabaseError:
        state["role"] = set()

    try:
        from .models import UsuarioPermiso

        direct_rows = (
            UsuarioPermiso.objects
            .filter(usuario_id=getattr(user, "pk", None))
            .select_related("permiso")
            .values_list("permiso__nombre", "permitido")
        )
        for permission_name, allowed in direct_rows:
            target = "allow" if allowed else "deny"
            state[target].update(_keys_for_db_permission(permission_name))
    except DatabaseError:
        state["allow"] = set()
        state["deny"] = set()

    setattr(user, cache_name, state)
    return state


def clear_permission_cache(user) -> None:
    if hasattr(user, "_mainapp_permission_state"):
        delattr(user, "_mainapp_permission_state")


def user_has_permission(user, code: Optional[str]) -> bool:
    if not code:
        return True
    if is_permission_admin(user):
        return True

    wanted = _permission_keys_for_definition(code)
    wanted.add(normalize_permission_key(code))
    state = _load_permission_state(user)

    if state["deny"] & wanted:
        return False
    if state["allow"] & wanted:
        return True
    return bool(state["role"] & wanted)


def route_permission_for_url_name(url_name: Optional[str]) -> Optional[str]:
    if not url_name or url_name in PUBLIC_URL_NAMES or url_name in ALWAYS_ALLOWED_URL_NAMES:
        return None
    return ROUTE_PERMISSIONS.get(url_name)


def user_can_access_url_name(user, url_name: Optional[str]) -> bool:
    if not url_name:
        return True
    if url_name in PUBLIC_URL_NAMES:
        return True
    if url_name in ALWAYS_ALLOWED_URL_NAMES:
        return getattr(user, "is_authenticated", False)
    return user_has_permission(user, route_permission_for_url_name(url_name))


def _resolve_nav_item(raw_item: Dict[str, object], user, current_path: str) -> Optional[Dict[str, object]]:
    url_name = raw_item.get("url_name")
    if url_name and not user_can_access_url_name(user, str(url_name)):
        return None

    try:
        url = reverse(str(url_name), args=raw_item.get("args", [])) if url_name else "#"
    except NoReverseMatch:
        return None

    path = current_path or ""
    active = path == url or (url != "/" and path.startswith(url))
    return {
        "label": raw_item["label"],
        "url": url,
        "active": active,
        "children": [],
    }


def build_nav_menu(user, current_path: str) -> List[Dict[str, object]]:
    if not getattr(user, "is_authenticated", False):
        return []

    menu = []
    for raw_group in NAV_GROUPS:
        children = raw_group.get("children")
        if not children:
            item = _resolve_nav_item(raw_group, user, current_path)
            if item:
                menu.append(item)
            continue

        visible_children = [
            child
            for child in (
                _resolve_nav_item(raw_child, user, current_path)
                for raw_child in children
            )
            if child
        ]
        if not visible_children:
            continue

        group = {
            "label": raw_group["label"],
            "url": visible_children[0]["url"],
            "active": any(child["active"] for child in visible_children),
            "children": visible_children,
        }
        menu.append(group)
    return menu
