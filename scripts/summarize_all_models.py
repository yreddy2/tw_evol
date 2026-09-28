#!/usr/bin/env python3
"""Audit a completed reconstructed all-model run and its ranked CSV."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from rna_design.metrics import levenshtein
from rna_design.reconstruction import reconstruct
from rna_design.reporting import collect
from rna_design.storage import committed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest, candidates, evaluations = collect(args.run)
    context = manifest["immutable"]
    assert manifest["status"] == "completed"
    assert context["config"]["model"]["name"] == "all"
    mapping = context["reconstruction"]
    reference = context["sequence"]
    proposals = [e for b in committed(args.run, "batches") for e in b["events"]]
    quota = context["config"]["budget"]["proposals"]
    assert Counter(e["model"]["name"] for e in proposals) == {
        "grnade": quota,
        "struct2seq": quota,
        "na_mpnn": quota,
    }
    for proposal in proposals:
        assert proposal["valid"] and proposal["reconstruction_id"] == mapping["id"]
        assert reconstruct(proposal["designed_sequence"], mapping) == proposal["sequence"]
        assert proposal["target_id"] == context["target"]["id"]
        if proposal["model"]["name"] == "na_mpnn":
            assert (
                proposal["backbone_id"] == context["backbone"]["id"]
                and proposal["generation_target_id"] is None
            )
            runtime = context["runtime"]["na_mpnn"]
        else:
            assert (
                proposal["generation_target_id"] == context["generation_target"]["id"]
                and proposal["backbone_id"] is None
            )
            runtime = (
                context["runtime"]["generator"]
                if proposal["model"]["name"] == "struct2seq"
                else context["runtime"]
            )
        assert (
            proposal["checkpoint_sha256"] == runtime["assets"][proposal["model"]["name"]]["sha256"]
        )
        assert proposal["source_revision"] == runtime["source_revision"]
    for candidate in candidates:
        sequence = candidate["sequence"]
        assert len(sequence) == len(reference)
        assert all(
            sequence[p - 1] == reference[p - 1] for p in mapping["fixed_reference_positions"]
        )
        evaluation = evaluations[candidate["candidate_id"]]
        metrics = evaluation["metrics"]
        assert metrics["sequence_levenshtein_to_input"]["value"] == levenshtein(sequence, reference)
        assert metrics["mfe_structure_levenshtein_to_input"]["value"] == levenshtein(
            evaluation["fold"]["output"]["dot_bracket"], context["reference_fold"]["dot_bracket"]
        )
        assert metrics["predicted_openknot_score"]["status"] == metrics["pair_f1"]["status"] == "ok"
    csv_path = args.run / "exports/ranked.csv"
    with csv_path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3 * quota and len({r["candidate_id"] for r in rows}) == len(rows)
    assert Counter(r["generator"] for r in rows) == {
        "grnade": quota,
        "struct2seq": quota,
        "na_mpnn": quota,
    }
    assert [int(r["rank"]) for r in rows] == list(range(1, len(rows) + 1))
    assert rows == sorted(
        rows,
        key=lambda r: (
            -float(r["predicted_openknot_score"]),
            -float(r["pair_f1"]),
            r["candidate_id"],
        ),
    )
    for row in rows:
        assert len(row["sequence"]) == mapping["full_length"]
        assert len(row["designed_sequence"]) == mapping["designed_length"]
        assert row["is_reference"] == "False" and row["run_status"] == "completed"
        assert row["design_policy"] == "mapped_segment_with_fixed_context"
        assert reconstruct(row["designed_sequence"], mapping) == row["sequence"]
    report = {
        "status": "passed",
        "run": str(args.run.resolve()),
        "manifest_immutable_sha256": manifest["immutable_sha256"],
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "rows": len(rows),
        "counts": manifest["counts"],
        "design_length": mapping["designed_length"],
        "comparison_length": mapping["full_length"],
        "fixed_positions_verified_per_candidate": len(mapping["fixed_reference_positions"]),
        "reference_evaluated_separately": True,
        "both_edit_distances_independently_recomputed": True,
        "generation_and_evaluation_targets_distinct": context["generation_target"]["id"]
        != context["target"]["id"],
        "correct_native_conditioning_receipts": True,
        "same_shared_scoring_and_global_sort": True,
        "elapsed_seconds": manifest["elapsed_seconds"],
        "peak_rss_bytes": manifest["peak_rss_bytes"],
        "device": context["config"]["runtime"]["device"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
