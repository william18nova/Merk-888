# mainApp/context_processors.py
from django.conf import settings
from .permissions import build_nav_menu


def _first_token(value):
    parts = str(value or "").strip().split()
    return parts[0] if parts else ""


def _session_user_payload(user):
    if not getattr(user, "is_authenticated", False):
        return {
            "nav_session_name": "",
            "nav_session_username": "",
            "nav_session_initials": "",
        }

    username = (
        getattr(user, "nombreusuario", "")
        or getattr(user, "username", "")
        or str(user)
    )

    empleado = None
    try:
        empleado = getattr(user, "empleado", None)
    except Exception:
        empleado = None

    first_name = _first_token(getattr(empleado, "nombre", "")) if empleado else ""
    first_last = _first_token(getattr(empleado, "apellido", "")) if empleado else ""
    display_name = " ".join(part for part in [first_name, first_last] if part).strip()
    if not display_name:
        display_name = username

    initials_source = display_name or username
    initials = "".join(part[:1] for part in initials_source.split()[:2]).upper()
    if not initials:
        initials = (username[:2] or "U").upper()

    return {
        "nav_session_name": display_name,
        "nav_session_username": username,
        "nav_session_initials": initials,
    }


def pos_agent(request):
    return {
        "POS_AGENT_URL": getattr(settings, "POS_AGENT_URL", "http://127.0.0.1:8787"),
        "POS_AGENT_TOKEN": getattr(settings, "POS_AGENT_TOKEN", ""),
        "INVENTARIO_AGENT_URL": getattr(settings, "INVENTARIO_AGENT_URL", "http://127.0.0.1:8788"),
        "INVENTARIO_AGENT_TOKEN": getattr(settings, "INVENTARIO_AGENT_TOKEN", ""),
    }


def permissions_nav(request):
    user = getattr(request, "user", None)
    return {
        "nav_menu": build_nav_menu(
            user,
            getattr(request, "path", ""),
        ),
        **_session_user_payload(user),
    }
