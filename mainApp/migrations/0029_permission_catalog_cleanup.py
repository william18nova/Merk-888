import re
import unicodedata

from django.db import migrations, models
from django.db.models.functions import Lower, Trim


# Este catálogo queda congelado en la migración para que despliegues futuros no
# dependan del contenido que tenga mainApp.permissions en ese momento.
CANONICAL_DELEGABLE_PERMISSIONS = (
    ("Agregar sucursal", "Permite crear sucursales."),
    ("Visualizar sucursales", "Permite ver el listado de sucursales."),
    ("Editar sucursal", "Permite modificar sucursales."),
    ("Eliminar sucursal", "Permite eliminar sucursales."),
    ("Agregar categoria", "Permite crear categorias."),
    ("Visualizar categorias", "Permite ver categorias."),
    ("Editar categoria", "Permite modificar categorias."),
    ("Eliminar categoria", "Permite eliminar categorias."),
    ("Agregar producto", "Permite crear productos."),
    ("Visualizar productos", "Permite ver productos."),
    ("Editar producto", "Permite modificar productos."),
    ("Eliminar producto", "Permite eliminar productos."),
    ("Agregar inventario", "Permite crear inventario."),
    ("Visualizar inventarios", "Permite ver inventarios."),
    ("Editar inventario", "Permite modificar inventarios."),
    (
        "Inventario desde fotos",
        "Permite cargar inventario desde fotos de facturas.",
    ),
    (
        "Inventario plaza WhatsApp",
        "Permite llenar y enviar por WhatsApp el inventario de plaza.",
    ),
    ("Eliminar inventario", "Permite eliminar productos de inventario."),
    ("Agregar proveedor", "Permite crear proveedores."),
    ("Visualizar proveedores", "Permite ver proveedores."),
    ("Editar proveedor", "Permite modificar proveedores."),
    ("Eliminar proveedor", "Permite eliminar proveedores."),
    ("Agregar precios proveedor", "Permite crear precios de proveedor."),
    ("Visualizar precios proveedor", "Permite ver precios de proveedor."),
    ("Editar precios proveedor", "Permite modificar precios de proveedor."),
    ("Eliminar precios proveedor", "Permite eliminar precios de proveedor."),
    ("Agregar punto de pago", "Permite crear puntos de pago."),
    ("Visualizar puntos de pago", "Permite ver puntos de pago."),
    ("Editar punto de pago", "Permite modificar puntos de pago."),
    ("Eliminar punto de pago", "Permite eliminar puntos de pago."),
    ("Agregar rol", "Permite crear roles."),
    ("Visualizar roles", "Permite ver roles."),
    ("Editar rol", "Permite modificar roles."),
    ("Eliminar rol", "Permite eliminar roles."),
    ("Agregar usuario", "Permite crear usuarios."),
    ("Visualizar usuarios", "Permite ver usuarios."),
    ("Editar usuario", "Permite modificar usuarios."),
    ("Eliminar usuario", "Permite eliminar usuarios."),
    ("Agregar empleado", "Permite crear empleados."),
    ("Visualizar empleados", "Permite ver empleados."),
    ("Editar empleado", "Permite modificar empleados."),
    ("Eliminar empleado", "Permite eliminar empleados."),
    ("Agregar horario", "Permite crear horarios."),
    ("Visualizar horarios", "Permite ver horarios."),
    ("Editar horario", "Permite modificar horarios."),
    ("Eliminar horario", "Permite eliminar horarios."),
    ("Agregar horario de caja", "Permite crear horarios de caja."),
    ("Visualizar horarios de caja", "Permite ver horarios de caja."),
    ("Editar horario de caja", "Permite modificar horarios de caja."),
    ("Eliminar horario de caja", "Permite eliminar horarios de caja."),
    ("Agregar cliente", "Permite crear clientes."),
    ("Visualizar clientes", "Permite ver clientes."),
    ("Editar cliente", "Permite modificar clientes."),
    ("Eliminar cliente", "Permite eliminar clientes."),
    ("Generar venta", "Permite registrar ventas."),
    ("Visualizar ventas", "Permite ver ventas y facturas."),
    (
        "Imprimir venta",
        "Permite abrir el detalle de una venta en modo solo lectura e imprimir la factura.",
    ),
    (
        "Cambios y devoluciones",
        "Permite ver y gestionar cambios o devoluciones.",
    ),
    ("Ventas diarias", "Permite consultar ventas diarias."),
    ("Ventas por producto", "Permite consultar ventas por producto."),
    (
        "Metricas del negocio",
        "Permite consultar metricas estadisticas generales del negocio.",
    ),
    (
        "Notificaciones Nequi",
        "Permite ver las notificaciones de pagos recibidos por Nequi.",
    ),
    (
        "Eliminar notificaciones Nequi",
        "Permite eliminar notificaciones de pagos recibidos por Nequi.",
    ),
    ("Agregar pedido", "Permite crear pedidos a proveedores."),
    ("Visualizar pedidos", "Permite ver pedidos."),
    ("Editar pedido", "Permite modificar pedidos."),
    ("Eliminar pedido", "Permite eliminar pedidos."),
    ("Pedidos pagados", "Permite consultar pedidos pagados."),
    ("Turno de caja", "Permite operar el turno de caja propio."),
    (
        "Retiro base de caja",
        "Permite entrar a la pagina donde se retira dinero para dejar la base de caja.",
    ),
    ("Dashboard turnos", "Permite consultar el dashboard de turnos de caja."),
    (
        "Administrar turnos de caja",
        "Permite editar, cerrar y eliminar turnos desde el panel administrativo.",
    ),
    (
        "Editar turnos de caja",
        "Permite cargar y editar turnos de caja desde el panel administrativo, sin eliminar turnos.",
    ),
    (
        "Administrar permisos",
        "Permite consultar el catálogo y asignar permisos a roles o usuarios.",
    ),
)


INERT_PERMISSION_ALIASES = (
    "Visor Barcode",
    "visor_barcode",
    "visor_barcode_buscar",
    "visor_barcode_lookup",
    "Ventas no realizadas",
    "ventas_no_realizadas",
    "carritos limpiados",
    "auditoria carritos",
    "Generar códigos de descuento especial",
    "descuentos_especiales_generar",
    "claves_descuento_merk2888",
    "claves merk2888",
    "descuento especial merk2888",
    "Administrar funcionalidades del sistema",
    "configuracion_funcionalidades",
    "funcionalidades del sistema",
    "feature flags",
    "Configurar impresión",
    "configuracion_impresion",
    "configuración de impresión",
    "configurar impresora",
    "Administrar métodos de pago",
    "configuracion_metodos_pago",
    "métodos de pago",
    "medios de pago",
)


TURN_ADMIN_ALIASES = (
    "Administrar turnos",
    "Administrar turnos de caja",
    "turnos_caja_admin",
    "admin turnos",
    "caja_admin",
    "api_admin_turno_delete",
    "Eliminar turnos",
    "Eliminar turnos de caja",
)


NEQUI_DELETE_ALIASES = (
    "nequi_notificaciones_eliminar",
    "nequi_notificacion_eliminar",
    "nequi_notificaciones_eliminar_seleccionadas",
    "Eliminar notificaciones Nequi",
)


def _alias_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _database_name_key(value):
    return str(value or "").strip().lower()


def _role_ids_for_permissions(cursor, table, role_column, permission_column, ids):
    role_ids = set()
    for permission_id in ids:
        cursor.execute(
            f"SELECT {role_column} FROM {table} WHERE {permission_column} = %s",
            [permission_id],
        )
        role_ids.update(row[0] for row in cursor.fetchall())
    return role_ids


def _delete_role_links(cursor, table, permission_column, ids):
    for permission_id in ids:
        cursor.execute(
            f"DELETE FROM {table} WHERE {permission_column} = %s",
            [permission_id],
        )


def _insert_role_link(
    cursor,
    table,
    role_column,
    permission_column,
    role_id,
    permission_id,
):
    cursor.execute(
        (
            f"SELECT 1 FROM {table} "
            f"WHERE {role_column} = %s AND {permission_column} = %s"
        ),
        [role_id, permission_id],
    )
    if cursor.fetchone() is None:
        cursor.execute(
            (
                f"INSERT INTO {table} ({role_column}, {permission_column}) "
                "VALUES (%s, %s)"
            ),
            [role_id, permission_id],
        )


def _merge_permissions(
    Permiso,
    UsuarioPermiso,
    cursor,
    role_table,
    role_column,
    permission_column,
    permission_ids,
    keeper_id,
):
    permission_ids = sorted(set(permission_ids))
    if not permission_ids:
        return keeper_id
    if len(permission_ids) == 1:
        return keeper_id

    role_ids = _role_ids_for_permissions(
        cursor,
        role_table,
        role_column,
        permission_column,
        permission_ids,
    )

    # Al fusionar overrides directos, cualquier denegación explícita prevalece.
    user_states = {}
    for user_id, permitido in UsuarioPermiso.objects.filter(
        permiso_id__in=permission_ids
    ).values_list("usuario_id", "permitido"):
        previous = user_states.get(user_id, True)
        user_states[user_id] = previous and bool(permitido)

    _delete_role_links(cursor, role_table, permission_column, permission_ids)
    UsuarioPermiso.objects.filter(permiso_id__in=permission_ids).delete()
    Permiso.objects.filter(pk__in=[
        permission_id
        for permission_id in permission_ids
        if permission_id != keeper_id
    ]).delete()

    for role_id in sorted(role_ids):
        _insert_role_link(
            cursor,
            role_table,
            role_column,
            permission_column,
            role_id,
            keeper_id,
        )

    UsuarioPermiso.objects.bulk_create(
        [
            UsuarioPermiso(
                usuario_id=user_id,
                permiso_id=keeper_id,
                permitido=permitido,
            )
            for user_id, permitido in sorted(user_states.items())
        ]
    )
    return keeper_id


def _deduplicate_database_names(
    Permiso,
    UsuarioPermiso,
    cursor,
    role_table,
    role_column,
    permission_column,
):
    canonical_by_key = {
        _database_name_key(label): (label, description)
        for label, description in CANONICAL_DELEGABLE_PERMISSIONS
    }
    grouped = {}
    for permission in Permiso.objects.annotate(
        normalized_name=Lower(Trim("nombre"))
    ).order_by("pk"):
        grouped.setdefault(permission.normalized_name, []).append(permission)

    for normalized_name, matches in grouped.items():
        canonical = canonical_by_key.get(normalized_name)
        if canonical:
            canonical_label, canonical_description = canonical
            matches.sort(
                key=lambda permission: (
                    permission.nombre != canonical_label,
                    permission.pk,
                )
            )
        else:
            canonical_label = None
            canonical_description = None

        keeper = matches[0]
        _merge_permissions(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
            [permission.pk for permission in matches],
            keeper.pk,
        )
        if canonical_label and (
            keeper.nombre != canonical_label
            or keeper.descripcion != canonical_description
        ):
            keeper.nombre = canonical_label
            keeper.descripcion = canonical_description
            keeper.save(update_fields=["nombre", "descripcion"])


def _canonicalize_alias_group(
    Permiso,
    UsuarioPermiso,
    cursor,
    role_table,
    role_column,
    permission_column,
    aliases,
    canonical_label,
    canonical_description,
):
    accepted_keys = {_alias_key(alias) for alias in aliases}
    matches = [
        permission
        for permission in Permiso.objects.order_by("pk")
        if _alias_key(permission.nombre) in accepted_keys
    ]
    if matches:
        matches.sort(
            key=lambda permission: (
                permission.nombre != canonical_label,
                permission.pk,
            )
        )
        keeper = matches[0]
        _merge_permissions(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
            [permission.pk for permission in matches],
            keeper.pk,
        )
    else:
        keeper = Permiso.objects.create(
            nombre=canonical_label,
            descripcion=canonical_description,
        )

    if (
        keeper.nombre != canonical_label
        or keeper.descripcion != canonical_description
    ):
        keeper.nombre = canonical_label
        keeper.descripcion = canonical_description
        keeper.save(update_fields=["nombre", "descripcion"])
    return keeper


def _delete_inert_permissions(
    Permiso,
    UsuarioPermiso,
    cursor,
    role_table,
    permission_column,
):
    inert_keys = {_alias_key(alias) for alias in INERT_PERMISSION_ALIASES}
    permission_ids = [
        permission.pk
        for permission in Permiso.objects.only("pk", "nombre")
        if _alias_key(permission.nombre) in inert_keys
    ]
    if not permission_ids:
        return
    _delete_role_links(cursor, role_table, permission_column, permission_ids)
    UsuarioPermiso.objects.filter(permiso_id__in=permission_ids).delete()
    Permiso.objects.filter(pk__in=permission_ids).delete()


def cleanup_permission_catalog(apps, schema_editor):
    Permiso = apps.get_model("mainApp", "Permiso")
    Rol = apps.get_model("mainApp", "Rol")
    UsuarioPermiso = apps.get_model("mainApp", "UsuarioPermiso")

    quote = schema_editor.connection.ops.quote_name
    role_table = quote("rolespermisos")
    role_column = quote("rolid")
    permission_column = quote("permisoid")

    with schema_editor.connection.cursor() as cursor:
        _deduplicate_database_names(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
        )

        _canonicalize_alias_group(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
            TURN_ADMIN_ALIASES,
            "Administrar turnos de caja",
            "Permite editar, cerrar y eliminar turnos desde el panel administrativo.",
        )
        _canonicalize_alias_group(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
            NEQUI_DELETE_ALIASES,
            "Eliminar notificaciones Nequi",
            "Permite eliminar notificaciones de pagos recibidos por Nequi.",
        )

        _delete_inert_permissions(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            permission_column,
        )

        # Crea/actualiza únicamente el catálogo delegable. Los permisos
        # desconocidos se conservan, pero no se conceden automáticamente.
        canonical_permissions = []
        existing_by_key = {
            permission.normalized_name: permission
            for permission in Permiso.objects.annotate(
                normalized_name=Lower(Trim("nombre"))
            )
        }
        for label, description in CANONICAL_DELEGABLE_PERMISSIONS:
            permission = existing_by_key.get(_database_name_key(label))
            if permission is None:
                permission = Permiso.objects.create(
                    nombre=label,
                    descripcion=description,
                )
                existing_by_key[_database_name_key(label)] = permission
            elif permission.nombre != label or permission.descripcion != description:
                permission.nombre = label
                permission.descripcion = description
                permission.save(update_fields=["nombre", "descripcion"])
            canonical_permissions.append(permission)

        _deduplicate_database_names(
            Permiso,
            UsuarioPermiso,
            cursor,
            role_table,
            role_column,
            permission_column,
        )

        web_master_role_ids = [
            role.pk
            for role in Rol.objects.only("pk", "nombre")
            if _alias_key(role.nombre) in {"web master", "webmaster"}
        ]
        for role_id in web_master_role_ids:
            for permission in canonical_permissions:
                _insert_role_link(
                    cursor,
                    role_table,
                    role_column,
                    permission_column,
                    role_id,
                    permission.pk,
                )


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0028_metodopago"),
    ]

    operations = [
        migrations.RunPython(
            cleanup_permission_catalog,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="permiso",
            constraint=models.UniqueConstraint(
                Lower(Trim("nombre")),
                name="ux_permisos_nombre_ci",
            ),
        ),
    ]
