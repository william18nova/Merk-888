# mainApp/admin.py
from django.contrib import admin
from .models import CambioDevolucion


@admin.register(CambioDevolucion)
class CambioDevolucionAdmin(admin.ModelAdmin):
    list_display = (
        "cambioid",
        "venta_ref",        # ✅ en vez de "venta"
        "productoid",
        "cantidad",
        "tipo",
        "estado",
        "fecha",
    )
    list_select_related = ("ventaid", "productoid", "detalle_id")
    search_fields = ("cambioid", "tipo", "estado", "motivo", "ventaid__ventaid")
    list_filter = ("tipo", "estado", "fecha")

    @admin.display(description="Venta")
    def venta_ref(self, obj):
        return obj.ventaid_id  # o: return obj.ventaid.ventaid si no es null
        return obj.cantidad * obj.detalle.preciounitario
