"""Servidor de prueba visual: datos ficticios, sin Django ni internet."""
import sys
from pathlib import Path
import tempfile
import threading
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hybrid_client.client import Client
from hybrid_client.server import make_server
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud
from hybrid_client import printing

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--sale-ui', action='store_true')
    parser.add_argument('--port', type=int, default=8893)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        cloud = FakeCloud()
        client = Client(Store(folder), transport=cloud)
        client.enroll({"url":"https://example.test", "code":"demo"})
        client.start({"username":"demo", "password":"not-a-real-password", "pin":"demo-local"})
        if args.sale_ui:
            client.store.merge_catalog({'updates':[
                {'id':11,'name':'AGUA DE PRUEBA','price':'1800','stock':0,'barcode':'7700000000011','quote':'signed','digest':'b'*32},
                {'id':12,'name':'MANZANA ÁCIDA DE PRUEBA','price':'2200','stock':-10,'barcode':'7700000000012','quote':'signed','digest':'c'*32},
                {'id':13,'name':'ARROZ DE PRUEBA PAQUETE GRANDE CON NOMBRE LARGO','price':'12000','stock':4,'barcode':'7700000000013','quote':'signed','digest':'d'*32},
                {'id':7318,'name':'BOLSA GRANDE','price':'100','stock':0,'barcode':'7700000007318','quote':'signed','digest':'e'*32},
            ],'deleted':[]})
        cloud.offline = True
        # Esta demo JAMÁS llama al agente ni a CUPS, incluso al configurar impresión.
        printing.send_job = lambda payload, token: "Simulación: comprobante enviado, sin impresora real."
        stop = threading.Event()
        worker = threading.Thread(target=client.printer.worker, args=(stop,), daemon=True)
        worker.start()
        server = make_server(client, args.port)
        print(f"DEMO lista: http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            client.printer.wake.set()
            worker.join(timeout=2)
            server.server_close()
