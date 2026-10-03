"""Dependencias UI offline; descarga únicamente bibliotecas públicas explícitas.

Se ejecuta al preparar el laboratorio, nunca durante una solicitud del cajero.
Conserva versiones del proyecto y las cabeceras/licencias de los archivos.
"""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CHART_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
ROOT_URLS = [
    *[f"https://cdn.datatables.net/{version}/{kind}/jquery.dataTables.min.{kind}"
      for version in ("1.11.5", "1.13.8") for kind in ("css", "js")],
    *[f"https://code.jquery.com/jquery-{version}.min.js" for version in ("3.6.0", "3.7.1")],
    *[f"https://code.jquery.com/ui/{version}/{path}" for version in ("1.13.2", "1.13.3")
      for path in ("jquery-ui.min.js", "themes/base/jquery-ui.css")],
    *[f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{version}/css/all.min.css"
      for version in ("6.0.0-beta3", "6.5.1", "6.5.2")],
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/js/all.min.js",
    "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js",
    CHART_URL,
]
ALLOWED_PREFIXES = (
    "https://code.jquery.com/", "https://cdn.datatables.net/1.11.5/", "https://cdn.datatables.net/1.13.8/",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/",
    "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/",
    "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/",
)
CSS_URL = re.compile(r"url\(\s*['\"]?([^)'\"\s]+)['\"]?\s*\)")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Una dependencia intentó redirigir la descarga; revisar manualmente.")


def build(static_root, cache_dir=None):
    static_root = Path(static_root)
    target = static_root / "local_vendor"
    target.mkdir(parents=True, exist_ok=True)
    # Cache de recursos públicos, separada de configuración y datos privados.
    # Permite volver a abrir el laboratorio sin internet tras prepararlo una vez.
    cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parents[1] / "build/hybrid/full-local-asset-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    urls, checksums = {}, {}
    opener = build_opener(NoRedirects())

    def fetch(raw_url):
        url, fragment = urldefrag(raw_url)
        if url in urls:
            return urls[url] + ("#" + fragment if fragment else "")
        if not url.startswith(ALLOWED_PREFIXES):
            raise RuntimeError("Dependencia fuera del catálogo de bibliotecas permitido.")
        parsed = urlsplit(url)
        suffix = Path(parsed.path).suffix
        if suffix not in {".js", ".css", ".woff", ".woff2", ".ttf", ".eot", ".svg", ".png", ".gif"}:
            raise RuntimeError("Tipo de dependencia no permitido.")
        cache_key = hashlib.sha256(url.encode()).hexdigest()
        cached = cache_dir / (cache_key + ".source")
        checksum = cache_dir / (cache_key + ".sha256")
        if cached.exists() and checksum.exists():
            data = cached.read_bytes()
            if hashlib.sha256(data).hexdigest() != checksum.read_text():
                raise RuntimeError("La caché de una biblioteca cambió; revisar antes de empaquetar.")
        else:
            request = Request(url, headers={"User-Agent": "NovaPOS-Local-Asset-Builder/1.0"})
            with opener.open(request, timeout=25) as response:
                data = response.read(8_000_001)
        if len(data) > 8_000_000:
            raise RuntimeError("Dependencia demasiado grande.")
        if not (cached.exists() and checksum.exists()):
            cached.write_bytes(data)
            checksum.write_text(hashlib.sha256(data).hexdigest())
        filename = hashlib.sha256(url.encode()).hexdigest()[:24] + suffix
        urls[url] = "/static/local_vendor/" + filename
        checksums[url] = {"source_sha256": hashlib.sha256(data).hexdigest(), "file": filename}
        if suffix == ".css":
            css = data.decode("utf-8")
            def replace(match):
                path = match.group(1)
                if path.startswith(("data:", "#")):
                    return match.group(0)
                return 'url("' + fetch(urljoin(url, path)) + '")'
            data = CSS_URL.sub(replace, css).encode("utf-8")
        checksums[url]["packaged_sha256"] = hashlib.sha256(data).hexdigest()
        (target / filename).write_bytes(data)
        return urls[url] + ("#" + fragment if fragment else "")

    for url in ROOT_URLS:
        fetch(url)
    # El proyecto no fija Chart.js; el laboratorio sí, para ser reproducible.
    if CHART_URL in urls:
        urls["https://cdn.jsdelivr.net/npm/chart.js"] = urls[CHART_URL]
    # Fuentes de sistema: no hay solicitudes a Google Fonts estando offline.
    for css_file in static_root.rglob("*.css"):
        if target in css_file.parents:
            continue
        css = css_file.read_text(encoding="utf-8")
        css = re.sub(r"@import\s+url\(['\"]?https://fonts\.googleapis\.com/[^)]*\)\s*;", "", css)
        css_file.write_text(css, encoding="utf-8")
    manifest = {"urls": urls, "integrity": checksums}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Dependencias locales empaquetadas: {len(checksums)} archivos.", flush=True)
    return manifest
