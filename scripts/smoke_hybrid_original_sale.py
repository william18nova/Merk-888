"""Prueba aislada de Generar Venta original. NUNCA conecta servicios reales."""
import json
import secrets
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hybrid_client.client import Client
from hybrid_client.server import make_server, sync_worker
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud
from hybrid_client import printing


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        cloud = FakeCloud()
        client = Client(Store(folder), transport=cloud)
        client.enroll({"url": "https://example.test", "code": "demo"})
        client.start({"username": "demo", "password": "ficticia", "pin": "demo-local"})
        client.store.merge_catalog({"updates": [
            {"id": 11, "name": "AGUA DE PRUEBA", "price": "1800", "stock": 0,
             "barcode": "7700000000011", "quote": "signed", "digest": "b"*32},
            {"id": 12, "name": "MANZANA ÁCIDA DE PRUEBA", "price": "2200", "stock": -10,
             "barcode": "7700000000012", "quote": "signed", "digest": "c"*32},
        ], "deleted": []})
        printing.send_job = lambda *_args: "PRUEBA SIN IMPRESORA"
        server = make_server(client, 8902)
        BaseHandler = server.RequestHandlerClass
        control = secrets.token_urlsafe(24)

        class TestHandler(BaseHandler):
            def do_POST(self):
                if self.path != "/__test__/network":
                    return super().do_POST()
                if not self.trusted() or not secrets.compare_digest(self.headers.get("X-Test-Control", ""), control):
                    return self.reply(403, {"error": "No autorizado"})
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 500:
                    return self.reply(400, {})
                data = json.loads(self.rfile.read(size))
                cloud.offline = data.get("offline") is True
                cloud.lose_ack = data.get("lose_ack") is True
                client.synchronize()
                self.reply(200, {"cloud_sales": cloud.counter, **client.status()})

        server.RequestHandlerClass = TestHandler
        stop = threading.Event()
        threading.Thread(target=sync_worker, args=(client, stop), daemon=True).start()
        # Token de control SOLO del simulador ficticio, entregado al test por archivo.
        output = Path(__file__).resolve().parents[1] / "outputs/hybrid-original"
        output.mkdir(parents=True, exist_ok=True)
        (output / "test-control.json").write_text(json.dumps({"token": control}), encoding="utf-8")
        print("PRUEBA SIN DATOS REALES: http://127.0.0.1:8902/generar_venta/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
