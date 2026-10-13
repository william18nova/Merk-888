"""Shared settings overlay, also exercised against disposable test databases."""


def configure(namespace, config, database, *, journal=False):
    namespace["LOCAL_CONFIG"] = config
    namespace["DATABASES"] = {**namespace["DATABASES"], "offline": database}
    namespace["DATABASE_ROUTERS"] = ["local_pos.request_context.IntegratedRouter", *namespace.get("DATABASE_ROUTERS", [])]
    # Django resolves duplicate commands in favor of the EARLIER app. Our
    # bounded offline migration check must override staticfiles.runserver.
    namespace["INSTALLED_APPS"] = ["local_pos", *namespace["INSTALLED_APPS"]]
    namespace["HYBRID_LOCAL_ENABLED"] = True
    namespace["NOVA_INTEGRATED_POS_ENABLED"] = True
    namespace["SESSION_ENGINE"] = "django.contrib.sessions.backends.db"
    namespace["AUTHENTICATION_BACKENDS"] = ["local_pos.integrated_auth.IntegratedBackend"]
    namespace["ALLOWED_HOSTS"] = ["127.0.0.1", "localhost", "[::1]"]
    namespace["SESSION_COOKIE_SECURE"] = False  # Localhost only, never the hosted site.
    cookie = "nova_integrated_" + config["instance_id"].replace("-", "")[:12]
    namespace["SESSION_COOKIE_NAME"] = cookie
    namespace["CACHES"] = {**namespace["CACHES"], "offline": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": cookie + "_offline"}}
    middleware = ["local_pos.integrated_middleware.ConnectionMiddleware", *namespace["MIDDLEWARE"]]
    auth_index = middleware.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    middleware.insert(auth_index + 1, "local_pos.integrated_middleware.OfflineAuthorizationMiddleware")
    namespace["MIDDLEWARE"] = middleware
    if journal:
        namespace["NOVA_INTEGRATED_JOURNAL_ENABLED"] = True
        namespace["NOVA_REFERENCE_ENABLED"] = True
        # Must run BEFORE pending-state gates so recovery can unblock them.
        namespace["MIDDLEWARE"] = ["local_pos.journal_runtime.JournalRecoveryMiddleware", *middleware]
