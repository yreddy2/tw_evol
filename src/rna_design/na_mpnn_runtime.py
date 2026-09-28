"""Optional official NA-MPNN runtime, isolated from the shared scoring stack."""

import json
import subprocess
from pathlib import Path

from .errors import CapabilityError, ExecutionError
from .runtime import Worker, doctor, sha256_file, source_identity

LOCK = json.loads(Path(__file__).with_name("na-mpnn-lock.json").read_text())


class NaMPNNWorker(Worker):
    python_directory = "na-mpnn-venv"
    worker_filename = "na_mpnn_worker.py"


def readiness(root):
    errors = []
    python = root / "na-mpnn-venv/bin/python"
    if not python.is_file():
        errors.append(f"Missing {python}; run scripts/setup_na_mpnn.py")
    source = root / "sources/na_mpnn"
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(source), "diff", "HEAD", "--"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        if revision != LOCK["source"]["revision"] or dirty:
            errors.append("NA-MPNN source revision is incorrect or tracked source is modified")
    except (OSError, subprocess.CalledProcessError):
        revision = None
        errors.append(f"Missing official NA-MPNN source: {source}")
    entry = LOCK["checkpoint"]
    path = root / "checkpoints" / entry["file"]
    digest = sha256_file(path) if path.is_file() else None
    if digest != entry["sha256"]:
        errors.append(f"Missing or corrupt NA-MPNN checkpoint: {path}")
    return {
        "ready": not errors,
        "errors": errors,
        "source_revision": revision,
        "source_sha256": source_identity(source) if source.exists() else None,
        "worker_sha256": sha256_file(Path(__file__).with_name("na_mpnn_worker.py")),
        "assets": {"na_mpnn": {**entry, "actual_sha256": digest, "path": str(path)}},
        "python": str(python),
        "registry": LOCK,
    }


def check_runtime(config, imports=True, include_struct2seq=False):
    if include_struct2seq:
        from .struct2seq_runtime import check_runtime as check_struct2seq

        common = check_struct2seq(config, imports=imports)
    else:
        common = doctor(config, imports=imports)
    generator = readiness(config.root)
    if generator["ready"] and imports:
        try:
            with NaMPNNWorker(config) as worker:
                generator["runtime"] = worker.request("check")["result"]
        except (ExecutionError, OSError) as error:
            generator["ready"] = False
            generator["errors"].append(str(error))
    return {
        **common,
        "ready": common["ready"] and generator["ready"],
        "errors": common["errors"] + generator["errors"],
        "na_mpnn": generator,
    }


def require_runtime(config, include_struct2seq=False):
    status = check_runtime(config, include_struct2seq=include_struct2seq)
    if not status["ready"]:
        raise CapabilityError("; ".join(status["errors"]))
    return status


def local_verification(root):
    path = root / "na_mpnn-verification.json"
    if not path.is_file():
        return {"status": "not_run", "tested_lengths": []}
    try:
        value = json.loads(path.read_text())
        if (
            value["source_revision"] != LOCK["source"]["revision"]
            or value["worker_sha256"] != sha256_file(Path(__file__).with_name("na_mpnn_worker.py"))
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
    counts = manifest["counts"].get("per_model", {}).get("na_mpnn", manifest["counts"])
    if manifest["status"] != "completed" or counts["scored"] < 10:
        return
    with (root / ".na_mpnn-verification.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        previous = local_verification(root)
        lengths = previous["tested_lengths"] if previous["status"] == "locally_verified" else []
        write_json(
            root / "na_mpnn-verification.json",
            {
                "status": "locally_verified",
                "source_revision": LOCK["source"]["revision"],
                "worker_sha256": context["runtime"]["na_mpnn"]["worker_sha256"],
                "checkpoint_sha256": LOCK["checkpoint"]["sha256"],
                "tested_lengths": sorted(
                    set(lengths + [len(context.get("design_sequence", context["sequence"]))])
                ),
                "latest_evaluation_length": len(context["sequence"]),
                "device": context["config"]["runtime"]["device"],
                "verified_at": now(),
                "latest_run_immutable_sha256": manifest["immutable_sha256"],
                "note": "At least ten real NA-MPNN candidates generated at each observed design length, then scored as the full comparison construct. This is not verification of other lengths or full-length 3D reconstruction.",
            },
        )
