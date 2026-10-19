"""Publish only a verified installer and checksum as a GitHub prerelease.

Uses the existing Git credential helper in memory; never prints/stores credentials.
Creates a draft first and publishes only after both uploads are verified.
Does not upload a repository archive, local databases or configuration.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess

import requests

ROOT = Path(__file__).resolve().parents[1]
REPO = "william18nova/Merk-888"


def git(*args, **kwargs):
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True,
                          check=True, **kwargs).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    if git("status", "--porcelain"):
        raise SystemExit("Commit the reviewed source before publishing.")
    commit = git("rev-parse", "HEAD")
    if git("ls-remote", "origin", "refs/heads/main").split()[0] != commit:
        raise SystemExit("The release must match published origin/main.")
    installer = args.stage / "NovaPOS-Instalar-Windows-x64.exe"
    checksum = args.stage / "SHA256.txt"
    with installer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum.read_text().split() != [digest, installer.name]:
        raise SystemExit("Installer checksum mismatch.")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    credential = git("credential", "fill", input="protocol=https\nhost=github.com\npath=" + REPO + ".git\n\n", env=env)
    fields = dict(line.split("=", 1) for line in credential.splitlines() if "=" in line)
    token = fields.get("password")
    if not token:
        raise SystemExit("GitHub authentication is required; no credentials were printed.")
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    base = "https://api.github.com/repos/" + REPO

    def call(method, url, **kwargs):
        response = session.request(method, url, timeout=(20, 300), allow_redirects=False, **kwargs)
        if response.status_code not in (200, 201):
            raise SystemExit(f"GitHub request failed ({response.status_code}); any draft is preserved. No secrets logged.")
        return response.json()

    release = call("POST", base + "/releases", json={"tag_name": args.tag,
        "target_commitish": commit, "name": "NovaPOS Windows 10/11 x64 — 2026.10.18 RC1",
        "body": args.notes.read_text(encoding="utf-8"), "draft": True, "prerelease": True})
    print("Draft created:", release["id"], flush=True)
    upload = release["upload_url"].split("{")[0]
    if not upload.startswith("https://uploads.github.com/repos/" + REPO + "/releases/"):
        raise SystemExit("Unexpected upload host; draft preserved.")
    for file in (installer, checksum):
        with file.open("rb") as stream:
            asset = call("POST", upload, params={"name": file.name}, data=stream,
                         headers={"Content-Type": "application/octet-stream"})
        if asset.get("state") != "uploaded" or asset.get("size") != file.stat().st_size:
            raise SystemExit("Upload verification failed; draft preserved.")
        if file == installer and asset.get("digest") not in (None, "sha256:" + digest):
            raise SystemExit("Server checksum mismatch; draft preserved.")
        print("Uploaded:", file.name, asset["size"], flush=True)
    published = call("PATCH", base + "/releases/" + str(release["id"]),
                     json={"draft": False, "prerelease": True, "make_latest": "false"})
    print("Published:", published["html_url"])


if __name__ == "__main__":
    main()
