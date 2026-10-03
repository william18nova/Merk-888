"""Contrato de la réplica de referencia; nunca contiene SQL ni credenciales."""
import hashlib
import json

VERSION = 1
MAX_ROWS = 60000
MAX_BYTES = 24000000
PAGE_SIZE = 200
ENTITIES = ("branch", "point", "category", "product", "stock", "client", "payment_method")
# Campos explícitos: no serializar modelos completos ni relaciones de usuarios.
FIELDS = {
    "branch": ("nombre",),
    "point": ("nombre", "sucursalid_id"),
    "category": ("nombre", "descripcion"),
    "product": ("nombre", "descripcion", "precio", "categoria_id", "codigo_de_barras", "iva",
                "impuesto_consumo", "icui", "ibua", "rentabilidad", "precio_anterior", "tipo_ptm"),
    "stock": ("productoid_id", "sucursalid_id", "cantidad"),
    "client": ("nombre", "apellido", "numerodocumento"),
    "payment_method": ("nombre", "activo", "es_efectivo", "es_sistema", "aplica_4xmil_egresos", "orden", "version"),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def row_key(row):
    return row["entity"] + ":" + str(row["id"])


def manifest(rows):
    return digest(sorted(rows, key=row_key))
