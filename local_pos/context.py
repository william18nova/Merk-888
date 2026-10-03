from django.conf import settings


def context(request):
    return {"full_local_development": getattr(settings, "HYBRID_LOCAL_ENABLED", False),
            "full_local_sales": getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False),
            "full_local_operations": getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False),
            "full_local_installed": getattr(settings, "LOCAL_INSTALLED_RUNTIME", False),
            "full_local_sale_demo": getattr(settings, "LOCAL_SALES_DEMO_CONTROLS", False)}
