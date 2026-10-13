"""Original application + a private fallback. Selected only by runserver."""
from .settings import *  # noqa: F401,F403

from local_pos.integrated_config import load_config, local_database
from local_pos.integrated_configuration import configure
import os

LOCAL_CONFIG = load_config(os.environ["NOVA_INTEGRATED_CONFIG"], BASE_DIR)
configure(globals(), LOCAL_CONFIG, local_database(LOCAL_CONFIG),
          journal=LOCAL_CONFIG.get("activation") == "journal_preview")
from local_pos.integrated_assets import configure_assets
configure_assets(globals())
