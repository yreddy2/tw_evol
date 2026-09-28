import hashlib
import json
import os
import selectors
import subprocess
import tempfile
from pathlib import Path

from .errors import CapabilityError, ExecutionError

LOCK = json.loads(Path(__file__).with_name("runtime-lock.json").read_text())


def local_verification(root: Path) -> dict:
    """Actual smoke evidence is separate from import/asset readiness."""
    path = root / "verification.json"
    if not path.is_file():
        return {"status": "not_run", "tested_lengths": []}
    try:
        evidence = json.loads(path.read_text())
        if evidence["source_revision"] != LOCK["source"]["revision"] or evidence[
            "worker_sha256"
        ] != sha256_file(Path(__file__).with_name("worker.py")):
            return {"status": "stale", "tested_lengths": []}
        return evidence
    except (ValueError, KeyError, OSError):
        return {"status": "invalid_evidence", "tested_lengths": []}


def record_verification(root: Path, manifest: dict) -> None:
    """Record full-stack smoke success without changing scientific cache identity."""
    import fcntl

    from .storage import now, write_json

    context = manifest["immutable"]
    if (
        context["runtime"].get("source_revision") != LOCK["source"]["revision"]
        or manifest["status"] != "completed"
        or manifest["counts"]["scored"] < 10
    ):
        return
    with (root / ".verification.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        previous = local_verification(root)
        lengths = previous["tested_lengths"] if previous["status"] == "locally_verified" else []
        value = {
            "status": "locally_verified",
            "source_revision": LOCK["source"]["revision"],
            "worker_sha256": context["runtime"]["worker_sha256"],
            "tested_lengths": sorted(set(lengths + [len(context["sequence"])])),
            "device": context["config"]["runtime"]["device"],
            "verified_at": now(),
            "latest_run_immutable_sha256": manifest["immutable_sha256"],
            "note": "At least ten real generated candidates scored per observed length.",
        }
        write_json(root / "verification.json", value)


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_identity(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml"} and ".git" not in path.parts:
            digest.update(str(path.relative_to(source)).encode())
            digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def readiness(root: Path) -> dict:
    errors = []
    assets = {}
    python = root / "venv/bin/python"
    if not python.is_file():
        errors.append(f"Missing isolated Python: {python}. Run scripts/setup_runtime.py")
    source = root / "sources/grnade"
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(source), "diff", "HEAD", "--"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        if revision != LOCK["source"]["revision"] or dirty:
            errors.append("gRNAde source revision is incorrect or tracked source is modified")
    except (OSError, subprocess.CalledProcessError):
        revision = None
        errors.append(f"Missing official gRNAde source: {source}")
    for name, entry in LOCK["checkpoints"].items():
        path = root / "checkpoints" / entry["file"]
        digest = sha256_file(path) if path.is_file() else None
        assets[name] = {**entry, "actual_sha256": digest, "path": str(path)}
        if digest != entry["sha256"]:
            errors.append(f"Missing or corrupt {name} checkpoint: {path}")
    return {
        "ready": not errors,
        "errors": errors,
        "source_revision": revision,
        "source_sha256": source_identity(source) if source.exists() else None,
        "worker_sha256": sha256_file(Path(__file__).with_name("worker.py")),
        "assets": assets,
        "python": str(python),
        "registry": LOCK,
    }


class Worker:
    python_directory = "venv"
    worker_filename = "worker.py"

    def __init__(self, config, log_path: Path | None = None):
        self.config = config
        self.temp = tempfile.TemporaryDirectory(prefix="rna-design-worker-")
        self.log = log_path.open("ab") if log_path else tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [
                str(config.root / self.python_directory / "bin/python"),
                "-u",
                str(Path(__file__).with_name(self.worker_filename)),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            cwd=self.temp.name,
            env={**os.environ, "WANDB_MODE": "disabled", "PYTHONUNBUFFERED": "1"},
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def request(self, action: str, timeout: float | None = None, **payload) -> dict:
        request = {
            "action": action,
            "root": str(self.config.root.resolve()),
            "device": self.config.device,
            "threads": self.config.threads,
            **payload,
        }
        try:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            self.process.stdin.flush()
            wait = min(
                timeout or self.config.request_timeout_seconds, self.config.request_timeout_seconds
            )
            if wait <= 0 or not self.selector.select(wait):
                self.close()
                raise ExecutionError(
                    f"{action} timed out after {max(0, wait):.1f}s; committed work is safe"
                )
            line = self.process.stdout.readline()
            if not line:
                raise ExecutionError(
                    f"Model worker exited during {action}; see worker.log (possible OOM)"
                )
            value = json.loads(line)
            if not value.get("ok"):
                raise ExecutionError(f"{action}: {value.get('error', 'invalid worker response')}")
            return value
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionError(f"Model worker protocol failed: {error}") from error

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.log):
            stream.close()
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def doctor(config, imports=True) -> dict:
    status = readiness(config.root)
    if status["ready"] and imports:
        try:
            with Worker(config) as worker:
                status["runtime"] = worker.request("check")["result"]
        except (ExecutionError, OSError) as error:
            status["ready"] = False
            status["errors"].append(str(error))
    return status


def require_runtime(config) -> dict:
    status = doctor(config)
    if not status["ready"]:
        raise CapabilityError("; ".join(status["errors"]))
    return status
