import re
import unicodedata

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FEATURE_KEY = "ventas_exigir_turno_caja"
PERMISSION_NAME = "Administrar funcionalidades del sistema"
PERMISSION_DESCRIPTION = (
    "Permite activar o desactivar funcionalidades globales preparadas para "
    "operar de forma segura en ambos modos. Uso exclusivo Web Master."
)


def _normalize_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def seed_feature_and_permission(apps, schema_editor):
    ConfiguracionFuncionalidad = apps.get_model(
        "mainApp",
        "ConfiguracionFuncionalidad",
    )
    Permiso = apps.get_model("mainApp", "Permiso")
    Rol = apps.get_model("mainApp", "Rol")

    ConfiguracionFuncionalidad.objects.get_or_create(
        clave=FEATURE_KEY,
        defaults={
            "habilitada": True,
            "version": 1,
            "actualizada_por_nombre": "Configuración inicial",
        },
    )

    permission, _ = Permiso.objects.get_or_create(
        nombre=PERMISSION_NAME,
        defaults={"descripcion": PERMISSION_DESCRIPTION},
    )
    if not permission.descripcion:
        permission.descripcion = PERMISSION_DESCRIPTION
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


def keep_audit_data_on_reverse(apps, schema_editor):
    # Una reversión de esquema no debe borrar permisos ni decisiones auditadas.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0023_special_merk2888_discount"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionFuncionalidad",
            fields=[
                (
                    "clave",
                    models.CharField(
                        max_length=80,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("habilitada", models.BooleanField(default=True)),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("actualizada_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizada_por_nombre",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "actualizada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="funcionalidades_actualizadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "configuracion_funcionalidades",
                "ordering": ["clave"],
            },
        ),
        migrations.CreateModel(
            name="CambioConfiguracionFuncionalidad",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("anterior", models.BooleanField()),
                ("nuevo", models.BooleanField()),
                (
                    "motivo",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                (
                    "cambiado_por_nombre",
                    models.CharField(max_length=160),
                ),
                (
                    "ip",
                    models.GenericIPAddressField(blank=True, null=True),
                ),
                (
                    "user_agent",
                    models.CharField(blank=True, default="", max_length=300),
                ),
                (
                    "solicitud_id",
                    models.UUIDField(editable=False, unique=True),
                ),
                (
                    "creado_en",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "cambiado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cambios_funcionalidades",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "funcionalidad",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cambios",
                        to="mainApp.configuracionfuncionalidad",
                    ),
                ),
            ],
            options={
                "db_table": "cambios_configuracion_funcionalidades",
                "ordering": ["-creado_en", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="cambioconfiguracionfuncionalidad",
            constraint=models.CheckConstraint(
                condition=~models.Q(anterior=models.F("nuevo")),
                name="ccf_estado_debe_cambiar",
            ),
        ),
        migrations.AlterField(
            model_name="reintegroventa",
            name="turno",
            field=models.ForeignKey(
                blank=True,
                db_column="turno_id",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reintegros",
                to="mainApp.turnocaja",
            ),
        ),
        migrations.RunPython(
            seed_feature_and_permission,
            keep_audit_data_on_reverse,
        ),
    ]
