from django.urls import path
from django.http import HttpResponse
from mainApp.hybrid_views import replica_prepare, replica_page, catalog, sale, expense, operation, operation_read

urlpatterns = [path("api/hybrid/v1/replica/prepare/", replica_prepare),
               path("api/hybrid/v1/replica/page/", replica_page),
               path("api/hybrid/v1/catalog/", catalog), path("api/hybrid/v1/sale/", sale),
               path("api/hybrid/v1/expense/", expense), path("api/hybrid/v1/operation/", operation),
               path("api/hybrid/v1/operation-read/", operation_read),
               path("turno_caja/retiro/<int:turno_id>/", lambda request, turno_id: HttpResponse(status=404), name="turno_caja_retiro")]
