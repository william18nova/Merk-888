"""Local UI dependencies for the original app; never downloads at run time."""
import hashlib
import json
from pathlib import Path
import re

from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.exceptions import SuspiciousFileOperation
from django.http import Http404, HttpResponse


class IntegratedStaticFilesHandler(StaticFilesHandler):
    """Local runserver assets never need sessions, a grant, or any database."""
    def serve(self, request):
        try:
            return super().serve(request)
        except (Http404, SuspiciousFileOperation):
            # A missing asset is not a business page: don't render a 404
            # template whose context processors may query the cloud.
            return HttpResponse("Archivo estático no encontrado.", status=404, content_type="text/plain")


def asset_root(project):
    return Path(project) / "build/hybrid/integrated-ui"


def load_assets(project):
    root = asset_root(project)
    try:
        manifest = json.loads((root / "local_vendor/manifest.json").read_text(encoding="utf-8"))
        urls, integrity = manifest["urls"], manifest["integrity"]
        if not urls or not integrity:
            raise ValueError
        files = set()
        for entry in integrity.values():
            filename = entry["file"]
            if not re.fullmatch(r"[0-9a-f]{24}\.(js|css|woff2?|ttf|eot|svg|png|gif)", filename):
                raise ValueError
            data = (root / "local_vendor" / filename).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["packaged_sha256"]:
                raise ValueError
            files.add("/static/local_vendor/" + filename)
        if any(not url.startswith("https://") or target not in files for url, target in urls.items()):
            raise ValueError
        return urls
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Faltan bibliotecas locales o cambiaron. Con conexión, ejecuta "
                           "python scripts/prepare_integrated_assets.py antes de iniciar el POS integrado.") from None


def configure_assets(namespace):
    project = namespace["BASE_DIR"]
    namespace["NOVA_INTEGRATED_ASSETS"] = load_assets(project)
    namespace["STATICFILES_DIRS"] = [asset_root(project), *namespace.get("STATICFILES_DIRS", [])]
    namespace["MIDDLEWARE"] = ["local_pos.integrated_assets.LocalAssetsMiddleware", *namespace["MIDDLEWARE"]]


class LocalAssetsMiddleware:
    def __init__(self, get_response):
        from django.conf import settings
        self.get_response = get_response
        self.replacements = sorted(settings.NOVA_INTEGRATED_ASSETS.items(), key=lambda pair: -len(pair[0]))

    def __call__(self, request):
        response = self.get_response(request)
        if not response.streaming and response.get("Content-Type", "").startswith("text/html"):
            html = response.content.decode(response.charset)
            for remote, local in self.replacements:
                html = html.replace(remote, local)
            html = re.sub(r'<link\b[^>]*href=[\'\"]https://fonts\.googleapis\.com/[^>]*>', '', html)
            response.content = html.encode(response.charset)
            if response.has_header("Content-Length"):
                response["Content-Length"] = str(len(response.content))
            # Blocks Google font @imports in original stylesheets. System fonts
            # are the fallback. Do not constrain online payment/printing fetches.
            policy = "style-src 'self' 'unsafe-inline'; font-src 'self' data:"
            existing = response.get("Content-Security-Policy")
            response["Content-Security-Policy"] = existing + ", " + policy if existing else policy
        return response
