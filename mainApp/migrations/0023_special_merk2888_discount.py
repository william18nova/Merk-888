import re
import unicodedata

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


SPECIAL_CLIENT_KEY = "merk2888"
SPECIAL_CLIENT_DOCUMENT = "MERK2888"
SPECIAL_PERMISSION_NAME = "Generar códigos de descuento especial"
SPECIAL_PERMISSION_DESCRIPTION = (
    "Permite generar contraseñas de un solo uso para autorizar el descuento "
    "del 100% del cliente especial merk2888."
)


def _normalize_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def seed_special_client_and_permission(apps, schema_editor):
    Cliente = apps.get_model("mainApp", "Cliente")
    ClienteEspecial = apps.get_model("mainApp", "ClienteEspecial")
    Permiso = apps.get_model("mainApp", "Permiso")
    Rol = apps.get_model("mainApp", "Rol")

    matching_clients = list(
        Cliente.objects
        .filter(numerodocumento__iexact=SPECIAL_CLIENT_DOCUMENT)
        .order_by("pk")[:2]
    )
    if len(matching_clients) > 1:
        raise RuntimeError(
            "Hay varios clientes con el documento reservado MERK2888. "
            "Corrige los duplicados antes de aplicar la migración."
        )

    if matching_clients:
        client = matching_clients[0]
        changed_fields = []
        if client.nombre != SPECIAL_CLIENT_KEY:
            client.nombre = SPECIAL_CLIENT_KEY
            changed_fields.append("nombre")
        if client.apellido:
            client.apellido = ""
            changed_fields.append("apellido")
        if client.numerodocumento != SPECIAL_CLIENT_DOCUMENT:
            client.numerodocumento = SPECIAL_CLIENT_DOCUMENT
            changed_fields.append("numerodocumento")
        if changed_fields:
            client.save(update_fields=changed_fields)
    else:
        client = Cliente.objects.create(
            nombre=SPECIAL_CLIENT_KEY,
            apellido="",
            telefono=None,
            email=None,
            numerodocumento=SPECIAL_CLIENT_DOCUMENT,
        )

    profile, created = ClienteEspecial.objects.get_or_create(
        clave=SPECIAL_CLIENT_KEY,
        defaults={
            "cliente_id": client.pk,
            "activo": True,
            "creado_en": timezone.now(),
        },
    )
    if not created and profile.cliente_id != client.pk:
        raise RuntimeError(
            "La clave especial merk2888 ya apunta a otro cliente. "
            "Revisa la configuración antes de continuar."
        )
    if not profile.activo:
        profile.activo = True
        profile.save(update_fields=["activo"])

    permission, _ = Permiso.objects.get_or_create(
        nombre=SPECIAL_PERMISSION_NAME,
        defaults={"descripcion": SPECIAL_PERMISSION_DESCRIPTION},
    )
    if not permission.descripcion:
        permission.descripcion = SPECIAL_PERMISSION_DESCRIPTION
        permission.save(update_fields=["descripcion"])

    web_master_role_ids = [
        role.pk
        for role in Rol.objects.only("pk", "nombre")
        if _normalize_key(role.nombre) == "web_master"
    ]

    quote = schema_editor.connection.ops.quote_name
    role_permission_table = quote("rolespermisos")
    role_column = quote("rolid")
    permission_column = quote("permisoid")
    with schema_editor.connection.cursor() as cursor:
        for role_id in web_master_role_ids:
            cursor.execute(
                (
                    f"SELECT 1 FROM {role_permission_table} "
                    f"WHERE {role_column} = %s AND {permission_column} = %s "
                    "LIMIT 1"
                ),
                [role_id, permission.pk],
            )
            if cursor.fetchone():
                continue
            cursor.execute(
                (
                    f"INSERT INTO {role_permission_table} "
                    f"({role_column}, {permission_column}) VALUES (%s, %s)"
                ),
                [role_id, permission.pk],
            )


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0022_allow_negative_turno_medio_balances"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClienteEspecial",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "clave",
                    models.CharField(
                        default="merk2888",
                        max_length=50,
                        unique=True,
                    ),
                ),
                ("activo", models.BooleanField(default=True)),
                (
                    "creado_en",
                    models.DateTimeField(default=timezone.now),
                ),
                (
                    "cliente",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="perfil_especial",
                        to="mainApp.cliente",
                    ),
                ),
            ],
            options={
                "db_table": "clientes_especiales",
                "ordering": ["clave"],
            },
        ),
        migrations.CreateModel(
            name="AutorizacionDescuentoEspecial",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "selector",
                    models.CharField(
                        editable=False,
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "referencia",
                    models.CharField(
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                (
                    "solicitud_id",
                    models.CharField(
                        editable=False,
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "secreto_hash",
                    models.CharField(editable=False, max_length=128),
                ),
                (
                    "generada_por_nombre",
                    models.CharField(max_length=160),
                ),
                (
                    "generada_en",
                    models.DateTimeField(
                        db_index=True,
                        default=timezone.now,
                    ),
                ),
                (
                    "expira_en",
                    models.DateTimeField(db_index=True),
                ),
                (
                    "revocada_en",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "revocada_por_nombre",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=160,
                    ),
                ),
                (
                    "usada_en",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "bloqueada_en",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "intentos_fallidos",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "ultimo_intento_fallido_en",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "ultimo_intento_fallido_por_nombre",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=160,
                    ),
                ),
                (
                    "ultimo_intento_fallido_ip",
                    models.GenericIPAddressField(blank=True, null=True),
                ),
                (
                    "usada_por_nombre",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=160,
                    ),
                ),
                (
                    "subtotal_aplicado",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=15,
                        null=True,
                    ),
                ),
                (
                    "descuento_aplicado",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=15,
                        null=True,
                    ),
                ),
                (
                    "cliente_especial",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="autorizaciones",
                        to="mainApp.clienteespecial",
                    ),
                ),
                (
                    "generada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="descuentos_especiales_generados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "revocada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="descuentos_especiales_revocados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "sucursal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="autorizaciones_descuento_especial",
                        to="mainApp.sucursal",
                    ),
                ),
                (
                    "turno",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="autorizaciones_descuento_especial",
                        to="mainApp.turnocaja",
                    ),
                ),
                (
                    "ultimo_intento_fallido_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="intentos_descuento_especial_fallidos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "usada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="descuentos_especiales_usados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "venta",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="autorizacion_descuento_especial",
                        to="mainApp.venta",
                    ),
                ),
            ],
            options={
                "db_table": "autorizaciones_descuento_especial",
                "ordering": ["-generada_en", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="autorizaciondescuentoespecial",
            index=models.Index(
                fields=[
                    "cliente_especial",
                    "usada_en",
                    "revocada_en",
                    "bloqueada_en",
                ],
                name="ade_estado_cliente_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="autorizaciondescuentoespecial",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("bloqueada_en__isnull", True),
                    ("revocada_en__isnull", True),
                    ("usada_en__isnull", True),
                ),
                fields=("cliente_especial",),
                name="uniq_ade_activa_cliente",
            ),
        ),
        migrations.AddConstraint(
            model_name="autorizaciondescuentoespecial",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("intentos_fallidos__gte", 0),
                    ("intentos_fallidos__lte", 5),
                ),
                name="ade_intentos_0_5_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="autorizaciondescuentoespecial",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("usada_en__isnull", True),
                        ("venta__isnull", True),
                    )
                    | models.Q(
                        ("usada_en__isnull", False),
                        ("venta__isnull", False),
                    )
                ),
                name="ade_uso_venta_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="autorizaciondescuentoespecial",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("descuento_aplicado__isnull", True))
                    | models.Q(("descuento_aplicado__gte", 0))
                ),
                name="ade_descuento_no_neg_check",
            ),
        ),
        migrations.AddConstraint(
            model_name="autorizaciondescuentoespecial",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("subtotal_aplicado__isnull", True))
                    | models.Q(("subtotal_aplicado__gte", 0))
                ),
                name="ade_subtotal_no_neg_check",
            ),
        ),
        migrations.RunPython(
            seed_special_client_and_permission,
            migrations.RunPython.noop,
        ),
    ]
