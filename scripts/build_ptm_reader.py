"""Package only reviewed reader sources; never include profiles or configuration."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "browser_extensions" / "ptm_reader"
FILES = ("manifest.json", "background.js", "content.js", "parser.js", "options.html", "options.js", "options.css")


def main():
    version = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]
    target = ROOT / "build" / "releases" / f"NovaPOS-Lector-PTM-{version}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as bundle:
        for name in FILES:
            bundle.write(SOURCE / name, "ptm_reader/" + name)
        bundle.write(ROOT / "docs" / "lector_ptm.md", "LEEME.md")
    print(target)
    print("SHA256:", hashlib.sha256(target.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
