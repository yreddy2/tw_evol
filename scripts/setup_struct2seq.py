#!/usr/bin/env python3
"""Install the optional pinned Struct2SeQ generator alongside the base evaluator runtime."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / "src/rna_design/struct2seq-lock.json").read_text())


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT / ".runtime")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.device == "cuda" and platform.system() != "Linux":
        parser.error("CUDA recipe requires Linux")
    uv = shutil.which("uv") or str(Path(sys.executable).parent / "uv")
    if not Path(uv).is_file():
        raise SystemExit("Install uv in your CLI environment first: python -m pip install uv")
    source = root / "sources/struct2seq"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        run("git", "clone", "--no-checkout", "--filter=blob:none", LOCK["source"]["url"], source)
        run("git", "-C", source, "checkout", "--detach", LOCK["source"]["revision"])
    else:
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(source), "diff", "HEAD", "--"], text=True
        ).strip()
        if revision != LOCK["source"]["revision"] or dirty:
            raise SystemExit(f"Existing source at {source} differs; use a new --root")
    python = root / "struct2seq-venv/bin/python"
    if not python.exists():
        run(uv, "venv", "--python", "3.11", python.parent.parent)
    if platform.system() != "Darwin":
        index = "https://download.pytorch.org/whl/" + ("cu128" if args.device == "cuda" else "cpu")
        run(uv, "pip", "install", "--python", python, "torch==2.9.1", "--index-url", index)
    run(uv, "pip", "install", "--python", python, "-r", PROJECT / "requirements-struct2seq.txt")
    entry = LOCK["checkpoint"]
    checkpoint = root / "checkpoints" / entry["file"]
    if checkpoint.exists() and digest(checkpoint) != entry["sha256"]:
        raise SystemExit(f"Corrupt checkpoint: {checkpoint}; remove it explicitly to retry")
    if not checkpoint.exists():
        archive = root / "struct2seq-weights-v1.zip"
        if not archive.exists():
            temporary = archive.with_suffix(".download")
            print("Downloading the author's public dataset v1 (222 MB)", flush=True)
            request = urllib.request.Request(
                LOCK["download_url"], headers={"User-Agent": "rna-design"}
            )
            with (
                urllib.request.urlopen(request, timeout=180) as response,
                temporary.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            if digest(temporary) != LOCK["archive_sha256"]:
                raise SystemExit(f"Archive integrity failure: {temporary}")
            os.replace(temporary, archive)
        if digest(archive) != LOCK["archive_sha256"]:
            raise SystemExit(f"Corrupt archive: {archive}; remove it explicitly to retry")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(".download")
        with zipfile.ZipFile(archive) as bundle:
            # Extract only this known member; never trust archive paths as output paths.
            with bundle.open(entry["archive_member"]) as src, temporary.open("wb") as out:
                shutil.copyfileobj(src, out)
        if temporary.stat().st_size != entry["bytes"] or digest(temporary) != entry["sha256"]:
            raise SystemExit(f"Checkpoint integrity failure: {temporary}")
        os.replace(temporary, checkpoint)
    frozen = subprocess.check_output([uv, "pip", "freeze", "--python", str(python)], text=True)
    (root / "struct2seq-installed-requirements.txt").write_text(frozen)
    print(f"Verify both installations: rna-design doctor --model struct2seq --runtime-root {root}")
    print("The shared evaluators require the base scripts/setup_runtime.py installation.")


if __name__ == "__main__":
    main()
