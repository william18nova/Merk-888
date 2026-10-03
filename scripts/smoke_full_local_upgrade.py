"""Actualizar una instalación ficticia antigua; nunca usa una base del negocio."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("previous", type=Path)
parser.add_argument("candidate", type=Path)
parser.add_argument("--pg-bin", required=True, type=Path)
parser.add_argument("--port", type=int, default=8924)
parser.add_argument("--pg-port", type=int, default=55450)
args = parser.parse_args()
previous, candidate = args.previous.resolve(), args.candidate.resolve()
root = Path(tempfile.mkdtemp(prefix="nova-upgrade-smoke-"))
data = root / "data"
env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in
       {"DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG", "DATABASE_URL", "PYTHONPATH"}}
env["PYTHONUTF8"] = "1"


def command(package, code, *params):
    result = subprocess.run([sys.executable, "-B", "-c", code, *map(str, params)], cwd=package,
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    if result.returncode:
        print(result.stdout[-2000:]); print(result.stderr[-4500:])
        raise RuntimeError("Falló la actualización ficticia. Se conservaron sus datos y respaldos privados.")
    return result.stdout


print(f"Prueba de actualización {platform.system()}: {root}", flush=True)
command(previous, "from local_pos.runtime import initialize; import sys; initialize(sys.argv[1],sys.argv[2],username='laboratorio',password='solo-prueba-2026',http_port=int(sys.argv[3]),pg_port=int(sys.argv[4]))", data,args.pg_bin,args.port,args.pg_port)
probe = """
from pathlib import Path
from uuid import uuid4
import sys
from local_pos.runtime import installed_state, read_json, Postgres, django_setup
root=Path(sys.argv[1]); state=installed_state(root); config=read_json(root/'local.json'); pg=Postgres(root,state['pg_bin'],config)
pg.start()
try:
    django_setup(root)
    from django.db import connections
    from local_pos.models import LocalNode, LocalCommand
    from mainApp.models import Usuario, Categoria, Producto, Sucursal, Inventario
    node=LocalNode.objects.get()
    if sys.argv[2]=='seed':
        node.sequence=17; node.save()
        LocalCommand.objects.create(operation_id=uuid4(),node=node,sequence=17,actor=Usuario.objects.get(),kind='test.persistence',fingerprint='0'*64,payload={'fictitious':True},state='pending')
        category=Categoria.objects.create(nombre='FICTICIA')
        product=Producto.objects.create(nombre='PRODUCTO FICTICIO',precio='3.80',categoria=category)
        branch=Sucursal.objects.create(nombre='SUCURSAL FICTICIA')
        Inventario.objects.create(productoid=product,sucursalid=branch,cantidad=-500)
    else:
        assert node.sequence==17
        assert LocalCommand.objects.get().state=='pending'
        assert Inventario.objects.get().cantidad==-500
        assert Usuario.objects.get().check_password('solo-prueba-2026')
        assert 'NovaSoft.settings' not in sys.modules
    if sys.argv[2]=='change':
        Categoria.objects.create(nombre='NUEVA INFORMACION FICTICIA')
    connections.close_all()
finally:
    pg.stop()
"""
command(previous, probe, data, "seed")
identity = json.loads((data / "local.json").read_text())["instance_id"]
print("Simulando fallo de verificación y recuperación automática", flush=True)
command(candidate, """
import sys
from unittest.mock import patch
from local_pos.upgrades import upgrade
with patch('local_pos.upgrades._probe',side_effect=RuntimeError('FALLO FICTICIO')):
    try: upgrade(sys.argv[1],previous_package=sys.argv[2],backup_dir=sys.argv[3])
    except RuntimeError as exc: assert 'versión anterior' in str(exc), str(exc)
    else: raise AssertionError('No se detectó el fallo ficticio')
""", data, previous, root / "backup-failure")
command(previous, probe, data, "read")
print("Aplicando migración y verificando pendientes, inventario y autores", flush=True)
update = "from local_pos.upgrades import upgrade; import sys; upgrade(sys.argv[1],previous_package=sys.argv[2],backup_dir=sys.argv[3])"
command(candidate, update, data, previous, root / "backup-success")
command(candidate, probe, data, "read")
print("Regresando a la versión anterior sin perder información", flush=True)
command(candidate, "from local_pos.upgrades import rollback; import sys; rollback(sys.argv[1],backup_dir=sys.argv[2],confirm_instance=sys.argv[3])", data,root / "backup-success",identity)
command(previous, probe, data, "read")
command(candidate, update, data,previous,root / "backup-latest")
command(candidate, probe,data,"change")
print("Comprobando bloqueo de retroceso cuando ya hay información nueva", flush=True)
command(candidate, """
from local_pos.upgrades import rollback
import sys
try: rollback(sys.argv[1],backup_dir=sys.argv[2],confirm_instance=sys.argv[3])
except RuntimeError as exc: assert 'información nueva' in str(exc), str(exc)
else: raise AssertionError('Se permitió borrar información nueva')
""",data,root / "backup-latest",identity)
command(candidate,probe,data,"read")
assert json.loads((data / "local.json").read_text())["instance_id"]==identity
print(json.dumps({"passed":True,"system":platform.system(),"automatic_recovery":True,"upgrade":True,
    "rollback":True,"pending_preserved":True,"negative_stock_preserved":True,"new_data_protected":True,
    "production_access":False,"directory":str(root)}))
