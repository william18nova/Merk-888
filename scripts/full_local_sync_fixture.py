"""Escenario visual ficticio, solo invocado por full_local_lab --review-demo."""
from uuid import uuid4


def seed_review(user, remote):
    from django.conf import settings
    from local_pos.models import LocalCommand, LocalSaleSession
    from local_pos import sales, expenses
    assert settings.LOCAL_CONFIG["database"] == "nova_full_local_lab"
    assert getattr(settings, "LOCAL_SALES_DEMO_CONTROLS", False)
    assert remote.url.startswith("http://127.0.0.1:")
    session = LocalSaleSession.objects.get()
    product = next(row for row in session.products.values() if "ARROZ" in row["name"].upper())
    settings.LOCAL_SALES_SIMULATE_OFFLINE = True
    sale = sales.checkout(user, {"operation_id": str(uuid4()), "session_id": str(session.session_id),
        "items": [{"id": product["id"], "quantity": 1}], "cash_received": str(product["price"]),
        "expected_total": str(product["price"])}, remote)
    expenses.create(user, {"operation_id": str(uuid4()), "session_id": str(session.session_id),
        "concept": "PAGO FICTICIO DE REVISIÓN", "amount_base": "1000", "method": "nequi", "expected_tax": True}, remote)
    # Simular un rechazo solo en esta base temporal. El reintento posterior usa
    # contratos válidos contra el origen ficticio y conserva el mismo UUID.
    row = LocalCommand.objects.get(pk=sale["id"])
    row.state = "conflict"
    row.local_result = {**row.local_result, "sync": {**row.local_result.get("sync", {}), "code": "product"},
                        "error": "Rechazo ficticio para probar la pantalla."}
    row.save(update_fields=["state", "local_result"])
