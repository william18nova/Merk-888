"""Galería local con datos ficticios. No utiliza la base ni registra operaciones.

python -B scripts/preview_modules_ui.py --port 8971
"""
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.test_settings'
import django
django.setup()
from django.urls import reverse
from django.template import engines
from scripts.ui_preview_fixtures import PAGE_NAMES, render_ui_preview, safe_checkout_preview


def example_html(page):
    # El servidor de revisión refleja cambios de plantilla al recargar.
    for loader in engines['django'].engine.template_loaders:
        if hasattr(loader, 'reset'):
            loader.reset()
    html = render_ui_preview(page)
    if page == 'generar_venta':
        html = safe_checkout_preview(html)
    banner = ('<aside class="preview-banner">Vista de prueba · Datos ficticios · '
              'No guarda operaciones <a href="/__preview__/">Ver todas las pantallas</a></aside>')
    style = ('<style>.preview-banner{padding:9px 14px;border:1px solid #386858;border-radius:8px;'
             'background:#173f39;color:#a0e6be;font:12px/1.5 system-ui;margin-top:12px;'
             'display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}'
             '.preview-banner a{color:#c9f8dd}</style>')
    return html.replace('</head>', style + '</head>').replace('<main class="page-wrap">', '<main class="page-wrap">' + banner)


def gallery_html():
    cards = ''.join('<a href="/' + page + '/"><span>' + escape(label) + '</span><small>' +
                    ('Venta y modal de pago' if page == 'generar_venta' else
                     'Formulario adaptable' if page.startswith(('agregar_', 'editar_')) else
                     'Consulta y listado adaptable') + '</small><b>Ver pantalla ↗</b></a>'
                    for page, label in PAGE_NAMES.items())
    return '''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>NovaPOS · Galería de diseño</title><style>
    *{box-sizing:border-box}body{margin:0;padding:clamp(20px,4vw,50px);background:#0e2341;color:#dce9f9;font:15px/1.6 system-ui}
    main{max-width:1400px;margin:auto}h1{font-size:clamp(28px,4vw,42px);letter-spacing:-.04em;line-height:1.2;margin:0 0 12px}
    .label{color:#a4ccff;letter-spacing:.12em;font-size:11px;margin:0 0 12px}p{color:#afc0d6;margin:0 0 30px}
    section{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:16px}
    a{display:flex;flex-direction:column;gap:12px;padding:22px;border:1px solid #36506d;border-radius:14px;background:#162f4e;color:#edf4ff;text-decoration:none}
    a:hover{background:#234466;border-color:#7ea3d2}a:focus-visible{outline:3px solid #91c3ff;outline-offset:4px}
    a span{font-size:19px;font-weight:650}a small{color:#afc0d6}a b{font-size:12px;font-weight:600;color:#a4ccff;margin-top:auto}
    </style><main><div class="label">NOVA POS · PRUEBAS LOCALES</div><h1>Una misma identidad, en todas las pantallas</h1>
    <p>20 pantallas con datos ficticios. Se adaptan a escritorio, tablet y celular. Esta galería no consulta producción ni guarda operaciones.</p>
    <section>''' + cards + '</section></main></html>'


def product_rows():
    names = ['Arroz blanco 500 g', 'Leche entera 1 L', 'Azúcar blanca 1 kg',
             'Producto con un nombre largo para comprobar que la información se conserva']
    return [dict(productoid=801 + i, nombre=names[i % 4], descripcion='Presentación de ejemplo para pruebas.',
                 precio='$ 4.800', precio_anterior='$ 4.500', categoria='Alimentos',
                 codigo_de_barras='77000000000' + str(i % 10) + '1', iva='0%', impuesto_consumo='0%',
                 icui='0%', ibua='$ 0', rentabilidad='25%',
                 acciones='<div class="btn-container"><a class="btn editar" href="/editar_producto/" title="Editar producto de prueba"><i class="fas fa-edit"></i></a><button type="button" class="btn borrar" aria-label="Eliminar producto de ejemplo"><i class="fas fa-trash-alt"></i></button></div>')
            for i in range(18)]


class Preview(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, mime='text/html; charset=utf-8', status=200):
        payload = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value, status=200):
        self.send(json.dumps(value), 'application/json; charset=utf-8', status)

    def do_POST(self):
        # Respuestas simuladas: no existe ninguna llamada a modelos o servicios.
        path = urlparse(self.path).path
        if path == reverse('pedidos_pagados'):
            return self.send_json({'success': True, 'cantidad': 12, 'total': '2480000'})
        if path == '/visualizar_inventarios/':
            return self.send(example_html('visualizar_inventarios'))
        self.send_json({'success': False, 'message': 'Vista de prueba: no se guardan operaciones.'}, 405)

    def do_GET(self):
        url = urlparse(self.path)
        args = parse_qs(url.query)
        if url.path.startswith('/static/'):
            base = (ROOT / 'mainApp/static').resolve()
            target = (base / unquote(url.path[8:])).resolve()
            if not target.is_relative_to(base) or not target.is_file():
                return self.send('No encontrado', status=404)
            return self.send(target.read_bytes(), mimetypes.guess_type(target)[0] or 'application/octet-stream')
        if url.path in {'/', '/__preview__/'}:
            return self.send(gallery_html())
        if url.path == reverse('productos_datatable'):
            rows = product_rows()
            term = args.get('search[value]', [''])[0].casefold()
            if term:
                rows = [row for row in rows if term in ' '.join(str(value) for key, value in row.items() if key != 'acciones').casefold()]
            count = len(rows)
            start = int(args.get('start', ['0'])[0])
            length = int(args.get('length', ['25'])[0])
            selected = rows[start:] if length < 0 else rows[start:start + length]
            return self.send_json({'draw': int(args.get('draw', ['1'])[0]), 'recordsTotal': 18,
                                   'recordsFiltered': count, 'data': selected})
        if url.path == reverse('database_connection_status'):
            return self.send_json({'mode': 'online', 'label': 'Vista de prueba', 'description': 'Datos ficticios; sin acceso a la base.'})
        if 'autocomplete' in url.path:
            text = ('Alimentos' if 'categoria' in url.path else 'Proveedor de prueba' if 'proveedor' in url.path else
                    'Arroz blanco 500 g' if 'producto' in url.path else 'Caja de prueba' if 'punto' in url.path else 'Yerbabuena')
            return self.send_json({'results': [{'id': 1, 'text': text, 'nombre': text, 'precio': '4800',
                                               'codigo_de_barras': '7700000000011'}], 'has_more': False})
        page = url.path.strip('/').split('/')[0]
        if page in PAGE_NAMES:
            return self.send(example_html(page))
        self.send('Esta operación no forma parte de la galería.', status=404)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8971)
    options = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', options.port), Preview)
    print(f'Galería ficticia: http://127.0.0.1:{options.port}/__preview__/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
