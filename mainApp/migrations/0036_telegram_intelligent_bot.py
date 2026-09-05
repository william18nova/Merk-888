import uuid

import django.db.models.deletion
from django.db import migrations, models


FEATURE_KEY = "telegram_bot_inteligente"


def seed_telegram_feature(apps, schema_editor):
    ConfiguracionFuncionalidad = apps.get_model(
        "mainApp",
        "ConfiguracionFuncionalidad",
    )
    ConfiguracionFuncionalidad.objects.get_or_create(
        clave=FEATURE_KEY,
        defaults={
            "habilitada": False,
            "version": 1,
            "actualizada_por_nombre": "Configuración inicial segura",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0035_merge_card_payment_methods"),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramActualizacion",
            fields=[
                ("update_id", models.BigIntegerField(primary_key=True, serialize=False)),
                ("telegram_user_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("telegram_chat_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("telegram_username", models.CharField(blank=True, default="", max_length=80)),
                ("nombre_telegram", models.CharField(blank=True, default="", max_length=160)),
                ("chat_type", models.CharField(blank=True, default="", max_length=20)),
                ("tipo", models.CharField(choices=[("TEXTO", "Texto"), ("VOZ", "Voz"), ("CALLBACK", "Botón"), ("OTRO", "Otro")], default="OTRO", max_length=12)),
                ("texto", models.TextField(blank=True, default="")),
                ("voice_file_id", models.CharField(blank=True, default="", max_length=255)),
                ("voice_file_size", models.PositiveBigIntegerField(blank=True, null=True)),
                ("callback_query_id", models.CharField(blank=True, default="", max_length=160)),
                ("estado", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("PROCESANDO", "Procesando"), ("PROCESADO", "Procesado"), ("IGNORADO", "Ignorado"), ("ERROR", "Error")], db_index=True, default="PENDIENTE", max_length=12)),
                ("intentos", models.PositiveSmallIntegerField(default=0)),
                ("intencion", models.CharField(blank=True, default="", max_length=80)),
                ("transcripcion", models.TextField(blank=True, default="")),
                ("respuesta", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                ("recibido_en", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("iniciado_en", models.DateTimeField(blank=True, null=True)),
                ("procesado_en", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "telegram_actualizaciones",
                "ordering": ["recibido_en", "update_id"],
            },
        ),
        migrations.CreateModel(
            name="TelegramUsuario",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("telegram_user_id", models.BigIntegerField(unique=True)),
                ("telegram_chat_id", models.BigIntegerField(unique=True)),
                ("telegram_username", models.CharField(blank=True, default="", max_length=80)),
                ("nombre_telegram", models.CharField(blank=True, default="", max_length=160)),
                ("activo", models.BooleanField(db_index=True, default=True)),
                ("vinculado_en", models.DateTimeField(auto_now_add=True)),
                ("ultimo_uso_en", models.DateTimeField(blank=True, null=True)),
                ("usuario", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_perfil", to="mainApp.usuario")),
            ],
            options={
                "db_table": "telegram_usuarios",
                "ordering": ["usuario__nombreusuario"],
            },
        ),
        migrations.CreateModel(
            name="TelegramCodigoVinculacion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("codigo_hash", models.CharField(max_length=64, unique=True)),
                ("vence_en", models.DateTimeField(db_index=True)),
                ("usado_en", models.DateTimeField(blank=True, null=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("creado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="codigos_telegram_creados", to="mainApp.usuario")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="codigos_vinculacion_telegram", to="mainApp.usuario")),
            ],
            options={
                "db_table": "telegram_codigos_vinculacion",
                "ordering": ["-creado_en"],
            },
        ),
        migrations.CreateModel(
            name="TelegramAccionPendiente",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("accion", models.CharField(max_length=80)),
                ("argumentos", models.JSONField(default=dict)),
                ("resumen", models.CharField(max_length=500)),
                ("estado", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("CONFIRMADA", "Confirmada"), ("CANCELADA", "Cancelada"), ("EXPIRADA", "Expirada"), ("ERROR", "Error")], db_index=True, default="PENDIENTE", max_length=12)),
                ("vence_en", models.DateTimeField(db_index=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("resuelto_en", models.DateTimeField(blank=True, null=True)),
                ("actualizacion", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acciones_propuestas", to="mainApp.telegramactualizacion")),
                ("telegram_usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="acciones_pendientes", to="mainApp.telegramusuario")),
            ],
            options={
                "db_table": "telegram_acciones_pendientes",
                "ordering": ["-creado_en"],
            },
        ),
        migrations.CreateModel(
            name="TelegramAuditoria",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_chat_id", models.BigIntegerField(blank=True, null=True)),
                ("accion", models.CharField(db_index=True, max_length=80)),
                ("argumentos", models.JSONField(blank=True, default=dict)),
                ("exitoso", models.BooleanField(db_index=True, default=True)),
                ("detalle", models.TextField(blank=True, default="")),
                ("creado_en", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("usuario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="auditoria_telegram", to="mainApp.usuario")),
            ],
            options={
                "db_table": "telegram_auditoria",
                "ordering": ["-creado_en", "-id"],
            },
        ),
        migrations.RunPython(
            seed_telegram_feature,
            migrations.RunPython.noop,
        ),
    ]
