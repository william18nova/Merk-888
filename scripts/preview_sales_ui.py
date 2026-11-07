"""Vista local de ventas con datos ficticios: solo GET, sin acceso a la base.

python -B scripts/preview_sales_ui.py --port 8967
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.test_settings'
import django
django.setup()
from django.urls import reverse
from mainApp.test_sales_presentation import sales_preview_html

BASE_ROWS=[
    ('Consumidor final','Andrea Martínez','Yerbabuena','Caja 1','76.900','efectivo',False,False),
    ('María Alejandra Rodríguez de la Fuente','Camilo Rojas','La Plaza','Caja 2','128.500','nequi',True,True),
    ('Andrés Gómez','Andrea Martínez','Yerbabuena','Caja 1','43.200','nequi',True,False),
    ('Comercializadora de alimentos y productos de consumo con nombre largo','Camilo Rojas','La Plaza','Caja 2','1.234.567','tarjeta',False,False),
    ('Consumidor final','Andrea Martínez','Yerbabuena','Caja 1','15.800','mixto',True,True),
    ('Laura Sánchez','Camilo Rojas','La Plaza','Caja 2','8.900','efectivo',False,False),
]
ROWS=[dict(ventaid=431290-i,fecha='07/11/2026',hora=f'{18-i//6:02d}:{58-i%6*4:02d}',
    cliente=row[0],empleado=row[1],sucursal=row[2],puntopago=row[3],total='$ '+row[4],mediopago=row[5],
    nequi_payment=row[6],nequi_linked=row[7]) for i in range(64) for row in [BASE_ROWS[i%len(BASE_ROWS)]]]


class Preview(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def send(self,body,mime='text/html; charset=utf-8',status=200):
        data=body.encode('utf-8') if isinstance(body,str) else body
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(data)

    def do_POST(self): self.send('Solo vista de prueba; no permite operaciones.',status=405)

    def do_GET(self):
        url=urlparse(self.path); args=parse_qs(url.query); value=lambda name: args.get(name,[''])[0]
        if url.path.startswith('/static/'):
            base=ROOT/'mainApp/static'; file=(base/unquote(url.path[8:])).resolve()
            if not file.is_relative_to(base.resolve()) or not file.is_file(): return self.send('No encontrado',status=404)
            return self.send(file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream')
        if url.path==reverse('ventas_datatable'):
            rows=list(ROWS); term=value('search[value]').casefold()
            if term: rows=[r for r in rows if term in ' '.join(map(str,r.values())).casefold()]
            for key,field in [('mediopago','mediopago'),('cliente_term','cliente'),('venta_id','ventaid')]:
                if value(key): rows=[r for r in rows if value(key).casefold() in str(r[field]).casefold()]
            if value('nequi_status'): rows=[r for r in rows if r['nequi_payment'] and r['nequi_linked']==(value('nequi_status')=='linked')]
            for key,field,options in [('sucursal_id','sucursal',{'1':'Yerbabuena','2':'La Plaza'}),('puntopago_id','puntopago',{'1':'Caja 1','2':'Caja 2'}),('empleado_id','empleado',{'1':'Andrea Martínez','2':'Camilo Rojas'})]:
                if value(key): rows=[r for r in rows if r[field]==options.get(value(key))]
            if value('producto_term') and value('producto_term').casefold() not in 'arroz diana agua 7700000000011': rows=[]
            if value('order[0][column]'):
                field=list(ROWS[0])[int(value('order[0][column]'))]
                rows.sort(key=lambda r:r[field],reverse=value('order[0][dir]')=='desc')
            count=len(rows); start=int(value('start') or 0); length=int(value('length') or 25)
            data=rows[start:] if length<0 else rows[start:start+length]
            return self.send(json.dumps(dict(draw=int(value('draw') or 1),recordsTotal=len(ROWS),recordsFiltered=count,data=data)),'application/json')
        if url.path==reverse('producto_autocomplete_global'):
            return self.send(json.dumps({'results':[{'id':11,'text':'ARROZ DIANA 500 G','barcode':'7700000000011'}]}),'application/json')
        if url.path==reverse('database_connection_status'):
            return self.send(json.dumps({'mode':'online','label':'Prueba local','description':'Datos ficticios; no consulta producción.'}),'application/json')
        if url.path.startswith(reverse('ver_venta',args=[0]).split('0')[0]):
            return self.send('<h1>Detalle de venta ficticia</h1><p>No hay conexión con producción.</p><a href="/visualizar_ventas/">Volver al historial</a>')
        if url.path in {'/','/visualizar_ventas/'}: return self.send(sales_preview_html())
        self.send('No encontrado',status=404)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--port',type=int,default=8967)
    args=parser.parse_args(); print(f'Vista ficticia: http://127.0.0.1:{args.port}/visualizar_ventas/',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Preview)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
