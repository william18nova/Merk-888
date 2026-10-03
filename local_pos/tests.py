import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import SimpleTestCase, TransactionTestCase, RequestFactory, override_settings
from django.urls import reverse

from mainApp.models import Categoria, Inventario, Usuario
from .commands import CommandRegistry, LocalCommandError
from .config import load_config
from .middleware import LocalSafetyMiddleware
from .models import LocalCommand, LocalNode


class ConfigTests(SimpleTestCase):
    def test_runtime_never_imports_production_settings(self):
        self.assertNotIn("NovaSoft.settings", sys.modules)
        self.assertEqual(settings.DATABASES["default"]["HOST"], "127.0.0.1")
        self.assertEqual(settings.GEMINI_API_KEY, "")
        self.assertEqual(settings.TELEGRAM_BOT_TOKEN, "")

    def test_rejects_remote_or_additional_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            data = dict(settings.LOCAL_CONFIG, data_dir=tmp, host="remote.invalid")
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.dict(os.environ, NOVA_LOCAL_CONFIG=str(path)), self.assertRaises(RuntimeError):
                load_config()

    def test_rejects_invalid_secret_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            path.write_text(json.dumps(dict(settings.LOCAL_CONFIG, data_dir=tmp, password=None)), encoding="utf-8")
            with patch.dict(os.environ, NOVA_LOCAL_CONFIG=str(path)), self.assertRaises(RuntimeError):
                load_config()

    def test_rejects_live_mode_and_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            for changes in ({"mode": "live"}, {"database": "defaultdb"}):
                path.write_text(json.dumps(dict(settings.LOCAL_CONFIG, data_dir=tmp, **changes)), encoding="utf-8")
                with patch.dict(os.environ, NOVA_LOCAL_CONFIG=str(path)), self.assertRaises(RuntimeError):
                    load_config()


class AssetTests(SimpleTestCase):
    def test_cached_assets_can_be_built_without_network_and_css_is_local(self):
        from scripts.build_full_local_assets import build
        url = "https://code.jquery.com/ui/1.13.3/themes/base/jquery-ui.css"
        class FakeOpener:
            def open(self, request, **kwargs):
                if request.full_url.endswith(".css"):
                    return BytesIO(b'.icon{background:url("images/test.png")}')
                return BytesIO(b"fake-image-for-test")
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.build_full_local_assets.ROOT_URLS", [url]):
            root = Path(tmp)
            first = root / "first"
            first.mkdir()
            (first / "local.css").write_text('@import url("https://fonts.googleapis.com/css2?family=Roboto"); body{color:red}')
            with patch("scripts.build_full_local_assets.build_opener", return_value=FakeOpener()):
                manifest = build(first, root / "cache")
            with patch("scripts.build_full_local_assets.build_opener") as network:
                network.return_value.open.side_effect = AssertionError("Internet no disponible")
                again = build(root / "second", root / "cache")
                network.return_value.open.assert_not_called()
            self.assertEqual(manifest, again)
            self.assertNotIn("googleapis", (first / "local.css").read_text())
            css = first / "local_vendor" / manifest["integrity"][url]["file"]
            self.assertIn('/static/local_vendor/', css.read_text())

    def test_corrupted_asset_cache_is_not_silently_used(self):
        from scripts.build_full_local_assets import build
        import hashlib
        url = "https://code.jquery.com/jquery-3.7.1.min.js"
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.build_full_local_assets.ROOT_URLS", [url]):
            root = Path(tmp)
            key = hashlib.sha256(url.encode()).hexdigest()
            (root / (key + ".source")).write_bytes(b"altered")
            (root / (key + ".sha256")).write_text("incorrect")
            with self.assertRaisesRegex(RuntimeError, "caché"):
                build(root / "output", root)

    def test_arbitrary_remote_assets_are_rejected_without_a_request(self):
        from scripts.build_full_local_assets import build
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.build_full_local_assets.ROOT_URLS", ["https://private.invalid/script.js"]):
            with patch("scripts.build_full_local_assets.build_opener") as network:
                with self.assertRaises(RuntimeError):
                    build(Path(tmp) / "out", Path(tmp) / "cache")
                network.return_value.open.assert_not_called()


@override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1"])
class RuntimeTests(TransactionTestCase):
    def setUp(self):
        from scripts.full_local_lab import seed
        from django.core.cache import cache
        cache.clear()
        seed()
        self.user = Usuario.objects.get(nombreusuario="laboratorio")
        self.client.force_login(self.user)

    def test_original_pages_and_permissions_render(self):
        routes = ("local_runtime_status", "home", "visualizar_categorias", "visualizar_productos",
                  "visualizar_inventarios", "visualizar_clientes", "visualizar_proveedores",
                  "visualizar_empleados", "visualizar_ventas", "visualizar_pedidos",
                  "pagos_editar_lista", "calendario_empleados", "turnos_caja_dashboard", "metricas_negocio")
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "PRUEBAS LOCALES")
                self.assertEqual(response.headers["X-Nova-Mode"], "development-readonly")

    def test_no_post_put_patch_delete_business_writes(self):
        count = Categoria.objects.count()
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse("agregar_categoria"), {"nombre": "NO GUARDAR"})
                self.assertEqual(response.status_code, 409)
        self.assertEqual(Categoria.objects.count(), count)

    def test_get_deletion_and_external_integrations_blocked(self):
        cat = Categoria.objects.first()
        for url in (reverse("eliminar_categoria", args=[cat.pk]), reverse("imprimir_factura"),
                    reverse("telegram_webhook"), reverse("hybrid_enroll"), "/admin/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 409)
        self.assertTrue(Categoria.objects.filter(pk=cat.pk).exists())

    def test_json_failure_never_claims_it_was_queued(self):
        response = self.client.post(reverse("agregar_categoria"), {"nombre": "NO"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "sync_not_implemented")
        self.assertFalse(response.json()["success"])
        self.assertFalse(LocalCommand.objects.exists())

    def test_new_routes_fail_closed(self):
        response = self.client.get("/a-new-not-reviewed-route/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 409)

    def test_database_readonly_catches_even_swallowed_view_writes(self):
        request = RequestFactory().get("/home/", HTTP_ACCEPT="application/json")
        request.user = self.user
        def bad_get(_request):
            try:
                Categoria.objects.create(nombre="ACCIDENTAL GET WRITE")
            except Exception:
                pass
            return HttpResponse("se guardó")
        response = LocalSafetyMiddleware(bad_get)(request)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Categoria.objects.filter(nombre="ACCIDENTAL GET WRITE").exists())

    def test_auth_and_permissions_not_bypassed(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("visualizar_productos")).status_code, 302)
        from mainApp.models import Rol
        role = Rol.objects.create(nombre="SIN PERMISOS DE PRUEBA")
        user = Usuario.objects.create_user("limitado", "test-password", rolid=role)
        self.client.force_login(user)
        response = self.client.get(reverse("visualizar_productos"))
        self.assertIn(response.status_code, (302, 403))

    def test_local_login_works_and_cross_origin_login_rejected(self):
        self.client.logout()
        data = {"nombreusuario": "laboratorio", "contraseña": "prueba-local-2026"}
        response = self.client.post(reverse("login"), data, HTTP_ORIGIN="https://external.invalid")
        self.assertEqual(response.status_code, 409)
        response = self.client.post(reverse("login"), data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_inventory_negative_is_preserved(self):
        self.assertTrue(Inventario.objects.filter(cantidad__lt=0).exists())
        self.client.get(reverse("visualizar_inventarios"))
        self.assertTrue(Inventario.objects.filter(cantidad=-500).exists())

    def test_replica_access_requires_owner_lease_and_included_route(self):
        from .models import ReplicaState
        from django.utils import timezone
        from datetime import timedelta
        state = ReplicaState.objects.create(node=LocalNode.objects.get(), local_user=self.user,
            server="https://source.example.test", device_id=uuid4(), active_id=uuid4(),
            scope={"routes": ["visualizar_productos"]}, expires_at=timezone.now()+timedelta(hours=1))
        self.assertEqual(self.client.get(reverse("visualizar_productos")).status_code, 200)
        self.assertEqual(self.client.get(reverse("metricas_negocio")).status_code, 409)
        state.expires_at = timezone.now()-timedelta(seconds=1)
        state.save()
        self.assertEqual(self.client.get(reverse("visualizar_productos")).status_code, 409)
        state.expires_at = timezone.now()+timedelta(hours=1)
        state.local_user = Usuario.objects.create_user("another", "test-password", rolid=self.user.rolid)
        state.save()
        self.assertEqual(self.client.get(reverse("visualizar_productos")).status_code, 409)


class CommandTests(TransactionTestCase):
    def setUp(self):
        self.node = LocalNode.objects.create(id=settings.LOCAL_CONFIG["instance_id"])
        self.user = Usuario.objects.create_user("command-test", "test-password")
        self.registry = CommandRegistry()
        self.registry.register("test.category.create", permission=lambda actor: actor.pk == self.user.pk,
                               validate=lambda actor, data: {"nombre": data["nombre"].upper()},
                               apply=lambda actor, data: {"id": Categoria.objects.create(**data).pk})
        self.args = {"actor": self.user, "operation_id": uuid4(), "kind": "test.category.create",
                     "payload": {"nombre": "prueba"}}

    def test_same_operation_id_not_applied_twice(self):
        first = self.registry.execute(**self.args)
        self.assertEqual(self.registry.execute(**self.args), first)
        self.assertEqual(Categoria.objects.count(), 1)
        self.assertEqual(LocalCommand.objects.count(), 1)
        self.node.refresh_from_db()
        self.assertEqual(self.node.sequence, 1)
        self.assertEqual(LocalCommand.objects.get().payload, {"nombre": "PRUEBA"})

    def test_same_id_with_different_payload_is_rejected(self):
        self.registry.execute(**self.args)
        with self.assertRaises(LocalCommandError):
            self.registry.execute(**dict(self.args, payload={"nombre": "otro"}))
        self.assertEqual(Categoria.objects.count(), 1)

    def test_concurrent_duplicate_requests_commit_only_once(self):
        from django.db import connections
        def send():
            try:
                return self.registry.execute(**self.args)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Categoria.objects.count(), 1)
        self.assertEqual(LocalCommand.objects.count(), 1)

    def test_sequences_follow_commit_order_not_client_clock(self):
        self.registry.execute(**self.args)
        self.registry.execute(**dict(self.args, operation_id=uuid4(), payload={"nombre": "segunda"}))
        self.assertEqual(list(LocalCommand.objects.order_by("sequence").values_list("sequence", flat=True)), [1, 2])

    def test_inactive_actor_cannot_replay_or_create(self):
        self.registry.execute(**self.args)
        self.user.is_active = False
        with self.assertRaises(LocalCommandError):
            self.registry.execute(**self.args)
        self.assertEqual(LocalCommand.objects.count(), 1)

    def test_outbox_failure_rolls_back_business_and_sequence(self):
        with patch.object(LocalCommand.objects, "create", side_effect=IntegrityError("simulated journal failure")):
            with self.assertRaises(IntegrityError):
                self.registry.execute(**self.args)
        self.assertFalse(Categoria.objects.exists())
        self.node.refresh_from_db()
        self.assertEqual(self.node.sequence, 0)

    def test_validation_failure_leaves_nothing_pending(self):
        with self.assertRaises(KeyError):
            self.registry.execute(**dict(self.args, payload={}))
        self.assertFalse(LocalCommand.objects.exists())
        self.assertFalse(Categoria.objects.exists())

    def test_unknown_actions_and_unauthorized_actors_fail_closed(self):
        stranger = Usuario.objects.create_user("stranger", "test-password")
        for changes in ({"kind": "sql.execute"}, {"actor": stranger},
                        {"actor": SimpleNamespace(is_authenticated=False)}):
            with self.assertRaises(LocalCommandError):
                self.registry.execute(**dict(self.args, **changes))
        self.assertFalse(LocalCommand.objects.exists())

    def test_production_runtime_cannot_use_local_registry(self):
        with override_settings(HYBRID_LOCAL_ENABLED=False), self.assertRaises(LocalCommandError):
            self.registry.execute(**self.args)

    def test_no_business_adapters_registered_implicitly(self):
        from .commands import registry
        self.assertEqual(registry.handlers, {})
