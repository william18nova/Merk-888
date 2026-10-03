"""Confianza TLS privada del laboratorio: nunca instala una CA en Windows/Linux."""
import hashlib
import ipaddress
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit

FORMAT = "nova-fictional-two-pc-v1"


def private_host(value):
    address = ipaddress.ip_address(value)
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in networks):
        raise ValueError("Usa una IPv4 de la red privada, no una dirección pública.")
    return str(address)


def validate_link(data):
    from hybrid_client.client import cloud_url
    if not isinstance(data, dict) or set(data) != {"format", "url", "certificate", "sha256"} or data["format"] != FORMAT:
        raise ValueError("Archivo de conexión de pruebas inválido.")
    url = cloud_url(data["url"])
    parsed = urlsplit(url)
    private_host(parsed.hostname)
    if not parsed.port or not 1024 <= parsed.port <= 65535:
        raise ValueError("Puerto de laboratorio inválido.")
    cert = data["certificate"]
    if not isinstance(cert, str) or len(cert) > 20000:
        raise ValueError("Certificado de pruebas inválido.")
    der = ssl.PEM_cert_to_DER_cert(cert)
    if hashlib.sha256(der).hexdigest() != data["sha256"]:
        raise ValueError("La huella del certificado no coincide.")
    return url


def context_for(url):
    config = os.environ.get("NOVA_LOCAL_CONFIG")
    if not config:
        return None
    path = Path(config).parent / "pilot-trust.json"
    if not path.exists():
        return None
    from local_pos.runtime import read_json
    data = read_json(path)
    if validate_link(data) != url:
        raise ValueError("Esta caja ficticia solo puede comunicarse con su servidor de pruebas.")
    # Solo este transporte confía en esta CA. No se desactiva CERT_REQUIRED,
    # la comprobación de hostname ni se modifica el almacén del sistema.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=data["certificate"])
    return context
