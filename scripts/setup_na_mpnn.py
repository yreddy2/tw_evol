#!/usr/bin/env python3
"""Install the pinned optional NA-MPNN runtime without changing the other models."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / "src/rna_design/na-mpnn-lock.json").read_text())


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
    source = root / "sources/na_mpnn"
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
    python = root / "na-mpnn-venv/bin/python"
    if not python.exists():
        run(uv, "venv", "--python", "3.12", python.parent.parent)
    if platform.system() != "Darwin":
        index = "https://download.pytorch.org/whl/" + ("cu124" if args.device == "cuda" else "cpu")
        run(uv, "pip", "install", "--python", python, "torch==2.5.1", "--index-url", index)
    run(uv, "pip", "install", "--python", python, "-r", PROJECT / "requirements-na-mpnn.txt")
    entry = LOCK["checkpoint"]
    original = source / entry["source_file"]
    if original.stat().st_size != entry["bytes"] or digest(original) != entry["sha256"]:
        raise SystemExit(f"Checkpoint integrity failure: {original}")
    destination = root / "checkpoints" / entry["file"]
    if destination.exists() and digest(destination) != entry["sha256"]:
        raise SystemExit(f"Corrupt checkpoint: {destination}; remove it explicitly to retry")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".download")
        shutil.copyfile(original, temporary)
        os.replace(temporary, destination)
    frozen = subprocess.check_output([uv, "pip", "freeze", "--python", str(python)], text=True)
    (root / "na-mpnn-installed-requirements.txt").write_text(frozen)
    print(f"Verify: rna-design doctor --model all --runtime-root {root}")
    print("Also install the CLI [structure] extra and the shared evaluator runtime.")


if __name__ == "__main__":
    main()
