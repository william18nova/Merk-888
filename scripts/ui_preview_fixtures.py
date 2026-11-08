"""Datos ficticios para revisar plantillas sin consultar modelos ni la base de datos."""
from datetime import date
from decimal import Decimal
import json
import re
from types import SimpleNamespace as NS

from django import forms
from django.template.loader import render_to_string


PAGE_NAMES = {
    'visualizar_productos': 'Productos',
    'visualizar_inventarios': 'Inventarios',
    'visualizar_pedidos': 'Pedidos',
    'visualizar_proveedores': 'Proveedores',
    'visualizar_clientes': 'Clientes',
    'visualizar_empleados': 'Empleados',
    'visualizar_usuarios': 'Usuarios',
    'visualizar_categorias': 'Categorías',
    'visualizar_cambios': 'Cambios y devoluciones',
    'agregar_producto': 'Agregar producto',
    'editar_producto': 'Editar producto',
    'agregar_cliente': 'Agregar cliente',
    'editar_cliente': 'Editar cliente',
    'agregar_proveedor': 'Agregar proveedor',
    'editar_proveedor': 'Editar proveedor',
    'agregar_pedido': 'Agregar pedido',
    'editar_pedido': 'Editar pedido',
    'ver_pedido': 'Detalle del pedido',
    'pedidos_pagados': 'Pedidos pagados',
    'generar_venta': 'Generar venta',
}


def preview_form(kind):
    """Formularios planos: ningún ModelChoiceField puede consultar producción."""
    form = forms.Form()
    fields = {
        'producto': [
            ('nombre', 'Nombre', 'text'), ('descripcion', 'Descripción', 'textarea'),
            ('precio', 'Precio de venta', 'number'), ('codigo_de_barras', 'Código de barras', 'text'),
            ('iva', 'IVA (%)', 'number'), ('impuesto_consumo', 'Impuesto al consumo', 'number'),
            ('icui', 'ICUI (%)', 'number'), ('ibua', 'IBUA', 'number'),
            ('rentabilidad', 'Rentabilidad (%)', 'number'), ('categoria', 'Categoría', 'hidden'),
        ],
        'cliente': [
            ('nombre', 'Nombre', 'text'), ('apellido', 'Apellido', 'text'),
            ('telefono', 'Teléfono', 'text'), ('email', 'Correo electrónico', 'email'),
            ('numerodocumento', 'Número de documento', 'text'),
        ],
        'proveedor': [
            ('nombre', 'Nombre del contacto', 'text'), ('empresa', 'Empresa', 'text'),
            ('telefono', 'Teléfono', 'text'), ('email', 'Correo electrónico', 'email'),
            ('direccion', 'Dirección', 'text'),
        ],
        'pedido': [
            ('proveedor_autocomplete', 'Proveedor', 'text'), ('proveedor', 'Proveedor', 'hidden'),
            ('sucursal_autocomplete', 'Sucursal', 'text'), ('sucursal', 'Sucursal', 'hidden'),
            ('fechaestimadaentrega', 'Fecha estimada', 'date'), ('comentario', 'Comentario', 'textarea'),
            ('estado', 'Estado', 'select'), ('monto_pagado', 'Monto pagado', 'number'),
            ('caja_pago_autocomplete', 'Caja de pago', 'text'), ('caja_pagoid', 'Caja', 'hidden'),
            ('detalles', 'Detalles', 'hidden'),
        ],
        'venta': [],
    }[kind]
    widgets = {'text': forms.TextInput, 'textarea': forms.Textarea, 'number': forms.NumberInput,
               'email': forms.EmailInput, 'date': forms.DateInput, 'hidden': forms.HiddenInput}
    for name, label, widget in fields:
        if widget == 'select':
            field = forms.ChoiceField(label=label, choices=[('Pendiente', 'Pendiente'), ('Recibido', 'Recibido')])
        else:
            attrs = {'class': 'form-control'}
            if widget == 'date':
                attrs['type'] = 'date'
            if widget == 'textarea':
                attrs['rows'] = 3
            field = forms.CharField(label=label, required=widget not in {'hidden', 'textarea'},
                                    widget=widgets[widget](attrs=attrs))
        form.fields[name] = field
    form.initial = {
        'nombre': 'Producto de ejemplo' if kind == 'producto' else 'Persona de prueba',
        'apellido': 'Ejemplo', 'descripcion': 'Presentación de ejemplo, sin datos reales.',
        'precio': '4800', 'codigo_de_barras': '7700000000011', 'iva': '0',
        'impuesto_consumo': '0', 'icui': '0', 'ibua': '0', 'rentabilidad': '25',
        'categoria': '1', 'id_categoria_autocomplete_initial': 'Alimentos',
        'telefono': '3000000000', 'email': 'ejemplo@example.invalid', 'numerodocumento': '999000001',
        'empresa': 'Distribuciones de ejemplo', 'direccion': 'Dirección ficticia para pruebas',
        'proveedor_autocomplete': 'Distribuciones de ejemplo', 'proveedor': '1',
        'sucursal_autocomplete': 'Yerbabuena', 'sucursal': '1', 'puntopago': '1',
        'fechaestimadaentrega': '2026-11-10', 'comentario': 'Ejemplo de pedido para revisar el diseño.',
        'estado': 'Pendiente', 'monto_pagado': '73400', 'caja_pago_autocomplete': 'Caja de prueba',
        'caja_pagoid': '1', 'detalles': '[]',
    }
    form.instance = NS(pk=1)
    return form


def preview_context(page):
    user = NS(pk=1, is_authenticated=True, rolid=NS(nombre='Web Master'))
    branch = NS(pk=1, sucursalid=1, nombre='Yerbabuena')
    products = [NS(pk=index, productoid=index, nombre=name) for index, name in enumerate([
        'Arroz blanco 500 g', 'Leche entera 1 L', 'Azúcar blanca 1 kg',
        'Producto de prueba con un nombre especialmente largo para comprobar el ajuste',
    ], 1)]
    providers = [NS(pk=index, proveedorid=index, nombre=name, empresa='Distribuciones de ejemplo',
                    telefono='3000000000', email='contacto@example.invalid', direccion='Dirección ficticia')
                 for index, name in enumerate(['Proveedor de prueba', 'Comercializadora de alimentos de ejemplo'], 1)]
    details = [NS(detallepedidoid=i + 1, productoid=product, cantidad=i + 2,
                  preciounitario=Decimal('4800'), subtotal=Decimal('4800') * (i + 2))
               for i, product in enumerate(products)]
    orders = [NS(pedidoid=120 - i, proveedorid=providers[i % 2], sucursalid=branch,
                 fechapedido=date(2026, 11, 7), fechaestimadaentrega='10/11/2026',
                 costototal=Decimal('73400') + i * 5000, estado='Pendiente' if i % 2 else 'Recibido',
                 comentario='Pedido ficticio para la vista previa.') for i in range(6)]
    people = [NS(pk=i + 1, clienteid=i + 1, empleadoid=i + 1, nombre=name, apellido='Ejemplo',
                 telefono='3000000000', email='persona@example.invalid', direccion='Dirección ficticia',
                 puesto='Cajero', numerodocumento='99900000' + str(i), es_merk2888=i == 3)
              for i, name in enumerate(['Persona de prueba', 'María de ejemplo', 'Andrés de prueba', 'Cliente protegido'])]
    form_kind = ('producto' if 'producto' in page and not page.startswith('visualizar') else
                 'cliente' if 'cliente' in page and not page.startswith('visualizar') else
                 'proveedor' if 'proveedor' in page and not page.startswith('visualizar') else
                 'pedido' if page in {'agregar_pedido', 'editar_pedido', 'ver_pedido'} else 'venta')
    return {
        'user': user, 'request': NS(user=user, resolver_match=NS(url_name=page)),
        'nav_session_name': 'Vista de prueba', 'nav_session_username': 'Datos ficticios',
        'nav_menu': [{'label': 'Ejemplos', 'url': '/__preview__/'},
                     {'label': 'Productos', 'url': '/visualizar_productos/'},
                     {'label': 'Pedidos', 'url': '/visualizar_pedidos/'},
                     {'label': 'Venta', 'url': '/generar_venta/'}],
        'csrf_token': 'fixture-not-an-authentication-token', 'form': preview_form(form_kind),
        'object': products[0], 'proveedor': providers[0], 'proveedores': providers,
        'clientes': people, 'empleados': people,
        'usuarios': [NS(usuarioid=i + 1, nombreusuario='usuario.prueba.' + str(i + 1),
                        rolid=NS(nombre=role)) for i, role in enumerate(['Cajero', 'Administrador', 'Web Master'])],
        'categorias': [NS(categoriaid=i + 1, nombre=name, descripcion='Categoría de ejemplo para la vista previa.')
                       for i, name in enumerate(['Alimentos', 'Bebidas', 'Hogar y cuidado personal'])],
        'inventarios': [NS(inventarioid=i + 1, productoid=product, cantidad=[124, 38, 0, 7][i])
                        for i, product in enumerate(products)],
        'sucursal_seleccionada': branch, 'pedidos': orders, 'pedido': orders[0],
        'detalles': details, 'detalles_qs': details,
        'detalles_json': json.dumps([{'productoid': d.productoid.pk, 'nombre': d.productoid.nombre,
                                     'cantidad': d.cantidad, 'precio_unitario': float(d.preciounitario),
                                     'subtotal': float(d.subtotal)} for d in details]),
        'cambios': [NS(cambioid=4, venta=NS(ventaid=431290), productoid=products[0], cantidad=1,
                       tipo='Devolución', estado='Pendiente', fecha=date(2026, 11, 7),
                       motivo='Ejemplo de motivo para comprobar textos largos.')],
        'venta_habilitada': True, 'turno_requerido': True, 'turno_activo': NS(pk=1), 'turno_id': 1,
        'sucursal_nombre': 'Yerbabuena', 'puntopago_nombre': 'Caja de prueba', 'total': '$ 73.400',
        'system_features': NS(nequi_api_recepcion=False), 'nequi_payment_method_enabled': True,
        'payment_methods': [NS(code=code, label=label, is_cash=code == 'efectivo', active=True)
                            for code, label in [('efectivo', 'Efectivo'), ('nequi', 'Nequi'),
                                                ('tarjeta', 'Banco Caja Social'), ('daviplata', 'Daviplata')]],
    }


def render_ui_preview(page):
    if page not in PAGE_NAMES:
        raise KeyError(page)
    return render_to_string(page + '.html', preview_context(page))


def safe_checkout_preview(html):
    """Solo el servidor de ejemplos sustituye la operación por una maqueta interactiva."""
    html = re.sub(r'<script[^>]+src="[^\"]*/(?:generar_venta|sale_draft_lifecycle|product_autocomplete)\.js[^\"]*"[^>]*></script>', '', html)
    rows = ''.join(
        '<tr><td data-label="Producto">' + name + '</td><td data-label="Cant.">' + str(qty) +
        '</td><td data-label="Precio U.">$ ' + price + '</td><td data-label="Subtotal">$ ' + subtotal +
        '</td><td data-label="Acc."><button type="button" class="btn-chip-danger eliminar-producto" aria-label="Quitar producto de ejemplo"><i class="fas fa-trash-alt"></i></button></td></tr>'
        for name, qty, price, subtotal in [('Arroz blanco 500 g', 4, '4.800', '19.200'),
                                         ('Leche entera 1 L', 6, '6.200', '37.200'),
                                         ('Azúcar blanca 1 kg', 4, '4.250', '17.000')])
    html = re.sub(r'(<table id="detalle-productos"[\s\S]*?<tbody>)[\s\S]*?(</tbody>)',
                  lambda match: match[1] + rows + match[2], html, count=1)
    html = html.replace('id="modal-total" class="total-amount">$ 0,00', 'id="modal-total" class="total-amount">$ 73.400')
    html = html.replace('Falta por pagar: $ 0,00', 'Falta por pagar: $ 73.400')
    demo = """<script>
    document.getElementById('generar-venta').addEventListener('click', function () {
      document.getElementById('myModal').style.display = 'block';
      document.getElementById('myModal').classList.add('is-open');
      document.body.classList.add('modal-open');
    });
    document.querySelector('#myModal .close').addEventListener('click', function () {
      document.getElementById('myModal').style.display = 'none';
      document.getElementById('myModal').classList.remove('is-open');
      document.body.classList.remove('modal-open');
    });
    document.getElementById('confirmar-pago').addEventListener('click', function () {
      alert('Vista de prueba: no se registran ventas ni pagos.');
    });
    document.querySelectorAll('#myModal .pm-check').forEach(function (input) {
      input.addEventListener('change', function () {
        var row = input.closest('.mix-row');
        row.classList.toggle('show-amt', input.checked);
        row.querySelector('.pm-amt').disabled = !input.checked;
      });
    });
    </script>"""
    return html.replace('</body>', demo + '</body>')
