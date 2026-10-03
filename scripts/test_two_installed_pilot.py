"""Tres PostgreSQL privados + TLS real: origen y dos instalaciones persistentes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--pg-bin", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8950)
    parser.add_argument("--pg-port", type=int, default=55470)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    root = Path(tempfile.mkdtemp(prefix="nova-two-installed-"))
    print(f"Laboratorio aislado: {root}",flush=True)
    env = {k:v for k,v in os.environ.items() if not k.startswith("PG") and k not in
           {"DATABASE_URL","DJANGO_SETTINGS_MODULE","NOVA_LOCAL_CONFIG","PYTHONPATH","SSL_CERT_FILE"}}
    env["PYTHONUTF8"] = "1"
    python = sys.executable
    hub, clients = root / "server", [root / "pc1", root / "pc2"]
    processes = []

    def call(code, *params, timeout=180):
        result = subprocess.run([python,"-B","-c", code, *map(str,params)], cwd=package, env=env,
                                 capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stdout[-1500:] + result.stderr[-3500:])
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("RESULT:"):
                return json.loads(line[7:])
        return None

    def start(code, path, port):
        log = (root / f"{path.name}-process.log").open("w",encoding="utf-8")
        process = subprocess.Popen([python,"-B","-c",code,str(path)],cwd=package,env=env,
            stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,encoding="utf-8")
        processes.append((process,log))
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if process.poll() is not None:
                log.flush()
                raise RuntimeError((root / f"{path.name}-process.log").read_text(encoding="utf-8")[-4000:])
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1",port))==0:
                    return process
            time.sleep(.2)
        raise RuntimeError("El proceso no inició a tiempo.")

    def stop(process):
        if process and process.poll() is None:
            process.communicate(input="stop\n",timeout=60)
            if process.returncode:
                raise RuntimeError("Un proceso no se detuvo limpiamente.")

    hub_code = """
import sys, threading
from local_pos.pilot import serve_hub
def ready(server):
    def stop():
        if sys.stdin.readline().strip() == 'stop': server.shutdown()
    threading.Thread(target=stop,daemon=True).start()
serve_hub(sys.argv[1], ready_callback=ready, display_credentials=False)
"""
    client_code = """
import sys, threading, _thread
from local_pos.runtime import serve
def ready(server):
    server.adj.asyncore_loop_timeout=.2
    def stop():
        if sys.stdin.readline().strip() == 'stop':
            _thread.interrupt_main(); server.pull_trigger()
    threading.Thread(target=stop,daemon=True).start()
try: serve(sys.argv[1], ready_callback=ready)
except KeyboardInterrupt: pass
"""
    operation_code = """
import sys,json
from pathlib import Path
from uuid import uuid4
from local_pos.runtime import installed_state, read_json, Postgres, django_setup, save_json
root=Path(sys.argv[1]); action=sys.argv[2]
state=installed_state(root); pg=Postgres(root,state['pg_bin'],read_json(root/'local.json')); pg.start()
try:
    django_setup(root)
    from django.db import connections
    from mainApp.models import Usuario, Producto, Inventario
    from local_pos.models import LocalCommand,LocalSaleSession
    from local_pos import sales,expenses,operations
    from local_pos.replica_transport import ReplicaRemote
    from hybrid_client.client import RemoteError
    actor=Usuario.objects.get(pk=state['local_user_id']); remote=ReplicaRemote(read_json(root/'replica-connection.json'))
    if action=='sync':
        sales.sync_cycle(remote,local_user_id=actor.pk); result={'ok':True}
    elif action=='status':
        result={'pending':LocalCommand.objects.exclude(state='accepted').count(),
                'accepted':LocalCommand.objects.filter(state='accepted').count(),
                'stocks':dict(Inventario.objects.values_list('productoid__nombre','cantidad')),
                'instance':state['instance_id']}
    elif action in ('cash','lost_ack','nequi','tarjeta','mixed'):
        product=Producto.objects.get(nombre='TOMATE FICTICIO X GR' if action in ('lost_ack','mixed') else 'ARROZ FICTICIO')
        quantity=500 if action=='lost_ack' else 100 if action=='mixed' else 2 if action in ('nequi','tarjeta') else 1
        total=product.precio*quantity
        data={'operation_id':str(uuid4()),'session_id':remote.session_id,'items':[{'id':product.pk,'quantity':quantity}],
              'cash_received':str(total),'expected_total':str(total)}
        if action in ('nequi','tarjeta'):
            data.update(cash_received='0', payments=[{'medio_pago':action,'monto':str(total)}])
        if action=='mixed':
            data.update(cash_received='180', payments=[{'medio_pago':'efectivo','monto':'180'},{'medio_pago':'nequi','monto':'200'}])
        if action=='lost_ack':
            original=remote.call
            def lost(action,data):
                value=original(action,data)
                if action=='sale': raise RemoteError('Acuse perdido de prueba')
                return value
            remote.call=lost
        result=sales.checkout(actor,data,remote)
        if action=='cash': save_json(root/'test-sale.json',result)
    elif action=='expense':
        method=sys.argv[3]
        result=expenses.create(actor,{'operation_id':str(uuid4()),'session_id':remote.session_id,
               'concept':' PAGO FICTICIO ', 'amount_base':'1000','method':method,'expected_tax':method=='nequi'},remote)
    elif action=='snapshot':
        result=operations.sale_snapshot(actor,read_json(root/'test-sale.json')['sale_id'],remote)
        save_json(root/'test-return.json',result)
        result={'ok':True}
    elif action=='return':
        snap=read_json(root/'test-return.json'); item=snap['sale']['items'][0]
        result=operations.create_return(actor,{'operation_id':str(uuid4()),'session_id':remote.session_id,
               'data':{'sale_id':snap['sale']['sale_id'],'items':[{'detail_id':item['detail_id'],'quantity':1}],
                       'expected_total':'2500','refunds':{'efectivo':'2500'},'authorization':snap['authorization']}},remote)
    elif action=='close':
        session=LocalSaleSession.objects.get()
        result=operations.create_close(actor,{'operation_id':str(uuid4()),'session_id':remote.session_id,
            'data':{'turn_id':session.data['turn_id'],'cash_counted':sys.argv[3],'bills_paid':'0',
                    'methods':{'nequi':sys.argv[4],'tarjeta':sys.argv[5]},'ptm_count':0}},remote)
    else: raise ValueError(action)
    print('RESULT:'+json.dumps(result,default=str))
finally:
    connections.close_all(); pg.stop()
"""
    def op(index, action, *values):
        return call(operation_code,clients[index],action,*values)

    def summary():
        return call("import sys,json; from local_pos.pilot import summary; print('RESULT:'+json.dumps(summary(sys.argv[1])))",hub)

    source = None
    try:
        print("Creando servidor y dos PostgreSQL independientes; TLS verificado.",flush=True)
        call("import sys; from local_pos.pilot import initialize_hub; initialize_hub(sys.argv[1],sys.argv[2],host='127.0.0.1',port=int(sys.argv[3]),pg_port=int(sys.argv[4]))",
             hub,args.pg_bin,args.port,args.pg_port)
        for i,client in enumerate(clients):
            call("import sys; from local_pos.runtime import initialize; initialize(sys.argv[1],sys.argv[2],username='laboratorio',password='solo-ficticio-2026',http_port=int(sys.argv[3]),pg_port=int(sys.argv[4]))",
                 client,args.pg_bin,args.port+i+1,args.pg_port+i+1)
        source=start(hub_code,hub,args.port)
        for i,client in enumerate(clients):
            call("""import sys; from pathlib import Path; from local_pos.runtime import read_json; from local_pos.pilot import connect
hub=Path(sys.argv[2]); a=read_json(hub/'pilot-hub.json')['accounts'][sys.argv[3]]; link=read_json(hub/'conexion-pruebas.json')
connect(sys.argv[1],hub/'conexion-pruebas.json',username=a['username'],password=a['password'],code=a['code'],confirm_fingerprint=link['sha256'])""",client,hub,i+1)
            op(i,'sync')
        assert op(0,'status')['instance'] != op(1,'status')['instance']
        print("Ventas simultáneas y respuesta perdida: verificando UUID/idempotencia.",flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda index:op(index,'cash'),(0,1)))
        assert all(result['state']=='accepted' for result in results),results
        assert op(0,'lost_ack')['state']=='pending'
        op(0,'sync'); op(0,'sync')
        assert summary()['sales']==3
        op(0,'snapshot')
        stop(source); source=None
        print("Servidor detenido: ventas, medios, egresos, devolución y reinicio sin conexión.",flush=True)
        assert op(0,'nequi')['state']=='pending'
        assert op(1,'tarjeta')['state']=='pending'
        assert op(0,'mixed')['state']=='pending'
        assert op(0,'expense','nequi')['state']=='pending'
        assert op(1,'expense','efectivo')['state']=='pending'
        assert op(0,'return')['state']=='pending'
        before=[op(i,'status') for i in (0,1)]
        assert [r['pending'] for r in before]==[4,2],before
        running=[start(client_code,clients[i],args.port+i+1) for i in (0,1)]
        for i in (0,1):
            with urlopen(f'http://127.0.0.1:{args.port+i+1}/',timeout=15) as response:
                assert response.status==200
        for process in running: stop(process)
        after=[op(i,'status') for i in (0,1)]
        assert before==after
        source=start(hub_code,hub,args.port)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda index:op(index,'sync'),(0,1)))
        # Download a final shared snapshot after both uploads have committed.
        for i in (0,1): op(i,'sync')
        reconciled=summary()
        assert (reconciled['sales'],reconciled['expenses'],reconciled['returns'])==(6,2,1),reconciled
        expected={'ARROZ FICTICIO':15,'TOMATE FICTICIO X GR':-1100}
        for i in (0,1):
            status=op(i,'status')
            assert status['pending']==0 and status['stocks']==expected,status
        assert {r['empleadoid__usuarioid__nombreusuario']:r['count'] for r in reconciled['sales_by_author']}=={'prueba1':4,'prueba2':2}
        print("Reconciliados sin duplicar. Probando cierres offline y aceptación posterior.",flush=True)
        stop(source); source=None
        assert op(0,'close','2080','5200','0')['state']=='pending'
        assert op(1,'close','2500','0','5000')['state']=='pending'
        source=start(hub_code,hub,args.port)
        for i in (0,1): op(i,'sync'); op(i,'sync')
        final=summary()
        assert final['operations']==11 and final['sales']==6 and all(t['estado']=='CERRADO' for t in final['turns']),final
        assert all(op(i,'status')['pending']==0 for i in (0,1))
        report={'passed':True,'system':platform.system(),'package':str(package),'directory':str(root),
                'independent_postgres_clusters':3,'two_installed_clients':True,'tls_verified':True,
                'same_time_sales':True,'server_unavailable':True,'offline_restart':True,'lost_ack_no_duplicate':True,
                'payment_methods':['efectivo','nequi','tarjeta','mixto'],'expense_4xmil':True,
                'return_and_close':True,'converged_stock':expected,'final':final,'production_access':False,'printing':False}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print('PASS: dos instalaciones persistentes reconciliadas; sin producción ni impresión.',flush=True)
    finally:
        for process,log in reversed(processes):
            try: stop(process)
            finally: log.close()


if __name__ == '__main__':
    main()
