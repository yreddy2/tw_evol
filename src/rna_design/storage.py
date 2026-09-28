import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import DesignError, ExecutionError


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ExecutionError(f"Missing or corrupt run artifact {path}: {error}") from error


def atomic_write(path: Path, data: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode() if isinstance(data, str) else data
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value):
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def immutable_json(path: Path, value):
    if path.exists():
        if read_json(path) != value:
            raise ExecutionError(f"Refusing to overwrite immutable artifact {path}")
    else:
        write_json(path, value)


@contextmanager
def run_lock(directory: Path):
    if not directory.is_dir():
        raise DesignError(f"Run directory does not exist: {directory}")
    with (directory / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ExecutionError("Another process is using this run") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def event(directory: Path, kind: str, **details):
    # Run lock serializes writers; the newline-delimited log is advisory, committed JSON is authoritative.
    with (directory / "events.jsonl").open("a") as stream:
        stream.write(json.dumps({"time": now(), "type": kind, **details}, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_manifest(directory: Path) -> dict:
    manifest = read_json(directory / "manifest.json")
    if manifest.get("schema_version") != 1:
        raise DesignError("Unsupported run schema version")
    if identity(manifest["immutable"]) != manifest["immutable_sha256"]:
        raise ExecutionError("Immutable run configuration/input/target was modified")
    for filename, key in [("target.json", "target"), ("resolved-config.json", "config")]:
        if read_json(directory / filename) != manifest["immutable"][key]:
            raise ExecutionError(f"Immutable {filename} differs from the sealed manifest")
    for filename, key in [
        ("backbone.json", "backbone"),
        ("reconstruction.json", "reconstruction"),
        ("generation-target.json", "generation_target"),
    ]:
        if (
            key in manifest["immutable"]
            and read_json(directory / filename) != manifest["immutable"][key]
        ):
            raise ExecutionError(f"Immutable {filename} differs from the sealed manifest")
    for name, expected in manifest["immutable"]["snapshots"].items():
        path = directory / "inputs" / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ExecutionError(f"Input snapshot is missing or modified: {name}")
    return manifest


def committed(directory: Path, subdir: str) -> list[dict]:
    return [read_json(path) for path in sorted((directory / subdir).glob("*.json"))]
