#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    from pathlib import Path
    project = Path(__file__).resolve().parent
    # Opt-in privado de este equipo: no afecta WSGI, migraciones ni producción.
    if (len(sys.argv) > 1 and sys.argv[1] == "runserver"
            and (project / ".novapos-ptm-lab").is_file()
            and not any(a == "--settings" or a.startswith("--settings=") for a in sys.argv)):
        import runpy
        args = sys.argv[2:]
        if args and args != ["--noreload"]:
            raise RuntimeError("Pruebas PTM activas: usa python manage.py runserver sin argumentos.")
        sys.argv = [str(project / "scripts" / "ptm_lab.py"), "--manage"]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return
    from local_pos.integrated_bootstrap import runserver_context
    with runserver_context(sys.argv, Path(__file__).resolve().parent):
        execute()


def execute():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'NovaSoft.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
