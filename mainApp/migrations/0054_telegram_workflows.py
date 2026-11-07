from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0053_avance_cierre_caja")]
    operations = [
        migrations.AddField(model_name="telegramactualizacion", name="photo_file_id", field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="telegramactualizacion", name="photo_file_size", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AlterField(model_name="telegramactualizacion", name="tipo", field=models.CharField(choices=[("TEXTO", "Texto"), ("VOZ", "Voz"), ("FOTO", "Comprobante"), ("CALLBACK", "Botón"), ("OTRO", "Otro")], default="OTRO", max_length=12)),
        migrations.CreateModel(name="TelegramProgramacion", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("tipo", models.CharField(max_length=20)), ("frecuencia", models.CharField(max_length=12)),
            ("configuracion", models.JSONField(default=dict)), ("activo", models.BooleanField(db_index=True, default=True)),
            ("creado_en", models.DateTimeField(auto_now_add=True)), ("ultimo_chequeo", models.DateTimeField(blank=True, null=True)),
            ("telegram_usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="programaciones", to="mainApp.telegramusuario")),
        ], options={"db_table": "telegram_programaciones"}),
        migrations.CreateModel(name="TelegramEntregaProgramada", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("clave", models.CharField(max_length=64)), ("estado", models.CharField(default="RESERVADO", max_length=20)),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("programacion", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entregas", to="mainApp.telegramprogramacion")),
        ], options={"db_table": "telegram_entregas_programadas", "constraints": [models.UniqueConstraint(fields=("programacion", "clave"), name="telegram_programa_entrega_unica")]}),
    ]
