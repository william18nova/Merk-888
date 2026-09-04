import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LocalAutocompleteAssetsContractTests(SimpleTestCase):
    """Evita que los autocompletados dependan de code.jquery.com."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app_dir = Path(settings.BASE_DIR) / "mainApp"
        cls.static_dir = cls.app_dir / "static"
        cls.template = (
            cls.app_dir / "templates" / "generar_venta.html"
        ).read_text(encoding="utf-8")

    def test_sale_page_loads_pinned_jquery_and_jquery_ui_from_local_static(self):
        for local_reference in (
            "{% static 'vendor/jquery/jquery-3.6.4.min.js' %}",
            "{% static 'vendor/jquery-ui/jquery-ui-1.13.2.min.js' %}",
            "{% static 'vendor/jquery-ui/jquery-ui-1.13.2.min.css' %}",
        ):
            self.assertIn(local_reference, self.template)

        self.assertNotIn("code.jquery.com", self.template.lower())

    def test_pinned_javascript_and_stylesheet_assets_exist(self):
        expected_assets = (
            self.static_dir / "vendor" / "jquery" / "jquery-3.6.4.min.js",
            self.static_dir / "vendor" / "jquery-ui" / "jquery-ui-1.13.2.min.js",
            self.static_dir / "vendor" / "jquery-ui" / "jquery-ui-1.13.2.min.css",
        )

        for asset in expected_assets:
            with self.subTest(asset=asset.name):
                self.assertTrue(asset.is_file(), f"Falta el recurso local {asset}")
                self.assertGreater(asset.stat().st_size, 0, f"El recurso esta vacio: {asset}")

    def test_each_vendored_library_retains_its_license(self):
        vendor_dir = self.static_dir / "vendor"

        for library in ("jquery", "jquery-ui"):
            library_dir = vendor_dir / library
            licenses = tuple(library_dir.glob("LICENSE*"))
            with self.subTest(library=library):
                self.assertTrue(
                    licenses,
                    f"Falta la licencia distribuida con {library}",
                )
                self.assertTrue(
                    any(
                        license_path.is_file() and license_path.stat().st_size > 0
                        for license_path in licenses
                    ),
                    f"La licencia de {library} esta vacia",
                )

    def test_all_png_images_referenced_by_jquery_ui_css_are_local(self):
        jquery_ui_dir = self.static_dir / "vendor" / "jquery-ui"
        stylesheet = (
            jquery_ui_dir / "jquery-ui-1.13.2.min.css"
        ).read_text(encoding="utf-8")
        image_references = set(
            re.findall(r"url\([\"']?(images/[^)\"']+\.png)", stylesheet)
        )

        self.assertTrue(
            image_references,
            "La hoja de jQuery UI debe conservar sus imagenes de tema locales",
        )
        for reference in image_references:
            image = jquery_ui_dir / Path(reference)
            with self.subTest(image=reference):
                self.assertTrue(image.is_file(), f"Falta la imagen local {image}")
                self.assertGreater(image.stat().st_size, 0, f"La imagen esta vacia: {image}")
