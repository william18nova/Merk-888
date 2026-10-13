"""Fresh, read-only cloud diagnostics. No passwords or raw exceptions printed."""
import os
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.settings"
    import django
    django.setup()
    import psycopg2
    from django.db import connections
    from local_pos.cloud_health import classify_error
    params = connections["default"].get_connection_params()
    params.update(connect_timeout=6)
    params["options"] = params.get("options", "") + " -c statement_timeout=3000 -c default_transaction_read_only=on"
    print("Tres conexiones nuevas de solo lectura; no se cambian datos ni protecciones.")
    passed = 0
    for index in range(3):
        started, connection = time.monotonic(), None
        try:
            connection = psycopg2.connect(**params)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1, to_regclass('solicitudes_pos') IS NOT NULL")
                result = cursor.fetchone()
            passed += result == (1, True)
            print(f"Prueba {index+1}: SQL correcto; confirmaciones={'disponibles' if result[1] else 'faltan'}; {time.monotonic()-started:.2f}s")
        except psycopg2.Error as exc:
            print(f"Prueba {index+1}: {classify_error(exc).value}; {time.monotonic()-started:.2f}s")
        finally:
            if connection is not None: connection.close()
    print(f"Resultado: {passed}/3. Una muestra correcta no garantiza que la red no vuelva a interrumpirse.")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    sys.exit(run())
