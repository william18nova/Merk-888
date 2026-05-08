from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from django.conf import settings

from mainApp.models import Producto


class InventarioFotosError(Exception):
    pass


def exportar_catalogo_csv(destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["nombre", "codigo_de_barras", "productoid"])
        for producto in Producto.objects.order_by("nombre").only("nombre", "codigo_de_barras", "productoid"):
            writer.writerow([
                producto.nombre,
                producto.codigo_de_barras or "",
                producto.productoid,
            ])
    return destino


def guardar_imagenes_temporales(archivos: Iterable, destino: Path) -> list[Path]:
    destino.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for idx, archivo in enumerate(archivos, start=1):
        suffix = Path(getattr(archivo, "name", f"imagen_{idx}.jpg")).suffix or ".jpg"
        ruta = destino / f"imagen_{idx}{suffix.lower()}"
        with ruta.open("wb") as fh:
            for chunk in archivo.chunks():
                fh.write(chunk)
        rutas.append(ruta)
    return rutas


def ejecutar_procesador_local(*, imagenes: Iterable, script_path: str | None = None, timeout: int | None = None) -> dict:
    base_dir = Path(tempfile.mkdtemp(prefix="inventario_fotos_"))
    images_dir = base_dir / "imagenes"
    output_json = base_dir / "resultado.json"
    catalogo_csv = base_dir / "catalogo.csv"

    try:
        rutas = guardar_imagenes_temporales(imagenes, images_dir)
        if not rutas:
            raise InventarioFotosError("No se recibieron imágenes válidas.")

        exportar_catalogo_csv(catalogo_csv)

        script = Path(script_path or getattr(settings, "INVENTARIO_FOTOS_SCRIPT", "")).expanduser()
        if not script.exists():
            raise InventarioFotosError(f"No encontré el script local: {script}")

        cmd = [
            sys.executable,
            str(script),
            "--images-dir",
            str(images_dir),
            "--csv",
            str(catalogo_csv),
            "--json-out",
            str(output_json),
            "--no-wait",
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or getattr(settings, "INVENTARIO_FOTOS_TIMEOUT", 900),
            cwd=str(script.parent),
        )

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            detalle = stderr or stdout or f"Código de salida {proc.returncode}"
            raise InventarioFotosError(f"El script local falló: {detalle}")

        if not output_json.exists():
            raise InventarioFotosError("El script terminó pero no generó resultado.json")

        data = json.loads(output_json.read_text(encoding="utf-8"))
        if not data.get("ok"):
            raise InventarioFotosError(data.get("error") or "El script local devolvió un error.")

        return data

    except subprocess.TimeoutExpired as exc:
        raise InventarioFotosError(f"Tiempo agotado ejecutando el script local ({exc.timeout}s).") from exc
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
