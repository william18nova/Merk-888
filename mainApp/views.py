from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Usuario, Sucursal, Categoria, Producto, Inventario, Proveedor, PreciosProveedor, PuntosPago, Rol, Empleado, HorariosNegocio, HorarioCaja, Cliente, Venta, DetalleVenta, PedidoProveedor, DetallePedidoProveedor, CambioDevolucion, Permiso, RolPermiso, UsuarioPermiso, PagoVenta, TurnoCaja, TurnoCajaMedio, NotificacionNequi
from django.db.models import Count, Sum, Exists, OuterRef, Q, F, ExpressionWrapper, DecimalField, Value, IntegerField, Case, When, CharField
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpRequest, HttpResponse
from django.contrib.auth import authenticate, login as auth_login
import json
import csv
import re
import unicodedata
import hashlib
import hmac
from datetime import date, datetime, time
from django.utils import timezone
from django.contrib.auth import authenticate
import logging
from django.utils.dateparse import parse_date, parse_datetime
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
    InventarioFotosForm,
    InventarioFotosConfirmarForm,

)
from dal import autocomplete
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
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
from django.db.models.functions import Lower, StrIndex, Trim, Coalesce, TruncDate, Cast, ExtractHour, ExtractIsoWeekDay, ExtractDay
import os, io, textwrap, subprocess
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import timedelta
import pytz
from typing import List, Dict, Any
from urllib.parse import parse_qsl
from .permissions import clear_permission_cache, permission_catalog, sync_permission_catalog, user_can_access_url_name, user_has_permission


def _round_account_peso(value) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else "0"))
    except Exception:
        amount = Decimal("0")

    if amount > 0:
        return amount.quantize(Decimal("1"), rounding=ROUND_CEILING)
    if amount < 0:
        return amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return Decimal("0")


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

    @staticmethod
    def _money(value):
        try:
            amount = Decimal(value or 0)
        except Exception:
            amount = Decimal("0")
        return f"$ {amount:,.0f}".replace(",", ".")

    @staticmethod
    def _can_any_url(user, *url_names):
        return any(user_can_access_url_name(user, url_name) for url_name in url_names)

    def _dashboard_permissions(self, user):
        return {
            "sales_summary": self._can_any_url(
                user,
                "visualizar_ventas",
                "ventas_diarias",
                "metricas_negocio",
            ),
            "sales_products": self._can_any_url(
                user,
                "reporte_ventas_producto",
                "ventas_diarias",
                "metricas_negocio",
            ),
            "cash_turn": self._can_any_url(
                user,
                "turno_caja",
                "turnos_caja_dashboard",
                "generar_venta",
                "nequi_notificaciones",
            ),
            "inventory_alerts": self._can_any_url(
                user,
                "visualizar_inventarios",
                "inventario_masivo",
                "inventario_fotos",
            ),
            "product_quality": self._can_any_url(
                user,
                "visualizar_productos",
                "visualizar_inventarios",
            ),
            "orders": self._can_any_url(
                user,
                "visualizar_pedidos",
                "pedidos_pagados",
            ),
        }

    def _quick_actions(self):
        items = [
            ("generar_venta", "Generar venta", "Caja"),
            ("inventario_fotos", "Inventario por foto", "Inventario"),
            ("visualizar_inventarios", "Ver inventario", "Inventario"),
            ("turno_caja", "Turno de caja", "Caja"),
            ("nequi_notificaciones", "Notificaciones Nequi", "Caja"),
            ("ventas_diarias", "Ventas diarias", "Reportes"),
            ("metricas_negocio", "Metricas del negocio", "Reportes"),
            ("visualizar_pedidos", "Pedidos proveedor", "Compras"),
        ]
        actions = []
        for url_name, label, eyebrow in items:
            if not user_can_access_url_name(self.request.user, url_name):
                continue
            try:
                url = reverse(url_name)
            except Exception:
                continue
            actions.append({"url": url, "label": label, "eyebrow": eyebrow})
        return actions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        user = self.request.user
        dashboard_permissions = self._dashboard_permissions(user)
        dashboard_cards = []

        if dashboard_permissions["sales_summary"]:
            ventas_hoy = Venta.objects.filter(fecha=today).aggregate(
                total=Sum("total"),
                cantidad=Count("ventaid"),
            )
            total_hoy = ventas_hoy.get("total") or Decimal("0")
            cantidad_ventas = ventas_hoy.get("cantidad") or 0
            dashboard_cards.append({
                "label": "Ventas de hoy",
                "value": self._money(total_hoy),
                "detail": f"{cantidad_ventas} venta{'s' if cantidad_ventas != 1 else ''}",
                "tone": "sales",
            })

        if user_can_access_url_name(user, "nequi_notificaciones"):
            nequi_hoy = NotificacionNequi.objects.filter(recibido_en__date=today).aggregate(
                total=Sum("monto"),
                cantidad=Count("notificacionid"),
            )
            dashboard_cards.append({
                "label": "Nequi hoy",
                "value": self._money(nequi_hoy.get("total") or Decimal("0")),
                "detail": f"{nequi_hoy.get('cantidad') or 0} notificacion{'es' if (nequi_hoy.get('cantidad') or 0) != 1 else ''}",
                "tone": "ok",
            })

        turno = None
        if dashboard_permissions["cash_turn"]:
            turno = (
                TurnoCaja.objects
                .select_related("puntopago", "puntopago__sucursalid")
                .filter(cajero=user, estado__in=["ABIERTO", "CIERRE"])
                .order_by("-inicio")
                .first()
            )

        low_stock_items = []
        if dashboard_permissions["inventory_alerts"]:
            low_stock_qs = (
                Inventario.objects
                .select_related("productoid", "sucursalid")
                .filter(cantidad__lte=5)
                .order_by("cantidad", "productoid__nombre")
            )
            low_stock_count = low_stock_qs.count()
            low_stock_items = low_stock_qs[:6]
            dashboard_cards.append({
                "label": "Stock bajo",
                "value": str(low_stock_count),
                "detail": "productos con 5 unidades o menos",
                "tone": "stock",
            })

        if dashboard_permissions["orders"]:
            pedidos_pendientes = PedidoProveedor.objects.filter(estado="En espera").count()
            dashboard_cards.append({
                "label": "Pedidos pendientes",
                "value": str(pedidos_pendientes),
                "detail": "pedidos en espera",
                "tone": "orders",
            })

        if dashboard_permissions["product_quality"]:
            productos_sin_codigo = Producto.objects.filter(
                Q(codigo_de_barras__isnull=True) | Q(codigo_de_barras__exact="")
            ).count()
            dashboard_cards.append({
                "label": "Sin codigo",
                "value": str(productos_sin_codigo),
                "detail": "productos por completar",
                "tone": "barcode",
            })

        top_productos = []
        if dashboard_permissions["sales_products"]:
            top_productos = (
                DetalleVenta.objects
                .filter(ventaid__fecha=today, cantidad__gt=0)
                .values("productoid__nombre")
                .annotate(cantidad_vendida=Sum("cantidad"))
                .order_by("-cantidad_vendida", "productoid__nombre")[:5]
            )

        turno_total = getattr(turno, "ventas_total", None) if turno else None
        turno_label = "Sesion activa"
        turno_detail = "Vista ajustada a los permisos de tu rol."
        turno_status = "neutral"
        if dashboard_permissions["cash_turn"]:
            turno_label = "Sin turno abierto"
            turno_detail = "Inicia o recupera un turno para vender."
            turno_status = "warning"
        if dashboard_permissions["cash_turn"] and turno:
            turno_label = f"Turno {turno.estado.lower()}"
            puntopago = getattr(turno, "puntopago", None)
            sucursal = getattr(puntopago, "sucursalid", None)
            turno_detail = " - ".join(
                part for part in [
                    getattr(sucursal, "nombre", ""),
                    getattr(puntopago, "nombre", ""),
                ] if part
            ) or "Caja activa"
            turno_status = "ok" if turno.estado == "ABIERTO" else "attention"
        if dashboard_permissions["cash_turn"]:
            dashboard_cards.insert(1 if dashboard_permissions["sales_summary"] else 0, {
                "label": "Turno actual",
                "value": self._money(turno_total) if turno else "Pendiente",
                "detail": turno_detail,
                "tone": turno_status,
            })

        empleado = getattr(user, "empleado", None)
        user_label = str(empleado or user)

        context.update({
            "dashboard_today": today,
            "dashboard_user_label": user_label,
            "dashboard_turno": turno,
            "dashboard_turno_label": turno_label,
            "dashboard_turno_detail": turno_detail,
            "dashboard_turno_status": turno_status,
            "dashboard_cards": dashboard_cards,
            "dashboard_permissions": dashboard_permissions,
            "dashboard_low_stock": low_stock_items,
            "dashboard_top_products": top_productos,
            "dashboard_quick_actions": self._quick_actions(),
            "dashboard_scope_copy": "Informacion visible segun los permisos asignados a tu rol.",
            "dashboard_show_top_products": dashboard_permissions["sales_products"],
            "dashboard_show_inventory_alerts": dashboard_permissions["inventory_alerts"],
        })
        return context


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


class UsuarioListView(LoginRequiredMixin,DenyRolesMixin, ListView):
    deny_roles = ["Cajero", "Auxiliar"]
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


class UsuarioUpdateAJAXView(LoginRequiredMixin, DenyRolesMixin, UpdateView):
    deny_roles = ["Cajero", "Auxiliar"]
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
    EMPLOYEE_DISCOUNT_RATE = Decimal("0.10")

    # =========================
    # Helpers TURNO / LOCK
    # =========================
    def _get_turno_activo(self, user):
        return (TurnoCaja.objects
                .select_related("puntopago")
                .filter(cajero=user, estado="ABIERTO")
                .order_by("-inicio")
                .first())

    def _resolve_sucursal_from_turno(self, turno):
        """
        Intenta deducir sucursal desde el punto de pago del turno.
        Ajusta según tu modelo (pp.sucursalid / pp.sucursal).
        """
        pp = getattr(turno, "puntopago", None)
        if not pp:
            return None
        return getattr(pp, "sucursalid", None) or getattr(pp, "sucursal", None)

    def _lock_fields(self, form):
        """
        Bloquea campos en el form (solo UX; el backend igual ignora POST).
        """
        for fname in ("sucursal", "puntopago"):
            if fname in form.fields:
                form.fields[fname].disabled = True
                form.fields[fname].widget.attrs.update({
                    "readonly": "readonly",
                    "data-locked": "1",
                })
        return form

    @staticmethod
    def _normalize_document(value):
        return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).lower()

    @classmethod
    def _empleado_por_documento_cliente(cls, cliente):
        doc = cls._normalize_document(getattr(cliente, "numerodocumento", ""))
        if not doc:
            return None

        exact = (
            Empleado.objects
            .select_related("usuarioid")
            .filter(numerodocumento__iexact=str(getattr(cliente, "numerodocumento", "") or "").strip())
            .first()
        )
        if exact:
            return exact

        for empleado in Empleado.objects.select_related("usuarioid").all():
            if cls._normalize_document(empleado.numerodocumento) == doc:
                return empleado
        return None

    @classmethod
    def _validar_compra_empleado(cls, *, cajero_user, cliente, empleado_password):
        if not cliente:
            return None

        empleado_comprador = cls._empleado_por_documento_cliente(cliente)
        if not empleado_comprador:
            return None

        empleado_cajero = getattr(cajero_user, "empleado", None)
        if empleado_cajero and empleado_cajero.pk == empleado_comprador.pk:
            raise ValueError("Un empleado no puede autofacturarse con descuento.")

        doc_cajero = cls._normalize_document(getattr(empleado_cajero, "numerodocumento", "")) if empleado_cajero else ""
        doc_comprador = cls._normalize_document(getattr(empleado_comprador, "numerodocumento", ""))
        if doc_cajero and doc_comprador and doc_cajero == doc_comprador:
            raise ValueError("Un empleado no puede autofacturarse con descuento.")

        usuario_comprador = getattr(empleado_comprador, "usuarioid", None)
        if not usuario_comprador:
            raise ValueError("El empleado comprador no tiene usuario asociado para autorizar el descuento.")

        if not empleado_password:
            raise ValueError("La compra de empleado requiere la contrasena del trabajador comprador.")

        if not usuario_comprador.check_password(empleado_password):
            raise ValueError("La contrasena del empleado comprador no es correcta.")

        return empleado_comprador

    # ---------- GET ----------
    def get(self, request, *args, **kwargs):
        turno = self._get_turno_activo(request.user)
        if not turno:
            messages.error(request, "Debes iniciar un turno de caja para poder generar ventas.")
            return redirect("turno_caja")
        initial = {}
        suc_inst = None
        pp_inst = None

        if turno:
            pp_inst = getattr(turno, "puntopago", None)
            suc_inst = self._resolve_sucursal_from_turno(turno)

            if suc_inst:
                initial["sucursal"] = getattr(suc_inst, "pk", suc_inst)
            if pp_inst:
                initial["puntopago"] = getattr(pp_inst, "pk", pp_inst)

        form = GenerarVentaForm(request.GET or None, initial=initial)

        if turno:
            form = self._lock_fields(form)

        ctx = self._base_context(form)
        ctx["turno_activo"] = bool(turno)
        ctx["turno_id"]     = getattr(turno, "pk", None)
        ctx["sucursal_nombre"]  = getattr(suc_inst, "nombre", "") if suc_inst else ""
        ctx["puntopago_nombre"] = getattr(pp_inst, "nombre", "") if pp_inst else ""

        return render(request, self.template_name, ctx)

    # ---------- POST ----------
    def post(self, request, *args, **kwargs):
        # ✅ 1) turno obligatorio
        turno = self._get_turno_activo(request.user)
        if not turno:
            return JsonResponse({
                "success": False,
                "error": "No puedes generar ventas porque NO tienes un turno de caja iniciado (ABIERTO)."
            })

        # ✅ 2) sucursal y punto pago SOLO salen del turno (no del POST)
        pp_inst  = getattr(turno, "puntopago", None)
        suc_inst = self._resolve_sucursal_from_turno(turno)

        if not pp_inst or not suc_inst:
            return JsonResponse({
                "success": False,
                "error": "Tu turno activo no tiene punto de pago y/o sucursal asociada. Revisa la configuración."
            })

        # ✅ 3) inyectar sucursal/puntopago para que el form valide aunque estén disabled
        post_data = request.POST.copy()
        post_data["sucursal"]  = str(getattr(suc_inst, "pk", suc_inst))
        post_data["puntopago"] = str(getattr(pp_inst, "pk", pp_inst))

        form = GenerarVentaForm(post_data)
        if not form.is_valid():
            if getattr(settings, "DEBUG", False):
                return JsonResponse({'success': False, 'error': 'Formulario inválido.', 'details': form.errors})
            return JsonResponse({'success': False, 'error': 'Formulario inválido.'})

        data = form.cleaned_data

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

        # ✅ parse qty 1 vez (más rápido)
        try:
            qty_list = [int(cantidades[i]) for i in range(len(prod_ids))]
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Cantidad inválida en el carrito.'})

        # ✅ 1 query y solo campos necesarios
        prods_qs = (
            Producto.objects
            .filter(productoid__in=prod_ids)
            .only("productoid", "nombre", "precio")
        )
        prods_map = {p.productoid: p for p in prods_qs}

        detalles = []
        total = Decimal('0')

        for pid, qty in zip(prod_ids, qty_list):
            if qty == 0:
                continue
            prod = prods_map.get(pid)
            if not prod:
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

        detalles, total = self._apply_bag_promo(detalles)

        cliente_id = data.get('cliente_id')
        cliente_inst = Cliente.objects.filter(pk=cliente_id).first() if cliente_id else None
        empleado_comprador = None
        descuento_empleado = Decimal("0")

        try:
            empleado_comprador = self._validar_compra_empleado(
                cajero_user=request.user,
                cliente=cliente_inst,
                empleado_password=data.get("empleado_password"),
            )
        except ValueError as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

        total_cuenta = _round_account_peso(total)
        if empleado_comprador and total_cuenta > 0:
            descuento_empleado = _round_account_peso(total_cuenta * self.EMPLOYEE_DISCOUNT_RATE)
            total = _round_account_peso(total_cuenta - descuento_empleado)
        else:
            total = total_cuenta

        # pagos puede llegar como LISTA o como STRING JSON
        pagos = data.get("pagos") or []
        if isinstance(pagos, str):
            try:
                pagos = json.loads(pagos or "[]")
            except Exception:
                pagos = []

        medio_pago_simple = (data.get("medio_pago") or "").strip().lower()
        efectivo_recibido = data.get("efectivo_recibido") or Decimal("0")
        nequi_notificacion_id = data.get("nequi_notificacion_id")

        # 🔥 si quieres máxima velocidad en prod, NO imprimas debug
        if getattr(settings, "DEBUG", False):
            try:
                print("\n[VENTA DEBUG] ---------------------------")
                print("TOTAL_BACK:", total)
                print("MEDIO_BACK:", medio_pago_simple)
                print("PAGOS_BACK:", pagos)
                print("EFECTIVO_RECIBIDO_BACK:", efectivo_recibido)
                print("TURNO_ACTIVO:", getattr(turno, "pk", None))
            except Exception:
                pass

        pagos_normalizados = self._normalize_payments(pagos, total, medio_pago_simple)

        if total > 0 and not pagos_normalizados:
            return JsonResponse({'success': False, 'error': 'Debe indicar el/los pagos.'})

        if total <= 0:
            pagos_normalizados = []

        # ✅ ULTRA FAST: inventario en 1 UPDATE atómico
        return self._crear_venta_ultra_fast(
            request.user, suc_inst, pp_inst,
            cliente_id,
            pagos_normalizados,
            detalles, total,
            efectivo_recibido,
            cliente_inst=cliente_inst,
            empleado_comprador=empleado_comprador,
            descuento_empleado=descuento_empleado,
            nequi_notificacion_id=nequi_notificacion_id,
        )

    # =========================
    # base / pagos
    # =========================
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

        if total <= 0:
            return []

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

        medio = (medio_pago_simple or "").strip().lower()
        if medio in allowed:
            return [{"medio_pago": medio, "monto": total}]

        return []

    @staticmethod
    def _apply_bag_promo(detalles):
        BAG_21 = 7318
        BAG_8001 = 8001
        BLOCK_VALUE = Decimal("11000")

        normalized = []
        for d in detalles or []:
            pid = int(d.get("productoid") or 0)
            qty = int(d.get("cantidad") or 0)
            price = GenerarVentaView._to_decimal(d.get("precio_unitario") or 0)
            nombre = d.get("producto") or f"Producto {pid}"
            normalized.append({
                "productoid": pid,
                "producto": nombre,
                "cantidad": qty,
                "precio_unitario": price,
            })

        promo_base = sum(
            (d["precio_unitario"] * d["cantidad"])
            for d in normalized
            if d["productoid"] not in {BAG_21, BAG_8001} and d["cantidad"] > 0 and d["precio_unitario"] > 0
        )
        blocks = int(promo_base // BLOCK_VALUE) if promo_base > 0 else 0

        qty_21 = sum(d["cantidad"] for d in normalized if d["productoid"] == BAG_21 and d["cantidad"] > 0)
        qty_8001 = sum(d["cantidad"] for d in normalized if d["productoid"] == BAG_8001 and d["cantidad"] > 0)

        price_21 = next((d["precio_unitario"] for d in normalized if d["productoid"] == BAG_21 and d["precio_unitario"] > 0), Decimal("0"))
        price_8001 = next((d["precio_unitario"] for d in normalized if d["productoid"] == BAG_8001 and d["precio_unitario"] > 0), Decimal("0"))

        free_21 = 0
        free_8001 = 0
        remaining_21 = qty_21
        remaining_8001 = qty_8001

        while blocks > 0 and (remaining_21 > 0 or remaining_8001 > 0):
            value_21 = (min(2, remaining_21) * price_21) if remaining_21 > 0 and price_21 > 0 else Decimal("-1")
            value_8001 = price_8001 if remaining_8001 > 0 and price_8001 > 0 else Decimal("-1")
            if value_21 <= 0 and value_8001 <= 0:
                break
            if value_8001 > value_21:
                free_8001 += 1
                remaining_8001 -= 1
            else:
                take_21 = min(2, remaining_21)
                free_21 += take_21
                remaining_21 -= take_21
            blocks -= 1

        remaining_free = {BAG_21: free_21, BAG_8001: free_8001}
        final_detalles = []
        total = Decimal("0")

        for d in normalized:
            pid = d["productoid"]
            qty = d["cantidad"]
            precio = d["precio_unitario"]
            nombre = d["producto"]

            if pid in remaining_free and qty > 0 and remaining_free[pid] > 0:
                free_qty = min(qty, remaining_free[pid])
                paid_qty = qty - free_qty
                if paid_qty > 0:
                    subtotal = precio * paid_qty
                    total += subtotal
                    final_detalles.append({
                        "productoid": pid,
                        "producto": nombre,
                        "cantidad": paid_qty,
                        "precio_unitario": precio,
                        "subtotal": subtotal,
                    })
                final_detalles.append({
                    "productoid": pid,
                    "producto": f"{nombre} (PROMO)",
                    "cantidad": free_qty,
                    "precio_unitario": Decimal("0"),
                    "subtotal": Decimal("0"),
                })
                remaining_free[pid] -= free_qty
                continue

            subtotal = precio * qty
            total += subtotal
            final_detalles.append({
                "productoid": pid,
                "producto": nombre,
                "cantidad": qty,
                "precio_unitario": precio,
                "subtotal": subtotal,
            })

        return final_detalles, total

    # =========================
    # Receipt
    # =========================
    @staticmethod
    def _build_receipt_text(venta_data: Dict[str, Any], detalles: list[dict], total, pagos: list[dict]):
        def money(n):
            return _format_money_cop(n)

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
        descuento_empleado = Decimal((venta_data or {}).get("descuento_empleado", 0) or 0)
        empleado_comprador = (venta_data or {}).get("empleado_comprador", "") or ""
        venta_id      = (venta_data or {}).get("venta_id", "")
        total_dec = Decimal(total or 0)
        subtotal_factura_raw = sum(
            (Decimal(d.get("subtotal") or 0) for d in detalles or []),
            Decimal("0"),
        )
        subtotal_factura = _round_account_peso(subtotal_factura_raw)
        descuento_calculado = Decimal("0")
        if subtotal_factura > 0 and total_dec >= 0:
            descuento_calculado = (subtotal_factura - total_dec).quantize(Decimal("0.01"))
            if descuento_calculado < Decimal("0.01"):
                descuento_calculado = Decimal("0")
        descuento_total = max(descuento_empleado, descuento_calculado)
        if descuento_total > 0 and subtotal_factura <= total_dec:
            subtotal_factura = total_dec + descuento_total

        head = [
            line("NOVA POS"),
            line("MERK2888"),
            line("NIT: 28.565.875 - 4"),
            line("FACTURA"),
        ]
        if venta_id not in (None, ""):
            head.append(line(f"Factura #{venta_id}"))
        head += [
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
        if descuento_total > 0:
            foot.append(lr("SUBTOTAL:", money(subtotal_factura)))
            descuento_label = "DESC. EMPLEADO:" if descuento_empleado > 0 else "DESCUENTO:"
            foot.append(lr(descuento_label, f"-{money(descuento_total)}"))
            foot.append(lr("USTED AHORRA:", money(descuento_total)))
            if empleado_comprador:
                foot.append(line(f"Empleado: {empleado_comprador}"))

        foot += [
            lr("TOTAL:", money(total)),
            *( [lr("CAMBIO:", money(cambio))] if cambio > 0 else [] ),
            "",
            line("¡Gracias por su compra! :) "),
            ""
        ]

        return "\n".join(head + body + pay_lines + foot)

    # =========================
    # ✅ ULTRA FAST CREAR VENTA
    # =========================
    @staticmethod
    def _crear_venta_ultra_fast(
        user, suc_inst, pp_inst, cliente_id, pagos, detalles, total, efectivo_recibido,
        cliente_inst=None, empleado_comprador=None, descuento_empleado=Decimal("0"),
        nequi_notificacion_id=None
    ):
        """
        ULTRA FAST:
        - NO select_for_update + NO bulk_update con loop Python
        - 1 UPDATE atómico con Case/When para inventario
        - bulk_create detalles y pagos
        """
        try:
            ahora = timezone.localtime()

            empleado = getattr(user, "empleado", None)
            if empleado is None:
                return JsonResponse({'success': False, 'error': 'El usuario no tiene un empleado asociado.'})

            cajero_nombre = f"{getattr(empleado, 'nombre', '')} {getattr(empleado, 'apellido', '')}".strip()
            if not cajero_nombre:
                cajero_nombre = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "—").strip()

            refund_total = sum(
                (-(d.get("subtotal") or Decimal("0")))
                for d in (detalles or [])
                if int(d.get("cantidad") or 0) < 0
            )
            if refund_total < 0:
                refund_total = Decimal("0")

            qty_map = {}
            for d in detalles:
                pid = int(d["productoid"])
                qty_map[pid] = qty_map.get(pid, 0) + int(d["cantidad"])
            prod_ids = list(qty_map.keys())

            with transaction.atomic():
                nequi_notification = None
                nequi_pago_total = sum(
                    (
                        GenerarVentaView._to_decimal(p.get("monto", 0))
                        for p in (pagos or [])
                        if (p.get("medio_pago") or "").strip().lower() == "nequi"
                    ),
                    Decimal("0")
                )

                if nequi_notificacion_id:
                    if nequi_pago_total <= 0:
                        return JsonResponse({
                            "success": False,
                            "error": "Seleccionaste un envio de Nequi, pero la venta no tiene pago por Nequi."
                        })

                    nequi_notification = (
                        NotificacionNequi.objects
                        .select_for_update()
                        .filter(pk=nequi_notificacion_id, venta__isnull=True)
                        .first()
                    )
                    if not nequi_notification:
                        return JsonResponse({
                            "success": False,
                            "error": "Ese envio de Nequi ya fue usado o no esta disponible."
                        })

                    nequi_monto = GenerarVentaView._to_decimal(nequi_notification.monto or 0)
                    if nequi_monto < nequi_pago_total:
                        return JsonResponse({
                            "success": False,
                            "error": (
                                f"El envio de Nequi seleccionado ({nequi_monto}) "
                                f"no cubre el pago Nequi ({nequi_pago_total})."
                            )
                        })

                cliente_inst = cliente_inst or (Cliente.objects.filter(pk=cliente_id).first() if cliente_id else None)
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

                # Detalles (bulk)
                det_objs = [
                    DetalleVenta(
                        ventaid=venta,
                        productoid_id=int(d["productoid"]),
                        cantidad=int(d["cantidad"]),
                        preciounitario=d["precio_unitario"],
                    )
                    for d in detalles
                ]
                DetalleVenta.objects.bulk_create(det_objs, batch_size=1000)

                # Asegura inventarios existentes (solo ids)
                existentes = set(
                    Inventario.objects
                    .filter(sucursalid=suc_inst, productoid_id__in=prod_ids)
                    .values_list("productoid_id", flat=True)
                )
                missing = [pid for pid in prod_ids if pid not in existentes]
                if missing:
                    Inventario.objects.bulk_create(
                        [Inventario(sucursalid=suc_inst, productoid_id=pid, cantidad=0) for pid in missing],
                        batch_size=2000,
                        ignore_conflicts=True
                    )

                # ✅ 1 UPDATE atómico: cantidad = cantidad - qty
                whens = [When(productoid_id=pid, then=Value(qty)) for pid, qty in qty_map.items()]
                delta = Case(*whens, default=Value(0), output_field=IntegerField())

                Inventario.objects.filter(
                    sucursalid=suc_inst,
                    productoid_id__in=prod_ids
                ).update(
                    cantidad=F("cantidad") - delta
                )

                # Pagos (bulk)
                pagos_objs = []
                efectivo_monto = Decimal("0")
                for p in pagos:
                    mp = (p.get("medio_pago") or "").lower()
                    monto = GenerarVentaView._to_decimal(p.get("monto", 0))
                    pagos_objs.append(PagoVenta(ventaid=venta, medio_pago=mp, monto=monto))
                    if mp == "efectivo":
                        efectivo_monto += monto

                if pagos_objs:
                    PagoVenta.objects.bulk_create(pagos_objs, batch_size=500)

                # caja (1 update)
                if efectivo_monto > 0:
                    PuntosPago.objects.filter(pk=pp_inst.pk).update(
                        dinerocaja=F("dinerocaja") + efectivo_monto
                    )

                if nequi_notification:
                    NotificacionNequi.objects.filter(pk=nequi_notification.pk).update(
                        venta=venta,
                        usado_en=timezone.now(),
                    )

            # CAMBIO: solo pago simple en efectivo
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
                    "cambio": cambio,
                    "venta_id": venta.pk,
                    "descuento_empleado": descuento_empleado,
                    "empleado_comprador": str(empleado_comprador or ""),
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

def _format_money_cop(x) -> str:
    """
    Formato COP compacto: $1.234 para enteros y $1,80 cuando hay decimales.
    """
    try:
        q = Decimal(str(x if x is not None else "0"))
    except Exception:
        q = Decimal("0")

    q = q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    q_abs = q.copy_abs()

    if q_abs == q_abs.to_integral_value():
        return f"{sign}${int(q_abs):,}".replace(",", ".")

    entero = int(q_abs)
    centavos = int((q_abs - Decimal(entero)) * 100)
    entero_text = f"{entero:,}".replace(",", ".")
    return f"{sign}${entero_text},{centavos:02d}"


def _fmt_money(x: Decimal) -> str:
    return _format_money_cop(x)

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


def _ticket_subtotal_discount(detalles, total) -> tuple[Decimal, Decimal]:
    subtotal = Decimal("0")
    for det in detalles or []:
        try:
            qty = Decimal(int(getattr(det, "cantidad", 0) or 0))
            price = Decimal(getattr(det, "preciounitario", 0) or 0)
        except Exception:
            continue
        subtotal += qty * price

    try:
        total_dec = Decimal(total or 0)
    except Exception:
        total_dec = Decimal("0")

    subtotal_cuenta = _round_account_peso(subtotal)
    if subtotal_cuenta <= 0 or total_dec < 0:
        return subtotal_cuenta.quantize(Decimal("0.01")), Decimal("0")

    discount = (subtotal_cuenta - total_dec).quantize(Decimal("0.01"))
    if discount < Decimal("0.01"):
        discount = Decimal("0")
    return subtotal_cuenta.quantize(Decimal("0.01")), discount


def _ticket_amount_line(label: str, amount: Decimal, width: int = TICKET_WIDTH_CHARS) -> str:
    try:
        amount_dec = Decimal(amount or 0)
    except Exception:
        amount_dec = Decimal("0")
    right = f"-{_fmt_money(amount_dec.copy_abs())}" if amount_dec < 0 else _fmt_money(amount_dec)
    return f"{label:<{width-len(right)}}{right}"


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

    detalles = list(
        DetalleVenta.objects
        .filter(ventaid=venta)
        .select_related("productoid")
    )
    subtotal_factura, descuento_total = _ticket_subtotal_discount(detalles, venta.total)

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

    if descuento_total > 0:
        out.append(_ticket_amount_line("SUBTOTAL", subtotal_factura))
        out.append(_ticket_amount_line("DESCUENTO", -descuento_total))
        out.append(_ticket_amount_line("USTED AHORRA", descuento_total))

    out.append(_ticket_amount_line("TOTAL", venta.total))
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
            "productoid__codigo_de_barras",
        ).order_by("productoid__nombre")[:limit+1])

        has_more = len(rows) > limit
        rows = rows[:limit]

        results = [{
            "id": r["productoid_id"],
            "text": r["productoid__nombre"],
            "barcode": r["productoid__codigo_de_barras"] or "",
            "precio": float(r["productoid__precio"] or 0),
            "stock": int(r["cantidad"] or 0),
        } for r in rows]

        return JsonResponse({"results": results, "has_more": has_more})

class ProductoAutocompleteGlobalView(LoginRequiredMixin, View):
    """
    Autocomplete de productos SIN restricción de sucursal.
    Busca por id (exacto/prefijo), nombre (icontains) o código de barras (icontains).
    Usado por el filtro de producto en visualizar_ventas.
    """
    per_page = 15

    def get(self, request, *args, **kwargs):
        term  = (request.GET.get("term", "") or "").strip()
        try:
            limit = int(request.GET.get("limit") or self.per_page)
        except ValueError:
            limit = self.per_page
        limit = max(1, min(limit, 50))

        qs = Producto.objects.all()

        if term:
            cond = Q(nombre__icontains=term) | Q(codigo_de_barras__icontains=term)
            if term.isdigit():
                cond = cond | Q(productoid=int(term))
            qs = qs.filter(cond)

        rows = list(qs.values(
            "productoid", "nombre", "codigo_de_barras", "precio"
        ).order_by("nombre")[:limit + 1])

        has_more = len(rows) > limit
        rows = rows[:limit]

        results = [{
            "id": r["productoid"],
            "text": r["nombre"] or "",
            "barcode": r["codigo_de_barras"] or "",
            "precio": float(r["precio"] or 0),
        } for r in rows]

        return JsonResponse({"results": results, "has_more": has_more})


class ClienteAutocompleteView(PaginatedAutocompleteMixin):
    """
    Cliente por nombre / apellido / documento.
    Usamos un override para poder buscar en varios campos a la vez.
    """
    model = Cliente
    id_field = "clienteid"
    per_page = 12

    def get(self, request, *args, **kwargs):
        term = request.GET.get("term", "").strip()
        try:
            page = max(int(request.GET.get("page", 1)), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            limit = max(1, min(int(request.GET.get("limit", self.per_page)), 30))
        except (TypeError, ValueError):
            limit = self.per_page
        start, end = (page - 1) * limit, page * limit

        qs = Cliente.objects.all()
        if term:
            qs = qs.filter(
                Q(nombre__icontains=term)  |
                Q(apellido__icontains=term)|
                Q(numerodocumento__icontains=term)
            )

        clientes = list(
            qs.order_by("nombre", "apellido")
            .values("clienteid", "nombre", "apellido", "numerodocumento")[start:end + 1]
        )
        has_more = len(clientes) > limit
        clientes = clientes[:limit]

        documentos = {
            str(c["numerodocumento"] or "").strip()
            for c in clientes
            if str(c["numerodocumento"] or "").strip()
        }

        empleados_por_doc = {}
        empleados_qs = Empleado.objects.select_related("usuarioid")
        if documentos:
            empleados_qs = empleados_qs.filter(numerodocumento__in=documentos)
        else:
            empleados_qs = Empleado.objects.none()

        for empleado in empleados_qs:
            doc_norm = GenerarVentaView._normalize_document(empleado.numerodocumento)
            if doc_norm:
                empleados_por_doc[doc_norm] = empleado

        results = []
        for c in clientes:
            empleado = empleados_por_doc.get(GenerarVentaView._normalize_document(c["numerodocumento"]))
            nombre = c["nombre"] or ""
            apellido = c["apellido"] or ""
            documento = c["numerodocumento"] or ""
            results.append({
              "id"  : c["clienteid"],
              "text": f"{nombre} {apellido} ({documento})",
              "documento": documento,
              "is_employee": bool(empleado),
              "employee_name": str(empleado or "") if empleado else "",
              "employee_has_user": bool(getattr(empleado, "usuarioid", None)) if empleado else False,
            })
        return JsonResponse({"results": results, "has_more": has_more})



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

        rows = list(
            Inventario.objects
            .filter(sucursalid=sid, productoid__codigo_de_barras=codigo)
            .select_related("productoid")
            .values(
                "productoid_id",
                "cantidad",
                "productoid__nombre",
                "productoid__codigo_de_barras",
                "productoid__precio",
            )
            .order_by("productoid_id", "inventarioid")
        )

        if not rows:
            return JsonResponse({"exists": False})

        product_ids = {row["productoid_id"] for row in rows}
        if len(product_ids) > 1:
            return JsonResponse({
                "exists": False,
                "ambiguous": True,
                "error": f'El código de barras "{codigo}" está asignado a más de un producto en esta sucursal.',
            })

        row = rows[0]

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
            "barcode": p.codigo_de_barras or "",
            "precio": float(p.precio or 0),
            "stock": int(inv_map.get(p.productoid, 0)),
        } for p in qs]

        return JsonResponse({"results": results, "has_more": total > self.per_page})

class ProductoBarrasAutocompleteView(LoginRequiredMixin, View):
    per_page = 15
    def get(self, request, *args, **kwargs):
        term = (request.GET.get("term","") or "").strip()
        sid  = (request.GET.get("sucursal_id") or "").strip()
        exact = request.GET.get("exact") == "1"
        if not sid.isdigit():
            return JsonResponse({"results": [], "has_more": False})

        qs = Producto.objects.filter(
            inventario__sucursalid=sid, inventario__cantidad__gt=0
        ).distinct()
        if term:
            if exact:
                qs = qs.filter(codigo_de_barras=term)
            else:
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
        qs_values = (
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
              )
        )
        qs_page = qs_values[start:] if length == -1 else qs_values[start:start + length]

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


class VentaListView(LoginRequiredMixin, ListView):
    """
    Todas las ventas con la MÁS RECIENTE primero.
    """
    model = Venta
    template_name = "visualizar_ventas.html"
    context_object_name = "ventas"
    paginate_by = None

    def get_queryset(self):
        return (
            Venta.objects
            .select_related("clienteid", "empleadoid", "sucursalid", "puntopagoid")
            .order_by("-ventaid")  # ✅ más recientes primero
        )

    def get_paginate_by(self, queryset):
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "sucursales": Sucursal.objects.order_by("nombre"),
            "puntos_pago": (
                PuntosPago.objects
                .select_related("sucursalid")
                .order_by("sucursalid__nombre", "nombre")
            ),
            "empleados": Empleado.objects.order_by("nombre", "apellido"),
            "medios_pago": [("mixto", "Mixto"), *MEDIOS_PAGO],
        })
        return context


class VentaDataTableView(LoginRequiredMixin, View):
    """
    Endpoint server-side ultra-rápido para DataTables en visualizar_ventas.
    Devuelve solo las ventas necesarias para la página actual.
    """

    def get(self, request, *args, **kwargs):
        from django.http import JsonResponse

        draw   = int(request.GET.get("draw", "1"))
        start  = int(request.GET.get("start", "0"))
        length = int(request.GET.get("length", "25"))
        search_value = request.GET.get("search[value]", "").strip()

        # ✅ Filtro por producto (id, nombre o código de barras).
        # Acepta:
        #   - producto_id: ID exacto (preferente, más rápido)
        #   - producto_term: texto libre (busca por nombre o codigo_de_barras)
        producto_id   = (request.GET.get("producto_id", "") or "").strip()
        producto_term = (request.GET.get("producto_term", "") or "").strip()
        venta_id      = (request.GET.get("venta_id", "") or "").strip()
        fecha_desde   = parse_date((request.GET.get("fecha_desde", "") or "").strip())
        fecha_hasta   = parse_date((request.GET.get("fecha_hasta", "") or "").strip())
        hora_desde    = (request.GET.get("hora_desde", "") or "").strip()
        hora_hasta    = (request.GET.get("hora_hasta", "") or "").strip()
        sucursal_id   = (request.GET.get("sucursal_id", "") or "").strip()
        puntopago_id  = (request.GET.get("puntopago_id", "") or "").strip()
        empleado_id   = (request.GET.get("empleado_id", "") or "").strip()
        cliente_term  = (request.GET.get("cliente_term", "") or "").strip()
        mediopago     = (request.GET.get("mediopago", "") or "").strip().lower()
        total_min_raw = (request.GET.get("total_min", "") or "").strip().replace(",", ".")
        total_max_raw = (request.GET.get("total_max", "") or "").strip().replace(",", ".")
        devoluciones  = (request.GET.get("devoluciones", "") or "").strip().lower()

        base_qs = Venta.objects.select_related(
            "clienteid", "empleadoid", "sucursalid", "puntopagoid"
        )

        records_total = base_qs.count()
        qs = base_qs

        if venta_id.isdigit():
            qs = qs.filter(ventaid=int(venta_id))

        if fecha_desde:
            qs = qs.filter(fecha__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha__lte=fecha_hasta)

        if hora_desde:
            try:
                qs = qs.filter(hora__gte=time.fromisoformat(hora_desde))
            except ValueError:
                pass
        if hora_hasta:
            try:
                qs = qs.filter(hora__lte=time.fromisoformat(hora_hasta))
            except ValueError:
                pass

        if sucursal_id.isdigit():
            qs = qs.filter(sucursalid_id=int(sucursal_id))
        if puntopago_id.isdigit():
            qs = qs.filter(puntopagoid_id=int(puntopago_id))
        if empleado_id.isdigit():
            qs = qs.filter(empleadoid_id=int(empleado_id))

        if mediopago:
            qs = qs.filter(mediopago__iexact=mediopago)

        if cliente_term:
            cliente_q = (
                Q(clienteid__nombre__icontains=cliente_term) |
                Q(clienteid__apellido__icontains=cliente_term) |
                Q(clienteid__numerodocumento__icontains=cliente_term) |
                Q(clienteid__telefono__icontains=cliente_term)
            )
            for token in cliente_term.split():
                cliente_q |= Q(clienteid__nombre__icontains=token) | Q(clienteid__apellido__icontains=token)
            qs = qs.filter(cliente_q)

        try:
            if total_min_raw:
                qs = qs.filter(total__gte=Decimal(total_min_raw))
        except InvalidOperation:
            pass
        try:
            if total_max_raw:
                qs = qs.filter(total__lte=Decimal(total_max_raw))
        except InvalidOperation:
            pass

        if devoluciones == "con":
            qs = qs.filter(cambios__isnull=False).distinct()
        elif devoluciones == "sin":
            qs = qs.filter(cambios__isnull=True)

        if producto_id.isdigit():
            qs = qs.filter(detalleventa__productoid_id=int(producto_id)).distinct()
        elif producto_term:
            prod_q = (
                Q(detalleventa__productoid__nombre__icontains=producto_term) |
                Q(detalleventa__productoid__codigo_de_barras__icontains=producto_term)
            )
            if producto_term.isdigit():
                prod_q = prod_q | Q(detalleventa__productoid_id=int(producto_term))
            qs = qs.filter(prod_q).distinct()

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

        order_column_index = request.GET.get("order[0][column]", "0")
        order_dir          = request.GET.get("order[0][dir]", "desc")

        columns = [
            "ventaid",                   # 0
            "fecha",                     # 1
            "hora",                      # 2
            "clienteid__nombre",         # 3
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

        qs_values = (
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
              )
        )
        qs_page = qs_values[start:] if length == -1 else qs_values[start:start + length]

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


class VentaDetailView(LoginRequiredMixin, DenyRolesMixin, View):
    deny_roles = []
    template_name = "ver_venta.html"
    view_permission = "ventas_ver"
    edit_permission = "ventas_cambios"
    print_permission = "ventas_imprimir"

    def _can_view_venta(self, user) -> bool:
        return user_has_permission(user, self.view_permission)

    def _can_edit_venta(self, user) -> bool:
        return user_has_permission(user, self.edit_permission)

    def _can_print_venta(self, user) -> bool:
        return (
            self._can_view_venta(user)
            or self._can_edit_venta(user)
            or user_has_permission(user, self.print_permission)
        )

    def _is_print_only(self, user) -> bool:
        return self._can_print_venta(user) and not self._can_edit_venta(user)

    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            return super().dispatch(request, *args, **kwargs)
        if self._can_print_venta(request.user):
            return View.dispatch(self, request, *args, **kwargs)
        message = "No tienes permiso para ver esta venta."
        wants_json = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", "")
        )
        if wants_json:
            return JsonResponse({"success": False, "error": message}, status=403)
        messages.error(request, message)
        return redirect("home")

    # -------------------------
    # Helpers
    # -------------------------
    def _venta_es_mixta(self, venta) -> bool:
        return (venta.mediopago or "").strip().lower() == "mixto"

    def _hay_devolucion_en_post(self, request) -> bool:
        """
        True si en el POST hay al menos un devolver > 0.
        Si no hay devoluciones, NO obligamos a validar/ejecutar el flujo de devolución.
        """
        for k, v in request.POST.items():
            if k.startswith("dev-") and k.endswith("-devolver"):
                s = (v or "").strip()
                if s == "":
                    continue
                try:
                    if int(s) > 0:
                        return True
                except ValueError:
                    # Si escribió algo raro, tratamos como "hay devolución"
                    # para que el formset falle con mensaje.
                    return True
        return False

    def _build_pagos_initial(self, venta):
        """
        Precarga pagos actuales desde venta_pagos (PagoVenta.monto) agrupado por medio_pago.
        """
        pagos_bd = {}
        if self._venta_es_mixta(venta):
            rows = (
                PagoVenta.objects
                .filter(ventaid=venta)
                .values("medio_pago")
                .annotate(total=Sum("monto"))
            )
            pagos_bd = {
                (r["medio_pago"] or "").strip().lower(): (r["total"] or Decimal("0.00"))
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

    def _calcular_total_reintegro(self, venta, devoluciones) -> Decimal:
        return CambioDevolucion.calcular_total_devolucion(venta, devoluciones).quantize(Q2)

    def _sum_formset_montos(self, formset) -> Decimal:
        s = Decimal("0.00")
        for row in (formset.cleaned_data or []):
            s += (row.get("monto") or Decimal("0.00"))
        return s.quantize(Q2)

    # -------------------------
    # ✅ Ticket texto (impresión)
    # -------------------------
    @staticmethod
    def _money(n) -> str:
        return _format_money_cop(n)

    @classmethod
    def _build_ticket_text_from_venta(cls, venta) -> str:
        WIDTH = 48

        def line(txt=""):
            t = str(txt or "")
            return t[:WIDTH]

        def lr(left, right):
            left = str(left or "")
            right = str(right or "")
            space = max(1, WIDTH - len(left) - len(right))
            return left + (" " * space) + right

        ahora = timezone.localtime(venta.fechaventa) if getattr(venta, "fechaventa", None) else timezone.localtime()

        detalles = list(
            DetalleVenta.objects
            .filter(ventaid=venta)
            .select_related("productoid")
            .order_by("pk")
        )

        pagos = []
        medio = (venta.mediopago or "").strip().lower()
        total = (venta.total or Decimal("0.00")).quantize(Q2)
        subtotal_factura, descuento_total = _ticket_subtotal_discount(detalles, total)

        if medio == "mixto":
            rows = (
                PagoVenta.objects
                .filter(ventaid=venta)
                .values("medio_pago")
                .annotate(total=Sum("monto"))
            )
            for r in rows:
                mp = (r["medio_pago"] or "").strip().lower()
                mt = (r["total"] or Decimal("0.00")).quantize(Q2)
                if mt > 0:
                    pagos.append((mp, mt))
        else:
            if total > 0 and medio:
                pagos.append((medio, total))

        if getattr(venta, "empleadoid", None):
            cajero = str(venta.empleadoid)
        elif getattr(venta, "cajeroid", None):
            cajero = str(venta.cajeroid)
        else:
            cajero = "—"

        cliente = str(venta.clienteid) if getattr(venta, "clienteid", None) else "—"

        out = []
        out.append(line("MERK888"))
        out.append(line("FACTURA / TICKET"))
        out.append(line("-" * WIDTH))
        out.append(line(f"Venta #{venta.pk}"))
        out.append(line(f"Fecha: {ahora.strftime('%Y-%m-%d %H:%M')}"))
        out.append(line(f"Cajero: {cajero}"))
        out.append(line(f"Cliente: {cliente}"))
        out.append(line("-" * WIDTH))
        out.append(line("ITEM                         CANT   SUBT"))

        for d in detalles:
            nombre = (getattr(d.productoid, "nombre", "") or "Producto")[:28]
            cant = int(d.cantidad or 0)
            sub = (Decimal(cant) * (d.preciounitario or Decimal("0.00"))).quantize(Q2)
            out.append(line(f"{nombre:<28} {cant:>4} {cls._money(sub):>7}"))

        out.append(line("-" * WIDTH))
        if descuento_total > 0:
            out.append(lr("SUBTOTAL", cls._money(subtotal_factura)))
            out.append(lr("DESCUENTO", f"-{cls._money(descuento_total)}"))
            out.append(lr("USTED AHORRA", cls._money(descuento_total)))
        out.append(lr("TOTAL", cls._money(total)))
        out.append(line("-" * WIDTH))

        if pagos:
            out.append(line("PAGOS:"))
            for mp, mt in pagos:
                out.append(lr(f"- {mp.upper()}", cls._money(mt)))

        out.append(line("-" * WIDTH))
        out.append(line("Gracias por su compra"))
        out.append(line("\n\n"))

        return "\n".join(out)

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
                nuevos.append(PagoVenta(ventaid=venta, medio_pago=medio, monto=monto))

        if nuevos:
            PagoVenta.objects.bulk_create(nuevos)

    def _guardar_pago_unico(self, venta):
        PagoVenta.objects.filter(ventaid=venta).delete()

        medio = (venta.mediopago or "").strip().lower()
        total = _to_q2(venta.total)

        if total > 0 and medio:
            PagoVenta.objects.create(
                ventaid=venta,
                medio_pago=medio,
                monto=total
            )

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
            "venta_total": (venta.total or Decimal("0.00")).quantize(Q2),
            "venta_print_only": self._is_print_only(request.user),
        })

    # -------------------------
    # POST
    # -------------------------
    @transaction.atomic
    def post(self, request, venta_id):
        venta = Venta.objects.select_for_update().get(pk=venta_id)

        accion = (request.POST.get("accion") or "").strip()

        # ✅ 0) IMPRIMIR FACTURA (no toca devoluciones/pagos)
        if accion == "imprimir_factura":
            try:
                text = self._build_ticket_text_from_venta(venta)
                return JsonResponse({"ok": True, "text": text, "venta_id": venta.pk})
            except Exception as e:
                return JsonResponse({"ok": False, "error": str(e)}, status=400)

        if self._is_print_only(request.user):
            message = "Este permiso solo permite ver e imprimir la factura."
            wants_json = (
                request.headers.get("x-requested-with") == "XMLHttpRequest"
                or "application/json" in request.headers.get("accept", "")
            )
            if wants_json:
                return JsonResponse({"success": False, "error": message}, status=403)
            return HttpResponseForbidden(message)

        detalles = list(DetalleVenta.objects.filter(ventaid=venta))

        nuevo_mediopago = (request.POST.get("mediopago") or "").strip().lower()

        dev_formset = DevolucionFormSet(request.POST, prefix="dev")
        pagos_formset = PagoMixtoFormSet(request.POST, prefix="pagos")
        reintegro_formset = ReintegroMixtoFormSet(request.POST, prefix="reint")

        metodo_old = (venta.mediopago or "").strip().lower()

        # ✅ clave: detectar si realmente hay devoluciones
        hay_devolucion = self._hay_devolucion_en_post(request)

        # 0) Cambio de medio (si cambió)
        if nuevo_mediopago and nuevo_mediopago != metodo_old:
            venta.mediopago = nuevo_mediopago
            venta.save(update_fields=["mediopago"])

            if metodo_old != "mixto" and nuevo_mediopago != "mixto":
                try:
                    self._mover_turno_por_cambio_medio(venta, metodo_old, nuevo_mediopago, venta.total)
                except Exception as e:
                    messages.warning(request, f"⚠️ Medio actualizado, pero no se pudo ajustar el turno: {e}")

            messages.success(request, "✅ Medio de pago actualizado.")

        # 1) Guardar pagos
        if self._venta_es_mixta(venta):
            ok, err = self._validar_pagos_mixtos(venta, pagos_formset)
            if not ok:
                messages.error(request, f"⚠️ {err}")
                return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))
            self._guardar_pagos_mixtos(venta, pagos_formset)
        else:
            self._guardar_pago_unico(venta)

        if accion == "volver_lista":
            return redirect(reverse_lazy("visualizar_ventas"))

        # ✅ SI NO HAY DEVOLUCIÓN: terminamos aquí (así el cambio de medio sí “se guarda” sin obligarte a devolver)
        if not hay_devolucion:
            messages.success(request, "✅ Cambios guardados.")
            return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

        # 2) Validar devoluciones (solo si hay devolucion)
        if not dev_formset.is_valid():
            messages.error(request, "⚠️ Revisa las cantidades a devolver.")
            return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

        det_map = {d.pk: d for d in detalles}
        devoluciones = []

        for row in dev_formset.cleaned_data:
            cant = int(row.get("devolver") or 0)
            if cant <= 0:
                continue

            det = det_map.get(row["detalle_id"])
            if not det:
                continue

            if cant > int(det.cantidad):
                messages.error(request, f"⚠️ No puedes devolver {cant} porque solo se vendieron {det.cantidad}.")
                return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

            devoluciones.append({"detalle": det, "cantidad": cant})

        if not devoluciones:
            return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

        total_reintegro = self._calcular_total_reintegro(venta, devoluciones)

        if self._venta_es_mixta(venta):
            ok, err, reintegro_map = self._validar_reintegro_mixto(venta, reintegro_formset, total_reintegro)
            if not ok:
                messages.error(request, f"⚠️ {err}")
                return redirect(reverse_lazy("ver_venta", kwargs={"venta_id": venta_id}))

            CambioDevolucion.registrar_devolucion(venta, devoluciones, reintegro_map=reintegro_map)
            self._restar_reintegro_de_pagos(venta, reintegro_map)

        else:
            CambioDevolucion.registrar_devolucion(venta, devoluciones)
            venta.refresh_from_db()
            self._guardar_pago_unico(venta)

        messages.success(request, "✅ Devolución registrada correctamente.")
        return redirect(reverse_lazy("visualizar_ventas"))

    @staticmethod
    def _get_field_esperado_name(obj) -> str:
        for name in ("esperado", "monto_esperado", "monto", "total", "valor"):
            if hasattr(obj, name):
                return name
        raise ValueError(
            "No encontré el campo 'esperado' en TurnoCajaMedio. "
            "Revisa tu modelo: debe existir un DecimalField tipo esperado/monto_esperado/monto/etc."
        )

    


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

class SyncPermissionCatalogMixin:
    def dispatch(self, request, *args, **kwargs):
        sync_permission_catalog()
        return super().dispatch(request, *args, **kwargs)


class PermisoCreateView(SyncPermissionCatalogMixin, LoginRequiredMixin, CreateView):
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

class PermisoListView(SyncPermissionCatalogMixin, LoginRequiredMixin, ListView):
    """
    Muestra la tabla de permisos con DataTable.
    """
    template_name       = "visualizar_permisos.html"
    model               = Permiso
    context_object_name = "permisos"

class PermisoUpdateAJAXView(SyncPermissionCatalogMixin, LoginRequiredMixin, UpdateView):
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

class RolPermisoAssignView(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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


class PermisoAutocomplete(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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

class VisualizarRolesPermisosView(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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


class RolConPermisosAutocomplete(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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

class RolesPermisosEditView(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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


class PermisoParaRolAutocomplete(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
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


class UsuarioPermisoAssignView(SyncPermissionCatalogMixin, LoginRequiredMixin, View):
    template_name = "usuarios_permisos.html"

    def _selected_user(self, request):
        raw_id = (request.POST.get("usuario") or request.GET.get("usuario") or "").strip()
        if not raw_id.isdigit():
            return None
        return Usuario.objects.select_related("rolid").filter(pk=int(raw_id)).first()

    def _context(self, request, selected_user=None):
        usuarios = Usuario.objects.select_related("rolid").order_by("nombreusuario")
        permisos = list(Permiso.objects.order_by("nombre"))
        selected_user = selected_user or self._selected_user(request)

        direct_by_perm = {}
        role_perm_ids = set()
        if selected_user:
            direct_by_perm = {
                row.permiso_id: row.permitido
                for row in UsuarioPermiso.objects.filter(usuario=selected_user)
            }
            role_id = getattr(selected_user, "rolid_id", None)
            if role_id:
                role_perm_ids = set(
                    RolPermiso.objects
                    .filter(rol_id=role_id)
                    .values_list("permiso_id", flat=True)
                )

        permission_rows = []
        for permiso in permisos:
            estado = "heredar"
            if permiso.pk in direct_by_perm:
                estado = "permitir" if direct_by_perm[permiso.pk] else "bloquear"
            permission_rows.append({
                "permiso": permiso,
                "estado": estado,
                "role_has": permiso.pk in role_perm_ids,
            })

        return {
            "usuarios": usuarios,
            "selected_user": selected_user,
            "permission_rows": permission_rows,
            "catalog_count": len(permission_catalog()),
        }

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    @transaction.atomic
    def post(self, request):
        selected_user = self._selected_user(request)
        if not selected_user:
            messages.error(request, "Seleccione un usuario valido.")
            return render(request, self.template_name, self._context(request), status=400)

        permisos = Permiso.objects.only("pk")
        valid_ids = {str(permiso.pk) for permiso in permisos}
        UsuarioPermiso.objects.filter(usuario=selected_user).delete()

        nuevos = []
        for key, value in request.POST.items():
            if not key.startswith("permiso_"):
                continue
            permiso_id = key.split("_", 1)[1]
            if permiso_id not in valid_ids or value not in {"permitir", "bloquear"}:
                continue
            nuevos.append(UsuarioPermiso(
                usuario=selected_user,
                permiso_id=int(permiso_id),
                permitido=value == "permitir",
            ))

        if nuevos:
            UsuarioPermiso.objects.bulk_create(nuevos, ignore_conflicts=True)

        clear_permission_cache(selected_user)
        messages.success(
            request,
            f"Permisos directos actualizados para {selected_user.nombreusuario}.",
        )
        return redirect(f"{reverse('usuarios_permisos')}?usuario={selected_user.pk}")

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
        pid  = request.GET.get("puntopago_id")          # numérico o "ALL"
        f    = request.GET.get("fecha")                  # fecha desde
        f_to = request.GET.get("fecha_hasta")            # ✅ nuevo: opcional, define rango
        modo = (request.GET.get("modo") or "TOTAL").upper().strip()

        # nuevas horas
        h_desde = request.GET.get("hora_desde")  # HH:MM o HH:MM:SS
        h_hasta = request.GET.get("hora_hasta")

        if not (sid and pid and f):
            return JsonResponse({"success": False, "error": "Parámetros incompletos."}, status=400)

        suc = get_object_or_404(Sucursal, pk=sid)

        # ✅ Punto de pago: 'ALL' suma todos los puntos de la sucursal
        is_all_pp = (str(pid).strip().upper() == "ALL")
        pp = None
        if not is_all_pp:
            pp = get_object_or_404(PuntosPago, pk=pid, sucursalid=suc)

        # fecha desde yyyy-mm-dd
        try:
            fecha_desde = datetime.fromisoformat(f).date()
        except Exception:
            return JsonResponse({"success": False, "error": "Fecha inválida."}, status=400)

        # ✅ fecha hasta (opcional). Si no llega, equivale a la misma fecha (un solo día).
        fecha_hasta = fecha_desde
        if f_to:
            try:
                fecha_hasta = datetime.fromisoformat(f_to).date()
            except Exception:
                return JsonResponse({"success": False, "error": "Fecha hasta inválida."}, status=400)
            if fecha_hasta < fecha_desde:
                return JsonResponse({"success": False, "error": "fecha_hasta no puede ser menor que fecha."}, status=400)
            # Cota dura para evitar consultas demasiado amplias.
            if (fecha_hasta - fecha_desde).days > 366:
                return JsonResponse({"success": False, "error": "Rango de fechas demasiado amplio (máx. 366 días)."}, status=400)

        # Base: ventas de la sucursal (y, si aplica, del punto de pago)
        ventas_qs = Venta.objects.filter(sucursalid=suc)
        if pp is not None:
            ventas_qs = ventas_qs.filter(puntopagoid=pp)

        # Filtro por fecha (un día o rango cerrado)
        if fecha_desde == fecha_hasta:
            ventas_qs = ventas_qs.filter(fecha=fecha_desde)
        else:
            ventas_qs = ventas_qs.filter(fecha__range=(fecha_desde, fecha_hasta))

        # --------- aplicar intervalo de horas (CERRADO, por día) ----------
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
                if fecha_desde == fecha_hasta:
                    # Un solo día: usamos rango de DateTime aware (comportamiento anterior)
                    tz = timezone.get_current_timezone()
                    dt_from = timezone.make_aware(datetime.combine(fecha_desde, t_from), tz)
                    dt_to   = timezone.make_aware(datetime.combine(fecha_desde, t_to), tz)
                    ventas_qs = ventas_qs.filter(**{
                        f"{dt_field}__gte": dt_from,
                        f"{dt_field}__lte": dt_to,
                    })
                else:
                    # Rango: el filtro por hora aplica a cada día del rango.
                    ventas_qs = ventas_qs.filter(**{
                        f"{dt_field}__time__gte": t_from,
                        f"{dt_field}__time__lte": t_to,
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


NEQUI_NOTIFICATION_LIMIT = 80


def _nequi_field(payload, *names):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _nequi_plain_text(value):
    value = str(value or "")
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def _normalize_money_value(raw_value):
    raw_value = (raw_value or "").strip()
    raw_value = re.sub(r"[^\d,.]", "", raw_value)
    if not raw_value:
        return None

    last_comma = raw_value.rfind(",")
    last_dot = raw_value.rfind(".")
    last_sep = max(last_comma, last_dot)
    if last_sep > -1:
        decimals = len(raw_value) - last_sep - 1
        sep = raw_value[last_sep]
        if decimals == 2 and raw_value.count(sep) == 1:
            whole = re.sub(r"[^\d]", "", raw_value[:last_sep]) or "0"
            cents = re.sub(r"[^\d]", "", raw_value[last_sep + 1:])
            return Decimal(f"{whole}.{cents}").quantize(Decimal("0.01"))

    digits = re.sub(r"[^\d]", "", raw_value)
    if not digits:
        return None
    return Decimal(digits).quantize(Decimal("0.01"))


def _parse_nequi_amount(text):
    text = text or ""
    search_text = _nequi_plain_text(text)
    patterns = [
        r"(?:\$|cop\s*)\s*([0-9][0-9.,]*)",
        r"(?:recibiste|enviaron|envio|depositaron|pagaron|pago|por)\s+(?:de\s+)?([0-9][0-9.,]*)",
        r"([0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]{2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            amount = _normalize_money_value(match.group(1))
        except (InvalidOperation, ValueError):
            amount = None
        if amount is not None:
            return amount
    return None


def _parse_nequi_sender(text):
    text = text or ""
    direct = re.search(r"^\s*(.+?)\s+te\s+envi[oó]\b", text, flags=re.IGNORECASE)
    if direct:
        sender = re.sub(r"\s+", " ", direct.group(1)).strip()
        return sender[:160]

    match = re.search(r"(?:\bde|\bdesde)\s+([^,.\n]{3,100})", text, flags=re.IGNORECASE)
    if not match:
        return ""
    sender = re.sub(r"\s+", " ", match.group(1)).strip()
    return sender[:160]


def _parse_nequi_sender_plain(text):
    text = text or ""
    plain_text = _nequi_plain_text(text)
    direct = re.search(r"^\s*(.+?)\s+te\s+envio\b", plain_text, flags=re.IGNORECASE)
    if direct:
        sender = re.sub(r"\s+", " ", text[: direct.end(1)]).strip()
        return sender[:160]

    match = re.search(r"(?:\bde|\bdesde)\s+([^,.\n]{3,100})", plain_text, flags=re.IGNORECASE)
    if not match:
        return ""
    sender = re.sub(r"\s+", " ", text[match.start(1): match.end(1)]).strip()
    return sender[:160]


def _parse_nequi_reference(text):
    text = text or ""
    match = re.search(
        r"(?:referencia|ref\.?|codigo|transaccion)\s*[:#-]?\s*([A-Za-z0-9-]{4,80})",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1)[:120] if match else ""


def _parse_nequi_received_at(payload):
    raw_value = _nequi_field(payload, "received_at", "fecha", "timestamp", "notification_time", "time")
    if raw_value:
        parsed = parse_datetime(raw_value)
        if parsed:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, CO_TZ)
            return parsed
        parsed_date = parse_date(raw_value)
        if parsed_date:
            return timezone.make_aware(datetime.combine(parsed_date, time.min), CO_TZ)
    return timezone.now()


def _make_nequi_fingerprint(payload, title, text, package, received_at):
    explicit_id = _nequi_field(payload, "id", "notification_id", "event_id", "macro_id")
    raw_time = _nequi_field(payload, "received_at", "fecha", "timestamp", "notification_time", "time")
    unique_part = explicit_id or raw_time
    if not unique_part:
        unique_part = received_at.isoformat(timespec="microseconds")
    base = f"{package}|{title}|{text}|{unique_part}"
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


def _looks_like_nequi_payment(title, text, amount):
    if amount is None:
        return False
    plain_text = _nequi_plain_text(f"{title} {text}")
    payment_markers = (
        "te envio",
        "te enviaron",
        "recibiste",
        "recibido",
        "depositaron",
        "pagaron",
        "transferencia",
        "pago",
    )
    return any(marker in plain_text for marker in payment_markers)


def _nequi_item_json(item):
    recibido = _as_co(item.recibido_en)
    return {
        "id": item.notificacionid,
        "titulo": item.titulo,
        "texto": item.texto,
        "app": item.app,
        "paquete": item.paquete,
        "monto": str(item.monto) if item.monto is not None else "",
        "remitente": item.remitente,
        "referencia": item.referencia,
        "fecha": recibido.strftime("%Y-%m-%d"),
        "hora": recibido.strftime("%I:%M %p").lower(),
        "iso": recibido.isoformat(),
        "venta_id": item.venta_id,
        "usada": bool(item.venta_id),
    }


def _nequi_sender_names(sender):
    parts = [p for p in re.split(r"\s+", str(sender or "").strip()) if p]
    return {
        "nombre": parts[0] if len(parts) >= 1 else "",
        "segundo_nombre": parts[1] if len(parts) >= 2 else "",
    }


def _nequi_sale_item_json(item):
    data = _nequi_item_json(item)
    names = _nequi_sender_names(item.remitente)
    data.update({
        "nombre": names["nombre"],
        "segundo_nombre": names["segundo_nombre"],
        "monto_num": float(item.monto or 0),
        "monto_label": f"$ {int(item.monto or 0):,}".replace(",", "."),
    })
    return data


def _nequi_summary():
    today = timezone.localdate()
    today_qs = NotificacionNequi.objects.filter(recibido_en__date=today)
    total = today_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    last_item = NotificacionNequi.objects.order_by("-recibido_en", "-notificacionid").first()
    return {
        "hoy_total": str(total.quantize(Decimal("0.01"))),
        "hoy_count": today_qs.count(),
        "ultima_hora": _as_co(last_item.recibido_en).strftime("%I:%M %p").lower() if last_item else "",
        "ultima_monto": str(last_item.monto) if last_item and last_item.monto is not None else "",
    }


class NequiNotificacionesView(LoginRequiredMixin, TemplateView):
    template_name = "nequi_notificaciones.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = NotificacionNequi.objects.order_by("-recibido_en", "-notificacionid")[:NEQUI_NOTIFICATION_LIMIT]
        context["notificaciones"] = items
        context["resumen_nequi"] = _nequi_summary()
        return context


class NequiNotificacionesDataView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        items = NotificacionNequi.objects.order_by("-recibido_en", "-notificacionid")[:NEQUI_NOTIFICATION_LIMIT]
        return JsonResponse({
            "success": True,
            "summary": _nequi_summary(),
            "items": [_nequi_item_json(item) for item in items],
        })


class NequiNotificacionEliminarView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, notificacion_id, *args, **kwargs):
        item = get_object_or_404(NotificacionNequi, pk=notificacion_id)
        if item.venta_id:
            return JsonResponse({
                "success": False,
                "error": "Esta notificacion ya fue usada en una venta y no se puede eliminar desde aqui.",
            }, status=409)

        item.delete()
        return JsonResponse({
            "success": True,
            "summary": _nequi_summary(),
        })


class NequiNotificacionesEliminarSeleccionadasView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        ids = []
        content_type = (request.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            raw_ids = payload.get("ids", [])
        else:
            raw_ids = request.POST.getlist("ids[]") or request.POST.getlist("ids") or []

        for value in raw_ids:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                ids.append(parsed)

        ids = list(dict.fromkeys(ids))
        if not ids:
            return JsonResponse({
                "success": False,
                "error": "No seleccionaste notificaciones para eliminar.",
            }, status=400)

        qs = NotificacionNequi.objects.filter(pk__in=ids)
        protected_count = qs.filter(venta__isnull=False).count()
        deleted_count, _ = qs.filter(venta__isnull=True).delete()

        return JsonResponse({
            "success": True,
            "deleted": deleted_count,
            "protected": protected_count,
            "summary": _nequi_summary(),
        })


class NequiNotificacionesDisponiblesView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        items = (
            NotificacionNequi.objects
            .filter(venta__isnull=True, monto__isnull=False, monto__gt=0)
            .order_by("-recibido_en", "-notificacionid")[:120]
        )
        return JsonResponse({
            "success": True,
            "items": [_nequi_sale_item_json(item) for item in items],
        })


@method_decorator(csrf_exempt, name="dispatch")
class NequiNotificationWebhookView(View):
    http_method_names = ["post"]

    def _payload(self, request):
        payload = request.GET.dict()
        content_type = (request.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                data = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON invalido: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("El cuerpo JSON debe ser un objeto.")
            payload.update(data)
            return payload
        form_data = request.POST.dict()
        if form_data:
            payload.update(form_data)
            return payload

        raw_body = (request.body or b"").decode("utf-8", errors="ignore").strip()
        if raw_body:
            if raw_body.startswith("{"):
                try:
                    data = json.loads(raw_body)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSON invalido: {exc}") from exc
                if not isinstance(data, dict):
                    raise ValueError("El cuerpo JSON debe ser un objeto.")
                payload.update(data)
            elif "=" in raw_body:
                payload.update(dict(parse_qsl(raw_body, keep_blank_values=True)))
            else:
                payload["raw_text"] = raw_body
        return payload

    def _request_token(self, request, payload):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        return (
            request.headers.get("X-Macrodroid-Token")
            or request.headers.get("X-MacroDroid-Token")
            or request.GET.get("token")
            or payload.get("token")
            or ""
        )

    def post(self, request, *args, **kwargs):
        configured_token = getattr(settings, "MACRODROID_NEQUI_TOKEN", "")
        if not configured_token:
            return JsonResponse({
                "success": False,
                "error": "MACRODROID_NEQUI_TOKEN no esta configurado en el servidor.",
            }, status=503)

        try:
            payload = self._payload(request)
        except ValueError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        request_token = str(self._request_token(request, payload)).strip()
        if not hmac.compare_digest(request_token, configured_token):
            return JsonResponse({"success": False, "error": "Token invalido."}, status=403)

        title = _nequi_field(
            payload,
            "title", "titulo", "notification_title", "not_title", "subject",
        )
        text = _nequi_field(
            payload,
            "text", "texto", "body", "message", "notification_text", "not_text",
            "notification_body", "notification_message", "big_text", "not_big_text",
            "not_text_lines", "ticker", "not_ticker", "content", "raw_text",
        )
        app_name = _nequi_field(
            payload,
            "app", "application", "application_name", "notification_app_name",
            "notification_app", "app_name", "not_app_name", "not_application_name",
        )
        package = _nequi_field(
            payload,
            "package", "package_name", "notification_package", "notification_package_name",
            "app_package", "not_package", "not_package_name",
        )

        amount = _parse_nequi_amount(f"{title} {text}")
        joined = _nequi_plain_text(f"{title} {text} {app_name} {package}")
        if "nequi" not in joined and not _looks_like_nequi_payment(title, text, amount):
            return JsonResponse({
                "success": True,
                "ignored": True,
                "reason": "La notificacion no parece venir de Nequi.",
            }, status=202)

        if not text and not title:
            return JsonResponse({"success": False, "error": "La notificacion llego sin titulo ni texto."}, status=400)

        received_at = _parse_nequi_received_at(payload)
        sender = _nequi_field(payload, "sender", "remitente") or _parse_nequi_sender_plain(text)
        reference = _nequi_field(payload, "reference", "referencia") or _parse_nequi_reference(text)
        fingerprint = _make_nequi_fingerprint(payload, title, text, package, received_at)
        safe_payload = {key: value for key, value in payload.items() if key.lower() != "token"}

        notification, created = NotificacionNequi.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={
                "titulo": title[:180],
                "texto": text or title,
                "app": app_name[:120],
                "paquete": package[:160],
                "monto": amount,
                "remitente": sender[:160],
                "referencia": reference[:120],
                "recibido_en": received_at,
                "raw_payload": safe_payload,
            },
        )

        return JsonResponse({
            "success": True,
            "created": created,
            "item": _nequi_item_json(notification),
        }, status=201 if created else 200)




class MetricasNegocioView(LoginRequiredMixin, View):
    template_name = "metricas_negocio.html"

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        return render(request, self.template_name, {
            "fecha_desde_default": (today - timedelta(days=30)).isoformat(),
            "fecha_hasta_default": today.isoformat(),
            "sucursales": Sucursal.objects.order_by("nombre"),
            "puntos_pago": PuntosPago.objects.select_related("sucursalid").order_by("sucursalid__nombre", "nombre"),
        })


class MetricasNegocioDataView(LoginRequiredMixin, View):
    MAX_RANGE_DAYS = 731

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Exception as exc:
            logger.exception("Error calculando metricas del negocio")
            return JsonResponse({
                "success": False,
                "error": f"No se pudieron calcular las metricas: {exc}",
            }, status=500)

    @staticmethod
    def _dec(value):
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _int(value):
        try:
            return int(value or 0)
        except Exception:
            return 0

    @staticmethod
    def _pct(current, previous):
        current = Decimal(str(current or 0))
        previous = Decimal(str(previous or 0))
        if previous == 0:
            return None if current == 0 else 100.0
        return float(((current - previous) / previous * Decimal("100")).quantize(Decimal("0.01")))

    @staticmethod
    def _avg_decimal(value, divisor):
        if not divisor:
            return Decimal("0")
        return (Decimal(str(value or 0)) / Decimal(divisor)).quantize(Decimal("0.01"))

    @staticmethod
    def _parse_date_param(value, default):
        parsed = parse_date(value) if value else None
        return parsed or default

    def _filters(self, request):
        today = timezone.localdate()
        start = self._parse_date_param(request.GET.get("desde"), today - timedelta(days=30))
        end = self._parse_date_param(request.GET.get("hasta"), today)
        if end < start:
            start, end = end, start
        if (end - start).days > self.MAX_RANGE_DAYS:
            raise ValueError("El rango maximo permitido es de 731 dias.")

        sucursal_id = (request.GET.get("sucursal_id") or "").strip()
        puntopago_id = (request.GET.get("puntopago_id") or "").strip()
        if sucursal_id and not sucursal_id.isdigit():
            raise ValueError("Sucursal invalida.")
        if puntopago_id and puntopago_id.upper() != "ALL" and not puntopago_id.isdigit():
            raise ValueError("Punto de pago invalido.")

        return {
            "start": start,
            "end": end,
            "sucursal_id": int(sucursal_id) if sucursal_id else None,
            "puntopago_id": int(puntopago_id) if puntopago_id and puntopago_id.upper() != "ALL" else None,
        }

    @staticmethod
    def _sales_qs(start, end, sucursal_id=None, puntopago_id=None):
        qs = Venta.objects.filter(fecha__range=(start, end))
        if sucursal_id:
            qs = qs.filter(sucursalid_id=sucursal_id)
        if puntopago_id:
            qs = qs.filter(puntopagoid_id=puntopago_id)
        return qs

    def _period_summary(self, ventas_qs):
        agg = ventas_qs.aggregate(total=Sum("total"), ventas=Count("ventaid"))
        total = agg["total"] or Decimal("0")
        ventas = agg["ventas"] or 0
        promedio = (total / ventas).quantize(Decimal("0.01")) if ventas else Decimal("0")

        ventas_ids = ventas_qs.values("ventaid")
        detalle_qs = DetalleVenta.objects.filter(ventaid__in=ventas_ids, cantidad__gt=0)
        unidades = detalle_qs.aggregate(unidades=Sum("cantidad"))["unidades"] or 0

        pagos_qs = PagoVenta.objects.filter(ventaid__in=ventas_ids)
        efectivo = pagos_qs.filter(medio_pago__iexact="efectivo").aggregate(total=Sum("monto"))["total"] or Decimal("0")
        total_pagos = pagos_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
        if total_pagos == 0 and total > 0:
            medio_rows = ventas_qs.values("mediopago").annotate(total=Sum("total"))
            efectivo = sum(
                (row["total"] or Decimal("0"))
                for row in medio_rows
                if (row["mediopago"] or "").strip().lower() == "efectivo"
            )
            total_pagos = total

        return {
            "total_sales": total,
            "sale_count": ventas,
            "avg_ticket": promedio,
            "units_sold": unidades,
            "cash_total": efectivo,
            "non_cash_total": max(total_pagos - efectivo, Decimal("0")),
            "active_customers": ventas_qs.exclude(clienteid__isnull=True).values("clienteid").distinct().count(),
        }

    def _inventory_summary(self, sucursal_id=None):
        qs = Inventario.objects.select_related("productoid", "sucursalid")
        if sucursal_id:
            qs = qs.filter(sucursalid_id=sucursal_id)

        value_expr = ExpressionWrapper(
            F("cantidad") * F("productoid__precio"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
        inv_value = qs.filter(cantidad__gt=0).aggregate(total=Sum(value_expr))["total"] or Decimal("0")
        low_qs = qs.filter(cantidad__lte=5).order_by("cantidad", "productoid__nombre")

        return {
            "inventory_value": inv_value,
            "low_stock_count": low_qs.count(),
            "negative_stock_count": qs.filter(cantidad__lt=0).count(),
            "low_stock": [
                {
                    "producto": item.productoid.nombre,
                    "sucursal": item.sucursalid.nombre,
                    "cantidad": item.cantidad,
                }
                for item in low_qs[:12]
            ],
        }

    def get(self, request, *args, **kwargs):
        try:
            filters = self._filters(request)
        except ValueError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        start = filters["start"]
        end = filters["end"]
        sucursal_id = filters["sucursal_id"]
        puntopago_id = filters["puntopago_id"]

        ventas_qs = self._sales_qs(start, end, sucursal_id, puntopago_id)
        ventas_ids = ventas_qs.values("ventaid")
        summary_raw = self._period_summary(ventas_qs)

        period_days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)
        previous_raw = self._period_summary(self._sales_qs(prev_start, prev_end, sucursal_id, puntopago_id))
        inventory_raw = self._inventory_summary(sucursal_id)

        pedidos_qs = PedidoProveedor.objects.filter(fechapedido__range=(start, end))
        if sucursal_id:
            pedidos_qs = pedidos_qs.filter(sucursalid_id=sucursal_id)
        pedidos_agg = pedidos_qs.aggregate(total=Sum("costototal"), cantidad=Count("pedidoid"))

        cambios_qs = CambioDevolucion.objects.filter(fecha__range=(start, end))
        if sucursal_id:
            cambios_qs = cambios_qs.filter(Q(venta__sucursalid_id=sucursal_id) | Q(venta__isnull=True))
        if puntopago_id:
            cambios_qs = cambios_qs.filter(Q(venta__puntopagoid_id=puntopago_id) | Q(venta__isnull=True))

        line_total = ExpressionWrapper(
            F("cantidad") * F("preciounitario"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )

        daily_map = {
            row["fecha"].isoformat(): row
            for row in ventas_qs.values("fecha").annotate(total=Sum("total"), ventas=Count("ventaid")).order_by("fecha")
        }
        daily = []
        weekday_occurrences = {day: 0 for day in range(1, 8)}
        month_day_occurrences = {day: 0 for day in range(1, 32)}
        cursor = start
        while cursor <= end:
            row = daily_map.get(cursor.isoformat(), {})
            daily.append({
                "label": cursor.isoformat(),
                "total": self._dec(row.get("total")),
                "ventas": self._int(row.get("ventas")),
            })
            weekday_occurrences[cursor.isoweekday()] += 1
            month_day_occurrences[cursor.day] += 1
            cursor += timedelta(days=1)

        hours_map = {
            self._int(row["hour"]): row
            for row in ventas_qs.annotate(hour=ExtractHour("hora"))
            .values("hour")
            .annotate(total=Sum("total"), ventas=Count("ventaid"))
            .order_by("hour")
        }
        by_hour = [
            {
                "label": f"{hour:02d}:00",
                "total": self._dec(hours_map.get(hour, {}).get("total")),
                "ventas": self._int(hours_map.get(hour, {}).get("ventas")),
            }
            for hour in range(24)
        ]

        weekday_labels = {
            1: "Lunes",
            2: "Martes",
            3: "Miercoles",
            4: "Jueves",
            5: "Viernes",
            6: "Sabado",
            7: "Domingo",
        }
        weekday_map = {
            self._int(row["weekday"]): row
            for row in ventas_qs.annotate(weekday=ExtractIsoWeekDay("fecha"))
            .values("weekday")
            .annotate(total=Sum("total"), ventas=Count("ventaid"))
            .order_by("weekday")
        }
        by_weekday = [
            {
                "day": day,
                "label": weekday_labels[day],
                "occurrences": weekday_occurrences.get(day, 0),
                "total": self._dec(weekday_map.get(day, {}).get("total")),
                "average_total": self._dec(self._avg_decimal(
                    weekday_map.get(day, {}).get("total"),
                    weekday_occurrences.get(day, 0),
                )),
                "ventas": self._int(weekday_map.get(day, {}).get("ventas")),
                "average_sales": self._dec(self._avg_decimal(
                    weekday_map.get(day, {}).get("ventas"),
                    weekday_occurrences.get(day, 0),
                )),
            }
            for day in range(1, 8)
        ]

        month_day_map = {
            self._int(row["month_day"]): row
            for row in ventas_qs.annotate(month_day=ExtractDay("fecha"))
            .values("month_day")
            .annotate(total=Sum("total"), ventas=Count("ventaid"))
            .order_by("month_day")
        }
        by_month_day = [
            {
                "day": day,
                "label": str(day),
                "occurrences": month_day_occurrences.get(day, 0),
                "total": self._dec(month_day_map.get(day, {}).get("total")),
                "average_total": self._dec(self._avg_decimal(
                    month_day_map.get(day, {}).get("total"),
                    month_day_occurrences.get(day, 0),
                )),
                "ventas": self._int(month_day_map.get(day, {}).get("ventas")),
                "average_sales": self._dec(self._avg_decimal(
                    month_day_map.get(day, {}).get("ventas"),
                    month_day_occurrences.get(day, 0),
                )),
            }
            for day in range(1, 32)
            if month_day_occurrences.get(day, 0) > 0
        ]

        payments = [
            {
                "label": (row["medio_pago"] or "sin_pago").replace("_", " ").title(),
                "total": self._dec(row["total"]),
                "cantidad": self._int(row["cantidad"]),
            }
            for row in PagoVenta.objects.filter(ventaid__in=ventas_ids)
            .values("medio_pago")
            .annotate(total=Sum("monto"), cantidad=Count("id"))
            .order_by("-total")
        ]
        if not payments:
            payments = [
                {
                    "label": (row["mediopago"] or "sin_pago").replace("_", " ").title(),
                    "total": self._dec(row["total"]),
                    "cantidad": self._int(row["cantidad"]),
                }
                for row in ventas_qs.values("mediopago").annotate(total=Sum("total"), cantidad=Count("ventaid")).order_by("-total")
            ]

        top_products = [
            {
                "producto": row["productoid__nombre"] or "Sin nombre",
                "cantidad": self._int(row["cantidad"]),
                "total": self._dec(row["total"]),
            }
            for row in DetalleVenta.objects.filter(ventaid__in=ventas_ids, cantidad__gt=0)
            .annotate(line_total=line_total)
            .values("productoid__nombre")
            .annotate(cantidad=Sum("cantidad"), total=Sum("line_total"))
            .order_by("-total", "-cantidad")[:12]
        ]

        categories = [
            {
                "label": row["productoid__categoria__nombre"] or "Sin categoria",
                "total": self._dec(row["total"]),
                "cantidad": self._int(row["cantidad"]),
            }
            for row in DetalleVenta.objects.filter(ventaid__in=ventas_ids, cantidad__gt=0)
            .annotate(line_total=line_total)
            .values("productoid__categoria__nombre")
            .annotate(cantidad=Sum("cantidad"), total=Sum("line_total"))
            .order_by("-total")[:10]
        ]

        cashiers = [
            {
                "nombre": f"{row['empleadoid__nombre'] or ''} {row['empleadoid__apellido'] or ''}".strip() or "Sin cajero",
                "ventas": self._int(row["ventas"]),
                "total": self._dec(row["total"]),
                "promedio": self._dec((row["total"] or Decimal("0")) / row["ventas"]) if row["ventas"] else 0,
            }
            for row in ventas_qs.values("empleadoid__nombre", "empleadoid__apellido")
            .annotate(total=Sum("total"), ventas=Count("ventaid"))
            .order_by("-total")[:10]
        ]

        pedidos_estado = [
            {
                "estado": row["estado"] or "Sin estado",
                "cantidad": self._int(row["cantidad"]),
                "total": self._dec(row["total"]),
            }
            for row in pedidos_qs.values("estado").annotate(cantidad=Count("pedidoid"), total=Sum("costototal")).order_by("-total")
        ]

        cambios_estado = [
            {
                "tipo": row["tipo"] or "Sin tipo",
                "estado": row["estado"] or "Sin estado",
                "cantidad": self._int(row["cantidad"]),
            }
            for row in cambios_qs.values("tipo", "estado").annotate(cantidad=Count("cambioid")).order_by("tipo", "estado")
        ]

        summary = {
            **{key: self._dec(value) if isinstance(value, Decimal) else value for key, value in summary_raw.items()},
            "inventory_value": self._dec(inventory_raw["inventory_value"]),
            "low_stock_count": inventory_raw["low_stock_count"],
            "negative_stock_count": inventory_raw["negative_stock_count"],
            "orders_total": self._dec(pedidos_agg["total"]),
            "orders_count": pedidos_agg["cantidad"] or 0,
            "returns_count": cambios_qs.count(),
        }

        return JsonResponse({
            "success": True,
            "filters": {
                "desde": start.isoformat(),
                "hasta": end.isoformat(),
                "dias": period_days,
                "comparacion_desde": prev_start.isoformat(),
                "comparacion_hasta": prev_end.isoformat(),
            },
            "summary": summary,
            "comparison": {
                "total_sales_pct": self._pct(summary_raw["total_sales"], previous_raw["total_sales"]),
                "sale_count_pct": self._pct(summary_raw["sale_count"], previous_raw["sale_count"]),
                "avg_ticket_pct": self._pct(summary_raw["avg_ticket"], previous_raw["avg_ticket"]),
            },
            "charts": {
                "daily": daily,
                "by_hour": by_hour,
                "by_weekday": by_weekday,
                "by_month_day": by_month_day,
                "payments": payments,
                "top_products": top_products[:10],
                "categories": categories,
            },
            "tables": {
                "top_products": top_products,
                "cashiers": cashiers,
                "low_stock": inventory_raw["low_stock"],
                "by_weekday": by_weekday,
                "by_month_day": by_month_day,
                "orders": pedidos_estado,
                "returns": cambios_estado,
            },
        })


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

FACTURAS_PAGADAS_METODO = "facturas_pagadas"

def _now_co():
    return timezone.now().astimezone(CO_TZ)

def _to_decimal(v, default=Decimal("0")) -> Decimal:
    try:
        if v is None or str(v).strip() == "":
            return default
        s = str(v).strip().replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif "." in s and s.rsplit(".", 1)[-1].isdigit() and len(s.rsplit(".", 1)[-1]) == 3:
            s = s.replace(".", "")
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return default

def _normalize_metodo(s: str) -> str:
    s = (s or "").strip().lower()
    s = " ".join(s.split())
    s = s.replace("-", " ").replace("_", " ")
    aliases = {
        "ef": "efectivo",
        "cash": "efectivo",
        "efectivo": "efectivo",
        "nequi": "nequi",
        "davi": "daviplata",
        "davi plata": "daviplata",
        "daviplata": "daviplata",
        "tarjeta": "tarjeta",
        "card": "tarjeta",
        "tc": "tarjeta",
        "credito": "tarjeta",
        "debito": "tarjeta",
        "tarjeta credito": "tarjeta",
        "tarjeta debito": "tarjeta",
        "banco caja social": "banco_caja_social",
        "caja social": "banco_caja_social",
        "bcs": "banco_caja_social",
    }
    return aliases.get(s, s.replace(" ", "_"))

def _turno_label_usuario(usuario) -> str:
    return getattr(usuario, "nombreusuario", None) or str(getattr(usuario, "pk", "") or usuario)

def _can_operate_cajero(user, cajero) -> bool:
    return _require_admin(user) or getattr(user, "pk", None) == getattr(cajero, "pk", None)

def _can_operate_turno(user, turno) -> bool:
    return _require_admin(user) or getattr(user, "pk", None) == getattr(turno, "cajero_id", None)

def _turno_identity_payload(turno):
    return {
        "id": turno.id,
        "estado": turno.estado,
        "inicio": _iso_dt(turno.inicio),
        "cierre_iniciado": _iso_dt(turno.cierre_iniciado),
        "base": float(turno.saldo_apertura_efectivo or 0),
        "puntopago": {
            "id": turno.puntopago_id,
            "nombre": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
        },
        "cajero": {
            "id": turno.cajero_id,
            "nombreusuario": _turno_label_usuario(turno.cajero),
        },
    }

def _medios_payload(turno, auto_confirmados=None):
    auto_confirmados = auto_confirmados or {}
    medios = []
    for m in TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"):
        metodo = _normalize_metodo(m.metodo)
        if metodo == FACTURAS_PAGADAS_METODO:
            continue
        auto_confirmado = auto_confirmados.get(metodo, Decimal("0.00")) or Decimal("0.00")
        medios.append({
            "metodo": metodo,
            "label": DISPLAY_METODO.get(metodo, metodo.replace("_", " ").title()),
            "esperado": float(m.esperado or 0),
            "contado": float(m.contado) if m.contado is not None else None,
            "diferencia": float(m.diferencia or 0),
            "auto_confirmado": float(auto_confirmado),
        })
    return medios

def _sync_turno_medios_esperados(turno, expected: dict[str, Decimal], reset_contados=False):
    existing_objs = {
        _normalize_metodo(m.metodo): m
        for m in TurnoCajaMedio.objects.filter(turno=turno)
    }
    existing = {metodo: obj.metodo for metodo, obj in existing_objs.items()}
    all_methods = list(dict.fromkeys(DEFAULT_METODOS + list(expected.keys()) + list(existing.keys())))

    missing = [
        TurnoCajaMedio(
            turno=turno,
            metodo=metodo,
            esperado=Decimal("0.00"),
            contado=None,
            diferencia=Decimal("0.00"),
        )
        for metodo in all_methods
        if metodo not in existing_objs
    ]
    if missing:
        TurnoCajaMedio.objects.bulk_create(missing, ignore_conflicts=True, batch_size=100)
        existing_objs = {
            _normalize_metodo(m.metodo): m
            for m in TurnoCajaMedio.objects.filter(turno=turno)
        }

    to_update = []
    for metodo in all_methods:
        metodo = _normalize_metodo(metodo)
        medio = existing_objs.get(metodo)
        if medio is None:
            continue
        created = metodo not in existing
        medio.esperado = (expected.get(metodo, Decimal("0.00")) or Decimal("0.00")).quantize(Decimal("0.01"))
        if reset_contados or created:
            medio.contado = None
            medio.diferencia = Decimal("0.00")
        elif medio.contado is not None:
            medio.diferencia = ((medio.contado or Decimal("0.00")) - medio.esperado).quantize(Decimal("0.01"))
        else:
            medio.diferencia = Decimal("0.00")
        to_update.append(medio)

    if to_update:
        TurnoCajaMedio.objects.bulk_update(
            to_update,
            ["esperado", "contado", "diferencia"],
            batch_size=100,
        )

    return all_methods

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
    Fallback: ventas.mediopago + ventas.total para ventas sin filas en venta_pagos.
    """
    sql = """
        SELECT lower(trim(v.mediopago)) as metodo, COALESCE(SUM(v.total),0) as total
        FROM ventas v
        WHERE v.puntopagoid = %s
          AND (v.fecha + v.hora) >= %s
          AND (v.fecha + v.hora) <= %s
          AND NOT EXISTS (
              SELECT 1
              FROM venta_pagos vp
              WHERE vp.ventaid = v.ventaid
          )
        GROUP BY lower(trim(v.mediopago))
    """
    out: dict[str, Decimal] = {}
    with connection.cursor() as cur:
        cur.execute(sql, [puntopago_id, start_naive, end_naive])
        for metodo_raw, total in cur.fetchall():
            m = _normalize_metodo(metodo_raw)
            out[m] = _to_decimal(total)
    return out

def _sum_nequi_confirmado_api(turno: TurnoCaja) -> Decimal:
    """
    Nequi confirmado por MacroDroid/API dentro del intervalo del turno.
    Sumamos el valor del pago registrado en la venta, no el monto bruto de la
    notificacion, para que el cierre cuadre contra lo vendido.
    """
    if not turno.cierre_iniciado:
        return Decimal("0.00")

    start_naive, end_naive = _range_local_naive(turno, turno.cierre_iniciado)

    sql_pagos = """
        SELECT COALESCE(SUM(vp.monto),0) as total
        FROM venta_pagos vp
        JOIN ventas v ON v.ventaid = vp.ventaid
        WHERE v.puntopagoid = %s
          AND (v.fecha + v.hora) >= %s
          AND (v.fecha + v.hora) <= %s
          AND lower(trim(vp.metodo)) = 'nequi'
          AND EXISTS (
              SELECT 1
              FROM notificaciones_nequi nn
              WHERE nn.ventaid = v.ventaid
          )
    """
    sql_fallback = """
        SELECT COALESCE(SUM(v.total),0) as total
        FROM ventas v
        WHERE v.puntopagoid = %s
          AND (v.fecha + v.hora) >= %s
          AND (v.fecha + v.hora) <= %s
          AND lower(trim(v.mediopago)) = 'nequi'
          AND NOT EXISTS (
              SELECT 1
              FROM venta_pagos vp
              WHERE vp.ventaid = v.ventaid
          )
          AND EXISTS (
              SELECT 1
              FROM notificaciones_nequi nn
              WHERE nn.ventaid = v.ventaid
          )
    """

    with connection.cursor() as cur:
        cur.execute(sql_pagos, [turno.puntopago_id, start_naive, end_naive])
        total_pagos = _to_decimal(cur.fetchone()[0])
        cur.execute(sql_fallback, [turno.puntopago_id, start_naive, end_naive])
        total_fallback = _to_decimal(cur.fetchone()[0])

    return (total_pagos + total_fallback).quantize(Decimal("0.01"))

def _auto_confirmados_por_metodo(turno: TurnoCaja) -> dict[str, Decimal]:
    return {
        "nequi": _sum_nequi_confirmado_api(turno),
    }

def _expected_por_metodo(turno: TurnoCaja) -> tuple[dict[str, Decimal], Decimal, Decimal, Decimal]:
    """
    Retorna:
      expected_by_method, esperado_total, esperado_efectivo, esperado_no_efectivo
    """
    if not turno.cierre_iniciado:
        return {}, Decimal("0"), Decimal("0"), Decimal("0")

    start_naive, end_naive = _range_local_naive(turno, turno.cierre_iniciado)

    expected = _sum_pagos_por_metodo(turno.puntopago_id, start_naive, end_naive)
    fallback = _sum_ventas_por_mediopago_fallback(turno.puntopago_id, start_naive, end_naive)
    for metodo, total in fallback.items():
        expected[metodo] = expected.get(metodo, Decimal("0.00")) + total

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
class TurnoCajaPageView(LoginRequiredMixin, TemplateView):
    template_name = "turno_caja.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ✅ Ajusta a tu lógica real:
        # Opción A: por grupos
        hide_bd = _hide_bd_cols_for_user(self.request.user)

        # Opción B: si tienes un campo rol (ej: user.rol)
        # hide_bd = getattr(self.request.user, "rol", "") in ["Cajero", "Auxiliar"]

        ctx["hide_bd_cols"] = hide_bd
        return ctx

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
        if not _require_admin(request.user):
            qs = qs.filter(pk=request.user.pk)
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
        puntopago_id = (request.POST.get("puntopago_id") or "").strip()
        cajero_id    = (request.POST.get("cajero_id") or "").strip()
        password     = request.POST.get("password", "")
        base_str     = (request.POST.get("saldo_apertura_efectivo", "0") or "0").strip()

        if not (puntopago_id.isdigit() and cajero_id.isdigit()):
            return JsonResponse({"success": False, "error": "Datos incompletos."}, status=400)

        pp = get_object_or_404(PuntosPago.objects.select_for_update(), pk=int(puntopago_id))
        cajero = get_object_or_404(Usuario, pk=int(cajero_id))

        if not _can_operate_cajero(request.user, cajero):
            return JsonResponse({"success": False, "error": "No puedes iniciar turno para otro cajero."}, status=403)

        if not _password_ok(cajero, password):
            return JsonResponse({"success": False, "error": "Usuario o contraseña inválidos."}, status=401)

        # Evita dos turnos abiertos por punto de pago.
        turno_existente = (
            TurnoCaja.objects
            .select_for_update()
            .select_related("puntopago", "cajero")
            .filter(puntopago=pp, estado__in=["ABIERTO", "CIERRE"])
            .order_by("-inicio")
            .first()
        )
        if turno_existente:
            return JsonResponse(
                {
                    "success": False,
                    "error": (
                        "Ya existe un turno ABIERTO/CIERRE en este punto de pago "
                        f"para {_turno_label_usuario(turno_existente.cajero)}."
                    ),
                    "turno_id": turno_existente.pk,
                    "estado": turno_existente.estado,
                },
                status=409
            )

        base = _to_decimal(base_str, Decimal("0.00"))
        if base < 0:
            base = Decimal("0.00")
        turno = TurnoCaja.objects.create(
            puntopago=pp,
            cajero=cajero,
            saldo_apertura_efectivo=base,
            estado="ABIERTO",
        )

        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "inicio": _iso_co(timezone.localtime(turno.inicio, CO_TZ)),
            "base": float(turno.saldo_apertura_efectivo),
            "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
            "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},

            # ✅ CLAVE
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


class TurnoCajaIniciarCierreApi(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
        turno_id = request.POST.get("turno_id")
        if not turno_id:
            return JsonResponse({"success": False, "error": "Falta turno_id."}, status=400)
        if not str(turno_id).isdigit():
            return JsonResponse({"success": False, "error": "turno_id invÃ¡lido."}, status=400)

        turno = get_object_or_404(
            TurnoCaja.objects.select_for_update().select_related("puntopago", "cajero"),
            pk=int(turno_id),
        )

        if not _can_operate_turno(request.user, turno):
            return JsonResponse({"success": False, "error": "No puedes operar un turno de otro cajero."}, status=403)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya esta CERRADO."}, status=409)
            return JsonResponse(
                {"success": False, "error": f"El turno no está ABIERTO (estado={turno.estado})."},
                status=409
            )

        reset_contados = False
        if turno.estado == "ABIERTO":
            turno.cierre_iniciado = _now_co()
            turno.estado = "CIERRE"
            turno.save(update_fields=["cierre_iniciado", "estado"])
            reset_contados = True
        elif turno.estado != "CIERRE":
            return JsonResponse(
                {"success": False, "error": f"Estado de turno invalido ({turno.estado})."},
                status=409
            )

        expected, esperado_total, esperado_efectivo, esperado_no_efectivo = _expected_por_metodo(turno)

        turno.ventas_total = esperado_total
        turno.ventas_efectivo = esperado_efectivo
        turno.ventas_no_efectivo = esperado_no_efectivo

        turno.esperado_total = esperado_total
        turno.save(update_fields=[
            "ventas_total", "ventas_efectivo", "ventas_no_efectivo",
            "esperado_total"
        ])

        _sync_turno_medios_esperados(turno, expected, reset_contados=reset_contados)
        auto_confirmados = _auto_confirmados_por_metodo(turno)

        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "cierre_iniciado": _iso_co(timezone.localtime(turno.cierre_iniciado, CO_TZ)),
            "base": float(turno.saldo_apertura_efectivo),
            "esperado_total": float(esperado_total),
            "esperado_efectivo": float(esperado_efectivo),
            "esperado_no_efectivo": float(esperado_no_efectivo),
            "puntopago": {"id": turno.puntopago_id, "nombre": getattr(turno.puntopago, "nombre", str(turno.puntopago_id))},
            "cajero": {"id": turno.cajero_id, "nombreusuario": _turno_label_usuario(turno.cajero)},
            "medios": _medios_payload(turno, auto_confirmados=auto_confirmados),
            "auto_confirmados": {k: float(v or 0) for k, v in auto_confirmados.items()},

            # ✅ CLAVE
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


class TurnoCajaCerrarApi(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
        turno_id = request.POST.get("turno_id")
        if not turno_id:
            return JsonResponse({"success": False, "error": "Falta turno_id."}, status=400)
        if not str(turno_id).isdigit():
            return JsonResponse({"success": False, "error": "turno_id invÃ¡lido."}, status=400)

        turno = get_object_or_404(
            TurnoCaja.objects.select_for_update().select_related("puntopago", "cajero"),
            pk=int(turno_id),
        )

        if not _can_operate_turno(request.user, turno):
            return JsonResponse({"success": False, "error": "No puedes cerrar un turno de otro cajero."}, status=403)

        if turno.estado != "CIERRE":
            return JsonResponse(
                {"success": False, "error": f"El turno no está en CIERRE (estado={turno.estado})."},
                status=409
            )

        efectivo_entregado = _to_decimal(request.POST.get("efectivo_entregado"), Decimal("0.00"))
        if efectivo_entregado < 0:
            return JsonResponse({"success": False, "error": "Efectivo entregado no puede ser negativo."}, status=400)

        facturas_pagadas = _to_decimal(request.POST.get("facturas_pagadas"), Decimal("0.00"))
        if facturas_pagadas < 0:
            return JsonResponse({"success": False, "error": "Facturas pagadas no puede ser negativo."}, status=400)

        base = turno.saldo_apertura_efectivo or Decimal("0.00")
        efectivo_contado = (efectivo_entregado - base).quantize(Decimal("0.01"))
        if efectivo_contado < 0:
            efectivo_contado = Decimal("0.00")
        efectivo_para_cuadre = (efectivo_contado + facturas_pagadas).quantize(Decimal("0.01"))

        import json
        medios_json = request.POST.get("medios_json", "[]")
        try:
            medios_in = json.loads(medios_json) if medios_json else []
        except Exception:
            return JsonResponse({"success": False, "error": "medios_json inválido."}, status=400)

        if not isinstance(medios_in, list):
            return JsonResponse({"success": False, "error": "medios_json debe ser una lista."}, status=400)

        expected, esperado_total, esperado_efectivo, esperado_no_efectivo = _expected_por_metodo(turno)
        _sync_turno_medios_esperados(turno, expected, reset_contados=False)
        auto_confirmados = _auto_confirmados_por_metodo(turno)

        medios_db = {_normalize_metodo(m.metodo): m for m in turno.medios.select_for_update().all()}

        if "efectivo" not in medios_db:
            medios_db["efectivo"] = TurnoCajaMedio.objects.create(
                turno=turno, metodo="efectivo", esperado=Decimal("0.00")
            )

        contados: dict[str, Decimal] = {}
        for item in medios_in:
            if not isinstance(item, dict):
                continue
            metodo = _normalize_metodo(item.get("metodo"))
            if not metodo or metodo == "efectivo":
                continue

            contado = _to_decimal(item.get("contado"), Decimal("0.00"))
            if contado < 0:
                contado = Decimal("0.00")

            contados[metodo] = contado

        for metodo, confirmado in auto_confirmados.items():
            confirmado = (confirmado or Decimal("0.00")).quantize(Decimal("0.01"))
            if confirmado > 0:
                contados[metodo] = (contados.get(metodo, Decimal("0.00")) + confirmado).quantize(Decimal("0.01"))

        missing_methods = [
            metodo for metodo in contados
            if metodo not in medios_db and metodo not in {"efectivo", FACTURAS_PAGADAS_METODO}
        ]
        if missing_methods:
            TurnoCajaMedio.objects.bulk_create(
                [
                    TurnoCajaMedio(turno=turno, metodo=metodo, esperado=Decimal("0.00"))
                    for metodo in missing_methods
                ],
                ignore_conflicts=True,
                batch_size=50,
            )
            medios_db = {_normalize_metodo(m.metodo): m for m in turno.medios.select_for_update().all()}

        contados["efectivo"] = efectivo_para_cuadre

        sum_contado = Decimal("0.00")
        sum_esperado = Decimal("0.00")
        esperado_efectivo = Decimal("0.00")
        contado_efectivo = Decimal("0.00")
        contado_no_efectivo = Decimal("0.00")

        deuda_total = Decimal("0.00")  # NEGATIVA o 0
        medios_to_update = []

        for metodo, medio_obj in medios_db.items():
            if metodo == FACTURAS_PAGADAS_METODO:
                continue

            esperado = medio_obj.esperado or Decimal("0.00")
            contado = contados.get(metodo)

            if contado is None and metodo != "efectivo":
                contado = Decimal("0.00")

            if metodo == "efectivo":
                contado = efectivo_para_cuadre
                esperado_efectivo = esperado

            contado = (contado or Decimal("0.00")).quantize(Decimal("0.01"))
            diff = (contado - esperado).quantize(Decimal("0.01"))

            medio_obj.contado = contado
            medio_obj.diferencia = diff
            medios_to_update.append(medio_obj)

            sum_contado += contado
            sum_esperado += esperado
            if metodo == "efectivo":
                contado_efectivo += contado
            else:
                contado_no_efectivo += contado

            if diff < 0:
                deuda_total += diff  # diff negativo

        if medios_to_update:
            TurnoCajaMedio.objects.bulk_update(
                medios_to_update,
                ["contado", "diferencia"],
                batch_size=100,
            )

        diferencia_total = (sum_contado - sum_esperado).quantize(Decimal("0.01"))
        deuda_total = deuda_total.quantize(Decimal("0.01"))

        facturas_medio, _ = TurnoCajaMedio.objects.get_or_create(
            turno=turno,
            metodo=FACTURAS_PAGADAS_METODO,
            defaults={
                "esperado": Decimal("0.00"),
                "contado": Decimal("0.00"),
                "diferencia": Decimal("0.00"),
            },
        )
        facturas_medio.esperado = Decimal("0.00")
        facturas_medio.contado = facturas_pagadas
        facturas_medio.diferencia = Decimal("0.00")
        facturas_medio.save(update_fields=["esperado", "contado", "diferencia"])

        real_total = (efectivo_entregado + (sum_contado - efectivo_para_cuadre)).quantize(Decimal("0.01"))

        turno.fin = _now_co()
        turno.estado = "CERRADO"

        turno.efectivo_real = efectivo_entregado
        turno.diferencia_efectivo = (efectivo_para_cuadre - esperado_efectivo).quantize(Decimal("0.01"))

        turno.esperado_total = sum_esperado
        turno.real_total = real_total
        turno.ventas_total = sum_contado
        turno.ventas_efectivo = contado_efectivo
        turno.ventas_no_efectivo = contado_no_efectivo
        turno.diferencia_total = diferencia_total
        turno.deuda_total = deuda_total

        turno.save(update_fields=[
            "fin", "estado",
            "efectivo_real", "diferencia_efectivo",
            "esperado_total", "real_total",
            "ventas_total", "ventas_efectivo", "ventas_no_efectivo",
            "diferencia_total", "deuda_total",
        ])

        faltante_abs = abs(deuda_total)
        msg = "Cierre OK. Sin faltantes." if deuda_total == 0 else f"⚠️ Faltante: {faltante_abs}."

        return JsonResponse({
            "success": True,
            "turno_id": turno.id,
            "estado": turno.estado,
            "ventas_total": float(turno.ventas_total),
            "esperado_total": float(turno.esperado_total),
            "diferencia_total": float(turno.diferencia_total),
            "deuda_total": float(turno.deuda_total),
            "facturas_pagadas": float(facturas_pagadas),
            "auto_confirmados": {k: float(v or 0) for k, v in auto_confirmados.items()},
            "retiro_url": reverse("turno_caja_retiro", kwargs={"turno_id": turno.id}),
            "msg": msg,

            # ✅ CLAVE (para mantener el ocultamiento en frontend)
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })






class TurnoCajaRetiroView(LoginRequiredMixin, View):
    template_name = "turno_caja_retiro.html"

    def get(self, request: HttpRequest, turno_id: int):
        turno = get_object_or_404(
            TurnoCaja.objects.select_related("puntopago", "cajero"),
            pk=turno_id,
        )

        if not _can_operate_turno(request.user, turno):
            return HttpResponseForbidden("No puedes revisar el cierre de otro cajero.")

        if turno.estado != "CERRADO":
            messages.warning(request, "El retiro de denominaciones solo aplica despues de cerrar el turno.")
            return redirect("turno_caja")

        medios = []
        facturas_pagadas = Decimal("0.00")
        for medio in TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"):
            metodo = _normalize_metodo(medio.metodo)
            contado = (medio.contado or Decimal("0.00")).quantize(Decimal("0.01"))
            if metodo == FACTURAS_PAGADAS_METODO:
                facturas_pagadas = contado
                continue
            medios.append({
                "metodo": metodo,
                "label": DISPLAY_METODO.get(metodo, metodo.replace("_", " ").title()),
                "contado": float(contado),
                "vendido": float(contado),
            })

        retiro_data = {
            "turno_id": turno.id,
            "puntopago": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
            "cajero": _turno_label_usuario(turno.cajero),
            "inicio": _iso_dt(turno.inicio),
            "cierre_iniciado": _iso_dt(turno.cierre_iniciado),
            "fin": _iso_dt(turno.fin),
            "base_apertura": float(turno.saldo_apertura_efectivo or 0),
            "efectivo_real": float(turno.efectivo_real or 0),
            "facturas_pagadas": float(facturas_pagadas),
            "ventas_total": float(turno.ventas_total or 0),
            "ventas_total_vendido": float(turno.ventas_total or 0),
            "ventas_efectivo": float(turno.ventas_efectivo or 0),
            "ventas_no_efectivo": float(turno.ventas_no_efectivo or 0),
            "medios": medios,
        }

        return render(request, self.template_name, {
            "turno": turno,
            "retiro_data": retiro_data,
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


PAGE_SIZE = 30

# Orden canónico de métodos
try:
    CANON_ORDER = list(CANON_ORDER)  # si ya existe
except Exception:
    CANON_ORDER = ["efectivo", "nequi", "daviplata", "tarjeta", "banco_caja_social"]

ESTADOS_TURNO = ("ABIERTO", "CIERRE", "CERRADO")


# =========================
# ✅ Helpers rol (ocultar BD)
# =========================
def _role_name(user) -> str:
    """
    Intenta inferir el nombre del rol de forma robusta:
    - user.rol (str o FK con .nombre/.name)
    - user.role
    - user.perfil.rol, etc.
    - grupos de Django (si aplicara)
    """
    for key in ("rol", "rolid", "role", "perfil", "cargo"):
        obj = getattr(user, key, None)
        if not obj:
            continue
        if isinstance(obj, str):
            return obj
        for attr in ("nombre", "name", "rol", "tipo"):
            if hasattr(obj, attr):
                try:
                    v = getattr(obj, attr)
                    if v:
                        return str(v)
                except Exception:
                    pass
        try:
            return str(obj)
        except Exception:
            pass

    try:
        g = user.groups.first()
        if g:
            return str(g.name)
    except Exception:
        pass

    return ""


def _hide_bd_cols_for_user(user) -> bool:
    # ✅ Cajero y Auxiliar NO deben ver columnas BD
    try:
        if user.groups.filter(name__in=["Cajero", "Auxiliar"]).exists():
            return True
    except Exception:
        pass
    return _role_name(user).strip().lower() in {"cajero", "auxiliar"}


# =========================
# Helpers num / fechas (admin)
# =========================
def _to_dec(v, default=Decimal("0.00")):
    try:
        s = str(v).strip()
        if s == "":
            return default
        s = s.replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif "." in s and s.rsplit(".", 1)[-1].isdigit() and len(s.rsplit(".", 1)[-1]) == 3:
            s = s.replace(".", "")
        return Decimal(s).quantize(Decimal("0.01"))
    except Exception:
        return default


def _iso_dt(dt):
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S")


def _iso_dt_local_input(dt):
    """Para <input type="datetime-local">"""
    if not dt:
        return ""
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


def _require_admin(user):
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True
    return _role_name(user).strip().lower() in {"admin", "administrador", "supervisor"}


def _can_admin_turnos(user):
    return _require_admin(user) or user_can_access_url_name(user, "api_admin_turno_delete")


def _can_edit_turnos(user):
    return _can_admin_turnos(user) or user_can_access_url_name(user, "turnos_caja_admin")


def _recalc_turno_from_medios(turno):
    """
    ✅ REGLA AJUSTADA:
      - deuda_total = SUMA SOLO de diferencias negativas (diff < 0), queda NEGATIVA o 0
      - diferencia_total = sum(contado) - sum(esperado)  (neto)
    """
    medios = list(TurnoCajaMedio.objects.filter(turno=turno))

    esperado_total = Decimal("0.00")
    ventas_total = Decimal("0.00")

    esperado_ef = Decimal("0.00")
    contado_ef = Decimal("0.00")

    deuda_total = Decimal("0.00")  # ✅ suma SOLO negativos

    for m in medios:
        metodo_norm = _normalize_metodo(m.metodo)
        if metodo_norm == FACTURAS_PAGADAS_METODO:
            m.diferencia = Decimal("0.00")
            m.save(update_fields=["diferencia"])
            continue

        esp = (m.esperado or Decimal("0.00")).quantize(Decimal("0.01"))
        con = (m.contado or Decimal("0.00")).quantize(Decimal("0.01")) if m.contado is not None else Decimal("0.00")

        esperado_total += esp
        ventas_total += con

        if metodo_norm == "efectivo":
            esperado_ef = esp
            contado_ef = con

        diff = (con - esp).quantize(Decimal("0.01"))
        m.diferencia = diff
        m.save(update_fields=["diferencia"])

        if diff < 0:
            deuda_total += diff  # ✅ solo negativos

    diferencia_total = (ventas_total - esperado_total).quantize(Decimal("0.01"))
    deuda_total = deuda_total.quantize(Decimal("0.01"))  # negativo o 0

    turno.esperado_total = esperado_total
    turno.ventas_total = ventas_total
    turno.ventas_efectivo = contado_ef
    turno.ventas_no_efectivo = (ventas_total - contado_ef).quantize(Decimal("0.01"))
    turno.diferencia_total = diferencia_total
    turno.deuda_total = deuda_total
    turno.diferencia_efectivo = (contado_ef - esperado_ef).quantize(Decimal("0.01"))

    turno.save(update_fields=[
        "esperado_total", "ventas_total", "ventas_efectivo", "ventas_no_efectivo",
        "diferencia_total", "deuda_total", "diferencia_efectivo"
    ])


# =========================
# Página dashboard
# =========================
@method_decorator(login_required, name="dispatch")
class TurnosCajaDashboardView(View):
    template_name = "turnos_caja_dashboard.html"

    def get(self, request: HttpRequest):
        return render(request, self.template_name, {})


# =========================
# Autocomplete punto de pago
# =========================
@method_decorator(login_required, name="dispatch")
class PuntoPagoAutocompleteSimple(View):
    def get(self, request: HttpRequest):
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
    def get(self, request: HttpRequest):
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
                "inicio": timezone.localtime(t.inicio).strftime("%Y-%m-%d %H:%M:%S") if t.inicio else None,
                "cierre_iniciado": timezone.localtime(t.cierre_iniciado).strftime("%Y-%m-%d %H:%M:%S") if t.cierre_iniciado else None,
            })

        return JsonResponse({"success": True, "turnos": out})


# =========================
# API: iniciar cierre (set cierre_iniciado + generar esperados)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaIniciarCierreAPI(View):
    @transaction.atomic
    def post(self, request: HttpRequest, turno_id: int):
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)

        if not turno.cierre_iniciado:
            turno.cierre_iniciado = timezone.now()
            turno.estado = "CIERRE"
            turno.save(update_fields=["cierre_iniciado", "estado"])

        esperado_total, esperado_por = _calcular_esperados_por_metodo(
            pp_id=turno.puntopago_id,
            start_dt=turno.inicio,
            end_dt=turno.cierre_iniciado,
        )

        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.update_or_create(
                turno=turno,
                metodo=metodo,
                defaults={"esperado": esperado_por.get(metodo, Decimal("0.00"))}
            )

        turno.esperado_total = esperado_total
        turno.save(update_fields=["esperado_total"])

        medios = []
        for m in TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"):
            medios.append({
                "metodo": (m.metodo or "").strip().lower(),
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
                "inicio": timezone.localtime(turno.inicio).strftime("%Y-%m-%d %H:%M:%S"),
                "cierre_iniciado": timezone.localtime(turno.cierre_iniciado).strftime("%Y-%m-%d %H:%M:%S"),
                "saldo_apertura_efectivo": float(turno.saldo_apertura_efectivo or 0),
                "esperado_total": float(turno.esperado_total or 0),
            },
            "medios": medios,
            # ✅ para el front
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


# =========================
# API: snapshot (para continuar cierres ya iniciados)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaSnapshotAPI(View):
    def get(self, request: HttpRequest, turno_id: int):
        turno = get_object_or_404(TurnoCaja.objects.select_related("puntopago", "cajero"), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)

        if not turno.cierre_iniciado:
            return JsonResponse({"success": False, "error": "Este turno aún no tiene cierre iniciado."}, status=400)

        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=metodo, defaults={"esperado": Decimal("0.00")})

        medios = []
        for m in TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"):
            medios.append({
                "metodo": (m.metodo or "").strip().lower(),
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
                "inicio": timezone.localtime(turno.inicio).strftime("%Y-%m-%d %H:%M:%S") if turno.inicio else None,
                "cierre_iniciado": timezone.localtime(turno.cierre_iniciado).strftime("%Y-%m-%d %H:%M:%S") if turno.cierre_iniciado else None,
                "saldo_apertura_efectivo": float(turno.saldo_apertura_efectivo or 0),
                "esperado_total": float(turno.esperado_total or 0),
                "efectivo_real": float(turno.efectivo_real or 0) if turno.efectivo_real is not None else None,
                "ventas_total": float(turno.ventas_total or 0),
                "deuda_total": float(turno.deuda_total or 0),
            },
            "medios": medios,
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


# =========================
# API: cerrar turno (guardar contados + diferencias + fin)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnoCajaCerrarAPI(View):
    @transaction.atomic
    def post(self, request: HttpRequest, turno_id: int):
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        if turno.estado == "CERRADO":
            return JsonResponse({"success": False, "error": "El turno ya está cerrado."}, status=400)
        if not turno.cierre_iniciado:
            return JsonResponse({"success": False, "error": "Primero inicia el cierre."}, status=400)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"success": False, "error": "JSON inválido."}, status=400)

        efectivo_entregado = _to_dec(payload.get("efectivo_entregado"), Decimal("0.00"))
        contados = payload.get("contados") or {}

        base = turno.saldo_apertura_efectivo or Decimal("0.00")
        efectivo_contado = (efectivo_entregado - base).quantize(Decimal("0.01"))
        if efectivo_contado < 0:
            efectivo_contado = Decimal("0.00")

        for metodo in CANON_ORDER:
            TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=metodo, defaults={"esperado": Decimal("0.00")})

        ventas_no_efectivo_real = Decimal("0.00")
        for m in TurnoCajaMedio.objects.filter(turno=turno):
            metodo = _canon_metodo(m.metodo)

            if metodo == "efectivo":
                m.contado = efectivo_contado
            else:
                v = _to_dec(contados.get(metodo), Decimal("0.00"))
                if v < 0:
                    v = Decimal("0.00")
                m.contado = v
                ventas_no_efectivo_real += v

            m.diferencia = (m.contado or Decimal("0.00")) - (m.esperado or Decimal("0.00"))
            m.save(update_fields=["contado", "diferencia"])

        esperado_total, esperado_por = _calcular_esperados_por_metodo(
            pp_id=turno.puntopago_id,
            start_dt=turno.inicio,
            end_dt=turno.cierre_iniciado,
        )

        ventas_total_real = (ventas_no_efectivo_real + efectivo_contado).quantize(Decimal("0.01"))
        diferencia_total = (ventas_total_real - esperado_total).quantize(Decimal("0.01"))

        # ✅ deuda_total = SUMA SOLO de negativos por medio (diff < 0). Queda NEGATIVA o 0.
        deuda_total = Decimal("0.00")
        for m in TurnoCajaMedio.objects.filter(turno=turno):
            diff = (m.diferencia or Decimal("0.00")).quantize(Decimal("0.01"))
            if diff < 0:
                deuda_total += diff
        deuda_total = deuda_total.quantize(Decimal("0.01"))

        turno.ventas_total = ventas_total_real
        turno.ventas_efectivo = efectivo_contado
        turno.ventas_no_efectivo = ventas_no_efectivo_real

        turno.esperado_total = esperado_total
        turno.efectivo_real = efectivo_entregado

        turno.diferencia_total = diferencia_total
        turno.diferencia_efectivo = (efectivo_contado - esperado_por.get("efectivo", Decimal("0.00"))).quantize(Decimal("0.01"))

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
            "diferencia_total": float(turno.diferencia_total or 0),
            "deuda_total": float(turno.deuda_total or 0),  # negativo o 0
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


# =========================
# Recuperar o iniciar (tu endpoint actual)
# =========================
class TurnoCajaRecuperarOIniciarView(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest):
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

        base = _to_dec(base_str, Decimal("0.00"))
        if base < 0:
            base = Decimal("0.00")

        pp = get_object_or_404(PuntosPago.objects.select_for_update(), pk=int(puntopago_id))
        cajero = get_object_or_404(Usuario, pk=int(usuario_id))

        if not _can_operate_cajero(request.user, cajero):
            return JsonResponse({"success": False, "error": "No puedes iniciar o retomar turno para otro cajero."}, status=403)

        if not _password_ok(cajero, password):
            return JsonResponse({"success": False, "error": "Contraseña incorrecta."}, status=403)

        turno = (TurnoCaja.objects
                 .select_for_update()
                 .select_related("puntopago", "cajero")
                 .filter(puntopago=pp, estado__in=["ABIERTO", "CIERRE"])
                 .order_by("-inicio")
                 .first())

        if turno:
            if turno.cajero_id != cajero.pk:
                return JsonResponse({
                    "success": False,
                    "error": (
                        "Este punto de pago ya tiene un turno activo "
                        f"para {_turno_label_usuario(turno.cajero)}."
                    ),
                    "turno_id": turno.pk,
                    "estado": turno.estado,
                }, status=409)

            if turno.estado == "CIERRE":
                expected, esperado_total, esperado_efectivo, esperado_no_efectivo = _expected_por_metodo(turno)
                _sync_turno_medios_esperados(turno, expected, reset_contados=False)
                auto_confirmados = _auto_confirmados_por_metodo(turno)
                turno.esperado_total = esperado_total
                turno.ventas_total = esperado_total
                turno.ventas_efectivo = esperado_efectivo
                turno.ventas_no_efectivo = esperado_no_efectivo
                turno.save(update_fields=["esperado_total", "ventas_total", "ventas_efectivo", "ventas_no_efectivo"])
                return JsonResponse({
                    "success": True,
                    "msg": "Turno retomado.",
                    "modo": "RETOMADO",
                    "turno_id": turno.pk,
                    "estado": turno.estado,
                    "inicio": _iso_dt(turno.inicio),
                    "cierre_iniciado": _iso_dt(turno.cierre_iniciado) if turno.cierre_iniciado else None,
                    "base": float(turno.saldo_apertura_efectivo or 0),
                    "puntopago": {"id": turno.puntopago_id, "nombre": getattr(turno.puntopago, "nombre", str(turno.puntopago_id))},
                    "cajero": {"id": turno.cajero_id, "nombreusuario": _turno_label_usuario(turno.cajero)},
                    "esperado_total": float(esperado_total),
                    "medios": _medios_payload(turno, auto_confirmados=auto_confirmados),
                    "auto_confirmados": {k: float(v or 0) for k, v in auto_confirmados.items()},
                    "hide_bd_cols": _hide_bd_cols_for_user(request.user),
                })

            return JsonResponse({
                "success": True,
                "msg": "Turno retomado.",
                "modo": "RETOMADO",
                "turno_id": turno.pk,
                "estado": turno.estado,
                "inicio": _iso_dt(turno.inicio),
                "cierre_iniciado": _iso_dt(turno.cierre_iniciado) if turno.cierre_iniciado else None,
                "base": float(turno.saldo_apertura_efectivo or 0),
                "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
                "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},
                "hide_bd_cols": _hide_bd_cols_for_user(request.user),
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
            "inicio": _iso_dt(turno.inicio),
            "base": float(turno.saldo_apertura_efectivo or 0),
            "puntopago": {"id": pp.pk, "nombre": getattr(pp, "nombre", str(pp.pk))},
            "cajero": {"id": cajero.pk, "nombreusuario": getattr(cajero, "nombreusuario", str(cajero.pk))},
            "hide_bd_cols": _hide_bd_cols_for_user(request.user),
        })


# =========================
# Dashboard extra APIs (las que pegaste)
# =========================
@method_decorator(login_required, name="dispatch")
class TurnosCajaDashboardListAPI(View):
    def get(self, request: HttpRequest):
        estado = (request.GET.get("estado") or "ALL").strip().upper()
        q = (request.GET.get("q") or "").strip()
        pp_id = (request.GET.get("puntopago_id") or "").strip()
        cajero_id = (request.GET.get("cajero_id") or "").strip()
        date_from = (request.GET.get("date_from") or "").strip()
        date_to   = (request.GET.get("date_to") or "").strip()

        page = max(1, int(request.GET.get("page") or 1))
        page_size = min(max(10, int(request.GET.get("page_size") or 25)), 200)

        qs = (TurnoCaja.objects.select_related("puntopago", "cajero").all().order_by("-inicio"))

        if estado in {"ABIERTO", "CIERRE", "CERRADO"}:
            qs = qs.filter(estado=estado)

        if pp_id.isdigit():
            qs = qs.filter(puntopago_id=int(pp_id))
        if cajero_id.isdigit():
            qs = qs.filter(cajero_id=int(cajero_id))

        if date_from:
            qs = qs.filter(inicio__date__gte=date_from)
        if date_to:
            qs = qs.filter(inicio__date__lte=date_to)

        if q:
            qs = qs.filter(Q(puntopago__nombre__icontains=q) | Q(cajero__nombreusuario__icontains=q))

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size

        items = []
        for t in qs[start:end]:
            items.append({
                "id": t.id,
                "estado": t.estado,
                "puntopago": getattr(t.puntopago, "nombre", str(t.puntopago_id)),
                "cajero": getattr(t.cajero, "nombreusuario", str(t.cajero_id)),
                "inicio": _iso_dt(t.inicio),
                "cierre_iniciado": _iso_dt(t.cierre_iniciado),
                "fin": _iso_dt(t.fin),
                "base": float(getattr(t, "saldo_apertura_efectivo", Decimal("0")) or 0),
                "esperado_total": float(getattr(t, "esperado_total", Decimal("0")) or 0),
                "ventas_total": float(getattr(t, "ventas_total", Decimal("0")) or 0),
                "diferencia_total": float(getattr(t, "diferencia_total", Decimal("0")) or 0),
                "deuda_total": float(getattr(t, "deuda_total", Decimal("0")) or 0),
            })

        return JsonResponse({
            "success": True,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": end < total,
            "items": items,
        })


@method_decorator(login_required, name="dispatch")
class TurnoCajaDashboardDetailAPI(View):
    def get(self, request: HttpRequest, turno_id: int):
        compute_expected = (request.GET.get("compute_expected") or "0").strip() == "1"

        turno = get_object_or_404(TurnoCaja.objects.select_related("puntopago", "cajero"), pk=turno_id)

        try:
            canon_order = list(CANON_ORDER)
        except Exception:
            canon_order = ["efectivo", "nequi", "daviplata", "tarjeta", "banco_caja_social"]

        for m in canon_order:
            TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=m, defaults={"esperado": Decimal("0.00")})

        esperado_total_calc = None
        esperado_por_calc = None
        if compute_expected:
            end_dt = turno.cierre_iniciado or (turno.fin if turno.estado == "CERRADO" else timezone.now())
            esperado_total_calc, esperado_por_calc = _calcular_esperados_por_metodo(
                pp_id=turno.puntopago_id,
                start_dt=turno.inicio,
                end_dt=end_dt,
            )

        medios_qs = TurnoCajaMedio.objects.filter(turno=turno)

        order_index = {m: i for i, m in enumerate(canon_order)}
        medios_list = sorted(list(medios_qs), key=lambda x: order_index.get((x.metodo or "").lower(), 999))

        medios_out = []
        for medio in medios_list:
            metodo = (medio.metodo or "").lower().strip()
            esperado = medio.esperado or Decimal("0.00")
            contado = medio.contado
            diferencia = medio.diferencia or Decimal("0.00")

            esperado_calc = None
            if esperado_por_calc is not None:
                esperado_calc = esperado_por_calc.get(metodo, Decimal("0.00"))

            medios_out.append({
                "metodo": metodo,
                "esperado_bd": float(esperado),
                "esperado_calc": float(esperado_calc) if esperado_calc is not None else None,
                "contado": float(contado) if contado is not None else None,
                "diferencia": float(diferencia),
            })

        out = {
            "success": True,
            "turno": {
                "id": turno.id,
                "estado": turno.estado,
                "puntopago": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
                "cajero": getattr(turno.cajero, "nombreusuario", str(turno.cajero_id)),
                "inicio": _iso_dt(turno.inicio),
                "cierre_iniciado": _iso_dt(turno.cierre_iniciado),
                "fin": _iso_dt(turno.fin),
                "base": float(getattr(turno, "saldo_apertura_efectivo", Decimal("0")) or 0),
                "esperado_total_bd": float(getattr(turno, "esperado_total", Decimal("0")) or 0),
                "ventas_total": float(getattr(turno, "ventas_total", Decimal("0")) or 0),
                "diferencia_total": float(getattr(turno, "diferencia_total", Decimal("0")) or 0),
                "deuda_total": float(getattr(turno, "deuda_total", Decimal("0")) or 0),
                "efectivo_real": float(getattr(turno, "efectivo_real", Decimal("0")) or 0) if getattr(turno, "efectivo_real", None) is not None else None,
            },
            "medios": medios_out,
        }

        if compute_expected:
            out["expected_calc"] = {"esperado_total_calc": float(esperado_total_calc or 0)}

        return JsonResponse(out)


# =========================
# Admin Page + APIs (las que pegaste)
# =========================
class TurnosCajaAdminPageView(LoginRequiredMixin, View):
    template_name = "turnos_caja_admin.html"

    def get(self, request: HttpRequest):
        if not _can_edit_turnos(request.user):
            return HttpResponseForbidden("No tienes permiso para editar turnos de caja.")
        return render(request, self.template_name, {
            "can_delete_turnos": _can_admin_turnos(request.user),
        })


class TurnoCajaAdminDetailAPI(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, turno_id: int):
        if not _can_edit_turnos(request.user):
            return JsonResponse({"success": False, "error": "No tienes permiso para editar turnos."}, status=403)
        turno = get_object_or_404(TurnoCaja.objects.select_related("puntopago", "cajero"), pk=turno_id)
        medios = list(TurnoCajaMedio.objects.filter(turno=turno).order_by("metodo"))

        return JsonResponse({
            "success": True,
            "turno": {
                "id": turno.id,
                "estado": turno.estado,
                "puntopago": getattr(turno.puntopago, "nombre", str(turno.puntopago_id)),
                "cajero": getattr(turno.cajero, "nombreusuario", str(turno.cajero_id)),
                "inicio": _iso_dt(turno.inicio),
                "cierre_iniciado": _iso_dt(turno.cierre_iniciado),
                "fin": _iso_dt(turno.fin),
                "inicio_local": _iso_dt_local_input(turno.inicio),
                "cierre_iniciado_local": _iso_dt_local_input(turno.cierre_iniciado),
                "fin_local": _iso_dt_local_input(turno.fin),
                "saldo_apertura_efectivo": float(getattr(turno, "saldo_apertura_efectivo", Decimal("0")) or 0),
                "efectivo_real": float(getattr(turno, "efectivo_real", Decimal("0")) or 0) if getattr(turno, "efectivo_real", None) is not None else None,
                "esperado_total": float(getattr(turno, "esperado_total", Decimal("0")) or 0),
                "ventas_total": float(getattr(turno, "ventas_total", Decimal("0")) or 0),
                "ventas_efectivo": float(getattr(turno, "ventas_efectivo", Decimal("0")) or 0),
                "ventas_no_efectivo": float(getattr(turno, "ventas_no_efectivo", Decimal("0")) or 0),
                "diferencia_total": float(getattr(turno, "diferencia_total", Decimal("0")) or 0),
                "deuda_total": float(getattr(turno, "deuda_total", Decimal("0")) or 0),
                "diferencia_efectivo": float(getattr(turno, "diferencia_efectivo", Decimal("0")) or 0),
            },
            "medios": [
                {
                    "id": m.id,
                    "metodo": (m.metodo or "").strip().lower(),
                    "esperado": float(m.esperado or 0),
                    "contado": float(m.contado) if m.contado is not None else None,
                    "diferencia": float(m.diferencia or 0),
                }
                for m in medios
            ]
        })


class TurnoCajaAdminUpdateAPI(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest, turno_id: int):
        if not _can_edit_turnos(request.user):
            return JsonResponse({"success": False, "error": "No tienes permiso para editar turnos."}, status=403)
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"success": False, "error": "JSON inválido."}, status=400)

        estado = (payload.get("estado") or "").strip().upper()
        if estado and estado not in ESTADOS_TURNO:
            return JsonResponse({"success": False, "error": "Estado inválido."}, status=400)

        def parse_dt_local(s):
            s = (s or "").strip()
            if not s:
                return None
            try:
                naive = timezone.datetime.strptime(s, "%Y-%m-%dT%H:%M")
                return timezone.make_aware(naive, timezone.get_current_timezone())
            except Exception:
                return None

        inicio = parse_dt_local(payload.get("inicio_local"))
        cierre_iniciado = parse_dt_local(payload.get("cierre_iniciado_local"))
        fin = parse_dt_local(payload.get("fin_local"))

        base = _to_dec(payload.get("saldo_apertura_efectivo"), getattr(turno, "saldo_apertura_efectivo", Decimal("0.00")) or Decimal("0.00"))
        if base < 0:
            base = Decimal("0.00")

        efectivo_real = payload.get("efectivo_real")
        efectivo_real_dec = None
        if efectivo_real is not None and str(efectivo_real).strip() != "":
            efectivo_real_dec = _to_dec(efectivo_real, Decimal("0.00"))
            if efectivo_real_dec < 0:
                efectivo_real_dec = Decimal("0.00")

        if estado:
            turno.estado = estado
        if inicio:
            turno.inicio = inicio
        turno.cierre_iniciado = cierre_iniciado
        turno.fin = fin
        turno.saldo_apertura_efectivo = base
        if hasattr(turno, "efectivo_real"):
            turno.efectivo_real = efectivo_real_dec

        turno.save()

        medios_in = payload.get("medios") or []
        for item in medios_in:
            metodo = (item.get("metodo") or "").strip().lower()
            if not metodo:
                continue

            esperado = _to_dec(item.get("esperado"), Decimal("0.00"))
            contado_raw = item.get("contado")
            contado = None
            if contado_raw is not None and str(contado_raw).strip() != "":
                contado = _to_dec(contado_raw, Decimal("0.00"))
                if contado < 0:
                    contado = Decimal("0.00")

            m, _ = TurnoCajaMedio.objects.get_or_create(turno=turno, metodo=metodo, defaults={"esperado": Decimal("0.00")})
            m.esperado = esperado
            m.contado = contado
            m.save(update_fields=["esperado", "contado"])

        _recalc_turno_from_medios(turno)

        return JsonResponse({"success": True, "msg": "Turno actualizado y recalculado."})


class TurnoCajaAdminDeleteAPI(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request: HttpRequest, turno_id: int):
        if not _can_admin_turnos(request.user):
            return JsonResponse({"success": False, "error": "No tienes permiso para eliminar turnos."}, status=403)
        turno = get_object_or_404(TurnoCaja.objects.select_for_update(), pk=turno_id)

        TurnoCajaMedio.objects.filter(turno=turno).delete()
        turno.delete()

        return JsonResponse({"success": True, "msg": f"Turno #{turno_id} eliminado."})











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




def _to_dt_bounds(date_ini, date_fin):
    """Convierte fechas (YYYY-MM-DD) a [start_dt, end_dt_exclusive) aware."""
    tz = timezone.get_current_timezone()
    start = datetime.combine(date_ini, datetime.min.time())
    end_excl = datetime.combine(date_fin + timedelta(days=1), datetime.min.time())

    if timezone.is_naive(start):
        start = timezone.make_aware(start, tz)
        end_excl = timezone.make_aware(end_excl, tz)

    return start, end_excl


def _pick_existing_field(model, candidates):
    """Devuelve el primer campo existente en el model, de una lista de candidatos."""
    for name in candidates:
        try:
            model._meta.get_field(name)
            return name
        except FieldDoesNotExist:
            continue
    return None


def _get_qs_int(request, key):
    v = (request.GET.get(key) or "").strip()
    return int(v) if v.isdigit() else None


# ----------------------------
# Page view
# ----------------------------
class VentasProductoRangoView(LoginRequiredMixin, View):
    template_name = "ventas_producto_rango.html"

    def get(self, request, *args, **kwargs):
        # OJO: NO uses .only("id") porque tu PK no se llama "id"
        sucursales = Sucursal.objects.only("nombre").order_by("nombre")

        return render(request, self.template_name, {
            "sucursales": sucursales,
        })


# ----------------------------
# DataTables server-side (por producto)
# ----------------------------
class VentasProductoRangoDataView(LoginRequiredMixin, View):
    """
    DataTables server-side:
    - agrega por producto en BD
    - pagina/ordena/busca sin reventar RAM
    - sucursal_id llega por querystring
    """

    def get(self, request, *args, **kwargs):
        # required
        sucursal_id = _get_qs_int(request, "sucursal_id")
        if not sucursal_id:
            return JsonResponse({"error": "Debe enviar sucursal_id."}, status=400)

        # DataTables params
        draw = int(request.GET.get("draw", 1))
        start = int(request.GET.get("start", 0))
        length = int(request.GET.get("length", 25))
        search = (request.GET.get("search[value]", "") or "").strip()

        order_col = request.GET.get("order[0][column]", "0")
        order_dir = request.GET.get("order[0][dir]", "asc")

        # Fechas
        ini_str = request.GET.get("fecha_ini")
        fin_str = request.GET.get("fecha_fin")

        today = timezone.localdate()
        date_ini = parse_date(ini_str) if ini_str else (today - timedelta(days=30))
        date_fin = parse_date(fin_str) if fin_str else today

        if not date_ini or not date_fin:
            return JsonResponse({"error": "Rango de fechas inválido."}, status=400)
        if date_fin < date_ini:
            date_ini, date_fin = date_fin, date_ini

        # Detecta nombre del campo fecha en Venta
        venta_fecha = _pick_existing_field(Venta, ["fechaventa", "fecha", "fecha_venta", "created_at"])
        if not venta_fecha:
            return JsonResponse({"error": "No se encontró campo fecha en Venta (fechaventa/fecha/...)."}, status=500)

        start_dt, end_dt_excl = _to_dt_bounds(date_ini, date_fin)

        # Detecta subtotal / cantidad
        dv_cant = _pick_existing_field(DetalleVenta, ["cantidad", "cant", "qty"]) or "cantidad"
        dv_subt = _pick_existing_field(DetalleVenta, ["subtotal", "total", "importe"])

        base = (
            DetalleVenta.objects
            .filter(
                ventaid__sucursalid_id=sucursal_id,
                **{
                    f"ventaid__{venta_fecha}__gte": start_dt,
                    f"ventaid__{venta_fecha}__lt": end_dt_excl,
                }
            )
            .values("productoid_id", "productoid__nombre")
            .annotate(
                unidades=Sum(dv_cant),
                total_ventas=Sum(dv_subt) if dv_subt else Sum(ExpressionWrapper(
                    F(dv_cant) * F("preciounitario"),
                    output_field=DecimalField(max_digits=18, decimal_places=2)
                )),
                num_ventas=Count("ventaid_id", distinct=True),
            )
        )

        records_total = base.count()

        if search:
            base = base.filter(productoid__nombre__icontains=search)

        records_filtered = base.count()

        col_map = {
            "0": "productoid__nombre",
            "1": "unidades",
            "2": "total_ventas",
            "3": "num_ventas",
        }
        order_field = col_map.get(order_col, "total_ventas")
        if order_dir == "desc":
            order_field = "-" + order_field

        rows = list(base.order_by(order_field)[start:start + length])

        data = [{
            "producto_id": r["productoid_id"],
            "producto": r["productoid__nombre"],
            "unidades": float(r["unidades"] or 0),
            "total_ventas": float(r["total_ventas"] or 0),
            "num_ventas": int(r["num_ventas"] or 0),
        } for r in rows]

        return JsonResponse({
            "draw": draw,
            "recordsTotal": records_total,
            "recordsFiltered": records_filtered,
            "data": data,
        })


# ----------------------------
# Stats (el que usa tu botón "Consultar ventas")
# ----------------------------
class ProductoVentasStatsAjaxView(LoginRequiredMixin, View):
    """
    GET /ventas/producto/stats/?sucursal_id=1&productoid=123&desde=2026-02-01&hasta=2026-02-16
    """

    def get(self, request, *args, **kwargs):
        sucursal_id = _get_qs_int(request, "sucursal_id")
        if not sucursal_id:
            return JsonResponse({"success": False, "error": "Debe enviar sucursal_id."}, status=400)

        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)

        pid = _get_qs_int(request, "productoid")
        if not pid:
            return JsonResponse({"success": False, "error": "productoid inválido."}, status=400)

        producto = get_object_or_404(
            Producto._base_manager.only("productoid", "nombre", "codigo_de_barras"),
            pk=pid
        )

        desde_s = (request.GET.get("desde") or "").strip()
        hasta_s = (request.GET.get("hasta") or "").strip()
        desde = parse_date(desde_s) if desde_s else None
        hasta = parse_date(hasta_s) if hasta_s else None

        if not desde or not hasta:
            return JsonResponse({"success": False, "error": "Debe enviar desde y hasta (YYYY-MM-DD)."}, status=400)
        if desde > hasta:
            return JsonResponse({"success": False, "error": "Rango inválido: desde > hasta."}, status=400)

        # Detecta campo fecha de Venta
        venta_fecha = _pick_existing_field(Venta, ["fechaventa", "fecha", "fecha_venta", "created_at"])
        if not venta_fecha:
            return JsonResponse({"success": False, "error": "No se encontró campo fecha en Venta."}, status=500)

        start_dt, end_dt_excl = _to_dt_bounds(desde, hasta)

        dv_cant = _pick_existing_field(DetalleVenta, ["cantidad", "cant", "qty"]) or "cantidad"
        dv_price = _pick_existing_field(DetalleVenta, ["preciounitario", "precio_unitario", "precio"]) or "preciounitario"
        dv_subt  = _pick_existing_field(DetalleVenta, ["subtotal", "total", "importe"])

        qs = (
            DetalleVenta.objects
            .filter(
                productoid_id=pid,
                ventaid__sucursalid_id=sucursal.pk,
                **{
                    f"ventaid__{venta_fecha}__gte": start_dt,
                    f"ventaid__{venta_fecha}__lt": end_dt_excl,
                }
            )
        )

        ventas_distintas = qs.values("ventaid_id").distinct().count()
        unidades = qs.aggregate(u=Sum(dv_cant))["u"] or 0

        if dv_subt:
            ingresos = qs.aggregate(x=Sum(dv_subt))["x"] or 0
        else:
            ingresos_expr = ExpressionWrapper(
                F(dv_cant) * F(dv_price),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
            ingresos = qs.aggregate(x=Sum(ingresos_expr))["x"] or 0

        daily = list(
            qs.annotate(dia=TruncDate(f"ventaid__{venta_fecha}"))
              .values("dia")
              .annotate(
                  ventas=Count("ventaid_id", distinct=True),
                  unidades=Sum(dv_cant),
              )
              .order_by("dia")
        )
        daily = [{
            "fecha": str(d["dia"]),
            "ventas": int(d["ventas"] or 0),
            "unidades": int(d["unidades"] or 0),
        } for d in daily]

        return JsonResponse({
            "success": True,
            "product": {
                "id": producto.productoid,
                "nombre": producto.nombre,
                "codigo_de_barras": getattr(producto, "codigo_de_barras", "") or "",
            },
            "range": {"desde": str(desde), "hasta": str(hasta)},
            "stats": {
                "ventas_distintas": int(ventas_distintas),
                "unidades": int(unidades),
                "ingresos": str(ingresos),
            },
            "daily": daily,
        })


# -----------------------------------------------------------------------------
# Inventario desde fotos con agente local
# -----------------------------------------------------------------------------
class InventarioFotosPageView(LoginRequiredMixin, View):
    template_name = "inventario_fotos.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": InventarioFotosForm(),
        })


class InventarioFotosCatalogoView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="catalogo_productos.csv"'
        writer = csv.writer(response)
        writer.writerow(["nombre", "codigo_de_barras", "productoid"])
        for producto in Producto.objects.order_by("nombre").only("nombre", "codigo_de_barras", "productoid"):
            writer.writerow([
                producto.nombre,
                producto.codigo_de_barras or "",
                producto.productoid,
            ])
        return response


def _normalizar_texto_simple(value):
    texto = str(value or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _buscar_proveedor_factura(nombre):
    nombre = str(nombre or "").strip()
    if not nombre:
        return None

    proveedor = Proveedor.objects.filter(
        Q(nombre__iexact=nombre) | Q(empresa__iexact=nombre)
    ).first()
    if proveedor:
        return proveedor

    norm = _normalizar_texto_simple(nombre)
    if not norm:
        return None

    candidatos = Proveedor.objects.filter(
        Q(nombre__icontains=nombre[:80]) | Q(empresa__icontains=nombre[:80])
    )[:10]
    for candidato in candidatos:
        candidato_norm = _normalizar_texto_simple(candidato.nombre) or _normalizar_texto_simple(candidato.empresa)
        if norm in candidato_norm or candidato_norm in norm:
            return candidato

    tokens = set(norm.split())
    mejor = None
    mejor_score = 0
    for candidato in Proveedor.objects.all().only("proveedorid", "nombre", "empresa")[:500]:
        cand_norm = _normalizar_texto_simple(candidato.nombre) or _normalizar_texto_simple(candidato.empresa)
        if not cand_norm:
            continue
        cand_tokens = set(cand_norm.split())
        inter = len(tokens & cand_tokens)
        union = len(tokens | cand_tokens) or 1
        score = inter / union
        if norm in cand_norm or cand_norm in norm:
            score += 0.35
        if score > mejor_score:
            mejor = candidato
            mejor_score = score
    return mejor if mejor_score >= 0.62 else None


def _proveedor_payload(proveedor, detectado=None):
    detectado = detectado or {}
    return {
        "nombre": getattr(proveedor, "nombre", "") if proveedor else str(detectado.get("nombre") or "").strip(),
        "empresa": getattr(proveedor, "empresa", "") if proveedor else str(detectado.get("empresa") or "").strip(),
        "nit": str(detectado.get("nit") or "").strip(),
        "factura": str(detectado.get("factura") or "").strip(),
        "fecha": str(detectado.get("fecha") or "").strip(),
        "proveedorid": proveedor.pk if proveedor else None,
        "encontrado": bool(proveedor),
        "nombre_bd": proveedor.nombre if proveedor else "",
    }


def _decimal_precio_factura(value):
    texto = str(value or "").strip()
    if not texto or texto == "?":
        return None
    if "/" in texto:
        for parte in texto.split("/"):
            precio = _decimal_precio_factura(parte)
            if precio is not None:
                return precio
        return None

    texto = texto.replace("$", "").replace("COP", "").strip()
    texto = re.sub(r"\s+", "", texto)
    texto = re.sub(r"[^0-9.,-]", "", texto)
    if not texto or texto in {"-", ".", ",", "-.", "-,"}:
        return None

    negativo = texto.startswith("-")
    texto = texto.lstrip("-")
    if "." in texto and "," in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) in {1, 2} else "".join(partes)
    elif "." in texto:
        partes = texto.split(".")
        texto = partes[0] + "." + partes[-1] if len(partes) == 2 and len(partes[-1]) in {1, 2} else "".join(partes)

    try:
        precio = Decimal(texto)
    except (InvalidOperation, ValueError):
        return None
    if negativo:
        precio = -precio
    if precio <= 0:
        return None
    return precio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calcular_rentabilidad_producto(precio_venta, precio_compra):
    try:
        venta = Decimal(str(precio_venta or "0"))
        compra = Decimal(str(precio_compra or "0"))
    except (InvalidOperation, ValueError):
        return None
    if venta <= 0 or compra <= 0:
        return None

    rentabilidad = ((venta - compra) / venta) * Decimal("100")
    if rentabilidad < 0:
        rentabilidad = Decimal("0")
    if rentabilidad > 100:
        rentabilidad = Decimal("100")
    return rentabilidad.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class InventarioFotosProveedorLookupView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        nombre = (request.GET.get("term") or request.GET.get("nombre") or "").strip()
        proveedor = _buscar_proveedor_factura(nombre)
        return JsonResponse({
            "success": True,
            "found": bool(proveedor),
            "proveedor": _proveedor_payload(proveedor, {"nombre": nombre}),
        })


@method_decorator(require_POST, name="dispatch")
class InventarioFotosProcesarView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        form = InventarioFotosForm(request.POST, request.FILES)
        if not form.is_valid():
            return JsonResponse({"success": False, "errors": form.errors}, status=400)

        if not getattr(settings, "INVENTARIO_FOTOS_ALLOW_SERVER_PROCESS", False):
            return JsonResponse({
                "success": False,
                "error": "El procesamiento por fotos se ejecuta mediante el agente local del PC.",
            }, status=400)

        from .services.inventario_fotos import ejecutar_procesador_local, InventarioFotosError

        try:
            resultado = ejecutar_procesador_local(imagenes=form.cleaned_data["fotos"])
        except InventarioFotosError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=500)
        except Exception as exc:
            if getattr(settings, "DEBUG", False):
                return JsonResponse({"success": False, "error": f"Error inesperado: {exc}"}, status=500)
            return JsonResponse({"success": False, "error": "Error inesperado procesando las fotos."}, status=500)

        rows = []

        def as_bool(value):
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "si", "sí", "yes"}

        for row in (resultado.get("rows") or []):
            nombre = str((row or {}).get("producto") or (row or {}).get("nombre") or "").strip()
            codigo = str((row or {}).get("codigo_de_barras") or (row or {}).get("barcode") or "").strip()
            precio_unitario = str((row or {}).get("precio_unitario") or (row or {}).get("precio") or "").strip()
            precio_unitario_visible = str((row or {}).get("precio_unitario_visible") or "").strip()
            precio_unitario_sin_iva = str((row or {}).get("precio_unitario_sin_iva") or "").strip()
            iva_porcentaje = str((row or {}).get("iva_porcentaje") or (row or {}).get("iva") or "").strip()
            precio_incluye_iva = as_bool((row or {}).get("precio_incluye_iva"))
            precio_iva_calculado = as_bool((row or {}).get("precio_iva_calculado"))
            productoid_raw = (row or {}).get("productoid")
            try:
                productoid = int(productoid_raw) if str(productoid_raw).strip() else None
            except (TypeError, ValueError):
                productoid = None
            try:
                cantidad = int((row or {}).get("cantidad", 0))
            except (TypeError, ValueError):
                cantidad = 0

            if (not nombre and not productoid and not codigo) or cantidad <= 0:
                continue

            producto = None
            if productoid:
                producto = Producto.objects.filter(pk=productoid).only("productoid", "nombre", "codigo_de_barras").first()
            if not producto and codigo:
                producto = Producto.objects.filter(codigo_de_barras=codigo).only("productoid", "nombre", "codigo_de_barras").first()
            if not producto:
                producto = Producto.objects.filter(nombre__iexact=nombre).only("productoid", "nombre", "codigo_de_barras").first()

            rows.append({
                "producto": producto.nombre if producto else nombre,
                "original_producto": nombre,
                "cantidad": cantidad,
                "productoid": producto.pk if producto else None,
                "codigo_de_barras": getattr(producto, "codigo_de_barras", "") if producto else "",
                "precio_unitario": precio_unitario,
                "precio_unitario_visible": precio_unitario_visible,
                "precio_unitario_sin_iva": precio_unitario_sin_iva,
                "iva_porcentaje": iva_porcentaje,
                "precio_incluye_iva": precio_incluye_iva,
                "precio_iva_calculado": precio_iva_calculado,
                "encontrado": bool(producto),
                "reemplazado_por_barcode": False,
            })

        proveedor_factura = resultado.get("proveedor_factura") or {}
        proveedor_nombre = str(
            proveedor_factura.get("nombre") or resultado.get("proveedor_nombre") or resultado.get("proveedor") or ""
        ).strip()
        proveedor_nit = str(proveedor_factura.get("nit") or resultado.get("proveedor_nit") or "").strip()
        proveedor_db = _buscar_proveedor_factura(proveedor_nombre)
        proveedor_payload = _proveedor_payload(proveedor_db, {
            "nombre": proveedor_nombre,
            "empresa": str(proveedor_factura.get("empresa") or "").strip(),
            "nit": proveedor_nit,
            "factura": str(proveedor_factura.get("factura") or resultado.get("factura_numero") or "").strip(),
            "fecha": str(proveedor_factura.get("fecha") or resultado.get("factura_fecha") or "").strip(),
        })

        return JsonResponse({
            "success": True,
            "rows": rows,
            "proveedor": proveedor_nombre,
            "proveedor_nombre": proveedor_nombre,
            "proveedor_nit": proveedor_nit,
            "proveedor_factura": proveedor_payload,
            "factura_numero": proveedor_payload["factura"],
            "factura_fecha": proveedor_payload["fecha"],
            "factura_encabezados": resultado.get("factura_encabezados", {}),
            "raw_text": resultado.get("raw_text", ""),
            "raw_text_modelo": resultado.get("raw_text_modelo", ""),
            "ocr_text": resultado.get("ocr_text", ""),
            "matching_debug": resultado.get("matching_debug", []),
        })


@method_decorator(require_POST, name="dispatch")
class InventarioFotosConfirmarView(LoginRequiredMixin, View):
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = InventarioFotosConfirmarForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"success": False, "errors": form.errors}, status=400)

        sucursal = get_object_or_404(Sucursal, pk=form.cleaned_data["sucursal_id"])
        items = form.cleaned_data["items_json"]
        proveedor_data = form.cleaned_data.get("proveedor_json") or {}
        productos_a_sumar = {}

        def as_bool(value):
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "si", "sí", "yes"}

        for item in items:
            nombre = item["producto"]
            cantidad = int(item["cantidad"])
            productoid = item.get("productoid")
            codigo = (item.get("codigo_de_barras") or "").strip()
            precio_unitario = (item.get("precio_unitario") or "").strip()
            precio_unitario_visible = (item.get("precio_unitario_visible") or "").strip()
            precio_unitario_sin_iva = (item.get("precio_unitario_sin_iva") or "").strip()
            iva_porcentaje = (item.get("iva_porcentaje") or "").strip()
            precio_incluye_iva = as_bool(item.get("precio_incluye_iva"))
            precio_iva_calculado = as_bool(item.get("precio_iva_calculado"))

            producto = None
            if productoid:
                producto = Producto.objects.filter(pk=productoid).first()
            if not producto and codigo:
                producto = Producto.objects.filter(codigo_de_barras=codigo).first()
            if not producto and nombre:
                producto = Producto.objects.filter(nombre__iexact=nombre).first()
            if not producto:
                return JsonResponse({
                    "success": False,
                    "error": f"El producto '{nombre or codigo or productoid}' no existe en la base de datos."
                }, status=400)

            if producto.pk not in productos_a_sumar:
                productos_a_sumar[producto.pk] = {
                    "producto": producto,
                    "cantidad": 0,
                    "precio_unitario": precio_unitario,
                    "precio_unitario_visible": precio_unitario_visible,
                    "precio_unitario_sin_iva": precio_unitario_sin_iva,
                    "iva_porcentaje": iva_porcentaje,
                    "precio_incluye_iva": precio_incluye_iva,
                    "precio_iva_calculado": precio_iva_calculado,
                }
            productos_a_sumar[producto.pk]["cantidad"] += cantidad
            if precio_unitario and not productos_a_sumar[producto.pk].get("precio_unitario"):
                productos_a_sumar[producto.pk]["precio_unitario"] = precio_unitario
            if precio_unitario_visible and not productos_a_sumar[producto.pk].get("precio_unitario_visible"):
                productos_a_sumar[producto.pk]["precio_unitario_visible"] = precio_unitario_visible
            if precio_unitario_sin_iva and not productos_a_sumar[producto.pk].get("precio_unitario_sin_iva"):
                productos_a_sumar[producto.pk]["precio_unitario_sin_iva"] = precio_unitario_sin_iva
            if iva_porcentaje and not productos_a_sumar[producto.pk].get("iva_porcentaje"):
                productos_a_sumar[producto.pk]["iva_porcentaje"] = iva_porcentaje
            if precio_iva_calculado:
                productos_a_sumar[producto.pk]["precio_iva_calculado"] = True
                productos_a_sumar[producto.pk]["precio_incluye_iva"] = False

        proveedor = None
        proveedorid = proveedor_data.get("proveedorid")
        proveedor_nombre = (proveedor_data.get("nombre") or "").strip()
        if proveedorid:
            proveedor = Proveedor.objects.filter(pk=proveedorid).first()
        if not proveedor and proveedor_nombre:
            proveedor = _buscar_proveedor_factura(proveedor_nombre)
        if not proveedor and proveedor_data.get("create_if_missing"):
            if not proveedor_nombre:
                return JsonResponse({
                    "success": False,
                    "needs_provider": True,
                    "error": "Escribe el nombre del proveedor para poder crearlo.",
                    "proveedor_factura": _proveedor_payload(None, proveedor_data),
                }, status=409)
            proveedor = Proveedor.objects.create(
                nombre=proveedor_nombre[:100],
                empresa=(proveedor_data.get("empresa") or "")[:100],
                telefono=(proveedor_data.get("telefono") or "")[:20],
                email=(proveedor_data.get("email") or "")[:100],
                direccion=proveedor_data.get("direccion") or "",
            )
        if not proveedor:
            return JsonResponse({
                "success": False,
                "needs_provider": True,
                "error": "Confirma o crea el proveedor antes de guardar.",
                "proveedor_factura": _proveedor_payload(None, proveedor_data),
            }, status=409)

        actualizados = []
        precios_actualizados = []

        for data in productos_a_sumar.values():
            producto = data["producto"]
            cantidad = int(data["cantidad"])
            precio_compra = _decimal_precio_factura(data.get("precio_unitario"))

            inv, _created = Inventario.objects.select_for_update().get_or_create(
                sucursalid=sucursal,
                productoid=producto,
                defaults={"cantidad": 0}
            )
            Inventario.objects.filter(pk=inv.pk).update(cantidad=F("cantidad") + cantidad)

            precio_guardado = False
            if precio_compra is not None:
                precio_proveedor, precio_created = PreciosProveedor.objects.update_or_create(
                    productoid=producto,
                    proveedorid=proveedor,
                    defaults={"precio": precio_compra},
                )
                rentabilidad = _calcular_rentabilidad_producto(producto.precio, precio_compra)
                if rentabilidad is not None and producto.rentabilidad != rentabilidad:
                    producto.rentabilidad = rentabilidad
                    producto.save(update_fields=["rentabilidad"])
                precio_guardado = True
                precios_actualizados.append({
                    "id": precio_proveedor.pk,
                    "productoid": producto.pk,
                    "producto": producto.nombre,
                    "proveedorid": proveedor.pk,
                    "proveedor": proveedor.nombre,
                    "precio": str(precio_compra),
                    "precio_venta": str(producto.precio),
                    "rentabilidad": str(producto.rentabilidad),
                    "creado": bool(precio_created),
                })

            actualizados.append({
                "productoid": producto.pk,
                "producto": producto.nombre,
                "codigo_de_barras": getattr(producto, "codigo_de_barras", "") or "",
                "cantidad_sumada": cantidad,
                "precio_unitario": data.get("precio_unitario") or "",
                "precio_proveedor_guardado": precio_guardado,
                "rentabilidad": str(producto.rentabilidad),
                "precio_unitario_visible": data.get("precio_unitario_visible") or "",
                "precio_unitario_sin_iva": data.get("precio_unitario_sin_iva") or "",
                "iva_porcentaje": data.get("iva_porcentaje") or "",
                "precio_incluye_iva": as_bool(data.get("precio_incluye_iva")),
                "precio_iva_calculado": as_bool(data.get("precio_iva_calculado")),
            })

        messages.success(request, f"Inventario actualizado en {sucursal.nombre} con {len(actualizados)} producto(s).")
        return JsonResponse({
            "success": True,
            "message": f"Inventario actualizado en {sucursal.nombre}; proveedor {proveedor.nombre} vinculado con {len(precios_actualizados)} precio(s).",
            "rows": actualizados,
            "proveedor_factura": _proveedor_payload(proveedor, proveedor_data),
            "precios_actualizados": precios_actualizados,
            "redirect_url": reverse("visualizar_inventarios"),
        })

