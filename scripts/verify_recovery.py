#!/usr/bin/env python3
"""Interrupt a real CLI after a committed RNet stage, then verify resume artifacts."""

import argparse
import hashlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def hashes(paths, run):
    return {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["grnade", "struct2seq", "both"], default="grnade")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    name = "recovery-real" if args.model == "grnade" else f"{args.model}-recovery-real"
    run = args.out or PROJECT / "runs" / name
    if run.exists():
        raise SystemExit(f"Use a fresh path; already exists: {run}")
    with (PROJECT / "evidence" / f"{name}.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rna_design",
                "run",
                "--model",
                args.model,
                "--sequence",
                str(PROJECT / "examples/trna.fasta"),
                "--proposals",
                "10",
                "--batch-size",
                "5",
                "--out",
                str(run),
                "--json",
            ],
            stdout=log,
            stderr=log,
        )
        started = time.monotonic()
        while not list(run.glob("evaluations/*/stages/*/rnet.json")):
            if process.poll() is not None:
                raise RuntimeError("CLI exited before the interruption point")
            if time.monotonic() - started > 120:
                process.terminate()
                process.wait(timeout=10)
                raise RuntimeError("Real inference did not reach the interruption point")
            time.sleep(0.05)
        process.send_signal(signal.SIGINT)
        code = process.wait(timeout=20)
        assert code == 130, code
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["status"] == "interrupted"
        protected = [
            run / "target.json",
            *run.glob("batches/*.json"),
            *run.glob("evaluations/*/stages/*/*.json"),
        ]
        before = hashes(protected, run)
        result = subprocess.run(
            [sys.executable, "-m", "rna_design", "resume", str(run), "--json"],
            stdout=log,
            stderr=log,
            timeout=180,
        )
        assert result.returncode == 0, result.returncode
        assert hashes(protected, run) == before
        final = json.loads((run / "manifest.json").read_text())
        assert final["status"] == "completed"
        n_models = 2 if args.model == "both" else 1
        assert final["counts"]["returned"] == 10 * n_models
        assert len(list(run.glob("batches/*.json"))) == 2 * n_models
        report = {
            "model": args.model,
            "interrupted_exit": code,
            "resumed_exit": result.returncode,
            "preserved_artifacts": before,
            "final_counts": final["counts"],
            "target_id": final["immutable"]["target"]["id"],
        }
        (PROJECT / "evidence" / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            "Real interruption/resume passed; committed generation and predictor stages unchanged."
        )


if __name__ == "__main__":
    main()
