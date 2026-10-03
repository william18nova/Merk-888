"""Empaqueta Generar Venta ORIGINAL sin importar settings, modelos ni credenciales.

Django solo se usa al construir. El cliente instalado sigue sin Django ni acceso
directo a PostgreSQL. La lista explícita impide empaquetar archivos del proyecto.
"""
import hashlib
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ("base.html", "navbar.html", "generar_venta.html", "modal_venta.html")
STATIC = (
    "css/navbar.css", "css/barcode_camera.css", "css/generar_venta.css", "css/modal_venta.css",
    "javascript/number_input_wheel_guard.js", "javascript/navbar.js", "javascript/page_navigation.js",
    "javascript/barcode_camera.js", "javascript/sale_draft_lifecycle.js",
    "javascript/product_autocomplete.js", "javascript/generar_venta.js",
    "vendor/jquery/jquery-3.6.4.min.js", "vendor/jquery-ui/jquery-ui-1.13.2.min.js",
    "vendor/jquery-ui/jquery-ui-1.13.2.min.css", "images/logoNovaAdvance.png",
)


def build():
    from django.conf import settings
    from django.template import Context, Engine
    from django.urls import path
    import django

    if settings.configured:
        raise RuntimeError("Ejecuta este constructor en un proceso aislado, sin settings del POS.")
    urls = types.ModuleType("_hybrid_build_urls")
    names = set()
    for name in TEMPLATES:
        names.update(re.findall(r"{%\s*url\s+['\"]([^'\"]+)", (ROOT / "mainApp/templates" / name).read_text(encoding="utf-8")))
    urls.urlpatterns = [path("generar_venta/" if name == "generar_venta" else f"_sale/{name}/", lambda _r: None, name=name) for name in sorted(names)]
    sys.modules[urls.__name__] = urls
    settings.configure(SECRET_KEY="build-only-not-a-server", INSTALLED_APPS=[], USE_I18N=False,
                       STATIC_URL="/static/", ROOT_URLCONF=urls.__name__)
    django.setup()
    engine = Engine(dirs=[str(ROOT / "mainApp/templates")], libraries={"static": "django.templatetags.static"})
    context = {
        "hybrid_local": True, "turno_requerido": True, "turno_activo": True, "venta_habilitada": True,
        "sucursal_nombre": "__HYBRID_BRANCH__", "puntopago_nombre": "__HYBRID_POINT__", "turno_id": 1,
        "form": {"initial": {"sucursal": 1, "puntopago": 1}},
        "request": {"user": {"pk": 1}, "resolver_match": {"url_name": "generar_venta"}},
        "user": {"is_authenticated": True}, "nav_session_name": "__HYBRID_USER__",
        "nav_session_username": "__HYBRID_USER__", "csrf_token": "build-placeholder",
        "nav_menu": [{"label": "Generar Venta", "url": "/generar_venta/", "active": True},
                     {"label": "Conexión y sesión", "url": "/"}],
        "payment_methods": [{"code": "efectivo", "label": "Efectivo", "is_cash": True, "active": True}],
    }
    html = engine.get_template("generar_venta.html").render(Context(context))
    # CSP autoriza únicamente estos scripts empaquetados (incluidos los inline originales).
    html = re.sub(r"<script(?![^>]*\bnonce=)", '<script nonce="__LOCAL_NONCE__"', html)
    html = html.replace('data-zxing-url="https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js"', 'data-zxing-url=""')
    target = ROOT / "hybrid_client/assets/original_sale"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(html, encoding="utf-8")
    manifest = {}
    paths = list(STATIC)
    paths.extend(str(p.relative_to(ROOT / "mainApp/static")).replace("\\", "/")
                 for p in (ROOT / "mainApp/static/vendor/jquery-ui/images").glob("*.png"))
    for name in paths:
        source = ROOT / "mainApp/static" / name
        data = source.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        if name.endswith(".css"):
            # Fuentes del sistema mientras se empaqueta una fuente local; nunca CDN offline.
            data = re.sub(rb"@import\s+url\(['\"]https?://[^)]*\)\s*;", b"", data)
        output = target / "static" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    (target / "sources.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Generar Venta original empaquetada; sin configuración ni datos de producción.")


if __name__ == "__main__":
    build()
