"""Instalar, reiniciar y recuperar UNA instalación ficticia aislada, sin nube."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("package", type=Path)
parser.add_argument("--pg-bin", type=Path, default=None)
parser.add_argument("--port", type=int, default=8918)
parser.add_argument("--pg-port", type=int, default=55444)
args = parser.parse_args()
package = args.package.resolve()
pg_bin = args.pg_bin
if pg_bin is None:
    if os.name == "nt":
        parser.error("En Windows indica --pg-bin con la instalación autorizada de PostgreSQL.")
    pg_bin = Path("/usr/lib/postgresql/16/bin")
if not pg_bin.is_absolute():
    parser.error("--pg-bin debe ser una ruta absoluta.")
root = Path(tempfile.mkdtemp(prefix="nova-installed-smoke-"))
data = root/"data"
python = sys.executable
port, pg_port = args.port, args.pg_port
env = {key:value for key,value in os.environ.items() if not key.startswith("PG") and key not in {"DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG", "PYTHONPATH"}}
env["PYTHONUTF8"] = "1"


def command(code, *args):
    result = subprocess.run([python,"-B","-c",code,*map(str,args)],cwd=package,env=env,
                            capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180)
    if result.returncode:
        print(result.stdout[-3000:])
        print(result.stderr[-4000:])
        raise RuntimeError("Falló un paso del instalador ficticio.")
    return result.stdout


# Se detiene mediante KeyboardInterrupt dentro de ESTE proceso, sin enviar
# Ctrl+C a otras consolas ni terminar PostgreSQL a la fuerza en Windows.
serve_probe = """
import _thread
import sys
import threading
from local_pos.runtime import serve
def ready(server):
    server.adj.asyncore_loop_timeout = .2
    def stop():
        if sys.stdin.readline().strip() == 'stop':
            _thread.interrupt_main()
            server.pull_trigger()
    threading.Thread(target=stop, daemon=True).start()
serve(sys.argv[1], ready_callback=ready)
"""


def boot_twice():
    for iteration in range(2):
        print(f"Arranque y parada controlada {iteration + 1}/2", flush=True)
        process=subprocess.Popen([python,"-B","-c",serve_probe,str(data)],
                                 cwd=package,env=env,stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                 text=True,encoding="utf-8",errors="replace")
        try:
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError(process.communicate()[0][-4000:])
                try:
                    with urlopen(f"http://127.0.0.1:{port}/",timeout=1) as response:
                        assert response.status==200
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError("El proceso local no abrió su puerto.")
        finally:
            if process.poll() is None:
                output, _ = process.communicate(input="stop\n",timeout=45)
                if process.returncode != 0:
                    raise RuntimeError(output[-4000:])


print(f"Instalación ficticia en {platform.system()}: {root}", flush=True)
command("from local_pos.runtime import initialize; import sys; initialize(sys.argv[1],sys.argv[2],username='laboratorio',password='solo-prueba-2026',http_port=int(sys.argv[3]),pg_port=int(sys.argv[4]))", data, pg_bin, port, pg_port)
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
    from local_pos.models import LocalNode, LocalCommand, LocalOperator, LocalHandoff
    from mainApp.models import Usuario
    node=LocalNode.objects.get()
    assert not LocalOperator.objects.exists()
    assert not LocalHandoff.objects.exists()
    if sys.argv[2]=='write':
        node.sequence=123; node.save(update_fields=['sequence'])
        LocalCommand.objects.create(operation_id=uuid4(),node=node,sequence=123,actor=Usuario.objects.get(),kind='test.persistence',fingerprint='0'*64,payload={'fictitious':True},state='pending')
    else:
        assert node.sequence==123
        assert LocalCommand.objects.get().state=='pending'
        assert 'NovaSoft.settings' not in sys.modules
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT inet_server_addr(), inet_server_port()')
            assert cursor.fetchone() == ('127.0.0.1', config['port'])
    connections.close_all()
finally:
    pg.stop()
"""
command(probe,data,"write")
original=json.loads((data/"local.json").read_text())["instance_id"]
boot_twice()
command(probe,data,"read")
backup=root/"private-backups"/"snapshot.zip"
print("Comprobando respaldo y restauración sin perder el pendiente", flush=True)
command("from local_pos.runtime import backup; import sys; backup(sys.argv[1],sys.argv[2])",data,backup)
# Ambas rutas están dentro del directorio temporal creado exclusivamente aquí.
saved=root/"original-preserved"
assert data.parent==root and saved.parent==root and not saved.exists()
data.rename(saved)
command("from local_pos.runtime import restore; import sys; restore(sys.argv[1],sys.argv[2],confirm_instance=sys.argv[3])",data,backup,original)
command(probe,data,"read")
assert json.loads((data/"local.json").read_text())["instance_id"]==original
print(json.dumps({"passed":True,"system":platform.system(),"pg_bin":str(pg_bin),"restarts":2,"pending_preserved":True,"backup_restore":True,"production_access":False,"directory":str(root)}))
