#!/usr/bin/env python3
"""Explicit real-model feasibility checks; never run during ordinary installation."""

import argparse
import json
from pathlib import Path

from rna_design.config import BudgetConfig, Experiment, InputConfig, RuntimeConfig, Struct2SeqConfig
from rna_design.pipeline import create_run, execute
from rna_design.storage import write_json

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["grnade", "struct2seq"], default="grnade")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.out = args.out or PROJECT / "runs" / f"{args.model}-feasibility"
    fixtures = {
        "lower-bound-8": "ACGUACGU",
        "trna-76": "".join((PROJECT / "examples/trna.fasta").read_text().splitlines()[1:]),
        "twort-282": "".join((PROJECT / "twort_seq.txt").read_text().split()),
        "upper-bound-500": ("AGUCGACUGAAACGUAGCUAGGAUCCGAUUGCA" * 16)[:500],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    summaries = []
    for name, sequence in fixtures.items():
        path = args.out / f"{name}.fasta"
        path.write_text(f">{name}\n{sequence}\n")
        config = Experiment(
            input=InputConfig(sequence=path.resolve()),
            budget=BudgetConfig(proposals=10, attempt_cap=20),
            runtime=RuntimeConfig(root=PROJECT / ".runtime"),
        )
        if args.model == "struct2seq":
            config.model = Struct2SeqConfig()
        directory = args.out / name
        if not directory.exists():
            create_run(config, directory)
        result = execute(directory)
        summary = {
            "name": name,
            "length": len(sequence),
            "status": result["status"],
            "elapsed_seconds": result["elapsed_seconds"],
            "peak_rss_bytes": result["peak_rss_bytes"],
            "counts": result["counts"],
            "error": result["error"],
            "run": str(directory),
        }
        print(json.dumps(summary), flush=True)
        summaries.append(summary)
        write_json(PROJECT / f"evidence/{args.model}-length-feasibility.json", summaries)
        if not result["status"].startswith("completed"):
            raise SystemExit(4)


if __name__ == "__main__":
    main()
