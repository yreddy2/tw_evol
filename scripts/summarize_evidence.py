#!/usr/bin/env python3
"""Audit saved real runs and summarize measurements without rerunning inference."""

import json
import statistics
from pathlib import Path

from rna_design.metrics import levenshtein
from rna_design.reporting import collect
from rna_design.runtime import LOCK, sha256_file
from rna_design.storage import now, read_json, write_json

PROJECT = Path(__file__).resolve().parents[1]
RUNS = [
    "release-1000",
    "twort-smoke",
    "feasibility/lower-bound-8",
    "feasibility/trna-76",
    "feasibility/upper-bound-500",
    "pseudoknot-smoke",
    "filtered-smoke",
    "recovery-real",
]


def main():
    reports = []
    for name in RUNS:
        directory = PROJECT / "runs" / name
        manifest, candidates, evaluations = collect(directory)
        assert manifest["status"].startswith("completed"), (name, manifest["status"])
        context = manifest["immutable"]
        choice = read_json(directory / "selection.json")
        by_id = {c["candidate_id"]: c for c in candidates}
        sequences = [by_id[cid]["sequence"] for cid in choice["selected_ids"]]
        assert len(set(sequences)) == len(sequences)
        assert all(
            len(seq) == len(context["sequence"]) and seq != context["sequence"] for seq in sequences
        )
        for cid in choice["selected_ids"]:
            metrics = evaluations[cid]["metrics"]
            assert metrics["predicted_openknot_score"]["status"] == "ok"
            assert metrics["pair_f1"]["status"] == "ok"
        for candidate in candidates:
            metrics = evaluations[candidate["candidate_id"]]["metrics"]
            assert metrics["sequence_levenshtein_to_input"]["value"] == levenshtein(
                candidate["sequence"], context["sequence"]
            )
            structure = evaluations[candidate["candidate_id"]]["fold"]["output"]["dot_bracket"]
            assert metrics["mfe_structure_levenshtein_to_input"]["value"] == levenshtein(
                structure, context["reference_fold"]["dot_bracket"]
            )
        distributions = {}
        for key in [
            "predicted_openknot_score",
            "pair_f1",
            "sequence_levenshtein_to_input",
            "mfe_structure_levenshtein_to_input",
        ]:
            values = [
                evaluations[c["candidate_id"]]["metrics"][key]["value"]
                for c in candidates
                if not c["is_reference"]
            ]
            values = [v for v in values if v is not None]
            distributions[key] = (
                {
                    "count": len(values),
                    "min": min(values),
                    "median": statistics.median(values),
                    "max": max(values),
                }
                if values
                else {"count": 0}
            )
        reference_metrics = evaluations[context["reference_id"]]["metrics"]
        reports.append(
            {
                "run": name,
                "length": len(context["sequence"]),
                "status": manifest["status"],
                "target_source": context["target"]["source"],
                "pseudoknotted": context["target"]["pseudoknotted"],
                "counts": manifest["counts"],
                "elapsed_seconds": manifest["elapsed_seconds"],
                "peak_rss_bytes": manifest["peak_rss_bytes"],
                "distributions": distributions,
                "reference": {key: reference_metrics[key]["value"] for key in distributions},
                "distance_audit": "all candidate and reference records passed",
            }
        )
    write_json(PROJECT / "evidence/release-results.json", {"verified_at": now(), "runs": reports})
    verification = {
        "status": "locally_verified",
        "device": "cpu",
        "verified_at": now(),
        "source_revision": LOCK["source"]["revision"],
        "worker_sha256": sha256_file(PROJECT / "src/rna_design/worker.py"),
        "tested_lengths": sorted({r["length"] for r in reports}),
        "proposals_per_fixture": 10,
        "release_proposals": 1000,
        "evidence": str(PROJECT / "evidence/release-results.json"),
        "note": "Observed feasibility on this Mac, not a guarantee of biological utility.",
    }
    write_json(PROJECT / ".runtime/verification.json", verification)
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
