import argparse
import os
from pathlib import Path
import threading
import webbrowser

from hybrid_client.client import Client
from hybrid_client.server import make_server, sync_worker
from hybrid_client.store import Store, default_directory


class ProcessLock:
    """Un escritor por carpeta, incluso si alguien cambia el puerto."""
    def __init__(self, directory):
        self.file = (Path(directory) / "client.lock").open("a+b")
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.file.close()
            raise

    def close(self):
        self.file.close()


def main():
    parser = argparse.ArgumentParser(description="Nova POS híbrido · piloto en efectivo")
    parser.add_argument("--data-dir", type=Path, default=default_directory())
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--allow-local-server", action="store_true", help="Solo pruebas con nube en 127.0.0.1")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--install", action="store_true", help="Instalar el paquete compilado para este usuario")
    commands.add_argument("--export-backup", type=Path, metavar="ARCHIVO", help="Crear respaldo cifrado; pide contraseña en la terminal")
    commands.add_argument("--verify-backup", type=Path, metavar="ARCHIVO", help="Verificar contraseña, integridad y contenido sin restaurar")
    commands.add_argument("--restore-backup", type=Path, metavar="ARCHIVO", help="Recuperar en una carpeta nueva, bloqueada para revisión")
    commands.add_argument("--resume-recovery", type=Path, metavar="CARPETA", help="Conciliar una recuperación con autorización del Web Master")
    parser.add_argument("--recovery-dir", type=Path, help="Carpeta NUEVA para revisar una recuperación; no activa otra caja")
    args = parser.parse_args()
    if bool(args.restore_backup) != bool(args.recovery_dir):
        parser.error("--restore-backup requiere --recovery-dir (y viceversa).")
    if args.resume_recovery:
        import getpass
        from hybrid_client.recovery import Recovery
        from hybrid_client.client import RemoteError
        from pos_shared.protocol import ProtocolError
        lock = None
        try:
            lock = ProcessLock(args.resume_recovery)
            url = input("Dirección HTTPS del POS del respaldo: ").strip()
            recovery = Recovery(args.resume_recovery, url)
            # Un código nuevo permite retomar una autorización vencida/reemitida.
            code = getpass.getpass("Código de recuperación (vacío para continuar uno ya presentado): ").strip()
            if code:
                recovery.prepare(code)
            result = recovery.finish()
            if result["state"] == "review":
                print("Respaldo presentado. Revisa/aprueba el resumen en Seguridad → Equipos híbridos y repite este comando dejando el código vacío.")
                print(result["summary"])
            else:
                print("Conciliación completada. Inicia sesión de nuevo y configura la impresora; no se imprimieron copias.")
                print("Carpeta del POS recuperado:", result["directory"])
                print('Abre NovaPOS --data-dir "' + result["directory"] + '"')
        except (ProtocolError, RemoteError, OSError, ValueError, RuntimeError) as exc:
            parser.exit(1, f"Recuperación sin activar: {exc}\nConserva el respaldo y esta carpeta para reintentar; no registres sus ventas a mano.\n")
        finally:
            if lock:
                lock.close()
        return
    if args.export_backup or args.verify_backup or args.restore_backup:
        from hybrid_client.backup_cli import run
        from pos_shared.protocol import ProtocolError
        try:
            run(args)
        except (ProtocolError, OSError, ValueError) as exc:
            parser.exit(1, f"No se completó el respaldo/recuperación: {exc}\n")
        return
    if args.install:
        from hybrid_client.installer import install
        print(install())
        return
    if not 1024 <= args.port <= 65535:
        parser.error("Puerto fuera de rango.")
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        lock = ProcessLock(args.data_dir)
    except OSError:
        print("NovaPOS ya está abierto para esta carpeta. Abre http://127.0.0.1:8792/")
        return
    try:
        store = Store(args.data_dir)
        client = Client(store, allow_local=args.allow_local_server)
        server = make_server(client, args.port)
        stop = threading.Event()
        worker = threading.Thread(target=sync_worker, args=(client, stop), daemon=True)
        worker.start()
        print_worker = threading.Thread(target=client.printer.worker, args=(stop,), daemon=True)
        print_worker.start()
        url = f"http://127.0.0.1:{args.port}/"
        print(f"NovaPOS disponible en {url} — datos: {store.directory}")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            client.printer.wake.set()
            server.server_close()
            worker.join(timeout=6)
            print_worker.join(timeout=1)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
