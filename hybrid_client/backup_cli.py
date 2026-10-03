"""La contraseña solo se pide de forma interactiva, nunca por argumento o log."""
import getpass
import json

from .backup import encrypt, read_backup, restore_for_review, snapshot, summary, write_new
from pos_shared.protocol import ProtocolError


def run(args):
    if args.export_backup:
        password = getpass.getpass("Contraseña del respaldo (mínimo 12 caracteres): ")
        if password != getpass.getpass("Repite la contraseña: "):
            raise ProtocolError("Las contraseñas no coinciden; no se creó el archivo.")
        document = snapshot(args.data_dir / "operations.sqlite3")
        write_new(args.export_backup, encrypt(document, password))
        print("Respaldo cifrado creado. Guárdalo fuera de este disco y conserva la contraseña por separado.")
    else:
        password = getpass.getpass("Contraseña del respaldo: ")
        document = read_backup(args.verify_backup or args.restore_backup, password)
        if args.restore_backup:
            restore_for_review(document, args.recovery_dir)
            print("Recuperación verificada PARA REVISIÓN. No sustituye ni activa la caja; requiere conciliación asistida con la nube.")
        else:
            print("Contraseña, integridad y estructura del respaldo verificadas. No se modificó la caja.")
    print(json.dumps(summary(document), ensure_ascii=False, indent=2))
