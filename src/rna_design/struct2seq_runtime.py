"""Second-generator runtime; common RNet evaluators retain their original identity."""

import json
import subprocess
from pathlib import Path

from .errors import CapabilityError, ExecutionError
from .runtime import Worker, doctor, sha256_file, source_identity

LOCK = json.loads(Path(__file__).with_name("struct2seq-lock.json").read_text())


class Struct2SeqWorker(Worker):
    python_directory = "struct2seq-venv"
    worker_filename = "struct2seq_worker.py"


class PipelineWorker:
    """One live worker at a time bounds memory and isolates incompatible dependencies."""

    def __init__(self, config, log_path=None):
        self.config, self.log_path = config, log_path
        self.active = None
        self.start_failed = False

    @property
    def process(self):
        return self

    def poll(self):
        return self.active.process.poll() if self.active else (1 if self.start_failed else None)

    def request(self, action, **payload):
        model = payload.pop("generator", "struct2seq")
        factory = Struct2SeqWorker if action == "generate" and model == "struct2seq" else Worker
        if action == "generate" and model == "na_mpnn":
            from .na_mpnn_runtime import NaMPNNWorker

            factory = NaMPNNWorker
        if self.active is None or type(self.active) is not factory:
            self.close()
            try:
                self.active = factory(self.config, self.log_path)
                self.start_failed = False
            except Exception:
                self.start_failed = True
                raise
        return self.active.request(action, **payload)

    def close(self):
        if self.active is not None:
            self.active.close()
            self.active = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def readiness(root):
    errors = []
    python = root / "struct2seq-venv/bin/python"
    if not python.is_file():
        errors.append(f"Missing {python}; run scripts/setup_struct2seq.py")
    source = root / "sources/struct2seq"
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(source), "diff", "HEAD", "--"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        if revision != LOCK["source"]["revision"] or dirty:
            errors.append("Struct2SeQ source revision is incorrect or tracked source is modified")
    except (OSError, subprocess.CalledProcessError):
        revision = None
        errors.append(f"Missing official Struct2SeQ source: {source}")
    entry = LOCK["checkpoint"]
    path = root / "checkpoints" / entry["file"]
    digest = sha256_file(path) if path.is_file() else None
    if digest != entry["sha256"]:
        errors.append(f"Missing or corrupt Struct2SeQ checkpoint: {path}")
    return {
        "ready": not errors,
        "errors": errors,
        "source_revision": revision,
        "source_sha256": source_identity(source) if source.exists() else None,
        "worker_sha256": sha256_file(Path(__file__).with_name("struct2seq_worker.py")),
        "assets": {"struct2seq": {**entry, "actual_sha256": digest, "path": str(path)}},
        "python": str(python),
        "registry": LOCK,
    }


def check_runtime(config, imports=True):
    # The base installation supplies the shared, pinned RNet/RNet-SS evaluator stack.
    common = doctor(config, imports=imports)
    generator = readiness(config.root)
    if generator["ready"] and imports:
        try:
            with Struct2SeqWorker(config) as worker:
                generator["runtime"] = worker.request("check")["result"]
        except (ExecutionError, OSError) as error:
            generator["ready"] = False
            generator["errors"].append(str(error))
    return {
        **common,
        "ready": common["ready"] and generator["ready"],
        "errors": common["errors"] + generator["errors"],
        "generator": generator,
    }


def require_runtime(config):
    status = check_runtime(config)
    if not status["ready"]:
        raise CapabilityError("; ".join(status["errors"]))
    return status


def local_verification(root):
    path = root / "struct2seq-verification.json"
    if not path.is_file():
        return {"status": "not_run", "tested_lengths": []}
    try:
        value = json.loads(path.read_text())
        if (
            value["source_revision"] != LOCK["source"]["revision"]
            or value["worker_sha256"]
            != sha256_file(Path(__file__).with_name("struct2seq_worker.py"))
            or value["checkpoint_sha256"] != LOCK["checkpoint"]["sha256"]
        ):
            return {"status": "stale", "tested_lengths": []}
        return value
    except (KeyError, ValueError, OSError):
        return {"status": "invalid_evidence", "tested_lengths": []}


def record_verification(root, manifest):
    import fcntl

    from .storage import now, write_json

    context = manifest["immutable"]
    if manifest["status"] != "completed" or manifest["counts"]["scored"] < 10:
        return
    with (root / ".struct2seq-verification.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        previous = local_verification(root)
        lengths = previous["tested_lengths"] if previous["status"] == "locally_verified" else []
        write_json(
            root / "struct2seq-verification.json",
            {
                "status": "locally_verified",
                "source_revision": LOCK["source"]["revision"],
                "worker_sha256": context["runtime"]["generator"]["worker_sha256"],
                "checkpoint_sha256": LOCK["checkpoint"]["sha256"],
                "tested_lengths": sorted(set(lengths + [len(context["sequence"])])),
                "device": context["config"]["runtime"]["device"],
                "verified_at": now(),
                "latest_run_immutable_sha256": manifest["immutable_sha256"],
                "note": "At least ten real generated candidates scored per observed length.",
            },
        )
