from django.urls import include, path
from django.conf import settings
from . import views, sale_views, business_views, sync_views

urlpatterns = [path("local/estado/", views.status, name="local_runtime_status"),
               path("local/sincronizacion/", sync_views.index, name="local_sync"),
               path("local/sincronizacion/enviar/", sync_views.synchronize, name="local_sync_send"),
               path("local/sincronizacion/<uuid:operation_id>/", sync_views.detail, name="local_sync_detail"),
               path("local/sincronizacion/<uuid:operation_id>/reintentar/", sync_views.retry, name="local_sync_retry"),
               path("local/devoluciones/", business_views.return_page, name="local_return"),
               path("generar_venta/", sale_views.page, name="generar_venta"),
               path("local/sales/api/<path:action>", sale_views.endpoint, name="local_sale_api"),
               path("local/sales/read/<str:name>/", sale_views.lookup, name="local_sale_lookup"),
               path("local/sales/assets/<str:name>", sale_views.asset, name="local_sale_asset"),
               path("", include("NovaSoft.urls"))]
if getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False):
    urlpatterns = [path("caja/registrar-pago/", business_views.expense_page, name="registrar_egreso"),
                   path("turno_caja/", business_views.close_page, name="turno_caja")] + urlpatterns
