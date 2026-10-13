"""Idempotency for the ORIGINAL POS views, not the reduced offline form.

The receipt and all business writes commit in the same database transaction.
In particular, an unacknowledged commit is not evidence that a sale failed.
No form content, password or authorization code is stored in the receipt.
"""
import json
import hashlib
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils.crypto import salted_hmac

from mainApp.models import SolicitudPOS

HEADER = "X-Nova-Operation-ID"


def protect_business(action):
    """Do not import local models in PythonAnywhere / ordinary cloud settings."""
    def decorate(view):
        @wraps(view)
        def wrapped(self, request, *args, **kwargs):
            if not getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
                return view(self, request, *args, **kwargs)
            if action == "refund" and request.POST.get("accion") == "imprimir_factura":
                return view(self, request, *args, **kwargs)
            from local_pos.integrated_business import protect
            return protect(action)(view)(self, request, *args, **kwargs)
        return wrapped
    return decorate


def request_fingerprint(request):
    values = [(key, request.POST.getlist(key)) for key in sorted(request.POST)
              if key != "csrfmiddlewaretoken"]
    # A keyed hash also prevents guessing low-entropy authorization codes from
    # a database backup. It is never used as permission to execute the request.
    return salted_hmac("nova.pos.original-request.v1",
        json.dumps(values, ensure_ascii=False, separators=(",", ":")),
        algorithm="sha256").hexdigest()


def failure(message, code, status=409):
    return JsonResponse({"success": False, "error": message, "code": code}, status=status)


def lock_operation(operation_id):
    """Also serialize an absence check with an in-flight uncommitted insert."""
    if connection.vendor == "postgresql":
        key = int.from_bytes(hashlib.sha256(b"nova-pos-receipt:" + operation_id.bytes).digest()[:8],
                             "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def recover_receipt(actor, operation_id, *, expected_fingerprint=None):
    """Internal reconciliation API; an exception is NEVER a 'not found'.

    The future integrated worker calls this on Aiven before considering an
    ambiguous original request eligible for retry. Author identity is required.
    """
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionError("Una sesión activa es necesaria para consultar la confirmación.")
    operation_id = UUID(str(operation_id))
    with transaction.atomic():
        lock_operation(operation_id)
        receipt = SolicitudPOS.objects.filter(pk=operation_id).first()
        if receipt is None:
            return {"status": "absent", "operation_id": str(operation_id)}
        if receipt.usuario_id != actor.pk or receipt.accion != "sale":
            raise PermissionError("La solicitud no pertenece a este usuario.")
        if expected_fingerprint is not None and receipt.huella != expected_fingerprint:
            raise PermissionError("La solicitud confirmada no coincide con este intento.")
        if not receipt.respuesta:
            raise RuntimeError("Solicitud sin confirmación; se requiere revisión.")
        return {"status": "accepted", "operation_id": str(operation_id), "response": receipt.respuesta}


def protect_sale(view):
    """Opt-in via UUID header; old clients and the existing API remain valid."""
    @wraps(view)
    def wrapped(self, request, *args, **kwargs):
        raw_id = request.headers.get(HEADER)
        if raw_id is None:
            if getattr(settings, "NOVA_INTEGRATED_POS_ENABLED", False):
                return failure("Actualiza esta página antes de cobrar; falta la referencia protegida de la venta.",
                               "operation_required", 409)
            return view(self, request, *args, **kwargs)
        if not request.user.is_authenticated or not request.user.is_active:
            return failure("Inicia sesión para registrar la venta.", "authentication", 401)
        try:
            operation_id = UUID(raw_id)
            if operation_id.version != 4:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return failure("La referencia de esta venta no es válida.", "invalid_operation", 400)
        digest = request_fingerprint(request)
        with transaction.atomic():
            lock_operation(operation_id)
            # A primary key arbitrates simultaneous tabs/processes. A failed
            # attempt rolls this placeholder back along with the business data.
            receipt, _ = SolicitudPOS.objects.get_or_create(pk=operation_id, defaults={
                "usuario_id": request.user.pk, "accion": "sale", "huella": digest,
            })
            receipt = SolicitudPOS.objects.select_for_update().get(pk=receipt.pk)
            if (receipt.usuario_id != request.user.pk or receipt.accion != "sale"
                    or receipt.huella != digest):
                return failure("Esa referencia corresponde a otra solicitud. No se creó otra venta.",
                               "operation_conflict")
            if receipt.respuesta:
                response = JsonResponse(receipt.respuesta)
                response["X-Nova-Replayed"] = "1"
            else:
                response = view(self, request, *args, **kwargs)
                if response.streaming or not response.get("Content-Type", "").startswith("application/json"):
                    transaction.set_rollback(True)
                    return failure("No se pudo confirmar la venta. Conserva el carrito.", "unconfirmed_response", 503)
                data = json.loads(response.content)
                if response.status_code != 200 or not isinstance(data, dict) or data.get("success") is not True:
                    transaction.set_rollback(True)
                    return response
                sale_id = data.get("venta_id")
                if type(sale_id) is not int or sale_id <= 0:
                    transaction.set_rollback(True)
                    return failure("No se pudo confirmar la referencia de la factura.", "unconfirmed_sale", 503)
                receipt.venta_id = sale_id
                # A print token is short-lived authority, not business data.
                receipt.respuesta = {key: value for key, value in data.items() if key != "print_token"}
                receipt.save(update_fields=["venta", "respuesta"])
            response["Cache-Control"] = "no-store"
            response[HEADER] = str(operation_id)
            return response
    @wraps(view)
    def dispatch(self, request, *args, **kwargs):
        if getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
            from local_pos.integrated_journal import protect_original_request
            return protect_original_request(request, lambda: wrapped(self, request, *args, **kwargs))
        return wrapped(self, request, *args, **kwargs)
    return dispatch
