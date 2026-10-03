"""Transporte HTTPS sin redirecciones ni credenciales en la URL/logs."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler
from uuid import UUID

from hybrid_client.client import NoRedirects, RemoteError, cloud_url
from pos_shared.replica import canonical


class ReplicaRemote:
    def __init__(self, config, *, allow_local=False):
        if not isinstance(config, dict) or set(config) != {"url", "device_id", "secret", "session_id"}:
            raise ValueError("Archivo de vinculación inválido.")
        self.url = cloud_url(config["url"], allow_local=allow_local)
        self.device_id = str(UUID(config["device_id"]))
        self.session_id = str(UUID(config["session_id"]))
        if (not isinstance(config["secret"], str) or len(config["secret"]) != 64
                or any(c not in "0123456789abcdef" for c in config["secret"])):
            raise ValueError("Credencial de dispositivo inválida.")
        self._secret = config["secret"]
        from .pilot_tls import context_for
        self._tls_context = context_for(self.url)

    def call(self, action, data):
        if action not in {"prepare", "page", "catalog", "sale", "expense", "operation", "operation-read"}:
            raise ValueError("Operación de réplica inválida.")
        prefix = "replica/" if action in {"prepare", "page"} else ""
        request = Request(self.url + "/api/hybrid/v1/" + prefix + action + "/",
            data=canonical({**data, "session_id": self.session_id}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": f"Bearer {self.device_id}.{self._secret}"})
        try:
            with build_opener(NoRedirects(), HTTPSHandler(context=self._tls_context)).open(request, timeout=3 if action in {"sale", "expense", "operation", "operation-read"} else 30) as response:
                raw = response.read(2000001)
            if len(raw) > 2000000:
                raise RemoteError("La página supera el límite permitido.", 502)
            result = json.loads(raw)
            if not isinstance(result, dict) or result.pop("ok", None) is not True:
                raise RemoteError("El servidor no confirmó la descarga.", 502)
            return result
        except HTTPError as exc:
            try:
                code = json.loads(exc.read(16000)).get("code", "server")
            except (ValueError, AttributeError):
                code = "server"
            # No repetir mensajes remotos que pudieran contener datos sensibles.
            raise RemoteError("El servidor no autorizó o no pudo preparar la copia.", exc.code, code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RemoteError("Sin comunicación; la última copia válida sigue guardada.") from None
