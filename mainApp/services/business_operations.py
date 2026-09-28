"""Operaciones compartidas por la web y Telegram; sin SQL/código generado por IA."""
import json
from decimal import Decimal, InvalidOperation

from django.db import transaction


class BusinessOperationError(ValueError):
    pass


@transaction.atomic
def update_inventory_item(*, branch_id, product_id, quantity, mode, expected=None):
    from mainApp.models import Inventario, Producto, Sucursal
    if mode not in {"sumar", "fijar"} or isinstance(quantity, bool) or not isinstance(quantity, int):
        raise BusinessOperationError("Indica una cantidad entera y si deseas sumarla o fijar un conteo.")
    if not -2147483648 <= quantity <= 2147483647:
        raise BusinessOperationError("La cantidad está fuera del rango permitido.")
    if not Producto.objects.filter(pk=product_id).exists() or not Sucursal.objects.filter(pk=branch_id).exists():
        raise BusinessOperationError("El producto o la sucursal ya no existe.")
    item, _ = Inventario.objects.select_for_update().get_or_create(
        sucursalid_id=branch_id, productoid_id=product_id, defaults={"cantidad": 0},
    )
    before = int(item.cantidad or 0)
    if expected is not None and before != expected:
        raise BusinessOperationError("El inventario cambió desde la propuesta. Pide una nueva para revisar el valor actual.")
    if mode == "sumar" and before > 9000:
        raise BusinessOperationError("Este producto nunca se a contado cuentelo antes de surtir")
    after = before + quantity if mode == "sumar" else quantity
    if not -2147483648 <= after <= 2147483647:
        raise BusinessOperationError("La cantidad resultante está fuera del rango permitido.")
    item.cantidad = after
    item.save(update_fields=["cantidad"])
    return {"before": before, "new_cantidad": after, "product_name": item.productoid.nombre,
            "mode": "add" if mode == "sumar" else "exact", "delta": quantity}


def validate_supplier_order(data):
    from mainApp.forms import PedidoProveedorForm
    from mainApp.models import PreciosProveedor
    form = PedidoProveedorForm(data)
    if not form.is_valid():
        raise BusinessOperationError("; ".join(str(message) for errors in form.errors.values() for message in errors))
    try:
        lines = json.loads(form.cleaned_data["detalles"])
        if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
            raise ValueError
        clean, total, seen = [], Decimal("0.00"), set()
        for line in lines:
            raw_pid = str(line["productoid"])
            if not raw_pid.isascii() or not raw_pid.isdigit() or len(raw_pid) > 19:
                raise ValueError
            pid = int(raw_pid)
            quantity = Decimal(str(line["cantidad"]))
            price = Decimal(str(line["precio_unitario"]))
            if (pid <= 0 or pid in seen or not quantity.is_finite() or quantity != quantity.to_integral_value()
                    or not 1 <= quantity <= 2147483647 or not price.is_finite() or price <= 0
                    or price != price.quantize(Decimal("0.01"))):
                raise ValueError
            if not PreciosProveedor.objects.filter(productoid_id=pid, proveedorid=form.cleaned_data["proveedor"]).exists():
                raise BusinessOperationError(f"El proveedor no tiene registrado el producto #{pid}.")
            seen.add(pid)
            total += quantity * price
            clean.append({"productoid": pid, "cantidad": int(quantity), "precio_unitario": price})
        if total > Decimal("99999999.99"):
            raise BusinessOperationError("El total supera el límite del pedido.")
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        if isinstance(exc, BusinessOperationError):
            raise
        raise BusinessOperationError("Revisa los productos: cantidades enteras positivas, sin repetirlos, y precios positivos con máximo dos decimales.") from None
    return form, clean, total


@transaction.atomic
def create_supplier_order(data):
    from mainApp.models import PedidoProveedor, DetallePedidoProveedor
    form, lines, total = validate_supplier_order(data)
    order = PedidoProveedor.objects.create(
        proveedorid=form.cleaned_data["proveedor"], sucursalid=form.cleaned_data["sucursal"],
        fechaestimadaentrega=form.cleaned_data.get("fechaestimadaentrega"),
        comentario=form.cleaned_data.get("comentario", ""), costototal=total, estado="En espera",
    )
    DetallePedidoProveedor.objects.bulk_create([
        DetallePedidoProveedor(pedidoid=order, productoid_id=line["productoid"],
                               cantidad=line["cantidad"], preciounitario=line["precio_unitario"])
        for line in lines
    ])
    return order
