"""Únicamente API del laboratorio local; no carga las rutas de producción."""
from django.urls import path
from mainApp import hybrid_views

urlpatterns = [
    path("api/hybrid/v1/enroll/", hybrid_views.enroll),
    path("api/hybrid/v1/session/", hybrid_views.start_session),
    path("api/hybrid/v1/catalog/", hybrid_views.catalog),
    path("api/hybrid/v1/sale/", hybrid_views.sale),
    path("api/hybrid/v1/release/", hybrid_views.release_session),
]
