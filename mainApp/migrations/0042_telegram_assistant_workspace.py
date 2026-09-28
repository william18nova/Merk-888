import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0041_expense_edit_history")]
    operations = [
        migrations.CreateModel(name="TelegramAlias", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("entidad", models.CharField(max_length=30)), ("clave", models.CharField(max_length=100)),
            ("nombre", models.CharField(max_length=100)), ("registro_id", models.PositiveBigIntegerField()),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("telegram_usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="aliases", to="mainApp.telegramusuario")),
        ], options={"db_table": "telegram_aliases", "constraints": [models.UniqueConstraint(fields=("telegram_usuario", "entidad", "clave"), name="telegram_alias_usuario_unico")]}),
        migrations.CreateModel(name="TelegramSeguimiento", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("tipo", models.CharField(max_length=30)), ("hora", models.TimeField()),
            ("activo", models.BooleanField(default=True)), ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("telegram_usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="seguimientos", to="mainApp.telegramusuario")),
        ], options={"db_table": "telegram_seguimientos", "constraints": [models.UniqueConstraint(fields=("telegram_usuario", "tipo"), name="telegram_seguimiento_unico")]}),
        migrations.CreateModel(name="TelegramEnvioSeguimiento", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("fecha", models.DateField()), ("estado", models.CharField(default="RESERVADO", max_length=20)),
            ("detalle", models.CharField(blank=True, default="", max_length=250)),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("seguimiento", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="envios", to="mainApp.telegramseguimiento")),
        ], options={"db_table": "telegram_envios_seguimiento", "constraints": [models.UniqueConstraint(fields=("seguimiento", "fecha"), name="telegram_seguimiento_dia_unico")]}),
    ]
