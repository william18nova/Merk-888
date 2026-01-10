from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Usuario, Sucursal, Categoria, Producto, Inventario, Proveedor, PreciosProveedor, PuntosPago, Rol, Empleado, HorariosNegocio, HorarioCaja, Cliente, Venta, DetalleVenta, PedidoProveedor, DetallePedidoProveedor, CambioDevolucion, Permiso, RolPermiso, PagoVenta, TurnoCaja, TurnoCajaMedio
from django.db.models import Count, Sum, Exists, OuterRef, Q, F, ExpressionWrapper, DecimalField, Value, IntegerField, Case, When
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpRequest
from django.contrib.auth import authenticate, login as auth_login
import json
from datetime import date, datetime, time
from django.utils import timezone
from django.contrib.auth import authenticate
import logging
from django.utils.dateparse import parse_date
from django.db import transaction, connection, IntegrityError
from django.contrib import messages
from zoneinfo import ZoneInfo
from django.core.exceptions import FieldDoesNotExist
from django.views.generic import DetailView
from .forms import (
    CategoriaForm,
    ClienteForm,
    EmpleadoCreateForm,
    HorarioCajaForm,
    HorariosNegocioForm,
    SucursalForm,
    ProductoForm,
    ProductoEditarForm,
    ProveedorForm,
    RolForm,
    InventarioForm,
    InventarioFiltroForm,
    PreciosProveedorForm,
    PuntosPagoForm,
    UsuarioForm,
    EditarCategoriaForm,
    EditarClienteForm,
    EditarEmpleadoForm,
    EditarHorarioCajaForm,
    EditarHorariosSucursalForm,
    EditarInventarioForm,
    EditarPreciosProveedorForm,
    EditarProveedorForm,
    PuntosPagoEditarForm,
    RolEditarForm,
    SucursalEditarForm,
    UsuarioEditarForm,
    GenerarVentaForm,
    PedidoProveedorForm,
    EditarPedidoForm,
    PermisoForm,
    PermisoEditarForm,
    RolPermisoAssignForm,
    RolPermisoEditForm,
    DevolucionFormSet,
    PagoMixtoFormSet,
    ReintegroMixtoFormSet,
    MEDIOS_PAGO,

)
from dal import autocomplete
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.urls import reverse, reverse_lazy
from itertools import zip_longest
from django.forms import formset_factory
from django.views          import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, CreateView
from django.views.generic.edit import FormView, UpdateView
from django.views.generic import ListView
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.db.models import Subquery
from django.core.paginator import Paginator
from django.db.models.functions import Lower, StrIndex, Trim, Coalesce
import os, io, textwrap, subprocess
from django.views.decorators.http import require_POST
from django.conf import settings
from datetime import timedelta
import pytz
from typing import List, Dict, Any


CO_TZ = ZoneInfo("America/Bogota")

def _as_co(value):
    """
    Acepta datetime o date.
    - Si es date, lo convierte a datetime (00:00:00) antes de localtime.
    - Si es datetime naive, lo hace aware en CO_TZ.
    """
    if value is None:
        return None

    # ✅ Si viene un date, convertirlo a datetime
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time(0, 0, 0))

    # ✅ Si viene naive -> hacerlo aware en CO_TZ
    if timezone.is_naive(value):
        value = timezone.make_aware(value, CO_TZ)

    # ✅ Pasar a hora CO
    return timezone.localtime(value, CO_TZ)

def _iso_co(value):
    """
    Devuelve string ISO 'YYYY-MM-DD' en hora CO.
    Acepta datetime o date.
    """
    dt_co = _as_co(value)
    return dt_co.date().isoformat()

def _now_co():
    """
    datetime aware "ahora" pero convertido a Colombia (para asignar si lo necesitas).
    """
    return timezone.now().astimezone(CO_TZ)


logger = logging.getLogger(__name__)

class DenyRolesMixin:
    """
    Bloquea acceso si el usuario tiene alguno de los roles en deny_roles.
    Asume que el user tiene FK user.rolid y rol tiene campo .nombre
    """
    deny_roles = []                 # ej: ["CajeroY"]
    redirect_url = "home"           # o la que quieras
    forbid_instead_of_redirect = False

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        rol_nombre = ""
        try:
            rol_nombre = (getattr(getattr(user, "rolid", None), "nombre", "") or "").strip()
        except Exception:
            rol_nombre = ""

        if rol_nombre in (self.deny_roles or []):
            if self.forbid_instead_of_redirect:
                return HttpResponseForbidden("No tienes permiso para acceder a esta página.")
            messages.error(request, "⛔ No tienes permiso para acceder a esa página.")
            return redirect(self.redirect_url)

        return super().dispatch(request, *args, **kwargs)

# ---------- mixin reutilizable para autocompletados ----------
class PaginatedAutocompleteMixin(LoginRequiredMixin, View):
    """
    Mixin genérico para autocompletados paginados.
    Las sub-clases solo declaran `model`, `text_field`, `id_field` y
    opcionalmente `extra_filter` o `per_page`.
    """
    model        = None         #  ← se define en la sub-clase
    text_field   = "nombre"
    id_field     = "pk"
    extra_filter = None         #  callable(qs, request)  →  qs
    per_page     = 10

    def get(self, request, *args, **kwargs):
        term   = request.GET.get("term", "").strip()
        page   = max(int(request.GET.get("page", 1)), 1)
        start, end = (page - 1) * self.per_page, page * self.per_page

        qs = self.model.objects.all().order_by(self.text_field)
        if self.extra_filter:
            qs = self.extra_filter(qs, request)

        if term:
            qs = qs.filter(**{f"{self.text_field}__icontains": term})

        total = qs.count()
        qs    = qs[start:end]

        results = [
            {"id": getattr(obj, self.id_field), "text": getattr(obj, self.text_field)}
            for obj in qs
        ]
        return JsonResponse({"results": results, "has_more": end < total})

class LoginView(View):
    template_name = "login.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        u = request.POST.get("nombreusuario")
        p = request.POST.get("contraseña")
        user = authenticate(request, username=u, password=p)
        if user:
            auth_login(request, user)
            return redirect("home")
        messages.error(request, "Usuario o contraseña incorrectos.")
        return render(request, self.template_name)


class HomePageView(LoginRequiredMixin, TemplateView):
    template_name = "homePage.html"


class SucursalCreateAJAXView(LoginRequiredMixin, FormView):
    template_name = 'agregar_sucursal.html'
    form_class = SucursalForm

    def form_valid(self, form):
        sucursal = form.save()
        # Cuando es AJAX devolvemos JSON
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Sucursal agregada exitosamente.',
                'sucursal': {
                    'id': sucursal.sucursalid,
                    'nombre': sucursal.nombre
                }
            })
        # En caso normal, redirigir con mensaje
        return redirect('listar_sucursales')  # O la vista deseada

    def form_invalid(self, form):
        errors = form.errors.get_json_data()
        return JsonResponse({'success': False, 'errors': errors}, status=400)


class SucursalListView(LoginRequiredMixin, ListView):
    template_name = "visualizar_sucursales.html"
    model = Sucursal               # => queryset = Sucursal.objects.all()
    context_object_name = "sucursales"

@login_required
def eliminar_sucursal(request, sucursal_id):
    sucursal = get_object_or_404(Sucursal, sucursalid=sucursal_id)
    if request.method == 'POST':
        sucursal.delete()
        messages.success(request, 'La sucursal ha sido eliminada exitosamente.')
        return redirect('visualizar_sucursales')
    return render(request, 'visualizar_sucursales.html', {'sucursales': Sucursal.objects.all()})


class SucursalUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    ▸  Edita una sucursal mediante AJAX.
    ▸  Si la petición NO es AJAX, actúa como un UpdateView normal.
    """
    model         = Sucursal
    pk_url_kwarg  = "sucursal_id"       # <int:sucursal_id> en la URL
    form_class    = SucursalEditarForm
    template_name = "editar_sucursal.html"
    success_url   = reverse_lazy("visualizar_sucursales")

    # ------------------------------------------------------------------ AJAX
    def form_valid(self, form):
        self.object = form.save()                 # guarda y conserva instancia
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": f'Sucursal “{self.object.nombre}” actualizada.',
                "redirect_url": self.success_url,
            })
        # Petición normal (no-AJAX) → mensaje + redirect
        messages.success(
            self.request,
            f'Sucursal “{self.object.nombre}” actualizada exitosamente.',
        )
        return redirect(self.success_url)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        # fallback: renderiza template con errores
        return self.render_to_response(self.get_context_data(form=form))

@login_required
def puntopago_autocomplete_venta(request):
    """
    Autocomplete para Punto de Pago.
    Permite buscar puntos de pago por nombre, filtrando por la sucursal seleccionada.
    Soporta paginación con 'term', 'page' y 'per_page'.
    """
    term = request.GET.get('term', '').strip()
    page_str = request.GET.get('page', '1').strip()
    per_page_str = request.GET.get('per_page', '50').strip()

    try:
        page = int(page_str)
    except ValueError:
        page = 1
    try:
        per_page = int(per_page_str)
    except ValueError:
        per_page = 50

    start = (page - 1) * per_page
    end = start + per_page

    # Si se pasa el id de sucursal, filtramos por ella (opcional)
    sucursal_id = request.GET.get('sucursal_id')
    qs = PuntosPago.objects.all().order_by('nombre')
    if sucursal_id and sucursal_id.isdigit():
        qs = qs.filter(sucursalid__sucursalid=sucursal_id)
    if term:
        qs = qs.filter(nombre__icontains=term)

    total_results = qs.count()
    qs = qs[start:end]
    results = [{'id': pp.puntopagoid, 'text': pp.nombre} for pp in qs]
    has_more = end < total_results
    return JsonResponse({'results': results, 'has_more': has_more})


class CategoriaCreateAJAXView(LoginRequiredMixin, FormView):
    template_name = "agregar_categoria.html"
    form_class    = CategoriaForm
    success_url   = reverse_lazy("visualizar_categorias")   # o la lista que uses

    # ------------- POST -------------
    def form_valid(self, form):
        categoria = form.save()
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success"  : True,
                "message"  : "Categoría agregada exitosamente.",
                "redirect_url": str(self.success_url),
                "categoria": {
                    "id"      : categoria.categoriaid,
                    "nombre"  : categoria.nombre
                }
            })
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400
            )
        return super().form_invalid(form)


class CategoriaListView(LoginRequiredMixin, ListView):
    """
    Lista todas las categorías ordenadas alfabéticamente para usarse
    con DataTables.  No paginamos en el servidor porque la paginación
    se delega al plugin JS.
    """
    model               = Categoria
    template_name       = "visualizar_categorias.html"
    context_object_name = "categorias"
    ordering            = ["nombre"]

@login_required
def eliminar_categoria(request, categoria_id):
    categoria = get_object_or_404(Categoria, categoriaid=categoria_id)
    nombre_categoria = categoria.nombre
    if request.method == 'POST':
        productos_asociados = Producto.objects.filter(categoria=categoria)
        productos_asociados.update(categoria=None)

        categoria.delete()
        messages.success(request, f'La categoría "{nombre_categoria}" ha sido eliminada exitosamente.')
        return redirect('visualizar_categorias')
    return render(request, 'visualizar_categorias.html', {'categorias': Categoria.objects.all()})

class CategoriaUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    Edita una categoría mediante AJAX.
    • En caso de éxito devuelve JSON  {success:true, redirect_url:"…"}
    • Si hay errores devuelve        {success:false, errors:{…}}
    """
    model         = Categoria
    form_class    = EditarCategoriaForm
    template_name = "editar_categoria.html"
    pk_url_kwarg  = "categoria_id"        # <int:categoria_id> en la URL
    success_url   = reverse_lazy("visualizar_categorias")

    # ── sobreevaluamos post() para responder siempre JSON a peticiones AJAX ──
    def form_valid(self, form):
        """
        Guardamos, y enviamos ‘flash’ a sessionStorage mediante JS
        => sólo enviamos la URL destino
        """
        self.object = form.save()
        success_msg = f'Categoría «{self.object.nombre}» actualizada correctamente.'

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success"     : True,
                "redirect_url": str(self.success_url),
                "flash_msg"   : success_msg,          # opcional (por si lo quieres)
            })

        # Fallback (no-AJAX)
        from django.contrib import messages
        messages.success(self.request, success_msg)
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)



class ProductoCreateAJAXView(LoginRequiredMixin, FormView):
    template_name = "agregar_producto.html"
    form_class    = ProductoForm

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["categorias"] = Categoria.objects.all()   #  para precargar si quieres un select
        return ctx

    #  POST -------------------------------------------------
    def form_valid(self, form):
        producto = form.save()
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": "Producto agregado exitosamente.",
                "producto": {
                    "id": producto.productoid,
                    "nombre": producto.nombre,
                }
            })
        #  fallback (no-AJAX)
        return super().form_valid(form)

    def form_invalid(self, form):
        return JsonResponse(
            {"success": False, "errors": form.errors.get_json_data()},
            status=400
        )


# ----------  Autocomplete «Categoría»  ----------
class CategoriaAutocompleteView(PaginatedAutocompleteMixin):
    """Devuelve categorías paginadas para el componente de autocompletado."""
    model      = Categoria            # ← modelo a consultar
    text_field = "nombre"             # ← columna que se muestra
    id_field   = "categoriaid"        # ← valor que se envía al form
    per_page   = 10                   # ← (opcional) página de 10 resultados


class ProductoListView(LoginRequiredMixin, TemplateView):
    template_name = "visualizar_productos.html"
    ordering = "nombre"

    def get_queryset(self):
        return (
            Producto.objects
            .select_related("categoria")
            .order_by(self.ordering or "nombre")
        )


class ProductoDataTableView(LoginRequiredMixin, View):

    def get(self, request, *args, **kwargs):
      draw   = int(request.GET.get("draw", "1"))
      start  = int(request.GET.get("start", "0"))
      length = int(request.GET.get("length", "25"))
      search_value = request.GET.get("search[value]", "").strip()

      base_qs = Producto.objects.all()
      records_total = base_qs.count()

      qs = base_qs

      if search_value:
          if search_value.isdigit() and len(search_value) >= 8:
              qs = qs.filter(codigo_de_barras__iexact=search_value)
          else:
              tokens = search_value.split()
              for token in tokens:
                  qs = qs.filter(
                      Q(nombre__icontains=token) |
                      Q(codigo_de_barras__icontains=token) |
                      Q(categoria__nombre__icontains=token)
                  )

      records_filtered = qs.count()

      order_column_index = request.GET.get("order[0][column]", "1")
      order_dir          = request.GET.get("order[0][dir]", "asc")

      columns = [
          "productoid",           # 0
          "nombre",               # 1
          "descripcion",          # 2
          "precio",               # 3
          "precio_anterior",      # 4 ✅
          "categoria__nombre",    # 5
          "codigo_de_barras",     # 6
          "iva",                  # 7
          "impuesto_consumo",     # 8
          "icui",                 # 9
          "ibua",                 # 10
          "rentabilidad",         # 11
          # 12 = acciones
      ]

      try:
          idx = int(order_column_index)
          order_column = columns[idx]
      except (ValueError, IndexError):
          order_column = "nombre"

      if order_dir == "desc":
          order_column = "-" + order_column

      qs_page = (
          qs.select_related("categoria")
            .order_by(order_column)
            .values(
                "productoid",
                "nombre",
                "descripcion",
                "precio",
                "precio_anterior",     # ✅
                "categoria__nombre",
                "codigo_de_barras",
                "iva",
                "impuesto_consumo",
                "icui",
                "ibua",
                "rentabilidad",
            )[start:start + length]
      )

      data = []
      for p in qs_page:
          precio_anterior = p["precio_anterior"]
          data.append({
              "productoid": p["productoid"],
              "nombre": p["nombre"],
              "descripcion": p["descripcion"] or "—",
              "precio": f"${p['precio']:.2f}",
              "precio_anterior": f"${precio_anterior:.2f}" if precio_anterior is not None else "—",
              "categoria": p["categoria__nombre"] or "—",
              "codigo_de_barras": p["codigo_de_barras"] or "—",
              "iva": f"{p['iva']:.2f}",
              "impuesto_consumo": f"${p['impuesto_consumo']:.2f}",
              "icui": f"${p['icui']:.2f}",
              "ibua": f"${p['ibua']:.2f}",
              "rentabilidad": f"{p['rentabilidad']:.2f}%",
              "acciones": f"""
                <div class="btn-container">
                  <a href="{reverse('editar_producto', args=[p['productoid']])}"
                     class="btn editar" title="Editar {p['nombre']}">
                    <i class="fas fa-edit"></i>
                  </a>
                  <button type="button" class="btn borrar"
                          data-url="{reverse('eliminar_producto', args=[p['productoid']])}"
                          data-nombre="{p['nombre']}"
                          title="Eliminar {p['nombre']}">
                    <i class="fas fa-trash-alt"></i>
                  </button>
                </div>
              """,
          })

      return JsonResponse({
          "draw": draw,
          "recordsTotal": records_total,
          "recordsFiltered": records_filtered,
          "data": data,
      })


@login_required
def eliminar_producto(request, producto_id):
    producto = get_object_or_404(Producto, productoid=producto_id)
    if request.method == 'POST':
        nombre_producto = producto.nombre
        producto.delete()
        messages.success(request, f'El producto "{nombre_producto}" ha sido eliminado exitosamente.')
        return redirect('visualizar_productos')
    productos = Producto.objects.all()
    return render(request, 'visualizar_productos.html', {'productos': productos})


class ProductoUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    · Renderiza el formulario de edición con template + JS.
    · Si la petición es AJAX (fetch), responde JSON.
    · Para navegación clásica usa messages y redirect normal.
    """
    model         = Producto
    form_class    = ProductoEditarForm
    template_name = "editar_producto.html"
    pk_url_kwarg  = "producto_id"

    def form_valid(self, form):
        # precio actual en BD (antes del cambio)
        old_obj = self.get_object()
        old_precio = old_obj.precio

        # No guardamos aún para poder inyectar precio_anterior si aplica
        producto = form.save(commit=False)

        # ✅ regla: solo actualiza precio_anterior si cambió el precio
        if producto.precio != old_precio:
            producto.precio_anterior = old_precio

        producto.save()

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "redirect_url": reverse("visualizar_productos"),
                "nombre": producto.nombre,
            })

        messages.success(self.request, f'Producto «{producto.nombre}» actualizado correctamente.')
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data(escape_html=True)},
                status=400,
            )
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy("visualizar_productos")


# ---------- alta de inventario ----------
@method_decorator(transaction.atomic, name="dispatch")
class InventarioCreateAJAXView(LoginRequiredMixin, View):
    """
    · GET  →  renderiza formulario + dataset inicial.
    · POST →  guarda lotes a partir de 'inventarios_temp' (JSON).
              Respuesta JSON {success, errors}
    """
    template_name = "agregar_inventario.html"
    form_class    = InventarioForm
    success_msg   = "Inventario creado exitosamente."

    # ----------  GET ----------
    def get(self, request):
        form  = self.form_class()
        sucs  = (Sucursal.objects
                 .annotate(inv_count=Count("inventario"))
                 .filter(inv_count=0))
        prods = Producto.objects.all()

        if not sucs.exists():
            messages.error(request,
                "Todas las sucursales ya tienen inventario activo.")
        if not prods.exists():
            messages.error(request,
                "No hay productos en el sistema. Agrega productos primero.")

        ctx = {"form": form, "sucursales": sucs, "productos": prods}
        return render(request, self.template_name, ctx)

    # ----------  POST ----------
    def post(self, request):
        form = self.form_class(request.POST)

        # 1) Validación de formulario base
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        sucursal = form.cleaned_data["sucursal"]
        raw_list = request.POST.get("inventarios_temp", "[]")

        try:
            items = json.loads(raw_list)
        except json.JSONDecodeError:
            items = []

        if not items:
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "inventarios_temp": [{"message": "Debe agregar al menos un producto."}]
                })
            })

        # 2) Construir lotes
        batch = []
        for it in items:
            pid = it.get("productId")
            qty = it.get("cantidad")
            if not (pid and qty):
                continue

            producto = get_object_or_404(Producto, pk=pid)

            # evitar duplicados
            if Inventario.objects.filter(productoid=producto,
                                         sucursalid=sucursal).exists():
                continue

            try:
                qty_int = int(qty)
                if qty_int <= 0:
                    raise ValueError
            except ValueError:
                continue

            batch.append(Inventario(
                productoid = producto,
                sucursalid = sucursal,
                cantidad   = qty_int
            ))

        if not batch:
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "__all__": [{"message": "Nada que guardar."}]
                })
            })

        # 3) Guardar
        Inventario.objects.bulk_create(batch)

        # ► ¡Ya NO se añade messages.success aquí! ◄
        # messages.success(request, self.success_msg)

        return JsonResponse({"success": True})


# ---------- autocompletado ①: sucursales sin inventario ----------
@method_decorator(login_required, name="dispatch")
class SucursalSinInventarioAutocomplete(PaginatedAutocompleteMixin):
    model = Sucursal

    def get_queryset(self, request):
        term = (request.GET.get("term") or "").strip()

        qs = Sucursal.objects.all()

        # filtro por término (opcional, para que al escribir busque)
        if term:
            qs = qs.filter(
                Q(nombre__icontains=term) |
                Q(direccion__icontains=term)
            )

        # SOLO sucursales SIN inventario
        qs = qs.annotate(
            tiene_inv=Exists(
                Inventario.objects.filter(sucursalid=OuterRef("pk"))
            )
        ).filter(tiene_inv=False)

        return qs.order_by("nombre")


def sucursal_sin_inventario_autocomplete(request):
    term = (request.GET.get("term") or "").strip()
    page = int(request.GET.get("page") or 1)
    page_size = 10

    # ✅ NO depende del related_name. "Sucursal" PK real: sucursalid
    inv_qs = Inventario.objects.filter(sucursalid=OuterRef("pk"))

    qs = (
        Sucursal.objects
        .annotate(_has_inv=Exists(inv_qs))
        .filter(_has_inv=False)
    )

    # ✅ permitir term vacío
    if term:
        qs = qs.filter(
            Q(nombre__icontains=term) |
            Q(direccion__icontains=term)
        )

    # ✅ orden correcto (usa pk o sucursalid)
    qs = qs.order_by("nombre", "sucursalid")

    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size

    results = []
    for s in qs[start:end]:
        results.append({
            "id": s.pk,          # Django pk = sucursalid en tu caso
            "text": s.nombre
        })

    return JsonResponse({
        "results": results,
        "has_more": end < total
    })

# ---------- autocompletado ②: productos (excluye IDs recibidos) ----------
class ProductoAutocomplete(PaginatedAutocompleteMixin):
    model = Producto
    id_field = "productoid"

    def extra_filter(self, qs, request):
        excluded = request.GET.get("excluded", "")
        ids      = [int(x) for x in excluded.split(",") if x.isdigit()]
        return qs.exclude(productoid__in=ids) if ids else qs

class InventarioListView(LoginRequiredMixin, View):
    """Renderiza y filtra inventarios por sucursal (o modo global)."""

    template_name = "visualizar_inventarios.html"

    def get_context(self, request):
        """Devuelve contexto según el filtro POST (si lo hay)."""
        form         = InventarioFiltroForm(request.POST or None)
        sucursales   = Sucursal.objects.filter(inventario__isnull=False).distinct()
        sucursal_sel = None
        inventarios  = []
        global_mode  = False
        global_data  = []

        if form.is_valid():
            filtro = form.cleaned_data.get("sucursal")
            if filtro == "global":
                global_mode = True
                global_data = (Inventario.objects
                               .values("productoid__nombre")
                               .annotate(total_cantidad=Sum("cantidad"))
                               .order_by("productoid__nombre"))
            elif filtro:
                sucursal_sel = get_object_or_404(Sucursal, pk=int(filtro))
                inventarios  = (Inventario.objects
                                .filter(sucursalid=sucursal_sel)
                                .select_related("productoid"))
        return {
            "form"                : form,
            "sucursales"          : sucursales,
            "inventarios"         : inventarios,
            "inventario_global"   : global_mode,
            "inventario_global_data": global_data,
            "sucursal_seleccionada": sucursal_sel,
        }

    # GET y POST necesitan la misma lógica
    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        return render(request, self.template_name, self.get_context(request))

class SucursalInventarioAutocompleteView(PaginatedAutocompleteMixin):
    """
    Autocompletado de sucursales que YA tienen inventario.
    En la primera página agrega la opción «Inventario Global».
    """
    model       = Sucursal
    text_field  = "nombre"
    id_field    = "sucursalid"

    # ── se filtra solo a sucursales con inventario ───────────────────────────
    def extra_filter(self, qs, request):
        #  opción A (sin Count) – más simple
        return qs.filter(inventario__isnull=False).distinct()

        #  opción B (con Count) – si prefieres una sola consulta
        # return qs.annotate(n=Count("inventario")).filter(n__gt=0)

    # ── se añade la opción «global» en la página 1 ────────────────────────────
    def get(self, request, *args, **kwargs):
        # llamamos al mixin ⇒ JsonResponse
        base_response = super().get(request, *args, **kwargs)
        data          = json.loads(base_response.content)   # → dict

        if int(request.GET.get("page", "1")) == 1:
            data["results"].insert(0, {"id": "global", "text": "Inventario Global"})

        return JsonResponse(data)


# ─────────────────────────────────────────────────────────────────────────────
# Editar Inventario  (CBV)
# ─────────────────────────────────────────────────────────────────────────────


class EditarInventarioView(LoginRequiredMixin, View):
    """
    GET  → muestra formulario precargado.
    POST →
      - action=add_item (AJAX):
          * si viene add_cantidad => suma atómica (F) a cantidad actual
            - si cantidad actual > 9000: BLOQUEA y pide contar antes
          * si no viene add_cantidad => set exacto (puede ser negativo o 0)
      - submit principal: merge (upsert SOLO lo enviado) SIN borrar faltantes.
    ✅ Permite cantidades negativas o 0.
    """
    template_name = "editar_inventario.html"

    # ---------- GET ----------
    def get(self, request, sucursal_id):
        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
        inventarios_existentes = (
            Inventario.objects
            .filter(sucursalid=sucursal)
            .select_related("productoid")
            .order_by("productoid__nombre")
        )

        form = EditarInventarioForm(initial={
            "sucursal": sucursal.pk,
            "sucursal_autocomplete": sucursal.nombre,
        })

        return render(request, self.template_name, {
            "form": form,
            "sucursal": sucursal,
            "inventarios": inventarios_existentes,
        })

    # ---------- POST ----------
    @transaction.atomic
    def post(self, request, sucursal_id):
        # ───── AJAX: agregar/actualizar un item ─────
        if request.POST.get("action") == "add_item":
            sucursal = get_object_or_404(Sucursal, pk=sucursal_id)

            productoid   = (request.POST.get("productoid") or "").strip()
            cantidad     = (request.POST.get("cantidad") or "").strip()       # Cantidad exacta
            add_cantidad = (request.POST.get("add_cantidad") or "").strip()   # Añadir cantidad (suma)

            errors = {}

            if not productoid or not productoid.isdigit():
                errors.setdefault("productoid", []).append({"message": "Debe seleccionar un producto válido."})

            def parse_int_nullable(val):
                val = (val or "").strip()
                if val == "":
                    return None
                try:
                    return int(val)  # permite negativos y 0
                except ValueError:
                    return "ERR"

            cantidad_int = parse_int_nullable(cantidad)
            add_int      = parse_int_nullable(add_cantidad)

            # Debe venir exacta o add
            if cantidad_int is None and add_int is None:
                errors.setdefault("cantidad", []).append({"message": "Ingrese Cantidad exacta o Añadir cantidad."})

            if cantidad_int == "ERR":
                errors.setdefault("cantidad", []).append({"message": "Cantidad exacta debe ser un entero (puede ser negativo o 0)."})
            if add_int == "ERR":
                errors.setdefault("add_cantidad", []).append({"message": "Añadir cantidad debe ser un entero (puede ser negativo o 0)."})

            if errors:
                return JsonResponse({"success": False, "errors": json.dumps(errors)}, status=400)

            pid = int(productoid)

            # Lock row
            inv, _created = Inventario.objects.select_for_update().get_or_create(
                sucursalid=sucursal,
                productoid_id=pid,
                defaults={"cantidad": 0},
            )

            # Para el mensaje del JS
            producto_nombre = ""
            try:
                producto_nombre = (inv.productoid.nombre or "").strip()
            except Exception:
                producto_nombre = ""

            # ✅ MODO: SUMA (añadir)
            if add_int is not None:
                # Si actual > 9000 => bloquear surtido
                if (inv.cantidad or 0) > 9000:
                    return JsonResponse({
                        "success": False,
                        "errors": json.dumps({
                            "add_cantidad": [{"message": "Este producto nunca se a contado cuentelo antes de surtir"}]
                        })
                    }, status=400)

                before = int(inv.cantidad or 0)

                Inventario.objects.filter(pk=inv.pk).update(cantidad=F("cantidad") + add_int)
                inv.refresh_from_db(fields=["cantidad"])

                # (messages opcional)
                messages.success(request, f"Producto actualizado en «{sucursal.nombre}».")

                return JsonResponse({
                    "success": True,
                    "mode": "add",
                    "product_name": producto_nombre,
                    "delta": int(add_int),         # lo que se agregó (puede ser negativo)
                    "before": before,              # cantidad anterior
                    "new_cantidad": int(inv.cantidad),
                })

            # ✅ MODO: SET EXACTO
            inv.cantidad = int(cantidad_int)  # aquí no es None
            inv.save(update_fields=["cantidad"])

            messages.success(request, f"Producto actualizado en «{sucursal.nombre}».")
            return JsonResponse({
                "success": True,
                "mode": "exact",
                "product_name": producto_nombre,
                "new_cantidad": int(inv.cantidad),
            })

        # ───── Submit principal: MERGE (no eliminar faltantes) ─────
        form = EditarInventarioForm(request.POST)
        if not form.is_valid():
            errors = {
                fld: [{"message": e["message"]} for e in ferr]
                for fld, ferr in form.errors.get_json_data().items()
            }
            return JsonResponse({"success": False, "errors": json.dumps(errors)}, status=400)

        sucursal_destino = form.cleaned_data["sucursal"]
        raw_json         = form.cleaned_data["inventarios_temp"]

        try:
            payload = json.loads(raw_json or "[]")
        except json.JSONDecodeError:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "inventarios_temp": [{"message": "Formato JSON inválido."}]
                })
            }, status=400)

        # ✅ Normaliza payload permitiendo 0 y negativos
        upserts = {}
        for item in payload:
            pid = item.get("productId")
            cant = item.get("cantidad")
            if pid is None or cant is None:
                continue
            try:
                pid_int = int(pid)
                cant_int = int(cant)
            except (ValueError, TypeError):
                continue
            upserts[str(pid_int)] = cant_int

        existentes = {
            str(obj.productoid_id): obj
            for obj in (Inventario.objects
                        .select_for_update()
                        .filter(sucursalid=sucursal_destino))
        }

        for pid_str, cant_int in upserts.items():
            if pid_str in existentes:
                inv = existentes[pid_str]
                inv.cantidad = cant_int
                inv.save(update_fields=["cantidad"])
            else:
                Inventario.objects.create(
                    productoid_id=int(pid_str),
                    sucursalid=sucursal_destino,
                    cantidad=cant_int
                )

        messages.success(
            request,
            f'Inventario de «{sucursal_destino.nombre}» actualizado (merge: sin eliminar productos no listados).'
        )
        return JsonResponse({
            "success": True,
            "redirect_url": reverse("visualizar_inventarios"),
        })

class InventarioItemAjaxView(LoginRequiredMixin, View):
    """
    GET /inventario/<sucursal_id>/item/?productoid=<id>
    Devuelve el inventario de SOLO ese producto en esa sucursal.
    ✅ Si no existe, cantidad = 0 (pero si existe puede ser negativa).
    """
    def get(self, request, sucursal_id):
        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
        pid = (request.GET.get("productoid") or "").strip()

        if not pid.isdigit():
            return JsonResponse({"success": False, "error": "productoid inválido."}, status=400)

        pid_int = int(pid)
        producto = get_object_or_404(
            Producto._base_manager.only("productoid", "nombre", "codigo_de_barras"),
            pk=pid_int
        )

        inv = (Inventario.objects
               .filter(sucursalid=sucursal, productoid_id=pid_int)
               .select_related("productoid")
               .only("inventarioid", "cantidad", "productoid__nombre")
               .first())

        return JsonResponse({
            "success": True,
            "exists": bool(inv),
            "inventario_id": inv.pk if inv else None,
            "product": {
                "id": producto.pk,
                "nombre": producto.nombre,
                "codigo_de_barras": getattr(producto, "codigo_de_barras", "") or "",
            },
            # ✅ devuelve tal cual, aunque sea 0 o negativa
            "cantidad": inv.cantidad if inv else 0,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Autocomplete de SUCURSALES (modo editar)
# ─────────────────────────────────────────────────────────────────────────────
class SucursalInventarioAutocompleteEditarView(PaginatedAutocompleteMixin):
    model      = Sucursal
    text_field = "nombre"
    id_field   = "sucursalid"
    per_page   = 50

    def extra_filter(self, qs, request):
        current_id = request.GET.get("current_sucursal_id")
        if current_id and current_id.isdigit():
            qs = qs.filter(Q(pk=current_id) | Q(inventario__isnull=True))
        else:
            qs = qs.filter(inventario__isnull=True)
        return qs.distinct()

    # Añadimos el método «global» solo si lo necesitas; aquí NO se agrega.


# ─────────────────────────────────────────────────────────────────────────────
# Autocomplete de PRODUCTOS (excluye IDs ya listados)
# ─────────────────────────────────────────────────────────────────────────────


class _ProductoBaseAutocomplete(LoginRequiredMixin, View):
    page_size = 30

    def base_qs(self):
        return Producto._base_manager.all().only("productoid", "nombre", "codigo_de_barras")

    def paginate(self, qs, page):
        paginator = Paginator(qs, self.page_size)
        page_obj = paginator.get_page(page)
        results = [{
            "id": p.productoid,
            "text": p.nombre,
            "barcode": getattr(p, "codigo_de_barras", "") or "",
        } for p in page_obj.object_list]
        return JsonResponse({
            "results": results,
            "pagination": {"more": page_obj.has_next()}
        })


class ProductoInventarioBuscarNombreView(_ProductoBaseAutocomplete):
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = self.base_qs()
        if term:
            qs = qs.filter(nombre__icontains=term)
        qs = qs.order_by("nombre")
        return self.paginate(qs, page)


class ProductoInventarioBuscarBarrasView(_ProductoBaseAutocomplete):
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = self.base_qs()
        if term:
            qs = qs.filter(Q(codigo_de_barras__startswith=term) | Q(codigo_de_barras__icontains=term))
        qs = qs.order_by("codigo_de_barras", "nombre")
        return self.paginate(qs, page)


class ProductoInventarioBuscarIdView(_ProductoBaseAutocomplete):
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        if not term.isdigit():
            return JsonResponse({"results": [], "pagination": {"more": False}})

        pid = int(term)
        qs = self.base_qs().filter(productoid=pid).order_by("nombre")
        return self.paginate(qs, page)



@login_required
def eliminar_producto_inventario_view(request, inventario_id):
    """Eliminar un registro de inventario vía AJAX."""
    if request.method == "POST":
        inventario = get_object_or_404(Inventario, pk=inventario_id)
        producto_nombre = inventario.productoid.nombre
        inventario.delete()
        return JsonResponse({
            "success": True,
            "message": f'Producto "{producto_nombre}" eliminado exitosamente.'
        })
    return JsonResponse({"success": False, "message": "Método no permitido."}, status=405)


class ProveedorCreateView(LoginRequiredMixin, View):
    """
    Crea un proveedor vía AJAX:
      • GET  → renderiza formulario
      • POST → devuelve JSON {success, message|errors}
    """
    template_name = "agregar_proveedor.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ProveedorForm()})

    @transaction.atomic
    def post(self, request):
        form = ProveedorForm(request.POST)
        if form.is_valid():
            form.save()
            return JsonResponse({
                "success": True,
                "message": "Proveedor agregado exitosamente."
            })
        # form.errors es un ErrorDict → .get_json_data() lista mensajes y códigos
        return JsonResponse({
            "success": False,
            "errors": form.errors.get_json_data()
        }, status=400)


class ProveedorListView(LoginRequiredMixin, ListView):
    """Lista de proveedores (lectura únicamente)."""
    model               = Proveedor
    template_name       = "visualizar_proveedores.html"
    context_object_name = "proveedores"
    paginate_by         = 0          # paginación la maneja DataTables

@login_required
def eliminar_proveedor(request, proveedor_id):
    proveedor = get_object_or_404(Proveedor, proveedorid=proveedor_id)
    if request.method == 'POST':
        proveedor.delete()
        messages.success(request, 'El proveedor ha sido eliminado exitosamente.')
        return redirect('visualizar_proveedores')
    # Si la solicitud no es POST se vuelve a renderizar la página
    return render(request, 'visualizar_proveedores.html', {'proveedores': Proveedor.objects.all()})


class ProveedorUpdateView(LoginRequiredMixin, View):
    """
    Edición de proveedor con soporte AJAX.
    – GET  → renderiza el formulario.
    – POST → valida + responde JSON (success | errors)
    """

    def get(self, request, proveedor_id: int):
        proveedor = get_object_or_404(Proveedor, pk=proveedor_id)
        form      = EditarProveedorForm(instance=proveedor)
        return render(
            request,
            "editar_proveedor.html",
            {"form": form, "proveedor": proveedor},
        )

    def post(self, request, proveedor_id: int):
        proveedor = get_object_or_404(Proveedor, pk=proveedor_id)
        form      = EditarProveedorForm(request.POST, instance=proveedor)

        if form.is_valid():
            form.save()

            # el “flash” se mostrará al volver a la lista
            request.session["flash-prov"] = "Proveedor actualizado exitosamente."

            return JsonResponse(
                {
                    "success": True,
                    "redirect_url": reverse("visualizar_proveedores"),
                }
            )

        # serializamos los errores tal cual los genera Django
        return JsonResponse(
            {
                "success": False,
                "errors": form.errors.get_json_data(),
            }
        )



@method_decorator(transaction.atomic, name="dispatch")
class PreciosProveedorCreateAJAXView(LoginRequiredMixin, View):
    template_name = "agregar_productos_precios_proveedor.html"
    form_class    = PreciosProveedorForm

    # ---------- GET ----------
    def get(self, request):
        form = self.form_class()
        sin_precios = (
            Proveedor.objects
                     .annotate(p_count=Count("preciosproveedor"))
                     .filter(p_count=0)
        )
        if not sin_precios.exists():
            messages.error(request, "Todos los proveedores ya tienen precios cargados.")

        ctx = {"form": form, "proveedores": sin_precios}
        return render(request, self.template_name, ctx)

    # ---------- POST ----------
    def post(self, request):
        form = self.form_class(request.POST)

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        proveedor = form.cleaned_data["proveedor"]
        raw_json  = request.POST.get("precios_temp", "[]")

        try:
            items = json.loads(raw_json)
        except json.JSONDecodeError:
            items = []

        if not items:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "precios_temp": [{"message": "Debe agregar al menos un producto."}]
                })
            })

        batch = []
        for it in items:
            pid   = it.get("productId")
            price = it.get("price")

            # validaciones mínimas
            if not (pid and price):
                continue
            try:
                precio_dec = Decimal(price)
                if precio_dec <= 0:
                    raise ValidationError("Precio no válido")
            except Exception:
                continue

            producto = get_object_or_404(Producto, pk=pid)

            # evitar duplicados
            if PreciosProveedor.objects.filter(
                    productoid=producto, proveedorid=proveedor).exists():
                continue

            batch.append(PreciosProveedor(
                productoid=producto,
                proveedorid=proveedor,
                precio=precio_dec
            ))

        if not batch:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "__all__": [{"message": "Nada que guardar."}]
                })
            })

        PreciosProveedor.objects.bulk_create(batch)
        return JsonResponse({"success": True})


# ──────────────────────────────────────────────────────────────
# 2. Autocompletados (mismo patrón que inventario)
# ──────────────────────────────────────────────────────────────
class ProveedorSinPreciosAutocomplete(PaginatedAutocompleteMixin):
    """
    • Devuelve todos los proveedores SIN precios
    • + el proveedor indicado en ?current=<id> (aunque tenga precios)
    """
    model = Proveedor    # el mixin usa id_field y text_field por defecto

    def extra_filter(self, qs, request):
        sin_precios = qs.annotate(cnt=Count("preciosproveedor")).filter(cnt=0)

        cur = request.GET.get("current", "").strip()
        if cur.isdigit():
            sin_precios = sin_precios | qs.filter(pk=cur)

        return sin_precios.distinct()


class ProductoExcludingAutocomplete(PaginatedAutocompleteMixin):
    """Productos excluyendo IDs ya listados en el front (query param excluded)."""
    model     = Producto
    id_field  = "productoid"

    def extra_filter(self, qs, request):
        excluded = request.GET.get("excluded", "")
        ids      = [int(x) for x in excluded.split(",") if x.isdigit()]
        return qs.exclude(productoid__in=ids) if ids else qs


class PreciosProveedorListView(LoginRequiredMixin, View):
    template_name = "visualizar_productos_precios_proveedores.html"

    # ---------- GET ----------
    def get(self, request):
        ctx = self._base_context()
        return render(request, self.template_name, ctx)

    # ---------- POST (filtro) ----------
    def post(self, request):
        ctx = self._base_context()
        pid = request.POST.get("proveedor")
        if pid:
            ctx["proveedor_seleccionado"] = prov = get_object_or_404(Proveedor, pk=pid)
            ctx["productos_precios"] = (
                PreciosProveedor.objects.filter(proveedorid=prov)
            )
        return render(request, self.template_name, ctx)

    # ---------- contexto común ----------
    def _base_context(self):
        proveedores = (
            Proveedor.objects.annotate(num=Count("preciosproveedor"))
                             .filter(num__gt=0)
        )
        return {
            "proveedores": proveedores,
            "productos_precios": None,
            "proveedor_seleccionado": None,
        }


class ProveedorConProductosAutocomplete(PaginatedAutocompleteMixin):
    """
    • Devuelve únicamente los proveedores que YA tienen al menos un
      producto en la tabla `PreciosProveedor`.
    • Soporta paginación (`page`, `per_page`) y búsqueda (`term`)
      exactamente igual que los demás autocompletes de la app.
    """
    model       = Proveedor
    text_field  = "nombre"        # lo que verá el usuario
    id_field    = "proveedorid"   # value que se enviará al servidor
    per_page    = 10              # por coherencia con tus otros autocompletes

    # ——— filtro adicional ———
    def extra_filter(self, qs, request):
        """
        · El mixin ya aplica «term» y la paginación.
        · Aquí sólo restringimos a “con productos”.
        """
        return (
            qs.filter(preciosproveedor__isnull=False)   # al menos un registro
              .distinct()
              .order_by("nombre")
        )


@login_required
def eliminar_precio_proveedor_view(request, id):
    if request.method == 'POST':
        precio_proveedor = get_object_or_404(PreciosProveedor, pk=id)
        nombre_producto = precio_proveedor.productoid.nombre
        precio_proveedor.delete()
        return JsonResponse({'success': True, 'message': f'Producto "{nombre_producto}" eliminado correctamente.'})
    return JsonResponse({'success': False, 'message': 'Error al eliminar el producto.'})


@method_decorator(transaction.atomic, name="dispatch")
class PreciosProveedorUpdateAJAXView(LoginRequiredMixin, View):
    """
    · GET  →  muestra el formulario con los productos del proveedor.
    · POST →  guarda los cambios recibidos en `precios_temp` (JSON):
              crea / actualiza / elimina, y si el usuario cambió
              de proveedor, desvincula todos los productos del anterior.
    """
    template_name = "editar_productos_precios_proveedor.html"
    form_class    = EditarPreciosProveedorForm

    # ---------- GET ----------
    def get(self, request, proveedor_id):
        proveedor = get_object_or_404(Proveedor, pk=proveedor_id)

        # --- productos actuales -> JSON que inyectamos al JS ---
        existentes = (
            PreciosProveedor.objects
            .filter(proveedorid=proveedor)
            .select_related("productoid")
        )
        productos_json = json.dumps([
            {
                "productId"  : pp.productoid_id,
                "productName": pp.productoid.nombre,
                "price"      : str(pp.precio),
            } for pp in existentes
        ])

        form = self.form_class(initial={
            "proveedor_autocomplete": proveedor.nombre,
            "proveedor"             : proveedor.pk,
        })

        ctx = {
            "form"                     : form,
            "proveedor"                : proveedor,
            "productos_existentes_json": productos_json,
        }
        return render(request, self.template_name, ctx)

    # ---------- POST ----------
    def post(self, request, proveedor_id):
        old_prov = get_object_or_404(Proveedor, pk=proveedor_id)   # (1) URL
        form     = self.form_class(request.POST)

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        new_prov = form.cleaned_data["proveedor"]                  # (2) HIDDEN
        prov_has_changed = old_prov.pk != new_prov.pk              # (3) ¿cambió?

        # ───── si cambió, borramos TODO lo del proveedor anterior ─────
        if prov_has_changed:
            PreciosProveedor.objects.filter(proveedorid=old_prov).delete()

        prov = new_prov  # a partir de aquí siempre trabajamos con 'prov'

        # ---------- 1) JSON entrante ----------
        raw_json = request.POST.get("precios_temp", "[]")
        try:
            nuevos = json.loads(raw_json)
        except json.JSONDecodeError:
            nuevos = []

        # ---------- 2) normalizar ----------
        nuevos_map = {}
        for it in nuevos:
            pid, price = it.get("productId"), it.get("price")
            if not (pid and price):
                continue
            try:
                precio_dec = Decimal(price)
                if precio_dec <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                continue
            nuevos_map[str(pid)] = precio_dec

        # ---------- 3) registros existentes del proveedor ----------
        existentes_qs   = PreciosProveedor.objects.filter(proveedorid=prov)
        existentes_dict = {str(pp.productoid_id): pp for pp in existentes_qs}

        to_create, to_update = [], []

        # ---------- 4) create / update ----------
        for pid, new_price in nuevos_map.items():
            if pid in existentes_dict:
                obj = existentes_dict.pop(pid)      # ya existe → quizás actualizar
                if obj.precio != new_price:
                    obj.precio = new_price
                    to_update.append(obj)
            else:                                   # nuevo
                to_create.append(
                    PreciosProveedor(
                        proveedorid   = prov,
                        productoid_id = int(pid),
                        precio        = new_price
                    )
                )

        # ---------- 5) delete (los que quedaron fuera del JSON) ----------
        if existentes_dict:
            PreciosProveedor.objects.filter(pk__in=existentes_dict).delete()

        # ---------- 6) bulk ops ----------
        if to_create:
            PreciosProveedor.objects.bulk_create(to_create)
        if to_update:
            PreciosProveedor.objects.bulk_update(to_update, ["precio"])

        messages.success(
            request,
            f"Productos y precios actualizados exitosamente para «{prov.nombre}»."
        )
        return JsonResponse({
            "success"     : True,
            "redirect_url": reverse("visualizar_productos_precios_proveedores")
        })

@method_decorator(transaction.atomic, name="dispatch")
class PuntosPagoCreateAJAXView(LoginRequiredMixin, View):
    """
    · GET  →  formulario + DataSet inicial.
    · POST →  crea puntos de pago a partir de `puntos_temp` (JSON).
              Responde {success, errors}
    """
    template_name = "agregar_punto_pago.html"
    form_class    = PuntosPagoForm

    # ---------- GET ----------
    def get(self, request):
        form = self.form_class()

        sin_pp = (Sucursal.objects
                  .annotate(pp_count=Count("puntospago"))
                  .filter(pp_count=0))

        if not sin_pp.exists():
            messages.error(request, "Todas las sucursales ya tienen puntos de pago.")

        ctx = {"form": form, "sucursales": sin_pp}
        return render(request, self.template_name, ctx)

    # ---------- POST ----------
    def post(self, request):
        form = self.form_class(request.POST)

        # 1) Val. básica del formulario
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        sucursal = form.cleaned_data["sucursal"]
        raw_json = request.POST.get("puntos_temp", "[]")

        # 2) Parse JSON
        try:
            items = json.loads(raw_json)
        except json.JSONDecodeError:
            items = []

        if not items:
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "puntos_temp": [{"message": "Debe agregar al menos un punto de pago."}]
                })
            })

        # 3) Construir lote
        batch = []
        for it in items:
            nombre  = (it.get("nombre") or "").strip()
            descr   = (it.get("descripcion") or "").strip()
            caja    = it.get("dinerocaja") or "0"

            if not nombre:
                continue

            # evitar duplicados
            if PuntosPago.objects.filter(sucursalid=sucursal, nombre__iexact=nombre).exists():
                return JsonResponse({
                    "success": False,
                    "errors" : json.dumps({
                        "nombre": [{"message": f'«{nombre}» ya existe en la sucursal.'}]
                    })
                })

            try:
                caja_dec = Decimal(caja)
                if caja_dec < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                caja_dec = Decimal("0")

            batch.append(PuntosPago(
                sucursalid = sucursal,
                nombre     = nombre,
                descripcion= descr,
                dinerocaja = caja_dec
            ))

        if not batch:
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "__all__": [{"message": "Nada que guardar."}]
                })
            })

        # 4) Guardar
        PuntosPago.objects.bulk_create(batch)
        return JsonResponse({"success": True})


# ────────────────────────────────────────────────
# ②  Autocomplete  Sucursales sin Puntos-de-Pago
# ────────────────────────────────────────────────
class SucursalSinPuntoPagoAutocomplete(PaginatedAutocompleteMixin):
    model      = Sucursal
    text_field = "nombre"
    id_field   = "sucursalid"

    def extra_filter(self, qs, request):
        """
        Devuelve sólo las sucursales que aún NO tienen puntos de pago.
        """
        return (
            qs.annotate(pp_count=Count("puntospago"))
              .filter(pp_count=0)
              .order_by("nombre")
        )


class PuntosPagoListView(LoginRequiredMixin, View):
    """Listado + filtro por sucursal que YA tiene puntos de pago"""

    template_name = "visualizar_puntos_pago.html"

    # ---------- GET ----------
    def get(self, request):
        ctx = self._base_context()
        return render(request, self.template_name, ctx)

    # ---------- POST (filtro) ----------
    def post(self, request):
        ctx = self._base_context()
        sid = request.POST.get("sucursal")
        if sid:
            ctx["sucursal_seleccionada"] = suc = get_object_or_404(Sucursal, pk=sid)
            ctx["puntos_pago"] = PuntosPago.objects.filter(sucursalid=suc)
        return render(request, self.template_name, ctx)

    # ---------- contexto común ----------
    def _base_context(self):
        sucursales = (
            Sucursal.objects.annotate(num=Count("puntospago"))
                            .filter(num__gt=0)
        )
        return {
            "sucursales"          : sucursales,
            "puntos_pago"         : None,
            "sucursal_seleccionada": None,
        }

@login_required
def eliminar_punto_pago_view(request, puntopagoid):
    if request.method == 'POST':
        try:
            punto_pago = get_object_or_404(PuntosPago, pk=puntopagoid)
            nombre_punto = punto_pago.nombre
            punto_pago.delete()
            return JsonResponse({
                'success': True,
                'message': f'Punto de pago "{nombre_punto}" eliminado correctamente.'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Error al eliminar el punto de pago: {str(e)}'
            })
    return JsonResponse({'success': False, 'message': 'Método no permitido.'})

class SucursalConPuntosAutocomplete(PaginatedAutocompleteMixin):
    """
    Autocomplete ▸ solo sucursales que YA tienen al menos un Punto de Pago.
    Conserva term, page, per_page y JSON estándar.
    """
    model        = Sucursal
    label_field  = "nombre"
    per_page     = 10

    def extra_filter(self, qs, request):
        sub = PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
        return (
            qs.annotate(has_pp=Exists(sub))        # True si existe ≥1 punto
              .filter(has_pp=True)                 # ← elimina sucursales vacías
              .order_by("nombre")                  # orden alfabético consistente
        )

@method_decorator(login_required, name="dispatch")
@method_decorator(transaction.atomic,  name="dispatch")
class PuntosPagoUpdateAJAXView(View):
    template_name = "editar_puntos_pago.html"
    form_class    = PuntosPagoEditarForm

    # ---------- GET ----------
    def get(self, request, sucursal_id):
        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)

        puntos_qs = (
            PuntosPago.objects
                      .filter(sucursalid=sucursal)
                      .values("puntopagoid", "nombre", "descripcion", "dinerocaja")
        )
        puntos = [
            {
                "id"         : p["puntopagoid"],
                "nombre"     : p["nombre"],
                "descripcion": p["descripcion"] or "",
                "dinerocaja" : float(p["dinerocaja"] or 0),
            }
            for p in puntos_qs
        ]

        form = self.form_class(initial={
            "sucursal"             : sucursal.pk,
            "sucursal_autocomplete": sucursal.nombre,
        })

        return render(request, self.template_name, {
            "form"       : form,
            "sucursal"   : sucursal,
            "puntos_json": json.dumps(puntos),
        })

    # ---------- POST ----------
    @transaction.atomic
    def post(self, request, sucursal_id):
        old_suc = get_object_or_404(Sucursal, pk=sucursal_id)
        form    = self.form_class(request.POST, initial={"sucursal": old_suc.pk})

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        new_suc = form.cleaned_data["sucursal"]  # instancia

        # JSON de la tabla
        try:
            items = json.loads(request.POST.get("puntos_temp", "[]"))
        except json.JSONDecodeError:
            items = []

        if not items:
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "puntos_temp": [{"message": "Debe agregar al menos un punto de pago."}]
                })
            })

        # helpers
        norm = lambda s: (s or "").strip().casefold()

        # existentes en sucursal origen (para eliminar los que no vengan)
        existentes_origen = {
            pp.puntopagoid: pp
            for pp in PuntosPago.objects.select_for_update().filter(sucursalid=old_suc)
        }

        # existentes por nombre en sucursal destino (para “merge” al crear)
        existentes_dest_por_nombre = {
            norm(pp.nombre): pp
            for pp in PuntosPago.objects.select_for_update().filter(sucursalid=new_suc)
        }

        keep_ids, to_create = set(), []

        for it in items:
            pid    = it.get("id")
            nombre = (it.get("nombre") or "").strip()
            descr  = (it.get("descripcion") or "").strip()
            caja   = it.get("dinerocaja") or "0"

            if not nombre:
                # OJO: esta validación es por ítem de la tabla, no por el input de arriba
                return JsonResponse({
                    "success": False,
                    "errors" : json.dumps({
                        "puntos_temp": [{"message": "Hay una fila sin nombre en la tabla."}]
                    })
                })

            try:
                caja_dec = Decimal(str(caja))
                if caja_dec < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                caja_dec = Decimal("0")

            # ---------- UPDATE ----------
            if pid:
                pid = int(pid)
                keep_ids.add(pid)
                obj = existentes_origen.get(pid)

                if not obj:
                    # el ID ya no existe en BD: lo tratamos como nuevo
                    # y seguimos la rama CREATE (ver abajo) sin error
                    pid = None
                else:
                    name_changed = norm(obj.nombre) != norm(nombre)
                    suc_changed  = obj.sucursalid_id != new_suc.pk

                    if name_changed or suc_changed:
                        # ¿Hay otro con el mismo nombre en la sucursal destino?
                        dup = PuntosPago.objects.filter(
                            sucursalid=new_suc, nombre__iexact=nombre
                        ).exclude(puntopagoid=pid).first()
                        if dup:
                            # En lugar de error, “fusionamos” al duplicado
                            dup.descripcion = descr
                            dup.dinerocaja  = caja_dec
                            dup.save(update_fields=["descripcion", "dinerocaja"])
                            keep_ids.add(dup.puntopagoid)
                            # y este lo marcamos para borrar si venía de old_suc
                            continue

                    # actualización normal
                    obj.sucursalid  = new_suc
                    obj.nombre      = nombre
                    obj.descripcion = descr
                    obj.dinerocaja  = caja_dec
                    obj.save()
                    continue  # next item

            # ---------- CREATE ----------
            if not pid:
                name_key = norm(nombre)
                if name_key in existentes_dest_por_nombre:
                    # Si ya existe en la sucursal destino con ese nombre,
                    # lo tratamos como UPDATE (merge), no como error.
                    obj = existentes_dest_por_nombre[name_key]
                    obj.descripcion = descr
                    obj.dinerocaja  = caja_dec
                    obj.save(update_fields=["descripcion", "dinerocaja"])
                    keep_ids.add(obj.puntopagoid)
                else:
                    to_create.append(PuntosPago(
                        sucursalid=new_suc, nombre=nombre,
                        descripcion=descr, dinerocaja=caja_dec
                    ))

        # eliminar los que ya no vienen (solo de la sucursal origen)
        delete_ids = [pk for pk in existentes_origen if pk not in keep_ids]
        if delete_ids:
            PuntosPago.objects.filter(puntopagoid__in=delete_ids).delete()

        if to_create:
            PuntosPago.objects.bulk_create(to_create)

        messages.success(request, "Puntos de pago actualizados exitosamente.")
        return JsonResponse({
            "success"     : True,
            "redirect_url": reverse("visualizar_puntos_pago")
        })

class SucursalEditarPuntoPagoAutocomplete(PaginatedAutocompleteMixin):
    """
    Devuelve:
      • la sucursal actual   (?current_sucursal_id=…)
      • + las sucursales que NO tienen puntos de pago
    """
    model       = Sucursal
    label_field = "nombre"
    per_page    = 10   # el mixin hace la paginación

    # ------------ filtro extra ------------
    def extra_filter(self, qs, request):
        term        = request.GET.get("term", "").strip()
        current_id  = request.GET.get("current_sucursal_id", "")

        # ¿la sucursal tiene puntos de pago?
        sub = PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
        qs  = qs.annotate(has_pp=Exists(sub))

        # • sin puntos de pago                 OR
        # • la sucursal actual (si el parámetro es válido)
        filtro = Q(has_pp=False)
        if current_id.isdigit():
            filtro |= Q(pk=current_id)

        qs = qs.filter(filtro)

        if term:
            qs = qs.filter(nombre__icontains=term)

        return qs.order_by("nombre")


class RolCreateAJAXView(LoginRequiredMixin, FormView):
    """
    • GET  → renderiza formulario clásico
    • POST → alta AJAX; responde JSON {success, message | errors}
    """
    template_name = "agregar_rol.html"
    form_class    = RolForm

    # ---------- POST OK ----------
    def form_valid(self, form):
        rol = form.save()

        # Llamada AJAX (fetch) → devolvemos JSON
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": "Rol agregado exitosamente.",
                "rol": {
                    "id":   rol.pk,
                    "name": rol.nombre
                }
            })

        # Petición clásica → redirección donde corresponda
        return redirect("listar_roles")  # ajusta a tu flujo

    # ---------- POST errores ----------
    def form_invalid(self, form):
        return JsonResponse(
            {"success": False, "errors": form.errors.get_json_data()},
            status=400
        )


class RolListView(LoginRequiredMixin, ListView):
    """
    Muestra la tabla de roles con DataTable.
    """
    template_name       = "visualizar_roles.html"
    model               = Rol
    context_object_name = "roles"


class RolUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    ▸ Edita un rol vía AJAX manteniendo la UX de ‘Editar Sucursal’.
    """
    model         = Rol
    pk_url_kwarg  = "rol_id"
    form_class    = RolEditarForm
    template_name = "editar_rol.html"
    success_url   = reverse_lazy("visualizar_roles")

    # -------- AJAX OK --------
    def form_valid(self, form):
        self.object = form.save()
        msg = f'Rol «{self.object.nombre}» actualizado correctamente.'
        # Siempre guardar el mensaje (aparecerá tras la redirección)
        messages.success(self.request, msg)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect_url": str(self.success_url),
            })
        return super().form_valid(form)

    # -------- AJAX KO --------
    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)


@login_required
def eliminar_rol_view(request, rol_id):
    if request.method == 'POST':
        rol = get_object_or_404(Rol, pk=rol_id)
        nombre_rol = rol.nombre
        rol.delete()
        messages.success(request, f'Se eliminó el rol "{nombre_rol}" correctamente.')
        return redirect('visualizar_roles')
    # Si no es POST, retornamos un JSON de error (o podrías redirigir)
    return JsonResponse({'success': False, 'message': 'Error al eliminar el rol.'})


class UsuarioCreateAJAXView(LoginRequiredMixin, FormView):
    template_name = "agregar_usuario.html"
    form_class    = UsuarioForm
    success_url   = reverse_lazy("visualizar_usuarios")   # ajusta la URL si existe

    # ----- POST ↩︎ JSON ----------------------------------------------------
    def form_valid(self, form):
        usuario = form.save()
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": "Usuario creado exitosamente.",
                "redirect_url": str(self.success_url),
            })
        messages.success(self.request, "Usuario creado exitosamente.")
        return redirect(self.success_url)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400
            )
        return self.render_to_response(self.get_context_data(form=form))


# ──────────────────────────────────────────────────────────────────
#  Autocomplete «Rol»
# ──────────────────────────────────────────────────────────────────
class RolAutocompleteView(PaginatedAutocompleteMixin):
    """
    Devuelve roles paginados para el componente de autocompletado.
    """
    model      = Rol
    text_field = "nombre"
    id_field   = "rolid"
    per_page   = 10


class UsuarioListView(LoginRequiredMixin, ListView):
    """
    Muestra los usuarios en una tabla paginada con DataTables.

    • `select_related('rolid')` evita el N+1 al traer el Rol.
    • Se ordena alfabéticamente por nombre de usuario.
    • `context_object_name = "usuarios"` → variable en la plantilla.
    """
    model               = Usuario
    template_name       = "visualizar_usuarios.html"
    context_object_name = "usuarios"
    paginate_by         = 50

    def get_queryset(self):
        return (
            Usuario.objects
            .select_related("rolid")
            .order_by("nombreusuario")
        )

@login_required
def eliminar_usuario_view(request, usuarioid):
    usuario = get_object_or_404(Usuario, pk=usuarioid)
    nombre_usuario = usuario.nombreusuario
    usuario.delete()
    messages.success(request, f'Usuario "{nombre_usuario}" eliminado exitosamente.')
    return redirect('visualizar_usuarios')


class UsuarioUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    Vista de actualización de usuarios:

    • GET  → Renderiza el formulario “editar_usuario.html”.
    • POST →   – Fetch/AJAX  ⇒ JSON   (success / errors)
               – Navegación   ⇒ redirect + messages.

    save() del formulario ya gestiona el cambio de contraseña.
    """
    model         = Usuario
    form_class    = UsuarioEditarForm
    template_name = "editar_usuario.html"
    pk_url_kwarg  = "usuario_id"              # /usuarios/editar/<usuario_id>/

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _is_ajax(request) -> bool:
        return request.headers.get("x-requested-with") == "XMLHttpRequest"

    # ---------------------------------------------------------------- context
    def get_context_data(self, **kwargs):
        """Inyectamos la lista de campos de contraseña para el bucle del template."""
        ctx = super().get_context_data(**kwargs)
        frm = ctx["form"]
        ctx["password_fields"] = [frm["contraseña"], frm["confirmar_contraseña"]]
        return ctx

    # ---------------------------------------------------------------- POST
    def form_valid(self, form):
        usuario = form.save()  # El ModelForm setea rol y contraseña si aplica

        if self._is_ajax(self.request):
            return JsonResponse({
                "success"     : True,
                "redirect_url": reverse("visualizar_usuarios"),
                "nombre"      : usuario.nombreusuario,
            })

        messages.success(
            self.request,
            f'Usuario «{usuario.nombreusuario}» actualizado correctamente.'
        )
        return super().form_valid(form)

    def form_invalid(self, form):
        if self._is_ajax(self.request):
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)

    # ---------------------------------------------------------------- redirect
    def get_success_url(self):
        return reverse_lazy("visualizar_usuarios")




@method_decorator(transaction.atomic, name="dispatch")
class EmpleadoCreateAJAXView(LoginRequiredMixin, View):
    template_name = "agregar_empleado.html"
    form_class    = EmpleadoCreateForm

    # ---------- GET ----------
    def get(self, request):
        form = self.form_class()
        return render(request, self.template_name, {"form": form})

    # ---------- POST ----------
    def post(self, request):
        form = self.form_class(request.POST)

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : json.dumps(form.errors.get_json_data(escape_html=True))
            })

        try:
            emp = form.save()
            logger.info("Empleado creado %s", emp.pk)
        except Exception as exc:
            logger.exception("Error al guardar empleado")
            return JsonResponse({
                "success": False,
                "errors" : json.dumps({
                    "__all__": [{"message": "Ocurrió un error inesperado."}]
                })
            })

        return JsonResponse({"success": True})


# ─────────────────────────────────────────────────────────────
# 2. Autocompletados
# ─────────────────────────────────────────────────────────────
class UsuarioDisponibleAutocomplete(PaginatedAutocompleteMixin):
    """
    • Devuelve usuarios que **no** tienen empleado asociado
    • Incluye (?current=<id>) al editar para que siga apareciendo el usuario ya asignado
    """
    model       = Usuario
    id_field    = "pk"
    text_field  = "nombreusuario"

    def extra_filter(self, qs, request):
        # Excluir los que ya están vinculados, sin depender de related_name
        sub = Empleado.objects.filter(usuarioid=OuterRef("pk"))
        qs  = qs.annotate(has_emp=Exists(sub)).filter(has_emp=False)

        # incluir el usuario actual (modo edición)
        cur = request.GET.get("current", "").strip()
        if cur.isdigit():
            qs = qs | Usuario.objects.filter(pk=cur)

        # búsqueda por término
        term = request.GET.get("term", "").strip()
        if term:
            qs = qs.filter(nombreusuario__icontains=term)

        return qs.distinct().order_by("nombreusuario")


class SucursalAutocomplete(PaginatedAutocompleteMixin):
    """Todas las sucursales con búsqueda por nombre."""
    model     = Sucursal
    id_field  = "pk"
    text_field = "nombre"

    def extra_filter(self, qs, request):
        term = request.GET.get("term", "").strip()
        return qs.filter(nombre__icontains=term) if term else qs



class EmpleadoListView(LoginRequiredMixin, ListView):
    """
    Tabla paginada de empleados con DataTables (idéntico estilo a usuarios).

    • `order_by("nombre", "apellido")` para orden alfabético.
    • `context_object_name = "empleados"` → variable usada en la plantilla.
    """
    model               = Empleado
    template_name       = "visualizar_empleados.html"
    context_object_name = "empleados"
    paginate_by         = 50          # DataTables usa toda la page; igualmente paginamos.

    def get_queryset(self):
        return (
            Empleado.objects
            .order_by("nombre", "apellido")   # puedes añadir select_related() si lo necesitas
        )


class EmpleadoUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    • GET  → renderiza “editar_empleado.html”.
    • POST →
        – Fetch/AJAX ⇒ JSON (success / errors)
        – Navegación  ⇒ redirect + messages
    """
    model         = Empleado
    form_class    = EditarEmpleadoForm          # ⇠ ver punto 3
    template_name = "editar_empleado.html"
    pk_url_kwarg  = "empleado_id"               # /empleados/editar/<empleado_id>/

    # ---------- util ----------
    @staticmethod
    def _is_ajax(request) -> bool:
        return request.headers.get("x-requested-with") == "XMLHttpRequest"

    # ---------- POST ----------
    def form_valid(self, form):
        emp = form.save()

        if self._is_ajax(self.request):
            return JsonResponse({
                "success"     : True,
                "redirect_url": reverse("visualizar_empleados"),
                "nombre"      : f"{emp.nombre} {emp.apellido}",
            })

        messages.success(
            self.request,
            f'Empleado «{emp.nombre} {emp.apellido}» actualizado correctamente.',
        )
        return super().form_valid(form)

    def form_invalid(self, form):
        if self._is_ajax(self.request):
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)

    # ---------- redirect ----------
    def get_success_url(self):
        return reverse_lazy("visualizar_empleados")


@login_required
def eliminar_empleado_view(request, empleado_id):
    empleado = get_object_or_404(Empleado, pk=empleado_id)
    nombre_completo = f"{empleado.nombre} {empleado.apellido}"
    empleado.delete()
    messages.success(request, f'Empleado "{nombre_completo}" ha sido eliminado exitosamente.')
    return redirect('visualizar_empleados')


@method_decorator(transaction.atomic, name="dispatch")
class HorarioCreateAJAXView(LoginRequiredMixin, View):
    """
    • GET  → renderiza formulario “agregar_horario.html”
    • POST → guarda horarios a partir de 'horarios' (JSON)
               y responde JSON {success, errors}
    """
    template_name = "agregar_horario.html"
    form_class    = HorariosNegocioForm
    success_msg   = "Horario(s) agregado(s) exitosamente."

    def get(self, request):
        form = self.form_class()
        # Lista de días para el template
        days = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
        return render(request, self.template_name, {
            "form":  form,
            "days":  days,
        })

    def post(self, request):
        form = self.form_class(request.POST)
        # 1) validación del formulario base
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors": json.dumps(
                    form.errors.get_json_data(escape_html=True)
                )
            }, status=400)

        # 2) cargar array de horarios (JSON)
        raw = request.POST.get("horarios", "[]")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        if not data:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "horarios": [{"message": "Debe agregar al menos un horario."}]
                })
            }, status=400)

        # 3) construir batch de HorariosNegocio
        suc = form.cleaned_data["sucursalid"]
        batch = []
        for h in data:
            dia = h.get("dia")
            ap  = h.get("horaapertura")
            ci  = h.get("horacierre")
            if dia and ap and ci:
                batch.append(HorariosNegocio(
                    sucursalid   = suc,
                    dia_semana   = dia,
                    horaapertura = ap,
                    horacierre   = ci
                ))
        if not batch:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "__all__": [{"message": "No hay horarios válidos para guardar."}]
                })
            }, status=400)

        # 4) bulk create y responder éxito
        HorariosNegocio.objects.bulk_create(batch)
        return JsonResponse({"success": True})

class SucursalSinHorarioAutocomplete(PaginatedAutocompleteMixin):
    """
    Autocomplete de sucursales sin horarios (paginado).
    """
    model      = Sucursal
    id_field   = "pk"
    text_field = "nombre"

    def extra_filter(self, qs, request):
        return qs.filter(horariosnegocio__isnull=True).order_by("nombre")

class HorariosListView(LoginRequiredMixin, View):
    template_name = "visualizar_horarios.html"

    # GET
    def get(self, request):
        ctx = self._base_context()
        return render(request, self.template_name, ctx)

    # POST (filtro)
    def post(self, request):
        ctx = self._base_context()
        sid = request.POST.get("sucursal")
        if sid:
            ctx["sucursal_seleccionada"] = suc = get_object_or_404(Sucursal, pk=sid)
            ctx["horarios"] = HorariosNegocio.objects.filter(sucursalid=suc)
        return render(request, self.template_name, ctx)

    # contexto común
    def _base_context(self):
        sucursales = (
            Sucursal.objects.annotate(num=Count("horariosnegocio"))
                            .filter(num__gt=0)               # sólo sucursales con horarios
        )
        return {
            "sucursales"          : sucursales,
            "horarios"            : None,
            "sucursal_seleccionada": None,
        }

# ---------- autocomplete ----------
class SucursalConHorariosAutocomplete(PaginatedAutocompleteMixin):
    """
    Autocomplete ▸ sólo sucursales que YA tienen al menos un horario.
    term, page, per_page & JSON estándar.
    """
    model        = Sucursal
    label_field  = "nombre"
    per_page     = 10

    def extra_filter(self, qs, request):
        sub = HorariosNegocio.objects.filter(sucursalid=OuterRef("pk"))
        return (
            qs.annotate(has_h=Exists(sub))
              .filter(has_h=True)
              .order_by("nombre")
        )


@method_decorator(transaction.atomic, name="dispatch")
class HorarioUpdateAJAXView(LoginRequiredMixin, View):
    """
    • GET  → muestra la plantilla con horarios actuales.
    • POST → recibe JSON con lista de horarios, los reemplaza y responde JSON.
    """
    template_name = "editar_horario.html"
    form_class    = EditarHorariosSucursalForm
    success_msg   = "Horarios actualizados correctamente."

    def get(self, request, sucursal_id):
        suc = get_object_or_404(Sucursal, pk=sucursal_id)
        hrs = (
            HorariosNegocio.objects
            .filter(sucursalid=suc)
            .order_by("dia_semana")
        )
        days = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
        return render(request, self.template_name, {
            "sucursal": suc,
            "horarios": hrs,
            "days":     days,
        })

    def post(self, request, sucursal_id):
        # 1) Cargo la payload JSON
        try:
            payload = json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse(
                {"success": False, "error": "JSON inválido."},
                status=400
            )

        horarios = payload.get("horarios", [])
        form = self.form_class(payload, horarios_present=bool(horarios))

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors": json.dumps(
                    form.errors.get_json_data(escape_html=True)
                ),
            }, status=400)

        # 2) Borro los horarios de la sucursal original (de la URL)
        original_suc = get_object_or_404(Sucursal, pk=sucursal_id)
        HorariosNegocio.objects.filter(sucursalid=original_suc).delete()

        # 3) Creo los nuevos horarios en la sucursal seleccionada
        new_suc = get_object_or_404(
            Sucursal, pk=form.cleaned_data["sucursalid"]
        )
        nuevos = [
            HorariosNegocio(
                sucursalid=new_suc,
                dia_semana=h["dia"],
                horaapertura=h["horaapertura"],
                horacierre=h["horacierre"],
            )
            for h in horarios
        ]
        HorariosNegocio.objects.bulk_create(nuevos)

        # 4) Mensaje de éxito
        messages.success(request, self.success_msg)
        return JsonResponse({"success": True})



@login_required
def eliminar_horario_view(request, horario_id):
    if request.method == 'POST':
        horario = get_object_or_404(HorariosNegocio, pk=horario_id)
        horario.delete()
        return JsonResponse({'success': True, 'message': 'Horario eliminado exitosamente.'})
    return JsonResponse({'success': False, 'message': 'Método no permitido.'})


# ────────────────────────────────────────────────────────────────
#  AJAX Create — Horario de Caja
# ────────────────────────────────────────────────────────────────
@method_decorator(transaction.atomic, name="dispatch")
class HorarioCajaCreateAJAXView(LoginRequiredMixin, View):
    """
    • GET  → muestra el form y lista vacía.
    • POST → recibe JSON con lista de horarios, los guarda y responde JSON.
    """
    template_name = "agregar_horario_caja.html"
    form_class    = HorarioCajaForm
    success_msg   = "Horario(s) de caja agregado(s) exitosamente."

    def get(self, request):
        form = self.form_class()
        days = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
        return render(request, self.template_name, {
            "form": form,
            "days": days,
        })

    def post(self, request):
        raw = request.POST.get("horarios", "[]")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        horarios_present = bool(data)

        form = self.form_class(request.POST, horarios_present=horarios_present)
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors": json.dumps(form.errors.get_json_data(escape_html=True))
            }, status=400)

        puntopago = form.cleaned_data["puntopagoid"]
        batch = []
        # Si viene lista JSON, la usamos directamente
        if horarios_present:
            for h in data:
                batch.append(HorarioCaja(
                    puntopagoid=puntopago,
                    dia_semana=h["dia"],
                    horaapertura=h["horaapertura"],
                    horacierre=h["horacierre"],
                ))
        else:
            # Fallback a campos individuales
            dias = form.cleaned_data["dia_semana"].split(",")
            ap   = form.cleaned_data["horaapertura"]
            ci   = form.cleaned_data["horacierre"]
            for d in dias:
                batch.append(HorarioCaja(
                    puntopagoid=puntopago,
                    dia_semana=d,
                    horaapertura=ap,
                    horacierre=ci,
                ))

        HorarioCaja.objects.bulk_create(batch)
        return JsonResponse({"success": True})


class SucursalAgregarHorarioCajaAutocomplete(View):
    """
    Devuelve sucursales que tienen >=1 Punto de Pago SIN ningún HorarioCaja.
    JSON: { results: [{id, text}], has_more: bool }
    """
    per_page = 10

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        # IDs de sucursales con al menos un punto de pago sin horarios
        suc_ids = (
            PuntosPago.objects
            .filter(horarios_caja__isnull=True)        # ← usa related_name en HorarioCaja
            .values("sucursalid_id")
            .distinct()
        )

        qs = Sucursal.objects.filter(sucursalid__in=Subquery(suc_ids))
        if term:
            qs = qs.filter(nombre__icontains=term)

        qs = qs.order_by("nombre", "sucursalid")       # orden estable para paginación

        paginator = Paginator(qs, self.per_page)
        page_obj  = paginator.get_page(page)

        results = [{"id": s.sucursalid, "text": s.nombre} for s in page_obj.object_list]
        return JsonResponse({"results": results, "has_more": page_obj.has_next()})


class PuntosPagoAgregarHorarioCajaAutocomplete(View):
    """
    Devuelve puntos de pago de la sucursal dada que NO tengan HorarioCaja.
    JSON: { results: [{id, text}], has_more: bool }
    """
    per_page = 10

    def get(self, request):
        term   = (request.GET.get("term") or "").strip()
        page   = int(request.GET.get("page") or 1)
        suc_id = request.GET.get("sucursal_id")

        if not suc_id:
            return JsonResponse({"results": [], "has_more": False})

        qs = PuntosPago.objects.filter(
            sucursalid_id=suc_id,
            horarios_caja__isnull=True                 # ← usa related_name en HorarioCaja
        )
        if term:
            qs = qs.filter(nombre__icontains=term)

        qs = qs.order_by("nombre", "puntopagoid")      # orden estable para paginación

        paginator = Paginator(qs, self.per_page)
        page_obj  = paginator.get_page(page)

        results = [{"id": p.puntopagoid, "text": p.nombre} for p in page_obj.object_list]
        return JsonResponse({"results": results, "has_more": page_obj.has_next()})


# ─────────── Vista principal ───────────
@method_decorator(login_required, name="dispatch")
class VisualizarHorariosCajasView(View):
    template_name = "visualizar_horarios_cajas.html"

    def get(self, request):
        return render(request, self.template_name, self._base_context())

    def post(self, request):
        ctx = self._base_context()
        sid = request.POST.get("sucursal")
        if sid:
            suc = get_object_or_404(Sucursal, pk=sid)
            ctx["sucursal_seleccionada"] = suc

            # sólo puntos de esa sucursal que ya tienen al menos 1 horario
            sub = HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))
            ctx["puntos_pago"] = (
                PuntosPago.objects
                .filter(sucursalid=suc)
                .annotate(has_h=Exists(sub))
                .filter(has_h=True)
                .order_by("nombre")
            )

            pid = request.POST.get("punto_pago")
            if pid:
                pp = get_object_or_404(PuntosPago, pk=pid)
                ctx["punto_pago_seleccionado"] = pp
                ctx["horarios"] = HorarioCaja.objects.filter(puntopagoid=pp)

        return render(request, self.template_name, ctx)

    def _base_context(self):
        # sucursales que tienen al menos un punto de pago con horarios
        sub_pp = PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
        sub_h  = HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))
        qs = (
            Sucursal.objects
            .annotate(has_pp=Exists(sub_pp.filter(Exists(sub_h))))
            .filter(has_pp=True)
            .order_by("nombre")
        )
        return {
            "sucursales": qs,
            "sucursal_seleccionada": None,
            "puntos_pago": [],
            "punto_pago_seleccionado": None,
            "horarios": [],
        }


# ─────────── Autocomplete Sucursal ───────────
class SucursalHorarioCajaAutocomplete(PaginatedAutocompleteMixin):
    model        = Sucursal
    id_field     = "pk"
    text_field   = "nombre"
    per_page     = 10

    def extra_filter(self, qs, request):
        # sólo sucursales con al menos un PuntoPago que tenga horarios
        sub_pp = PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
        sub_h  = HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))
        return (
            qs.annotate(has=Exists(sub_pp.filter(Exists(sub_h))))
              .filter(has=True)
              .order_by("nombre")
        )


# ─────────── Autocomplete PuntoPago ───────────
class PuntoPagoHorarioCajaAutocomplete(PaginatedAutocompleteMixin):
    model        = PuntosPago
    id_field     = "pk"
    text_field   = "nombre"
    per_page     = 10

    def extra_filter(self, qs, request):
        # filtramos por sucursal_id y sólo puntos de pago con al menos un horario
        sid = request.GET.get("sucursal_id")
        sub_h = HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))
        qs = qs.annotate(has=Exists(sub_h)).filter(has=True)
        if sid:
            qs = qs.filter(sucursalid_id=sid)
        return qs.order_by("nombre")



@login_required
def eliminar_horario_caja_view(request, horario_id):
    try:
        horario = get_object_or_404(HorarioCaja, pk=horario_id)
        horario.delete()
        return JsonResponse({'success': True, 'message': 'Horario eliminado exitosamente.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': 'Ocurrió un error al eliminar el horario.'})


# ──────────────────────────────────────────────────────────────
#  Vista principal (GET  → plantilla  |  POST → JSON)
# ──────────────────────────────────────────────────────────────
@method_decorator(transaction.atomic, name="dispatch")
class EditarHorarioCajaView(LoginRequiredMixin, View):
    template_name = "editar_horario_caja.html"
    form_class    = EditarHorarioCajaForm
    success_msg   = "Horarios actualizados correctamente."

    # ─── GET ────────────────────────────────────────────────────
    def get(self, request, puntopagoid):
        pp       = get_object_or_404(PuntosPago, pk=puntopagoid)
        horarios = (HorarioCaja.objects
                               .filter(puntopagoid=pp)
                               .order_by("dia_semana"))
        dias     = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]

        return render(request, self.template_name, {
            "punto_pago": pp,
            "sucursal"  : pp.sucursalid,
            "horarios"  : horarios,
            "days"      : dias,
        })

    # ─── POST (AJAX) ────────────────────────────────────────────
    def post(self, request, puntopagoid):
        try:
            payload = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"success": False,
                                 "error":   "JSON inválido."},
                                status=400)

        form = self.form_class(payload,
                               horarios_present=bool(payload.get("horarios")))
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors" : form.errors.as_json(escape_html=True)
            }, status=400)

        new_sucursal_id  = form.cleaned_data["sucursalid"]
        new_puntopago_id = form.cleaned_data["puntopagoid"]

        old_pp = get_object_or_404(PuntosPago, pk=puntopagoid)

        try:
            with transaction.atomic():

                # 1 · Siempre borrar horarios del punto de pago original
                HorarioCaja.objects.filter(puntopagoid=old_pp).delete()

                # 2 · Si el usuario eligió OTRO punto de pago, limpiar ese nuevo
                if str(new_puntopago_id) != str(old_pp.pk):
                    HorarioCaja.objects.filter(puntopagoid_id=new_puntopago_id).delete()

                # 3 · Mover la caja de sucursal **solo si**:
                #       • El usuario NO cambió de punto de pago (sigue la misma caja)
                #       • Y cambió la sucursal
                if (str(new_puntopago_id) == str(old_pp.pk) and
                    str(new_sucursal_id)  != str(old_pp.sucursalid_id)):
                    new_suc = get_object_or_404(Sucursal, pk=new_sucursal_id)
                    old_pp.sucursalid = new_suc
                    old_pp.save(update_fields=["sucursalid"])

                # 4 · Crear la lista nueva de horarios
                nuevos = [
                    HorarioCaja(
                        puntopagoid_id=new_puntopago_id,
                        dia_semana    =h["dia"],
                        horaapertura  =h["horaapertura"],
                        horacierre    =h["horacierre"],
                    )
                    for h in payload["horarios"]
                ]
                HorarioCaja.objects.bulk_create(nuevos)

            messages.success(request, self.success_msg)
            return JsonResponse({"success": True})

        except Exception as exc:
            # En desarrollo imprime el traceback para ver la causa exacta
            import traceback, sys
            traceback.print_exc(file=sys.stderr)
            return JsonResponse({"success": False,
                                 "error":   str(exc)},
                                status=500)




class SucursalDisponibleCajaAutocomplete(PaginatedAutocompleteMixin):
    model = Sucursal

    def extra_filter(self, qs, request):
        """
        Devuelve sucursales que tengan al menos un punto de pago sin horario
        O la sucursal actualmente ligada al formulario (actual_id).
        """
        actual_id = request.GET.get("actual_id")

        sub_libre = PuntosPago.objects.filter(
            sucursalid=OuterRef("pk")
        ).filter(~Exists(HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))))

        qs = qs.annotate(tiene_libre=Exists(sub_libre))

        filtros = Q(tiene_libre=True)
        if actual_id and actual_id.isdigit():
            filtros |= Q(pk=actual_id)

        return qs.filter(filtros).distinct()


class PuntoCajaDisponibleAutocomplete(PaginatedAutocompleteMixin):
    model = PuntosPago

    def extra_filter(self, qs, request):
        """
        Devuelve los puntos de pago de la sucursal seleccionada que no tengan
        horario O el punto de pago actualmente ligado al formulario (actual_id).
        """
        suc_id    = request.GET.get("sucursal_id")
        actual_id = request.GET.get("actual_id")

        if suc_id and suc_id.isdigit():
            qs = qs.filter(sucursalid_id=suc_id)

        sub_horario = HorarioCaja.objects.filter(puntopagoid=OuterRef("pk"))
        qs = qs.annotate(ocupado=Exists(sub_horario))

        filtros = Q(ocupado=False)
        if actual_id and actual_id.isdigit():
            filtros |= Q(pk=actual_id)

        return qs.filter(filtros).distinct()





class ClienteCreateAJAXView(LoginRequiredMixin, FormView):
    """
    • GET  → muestra el formulario clásico.
    • POST → alta vía AJAX → responde JSON.
    """
    template_name = "agregar_cliente.html"
    form_class    = ClienteForm

    # ---------- POST OK ----------
    def form_valid(self, form):
        cliente = form.save()

        # llamada AJAX (fetch)
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": "Cliente agregado exitosamente.",
                "cliente": {
                    "id":   cliente.pk,
                    "name": f"{cliente.nombre} {cliente.apellido}"
                }
            })

        # Petición clásica (no-AJAX) – redirecciona a donde corresponda
        return redirect("listar_clientes")      # ajusta a tu flujo

    # ---------- POST con errores ----------
    def form_invalid(self, form):
        return JsonResponse(
            {"success": False, "errors": form.errors.get_json_data()},
            status=400
        )


class ClienteListView(LoginRequiredMixin, ListView):
    """
    Lista de clientes con DataTable.
    """
    template_name       = "visualizar_clientes.html"
    model               = Cliente
    context_object_name = "clientes"
    ordering            = ["nombre", "apellido"]   # opcional


@login_required
def eliminar_cliente(request, clienteid):
    cliente = get_object_or_404(Cliente, clienteid=clienteid)
    cliente.delete()
    messages.success(request, 'Cliente eliminado exitosamente.')
    return redirect('visualizar_clientes')


class ClienteUpdateAJAXView(LoginRequiredMixin, UpdateView):
    model         = Cliente
    pk_url_kwarg  = "cliente_id"
    form_class    = EditarClienteForm
    template_name = "editar_cliente.html"
    success_url   = reverse_lazy("visualizar_clientes")

    def form_valid(self, form):
        self.object = form.save()
        msg = f"Cliente «{self.object.nombre} {self.object.apellido}» actualizado."

        # Guarda SIEMPRE el mensaje en sesión (sirve tanto para AJAX como no-AJAX)
        messages.success(self.request, msg)

        # Respuesta AJAX: el front hace window.location = redirect_url
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect_url": str(self.success_url),
            })

        # No-AJAX: redirección estándar
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400
            )
        return super().form_invalid(form)


class GenerarVentaView(LoginRequiredMixin, View):
    template_name = "generar_venta.html"
    success_url   = reverse_lazy("generar_venta")

    # ---------- GET ----------
    def get(self, request, *args, **kwargs):
        form = GenerarVentaForm(request.GET or None)
        return render(request, self.template_name, self._base_context(form))

    # ---------- POST ----------
    def post(self, request, *args, **kwargs):
        form = GenerarVentaForm(request.POST)
        if not form.is_valid():
            if getattr(settings, "DEBUG", False):
                return JsonResponse({'success': False, 'error': 'Formulario inválido.', 'details': form.errors})
            return JsonResponse({'success': False, 'error': 'Formulario inválido.'})

        data = form.cleaned_data
        suc_inst = data['sucursal']
        pp_inst  = data['puntopago']

        productos  = data['productos']     # LISTA (por clean_productos)
        cantidades = data['cantidades']    # LISTA (por clean_cantidades)

        if not productos:
            return JsonResponse({'success': False, 'error': 'Carrito vacío.'})

        try:
            prod_ids = [int(p) for p in productos]
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'IDs de productos inválidos.'})

        if len(cantidades) < len(prod_ids):
            return JsonResponse({'success': False, 'error': 'Faltan cantidades para algunos productos.'})

        prods_qs  = Producto.objects.filter(productoid__in=prod_ids)
        prods_map = {p.productoid: p for p in prods_qs}

        detalles = []
        total = Decimal('0')

        for idx, pid in enumerate(prod_ids):
            prod = prods_map.get(pid)
            if not prod:
                continue

            try:
                qty = int(cantidades[idx])
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': f'Cantidad inválida para producto {pid}.'})

            # qty == 0 => ignora
            # qty < 0  => permitido (devolución/ajuste)
            if qty == 0:
                continue

            try:
                precio_unit = Decimal(str(prod.precio or 0))
            except (InvalidOperation, TypeError):
                precio_unit = Decimal('0')

            subtotal = precio_unit * Decimal(qty)
            total += subtotal

            detalles.append({
                'productoid'     : prod.productoid,
                'producto'       : prod.nombre,
                'cantidad'       : qty,
                'precio_unitario': precio_unit,
                'subtotal'       : subtotal
            })

        if not detalles:
            return JsonResponse({'success': False, 'error': 'No hay ítems válidos para procesar.'})

        # pagos puede llegar como LISTA o como STRING JSON
        pagos = data.get("pagos") or []
        if isinstance(pagos, str):
            try:
                pagos = json.loads(pagos or "[]")
            except Exception:
                pagos = []

        medio_pago_simple = (data.get("medio_pago") or "").strip().lower()

        # ✅ NUEVO: efectivo recibido (para CAMBIO)
        efectivo_recibido = data.get("efectivo_recibido") or Decimal("0")

        if getattr(settings, "DEBUG", False):
            try:
                print("\n[VENTA DEBUG] ---------------------------")
                print("TOTAL_BACK:", total, "type:", type(total))
                print("MEDIO_BACK:", medio_pago_simple, "raw:", data.get("medio_pago"))
                print("PAGOS_BACK:", pagos, "type:", type(pagos))
                print("EFECTIVO_RECIBIDO_BACK:", efectivo_recibido, "type:", type(efectivo_recibido))
                print("PROD_IDS:", prod_ids)
                print("CANTIDADES:", cantidades)
                print("DETALLES_BACK:", [
                    (d["productoid"], d["cantidad"], d["precio_unitario"], d["subtotal"])
                    for d in detalles
                ])
            except Exception as _e:
                print("[VENTA DEBUG] error imprimiendo debug:", _e)

        pagos_normalizados = self._normalize_payments(pagos, total, medio_pago_simple)

        # si total > 0: exige pagos
        # si total <= 0: NO exige pagos (ajustes / devoluciones)
        if total > 0 and not pagos_normalizados:
            return JsonResponse({'success': False, 'error': 'Debe indicar el/los pagos.'})

        if total <= 0:
            pagos_normalizados = []

        return self._crear_venta(
            request.user, suc_inst, pp_inst,
            data.get('cliente_id'),
            pagos_normalizados,
            detalles, total,
            efectivo_recibido,  # ✅ nuevo
        )

    def _base_context(self, form, detalles=None, total=Decimal('0')):
        return {'form': form, 'detalles': detalles or [], 'total': total}

    @staticmethod
    def _to_decimal(x):
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _normalize_payments(pagos_list, total, medio_pago_simple=""):
        allowed = {"nequi", "efectivo", "daviplata", "tarjeta", "banco_caja_social"}
        total = GenerarVentaView._to_decimal(total)

        if getattr(settings, "DEBUG", False):
            try:
                print("[PAGOS DEBUG] total=", total, "medio_simple=", medio_pago_simple,
                      "type(pagos_list)=", type(pagos_list), "pagos_list=", pagos_list)
            except Exception as _e:
                print("[PAGOS DEBUG] error imprimiendo debug:", _e)

        if total <= 0:
            return []

        # Caso 1: pagos mixtos en LISTA
        if isinstance(pagos_list, list) and len(pagos_list) > 0:
            acc = []
            for it in pagos_list:
                if not isinstance(it, dict):
                    continue

                medio = str(it.get("medio_pago", "")).strip().lower()
                if medio not in allowed:
                    continue

                monto = GenerarVentaView._to_decimal(it.get("monto", "0"))
                if monto <= 0:
                    continue

                acc.append({"medio_pago": medio, "monto": monto})

            if not acc:
                return []

            suma = sum((p["monto"] for p in acc), Decimal("0"))
            diff = suma - total

            if diff.copy_abs() > Decimal("0.01"):
                return []

            diff2 = total - suma
            if diff2 != 0:
                acc[-1]["monto"] = (acc[-1]["monto"] + diff2)

            return acc

        # Caso 2: pago simple
        medio = (medio_pago_simple or "").strip().lower()
        if medio in allowed:
            return [{"medio_pago": medio, "monto": total}]

        return []

    @staticmethod
    def _build_receipt_text(venta_data: Dict[str, Any], detalles: list[dict], total, pagos: list[dict]):
        """
        TEXTO (para POS Agent). Ajustado a 80mm (48 columnas aprox).
        Incluye: CAJERO + DEVUELTO (devoluciones) + CAMBIO (efectivo)
        """
        def money(n):
            try:
                q = Decimal(n)
            except Exception:
                q = Decimal("0")
            return f"${int(q):,}".replace(",", ".")

        WIDTH = 48

        def line(txt=""):
            t = str(txt or "")
            return t[:WIDTH]

        def lr(left, right):
            left = str(left or "")
            right = str(right or "")
            space = max(1, WIDTH - len(left) - len(right))
            return left + (" " * space) + right

        ahora = timezone.localtime()

        cajero_nombre = (venta_data or {}).get("cajero_nombre", "") or "—"
        refund_total  = Decimal((venta_data or {}).get("refund_total", 0) or 0)
        cambio        = Decimal((venta_data or {}).get("cambio", 0) or 0)

        head = [
            line("NOVA POS"),
            line("MERK2888"),
            line("NIT: 28.565.875 - 4"),
            line("FACTURA"),
            lr("Fecha:", ahora.strftime("%Y-%m-%d %H:%M")),
            lr("Sucursal:", (venta_data or {}).get("sucursal_nombre", "")),
            lr("Cajero:", cajero_nombre),
            "-" * WIDTH,
        ]

        body = []
        for d in detalles:
            nom = str(d.get("producto", ""))[:WIDTH]
            qty = d.get("cantidad", 1)
            pu  = d.get("precio_unitario", Decimal("0"))
            sub = d.get("subtotal", Decimal("0"))
            body.append(line(nom))
            body.append(lr(f" x{qty}  @ {money(pu)}", money(sub)))

        pay_lines = ["-" * WIDTH, line("PAGOS:")]
        for p in pagos or []:
            mp = (p.get("medio_pago") or "").upper().replace("_", " ")
            pay_lines.append(lr(mp[:18], money(p.get("monto", 0))))

        foot = ["-" * WIDTH]

        if refund_total > 0:
            foot.append(lr("DEVUELTO:", money(refund_total)))

        if cambio > 0:
            foot.append(lr("CAMBIO:", money(cambio)))

        foot += [
            lr("TOTAL:", money(total)),
            "",
            line("¡Gracias por su compra! :) "),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            line(""),
            ""
        ]
        return "\n".join(head + body + pay_lines + foot) + "\n\n\n\n\n\n\n\n\n"

    @staticmethod
    def _crear_venta(user, suc_inst, pp_inst, cliente_id, pagos, detalles, total, efectivo_recibido):
        try:
            ahora = timezone.localtime()

            empleado = getattr(user, "empleado", None)
            if empleado is None:
                return JsonResponse({'success': False, 'error': 'El usuario no tiene un empleado asociado.'})

            cajero_nombre = f"{getattr(empleado, 'nombre', '')} {getattr(empleado, 'apellido', '')}".strip()
            if not cajero_nombre:
                cajero_nombre = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "—").strip()

            # ✅ dinero devuelto (solo por items con qty < 0)
            refund_total = sum(
                (-(d.get("subtotal") or Decimal("0")))
                for d in (detalles or [])
                if int(d.get("cantidad") or 0) < 0
            )
            if refund_total < 0:
                refund_total = Decimal("0")

            with transaction.atomic():
                cliente_inst = Cliente.objects.filter(pk=cliente_id).first() if cliente_id else None
                mediopago = "mixto" if len(pagos) >= 2 else (pagos[0]["medio_pago"] if pagos else "sin_pago").lower()

                venta = Venta.objects.create(
                    fecha       = ahora.date(),
                    hora        = ahora.time(),
                    clienteid   = cliente_inst,
                    empleadoid  = empleado,
                    sucursalid  = suc_inst,
                    puntopagoid = pp_inst,
                    total       = total,
                    mediopago   = mediopago
                )

                prod_ids = [int(d["productoid"]) for d in detalles]
                qty_map  = {int(d["productoid"]): int(d["cantidad"]) for d in detalles}

                inv_qs = (Inventario.objects
                        .select_for_update()
                        .filter(sucursalid=suc_inst, productoid_id__in=prod_ids))

                inv_list = list(inv_qs)
                inv_map = {inv.productoid_id: inv for inv in inv_list}

                missing = [pid for pid in prod_ids if pid not in inv_map]
                if missing:
                    Inventario.objects.bulk_create(
                        [Inventario(sucursalid=suc_inst, productoid_id=pid, cantidad=0) for pid in missing],
                        batch_size=500
                    )
                    inv_list = list(
                        Inventario.objects.select_for_update()
                        .filter(sucursalid=suc_inst, productoid_id__in=prod_ids)
                    )
                    inv_map = {inv.productoid_id: inv for inv in inv_list}

                det_objs = [
                    DetalleVenta(
                        ventaid=venta,
                        productoid_id=int(d["productoid"]),
                        cantidad=int(d["cantidad"]),
                        preciounitario=d["precio_unitario"],
                    )
                    for d in detalles
                ]
                DetalleVenta.objects.bulk_create(det_objs, batch_size=500)

                # ✅ qty negativo => resta (-qty) => suma stock
                for pid, qty in qty_map.items():
                    inv = inv_map[pid]
                    inv.cantidad = (inv.cantidad or 0) - qty

                Inventario.objects.bulk_update(inv_list, ["cantidad"], batch_size=500)

                pagos_objs = []
                efectivo_monto = Decimal("0")
                for p in pagos:
                    mp = (p.get("medio_pago") or "").lower()
                    monto = GenerarVentaView._to_decimal(p.get("monto", 0))

                    pagos_objs.append(PagoVenta(
                        ventaid=venta,
                        medio_pago=mp,
                        monto=monto
                    ))

                    if mp == "efectivo":
                        efectivo_monto += monto

                if pagos_objs:
                    PagoVenta.objects.bulk_create(pagos_objs, batch_size=200)

                if efectivo_monto > 0:
                    PuntosPago.objects.filter(pk=pp_inst.pk).update(
                        dinerocaja=F("dinerocaja") + efectivo_monto
                    )

            # ✅ CAMBIO: SOLO cuando pago simple en efectivo y total > 0
            efectivo_recibido = GenerarVentaView._to_decimal(efectivo_recibido)
            cambio = Decimal("0")
            if total > 0 and pagos and len(pagos) == 1 and (pagos[0].get("medio_pago") or "").lower() == "efectivo":
                if efectivo_recibido > total:
                    cambio = efectivo_recibido - total

            receipt_text = GenerarVentaView._build_receipt_text(
                {
                    "sucursal_nombre": getattr(suc_inst, "nombre", str(suc_inst)),
                    "cajero_nombre": cajero_nombre,
                    "refund_total": refund_total,
                    "cambio": cambio,  # ✅ ahora sí llega
                },
                detalles, total, pagos
            )

            return JsonResponse({
                "success": True,
                "venta_id": venta.pk,
                "receipt_text": receipt_text,
            })

        except Exception as e:
            if getattr(settings, "DEBUG", False):
                return JsonResponse({'success': False, 'error': f'Error al crear la venta: {e!s}'})
            return JsonResponse({'success': False, 'error': 'Error al crear la venta.'})

# ============================================================================
# 2) AUTOCOMPLETE SOLO POR ID (FIX: startswith en IntegerField)
# ============================================================================
class ProductoIdAutocompleteView(LoginRequiredMixin, View):
    """
    Autocomplete independiente: SOLO por productoid (ID).
    - term debe ser dígitos.
    - Busca por prefijo (startswith) y prioriza exacto.
    - Filtra por sucursal y stock > 0.
    """
    per_page = 15

    def get(self, request, *args, **kwargs):
        term  = (request.GET.get("term", "") or "").strip()
        sid   = (request.GET.get("sucursal_id") or "").strip()
        limit = int(request.GET.get("limit") or self.per_page)

        if not sid.isdigit():
            return JsonResponse({"results": [], "has_more": False})
        sid = int(sid)

        digits = "".join(ch for ch in term if ch.isdigit())
        if not digits:
            return JsonResponse({"results": [], "has_more": False})

        qs = (
            Producto.objects
            .filter(inventario__sucursalid=sid, inventario__cantidad__gt=0)
            .distinct()
        )

        # ✅ productoid es int: para startswith usamos Cast a texto
        qs = qs.annotate(productoid_str=Cast("productoid", output_field=CharField()))
        qs = qs.filter(productoid_str__startswith=digits)

        try:
            exact_id = int(digits)
        except ValueError:
            exact_id = None

        qs = qs.order_by("productoid")[: max(5, min(50, limit))]

        prod_ids = list(qs.values_list("productoid", flat=True))

        inv_map = {
            inv["productoid_id"]: inv["cantidad"]
            for inv in Inventario.objects.filter(
                productoid_id__in=prod_ids, sucursalid=sid
            ).values("productoid_id", "cantidad")
        }

        rows = list(qs.values("productoid", "nombre", "precio", "codigo_de_barras"))

        if exact_id is not None:
            rows.sort(key=lambda r: (0 if r["productoid"] == exact_id else 1, r["productoid"]))

        results = [{
            "id": r["productoid"],
            "text": f'{r["productoid"]} — {r["nombre"]}',
            "nombre": r["nombre"],
            "precio": float(r["precio"] or 0),
            "stock": int(inv_map.get(r["productoid"], 0)),
            "barcode": r["codigo_de_barras"] or "",
        } for r in rows]

        has_more = len(results) >= limit
        return JsonResponse({"results": results, "has_more": has_more})


# ============================================================================
# 3) SNAPSHOT (permite stock 0 y negativos)
# ============================================================================
class ProductoSnapshotView(View):
    per_hard_limit = 15000

    def get(self, request, *args, **kwargs):
        sid = (request.GET.get("sucursal_id") or "").strip()
        if not sid.isdigit():
            return JsonResponse({"results": []})

        sid = int(sid)

        rows = (Inventario.objects
                .filter(sucursalid=sid)  # ✅ permite stock 0 y negativos
                .select_related("productoid")
                .values(
                    id=F("productoid_id"),
                    name=F("productoid__nombre"),
                    price=F("productoid__precio"),
                    stock=F("cantidad"),
                    barcode=F("productoid__codigo_de_barras"),
                )
                .order_by("productoid__nombre")[:self.per_hard_limit])

        results = [{
            "id": r["id"],
            "name": r["name"],
            "price": float(r["price"] or 0),
            "stock": int(r["stock"] or 0),
            "barcode": r["barcode"] or "",
        } for r in rows]

        return JsonResponse({"results": results})


# ============================================================================
# 4) TICKET 80mm x 60mm (LABEL) — ESC/POS
#    - 80mm ancho: 48 columnas (Font A)
#    - 60mm alto: rellenamos con feed por DOTS y cortamos
# ============================================================================
TICKET_WIDTH_CHARS = 48      # ✅ 80mm típico (Font A)
LABEL_HEIGHT_MM    = 60      # ✅ alto objetivo (label)
DOTS_PER_MM        = 8       # 203dpi ~ 8 dots/mm
LABEL_HEIGHT_DOTS  = int(LABEL_HEIGHT_MM * DOTS_PER_MM)  # 60mm => 480 dots aprox
LINE_HEIGHT_DOTS   = 24      # línea Font A aprox (default)

def _fmt_money(x: Decimal) -> str:
    """
    Formato COP sin decimales: $1.234.567
    """
    try:
        q = Decimal(x or "0")
    except Exception:
        q = Decimal("0")

    # Miles con punto (estilo Colombia)
    return f"${int(q):,}".replace(",", ".")

def _wrap(text: str, width: int = TICKET_WIDTH_CHARS) -> List[str]:
    """
    Wrap simple por palabras, recorta a width.
    """
    s = str(text or "").strip()
    if not s:
        return [""]

    words = s.split()
    lines: List[str] = []
    cur = ""

    for w in words:
        if not cur:
            cur = w
            continue

        if len(cur) + 1 + len(w) <= width:
            cur = cur + " " + w
        else:
            lines.append(cur[:width])
            cur = w

    if cur:
        lines.append(cur[:width])

    return lines or [""]

def _line() -> str:
    return "-" * TICKET_WIDTH_CHARS


def _build_ticket_lines(venta: Venta) -> list[str]:
    """
    Devuelve líneas ya formateadas a 48 columnas (80mm).
    Incluye: CAJERO + DEVUELTO (si aplica)
    """
    out: list[str] = []

    # --- helpers locales ---
    def _to_dec(x):
        try:
            return Decimal(x)
        except Exception:
            return Decimal("0")

    out += _wrap("NOVA ADVANCE")
    out += _wrap("NIT: 900.000.000-1")
    out.append(_line())
    out += _wrap(f"Factura #{venta.pk}")
    out += _wrap(f"Fecha: {venta.fecha}  {venta.hora.strftime('%H:%M')}")
    out += _wrap(f"Sucursal: {venta.sucursalid.nombre}")

    # ✅ CAJERO
    emp = getattr(venta, "empleadoid", None)
    cajero = f"{getattr(emp, 'nombre', '')} {getattr(emp, 'apellido', '')}".strip() if emp else ""
    out += _wrap(f"Cajero: {cajero or '—'}")

    if getattr(venta, "clienteid", None):
        out += _wrap(f"Cliente: {venta.clienteid.nombre}")
    out.append(_line())

    detalles = (
        DetalleVenta.objects
        .filter(ventaid=venta)
        .select_related("productoid")
    )

    # ✅ calcular devuelto (sumatoria de qty<0 en positivo)
    refund_total = Decimal("0")

    for det in detalles:
        nombre = (det.productoid.nombre or "").strip() or "(Producto)"
        pu     = _to_dec(det.preciounitario or 0)
        qty    = int(det.cantidad or 0)
        subtotal = pu * Decimal(qty)

        if qty < 0:
            refund_total += (-subtotal)  # positivo

        lines = _wrap(nombre)
        out.append(lines[0])

        left  = f"{qty} x {_fmt_money(pu)}"
        right = _fmt_money(subtotal)

        out.append(f"{left:<{TICKET_WIDTH_CHARS-len(right)}}{right}")

        for extra in lines[1:]:
            out.append(extra)

    out.append(_line())

    if refund_total > 0:
        out.append(f"{'DEVUELTO':<{TICKET_WIDTH_CHARS-10}}{_fmt_money(refund_total):>10}")

    out.append(f"{'TOTAL':<{TICKET_WIDTH_CHARS-10}}{_fmt_money(venta.total):>10}")
    out.append(_line())
    out += _wrap(f"Medio de pago: {str(venta.mediopago or '').upper()}")
    out.append("")
    out += _wrap("¡Gracias por su compra!")
    out.append("")
    return out

def _build_ticket_text(venta: Venta) -> str:
    """
    Texto (para POS Agent / JSON).
    """
    lines = _build_ticket_lines(venta)
    return "\n".join(lines) + "\n\n\n"

def _escpos_feed_dots(dots: int) -> bytes:
    """
    GS J n  (feed en dots). n es 0..255, por eso lo partimos.
    """
    if dots <= 0:
        return b""
    out = b""
    remaining = int(dots)
    while remaining > 0:
        n = min(255, remaining)
        out += b"\x1D\x4A" + bytes([n])  # GS J n
        remaining -= n
    return out

def _build_label_80x60_payload_from_lines(lines: list[str], open_drawer=True, cut=True) -> bytes:
    """
    ✅ Formato “80mm x 60mm” como label:
    - imprime líneas normales
    - rellena con feed hasta aprox 60mm de alto
    - corta
    - (opcional) abre caja
    """
    init      = b"\x1B\x40"          # ESC @
    codepage  = b"\x1B\x74\x00"      # ESC t 0  (CP437 en muchos modelos)
    drawer    = b"\x1B\x70\x00\x32\x32"  # ESC p m t1 t2
    full_cut  = b"\x1D\x56\x41\x00"  # GS V A 0  (full cut; si no soporta, no pasa nada)

    # cuerpo
    body = b"".join((str(ln).encode("cp437", errors="ignore") + b"\n") for ln in (lines or []))
    body += b"\n\n\n"  # margen final

    # altura estimada consumida por líneas
    printed_lines = max(0, len(lines) + 3)
    used_dots = printed_lines * LINE_HEIGHT_DOTS
    remaining = max(0, LABEL_HEIGHT_DOTS - used_dots)

    payload = init + codepage + body
    payload += _escpos_feed_dots(remaining)

    if open_drawer:
        payload += drawer
    if cut:
        payload += full_cut

    return payload

def _build_ticket_payload_80x60(venta: Venta) -> bytes:
    """
    ✅ ESTE es el que usarás para imprimir “80mm x 60mm”.
    """
    lines = _build_ticket_lines(venta)
    return _build_label_80x60_payload_from_lines(lines, open_drawer=True, cut=True)

def _just_open_drawer() -> bytes:
    # init + pulso
    return b"\x1B\x40" + b"\n" + b"\x1B\x70\x00\x32\x32"

def _send_to_printer(payload: bytes) -> tuple[bool, str]:
    """
    Intenta enviar a /dev/usb/lp0; si no existe, intenta CUPS (lp raw).
    Devuelve (ok, error_message).
    """
    device = os.environ.get("PRINTER_DEVICE", "/dev/usb/lp0")

    # 1) /dev/usb/lp0
    try:
        if os.path.exists(device):
            with open(device, "wb") as f:
                f.write(payload)
            return True, ""
    except Exception as e:
        last_err = f"lp0 error: {e}"
    else:
        last_err = "lp0 no encontrado"

    # 2) CUPS RAW
    try:
        printer = os.environ.get("PRINTER", "")
        # ✅ “80mm x 60mm” como media custom
        media = os.environ.get("CUPS_MEDIA", "Custom.80x60mm")
        cmd = ["lp", "-o", f"media={media}", "-o", "raw"]
        if printer:
            cmd.extend(["-d", printer])
        subprocess.run(cmd, input=payload, check=True)
        return True, ""
    except Exception as e:
        return False, f"{last_err} ; CUPS error: {e}"


# ============================================================================
# 5) VISTAS DE TICKET / IMPRESIÓN
# ============================================================================
@method_decorator(require_POST, name="dispatch")
class TicketTextoView(LoginRequiredMixin, View):
    """
    POST: {venta_id} → devuelve JSON con 'receipt_text' (para POS Agent).
    """
    def post(self, request, *args, **kwargs):
        venta_id = request.POST.get("venta_id")
        if not venta_id or not str(venta_id).isdigit():
            return HttpResponseBadRequest("venta_id inválido")
        venta = get_object_or_404(Venta, pk=int(venta_id))
        text = _build_ticket_text(venta)
        return JsonResponse({"success": True, "receipt_text": text})


@method_decorator(require_POST, name="dispatch")
class ImprimirFacturaView(LoginRequiredMixin, View):
    """
    POST: {venta_id} → imprime en formato ✅ 80mm x 60mm y abre la caja.
    """
    def post(self, request, *args, **kwargs):
        venta_id = request.POST.get("venta_id")
        if not venta_id or not str(venta_id).isdigit():
            return HttpResponseBadRequest("venta_id inválido")

        venta = get_object_or_404(Venta, pk=int(venta_id))

        # ✅ CAMBIO CLAVE: ahora imprimimos como “80mm x 60mm”
        payload = _build_ticket_payload_80x60(venta)

        ok, err = _send_to_printer(payload)
        if ok:
            return JsonResponse({"success": True})
        return JsonResponse({"success": False, "error": err}, status=500)


@method_decorator(require_POST, name="dispatch")
class AbrirCajaView(LoginRequiredMixin, View):
    """
    POST: abre la caja sin imprimir ticket.
    """
    def post(self, request, *args, **kwargs):
        payload = _just_open_drawer()
        ok, err = _send_to_printer(payload)
        if ok:
            return JsonResponse({"success": True})
        return JsonResponse({"success": False, "error": err}, status=500)

class SucursalAutocompleteView(PaginatedAutocompleteMixin):
    model      = Sucursal
    text_field = "nombre"
    id_field   = "sucursalid"

    @staticmethod
    def extra_filter(qs, request):
        # IDs de sucursales con stock positivo
        inv_ids = Inventario.objects \
                            .filter(cantidad__gt=0) \
                            .values_list('sucursalid', flat=True) \
                            .distinct()
        # IDs de sucursales con puntos de pago
        pp_ids  = PuntosPago.objects \
                            .values_list('sucursalid', flat=True) \
                            .distinct()
        # Primero filtras por inventario, luego por puntos de pago
        return qs.filter(pk__in=inv_ids) \
                 .filter(pk__in=pp_ids)


class PuntoPagoAutocompleteView(PaginatedAutocompleteMixin):
    """
    Lista puntos de pago para la sucursal seleccionada.
    """
    model      = PuntosPago
    text_field = "nombre"
    id_field   = "puntopagoid"

    @staticmethod
    def extra_filter(qs, request):
        sid = request.GET.get("sucursal_id")
        if sid:
            return qs.filter(sucursalid__sucursalid=sid)
        return qs.none()  # si no hay sucursal, no listar

class ProductoAutocompleteView(PaginatedAutocompleteMixin):
    model      = Producto
    text_field = "nombre"
    id_field   = "productoid"
    per_page   = 15

    def get(self, request, *args, **kwargs):
        term  = (request.GET.get("term","") or "").strip()
        sid   = (request.GET.get("sucursal_id") or "").strip()
        limit = int(request.GET.get("limit") or self.per_page)

        if not sid.isdigit():
            return JsonResponse({"results": [], "has_more": False})
        sid = int(sid)

        # ✅ base desde inventario: evita distinct() en Producto
        qs = (Inventario.objects
              .filter(sucursalid=sid, cantidad__gt=0)
              .select_related("productoid"))

        if term:
            qs = qs.filter(productoid__nombre__icontains=term)

        # trae limit+1 para saber si hay más
        rows = list(qs.values(
            "productoid_id",
            "cantidad",
            "productoid__nombre",
            "productoid__precio",
        ).order_by("productoid__nombre")[:limit+1])

        has_more = len(rows) > limit
        rows = rows[:limit]

        results = [{
            "id": r["productoid_id"],
            "text": r["productoid__nombre"],
            "precio": float(r["productoid__precio"] or 0),
            "stock": int(r["cantidad"] or 0),
        } for r in rows]

        return JsonResponse({"results": results, "has_more": has_more})

class ClienteAutocompleteView(PaginatedAutocompleteMixin):
    """
    Cliente por nombre / apellido / documento.
    Usamos un override para poder buscar en varios campos a la vez.
    """
    model = Cliente
    id_field = "clienteid"

    def get(self, request, *args, **kwargs):
        term   = request.GET.get("term", "").strip()
        page   = max(int(request.GET.get("page", 1)), 1)
        start, end = (page-1)*self.per_page, page*self.per_page

        qs = Cliente.objects.all()
        if term:
            qs = qs.filter(
                Q(nombre__icontains=term)  |
                Q(apellido__icontains=term)|
                Q(numerodocumento__icontains=term)
            )

        total   = qs.count()
        results = [
            {
              "id"  : c.clienteid,
              "text": f"{c.nombre} {c.apellido} ({c.numerodocumento})"
            }
            for c in qs.order_by("nombre")[start:end]
        ]
        return JsonResponse({"results": results, "has_more": end < total})



# ───────────────────────────────────────────────────────────────────────────
# ··· Vistas AJAX utilitarias ···
# ───────────────────────────────────────────────────────────────────────────
class VerificarProductoView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        producto_id = request.POST.get("producto_id")
        sucursal_id = request.POST.get("sucursal_id")

        try:
            cantidad = int(request.POST.get("cantidad") or 0)
        except ValueError:
            cantidad = 0

        if not (producto_id and str(producto_id).isdigit() and sucursal_id and str(sucursal_id).isdigit()):
            return JsonResponse({"exists": False})

        pid = int(producto_id)
        sid = int(sucursal_id)

        # ✅ 1 sola query: inventario + producto
        row = (Inventario.objects
               .filter(sucursalid=sid, productoid_id=pid)
               .select_related("productoid")
               .values(
                   "cantidad",
                   "productoid_id",
                   "productoid__nombre",
                   "productoid__precio",
                   "productoid__codigo_de_barras",
               )
               .first())

        if not row:
            return JsonResponse({"exists": False})

        disponible = int(row["cantidad"] or 0)
        if disponible < cantidad:
            return JsonResponse({"exists": True, "cantidad_disponible": disponible})

        precio = row["productoid__precio"] or 0
        subtotal = precio * cantidad

        return JsonResponse({
            "exists": True,
            "precio_unitario": precio,
            "precio_unitario_fmt": f"${precio:,.2f}",
            "subtotal": subtotal,
            "subtotal_fmt": f"${subtotal:,.2f}",
            "cantidad_disponible": disponible,
            "nombre": row["productoid__nombre"],
            "codigo_de_barras": row["productoid__codigo_de_barras"] or "",
        })


class BuscarProductoPorCodigoView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        codigo = (request.GET.get("codigo_de_barras", "") or "").strip()
        sucursal_id = request.GET.get("sucursal_id")

        if not codigo or not (sucursal_id and str(sucursal_id).isdigit()):
            return JsonResponse({"exists": False})

        sid = int(sucursal_id)

        row = (Inventario.objects
               .filter(sucursalid=sid, productoid__codigo_de_barras=codigo)
               .select_related("productoid")
               .values(
                    "productoid_id",
                    "cantidad",
                    "productoid__nombre",
                    "productoid__codigo_de_barras",
                    "productoid__precio",
               )
               .first())

        if not row:
            return JsonResponse({"exists": False})

        return JsonResponse({
            "exists": True,
            "producto": {
                "id": row["productoid_id"],
                "nombre": row["productoid__nombre"],
                "codigo_de_barras": row["productoid__codigo_de_barras"] or "",
                "precio": float(row["productoid__precio"] or 0),
                "stock": int(row["cantidad"] or 0),
            }
        })

class ProductoCodigoAutocompleteView(LoginRequiredMixin, View):
    per_page = 15
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term","") or "").strip()
        sid  = (request.GET.get("sucursal_id") or "").strip()
        if not sid.isdigit():
            return JsonResponse({"results": [], "has_more": False})

        qs = Producto.objects.filter(
            inventario__sucursalid=sid, inventario__cantidad__gt=0
        ).distinct()
        if term:
            filt = Q(nombre__icontains=term)
            if term.isdigit():
                try:
                    filt |= Q(productoid=int(term))
                except ValueError:
                    pass
            qs = qs.filter(filt)

        total = qs.count()
        qs = qs.order_by("nombre")[:self.per_page]

        inv_map = {inv.productoid_id: inv.cantidad
                   for inv in Inventario.objects.filter(
                        productoid__in=qs, sucursalid=int(sid)
                   )}

        results = [{
            "id": p.productoid,
            "text": p.nombre,
            "precio": float(p.precio or 0),
            "stock": int(inv_map.get(p.productoid, 0)),
        } for p in qs]

        return JsonResponse({"results": results, "has_more": total > self.per_page})

class ProductoBarrasAutocompleteView(LoginRequiredMixin, View):
    per_page = 15
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term","") or "").strip()
        sid  = (request.GET.get("sucursal_id") or "").strip()
        if not sid.isdigit():
            return JsonResponse({"results": [], "has_more": False})

        qs = Producto.objects.filter(
            inventario__sucursalid=sid, inventario__cantidad__gt=0
        ).distinct()
        if term:
            qs = qs.filter(Q(codigo_de_barras__icontains=term) | Q(nombre__icontains=term))

        total = qs.count()
        qs = qs.order_by("nombre")[:self.per_page]

        inv_map = {inv.productoid_id: inv.cantidad
                   for inv in Inventario.objects.filter(
                        productoid__in=qs, sucursalid=int(sid)
                   )}

        results = [{
            "id": p.productoid,
            "text": p.nombre,
            "barcode": p.codigo_de_barras or "",
            "precio": float(p.precio or 0),
            "stock": int(inv_map.get(p.productoid, 0)),
        } for p in qs]

        return JsonResponse({"results": results, "has_more": total > self.per_page})


class VentaListView( LoginRequiredMixin, TemplateView):
    """
    Todas las ventas con la MÁS RECIENTE primero.
    """
    model               = Venta
    template_name       = "visualizar_ventas.html"
    context_object_name = "ventas"
    paginate_by         = None  # sin paginación

    def get_queryset(self):
      return (
          Venta.objects
          .select_related("clienteid", "empleadoid", "sucursalid", "puntopagoid")
          # Opción 1 (si tu PK real es ventaid):
          .order_by("-ventaid")
          # Opción 2 (si usas PK estándar id): .order_by("-pk")
      )

    # (opcional/redundante) deja explícito que no hay paginación
    def get_paginate_by(self, queryset):
        return None

class VentaDataTableView(LoginRequiredMixin, View):
    """
    Endpoint server-side ultra-rápido para DataTables en visualizar_ventas.
    Devuelve solo las ventas necesarias para la página actual.
    """

    def get(self, request, *args, **kwargs):
        # ---------- parámetros básicos DataTables ----------
        draw   = int(request.GET.get("draw", "1"))
        start  = int(request.GET.get("start", "0"))
        length = int(request.GET.get("length", "25"))
        search_value = request.GET.get("search[value]", "").strip()

        # ---------- base queryset ----------
        base_qs = Venta.objects.select_related(
            "clienteid", "empleadoid", "sucursalid", "puntopagoid"
        )

        records_total = base_qs.count()
        qs = base_qs

        # ---------- filtro (buscador) ----------
        if search_value:
            tokens = search_value.split()
            for token in tokens:
                qs = qs.filter(
                    Q(ventaid__icontains=token) |
                    Q(clienteid__nombre__icontains=token) |
                    Q(clienteid__apellido__icontains=token) |
                    Q(empleadoid__nombre__icontains=token) |
                    Q(empleadoid__apellido__icontains=token) |
                    Q(sucursalid__nombre__icontains=token) |
                    Q(puntopagoid__nombre__icontains=token) |
                    Q(mediopago__icontains=token)
                )

        records_filtered = qs.count()

        # ---------- ordenamiento ----------
        order_column_index = request.GET.get("order[0][column]", "0")
        order_dir          = request.GET.get("order[0][dir]", "desc")  # más recientes

        columns = [
            "ventaid",                   # 0
            "fecha",                     # 1
            "hora",                      # 2
            "clienteid__nombre",         # 3 (solo nombre, para ordenar)
            "empleadoid__nombre",        # 4
            "sucursalid__nombre",        # 5
            "puntopagoid__nombre",       # 6
            "total",                     # 7
            "mediopago",                 # 8
        ]

        try:
            idx = int(order_column_index)
            order_column = columns[idx]
        except (ValueError, IndexError):
            order_column = "ventaid"

        if order_dir == "desc":
            order_column = "-" + order_column

        # ---------- slice + values (solo columnas que usamos) ----------
        qs_page = (
            qs.order_by(order_column)
              .values(
                  "ventaid",
                  "fecha",
                  "hora",
                  "total",
                  "mediopago",
                  "clienteid__nombre",
                  "clienteid__apellido",
                  "empleadoid__nombre",
                  "empleadoid__apellido",
                  "sucursalid__nombre",
                  "puntopagoid__nombre",
              )[start:start + length]
        )

        # ---------- construir datos para DataTables ----------
        data = []
        for v in qs_page:
            cliente = "—"
            if v["clienteid__nombre"]:
                apellido = v["clienteid__apellido"] or ""
                cliente = f"{v['clienteid__nombre']} {apellido}".strip()

            empleado = f"{v['empleadoid__nombre']} {(v['empleadoid__apellido'] or '')}".strip()
            sucursal = v["sucursalid__nombre"] or "—"
            punto    = v["puntopagoid__nombre"] or "—"
            medio    = (v["mediopago"] or "").title()

            fecha_str = v["fecha"].strftime("%d/%m/%Y") if v["fecha"] else ""
            hora_str  = v["hora"].strftime("%H:%M") if v["hora"] else ""

            data.append({
                "ventaid"   : v["ventaid"],
                "fecha"     : fecha_str,
                "hora"      : hora_str,
                "cliente"   : cliente,
                "empleado"  : empleado,
                "sucursal"  : sucursal,
                "puntopago" : punto,
                "total"     : f"${v['total']:.2f}",
                "mediopago" : medio,
            })

        return JsonResponse({
            "draw"            : draw,
            "recordsTotal"    : records_total,
            "recordsFiltered" : records_filtered,
            "data"            : data,
        })



Q2 = Decimal("0.01")

def _to_q2(x: Decimal) -> Decimal:
    return (x or Decimal("0.00")).quantize(Q2)

class VentaDetailView(LoginRequiredMixin, DenyRolesMixin , View):
    deny_roles = ["Cajero", "Auxiliar"]
    template_name = "ver_venta.html"

    # -------------------------
    # Helpers
    # -------------------------
    def _venta_es_mixta(self, venta) -> bool:
        return (venta.mediopago or "").strip().lower() == "mixto"

    def _build_pagos_initial(self, venta):
        """
        Precarga pagos actuales desde venta_pagos (PagoVenta.monto) agrupado por metodo.
        """
        pagos_bd = {}
        if self._venta_es_mixta(venta):
            rows = (
                PagoVenta.objects
                .filter(ventaid=venta)
                .values("metodo")                # ✅
                .annotate(total=Sum("monto"))
            )
            pagos_bd = {
                (r["metodo"] or "").strip().lower(): (r["total"] or Decimal("0.00"))
                for r in rows
            }

        initial = []
        for key, _label in MEDIOS_PAGO:
            initial.append({
                "medio_pago": key,
                "monto": (pagos_bd.get(key, Decimal("0.00"))).quantize(Q2)
            })
        return initial

    def _build_reintegro_initial(self, venta):
        return [{"medio_pago": key, "monto": Decimal("0.00")} for key, _label in MEDIOS_PAGO]

    def _calcular_total_reintegro(self, detalles, dev_formset) -> Decimal:
        total = Decimal("0.00")
        det_map = {d.pk: d for d in detalles}

        for row in dev_formset.cleaned_data:
            cant = int(row.get("devolver") or 0)
            if cant <= 0:
                continue
            det = det_map.get(row["detalle_id"])
            if not det:
                continue
            total += (Decimal(cant) * (det.preciounitario or Decimal("0.00")))

        return total.quantize(Q2)

    def _sum_formset_montos(self, formset) -> Decimal:
        s = Decimal("0.00")
        for row in (formset.cleaned_data or []):
            s += (row.get("monto") or Decimal("0.00"))
        return s.quantize(Q2)

    # -------------------------
    # Pagos mixtos: validar/guardar
    # -------------------------
    def _validar_pagos_mixtos(self, venta, pagos_formset):
        if not pagos_formset.is_valid():
            return False, "Montos de pago inválidos."

        total = (venta.total or Decimal("0.00")).quantize(Q2)
        suma = self._sum_formset_montos(pagos_formset)

        for row in pagos_formset.cleaned_data:
            monto = (row.get("monto") or Decimal("0.00")).quantize(Q2)
            if monto < 0:
                return False, "No puedes poner montos negativos."

        if suma != total:
            return False, f"La suma de pagos ({suma}) debe ser igual al total ({total})."

        return True, None

    def _guardar_pagos_mixtos(self, venta, pagos_formset):
        PagoVenta.objects.filter(ventaid=venta).delete()

        nuevos = []
        for row in pagos_formset.cleaned_data:
            medio = (row.get("medio_pago") or "").strip().lower()
            monto = (row.get("monto") or Decimal("0.00")).quantize(Q2)
            if monto > 0:
                nuevos.append(PagoVenta(ventaid=venta, metodo=medio, monto=monto))  # ✅ metodo

        if nuevos:
            PagoVenta.objects.bulk_create(nuevos)

    def _guardar_pago_unico(self, venta):
        """
        Si la venta NO es mixta, guardamos 1 solo pago en venta_pagos
        (y borramos cualquier registro previo).
        """
        PagoVenta.objects.filter(ventaid=venta).delete()

        medio = (venta.mediopago or "").strip().lower()
        total = _to_q2(venta.total)

        if total > 0 and medio:
            PagoVenta.objects.create(
                ventaid=venta,
                medio_pago=medio,   # ✅ NO "metodo"
                monto=total
            )

    # -------------------------
    # Reintegro mixto: validar contra lo pagado actual
    # -------------------------
    def _pagado_actual_por_medio_locked(self, venta) -> dict:
        out = {k: Decimal("0.00") for k, _ in MEDIOS_PAGO}
        qs = PagoVenta.objects.select_for_update().filter(ventaid=venta)

        for p in qs:
            medio = (p.metodo or "").strip().lower()  # ✅
            if medio in out:
                out[medio] += (p.monto or Decimal("0.00"))

        for k in out:
            out[k] = out[k].quantize(Q2)
        return out

    def _validar_reintegro_mixto(self, venta, reintegro_formset, total_reintegro: Decimal):
        if total_reintegro <= 0:
            return True, None, {}

        if not reintegro_formset.is_valid():
            return False, "Montos de reintegro inválidos.", {}

        disponibles = self._pagado_actual_por_medio_locked(venta)

        suma = Decimal("0.00")
        reintegro_map = {}

        for row in reintegro_formset.cleaned_data:
            metodo = (row.get("medio_pago") or "").strip().lower()
            monto = (row.get("monto") or Decimal("0.00")).quantize(Q2)

            if monto < 0:
                return False, "No puedes poner reintegros negativos.", {}

            if monto > 0:
                if metodo not in disponibles:
                    return False, f"Método inválido: {metodo}", {}
                if monto > disponibles[metodo]:
                    return False, f"El reintegro en {metodo} supera lo disponible ({disponibles[metodo]}).", {}
                reintegro_map[metodo] = monto

            suma += monto

        suma = suma.quantize(Q2)
        if suma != total_reintegro:
            return False, f"La suma del reintegro ({suma}) debe ser igual al total a reintegrar ({total_reintegro}).", {}

        return True, None, reintegro_map

    def _restar_reintegro_de_pagos(self, venta, reintegro_map: dict):
        """
        Resta en venta_pagos EXACTAMENTE lo que el usuario puso.
        """
        for metodo, monto_restar in reintegro_map.items():
            restante = (monto_restar or Decimal("0.00")).quantize(Q2)
            if restante <= 0:
                continue

            qs = (
                PagoVenta.objects
                .select_for_update()
                .filter(ventaid=venta, metodo=metodo)  # ✅
                .order_by("id")                        # ✅
            )

            for p in qs:
                if restante <= 0:
                    break
                actual = (p.monto or Decimal("0.00")).quantize(Q2)

                if actual <= restante:
                    restante = (restante - actual).quantize(Q2)
                    p.delete()
                else:
                    p.monto = (actual - restante).quantize(Q2)
                    p.save(update_fields=["monto"])
                    restante = Decimal("0.00")

            if restante > 0:
                raise ValueError(f"No hay suficiente saldo en {metodo} para restar {monto_restar}.")

    # -------------------------
    # Cambio de medio (no-mixto -> no-mixto) mueve dinero en el turno
    # -------------------------
    def _mover_turno_por_cambio_medio(self, venta, metodo_old: str, metodo_new: str, monto: Decimal):
        metodo_old = (metodo_old or "").strip().lower()
        metodo_new = (metodo_new or "").strip().lower()
        monto = (monto or Decimal("0.00")).quantize(Q2)

        if monto <= 0 or not metodo_old or not metodo_new or metodo_old == metodo_new:
            return

        turno = (
            TurnoCaja.objects
            .select_for_update()
            .filter(puntopago_id=venta.puntopagoid_id, estado__in=["ABIERTO", "CIERRE"])
            .order_by("-inicio")
            .first()
        )
        if not turno:
            return

        # mueve esperado por medio
        CambioDevolucion._upsert_turno_medio_delta(turno, metodo_old, -monto)
        CambioDevolucion._upsert_turno_medio_delta(turno, metodo_new, +monto)

        # mueve ventas_efectivo / ventas_no_efectivo
        if metodo_old == "efectivo":
            TurnoCaja.objects.filter(pk=turno.pk).update(ventas_efectivo=F("ventas_efectivo") - monto)
        else:
            TurnoCaja.objects.filter(pk=turno.pk).update(ventas_no_efectivo=F("ventas_no_efectivo") - monto)

        if metodo_new == "efectivo":
            TurnoCaja.objects.filter(pk=turno.pk).update(ventas_efectivo=F("ventas_efectivo") + monto)
        else:
            TurnoCaja.objects.filter(pk=turno.pk).update(ventas_no_efectivo=F("ventas_no_efectivo") + monto)

    # -------------------------
    # GET
    # -------------------------
    def get(self, request, venta_id):
        venta = get_object_or_404(
            Venta.objects.select_related("clienteid", "empleadoid", "sucursalid", "puntopagoid"),
            pk=venta_id
        )

        detalles = (
            DetalleVenta.objects
            .filter(ventaid=venta)
            .select_related("productoid")
            .annotate(
                subtotal=ExpressionWrapper(
                    F("cantidad") * F("preciounitario"),
                    output_field=DecimalField(max_digits=10, decimal_places=2)
                )
            )
        )

        dev_formset = DevolucionFormSet(
            initial=[{"detalle_id": d.pk, "devolver": 0} for d in detalles],
            prefix="dev"
        )

        pagos_formset = PagoMixtoFormSet(
            initial=self._build_pagos_initial(venta),
            prefix="pagos"
        )

        reintegro_formset = ReintegroMixtoFormSet(
            initial=self._build_reintegro_initial(venta),
            prefix="reint"
        )

        return render(request, self.template_name, {
            "venta": venta,
            "filas": zip(detalles, dev_formset.forms),
            "dev_formset": dev_formset,
            "pagos_formset": pagos_formset,
            "reintegro_formset": reintegro_formset,
            "es_mixto": self._venta_es_mixta(venta),
            "medios_pago": MEDIOS_PAGO,
        })

    # -------------------------
    # POST
    # -------------------------
    @transaction.atomic
    def post(self, request, venta_id):
        venta = Venta.objects.select_for_update().get(pk=venta_id)
        detalles = list(DetalleVenta.objects.filter(ventaid=venta))

        accion = (request.POST.get("accion") or "").strip()
        nuevo_mediopago = (request.POST.get("mediopago") or "").strip().lower()

        dev_formset = DevolucionFormSet(request.POST, prefix="dev")
        pagos_formset = PagoMixtoFormSet(request.POST, prefix="pagos")
        reintegro_formset = ReintegroMixtoFormSet(request.POST, prefix="reint")

        metodo_old = (venta.mediopago or "").strip().lower()

        # 0) Cambio de medio (si cambió)
        if nuevo_mediopago and nuevo_mediopago != metodo_old:
            venta.mediopago = nuevo_mediopago
            venta.save(update_fields=["mediopago"])

            # ✅ si ambos no-mixto: mover dinero en turno por el total actual
            if metodo_old != "mixto" and nuevo_mediopago != "mixto":
                self._mover_turno_por_cambio_medio(venta, metodo_old, nuevo_mediopago, venta.total)

            messages.success(request, "✅ Medio de pago actualizado.")

        # 1) Guardar pagos
        if self._venta_es_mixta(venta):
            ok, err = self._validar_pagos_mixtos(venta, pagos_formset)
            if not ok:
                messages.error(request, f"⚠️ {err}")
                return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))
            self._guardar_pagos_mixtos(venta, pagos_formset)
        else:
            # ✅ mantener pago único
            self._guardar_pago_unico(venta)

        if accion == "volver_lista":
            return redirect(reverse_lazy("visualizar_ventas"))

        # 2) Validar devoluciones
        if not dev_formset.is_valid():
            messages.error(request, "⚠️ Revisa las cantidades a devolver.")
            return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

        det_map = {d.pk: d for d in detalles}
        devoluciones = []
        for row in dev_formset.cleaned_data:
            cant = int(row.get("devolver") or 0)
            if cant > 0:
                det = det_map.get(row["detalle_id"])
                if det:
                    devoluciones.append({"detalle": det, "cantidad": cant})

        if not devoluciones:
            # Solo guardó medio/pagos
            return redirect(reverse_lazy("visualizar_ventas"))

        total_reintegro = self._calcular_total_reintegro(detalles, dev_formset)

        # 3) Devolver dinero + stock
        if self._venta_es_mixta(venta):
            ok, err, reintegro_map = self._validar_reintegro_mixto(venta, reintegro_formset, total_reintegro)
            if not ok:
                messages.error(request, f"⚠️ {err}")
                return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

            # A) devolución completa (inventario + turno + total venta)
            CambioDevolucion.registrar_devolucion(venta, devoluciones, reintegro_map=reintegro_map)

            # B) restar de venta_pagos según reintegro_map
            self._restar_reintegro_de_pagos(venta, reintegro_map)

        else:
            # no mixto
            CambioDevolucion.registrar_devolucion(venta, devoluciones)
            # sincronizar pago único con nuevo total
            venta.refresh_from_db()
            self._guardar_pago_unico(venta)

        messages.success(request, "✅ Devolución registrada correctamente.")
        return redirect(reverse_lazy("visualizar_ventas"))

class CambiosListView(LoginRequiredMixin, ListView):
    model = CambioDevolucion
    template_name = "visualizar_cambios.html"
    context_object_name = "cambios"
    paginate_by = 50
    ordering = ["-fecha", "-cambioid"]

    def get_queryset(self):
        return (
            CambioDevolucion.objects
            .select_related("venta", "productoid", "detalle")
            .order_by("-fecha", "-cambioid")
        )






@method_decorator(transaction.atomic, name="dispatch")
class PedidoProveedorCreateAJAXView(LoginRequiredMixin, View):
    """
    • GET  → renderiza form + selects iniciales
    • POST → valida, guarda y responde JSON {success, message|errors}
    """
    template_name = "agregar_pedido.html"
    form_class    = PedidoProveedorForm
    success_msg   = "Pedido guardado exitosamente."

    def get(self, request):
        form        = self.form_class()
        # opcionales: lista completa de proveedores y sucursales
        proveedores = Proveedor.objects.all().order_by("nombre")
        sucursales  = Sucursal.objects.all().order_by("nombre")
        return render(request, self.template_name, {
            "form": form,
            "proveedores": proveedores,
            "sucursales": sucursales,
        })

    def post(self, request):
        form = self.form_class(request.POST)
        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors": json.dumps(form.errors.get_json_data())
            })
        # datos base
        prov   = form.cleaned_data["proveedor"]
        suc    = form.cleaned_data["sucursal"]
        fecha  = form.cleaned_data.get("fechaestimadaentrega")
        comen  = form.cleaned_data.get("comentario", "")
        detalles = json.loads(form.cleaned_data["detalles"])

        # 1) al menos uno
        if not detalles:
            return JsonResponse({
                "success": False,
                "errors": json.dumps({
                    "detalles":[{"message":"Debe agregar al menos un producto."}]
                })
            })

        # 2) validar que el proveedor venda cada producto
        invalidos = []
        for d in detalles:
            pid = d["productoid"]
            if not PreciosProveedor.objects.filter(
                productoid_id=pid, proveedorid=prov
            ).exists():
                nombre = Producto.objects.filter(pk=pid).first()
                invalidos.append(nombre.nombre if nombre else f"ID {pid}")
        if invalidos:
            return JsonResponse({
                "success": False,
                "message": (
                  "El proveedor NO vende: "
                  + ", ".join(invalidos)
                  + ". Revise el pedido."
                )
            })

        # 3) calcular total
        total = Decimal("0.00")
        for d in detalles:
            c = Decimal(str(d["cantidad"]))
            p = Decimal(str(d["precio_unitario"]))
            total += c * p

        # 4) guardar
        try:
            pedido = PedidoProveedor.objects.create(
                proveedorid=prov,
                sucursalid=suc,
                fechaestimadaentrega=fecha,
                costototal=total,
                comentario=comen,
                estado="En espera"
            )
            for d in detalles:
                DetallePedidoProveedor.objects.create(
                    pedidoid=pedido,
                    productoid_id=d["productoid"],
                    cantidad=d["cantidad"],
                    preciounitario=d["precio_unitario"]
                )
        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": f"Error al guardar: {e}"
            })

        return JsonResponse({
            "success": True,
            "message": self.success_msg
        })


class ProductoPedidoAutocomplete(PaginatedAutocompleteMixin):
    """
    • Filtra por proveedor (GET ?proveedor_id=)
    • Excluye los IDs ya listados (?excluded=1,2,3)
    • Devuelve   id, text, precio   por página
    """
    model      = Producto
    text_field = "nombre"
    id_field   = "productoid"
    per_page   = 10           # si tu mixin ya lo trae, esta línea es opcional

    # --- filtros dinámicos --------------------------------------------------
    def extra_filter(self, qs, request):
        prov_id = request.GET.get("proveedor_id", "").strip()
        if prov_id:
            qs = qs.filter(
                Exists(
                    PreciosProveedor.objects.filter(
                        productoid=OuterRef("pk"),
                        proveedorid=prov_id
                    )
                )
            )

        excl = request.GET.get("excluded", "").split(",")
        excl_ids = [int(x) for x in excl if x.isdigit()]
        if excl_ids:
            qs = qs.exclude(productoid__in=excl_ids)

        return qs.order_by("nombre")

    # --- sobrescribimos GET para inyectar el precio -------------------------
    def get(self, request, *args, **kwargs):
        import json

        # respuesta «base» del mixin (JsonResponse)
        base_response = super().get(request, *args, **kwargs)

        # lo convertimos a dict
        base_data = json.loads(base_response.content)

        prov_id = request.GET.get("proveedor_id")
        nuevos  = []
        for itm in base_data["results"]:
            precio = (
                PreciosProveedor.objects
                .filter(productoid_id=itm["id"], proveedorid=prov_id)
                .values_list("precio", flat=True)
                .first()   # None → usamos 0
            ) or 0
            nuevos.append({**itm, "precio": str(precio)})

        return JsonResponse(
            {"results": nuevos, "has_more": base_data["has_more"]},
            safe=False
        )

class PedidoListView(LoginRequiredMixin, ListView):
    """
    Lista de pedidos a proveedor, más recientes primero, paginada
    y con la variable *just_updated* para mostrar la alerta cuando
    se vuelve desde “Editar Pedido”.
    """
    model               = PedidoProveedor
    template_name       = "visualizar_pedidos.html"
    context_object_name = "pedidos"
    paginate_by         = 50
    ordering            = ["-fechapedido"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["just_updated"] = self.request.GET.get("updated") == "1"
        return ctx


@login_required
def eliminar_pedido(request, pedido_id):
    if request.method == 'POST':
        try:
            pedido = get_object_or_404(PedidoProveedor, pk=pedido_id)
            pedido.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            print("Error al eliminar:", e)  # Para debug en la consola del servidor
            return JsonResponse({'success': False, 'message': 'Error al eliminar el pedido.'})
    else:
        return JsonResponse({'success': False, 'message': 'Método no permitido.'})

class PedidoDetailView(LoginRequiredMixin, DetailView):
    model               = PedidoProveedor
    template_name       = "ver_pedido.html"
    context_object_name = "pedido"
    pk_url_kwarg        = "pedido_id"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # obtenemos los detalles asociados
        detalles = DetallePedidoProveedor.objects.filter(pedidoid=self.object)
        # calculamos subtotal en cada uno
        for det in detalles:
            det.subtotal = det.preciounitario * det.cantidad
        ctx["detalles"] = detalles
        return ctx

@method_decorator(login_required, name="dispatch")
class EditarPedidoView(View):
    template_name = "editar_pedido.html"

    def get(self, request, pedido_id):
        pedido = get_object_or_404(PedidoProveedor, pk=pedido_id)

        detalles_qs = (
            DetallePedidoProveedor.objects
            .filter(pedidoid=pedido)
            .select_related("productoid")
            .annotate(
                subtotal=ExpressionWrapper(
                    F("cantidad") * F("preciounitario"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )
            .order_by("productoid__nombre")
        )

        detalles_json = json.dumps([
            {
                "detallepedidoid": det.detallepedidoid,
                "productoid":      det.productoid_id,
                "producto":        det.productoid.nombre,
                "cantidad":        det.cantidad,
                "precio_unitario": float(det.preciounitario),
                "subtotal":        float(det.subtotal),
            }
            for det in detalles_qs
        ])

        form = EditarPedidoForm(initial={
            "proveedor":               pedido.proveedorid_id,
            "proveedor_autocomplete":  pedido.proveedorid.nombre,
            "sucursal":                pedido.sucursalid_id,
            "sucursal_autocomplete":   pedido.sucursalid.nombre,
            "fechaestimadaentrega":    pedido.fechaestimadaentrega,
            "comentario":              pedido.comentario or "",
            "estado":                  pedido.estado,
            "monto_pagado":            pedido.monto_pagado or "",
            "caja_pagoid":             pedido.caja_pago_id or "",
            "caja_pago_autocomplete":  pedido.caja_pago.nombre if pedido.caja_pago else "",
            "detalles":                detalles_json,
        })

        return render(request, self.template_name, {
            "form":          form,
            "pedido":        pedido,
            "detalles_qs":   detalles_qs,
            "detalles_json": detalles_json,
        })

    @transaction.atomic
    def post(self, request, pedido_id):
        form = EditarPedidoForm(request.POST)
        if not form.is_valid():
            errs = {
                f: [{"message": e["message"]} for e in ferr]
                for f, ferr in form.errors.get_json_data().items()
            }
            return JsonResponse({"success": False, "errors": errs})

        pedido = get_object_or_404(PedidoProveedor, pk=pedido_id)

        # Estado previo para decidir caja y si sumar inventario
        previo_estado  = pedido.estado
        previo_caja_id = pedido.caja_pago_id  # puede ser None

        # Campos base
        pedido.proveedorid_id       = form.cleaned_data["proveedor"]
        pedido.sucursalid_id        = form.cleaned_data["sucursal"]
        pedido.fechaestimadaentrega = form.cleaned_data["fechaestimadaentrega"]
        pedido.comentario           = form.cleaned_data.get("comentario", "")
        pedido.estado               = form.cleaned_data["estado"]

        # Detalles desde el form (JSON)
        detalles = json.loads(form.cleaned_data["detalles"] or "[]")

        # Recalcular total
        total = sum(
            Decimal(str(d["precio_unitario"])) * Decimal(str(d["cantidad"]))
            for d in detalles
        )
        pedido.costototal = total

        # Si pasa a "Recibido", validar caja/pago
        if pedido.estado == "Recibido":
            monto   = Decimal(str(form.cleaned_data["monto_pagado"]))
            caja_id = form.cleaned_data["caja_pagoid"]

            # Descontar solo si antes no estaba recibido o cambió la caja
            debe_descontar = (previo_estado != "Recibido") or (str(previo_caja_id) != str(caja_id))
            if debe_descontar:
                caja = get_object_or_404(PuntosPago.objects.select_for_update(), pk=caja_id)
                if caja.dinerocaja < monto:
                    return JsonResponse({
                        "success": False,
                        "errors": {"monto_pagado": [{"message": "Saldo insuficiente en la caja seleccionada."}]}
                    })
                caja.dinerocaja -= monto
                caja.save(update_fields=["dinerocaja"])

            pedido.monto_pagado   = monto
            pedido.caja_pago_id   = caja_id
            pedido.fecha_recibido = date.today()
        else:
            # Limpiar campos de recibido si cambió de estado
            pedido.monto_pagado   = None
            pedido.caja_pago      = None
            pedido.fecha_recibido = None

        # Guardar cabecera antes de tocar líneas
        pedido.save()

        # Reemplazar detalles del pedido
        DetallePedidoProveedor.objects.filter(pedidoid=pedido).delete()
        nuevas = [
            DetallePedidoProveedor(
                pedidoid       = pedido,
                productoid_id  = d["productoid"],
                cantidad       = d["cantidad"],
                preciounitario = Decimal(str(d["precio_unitario"])),
            )
            for d in detalles
        ]
        if nuevas:
            DetallePedidoProveedor.objects.bulk_create(nuevas)

        # === Si quedó RECIBIDO ===
        if pedido.estado == "Recibido":
            proveedor_id = pedido.proveedorid_id
            sucursal_id  = pedido.sucursalid_id

            # 1) Actualizar precios del proveedor si cambiaron
            for d in detalles:
                prod_id      = int(d["productoid"])
                precio_nuevo = Decimal(str(d["precio_unitario"])).quantize(Decimal("0.01"))
                pp, created = PreciosProveedor.objects.get_or_create(
                    productoid_id=prod_id,
                    proveedorid_id=proveedor_id,
                    defaults={"precio": precio_nuevo},
                )
                if not created and pp.precio != precio_nuevo:
                    pp.precio = precio_nuevo
                    pp.save(update_fields=["precio"])

            # 2) Sumar al inventario de la sucursal (solo si antes NO estaba recibido)
            if previo_estado != "Recibido":
                for d in detalles:
                    prod_id = int(d["productoid"])
                    qty     = Decimal(str(d["cantidad"]))  # usa int(...) si tu campo es entero

                    inv, _ = Inventario.objects.select_for_update().get_or_create(
                        sucursalid_id=sucursal_id,
                        productoid_id=prod_id,
                        defaults={"cantidad": 0}
                    )
                    # Incremento atómico
                    Inventario.objects.filter(pk=inv.pk).update(
                        cantidad = F("cantidad") + qty
                    )

        # ✅ Mensaje de éxito (sistema de mensajes de Django)
        messages.success(request, f"El pedido #{pedido.pedidoid} se actualizó correctamente.")

        # ✅ Redirección con query param para alert en visualizar_pedidos
        return JsonResponse({
            "success": True,
            "redirect_url": reverse("visualizar_pedidos") + "?updated=1&msg=Pedido%20actualizado%20correctamente",
        })

class PuntoPagoPorSucursalAutocomplete(PaginatedAutocompleteMixin):
    model      = PuntosPago
    text_field = "nombre"
    id_field   = "puntopagoid"
    per_page   = 10

    def extra_filter(self, qs, request):
        sid = request.GET.get("sucursal_id")
        if sid:
            qs = qs.filter(sucursalid_id=sid)
        return qs.order_by(self.text_field)

class PermisoCreateView(LoginRequiredMixin, CreateView):
    model = Permiso
    form_class = PermisoForm
    template_name = "permiso_form.html"
    success_url = reverse_lazy("permiso_agregar")  # permanecer en la página para agregar varios

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Permiso creado correctamente.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Por favor corrige los errores.")
        return super().form_invalid(form)

class PermisoListView(LoginRequiredMixin, ListView):
    """
    Muestra la tabla de permisos con DataTable.
    """
    template_name       = "visualizar_permisos.html"
    model               = Permiso
    context_object_name = "permisos"

class PermisoUpdateAJAXView(LoginRequiredMixin, UpdateView):
    """
    ▸ Edita un permiso vía AJAX, manteniendo misma UX que ‘Editar Rol’.
    """
    model         = Permiso
    pk_url_kwarg  = "permiso_id"
    form_class    = PermisoEditarForm
    template_name = "editar_permiso.html"
    success_url   = reverse_lazy("visualizar_permisos")

    # -------- AJAX OK --------
    def form_valid(self, form):
        self.object = form.save()
        msg = f'Permiso «{self.object.nombre}» actualizado correctamente.'
        messages.success(self.request, msg)  # persiste tras redirect

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect_url": str(self.success_url),
            })
        return super().form_valid(form)

    # -------- AJAX KO --------
    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "errors": form.errors.get_json_data()},
                status=400,
            )
        return super().form_invalid(form)

def eliminar_permiso(request, pk):
    """Elimina por POST y vuelve a la lista."""
    if request.method == "POST":
        obj = get_object_or_404(Permiso, pk=pk)
        nombre = obj.nombre
        obj.delete()
        messages.success(request, f"Permiso «{nombre}» eliminado correctamente.")
    return redirect("visualizar_permisos")

class RolPermisoAssignView(LoginRequiredMixin, View):
    """
    Página + endpoint AJAX para asociar 1..n permisos a un rol.
    Espera:
      - form.rol (hidden) con el rol elegido.
      - permisos_temp (hidden) con JSON: [{permisoId:<id>, permisoName:<txt>}, ...]
    """
    template_name = "roles_permisos.html"
    form_class = RolPermisoAssignForm

    def get(self, request):
        return render(request, self.template_name, {"form": self.form_class()})

    @transaction.atomic
    def post(self, request):
        form = self.form_class(request.POST)
        if not form.is_valid():
            # form.errors.get_json_data() ya viene estructurado; lo serializamos
            return JsonResponse(
                {"success": False, "errors": json.dumps(form.errors.get_json_data(escape_html=True))},
                status=400,
            )

        # Rol
        rol = form.cleaned_data["rol"]

        # Lista de permisos venida del front
        raw = request.POST.get("permisos_temp", "[]")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "errors": json.dumps({"__all__":[{"message":"JSON inválido"}]})},
                status=400,
            )

        if not items:
            return JsonResponse(
                {"success": False, "errors": json.dumps({"__all__":[{"message":"Debe agregar al menos un permiso."}]})},
                status=400,
            )

        creados = 0
        for it in items:
            pid = it.get("permisoId")
            if not pid:
                continue
            permiso = get_object_or_404(Permiso, pk=pid)

            # Gracias al unique(rol, permiso) esto es seguro y atómico
            _, was_created = RolPermiso.objects.get_or_create(rol=rol, permiso=permiso)
            if was_created:
                creados += 1

        if not creados:
            return JsonResponse(
                {"success": False, "errors": json.dumps({"__all__":[{"message":"Nada que guardar."}]})},
                status=400,
            )

        messages.success(request, f"Se asociaron {creados} permisos al rol «{rol.nombre}».")
        return JsonResponse({"success": True, "created": creados})


# ---------- Autocompletes ----------
class RolAutocomplete(LoginRequiredMixin, View):
    """
    Devuelve {results: [{id, text}, ...], has_more: bool}
    • Solo roles SIN permisos asociados en rolespermisos
    • Filtro por 'term' y paginación por 'page'
    """
    PAGE_SIZE = 20

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        # Subquery: ¿existe algún rol-permiso para este rol?
        has_perms = Exists(
            RolPermiso.objects.filter(rol=OuterRef("pk"))
        )

        qs = (
            Rol.objects
               .annotate(_has_perms=has_perms)
               .filter(_has_perms=False)          # <-- solo SIN permisos
               .order_by("nombre")
        )

        if term:
            qs = qs.filter(Q(nombre__icontains=term))

        start = (page - 1) * self.PAGE_SIZE
        end   = start + self.PAGE_SIZE
        total = qs.count()
        rows  = qs[start:end]

        results = [{"id": r.pk, "text": r.nombre} for r in rows]
        has_more = end < total
        return JsonResponse({"results": results, "has_more": has_more})


class PermisoAutocomplete(LoginRequiredMixin, View):
    """
    Devuelve permisos excluyendo IDs ya listados (?excluded=1,2,3).
    Respuesta {results:[{id,text}], has_more}
    """
    PAGE_SIZE = 30

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = max(int(request.GET.get("page") or 1), 1)

        excluded = request.GET.get("excluded", "")
        ids = [int(x) for x in excluded.split(",") if x.isdigit()]

        qs = Permiso.objects.exclude(pk__in=ids).order_by("nombre")
        if term:
            qs = qs.filter(Q(nombre__icontains=term) | Q(descripcion__icontains=term))

        start, end = (page - 1) * self.PAGE_SIZE, page * self.PAGE_SIZE
        total = qs.count()
        rows = qs[start:end]

        results = [{"id": p.pk, "text": p.nombre} for p in rows]
        return JsonResponse({"results": results, "has_more": end < total})

class VisualizarRolesPermisosView(LoginRequiredMixin, View):
    """
    GET  -> página base sin tabla (hasta que el usuario elija un rol)
    POST -> recibe rol (id) y muestra sus permisos en tabla
    """
    template_name = "visualizar_roles_permisos.html"

    def get(self, request):
        return render(request, self.template_name, self._ctx())

    def post(self, request):
        ctx = self._ctx()
        rid = (request.POST.get("rol") or "").strip()  # <input name="rol" ...>
        if rid.isdigit():
            rol = get_object_or_404(Rol, pk=int(rid))
            permisos_rel = (
                RolPermiso.objects
                .select_related("permiso")
                .filter(rol=rol)
                .order_by("permiso__nombre")
            )
            ctx["rol_seleccionado"] = rol
            ctx["permisos_rel"] = permisos_rel
        # si no hay id válido, vuelve con página base
        return render(request, self.template_name, ctx)

    def _ctx(self):
        # contexto mínimo; la búsqueda se hace con un endpoint de autocomplete
        return {"rol_seleccionado": None, "permisos_rel": None}


@login_required
def eliminar_rol_permiso_view(request, rp_id):
    """
    Elimina una relación RolPermiso por PK (botón papelera) y devuelve JSON.
    Maneja grácilmente el caso 'no encontrado' para clientes AJAX.
    """
    try:
        rel = RolPermiso.objects.select_related("permiso").get(pk=rp_id)
    except RolPermiso.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Relación no encontrada."},
            status=404
        )

    nombre_perm = rel.permiso.nombre
    rel.delete()
    return JsonResponse(
        {"success": True, "message": f'Permiso "{nombre_perm}" desvinculado correctamente.'}
    )


class RolConPermisosAutocomplete(LoginRequiredMixin, View):
    """
    Respuesta: {results:[{id,text}], has_more:bool}
    GET: term, page
    """
    PAGE = 20

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        sub = RolPermiso.objects.filter(rol_id=OuterRef("pk"))
        qs = (Rol.objects
              .annotate(has_perms=Exists(sub))
              .filter(has_perms=True)
              .order_by("nombre"))
        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start = (page - 1) * self.PAGE
        end   = start + self.PAGE
        rows  = qs[start:end]

        results = [{"id": r.pk, "text": r.nombre} for r in rows]
        return JsonResponse({"results": results, "has_more": end < total})

class RolesPermisosEditView(LoginRequiredMixin, View):
    """
    Editar permisos de un rol con 'buffer':
      • GET  -> muestra permisos actuales
      • POST -> aplica altas (permisos_temp) y bajas (permisos_borrar) en batch
    """
    template_name = "editar_roles_permisos.html"
    form_class    = RolPermisoEditForm
    success_url   = reverse_lazy("visualizar_roles_permisos")

    def get(self, request, rol_id):
        rol = get_object_or_404(Rol, pk=rol_id)

        rels = (RolPermiso.objects
                .select_related("permiso")
                .filter(rol=rol)
                .order_by("permiso__nombre"))

        form = self.form_class(initial={"rol": rol})
        ctx = {"rol": rol, "form": form, "permisos_rel": rels}
        return render(request, self.template_name, ctx)

    @transaction.atomic
    def post(self, request, rol_id):
        """
        Espera:
          - 'permisos_temp'   => JSON con altas [{permisoId, permisoName}]
          - 'permisos_borrar' => JSON con bajas  [permisoId, ...]
        """
        rol  = get_object_or_404(Rol, pk=rol_id)
        form = self.form_class(request.POST, initial={"rol": rol})

        if not form.is_valid():
            return JsonResponse({
                "success": False,
                "errors": form.errors.get_json_data(escape_html=True),
            }, status=400)

        import json

        # ALTAS
        raw_add = request.POST.get("permisos_temp", "[]")
        try:
            items_add = json.loads(raw_add)
        except json.JSONDecodeError:
            items_add = []

        # BAJAS
        raw_del = request.POST.get("permisos_borrar", "[]")
        try:
            items_del = json.loads(raw_del)
        except json.JSONDecodeError:
            items_del = []

        # Nada que hacer
        if not items_add and not items_del:
            return JsonResponse({
                "success": False,
                "errors": {"__all__": [{"message": "No hay cambios para guardar."}]}
            }, status=400)

        creados = 0
        eliminados = 0

        # Procesar ALTAS (evitar duplicados)
        for it in items_add:
            pid = it.get("permisoId")
            if not pid:
                continue
            permiso = get_object_or_404(Permiso, pk=pid)
            _, was_created = RolPermiso.objects.get_or_create(rol=rol, permiso=permiso)
            if was_created:
                creados += 1

        # Procesar BAJAS (por permiso_id)
        if items_del:
            ids = [int(x) for x in items_del if str(x).isdigit()]
            if ids:
                qs = RolPermiso.objects.filter(rol=rol, permiso_id__in=ids)
                eliminados = qs.count()
                qs.delete()

        messages.success(
            request,
            f"Cambios guardados para «{rol.nombre}». (+{creados} altas, −{eliminados} bajas)"
        )
        return JsonResponse({
            "success": True,
            "created": creados,
            "deleted": eliminados,
            "redirect_url": str(self.success_url)
        })


class PermisoParaRolAutocomplete(LoginRequiredMixin, View):
    """
    Autocomplete de permisos EXCLUYENDO:
      • los que ya tiene el rol en BD (salvo los marcados en 'pending_remove')
      • los listados en 'excluded' (agregados en el front)
    GET:
      term, page, rol_id, excluded, pending_remove
    Resp: {results:[{id,text}], has_more:bool}
    """
    PAGE = 30

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        # agregados en el front (no deben aparecer)
        excluded = request.GET.get("excluded", "")
        excluded_ids = [int(x) for x in excluded.split(",") if x.isdigit()]

        # ids marcados PARA BORRAR (en borrador) -> deben volver a aparecer
        pend = request.GET.get("pending_remove", "")
        pending_remove_ids = [int(x) for x in pend.split(",") if x.isdigit()]

        # rol actual (para excluir los que tiene en BD, menos los pending_remove)
        rid = request.GET.get("rol_id")
        already_ids = []
        if rid and rid.isdigit():
            already_ids = list(
                RolPermiso.objects.filter(rol_id=int(rid))
                                   .values_list("permiso_id", flat=True)
            )
            if pending_remove_ids:
                # quita de "ya vinculados" los que están marcados para borrar en borrador
                already_ids = [pid for pid in already_ids if pid not in set(pending_remove_ids)]

        # construir queryset final
        qs = Permiso.objects.exclude(pk__in=already_ids + excluded_ids).order_by("nombre")
        if term:
            qs = qs.filter(Q(nombre__icontains=term) | Q(descripcion__icontains=term))

        total = qs.count()
        start = (page - 1) * self.PAGE
        rows  = qs[start:start + self.PAGE]

        data = [{"id": p.pk, "text": p.nombre} for p in rows]
        return JsonResponse({"results": data, "has_more": start + self.PAGE < total})

PAGE_SIZE = 20


class VentasDiariasStatsView(LoginRequiredMixin, View):
    """
    Devuelve {num_ventas, total_vendido} para (sucursal, puntopago, fecha) y modo.

    + Ahora soporta filtro por intervalo cerrado de horas:
      - hora_desde (HH:MM o HH:MM:SS)
      - hora_hasta (HH:MM o HH:MM:SS)
      Incluye endpoints: >= y <=
    """

    METODO_ALIASES = {
        "EFECTIVO": {"EFECTIVO", "CASH", "EF"},
        "NEQUI": {"NEQUI"},
        "DAVIPLATA": {"DAVIPLATA", "DAVI", "DAVI PLATA"},
        "TARJETA": {"TARJETA", "CARD", "TC", "TARJETA CREDITO", "TARJETA DEBITO", "CREDITO", "DEBITO"},
        "BANCO_CAJA_SOCIAL": {"BANCO_CAJA_SOCIAL", "BANCO CAJA SOCIAL", "CAJA SOCIAL", "BCS"},
    }

    METODO_CANON = {
        "EFECTIVO": "efectivo",
        "NEQUI": "nequi",
        "DAVIPLATA": "daviplata",
        "TARJETA": "tarjeta",
        "BANCO_CAJA_SOCIAL": "banco_caja_social",
    }

    def _resolve_canonical(self, modo_upper: str):
        modo_upper = (modo_upper or "").upper().strip()
        if modo_upper in self.METODO_CANON:
            return self.METODO_CANON[modo_upper]
        for key, aliases in self.METODO_ALIASES.items():
            if modo_upper in aliases:
                return self.METODO_CANON.get(key)
        return None

    def _pick_field(self, model, candidates):
        for name in candidates:
            try:
                model._meta.get_field(name)
                return name
            except FieldDoesNotExist:
                continue
        return None

    def _parse_time(self, s: str):
        """
        Acepta:
          - '07:00'
          - '07:00:00'
        """
        s = (s or "").strip()
        if not s:
            return None
        try:
            # time.fromisoformat soporta HH:MM[:SS[.ffffff]]
            return time.fromisoformat(s)
        except ValueError:
            return None

    def get(self, request):
        sid  = request.GET.get("sucursal_id")
        pid  = request.GET.get("puntopago_id")
        f    = request.GET.get("fecha")
        modo = (request.GET.get("modo") or "TOTAL").upper().strip()

        # nuevas horas
        h_desde = request.GET.get("hora_desde")  # HH:MM o HH:MM:SS
        h_hasta = request.GET.get("hora_hasta")

        if not (sid and pid and f):
            return JsonResponse({"success": False, "error": "Parámetros incompletos."}, status=400)

        suc = get_object_or_404(Sucursal, pk=sid)
        pp  = get_object_or_404(PuntosPago, pk=pid, sucursalid=suc)

        # fecha yyyy-mm-dd
        try:
            fecha = datetime.fromisoformat(f).date()
        except Exception:
            return JsonResponse({"success": False, "error": "Fecha inválida."}, status=400)

        # Base: ventas del punto de pago en esa fecha (como ya lo tenías)
        ventas_qs = Venta.objects.filter(puntopagoid=pp, sucursalid=suc, fecha=fecha)

        # --------- aplicar intervalo de horas (CERRADO) ----------
        t_from = self._parse_time(h_desde)
        t_to   = self._parse_time(h_hasta)

        # Si el usuario pone solo una:
        # - solo desde => hasta fin del día
        # - solo hasta => desde inicio del día
        if t_from and not t_to:
            t_to = time(23, 59, 59)
        if t_to and not t_from:
            t_from = time(0, 0, 0)

        if t_from and t_to:
            # 1) Si existe DateTimeField en Venta (recomendado)
            dt_field = self._pick_field(Venta, [
                "fechahora", "fecha_hora", "created_at", "fecha_creacion", "fecha_registro"
            ])

            # 2) Si no existe DateTimeField, buscamos TimeField
            tm_field = self._pick_field(Venta, [
                "hora", "hora_venta", "hora_registro"
            ])

            if dt_field:
                tz = timezone.get_current_timezone()
                dt_from = timezone.make_aware(datetime.combine(fecha, t_from), tz)
                dt_to   = timezone.make_aware(datetime.combine(fecha, t_to), tz)

                # intervalo cerrado: >= y <=
                ventas_qs = ventas_qs.filter(**{
                    f"{dt_field}__gte": dt_from,
                    f"{dt_field}__lte": dt_to,
                })

            elif tm_field:
                ventas_qs = ventas_qs.filter(**{
                    f"{tm_field}__gte": t_from,
                    f"{tm_field}__lte": t_to,
                })

            else:
                return JsonResponse({
                    "success": False,
                    "error": "Tu modelo Venta no tiene campo de hora/fecha-hora para filtrar (DateTimeField o TimeField)."
                }, status=400)
        # --------------------------------------------------------

        # TOTAL: cuenta ventas y suma total de ventas
        if modo == "TOTAL":
            agg = ventas_qs.aggregate(num=Count("ventaid"), total=Sum("total"))
            return JsonResponse({
                "success": True,
                "num_ventas": int(agg["num"] or 0),
                "total_vendido": float(agg["total"] or 0),
            })

        # Por método: usar venta_pagos (incluye mixtas)
        canon = self._resolve_canonical(modo)
        if not canon:
            return JsonResponse({"success": False, "error": "Modo de pago inválido."}, status=400)

        pagos_qs = (
            PagoVenta.objects
            .filter(ventaid__in=ventas_qs)
            .annotate(_mp=Lower(Trim(F("medio_pago"))))
            .filter(_mp=canon)
        )

        num_ventas = pagos_qs.values("ventaid_id").distinct().count()
        total_vendido = pagos_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")

        return JsonResponse({
            "success": True,
            "num_ventas": int(num_ventas),
            "total_vendido": float(total_vendido),
        })


class VentasDiariasView(LoginRequiredMixin,DenyRolesMixin, View):
    deny_roles = ["Cajero", "Auxiliar"]
    template_name = "ventas_diarias.html"



    template_name = "ventas_diarias.html"

    def get(self, request):
        hoy = timezone.localdate()  # date
        return render(request, self.template_name, {"fecha_hoy": _iso_co(hoy)})




class SucursalParaVentasAutocomplete(LoginRequiredMixin, View):
    """Autocomplete de sucursales que tienen al menos un Punto de Pago."""
    PAGE = PAGE_SIZE

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        sub = PuntosPago.objects.filter(sucursalid=OuterRef("pk"))
        qs = (
            Sucursal.objects
            .annotate(has_pp=Exists(sub))
            .filter(has_pp=True)
            .order_by("nombre")
        )

        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start, end = (page - 1) * self.PAGE, page * self.PAGE
        data = [{"id": s.pk, "text": s.nombre} for s in qs[start:end]]
        return JsonResponse({"results": data, "has_more": end < total})

class PuntoPagoParaVentasAutocomplete(LoginRequiredMixin, View):
    """Autocomplete de puntos de pago filtrados por sucursal."""
    PAGE = PAGE_SIZE

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)
        sid = request.GET.get("sucursal_id")

        qs = PuntosPago.objects.all().order_by("nombre")
        if sid and str(sid).isdigit():
            qs = qs.filter(sucursalid_id=int(sid))
        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start, end = (page - 1) * self.PAGE, page * self.PAGE
        data = [{"id": p.pk, "text": p.nombre} for p in qs[start:end]]
        return JsonResponse({"results": data, "has_more": end < total})


class SucursalConPedidosPagadosAutocomplete(LoginRequiredMixin, View):
    """
    Sucursales que tengan al menos un pedido 'Recibido' con monto_pagado > 0.
    GET: term, page, [fecha]
    """
    PAGE = 25
    def get(self, request):
        term  = (request.GET.get("term") or "").strip()
        page  = int(request.GET.get("page") or 1)
        fecha = request.GET.get("fecha")
        fecha = parse_date(fecha) if fecha else None

        subq = PedidoProveedor.objects.filter(
            sucursalid_id=OuterRef("pk"),
            estado="Recibido",
        ).exclude(monto_pagado__isnull=True).exclude(monto_pagado=0)

        if fecha:
            subq = subq.filter(fecha_recibido=fecha)

        qs = (Sucursal.objects
              .annotate(tiene=Exists(subq))
              .filter(tiene=True)
              .order_by("nombre"))
        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start = (page-1)*self.PAGE
        end   = start + self.PAGE
        rows  = qs[start:end]
        data  = [{"id": s.pk, "text": s.nombre} for s in rows]
        return JsonResponse({"results": data, "has_more": end < total})

class PuntosPagoConPedidosPagadosAutocomplete(LoginRequiredMixin, View):
    """
    Puntos de pago con pedidos 'Recibido' y monto_pagado > 0.
    GET: term, page, sucursal_id, [fecha]
    """
    PAGE = 25
    def get(self, request):
        term   = (request.GET.get("term") or "").strip()
        page   = int(request.GET.get("page") or 1)
        suc_id = request.GET.get("sucursal_id")
        fecha  = request.GET.get("fecha")
        fecha  = parse_date(fecha) if fecha else None

        qs = PuntosPago.objects.all()
        if suc_id and str(suc_id).isdigit():
            qs = qs.filter(sucursalid_id=int(suc_id))

        subq = PedidoProveedor.objects.filter(
            caja_pago_id=OuterRef("pk"),
            estado="Recibido",
        ).exclude(monto_pagado__isnull=True).exclude(monto_pagado=0)

        if fecha:
            subq = subq.filter(fecha_recibido=fecha)

        qs = qs.annotate(tiene=Exists(subq)).filter(tiene=True).order_by("nombre")
        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start = (page-1)*self.PAGE
        end   = start + self.PAGE
        rows  = qs[start:end]
        data  = [{"id": p.pk, "text": p.nombre} for p in rows]
        return JsonResponse({"results": data, "has_more": end < total})

class PedidosPagadosView(LoginRequiredMixin, View):
    """
    Página y endpoint para resumir pedidos pagados.
    GET  -> render
    POST -> JSON con 'cantidad' y 'total'
    """
    template_name = "pedidos_pagados.html"

    def get(self, request):
        return render(request, self.template_name, {})

    def post(self, request):
        suc_id  = request.POST.get("sucursal_id")
        pp_id   = request.POST.get("puntopago_id")
        fecha_s = request.POST.get("fecha")  # opcional
        fecha   = parse_date(fecha_s) if fecha_s else None

        if not (suc_id and str(suc_id).isdigit()):
            return JsonResponse({"success": False,
                                 "message": "Selecciona una sucursal."}, status=400)

        qs = PedidoProveedor.objects.filter(
            sucursalid_id=int(suc_id),
            estado="Recibido",
        ).exclude(monto_pagado__isnull=True).exclude(monto_pagado=0)

        if fecha:
            qs = qs.filter(fecha_recibido=fecha)

        if pp_id and str(pp_id).isdigit():
            qs = qs.filter(caja_pago_id=int(pp_id))

        agg = qs.aggregate(
            cantidad=Count("pedidoid"),
            total=Sum("monto_pagado")
        )
        cantidad = agg["cantidad"] or 0
        total    = agg["total"] or 0

        return JsonResponse({
            "success": True,
            "cantidad": int(cantidad),
            "total": f"{total:.2f}"
        })







CO_TZ = ZoneInfo("America/Bogota")
PAGE_SIZE = 20

# Medios "oficiales" que quieres ver siempre en cierre (aunque esperado=0)
DEFAULT_METODOS = [
    "efectivo",
    "nequi",
    "daviplata",
    "tarjeta",
    "banco_caja_social",
]

DISPLAY_METODO = {
    "efectivo": "Efectivo",
    "nequi": "Nequi",
    "daviplata": "Daviplata",
    "tarjeta": "Tarjeta",
    "banco_caja_social": "Banco Caja Social",
}

def _now_co():
    return timezone.now().astimezone(CO_TZ)

def _to_decimal(v, default=Decimal("0")) -> Decimal:
    try:
        if v is None or str(v).strip() == "":
            return default
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return default

def _normalize_metodo(s: str) -> str:
    s = (s or "").strip().lower()
    s = " ".join(s.split())
    s = s.replace(" ", "_")
    return s

def _password_ok(usuario: Usuario, raw_password: str) -> bool:
    raw_password = raw_password or ""
    # 1) si es Django User real (o tiene check_password)
    if hasattr(usuario, "check_password") and callable(getattr(usuario, "check_password")):
        try:
            return bool(usuario.check_password(raw_password))
        except Exception:
            pass

    # 2) si guardas en campo "contraseña" o similares
    for attr in ("contraseña", "contrasena", "password"):
        if hasattr(usuario, attr):
            stored = getattr(usuario, attr)
            if stored is None:
                continue
            stored = str(stored)
            # si parece hash Django -> check_password
            if stored.startswith("pbkdf2_") or stored.startswith("argon2$") or stored.startswith("bcrypt$"):
                return check_password(raw_password, stored)
            # si es texto plano
            return stored == raw_password

    return False

def _range_local_naive(turno: TurnoCaja, end_dt_aware) -> tuple:
    """
    ventas.fecha + ventas.hora es timestamp sin tz (asumimos hora local Colombia).
    Por eso convertimos inicio/cierre a hora CO y los volvemos naive.
    """
    start = timezone.localtime(turno.inicio, CO_TZ).replace(tzinfo=None)
    end   = timezone.localtime(end_dt_aware, CO_TZ).replace(tzinfo=None)
    return start, end

def _sum_pagos_por_metodo(puntopago_id: int, start_naive, end_naive) -> dict[str, Decimal]:
    """
    Preferido: venta_pagos (incluye ventas mixtas).
    Intervalo CERRADO: >= start AND <= end
    """
    sql = """
        SELECT lower(trim(vp.metodo)) as metodo, COALESCE(SUM(vp.monto),0) as total
        FROM venta_pagos vp
        JOIN ventas v ON v.ventaid = vp.ventaid
        WHERE v.puntopagoid = %s
          AND (v.fecha + v.hora) >= %s
          AND (v.fecha + v.hora) <= %s
        GROUP BY lower(trim(vp.metodo))
    """
    out: dict[str, Decimal] = {}
    with connection.cursor() as cur:
        cur.execute(sql, [puntopago_id, start_naive, end_naive])
        for metodo_raw, total in cur.fetchall():
            m = _normalize_metodo(metodo_raw)
            out[m] = _to_decimal(total)
    return out

def _sum_ventas_por_mediopago_fallback(puntopago_id: int, start_naive, end_naive) -> dict[str, Decimal]:
    """
    Fallback: ventas.mediopago + ventas.total (NO soporta mixtas).
    """
    sql = """
        SELECT lower(trim(v.mediopago)) as metodo, COALESCE(SUM(v.total),0) as total
        FROM ventas v
        WHERE v.puntopagoid = %s
          AND (v.fecha + v.hora) >= %s
          AND (v.fecha + v.hora) <= %s
        GROUP BY lower(trim(v.mediopago))
    """
    out: dict[str, Decimal] = {}
    with connection.cursor() as cur:
        cur.execute(sql, [puntopago_id, start_naive, end_naive])
        for metodo_raw, total in cur.fetchall():
            m = _normalize_metodo(metodo_raw)
            out[m] = _to_decimal(total)
    return out

def _expected_por_metodo(turno: TurnoCaja) -> tuple[dict[str, Decimal], Decimal, Decimal, Decimal]:
    """
    Retorna:
      expected_by_method, esperado_total, esperado_efectivo, esperado_no_efectivo
    """
    if not turno.cierre_iniciado:
        return {}, Decimal("0"), Decimal("0"), Decimal("0")

    start_naive, end_naive = _range_local_naive(turno, turno.cierre_iniciado)

    expected = _sum_pagos_por_metodo(turno.puntopago_id, start_naive, end_naive)
    if not expected:
        expected = _sum_ventas_por_mediopago_fallback(turno.puntopago_id, start_naive, end_naive)

    # aseguro llaves default
    for m in DEFAULT_METODOS:
        expected.setdefault(m, Decimal("0.00"))

    esperado_total = sum(expected.values(), Decimal("0.00"))
    esperado_efectivo = expected.get("efectivo", Decimal("0.00"))
    esperado_no_efectivo = (esperado_total - esperado_efectivo)

    return expected, esperado_total, esperado_efectivo, esperado_no_efectivo


# =========================
# PAGE
# =========================
class TurnoCajaPageView(LoginRequiredMixin, View):
    template_name = "turno_caja.html"

    def get(self, request: HttpRequest):
        return render(request, self.template_name, {})


# =========================
# AUTOCOMPLETES
# =========================
class PuntoPagoAutocomplete(LoginRequiredMixin, View):
    PAGE = PAGE_SIZE

    def get(self, request: HttpRequest):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = PuntosPago.objects.all().order_by("nombre")
        if term:
            qs = qs.filter(nombre__icontains=term)

        total = qs.count()
        start, end = (page - 1) * self.PAGE, page * self.PAGE
        data = [{"id": p.pk, "text": p.nombre} for p in qs[start:end]]
        return JsonResponse({"results": data, "has_more": end < total})


class CajeroAutocomplete(LoginRequiredMixin, View):
    PAGE = PAGE_SIZE

    def get(self, request: HttpRequest):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = Usuario.objects.all().order_by("nombreusuario")
        if term:
            qs = qs.filter(nombreusuario__icontains=term)

        total = qs.count()
        start, end = (page - 1) * self.PAGE, page * self.PAGE
        data = [{"id": u.pk, "text": u.nombreusuario} for u in qs[start:end]]
        return JsonResponse({"results": data, "has_more": end < total})


# =========================
# APIs
# =========================
class TurnoCajaIniciarApi(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
        puntopago_id = request.POST.get("puntopago_id")
        cajero_id    = request.POST.get("cajero_id")
        password     = request.POST.get("password", "")
        base_str     = request.POST.get("saldo_apertura_efectivo", "0")

        if not (puntopago_id and cajero_id):
            return JsonResponse({"success": False, "error": "Datos incompletos."}, status=400)

        pp = get_object_or_404(PuntosPago, pk=int(puntopago_id))
        cajero = get_object_or_404(Usuario, pk=int(cajero_id))

        if not _password_ok(cajero, password):
            return JsonResponse({"success": False, "error": "Usuario o contraseña inválidos."}, status=401)

        # Evita dos turnos abiertos por punto de pago
        if TurnoCaja.objects.filter(puntopago=pp, estado__in=["ABIERTO", "CIERRE"]).exists():
            return JsonResponse({"success": False, "error": "Ya existe un turno ABIERTO/CIERRE en este punto de pago."}, status=409)

        base = _to_decimal(base_str, Decimal("0.00"))
        turno = TurnoCaja.objects.create(
            puntopago=pp,
            cajero=cajero,
            saldo_apertura_efectivo=base,
            estado="ABIERTO",
        )

        # regreso info mínima
        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "inicio": _iso_co(timezone.localtime(turno.inicio, CO_TZ)) ,
            "base": float(turno.saldo_apertura_efectivo),
            "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
            "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},
        })


class TurnoCajaIniciarCierreApi(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
        turno_id = request.POST.get("turno_id")
        if not turno_id:
            return JsonResponse({"success": False, "error": "Falta turno_id."}, status=400)

        turno = get_object_or_404(TurnoCaja, pk=int(turno_id))

        if turno.estado != "ABIERTO":
            return JsonResponse({"success": False, "error": f"El turno no está ABIERTO (estado={turno.estado})."}, status=409)

        turno.cierre_iniciado = _now_co()
        turno.estado = "CIERRE"
        turno.save(update_fields=["cierre_iniciado", "estado"])

        expected, esperado_total, esperado_efectivo, esperado_no_efectivo = _expected_por_metodo(turno)

        # snapshot ventas (BD) en el turno
        turno.ventas_total = esperado_total
        turno.ventas_efectivo = esperado_efectivo
        turno.ventas_no_efectivo = esperado_no_efectivo

        turno.esperado_total = esperado_total
        turno.save(update_fields=[
            "ventas_total", "ventas_efectivo", "ventas_no_efectivo",
            "esperado_total"
        ])

        # crear/actualizar medios
        all_methods = list(dict.fromkeys(DEFAULT_METODOS + list(expected.keys())))
        for metodo in all_methods:
            TurnoCajaMedio.objects.update_or_create(
                turno=turno,
                metodo=metodo,
                defaults={
                    "esperado": expected.get(metodo, Decimal("0.00")),
                    "contado": None,
                    "diferencia": Decimal("0.00"),
                }
            )

        # payload
        medios_payload = []
        for metodo in all_methods:
            medios_payload.append({
                "metodo": metodo,
                "label": DISPLAY_METODO.get(metodo, metodo.replace("_", " ").title()),
                "esperado": float(expected.get(metodo, Decimal("0.00"))),
            })

        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "cierre_iniciado": _iso_co(timezone.localtime(turno.cierre_iniciado, CO_TZ)) ,
            "base": float(turno.saldo_apertura_efectivo),
            "esperado_total": float(esperado_total),
            "esperado_efectivo": float(esperado_efectivo),
            "esperado_no_efectivo": float(esperado_no_efectivo),
            "medios": medios_payload,
        })


class TurnoCajaCerrarApi(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
        turno_id = request.POST.get("turno_id")
        if not turno_id:
            return JsonResponse({"success": False, "error": "Falta turno_id."}, status=400)

        turno = get_object_or_404(TurnoCaja, pk=int(turno_id))

        if turno.estado != "CIERRE":
            return JsonResponse({"success": False, "error": f"El turno no está en CIERRE (estado={turno.estado})."}, status=409)

        # efectivo entregado (input grande)
        efectivo_entregado = _to_decimal(request.POST.get("efectivo_entregado"), Decimal("0.00"))
        if efectivo_entregado < 0:
            return JsonResponse({"success": False, "error": "Efectivo entregado no puede ser negativo."}, status=400)

        # efectivo contado = entregado - base
        base = turno.saldo_apertura_efectivo or Decimal("0.00")
        efectivo_contado = (efectivo_entregado - base).quantize(Decimal("0.01"))

        # medios enviados: viene como JSON en un campo "medios_json"
        # cada item: {metodo, contado}
        import json
        medios_json = request.POST.get("medios_json", "[]")
        try:
            medios_in = json.loads(medios_json) if medios_json else []
        except Exception:
            return JsonResponse({"success": False, "error": "medios_json inválido."}, status=400)

        # cargar medios existentes
        medios_db = {m.metodo: m for m in turno.medios.all()}

        # asegurar que exista fila efectivo
        if "efectivo" not in medios_db:
            medios_db["efectivo"] = TurnoCajaMedio.objects.create(
                turno=turno, metodo="efectivo", esperado=Decimal("0.00")
            )

        # aplicar contado para NO efectivo desde inputs (efectivo lo calculamos)
        contados: dict[str, Decimal] = {}

        for item in medios_in:
            metodo = _normalize_metodo(item.get("metodo"))
            if not metodo or metodo == "efectivo":
                continue
            contado = _to_decimal(item.get("contado"), Decimal("0.00"))
            if contado < 0:
                contado = Decimal("0.00")
            contados[metodo] = contado

            # si no existía ese medio en DB, lo creo con esperado=0
            if metodo not in medios_db:
                medios_db[metodo] = TurnoCajaMedio.objects.create(
                    turno=turno, metodo=metodo, esperado=Decimal("0.00")
                )

        contados["efectivo"] = efectivo_contado

        # calcula diferencias por medio y actualiza rows
        sum_contado = Decimal("0.00")
        sum_esperado = Decimal("0.00")
        esperado_efectivo = Decimal("0.00")

        for metodo, medio_obj in medios_db.items():
            esperado = medio_obj.esperado or Decimal("0.00")
            contado = contados.get(metodo)

            # si no mandaron ese método y no es efectivo -> lo tomamos 0 (para que sume coherente)
            if contado is None and metodo != "efectivo":
                contado = Decimal("0.00")

            # efectivo siempre calculado
            if metodo == "efectivo":
                contado = efectivo_contado
                esperado_efectivo = esperado

            contado = (contado or Decimal("0.00")).quantize(Decimal("0.01"))
            diff = (contado - esperado).quantize(Decimal("0.01"))

            medio_obj.contado = contado
            medio_obj.diferencia = diff
            medio_obj.save(update_fields=["contado", "diferencia"])

            sum_contado += contado
            sum_esperado += esperado

        diferencia_total = (sum_contado - sum_esperado).quantize(Decimal("0.01"))
        deuda_total = diferencia_total if diferencia_total < 0 else Decimal("0.00")

        # real_total = efectivo_entregado + lo "entregado" no efectivo (nequi/davi/tarjeta etc.)
        # (esto coincide con "entregó por cada medio")
        real_total = (efectivo_entregado + (sum_contado - efectivo_contado)).quantize(Decimal("0.01"))

        turno.fin = _now_co()
        turno.estado = "CERRADO"

        turno.efectivo_real = efectivo_entregado
        turno.diferencia_efectivo = (efectivo_contado - esperado_efectivo).quantize(Decimal("0.01"))

        # esperado_total ya estaba guardado al iniciar cierre, pero lo recalculamos consistente
        turno.esperado_total = sum_esperado
        turno.real_total = real_total

        # ventas_total aquí lo usamos como "ventas según usuario" (como tú pediste)
        turno.ventas_total = sum_contado

        turno.diferencia_total = diferencia_total
        turno.deuda_total = deuda_total

        turno.save(update_fields=[
            "fin", "estado",
            "efectivo_real", "diferencia_efectivo",
            "esperado_total", "real_total",
            "ventas_total",
            "diferencia_total", "deuda_total",
        ])

        msg = "Cierre OK. Sin faltantes." if deuda_total == 0 else f"⚠️ Faltante: {deuda_total} (deuda_total)."
        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "ventas_total": float(turno.ventas_total),
            "esperado_total": float(turno.esperado_total),
            "diferencia_total": float(turno.diferencia_total),
            "deuda_total": float(turno.deuda_total),
            "msg": msg,
        })








# =========================
# Helpers (intervalo cerrado)
# =========================
def _q_ventas_intervalo_cerrado(start_dt, end_dt):
    """
    Ventas están en tabla ventas: fecha (date) + hora (time).
    Intervalo cerrado: [start_dt, end_dt]
    """
    sd, ed = start_dt.date(), end_dt.date()
    st, et = start_dt.time(), end_dt.time()

    if sd == ed:
        return Q(fecha=sd, hora__gte=st, hora__lte=et)

    return (
        Q(fecha=sd, hora__gte=st) |
        Q(fecha=ed, hora__lte=et) |
        Q(fecha__gt=sd, fecha__lt=ed)
    )


def _canon_metodo(raw: str) -> str:
    """
    Normaliza el método a un canonical en minúscula para guardar en TurnoCajaMedio.metodo
    """
    s = (raw or "").strip().lower()

    # aliases típicos
    if s in {"ef", "cash"}: return "efectivo"
    if s in {"tc", "card", "credito", "debito", "tarjeta credito", "tarjeta debito"}: return "tarjeta"
    if s in {"banco caja social", "caja social", "bcs"}: return "banco_caja_social"
    if s in {"davi", "davi plata"}: return "daviplata"
    return s


CANON_ORDER = ["efectivo", "nequi", "daviplata", "tarjeta", "banco_caja_social"]


def _calcular_esperados_por_metodo(pp_id, start_dt, end_dt):
    """
    Devuelve:
      - esperado_total (suma ventas.total)
      - esperado_por_metodo (dict canonical->Decimal)
    Usa venta_pagos cuando existan pagos (incluye mixtas).
    Fallback: ventas sin pagos => usa ventas.mediopago.
    """
    q_int = _q_ventas_intervalo_cerrado(start_dt, end_dt)

    ventas_qs = Venta.objects.filter(puntopagoid_id=pp_id).filter(q_int)

    # total esperado
    esperado_total = ventas_qs.aggregate(t=Coalesce(Sum("total"), Decimal("0")))["t"] or Decimal("0")

    # detecta ventas con pagos
    sub_pagos = PagoVenta.objects.filter(ventaid_id=OuterRef("pk"))
    ventas_qs = ventas_qs.annotate(has_pagos=Exists(sub_pagos))

    # pagos (para ventas con pagos)
    ventas_con_pagos = ventas_qs.filter(has_pagos=True)
    pagos_qs = (
        PagoVenta.objects
        .filter(ventaid__in=ventas_con_pagos)
        .annotate(_m=Lower(Trim(F("metodo"))) if hasattr(PagoVenta, "metodo") else Lower(Trim(F("medio_pago"))))
    )

    # ojo: si tu modelo se llama medio_pago, cambia arriba a F("medio_pago")
    # aquí lo hacemos compatible si existe "metodo", si no, usa "medio_pago"

    # sum por método en pagos
    pagos_sums = (
        pagos_qs.values("_m")
        .annotate(total=Coalesce(Sum("monto"), Decimal("0")))
    )

    esperado_por = {}

    for row in pagos_sums:
        canon = _canon_metodo(row["_m"])
        esperado_por[canon] = (esperado_por.get(canon, Decimal("0")) + (row["total"] or Decimal("0")))

    # fallback: ventas sin pagos => ventas.mediopago
    ventas_sin_pagos = ventas_qs.filter(has_pagos=False)
    if ventas_sin_pagos.exists():
        simple_sums = (
            ventas_sin_pagos
            .annotate(_m=Lower(Trim(F("mediopago"))))
            .values("_m")
            .annotate(total=Coalesce(Sum("total"), Decimal("0")))
        )
        for row in simple_sums:
            canon = _canon_metodo(row["_m"])
            esperado_por[canon] = (esperado_por.get(canon, Decimal("0")) + (row["total"] or Decimal("0")))

    # asegurar keys fijas
    for k in CANON_ORDER:
        esperado_por.setdefault(k, Decimal("0"))

    return esperado_total, esperado_por


# =========================
# Página dashboard
# =========================
@method_decorator(login_required, name="dispatch")
class TurnosCajaDashboardView(View):
    template_name = "turnos_caja_dashboard.html"

    def get(self, request):
        return render(request, self.template_name, {})


# =========================
# Autocomplete punto de pago
# =========================
@method_decorator(login_required, name="dispatch")
class PuntoPagoAutocompleteSimple(View):
    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        qs = PuntosPago.objects.all().order_by("nombre")
        if term:
            qs = qs.filter(nombre__icontains=term)
        data = [{"id": p.pk, "text": p.nombre} for p in qs[:30]]
        return JsonResponse({"results": data})


# =========================
# API: listar turnos en curso
# =========================
@method_decorator(login_required, name="dispatch")
class TurnosCajaListAPI(View):
    def get(self, request):
        pp_id = request.GET.get("puntopago_id")
        estado = (request.GET.get("estado") or "").strip().upper()  # "", ABIERTO, CIERRE

        qs = TurnoCaja.objects.exclude(estado="CERRADO").select_related("puntopago", "cajero").order_by("-inicio")

        if pp_id and str(pp_id).isdigit():
            qs = qs.filter(puntopago_id=int(pp_id))
        if estado in {"ABIERTO", "CIERRE"}:
            qs = qs.filter(estado=estado)

        out = []
        for t in qs[:200]:
            out.append({
                "id": t.id,
                "estado": t.estado,
                "puntopago": getattr(t.puntopago, "nombre", str(t.puntopago_id)),
                "cajero": getattr(t.cajero, "nombreusuario", str(t.cajero_id)),
                "inicio": t.inicio.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
                "cierre_iniciado": t.cierre_iniciado.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S") if t.cierre_iniciado else None,
            })

        return JsonResponse({"success": True, "turnos": out})


# =========================
# API: iniciar cierre (set cierre_iniciado + generar esperados)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaIniciarCierreAPI(View):
    @transaction.atomic
    def post(self, request, turno_id: int):
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)

        # si aún no inició cierre, lo marcamos ahora
        if not turno.cierre_iniciado:
            turno.cierre_iniciado = timezone.now()
            turno.estado = "CIERRE"
            turno.save(update_fields=["cierre_iniciado", "estado"])

        # calcular esperados entre inicio y cierre_iniciado (intervalo cerrado)
        esperado_total, esperado_por = _calcular_esperados_por_metodo(
            pp_id=turno.puntopago_id,
            start_dt=turno.inicio,
            end_dt=turno.cierre_iniciado,
        )

        # upsert TurnoCajaMedio
        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.update_or_create(
                turno=turno,
                metodo=metodo,
                defaults={"esperado": esperado_por.get(metodo, Decimal("0"))}
            )

        # guardar snapshot esperado_total
        # (ventas_total aquí NO, porque eso es lo que el usuario reporta al cerrar)
        turno.esperado_total = esperado_total
        turno.save(update_fields=["esperado_total"])

        medios = []
        for m in turno.medios.all().order_by("metodo"):
            medios.append({
                "metodo": m.metodo,
                "esperado": float(m.esperado or 0),
                "contado": float(m.contado or 0) if m.contado is not None else None,
                "diferencia": float(m.diferencia or 0),
            })

        return JsonResponse({
            "success": True,
            "turno": {
                "id": turno.id,
                "estado": turno.estado,
                "puntopago": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
                "cajero": getattr(turno.cajero, "nombreusuario", str(turno.cajero_id)),
                "inicio": turno.inicio.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
                "cierre_iniciado": turno.cierre_iniciado.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
                "saldo_apertura_efectivo": float(turno.saldo_apertura_efectivo or 0),
                "esperado_total": float(turno.esperado_total or 0),
            },
            "medios": medios,
        })


# =========================
# API: snapshot (para continuar cierres ya iniciados)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaSnapshotAPI(View):
    def get(self, request, turno_id: int):
        turno = get_object_or_404(TurnoCaja.objects.select_related("puntopago", "cajero"), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)

        # si no tiene cierre iniciado, obliga a iniciar por el endpoint correspondiente
        if not turno.cierre_iniciado:
            return JsonResponse({"success": False, "error": "Este turno aún no tiene cierre iniciado."}, status=400)

        # asegurar medios (por si faltan)
        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=metodo, defaults={"esperado": Decimal("0")})

        medios = []
        for m in TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"):
            medios.append({
                "metodo": m.metodo,
                "esperado": float(m.esperado or 0),
                "contado": float(m.contado or 0) if m.contado is not None else None,
                "diferencia": float(m.diferencia or 0),
            })

        return JsonResponse({
            "success": True,
            "turno": {
                "id": turno.id,
                "estado": turno.estado,
                "puntopago": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
                "cajero": getattr(turno.cajero, "nombreusuario", str(turno.cajero_id)),
                "inicio": turno.inicio.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
                "cierre_iniciado": turno.cierre_iniciado.astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
                "saldo_apertura_efectivo": float(turno.saldo_apertura_efectivo or 0),
                "esperado_total": float(turno.esperado_total or 0),
                "efectivo_real": float(turno.efectivo_real or 0) if turno.efectivo_real is not None else None,
                "ventas_total": float(turno.ventas_total or 0),
                "deuda_total": float(turno.deuda_total or 0),
            },
            "medios": medios,
        })


# =========================
# API: cerrar turno (guardar contados + diferencias + fin)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaCerrarAPI(View):
    @transaction.atomic
    def post(self, request, turno_id: int):
        import json
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)
        if not turno.cierre_iniciado:
            return JsonResponse({"success": False, "error": "Primero inicia el cierre."}, status=400)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"success": False, "error": "JSON inválido."}, status=400)

        efectivo_entregado = Decimal(str(payload.get("efectivo_entregado") or "0"))
        contados = payload.get("contados") or {}  # {metodo: contado}

        # efectivo contado = efectivo_entregado - base
        base = turno.saldo_apertura_efectivo or Decimal("0")
        efectivo_contado = efectivo_entregado - base
        if efectivo_contado < 0:
            efectivo_contado = Decimal("0")

        # asegurar medios
        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=metodo, defaults={"esperado": Decimal("0")})

        # aplicar contados
        ventas_no_efectivo_real = Decimal("0")
        for m in TurnoCajaMedio.objects.filter(turno=turno):
            metodo = _canon_metodo(m.metodo)

            if metodo == "efectivo":
                m.contado = efectivo_contado
            else:
                v = Decimal(str(contados.get(metodo) or "0"))
                if v < 0: v = Decimal("0")
                m.contado = v
                ventas_no_efectivo_real += v

            m.diferencia = (m.contado or Decimal("0")) - (m.esperado or Decimal("0"))
            m.save(update_fields=["contado", "diferencia"])

        # recomputar esperado_total por seguridad (intervalo cerrado inicio->cierre_iniciado)
        esperado_total, esperado_por = _calcular_esperados_por_metodo(
            pp_id=turno.puntopago_id,
            start_dt=turno.inicio,
            end_dt=turno.cierre_iniciado,
        )

        # ventas_total (según tu regla): suma inputs no-efectivo + (efectivo_entregado - base)
        ventas_total_real = ventas_no_efectivo_real + efectivo_contado

        diferencia_total = ventas_total_real - esperado_total
        deuda_total = (esperado_total - ventas_total_real)
        if deuda_total <= 0:
            deuda_total = Decimal("0")

        # snapshot ventas por efectivo / no-efectivo (REAL)
        turno.ventas_total = ventas_total_real
        turno.ventas_efectivo = efectivo_contado
        turno.ventas_no_efectivo = ventas_no_efectivo_real

        turno.esperado_total = esperado_total
        turno.efectivo_real = efectivo_entregado

        turno.diferencia_total = diferencia_total
        turno.diferencia_efectivo = (efectivo_contado - esperado_por.get("efectivo", Decimal("0")))

        turno.deuda_total = deuda_total

        turno.fin = timezone.now()
        turno.estado = "CERRADO"
        turno.save(update_fields=[
            "ventas_total","ventas_efectivo","ventas_no_efectivo",
            "esperado_total","efectivo_real","diferencia_total","diferencia_efectivo",
            "deuda_total","fin","estado"
        ])

        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "ventas_total": float(turno.ventas_total or 0),
            "esperado_total": float(turno.esperado_total or 0),
            "deuda_total": float(turno.deuda_total or 0),
        })


class TurnoCajaRecuperarOIniciarView(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request):
        action = (request.POST.get("action") or "").strip()
        if action != "recuperar_o_iniciar":
            return JsonResponse({"success": False, "error": "Acción inválida."}, status=400)

        puntopago_id = (request.POST.get("puntopago_id") or "").strip()
        usuario_id   = (request.POST.get("usuario_id") or request.POST.get("cajero_id") or "").strip()
        password     = request.POST.get("password", "")
        base_str     = (request.POST.get("saldo_apertura_efectivo") or "0").strip()

        if not puntopago_id.isdigit():
            return JsonResponse({"success": False, "error": "Falta puntopago_id."}, status=400)
        if not usuario_id.isdigit():
            return JsonResponse({"success": False, "error": "Falta usuario_id/cajero_id."}, status=400)

        try:
            base = Decimal(base_str.replace(",", "").strip() or "0")
            if base < 0:
                base = Decimal("0")
        except Exception:
            return JsonResponse({"success": False, "error": "Base inválida."}, status=400)

        pp = get_object_or_404(PuntosPago, pk=int(puntopago_id))
        cajero = get_object_or_404(Usuario, pk=int(usuario_id))

        # Ajusta si tu Usuario no usa check_password
        if not hasattr(cajero, "check_password") or not cajero.check_password(password):
            return JsonResponse({"success": False, "error": "Contraseña incorrecta."}, status=403)

        turno = (TurnoCaja.objects
                 .select_for_update()
                 .filter(puntopago=pp, estado__in=["ABIERTO", "CIERRE"])
                 .order_by("-inicio")
                 .first())

        if turno:
            return JsonResponse({
                "success": True,
                "msg": "Turno retomado.",
                "modo": "RETOMADO",
                "turno_id": turno.pk,
                "estado": turno.estado,
                "inicio": _iso_co(turno.inicio) ,
                "cierre_iniciado": _iso_co(turno.cierre_iniciado) if turno.cierre_iniciado else None,
                "base": float(turno.saldo_apertura_efectivo or 0),
                "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
                "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},
            })

        turno = TurnoCaja.objects.create(
            puntopago=pp,
            cajero=cajero,
            saldo_apertura_efectivo=base,
            estado="ABIERTO",
        )

        return JsonResponse({
            "success": True,
            "msg": "Turno creado.",
            "modo": "CREADO",
            "turno_id": turno.pk,
            "estado": turno.estado,
            "inicio": _iso_co(turno.inicio) ,
            "base": float(turno.saldo_apertura_efectivo or 0),
            "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
            "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},
        })










# ─────────────────────────────────────────────────────────────────────────────
# Página principal
# ─────────────────────────────────────────────────────────────────────────────
class GestionInventarioMasivaView(LoginRequiredMixin, View):
    """
    GET: renderiza la página
    POST:
      - action=save_rows     -> guarda cambios de producto + suma ingresado a inventario
      - action=create_product-> crea producto + crea/actualiza inventario en sucursal
    """
    template_name = "gestion_inventario_masiva.html"

    def get(self, request):
        return render(request, self.template_name, {})

    @transaction.atomic
    def post(self, request):
        action = (request.POST.get("action") or "").strip()

        if action == "save_rows":
            return self._save_rows(request)

        if action == "create_product":
            return self._create_product(request)

        return JsonResponse({"success": False, "error": "Acción inválida."}, status=400)

    # ------------------ helpers conversion ------------------
    def _to_int_or_none(self, v):
        s = (str(v).strip() if v is not None else "")
        if s == "":
            return None
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    def _to_int_default(self, v, default=0):
        x = self._to_int_or_none(v)
        return default if x is None else x

    def _to_decimal_or_none(self, v):
        s = (str(v).strip() if v is not None else "")
        if s == "":
            return None
        # Permite coma colombiana
        s = s.replace(",", ".")
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return None

    def _to_decimal_default(self, v, default=Decimal("0")):
        x = self._to_decimal_or_none(v)
        return default if x is None else x

    def _to_float_or_none(self, v):
        s = (str(v).strip() if v is not None else "")
        if s == "":
            return None
        s = s.replace(",", ".")
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    # ------------------ save rows ------------------
    def _save_rows(self, request):
        sucursal_id = (request.POST.get("sucursal_id") or "").strip()
        payload_raw = request.POST.get("payload") or "[]"

        if not sucursal_id.isdigit():
            return JsonResponse({"success": False, "error": "Sucursal inválida."}, status=400)

        sucursal = get_object_or_404(Sucursal, pk=int(sucursal_id))

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "JSON inválido."}, status=400)

        if not isinstance(payload, list):
            return JsonResponse({"success": False, "error": "Payload debe ser lista."}, status=400)

        updated = 0

        for row in payload:
            pid = self._to_int_or_none(row.get("productId"))
            if not pid:
                continue

            ingresado_int = self._to_int_default(row.get("ingresado"), default=0)  # puede ser negativo/0
            pdata = row.get("producto") or {}

            producto = get_object_or_404(Producto, pk=pid)

            # --------- Producto: map completo según tu SQL ----------
            # nombre (required)
            if hasattr(producto, "nombre") and "nombre" in pdata:
                nombre = (pdata.get("nombre") or "").strip()
                if nombre:
                    producto.nombre = nombre

            # descripcion (nullable)
            if hasattr(producto, "descripcion") and "descripcion" in pdata:
                producto.descripcion = (pdata.get("descripcion") or "").strip() or None

            # codigo_de_barras (nullable)
            if hasattr(producto, "codigo_de_barras") and "codigo_de_barras" in pdata:
                producto.codigo_de_barras = (pdata.get("codigo_de_barras") or "").strip() or None

            # categoria_id (nullable int)
            if hasattr(producto, "categoria_id") and "categoria_id" in pdata:
                producto.categoria_id = self._to_int_or_none(pdata.get("categoria_id"))

            # precio (required numeric)
            if hasattr(producto, "precio") and "precio" in pdata:
                precio_dec = self._to_decimal_or_none(pdata.get("precio"))
                if precio_dec is None:
                    return JsonResponse({
                        "success": False,
                        "error": f"Precio inválido en producto #{pid}."
                    }, status=400)
                producto.precio = precio_dec

            # precio_anterior (nullable numeric)
            if hasattr(producto, "precio_anterior") and "precio_anterior" in pdata:
                producto.precio_anterior = self._to_decimal_or_none(pdata.get("precio_anterior"))

            # iva (required double precision)
            if hasattr(producto, "iva") and "iva" in pdata:
                iva_f = self._to_float_or_none(pdata.get("iva"))
                if iva_f is None:
                    return JsonResponse({
                        "success": False,
                        "error": f"IVA inválido en producto #{pid}."
                    }, status=400)
                producto.iva = iva_f

            # impuesto_consumo, icui, ibua, rentabilidad (NOT NULL, defaults 0)
            if hasattr(producto, "impuesto_consumo") and "impuesto_consumo" in pdata:
                producto.impuesto_consumo = self._to_decimal_default(pdata.get("impuesto_consumo"), Decimal("0"))

            if hasattr(producto, "icui") and "icui" in pdata:
                producto.icui = self._to_decimal_default(pdata.get("icui"), Decimal("0"))

            if hasattr(producto, "ibua") and "ibua" in pdata:
                producto.ibua = self._to_decimal_default(pdata.get("ibua"), Decimal("0"))

            if hasattr(producto, "rentabilidad") and "rentabilidad" in pdata:
                producto.rentabilidad = self._to_decimal_default(pdata.get("rentabilidad"), Decimal("0"))

            try:
                producto.save()
            except IntegrityError as e:
                return JsonResponse({
                    "success": False,
                    "error": f"Error guardando producto #{pid}: {str(e)}"
                }, status=400)

            # --------- Inventario: suma ingresado ----------
            inv, _created = Inventario.objects.select_for_update().get_or_create(
                sucursalid=sucursal,
                productoid_id=pid,
                defaults={"cantidad": 0}
            )
            inv.cantidad = int(inv.cantidad) + ingresado_int
            inv.save(update_fields=["cantidad"])

            updated += 1

        return JsonResponse({"success": True, "updated": updated})

    # ------------------ create product ------------------
    def _create_product(self, request):
        sucursal_id = (request.POST.get("sucursal_id") or "").strip()
        if not sucursal_id.isdigit():
            return JsonResponse({"success": False, "error": "Sucursal inválida."}, status=400)

        sucursal = get_object_or_404(Sucursal, pk=int(sucursal_id))

        # Required:
        nombre = (request.POST.get("nombre") or "").strip()
        precio_dec = self._to_decimal_or_none(request.POST.get("precio"))
        iva_f = self._to_float_or_none(request.POST.get("iva"))

        if not nombre:
            return JsonResponse({"success": False, "error": "El nombre es obligatorio."}, status=400)
        if precio_dec is None:
            return JsonResponse({"success": False, "error": "El precio es obligatorio y debe ser válido."}, status=400)
        if iva_f is None:
            return JsonResponse({"success": False, "error": "El IVA es obligatorio y debe ser válido."}, status=400)

        # Optional:
        descripcion = (request.POST.get("descripcion") or "").strip() or None
        codigo = (request.POST.get("codigo_de_barras") or "").strip() or None
        categoria_id = self._to_int_or_none(request.POST.get("categoria_id"))
        impuesto_consumo = self._to_decimal_default(request.POST.get("impuesto_consumo"), Decimal("0"))
        icui = self._to_decimal_default(request.POST.get("icui"), Decimal("0"))
        ibua = self._to_decimal_default(request.POST.get("ibua"), Decimal("0"))
        rentabilidad = self._to_decimal_default(request.POST.get("rentabilidad"), Decimal("0"))
        precio_anterior = self._to_decimal_or_none(request.POST.get("precio_anterior"))

        cantidad_inicial = self._to_int_default(request.POST.get("cantidad_inicial"), default=0)

        producto = Producto()
        # Asignaciones (según tu SQL)
        producto.nombre = nombre
        producto.descripcion = descripcion
        producto.precio = precio_dec
        producto.codigo_de_barras = codigo
        producto.iva = iva_f
        producto.categoria_id = categoria_id
        producto.impuesto_consumo = impuesto_consumo
        producto.icui = icui
        producto.ibua = ibua
        producto.rentabilidad = rentabilidad
        producto.precio_anterior = precio_anterior

        try:
            producto.save()
        except IntegrityError as e:
            return JsonResponse({"success": False, "error": f"No se pudo crear producto: {str(e)}"}, status=400)

        inv, _ = Inventario.objects.select_for_update().get_or_create(
            sucursalid=sucursal,
            productoid=producto,
            defaults={"cantidad": cantidad_inicial}
        )
        if int(inv.cantidad) != cantidad_inicial:
            inv.cantidad = cantidad_inicial
            inv.save(update_fields=["cantidad"])

        return JsonResponse({
            "success": True,
            "product": {
                "id": producto.pk,
                "nombre": producto.nombre,
                "descripcion": producto.descripcion or "",
                "codigo_de_barras": producto.codigo_de_barras or "",
                "categoria_id": producto.categoria_id or "",
                "precio": str(producto.precio),
                "precio_anterior": str(producto.precio_anterior) if producto.precio_anterior is not None else "",
                "iva": str(producto.iva),
                "impuesto_consumo": str(producto.impuesto_consumo),
                "icui": str(producto.icui),
                "ibua": str(producto.ibua),
                "rentabilidad": str(producto.rentabilidad),
            },
            "inventario": {"cantidad": int(inv.cantidad)}
        })


# ─────────────────────────────────────────────────────────────────────────────
# Autocomplete SUCURSAL (jQuery UI)
# ─────────────────────────────────────────────────────────────────────────────
class SucursalAutocompleteView(LoginRequiredMixin, View):
    page_size = 30

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = Sucursal.objects.all().only("sucursalid", "nombre")
        if term:
            qs = qs.filter(nombre__icontains=term)
        qs = qs.order_by("nombre")

        paginator = Paginator(qs, self.page_size)
        page_obj = paginator.get_page(page)

        results = [{"id": s.pk, "text": s.nombre} for s in page_obj.object_list]
        return JsonResponse({"results": results, "pagination": {"more": page_obj.has_next()}})


# ─────────────────────────────────────────────────────────────────────────────
# 3 Autocompletes de Producto
# ─────────────────────────────────────────────────────────────────────────────
class _ProductoBaseAutocomplete(LoginRequiredMixin, View):
    page_size = 30

    def base_qs(self):
        return Producto._base_manager.all().only("productoid", "nombre", "codigo_de_barras")

    def paginate(self, qs, page):
        paginator = Paginator(qs, self.page_size)
        page_obj = paginator.get_page(page)
        results = [{
            "id": p.productoid,
            "text": p.nombre,
            "barcode": getattr(p, "codigo_de_barras", "") or "",
        } for p in page_obj.object_list]
        return JsonResponse({"results": results, "pagination": {"more": page_obj.has_next()}})


class ProductoBuscarNombreView(_ProductoBaseAutocomplete):
    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = self.base_qs()
        if term:
            qs = qs.filter(nombre__icontains=term)
        qs = qs.order_by("nombre")
        return self.paginate(qs, page)


class ProductoBuscarBarrasView(_ProductoBaseAutocomplete):
    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        qs = self.base_qs()
        if term:
            qs = qs.filter(Q(codigo_de_barras__startswith=term) | Q(codigo_de_barras__icontains=term))
        qs = qs.order_by("codigo_de_barras", "nombre")
        return self.paginate(qs, page)


class ProductoBuscarIdView(_ProductoBaseAutocomplete):
    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        if not term.isdigit():
            return JsonResponse({"results": [], "pagination": {"more": False}})

        pid = int(term)
        qs = self.base_qs().filter(productoid=pid).order_by("nombre")
        return self.paginate(qs, page)


# ─────────────────────────────────────────────────────────────────────────────
# Detalle producto + cantidad inventario en sucursal
# ─────────────────────────────────────────────────────────────────────────────
class ProductoDetalleInventarioView(LoginRequiredMixin, View):
    """
    GET ?sucursal_id=<id>&productoid=<id>
    Devuelve producto (todas columnas relevantes) + cantidad inventario en esa sucursal.
    """
    def get(self, request):
        sucursal_id = (request.GET.get("sucursal_id") or "").strip()
        pid = (request.GET.get("productoid") or "").strip()

        if not (sucursal_id.isdigit() and pid.isdigit()):
            return JsonResponse({"success": False, "error": "Parámetros inválidos."}, status=400)

        sucursal = get_object_or_404(Sucursal, pk=int(sucursal_id))

        producto = get_object_or_404(
            Producto._base_manager.only(
                "productoid", "nombre", "descripcion", "precio", "codigo_de_barras", "iva",
                "categoria_id", "impuesto_consumo", "icui", "ibua", "rentabilidad", "precio_anterior"
            ),
            pk=int(pid)
        )

        inv = (Inventario.objects
               .filter(sucursalid=sucursal, productoid_id=int(pid))
               .only("inventarioid", "cantidad")
               .first())

        cantidad = int(inv.cantidad) if inv else 0

        return JsonResponse({
            "success": True,
            "product": {
                "id": producto.pk,
                "nombre": producto.nombre,
                "descripcion": producto.descripcion or "",
                "codigo_de_barras": producto.codigo_de_barras or "",
                "categoria_id": producto.categoria_id or "",
                "precio": str(producto.precio),
                "precio_anterior": str(producto.precio_anterior) if producto.precio_anterior is not None else "",
                "iva": str(producto.iva),
                "impuesto_consumo": str(producto.impuesto_consumo),
                "icui": str(producto.icui),
                "ibua": str(producto.ibua),
                "rentabilidad": str(producto.rentabilidad),
            },
            "inventario": {"cantidad": cantidad}
        })








class VisorProductoBarcodeView( View):
    template_name = "visor_producto_barcode.html"

    def get(self, request):
        return render(request, self.template_name)


class ProductoLookupPorBarrasVisorView(View):
    """
    GET ?barcode=7709...
    Respuesta inmediata para lectores USB (Enter) o input completo.
    """
    def get(self, request):
        barcode = (request.GET.get("barcode") or "").strip()
        if not barcode:
            return JsonResponse({"success": False, "error": "barcode vacío."}, status=400)

        p = (Producto._base_manager
             .only("productoid", "nombre", "codigo_de_barras", "precio", "precio_anterior")
             .filter(codigo_de_barras=barcode)
             .first())

        if not p:
            return JsonResponse({"success": False, "error": "No encontrado."}, status=404)

        return JsonResponse({
            "success": True,
            "product": {
                "id": p.productoid,
                "nombre": p.nombre,
                "codigo_de_barras": getattr(p, "codigo_de_barras", "") or "",
                "precio": str(p.precio) if p.precio is not None else "0",
                "precio_anterior": str(p.precio_anterior) if p.precio_anterior is not None else ""
            }
        })


class ProductoBuscarBarrasVisorView( View):
    """
    GET ?term=...
    Autocomplete fallback (cuando no hay match exacto).
    """
    page_size = 30

    def get(self, request):
        term = (request.GET.get("term") or "").strip()
        page = int(request.GET.get("page") or 1)

        if not term:
            return JsonResponse({"results": [], "pagination": {"more": False}})

        qs = (Producto._base_manager
              .only("productoid", "nombre", "codigo_de_barras", "precio", "precio_anterior")
              .filter(
                  Q(codigo_de_barras__startswith=term) |
                  Q(codigo_de_barras__icontains=term)
              )
              .order_by("codigo_de_barras", "nombre"))

        paginator = Paginator(qs, self.page_size)
        page_obj = paginator.get_page(page)

        results = []
        for p in page_obj.object_list:
            results.append({
                "id": p.productoid,
                "text": p.nombre,
                "barcode": getattr(p, "codigo_de_barras", "") or "",
                "precio": str(p.precio) if p.precio is not None else "0",
                "precio_anterior": str(p.precio_anterior) if p.precio_anterior is not None else ""
            })

        return JsonResponse({
            "results": results,
            "pagination": {"more": page_obj.has_next()}
        })