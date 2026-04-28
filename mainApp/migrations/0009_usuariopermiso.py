from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0008_alter_pagoventa_options_producto_ibua_producto_icui_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="UsuarioPermiso",
            fields=[
                ("id", models.BigAutoField(db_column="id", primary_key=True, serialize=False)),
                ("permitido", models.BooleanField(default=True)),
                (
                    "permiso",
                    models.ForeignKey(
                        db_column="permisoid",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="usuarios_permisos",
                        to="mainApp.permiso",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        db_column="usuarioid",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="permisos_directos",
                        to="mainApp.usuario",
                    ),
                ),
            ],
            options={
                "db_table": "usuariospermisos",
            },
        ),
        migrations.AddConstraint(
            model_name="usuariopermiso",
            constraint=models.UniqueConstraint(
                fields=("usuario", "permiso"),
                name="ux_usuariospermisos_usuario_perm",
            ),
        ),
    ]
