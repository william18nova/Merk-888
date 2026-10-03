"""Origen FICTICIO del laboratorio de réplica. Solo APIs en loopback."""
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4
from wsgiref.simple_server import make_server

REPO = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(REPO))
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.hybrid_local_settings"
    from django.conf import settings
    settings.DATABASES["default"]["NAME"] = "nova_full_local_source_lab"
    settings.SECRET_KEY = secrets.token_hex(48)
    settings.ROOT_URLCONF = "local_pos.test_replica_urls"
    settings.MIDDLEWARE = []
    import django
    django.setup()
    assert "NovaSoft.settings" not in sys.modules
    from django.core.management import call_command
    call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
    from scripts.full_local_lab import seed
    seed()
    from mainApp.models import ConfiguracionFuncionalidad, PuntosPago, Usuario
    from mainApp.services import hybrid
    from mainApp.services.feature_flags import clear_feature_cache, HYBRID_POS_FEATURE
    ConfiguracionFuncionalidad.objects.filter(clave=HYBRID_POS_FEATURE).update(habilitada=True)
    clear_feature_cache()
    user = Usuario.objects.get(nombreusuario="laboratorio")
    point = PuntosPago.objects.first()
    device, code = hybrid.create_device(actor=user, point=point, name="REPLICA FICTICIA")
    secret = secrets.token_hex(32)
    hybrid.enroll({"code": code, "secret": secret})
    device = hybrid.authenticate_device(f"Bearer {device.pk}.{secret}")
    session = hybrid.start_session(device, {"session_id": str(uuid4()), "username": user.nombreusuario,
                                             "password": "prueba-local-2026"})
    from django.core.wsgi import get_wsgi_application
    port = int(sys.argv[1])
    with make_server("127.0.0.1", port, get_wsgi_application()) as server:
        path = Path(settings.LOCAL_CONFIG["data_dir"]) / "replica-connection.json"
        path.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "device_id": str(device.pk),
                                   "secret": secret, "session_id": session["session_id"]}), encoding="utf-8")
        path.chmod(0o600)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
