"""Presentación de ventas: render aislado, sin base ni datos de producción."""
import os
import re
from pathlib import Path
from types import SimpleNamespace as NS

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase


def sales_preview_html():
    user=NS(pk=1,is_authenticated=True)
    return render_to_string('visualizar_ventas.html', {
        'user':user, 'request':NS(user=user,resolver_match=NS(url_name='visualizar_ventas')),
        'nav_session_name':'Usuario de prueba', 'nav_session_username':'Prueba local',
        'nav_connection':{'mode':'online','label':'Prueba local','description':'Datos ficticios. Sin acceso a producción.'},
        'nav_menu':[{'label':'Inicio','url':'/'},{'label':'Productos','url':'#'},
                    {'label':'Ventas','url':'/visualizar_ventas/','active':True}, {'label':'Caja','url':'#'}],
        'csrf_token':'fixture-not-an-authentication-token',
        'sucursales':[NS(sucursalid=1,nombre='Yerbabuena'),NS(sucursalid=2,nombre='La Plaza')],
        'puntos_pago':[NS(puntopagoid=1,sucursalid_id=1,sucursalid=NS(nombre='Yerbabuena'),nombre='Caja 1'),
                       NS(puntopagoid=2,sucursalid_id=2,sucursalid=NS(nombre='La Plaza'),nombre='Caja 2')],
        'empleados':[NS(empleadoid=1,nombre='Andrea',apellido='Martínez'),NS(empleadoid=2,nombre='Camilo',apellido='Rojas')],
        'medios_pago':[('efectivo','Efectivo'),('nequi','Nequi'),('tarjeta','Tarjeta / Banco Caja Social'),('mixto','Mixto')],
    })


class SalesPresentationTests(SimpleTestCase):
    def test_sales_owns_its_styles_and_keeps_all_existing_filter_contracts(self):
        html=sales_preview_html()
        self.assertIn('class="ventas-page"',html)
        self.assertIn('css/visualizar_ventas.css?v=6',html)
        self.assertNotIn('css/visualizar_pedidos.css',html)
        for key in ('buscador-ventas','filtro-producto','filtro-producto-id','filtro-producto-clear','filtro-producto-chip',
                    'ventas-filters-toggle','ventas-filters-body','ventas-filters-summary','filtro-fecha-desde',
                    'filtro-fecha-hasta','filtro-hora-desde','filtro-hora-hasta','filtro-sucursal','filtro-puntopago',
                    'filtro-mediopago','filtro-nequi-status','filtro-empleado','filtro-cliente','filtro-total-min',
                    'filtro-total-max','filtro-venta-id','filtro-devoluciones','limpiar-filtros-ventas','ventasTable'):
            self.assertEqual(html.count(f'id="{key}"'),1,key)
        self.assertEqual(html.count('scope="col"'),9)
        self.assertIn('for="buscador-ventas"',html)
        self.assertIn('for="filtro-producto"',html)
        self.assertIn('aria-label="Quitar filtro de producto"',html)
        self.assertIn('data-barcode-camera="true"',html)
        self.assertIn('javascript/visualizar_ventas.js?v=6',html)
        if os.environ.get('POS_TEST_ARTIFACT_DIR'):
            target=Path(os.environ['POS_TEST_ARTIFACT_DIR']); target.mkdir(parents=True,exist_ok=True)
            (target/'visualizar-ventas.html').write_text(html,encoding='utf-8')

    def test_styles_do_not_redefine_shared_navigation_tokens_or_use_mobile_translation(self):
        css=(settings.BASE_DIR/'mainApp/static/css/visualizar_ventas.css').read_text(encoding='utf-8')
        self.assertNotIn(':root',css)
        self.assertNotIn('.container-pedidos',css)
        self.assertNotIn('translateX(-50%)',css.split('@media (max-width: 760px)')[1])
        self.assertIn('prefers-reduced-motion',css)
        self.assertIn(':focus-visible',css)
        self.assertIn('.paginate_button.disabled',css)
        self.assertIn('.nequi-link-badge--unlinked',css)

    def test_dark_table_palette_keeps_text_readable_in_every_row_state(self):
        css = (settings.BASE_DIR / 'mainApp/static/css/visualizar_ventas.css').read_text(encoding='utf-8')
        palette = dict(re.findall(r'(--ventas-[\w-]+):\s*(#[0-9a-f]{6});', css))

        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
            return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

        for background in ('--ventas-surface', '--ventas-table-row', '--ventas-table-row-alt',
                           '--ventas-table-hover', '--ventas-table-head'):
            self.assertLess(luminance(palette[background]), .08, background)
            for foreground in ('--ventas-table-text', '--ventas-muted'):
                contrast = (luminance(palette[foreground]) + .05) / (luminance(palette[background]) + .05)
                self.assertGreaterEqual(contrast, 4.5, (foreground, background))
        self.assertIn('background: var(--ventas-table-row)', css)
        self.assertIn('background: var(--ventas-table-head)', css)
