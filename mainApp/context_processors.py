# mainApp/context_processors.py
from django.conf import settings
from .permissions import build_nav_menu

def pos_agent(request):
    return {
        "POS_AGENT_URL": getattr(settings, "POS_AGENT_URL", "http://127.0.0.1:8787"),
        "POS_AGENT_TOKEN": getattr(settings, "POS_AGENT_TOKEN", ""),
    }


def permissions_nav(request):
    return {
        "nav_menu": build_nav_menu(
            getattr(request, "user", None),
            getattr(request, "path", ""),
        )
    }
