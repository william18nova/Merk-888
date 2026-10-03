"""Adaptador de lectura para la página original. No replica el backend Django."""
import hashlib
import html
import json
import mimetypes
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs

from pos_shared.protocol import ProtocolError

BUNDLE = Path(__file__).with_name("assets") / "original_sale"


def session_context(client):
    session = client.store.get("session") or {}
    # Compatibilidad con autorizaciones antiguas: aislar borradores por sesión,
    # nunca usar un usuario genérico compartido entre todos los cajeros.
    scope = int(hashlib.sha256(str(session.get("session_id", "")).encode()).hexdigest()[:12], 16)
    return {**session, "user_id": session.get("user_id") or scope,
            "branch_id": session.get("branch_id") or scope,
            "point_id": session.get("point_id") or scope,
            "turn_id": session.get("turn_id") or scope}


def render_page(client, nonce):
    if not client.unlocked or not client.store.get("session"):
        raise ProtocolError("Primero desbloquea la sesión desde Conexión y sesión.")
    if not (BUNDLE / "index.html").is_file():
        raise ProtocolError("Falta empaquetar Generar Venta. Ejecuta scripts/build_hybrid_sale_assets.py al construir.")
    session = session_context(client)
    body = (BUNDLE / "index.html").read_text(encoding="utf-8")
    for key, value in {"USER": session["user"], "BRANCH": session["branch"], "POINT": session["point"]}.items():
        body = body.replace(f"__HYBRID_{key}__", html.escape(str(value), quote=True))
    context = json.dumps(session, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return body.replace("__HYBRID_CONTEXT__", context).replace("__LOCAL_NONCE__", nonce).encode()


def static_file(path):
    name = path.removeprefix("/static/")
    manifest_file = BUNDLE / "sources.json"
    if not manifest_file.is_file() or name not in json.loads(manifest_file.read_text(encoding="utf-8")):
        return None
    # Solo archivos explícitamente incluidos, ni traversal ni fallback al proyecto.
    data = (BUNDLE / "static" / name).read_bytes()
    mime = {".js": "text/javascript", ".css": "text/css"}.get(Path(name).suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return data, mime


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value).upper()) if not unicodedata.combining(c))


def read_endpoint(client, name, params):
    rows = [{k: p[k] for k in ("id", "name", "barcode", "price", "stock")} for p in client.store.products().values()]
    term = normalized(params.get("term", ""))
    if name == "producto_snapshot":
        return {"results": rows}
    if name.startswith("producto_autocomplete"):
        if name.endswith("_id"):
            selected = [p for p in rows if str(p["id"]).startswith(term)]
        elif name.endswith(("_barras", "_codigo")):
            selected = [p for p in rows if str(p["barcode"]).startswith(term) or str(p["id"]).startswith(term)]
        else:
            selected = [p for p in rows if all(word in normalized(p["name"]) or word in str(p["id"]) for word in term.split())]
        return {"results": [{**p, "text": p["name"]} for p in selected[:40]]}
    if name == "buscar_producto_por_codigo":
        matches = [p for p in rows if p["barcode"] == params.get("codigo_de_barras")]
        if len(matches) != 1:
            return {"exists": False, "ambiguous": len(matches) > 1}
        p = matches[0]
        return {"exists": True, "producto": {"id": p["id"], "nombre": p["name"], "codigo_de_barras": p["barcode"], "precio": p["price"], "stock": p["stock"]}}
    if name == "verificar_producto":
        p = next((p for p in rows if str(p["id"]) == str(params.get("producto_id"))), None)
        if not p:
            return {"exists": False, "error": "Producto no disponible en el catálogo local."}
        return {"exists": True, "nombre": p["name"], "codigo_de_barras": p["barcode"],
                "precio_unitario": p["price"], "cantidad_disponible": p["stock"]}
    session = session_context(client)
    if name == "sucursal_autocomplete":
        return {"results": [{"id": session["branch_id"], "text": session["branch"]}]}
    if name == "puntopago_autocomplete":
        return {"results": [{"id": session["point_id"], "text": session["point"]}]}
    if name == "cliente_autocomplete":
        return {"results": []}
    raise ProtocolError("Esta función todavía no está disponible en el piloto híbrido.")


def query_values(query):
    return {key: values[-1] for key, values in parse_qs(query, max_num_fields=30).items()}
