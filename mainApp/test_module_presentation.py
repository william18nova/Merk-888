"""Contratos de presentación, sin base de datos y con formularios ficticios."""
from collections import Counter
from html.parser import HTMLParser
import re
from types import ModuleType

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import include, path

from scripts.ui_preview_fixtures import PAGE_NAMES, preview_context, render_ui_preview, safe_checkout_preview


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ModulePresentationTests(SimpleTestCase):
    def test_every_module_renders_one_document_and_keeps_unique_ids(self):
        for page in PAGE_NAMES:
            with self.subTest(page=page):
                html = render_ui_preview(page)
                elements = Elements(html).elements
                self.assertEqual(sum(tag == 'html' for tag, _ in elements), 1)
                self.assertEqual(sum(tag == 'head' for tag, _ in elements), 1)
                bodies = [attrs for tag, attrs in elements if tag == 'body']
                self.assertEqual(len(bodies), 1)
                self.assertIn('nova-ui', bodies[0]['class'].split())
                self.assertIn('css/nova_modules.css?v=1', html)
                self.assertIn('javascript/nova_modules.js?v=1', html)
                ids = Counter(attrs['id'] for _, attrs in elements if 'id' in attrs)
                self.assertEqual([name for name, count in ids.items() if count > 1], [])
                self.assertIn('fixture-not-an-authentication-token', html)

    def test_forms_preserve_post_actions_and_field_error_contracts(self):
        contracts = {
            'agregar_producto': 'productoForm', 'editar_producto': 'productoForm',
            'agregar_cliente': 'form-agregar-cliente', 'editar_cliente': 'clienteForm',
            'agregar_proveedor': 'form-agregar-proveedor', 'editar_proveedor': 'form-editar-proveedor',
            'agregar_pedido': 'pedidoForm', 'editar_pedido': 'pedidoForm',
        }
        for page, form_id in contracts.items():
            with self.subTest(page=page):
                elements = Elements(render_ui_preview(page)).elements
                form = next(attrs for tag, attrs in elements if tag == 'form' and attrs.get('id') == form_id)
                self.assertEqual(form['method'], 'post')
                self.assertIn('nova-form-grid', form['class'])
                self.assertTrue(form['action'].startswith('/'))
                self.assertTrue(any(attrs.get('name') == 'csrfmiddlewaretoken' for _, attrs in elements))
                self.assertTrue(any('field-error' in attrs.get('class', '') for _, attrs in elements))

    def test_list_tables_keep_all_columns_and_mobile_labels(self):
        expected = {'visualizar_productos': ('productosTable', 13), 'visualizar_inventarios': ('inventariosTable', 3),
                    'visualizar_pedidos': ('pedidosTable', 8), 'visualizar_proveedores': ('proveedoresTable', 6),
                    'visualizar_clientes': ('clientesTable', 6), 'visualizar_empleados': ('empleadosTable', 8),
                    'visualizar_usuarios': ('usuariosTable', 3), 'visualizar_categorias': ('categoriasTable', 3),
                    'visualizar_cambios': ('cambiosTable', 8)}
        for page, (table_id, columns) in expected.items():
            with self.subTest(page=page):
                html = render_ui_preview(page)
                elements = Elements(html).elements
                self.assertTrue(any(tag == 'table' and attrs.get('id') == table_id for tag, attrs in elements))
                self.assertEqual(sum(tag == 'th' for tag, _ in elements), columns)
                if page != 'visualizar_productos':
                    cells = [attrs for tag, attrs in elements if tag == 'td']
                    self.assertTrue(all('data-label' in attrs for attrs in cells))

    def test_shared_styles_are_opt_in_and_keep_readable_contrast(self):
        css = (settings.BASE_DIR / 'mainApp/static/css/nova_modules.css').read_text(encoding='utf-8')
        self.assertNotIn(':root', css)
        self.assertIn('body.nova-ui', css)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('content: attr(data-label)', css)
        self.assertNotIn('translateX(-50%)', css.split('@media (max-width: 760px)')[1])
        palette = dict(re.findall(r'(--nova-[\w-]+):\s*(#[0-9a-f]{6});', css))

        def luminance(color):
            values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
            return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

        pairs = [(text, background) for text in ('--nova-text', '--nova-muted')
                 for background in ('--nova-surface', '--nova-row', '--nova-row-alt', '--nova-head', '--nova-hover')]
        pairs += [('--nova-success-text', '--nova-success-bg'), ('--nova-error-text', '--nova-error-bg')]
        for foreground, background in pairs:
            with self.subTest(foreground=foreground, background=background):
                ratio = (luminance(palette[foreground]) + .05) / (luminance(palette[background]) + .05)
                self.assertGreaterEqual(ratio, 4.5)

    def test_legacy_styles_no_longer_override_other_modules(self):
        names = ['visualizar_productos', 'visualizar_inventarios', 'visualizar_pedidos', 'visualizar_proveedores',
                 'visualizar_clientes', 'visualizar_empleados', 'visualizar_usuarios', 'visualizar_categorias',
                 'agregar_producto', 'agregar_cliente', 'agregar_proveedor', 'agregar_pedido', 'reportes_pedidos_pagados']
        for name in names:
            with self.subTest(style=name):
                css = (settings.BASE_DIR / ('mainApp/static/css/' + name + '.css')).read_text(encoding='utf-8')
                self.assertNotIn(':root', css)
                self.assertNotIn('@import', css)
                self.assertNotRegex(css, r'(?m)^body\s*\{')
        unchanged = render_to_string('visualizar_ventas.html', preview_context('visualizar_ventas'))
        self.assertNotIn('css/nova_modules.css', unchanged)

    def test_checkout_keeps_controls_scripts_and_local_runtime_styles(self):
        context = preview_context('generar_venta')
        html = render_to_string('generar_venta.html', context)
        for control in ('venta-form', 'generar-venta', 'vaciar-carrito', 'detalle-productos', 'myModal',
                        'confirmar-pago', 'mix-mode', 'cliente_id', 'sucursal_id', 'puntopago_id',
                        'productos', 'cantidades', 'pagos', 'nequi_notificacion_id'):
            self.assertIn('id="' + control + '"', html)
        self.assertIn('css/nova_checkout.css?v=2', html)
        self.assertIn('javascript/generar_venta.js?v=51', html)
        context.update(hybrid_local=True, hybrid_django_runtime=True, hybrid_sale_context={'preview': True})
        local = render_to_string('generar_venta.html', context)
        self.assertIn('/local/sales/assets/sale-bridge.css', local)
        self.assertIn('/local/sales/assets/sale-bridge.js', local)
        self.assertIn('css/nova_checkout.css?v=2', local)
        context.update(full_local_development=True, full_local_installed=False)
        # Solo se comprueba el contrato de plantilla; no se carga la BD del runtime.
        local_urls = ModuleType('presentation_local_urls')
        local_urls.urlpatterns = [
            path('local/estado/', lambda request: HttpResponse('preview'), name='local_runtime_status'),
            path('local/sincronizacion/', lambda request: HttpResponse('preview'), name='local_sync'),
            path('', include(settings.ROOT_URLCONF)),
        ]
        with self.settings(ROOT_URLCONF=local_urls):
            full_local = render_to_string('generar_venta.html', context)
        self.assertIn('class="nova-full-local nova-ui nova-ui--checkout"', full_local)

    def test_control_enhancer_only_changes_presentation(self):
        script = (settings.BASE_DIR / 'mainApp/static/javascript/nova_modules.js').read_text(encoding='utf-8')
        for forbidden in ('fetch(', 'XMLHttpRequest', 'localStorage', 'sessionStorage', '.submit(', '.requestSubmit('):
            self.assertNotIn(forbidden, script)
        self.assertIn('aria-label', script)

    def test_checkout_demo_cannot_execute_production_sale_script(self):
        html = safe_checkout_preview(render_ui_preview('generar_venta'))
        self.assertNotIn('src="/static/javascript/generar_venta.js', html)
        self.assertIn('no se registran ventas ni pagos', html)
        self.assertIn('data-label="Subtotal"', html)

    def test_checkout_uses_large_scoped_type_without_zooming_the_navigation(self):
        css = (settings.BASE_DIR / 'mainApp/static/css/nova_checkout.css').read_text(encoding='utf-8')
        sizes = {name: int(value) for name, value in re.findall(r'(--checkout-[\w-]+):\s*(\d+)px', css)}
        self.assertGreaterEqual(sizes['--checkout-text'], 20)
        self.assertGreaterEqual(sizes['--checkout-control'], 22)
        self.assertGreaterEqual(sizes['--checkout-small'], 16)
        self.assertIn('body.nova-ui--checkout', css)
        self.assertNotIn(':root', css)
        self.assertNotRegex(css, r'(?m)^\s*(?:body|html|\.navbar)\s*\{')
        self.assertNotRegex(css, r'\bzoom\s*:|scale\(')
        self.assertIn('@media (max-width: 1200px)', css)
        self.assertIn('@media (max-width: 860px)', css)
        self.assertIn('font-size: clamp(38px, 4.2vw, 48px)', css)
        self.assertIn('.nova-ui--checkout #myModal #confirmar-pago', css)
