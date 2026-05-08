from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import secrets
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request, send_file

APP_DIR = Path(__file__).resolve().parent
DEFAULT_SHARED_TOKEN = "BmFclqQdWkKjArLIYvakHG426BuLDUtJA0zVG5DJOgjZTWSEVa_i0hxiyXskSHUi"
DEFAULT_TOKEN = os.getenv("INVENTARIO_AGENT_TOKEN", os.getenv("POS_AGENT_TOKEN", DEFAULT_SHARED_TOKEN))
DEFAULT_SCRIPT = os.getenv("INVENTARIO_FOTOS_SCRIPT", str(APP_DIR / "gemini_selenium_cli.py"))
DEFAULT_TIMEOUT = int(os.getenv("INVENTARIO_FOTOS_TIMEOUT", "900"))
DEFAULT_HOST = os.getenv("INVENTARIO_AGENT_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("INVENTARIO_AGENT_PORT", "8788"))
DEBUG_DIR = APP_DIR / "inventario_agent_debug"
MOBILE_UPLOAD_DIR = DEBUG_DIR / "mobile_uploads"
MOBILE_SESSION_TTL = int(os.getenv("INVENTARIO_MOBILE_SESSION_TTL", str(6 * 60 * 60)))
MOBILE_SESSIONS = {}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def _origin():
    return request.headers.get("Origin") or "*"


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = _origin()
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Inventory-Agent-Token, X-Requested-With"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def _options_ok():
    return ("", 204)


def _authorized() -> bool:
    expected = (DEFAULT_TOKEN or "").strip()
    received = (request.headers.get("X-Inventory-Agent-Token") or "").strip()
    return bool(expected) and received == expected


def _local_lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def _cleanup_mobile_sessions() -> None:
    now = time.time()
    expired = [
        session_id
        for session_id, session in list(MOBILE_SESSIONS.items())
        if now - session.get("created_at", now) > MOBILE_SESSION_TTL
    ]
    for session_id in expired:
        session = MOBILE_SESSIONS.pop(session_id, None)
        if session:
            shutil.rmtree(session.get("dir", ""), ignore_errors=True)


def _new_mobile_session() -> dict:
    _cleanup_mobile_sessions()
    session_id = secrets.token_urlsafe(12)
    session_token = secrets.token_urlsafe(24)
    session_dir = MOBILE_UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    upload_url = f"http://{_local_lan_ip()}:{DEFAULT_PORT}/mobile/capture/{session_id}?t={session_token}"
    session = {
        "id": session_id,
        "token": session_token,
        "created_at": time.time(),
        "dir": session_dir,
        "upload_url": upload_url,
        "files": [],
    }
    MOBILE_SESSIONS[session_id] = session
    return session


def _mobile_session(session_id: str):
    _cleanup_mobile_sessions()
    return MOBILE_SESSIONS.get(session_id)


def _mobile_token_ok(session: dict | None) -> bool:
    if not session:
        return False
    received = (request.args.get("t") or request.form.get("t") or "").strip()
    return bool(received) and secrets.compare_digest(received, session.get("token", ""))


def _mobile_file_url(session_id: str, file_id: str, token: str) -> str:
    return f"{request.host_url.rstrip('/')}/mobile/session/{session_id}/file/{file_id}?t={token}"


def _save_mobile_uploaded_files(session: dict, files) -> list[dict]:
    saved = []
    target_dir = Path(session["dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    for idx, file_storage in enumerate(files, start=1):
        suffix = Path(file_storage.filename or f"foto_{idx}.jpg").suffix.lower() or ".jpg"
        if len(suffix) > 12 or not suffix.startswith("."):
            suffix = ".jpg"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_id = f"celular_{stamp}_{idx}{suffix}"
        path = target_dir / file_id
        file_storage.save(path)
        info = {
            "id": file_id,
            "name": file_storage.filename or file_id,
            "size": path.stat().st_size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        session["files"].append(info)
        saved.append(info)
    return saved


def _save_uploaded_files(files, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for idx, file_storage in enumerate(files, start=1):
        suffix = Path(file_storage.filename or f"imagen_{idx}.jpg").suffix or ".jpg"
        path = target_dir / f"imagen_{idx}{suffix.lower()}"
        file_storage.save(path)
        saved.append(path)
    return saved


def _tail(text: str, limit: int = 6000) -> str:
    text = text or ""
    return text[-limit:].strip()


def _read_result_json(output_json: Path):
    if not output_json.exists():
        return None, None
    try:
        return json.loads(output_json.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"No se pudo leer el JSON de salida: {exc}"


def _write_debug_run(proc, output_json: Path):
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        (DEBUG_DIR / "ultimo_stdout.log").write_text(stdout, encoding="utf-8", errors="replace")
        (DEBUG_DIR / "ultimo_stderr.log").write_text(stderr, encoding="utf-8", errors="replace")
        (DEBUG_DIR / f"{stamp}_stdout.log").write_text(stdout, encoding="utf-8", errors="replace")
        (DEBUG_DIR / f"{stamp}_stderr.log").write_text(stderr, encoding="utf-8", errors="replace")

        if output_json.exists():
            contenido = output_json.read_text(encoding="utf-8", errors="replace")
            (DEBUG_DIR / "ultimo_resultado.json").write_text(contenido, encoding="utf-8", errors="replace")
            (DEBUG_DIR / f"{stamp}_resultado.json").write_text(contenido, encoding="utf-8", errors="replace")
    except Exception:
        pass


def _build_script_error(proc, data=None, json_error=None):
    partes = []

    if data and data.get("error"):
        partes.append(str(data.get("error")).strip())
    if json_error:
        partes.append(str(json_error).strip())

    stderr_tail = _tail(getattr(proc, "stderr", ""))
    stdout_tail = _tail(getattr(proc, "stdout", ""))
    if stderr_tail:
        partes.append(f"STDERR: {stderr_tail}")
    if stdout_tail:
        partes.append(f"STDOUT: {stdout_tail}")

    if not partes:
        partes.append(f"Codigo de salida {getattr(proc, 'returncode', 'desconocido')}")

    return "\n\n".join(partes)


def _run_script(images_dir: Path, catalog_path: Path, output_json: Path):
    script = Path(DEFAULT_SCRIPT).expanduser()
    if not script.exists():
        return None, f"No se encontró el script local: {script}"

    cmd = [
        sys.executable,
        str(script),
        "--images-dir",
        str(images_dir),
        "--csv",
        str(catalog_path),
        "--json-out",
        str(output_json),
        "--no-wait",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_TIMEOUT,
            cwd=str(script.parent),
        )
    except subprocess.TimeoutExpired as exc:
        return None, f"Tiempo agotado ejecutando el script local ({exc.timeout}s)."
    except Exception as exc:
        return None, f"Error lanzando el script local: {exc}"

    _write_debug_run(proc, output_json)
    data, json_error = _read_result_json(output_json)

    if proc.returncode != 0:
        detalle = _build_script_error(proc, data=data, json_error=json_error)
        return data, f"El script local fallo: {detalle}"

    if proc.returncode != 0:
        detalle = (proc.stderr or proc.stdout or f"Código de salida {proc.returncode}").strip()
        return None, f"El script local falló: {detalle}"

    if not output_json.exists():
        return None, "El script terminó pero no generó resultado.json"

    try:
        data = json.loads(output_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"No se pudo leer el JSON de salida: {exc}"

    if not data.get("ok"):
        return data, data.get("error") or "El script local devolvio un error."

    if not data.get("ok"):
        return None, data.get("error") or "El script local devolvió un error."

    return data, None


@app.route("/ping", methods=["GET", "OPTIONS"])
def ping():
    if request.method == "OPTIONS":
        return _options_ok()
    if not _authorized():
        return jsonify({"ok": False, "message": "Token inválido."}), 401
    return jsonify({
        "ok": True,
        "message": "Agente local conectado y listo.",
        "script": str(Path(DEFAULT_SCRIPT).name),
    })


@app.route("/mobile/session", methods=["POST", "OPTIONS"])
def mobile_session_create():
    if request.method == "OPTIONS":
        return _options_ok()
    if not _authorized():
        return jsonify({"success": False, "error": "Token invalido para crear sesion movil."}), 401

    session = _new_mobile_session()
    return jsonify({
        "success": True,
        "session_id": session["id"],
        "upload_url": session["upload_url"],
        "qr_url": f"{request.host_url.rstrip('/')}/mobile/session/{session['id']}/qr?t={session['token']}",
        "expires_in": MOBILE_SESSION_TTL,
    })


@app.route("/mobile/session/<session_id>/qr", methods=["GET", "OPTIONS"])
def mobile_session_qr(session_id):
    if request.method == "OPTIONS":
        return _options_ok()

    session = _mobile_session(session_id)
    if not _mobile_token_ok(session):
        return jsonify({"success": False, "error": "Sesion movil invalida o vencida."}), 404

    try:
        import qrcode
    except Exception:
        return jsonify({
            "success": False,
            "error": "Falta instalar qrcode. Ejecuta start_inventario_agent.cmd para instalar dependencias.",
        }), 500

    img = qrcode.make(session["upload_url"])
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="inventario_movil_qr.png")


@app.route("/mobile/session/<session_id>/files", methods=["GET", "OPTIONS"])
def mobile_session_files(session_id):
    if request.method == "OPTIONS":
        return _options_ok()
    if not _authorized():
        return jsonify({"success": False, "error": "Token invalido."}), 401

    session = _mobile_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Sesion movil invalida o vencida."}), 404

    files = []
    for item in session.get("files", []):
        file_id = item["id"]
        files.append({
            **item,
            "download_url": _mobile_file_url(session_id, file_id, session["token"]),
        })
    return jsonify({"success": True, "files": files})


@app.route("/mobile/session/<session_id>/file/<file_id>", methods=["GET", "OPTIONS"])
def mobile_session_file(session_id, file_id):
    if request.method == "OPTIONS":
        return _options_ok()

    session = _mobile_session(session_id)
    if not _mobile_token_ok(session):
        return jsonify({"success": False, "error": "Sesion movil invalida o vencida."}), 404

    allowed = {item["id"] for item in session.get("files", [])}
    if file_id not in allowed:
        return jsonify({"success": False, "error": "Foto no encontrada."}), 404

    path = Path(session["dir"]) / file_id
    if not path.exists():
        return jsonify({"success": False, "error": "Foto no encontrada."}), 404

    return send_file(path, as_attachment=False, download_name=file_id)


@app.route("/mobile/session/<session_id>", methods=["DELETE", "OPTIONS"])
def mobile_session_delete(session_id):
    if request.method == "OPTIONS":
        return _options_ok()
    if not _authorized():
        return jsonify({"success": False, "error": "Token invalido."}), 401

    session = MOBILE_SESSIONS.pop(session_id, None)
    if session:
        shutil.rmtree(session.get("dir", ""), ignore_errors=True)
    return jsonify({"success": True})


def _mobile_capture_html(session: dict) -> str:
    upload_url = f"/mobile/capture/{session['id']}?t={session['token']}"
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fotos de inventario</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, -apple-system, Segoe UI, Arial, sans-serif; }}
    body {{ margin: 0; background: #10233f; color: #f7fbff; }}
    main {{ min-height: 100vh; display: grid; place-items: center; padding: 22px; }}
    .box {{ width: min(520px, 100%); background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.18); border-radius: 18px; padding: 22px; box-shadow: 0 18px 48px rgba(0,0,0,.28); }}
    h1 {{ margin: 0 0 8px; font-size: 25px; }}
    p {{ margin: 0 0 18px; color: rgba(247,251,255,.78); line-height: 1.45; }}
    label {{ display: grid; gap: 10px; min-height: 170px; place-items: center; text-align: center; border: 2px dashed rgba(255,255,255,.35); border-radius: 16px; background: rgba(255,255,255,.08); padding: 18px; }}
    input {{ width: 1px; height: 1px; opacity: 0; position: absolute; }}
    strong {{ display: block; font-size: 18px; }}
    span {{ color: rgba(247,251,255,.72); }}
    button {{ width: 100%; margin-top: 16px; border: 0; border-radius: 14px; padding: 14px 16px; font-weight: 800; font-size: 16px; background: #4da6ff; color: #061326; }}
    button:disabled {{ opacity: .55; }}
    .status {{ margin-top: 14px; min-height: 22px; font-weight: 700; }}
    .ok {{ color: #a8ffc1; }}
    .err {{ color: #ffd2d2; }}
    .list {{ margin-top: 12px; display: grid; gap: 8px; font-size: 13px; color: rgba(247,251,255,.75); }}
  </style>
</head>
<body>
  <main>
    <section class="box">
      <h1>Enviar fotos al inventario</h1>
      <p>Toma una o varias fotos de la factura. Apareceran automaticamente en la pantalla del PC.</p>
      <form id="form" enctype="multipart/form-data">
        <label for="fotos">
          <div>
            <strong>Abrir camara o galeria</strong>
            <span>Selecciona las fotos y toca Enviar</span>
          </div>
        </label>
        <input id="fotos" name="fotos" type="file" accept="image/*" capture="environment" multiple>
        <button id="send" type="submit">Enviar al PC</button>
      </form>
      <div id="status" class="status"></div>
      <div id="list" class="list"></div>
    </section>
  </main>
  <script>
    const form = document.getElementById("form");
    const fotos = document.getElementById("fotos");
    const send = document.getElementById("send");
    const statusBox = document.getElementById("status");
    const list = document.getElementById("list");
    function setStatus(cls, text) {{
      statusBox.className = "status " + cls;
      statusBox.textContent = text;
    }}
    fotos.addEventListener("change", () => {{
      const files = Array.from(fotos.files || []);
      list.innerHTML = files.map((file, idx) => `<div>${{idx + 1}}. ${{file.name}} (${{Math.round(file.size / 1024)}} KB)</div>`).join("");
    }});
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      if (!fotos.files || !fotos.files.length) {{
        setStatus("err", "Selecciona al menos una foto.");
        return;
      }}
      const fd = new FormData();
      Array.from(fotos.files).forEach(file => fd.append("fotos", file, file.name));
      try {{
        send.disabled = true;
        setStatus("", "Enviando...");
        const response = await fetch("{upload_url}", {{ method: "POST", body: fd }});
        const data = await response.json().catch(() => ({{}}));
        if (!response.ok || data.success === false) throw new Error(data.error || "No se pudo enviar.");
        setStatus("ok", `Listo. ${{data.files.length}} foto(s) enviada(s) al PC.`);
        fotos.value = "";
        list.innerHTML = "";
      }} catch (err) {{
        setStatus("err", err.message || "No se pudo enviar.");
      }} finally {{
        send.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""


@app.route("/mobile/capture/<session_id>", methods=["GET", "POST", "OPTIONS"])
def mobile_capture(session_id):
    if request.method == "OPTIONS":
        return _options_ok()

    session = _mobile_session(session_id)
    if not _mobile_token_ok(session):
        if request.method == "POST":
            return jsonify({"success": False, "error": "Sesion movil invalida o vencida."}), 404
        return Response("Sesion movil invalida o vencida.", status=404, mimetype="text/plain; charset=utf-8")

    if request.method == "GET":
        return Response(_mobile_capture_html(session), mimetype="text/html; charset=utf-8")

    fotos = request.files.getlist("fotos")
    if not fotos:
        return jsonify({"success": False, "error": "No se recibieron fotos."}), 400

    saved = _save_mobile_uploaded_files(session, fotos)
    return jsonify({"success": True, "files": saved})


@app.route("/inventory/process", methods=["POST", "OPTIONS"])
def inventory_process():
    if request.method == "OPTIONS":
        return _options_ok()

    if not _authorized():
        return jsonify({"success": False, "error": "Token inválido para el agente local."}), 401

    fotos = request.files.getlist("fotos")
    catalogo = request.files.get("catalogo")

    if not fotos:
        return jsonify({"success": False, "error": "No se recibieron fotos."}), 400
    if catalogo is None:
        return jsonify({"success": False, "error": "No se recibió el catálogo CSV."}), 400

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base_dir = DEBUG_DIR / "runs" / run_id
    images_dir = base_dir / "imagenes"
    output_json = base_dir / "resultado.json"
    catalog_path = base_dir / "catalogo.csv"
    conservar_debug = False

    try:
        _save_uploaded_files(fotos, images_dir)
        catalogo.save(catalog_path)

        data, error = _run_script(images_dir, catalog_path, output_json)
        if error:
            conservar_debug = True
            payload = {"success": False}
            payload.update(data or {})
            payload["success"] = False
            payload["error"] = error
            payload["debug_dir"] = str(DEBUG_DIR)
            payload["run_dir"] = str(base_dir)
            return jsonify(payload), 500

        payload = {"success": True}
        payload.update(data or {})
        return jsonify(payload)
    finally:
        if not conservar_debug:
            shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    print(f"Agente local de inventario escuchando en http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    print(f"URL para celulares en esta red: http://{_local_lan_ip()}:{DEFAULT_PORT}")
    print(f"Usando script: {DEFAULT_SCRIPT}")
    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT, debug=False, threaded=True)
