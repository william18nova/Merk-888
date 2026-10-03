"""Servidor FICTICIO: solo protocolo autenticado; sin administración web."""
from django.urls import path
from django.http import HttpResponseNotFound
from mainApp import hybrid_views as views

urlpatterns = [
    path("api/hybrid/v1/enroll/", views.enroll),
    path("api/hybrid/v1/session/", views.start_session),
    path("api/hybrid/v1/catalog/", views.catalog),
    path("api/hybrid/v1/sale/", views.sale),
    path("api/hybrid/v1/expense/", views.expense),
    path("api/hybrid/v1/operation/", views.operation),
    path("api/hybrid/v1/operation-read/", views.operation_read),
    path("api/hybrid/v1/replica/prepare/", views.replica_prepare),
    path("api/hybrid/v1/replica/page/", views.replica_page),
    # The business close response reverses this URL. The lab source exposes
    # no cash-withdrawal UI; clients render their own confirmed result.
    path("turno_caja/retiro/<int:turno_id>/", lambda request, turno_id: HttpResponseNotFound(), name="turno_caja_retiro"),
]
