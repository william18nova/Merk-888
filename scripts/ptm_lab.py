"""Arranque de pruebas manuales PTM sin conexiones de red salientes."""
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.ptm_lab_settings"

def blocked(*args, **kwargs):
    raise OSError("Laboratorio PTM: conexiones salientes deshabilitadas")

# La aplicación no puede contactar Aiven, PythonAnywhere, PTM ni proveedores.
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked

import django
django.setup()
from django.core.management import call_command
from django.test import Client
from mainApp.models import Rol, RolPermiso, Permiso, Usuario, Sucursal, CuentaLecturaPTM, PuntosPago, TurnoCaja

(ROOT / "build" / "ptm_lab").mkdir(parents=True, exist_ok=True)
call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
from django.db import connection
# Esta tabla heredada es unmanaged y syncdb no la crea.
if RolPermiso._meta.db_table not in connection.introspection.table_names():
    with connection.schema_editor() as schema:
        schema.create_model(RolPermiso)
role, _ = Rol.objects.get_or_create(nombre="Web Master")
admin, created = Usuario.objects.get_or_create(nombreusuario="WM Pruebas PTM",
    defaults={"rolid": role, "is_superuser": True, "is_staff": True})
password = "PruebasPTM-2026!"
if created:
    admin.set_password(password)
    admin.save()
branch, _ = Sucursal.objects.get_or_create(nombre="Yerbabuena PRUEBAS (ficticia)")
CuentaLecturaPTM.objects.get_or_create(usuario_ptm="DEMO-LOCAL-PTM",
    defaults={"nombre": "PTM ficticia - sin lector real"})
cashier_role, _ = Rol.objects.get_or_create(nombre="Cajero laboratorio PTM")
for code in ("caja_turno", "ventas_generar"):
    permission, _ = Permiso.objects.get_or_create(nombre=code)
    RolPermiso.objects.get_or_create(rol=cashier_role, permiso=permission)
for name in ("A", "B", "C"):
    user, created = Usuario.objects.get_or_create(nombreusuario="Cajero PTM " + name)
    if created:
        user.set_password(password)
        user.save()
    user.rolid = cashier_role
    user.save(update_fields=["rolid"])
    point, _ = PuntosPago.objects.get_or_create(nombre="Caja PRUEBAS " + name,
        sucursalid=branch, defaults={"dinerocaja": 0})
    if not TurnoCaja.objects.filter(cajero=user).exists():
        TurnoCaja.objects.create(cajero=user, puntopago=point)

client = Client()
assert client.login(nombreusuario=admin.nombreusuario, password=password)
assert client.get("/ptm/bolsas/").status_code == 200
client.logout()
assert client.login(nombreusuario="Cajero PTM A", password=password)
assert client.get("/ptm/bolsas/", HTTP_ACCEPT="application/json").status_code == 403
assert client.get("/turno_caja/").status_code == 200
print("Laboratorio listo. Solo datos ficticios. Usuario: WM Pruebas PTM")
print("Cajeros: Cajero PTM A / Cajero PTM B / Cajero PTM C")
print("Clave de pruebas: " + password)
port = 8000 if "--manage" in sys.argv else 8942
print(f"MODO PRUEBAS LOCAL: sin Aiven ni sincronización. URL: http://127.0.0.1:{port}/ptm/bolsas/", flush=True)
if "--check" not in sys.argv:
    call_command("runserver", f"127.0.0.1:{port}", use_reloader=False, use_threading=False)
