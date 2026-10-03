"""Impresión local sin Django. No reenvía automáticamente trabajos ambiguos."""
import json
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import UUID

from pos_shared.protocol import ProtocolError, canonical
from .client import NoRedirects

CUT = "\x1d\x56\x41\x00"
DRAWER = b"\x1b\x70\x00\x32\x32"
DEFAULT = {"backend": "browser", "paper": "80", "automatic": False,
           "cut": True, "drawer": False, "agent_port": 8787, "agent_token": "", "printer": ""}


class PrinterError(Exception):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def receipt_text(row, paper, *, copy=False):
    width = 32 if paper == "58" else 48
    result = json.loads(row["result"])
    source = result.get("receipt_text") or row["receipt"]
    header = ["COPIA / REIMPRESION"] if copy else []
    if row["state"] == "pending":
        header.append("PENDIENTE DE SINCRONIZAR")
    header.extend(["Ref. local: " + row["id"], ""])
    # Datos del catálogo o de la nube no deben inyectar comandos de impresora.
    source = "".join(ch if ch == "\n" or not unicodedata.category(ch).startswith("C") else " " for ch in source)
    lines = []
    for line in header + source.splitlines():
        lines.extend(textwrap.wrap(line, width=width, replace_whitespace=False, drop_whitespace=False) or [""])
    return "\n".join(lines) + "\n\n\n"


def escpos(text, *, cut=True, drawer=False):
    data = b"\x1b\x40\x1b\x74\x00" + text.encode("cp437", errors="replace")
    if drawer:
        data += DRAWER
    if cut:
        data += CUT.encode("ascii")
    return data


def agent_post(port, token, path, payload):
    request = Request(f"http://127.0.0.1:{port}/{path}", data=canonical(payload).encode(),
                      headers={"Content-Type": "application/json", "X-Pos-Agent-Token": token}, method="POST")
    try:
        # Nunca enviar secretos locales mediante proxies ni seguir redirecciones.
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=8) as response:
            raw = response.read(16000)
            if raw:
                try:
                    result = json.loads(raw)
                except ValueError:
                    result = None  # Agentes anteriores responden texto plano.
                if isinstance(result, dict) and (result.get("success") is False or result.get("ok") is False):
                    raise PrinterError("El agente no confirmó la impresión. Revisa la impresora antes de pedir una copia.", uncertain=True)
    except HTTPError as exc:
        raise PrinterError("El agente rechazó la solicitud. Revisa su token y configuración.", uncertain=exc.code >= 500) from None
    except (URLError, TimeoutError, OSError):
        raise PrinterError("No llegó confirmación del agente. Puede haber recibido el trabajo; revisa el papel antes de reimprimir.", uncertain=True) from None


def send_job(payload, token):
    if payload["backend"] == "agent":
        if not token:
            raise PrinterError("Configura el token del agente de impresión.")
        should_cut = payload["cut"]
        agent_post(payload["agent_port"], token, "print", {
            "text": payload["text"] + (CUT if should_cut else ""),
            "cut": should_cut, "cut_command_embedded": should_cut,
        })
        if payload["drawer"]:
            try:
                agent_post(payload["agent_port"], token, "kick", {})
            except PrinterError:
                return "Comprobante enviado al agente; no se pudo confirmar la apertura del cajón. No reimprimas por ese motivo."
        return "Enviado al agente. Comprueba el papel; su respuesta no garantiza que haya salido físicamente."
    if payload["backend"] == "cups":
        executable = shutil.which("lp") if sys.platform.startswith("linux") else None
        if not executable:
            raise PrinterError("CUPS no está disponible. Instala/configura lp en Linux o usa el diálogo del navegador.")
        media = "Custom.58x3276mm" if payload["paper"] == "58" else "Custom.80x60mm"
        command = [executable, "-d", payload["printer"], "-o", "raw", "-o", f"media={media}", "-t", "NovaPOS"]
        try:
            subprocess.run(command, input=escpos(payload["text"], cut=payload["cut"], drawer=payload["drawer"]),
                           check=True, timeout=8, capture_output=True, shell=False)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            raise PrinterError("CUPS no confirmó el trabajo. Revisa la cola y el papel antes de reimprimir.", uncertain=True) from None
        return "Enviado a la cola CUPS. La cola aceptó el trabajo; comprueba la salida física."
    raise PrinterError("Usa el botón de impresión del navegador.")


class PrinterManager:
    def __init__(self, client):
        self.client, self.store = client, client.store
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.store.recover_inflight_prints()

    def config(self, *, private=False):
        config = {**DEFAULT, **self.store.get("printer", {})}
        if not private:
            config["has_token"] = bool(config.pop("agent_token"))
            config["linux_available"] = sys.platform.startswith("linux")
        return config

    def configure(self, data):
        self.client.unlock({"pin": data.get("pin", "")})
        config = self.config(private=True)
        for key in ("backend", "paper", "automatic", "cut", "drawer", "agent_port", "printer"):
            if key in data:
                config[key] = data[key]
        if data.get("agent_token"):
            config["agent_token"] = data["agent_token"]
        if config["backend"] not in {"browser", "agent", "cups"} or config["paper"] not in {"58", "80"}:
            raise ProtocolError("Selecciona una conexión y un tamaño de papel válidos.")
        if any(type(config[key]) is not bool for key in ("automatic", "cut", "drawer")):
            raise ProtocolError("Opciones de impresión inválidas.")
        if type(config["agent_port"]) is not int or not 1024 <= config["agent_port"] <= 65535 or config["agent_port"] == 8792:
            raise ProtocolError("Puerto del agente inválido; normalmente es 8787.")
        token = config["agent_token"]
        if not isinstance(token, str) or len(token) > 512 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
            raise ProtocolError("El token debe ser texto sin espacios ni saltos de línea.")
        if config["backend"] == "agent" and not token:
            raise ProtocolError("Ingresa el token del agente instalado en este PC.")
        if not isinstance(config["printer"], str) or (config["printer"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,127}",config["printer"])):
            raise ProtocolError("Nombre de cola CUPS inválido.")
        if config["backend"] == "cups" and (not sys.platform.startswith("linux") or not config["printer"]):
            raise ProtocolError("CUPS requiere Linux y el nombre de una cola de impresión.")
        if config["backend"] == "browser":
            config["automatic"] = False
            config["drawer"] = False
        if self.store.print_jobs(state="queued") or self.store.print_jobs(state="sending"):
            raise ProtocolError("Espera a que terminen los trabajos antes de cambiar la impresora.")
        self.store.set("printer", config)
        return self.config()

    def enqueue(self, operation_id, *, request_id=None, automatic=False, confirm_copy=False):
        row = self.store.operation(operation_id)
        session = self.store.get("session") or {}
        if not self.client.unlocked or not row or row["session"] != session.get("session_id") or row["state"] == "conflict":
            raise ProtocolError("Solo puedes imprimir ventas válidas de tu sesión actual.")
        config = self.config(private=True)
        if config["backend"] == "browser" or (automatic and not config["automatic"]):
            return {"state": "browser", "paper": config["paper"]}
        job_id = "auto:" + operation_id if automatic else "manual:" + str(UUID(str(request_id)))
        existing = self.store.print_job(job_id)
        if existing:
            if existing["operation_id"] != operation_id:
                raise ProtocolError("La solicitud de impresión corresponde a otra venta.")
            return self.public(existing)
        previous = self.store.print_jobs(operation_id=operation_id)
        if previous and automatic:
            return self.public(previous[-1])
        if previous and not automatic:
            if any(job["state"] in {"queued", "sending"} for job in previous):
                raise ProtocolError("El comprobante está en proceso. Espera antes de pedir una copia.")
            if confirm_copy is not True:
                raise ProtocolError("Confirma que quieres una copia; el comprobante puede haberse impreso antes.")
        config.pop("agent_token")
        config["text"] = receipt_text(row,config["paper"],copy=bool(previous))
        config["drawer"] = config["drawer"] and automatic and not previous
        job = self.store.enqueue_print(job_id, operation_id, config)
        self.wake.set()
        return self.public(job)

    @staticmethod
    def public(job):
        return {"id":job["id"],"operation_id":job["operation_id"],"state":job["state"],"message":job["message"]}

    def process_one(self):
        with self.lock:
            jobs = self.store.print_jobs(state="queued")
            if not jobs:
                return False
            job = jobs[0]
            # Persistir ANTES del envío. Si se cae el proceso, no enviar de nuevo
            # al arrancar: el papel pudo salir aunque no recibimos la respuesta.
            self.store.mark_print(job["id"],"sending","Enviando a la impresora…")
            try:
                message = send_job(json.loads(job["payload"]), self.config(private=True)["agent_token"])
                state = "sent"
            except PrinterError as exc:
                state, message = ("uncertain" if exc.uncertain else "failed"), str(exc)
            except Exception:
                state, message = "uncertain", "No se pudo confirmar la impresión. Revisa el papel antes de pedir una copia."
            self.store.mark_print(job["id"],state,message)
            return True

    def worker(self, stop):
        while not stop.is_set():
            try:
                if self.process_one():
                    continue
            except Exception:
                pass  # Disco ocupado: conservar el trabajo, nunca repetir uno 'sending'.
            self.wake.wait(1)
            self.wake.clear()
