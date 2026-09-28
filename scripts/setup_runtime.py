#!/usr/bin/env python3
"""Explicit, pinned model setup. Never invoked implicitly by validation or inference."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / "src/rna_design/runtime-lock.json").read_text())


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=PROJECT / ".runtime")
    p.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = p.parse_args()
    root = args.root.resolve()
    if args.device == "cuda" and platform.system() != "Linux":
        p.error("CUDA recipe requires Linux")
    uv = shutil.which("uv") or str(Path(sys.executable).parent / "uv")
    if not Path(uv).is_file():
        run(sys.executable, "-m", "pip", "install", "uv==0.12.17")
        uv = str(Path(sys.executable).parent / "uv")
    root.mkdir(parents=True, exist_ok=True)
    for name, key in [("grnade", "source"), ("openknotscore", "scoring_source")]:
        source = root / "sources" / name
        spec = LOCK[key]
        if not source.exists():
            run("git", "clone", "--no-checkout", "--filter=blob:none", spec["url"], source)
            run("git", "-C", source, "checkout", "--detach", spec["revision"])
        else:
            revision = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
            ).strip()
            if revision != spec["revision"]:
                raise SystemExit(
                    f"Existing source at {source} has another revision; use a new --root"
                )
    if not (root / "venv/bin/python").exists():
        run(uv, "venv", "--python", "3.11", root / "venv")
    python = root / "venv/bin/python"
    torch_index = "https://download.pytorch.org/whl/" + (
        "cu121" if args.device == "cuda" else "cpu"
    )
    if platform.system() == "Darwin":
        run(uv, "pip", "install", "--python", python, "torch==2.1.2", "numpy==1.26.4")
    else:
        run(uv, "pip", "install", "--python", python, "torch==2.1.2", "--index-url", torch_index)
    run(uv, "pip", "install", "--python", python, "-r", PROJECT / "requirements-runtime.txt")
    suffix = "cu121" if args.device == "cuda" else "cpu"
    run(
        uv,
        "pip",
        "install",
        "--python",
        python,
        "--no-index",
        "--find-links",
        f"https://data.pyg.org/whl/torch-2.1.0+{suffix}.html",
        "torch-scatter==2.1.2",
        "torch-cluster==1.6.3",
        "torch-sparse==0.6.18",
    )
    for entry in LOCK["checkpoints"].values():
        path = root / "checkpoints" / entry["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
            continue
        if path.exists():
            raise SystemExit(
                f"Checkpoint hash mismatch: {path}; remove it explicitly before re-downloading"
            )
        url = LOCK["checkpoint_repository"] + "/resolve/main/" + urllib.parse.quote(entry["file"])
        temporary = path.with_suffix(path.suffix + ".download")
        print(f"Downloading {entry['file']} ({entry['bytes'] / 1e6:.1f} MB)", flush=True)
        urllib.request.urlretrieve(url, temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != entry["sha256"]:
            raise SystemExit(f"Download integrity failure: {temporary}")
        os.replace(temporary, path)
    frozen = subprocess.check_output([uv, "pip", "freeze", "--python", str(python)], text=True)
    (root / "installed-requirements.txt").write_text(frozen)
    print(f"Ready for verification: rna-design doctor --runtime-root {root}")


if __name__ == "__main__":
    main()
