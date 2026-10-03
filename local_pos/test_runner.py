"""Limpieza acotada de bases ficticias; no se usa en el POS instalado."""
from django.test.runner import DiscoverRunner


class LocalLabRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        saved = []
        try:
            for connection, old_name, destroy in old_config:
                config = connection.settings_dict
                if (config.get("HOST") != "127.0.0.1"
                        or config.get("USER") != "nova_full_local_lab"
                        or not config.get("NAME", "").startswith("test_nova_full_local_")
                        or not old_name.startswith("nova_full_local_")):
                    raise RuntimeError("La limpieza solo admite bases del laboratorio aislado.")
                original = config.get("OPTIONS", {})
                saved.append((config, original))
                # DROP DATABASE fuerza un checkpoint que puede tardar más de
                # 15 s en Windows. El límite habitual sigue activo en todos
                # los tests y en el runtime; solo cambia la conexión de limpieza.
                config["OPTIONS"] = {**original, "options": original.get("options", "")
                                     + " -c statement_timeout=120000"}
            return super().teardown_databases(old_config, **kwargs)
        finally:
            for config, original in saved:
                config["OPTIONS"] = original
