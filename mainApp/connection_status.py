"""Small read-only database status for the navbar, without connection details."""
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


STATES = {
    "online": ("Online", "Conectado a la base de datos online."),
    "offline": ("Local", "Sin conexión a la base online. Trabajando con la copia local."),
    "synchronizing": ("Sincronizando", "Usando la copia local mientras se sincronizan los pendientes con la base online."),
    "blocked": ("Revisar", "La conexión o los pendientes necesitan revisión. No se han descartado datos."),
    "unavailable": ("Sin conexión", "La base de datos no está disponible. No se ha confirmado el modo local."),
    "checking": ("Comprobando", "Comprobando la conexión con la base de datos."),
    "unknown": ("Sin verificar", "No se pudo comprobar el estado. Verifica que el programa siga funcionando."),
}


def status_for_mode(mode):
    mode = mode if mode in STATES else "unknown"
    label, description = STATES[mode]
    return {"mode": mode, "label": label, "description": description}


@never_cache
@require_GET
def database_status(request):
    if not getattr(getattr(request, "user", None), "is_authenticated", False):
        return JsonResponse(status_for_mode("unknown"), status=401)
    # In hybrid mode ConnectionMiddleware binds the entire request to the
    # selected database. Never probe Aiven directly or retry on another DB here.
    mode = getattr(request, "nova_mode", None)
    if mode is None:
        mode = "unknown" if getattr(settings, "NOVA_INTEGRATED_POS_ENABLED", False) else "online"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                mode = "unknown"
    except DatabaseError:
        return JsonResponse(status_for_mode("unavailable"), status=503)
    return JsonResponse(status_for_mode(mode))
