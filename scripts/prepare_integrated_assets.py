"""Download/cache public UI libraries for normal integrated runserver."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_assets():
    from scripts.build_full_local_assets import build
    from local_pos.integrated_assets import asset_root, load_assets
    build(asset_root(ROOT))
    load_assets(ROOT)
    print("Bibliotecas de las pantallas listas para trabajar sin internet.", flush=True)


if __name__ == "__main__":
    prepare_assets()
