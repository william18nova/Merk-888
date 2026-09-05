import importlib
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import URLPattern, URLResolver


class PermissionMigrationAtomicityTests(SimpleTestCase):
    def test_cleanup_commits_before_creating_case_insensitive_index(self):
        from django.db.migrations.operations.models import AddConstraint
        from django.db.migrations.operations.special import RunPython

        migration = importlib.import_module(
            "mainApp.migrations.0029_permission_catalog_cleanup"
        ).Migration

        self.assertFalse(migration.atomic)
        self.assertIsInstance(migration.operations[0], RunPython)
        self.assertTrue(migration.operations[0].atomic)
        self.assertIsInstance(migration.operations[1], AddConstraint)


class PermissionCatalogAuditTests(SimpleTestCase):
    """Invariantes del catálogo y de su relación con las rutas reales."""

    def test_public_routes_are_not_mapped_to_permissions(self):
        from .permissions import PUBLIC_URL_NAMES, ROUTE_PERMISSIONS

        self.assertEqual(
            set(PUBLIC_URL_NAMES) & set(ROUTE_PERMISSIONS),
            set(),
        )

    def test_every_assignable_permission_has_a_real_consumer(self):
        from .permissions import (
            PERMISSION_DEFINITIONS,
            PERMISSION_IMPLICATIONS,
            ROUTE_PERMISSION_ALTERNATIVES,
            ROUTE_PERMISSIONS,
            WEB_MASTER_ONLY_URL_NAMES,
        )

        used_codes = {
            code
            for url_name, code in ROUTE_PERMISSIONS.items()
            if url_name not in WEB_MASTER_ONLY_URL_NAMES
        }
        used_codes.update(
            code
            for url_name, alternatives in ROUTE_PERMISSION_ALTERNATIVES.items()
            if url_name not in WEB_MASTER_ONLY_URL_NAMES
            for code in alternatives
        )
        for child_code, parent_codes in PERMISSION_IMPLICATIONS.items():
            used_codes.add(child_code)
            used_codes.update(parent_codes)

        assignable_codes = {
            definition["code"]
            for definition in PERMISSION_DEFINITIONS
            if definition.get("assignable", True)
        }
        self.assertEqual(
            sorted(assignable_codes - used_codes),
            [],
            "Hay permisos asignables que no habilitan ninguna ruta ni implicación.",
        )

    def test_web_master_only_permissions_are_system_only_and_not_assignable(self):
        from .permissions import PERMISSION_BY_CODE

        expected_codes = {
            "ventas_no_realizadas",
            "descuentos_especiales_generar",
            "configuracion_funcionalidades",
            "configuracion_impresion",
            "configuracion_metodos_pago",
            "configuracion_telegram_bot",
        }
        system_only_codes = {
            code
            for code, definition in PERMISSION_BY_CODE.items()
            if definition.get("system_only") is True
        }

        self.assertEqual(system_only_codes, expected_codes)
        for code in expected_codes:
            with self.subTest(code=code):
                self.assertIs(
                    PERMISSION_BY_CODE[code].get("assignable"),
                    False,
                )

    def test_public_barcode_view_is_not_an_assignable_catalog_permission(self):
        from .permissions import PERMISSION_DEFINITIONS

        catalog_codes = {
            definition["code"] for definition in PERMISSION_DEFINITIONS
        }
        catalog_aliases = {
            alias
            for definition in PERMISSION_DEFINITIONS
            for alias in definition.get("aliases", [])
        }

        self.assertNotIn("visor_barcode", catalog_codes)
        self.assertNotIn("visor_barcode", catalog_aliases)

    def test_internal_web_master_policies_are_rejected_by_assignment_guard(self):
        from .permissions import is_assignable_permission_name

        for permission_name in (
            "Ventas no realizadas",
            "Configurar impresión",
            "Administrar métodos de pago",
            "Administrar bot inteligente de Telegram",
            "Visor Barcode",
        ):
            with self.subTest(permission_name=permission_name):
                self.assertFalse(
                    is_assignable_permission_name(permission_name)
                )

        self.assertTrue(is_assignable_permission_name("Generar venta"))

    def test_shared_autocompletes_accept_all_permissions_that_use_them(self):
        from .permissions import ROUTE_PERMISSION_ALTERNATIVES

        expectations = {
            "categoria_autocomplete": "productos_editar",
            "producto_autocomplete_global": "ventas_ver",
            "producto_detalle_inventario": "inventarios_editar",
        }
        for url_name, permission_code in expectations.items():
            with self.subTest(url_name=url_name):
                self.assertIn(
                    permission_code,
                    ROUTE_PERMISSION_ALTERNATIVES.get(url_name, []),
                )

    def test_nequi_delete_routes_use_their_own_permission(self):
        from .permissions import PERMISSION_BY_CODE, ROUTE_PERMISSIONS

        delete_code = "nequi_notificaciones_eliminar"
        self.assertIn(delete_code, PERMISSION_BY_CODE)
        self.assertEqual(
            ROUTE_PERMISSIONS.get("nequi_notificacion_eliminar"),
            delete_code,
        )
        self.assertEqual(
            ROUTE_PERMISSIONS.get(
                "nequi_notificaciones_eliminar_seleccionadas"
            ),
            delete_code,
        )


class PermissionRouteAuditTests(SimpleTestCase):
    @classmethod
    def _url_names(cls):
        from .urls import urlpatterns

        names = set()

        def visit(patterns):
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    if pattern.name:
                        names.add(pattern.name)
                elif isinstance(pattern, URLResolver):
                    visit(pattern.url_patterns)

        visit(urlpatterns)
        return names

    @staticmethod
    def _nav_names():
        from .permissions import NAV_GROUPS

        names = set()

        def visit(items):
            for item in items:
                if item.get("url_name"):
                    names.add(item["url_name"])
                visit(item.get("children") or [])

        visit(NAV_GROUPS)
        return names

    def test_manual_permission_create_edit_delete_routes_and_nav_are_removed(self):
        obsolete_names = {
            "permiso_agregar",
            "editar_permiso",
            "eliminar_permiso",
        }

        self.assertEqual(obsolete_names & self._url_names(), set())
        self.assertEqual(obsolete_names & self._nav_names(), set())

    def test_role_permission_relation_cannot_be_deleted_with_get(self):
        from .views import eliminar_rol_permiso_view

        request = RequestFactory().get("/roles_permisos/eliminar/7/")
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("mainApp.views.RolPermiso.objects") as objects:
            response = eliminar_rol_permiso_view(request, rp_id=7)

        self.assertEqual(response.status_code, 405)
        objects.select_related.assert_not_called()


class PermissionDecisionAuditTests(SimpleTestCase):
    def test_administradora_role_is_recognized_as_permission_admin(self):
        from .permissions import is_permission_admin

        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
        )
        with patch("mainApp.permissions.role_name", return_value="Administradora"):
            self.assertTrue(is_permission_admin(user))

    def test_direct_deny_overrides_admin_role_default_access(self):
        from .permissions import user_has_permission

        user = SimpleNamespace(is_authenticated=True)
        state = {
            "role": {"productos_ver"},
            "allow": set(),
            "deny": {"productos_ver"},
        }
        with (
            patch("mainApp.permissions.is_permission_admin", return_value=True),
            patch("mainApp.permissions._load_permission_state", return_value=state),
        ):
            allowed = user_has_permission(user, "productos_ver")

        self.assertFalse(allowed)

    def test_webmaster_compact_variant_is_also_privileged(self):
        from .permissions import is_privileged_role_name

        self.assertTrue(is_privileged_role_name("Webmaster"))

    def test_last_active_web_master_cannot_be_demoted(self):
        from .views import UsuarioUpdateAJAXView

        view = UsuarioUpdateAJAXView()
        view.object = SimpleNamespace(pk=9)
        view.request = SimpleNamespace(
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        form = MagicMock()
        form.cleaned_data = {
            "rolid": SimpleNamespace(nombre="Cajero"),
        }
        form.errors.get_json_data.return_value = {
            "rolid": [{"message": "protegido"}],
        }
        current = SimpleNamespace(pk=9, is_active=True)

        with (
            patch(
                "mainApp.views._lock_user_and_web_master_count",
                return_value=(current, "Web Master", 1),
            ),
            patch("mainApp.views.transaction.atomic", return_value=nullcontext()),
        ):
            response = view.form_valid(form)

        self.assertEqual(response.status_code, 400)
        form.add_error.assert_called_once()
        form.save.assert_not_called()


class SaleBranchPermissionAuditTests(SimpleTestCase):
    def test_cashier_cannot_open_sale_from_another_branch(self):
        from .views import VentaDetailView

        request = RequestFactory().get(
            "/ver_venta/91/",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(
            is_authenticated=True,
            rolid=SimpleNamespace(nombre="Cajero"),
        )
        sale = SimpleNamespace(
            pk=91,
            sucursalid_id=20,
            sucursalid=SimpleNamespace(pk=20),
        )

        with (
            patch("mainApp.views.get_object_or_404", return_value=sale),
            patch("mainApp.views._cajero_sucursal_id", return_value=10),
            patch("mainApp.views.DetalleVenta.objects") as detail_objects,
        ):
            detail_objects.filter.side_effect = AssertionError(
                "La vista consultó detalles antes de validar la sucursal."
            )
            response = VentaDetailView().get(request, venta_id=sale.pk)

        self.assertEqual(response.status_code, 403)
        detail_objects.filter.assert_not_called()
