#!/usr/bin/env python3
"""Audit real second-model artifacts and verify matched evaluator identities."""

import statistics
from pathlib import Path

from rna_design.metrics import levenshtein
from rna_design.reporting import collect
from rna_design.storage import committed, now, write_json
from rna_design.struct2seq_runtime import LOCK

PROJECT = Path(__file__).resolve().parents[1]
RUNS = [
    "struct2seq-hairpin",
    "struct2seq-feasibility/lower-bound-8",
    "struct2seq-feasibility/trna-76",
    "struct2seq-feasibility/twort-282",
    "struct2seq-feasibility/upper-bound-500",
    "struct2seq-recovery-real",
]


def main():
    summaries = []
    for name in RUNS:
        manifest, candidates, evaluations = collect(PROJECT / "runs" / name)
        context = manifest["immutable"]
        assert manifest["status"] == "completed"
        assert context["config"]["model"]["name"] == "struct2seq"
        assert manifest["counts"]["scored"] == manifest["counts"]["unique_generated"]
        batches = committed(PROJECT / "runs" / name, "batches")
        for batch in batches:
            assert batch["internal_search_effort"]["sampled_sequences"] == len(batch["events"])
            assert batch["internal_search_effort"]["generation_scorer_calls"] == 0
            for proposal in batch["events"]:
                assert proposal["checkpoint_sha256"] == LOCK["checkpoint"]["sha256"]
                assert proposal["source_revision"] == LOCK["source"]["revision"]
                assert proposal["target_id"] == context["target"]["id"]
                assert proposal["valid"] and len(proposal["sequence"]) == len(context["sequence"])
        for candidate in candidates:
            result = evaluations[candidate["candidate_id"]]
            metrics = result["metrics"]
            assert metrics["predicted_openknot_score"]["status"] == "ok"
            assert metrics["pair_f1"]["status"] == "ok"
            assert metrics["sequence_levenshtein_to_input"]["value"] == levenshtein(
                candidate["sequence"], context["sequence"]
            )
            assert metrics["mfe_structure_levenshtein_to_input"]["value"] == levenshtein(
                result["fold"]["output"]["dot_bracket"], context["reference_fold"]["dot_bracket"]
            )
        distributions = {}
        for metric in ["predicted_openknot_score", "pair_f1", "sequence_levenshtein_to_input"]:
            values = [
                evaluations[c["candidate_id"]]["metrics"][metric]["value"]
                for c in candidates
                if not c["is_reference"]
            ]
            distributions[metric] = {
                "min": min(values),
                "median": statistics.median(values),
                "max": max(values),
            }
        summaries.append(
            {
                "run": name,
                "length": len(context["sequence"]),
                "status": manifest["status"],
                "counts": manifest["counts"],
                "elapsed_seconds": manifest["elapsed_seconds"],
                "peak_rss_bytes": manifest["peak_rss_bytes"],
                "distributions": distributions,
                "immutable_sha256": manifest["immutable_sha256"],
                "candidate_distance_and_provenance_audit": "passed for every candidate and proposal",
            }
        )
    grnade = collect(PROJECT / "runs/twort-smoke")[0]["immutable"]
    struct = collect(PROJECT / "runs/struct2seq-feasibility/twort-282")[0]["immutable"]
    for key in ["construct_id", "target", "reference_fold"]:
        assert grnade[key] == struct[key], key
    assert grnade["config"]["evaluation"] == struct["config"]["evaluation"]
    for key in ["device", "threads"]:
        assert grnade["config"]["runtime"][key] == struct["config"]["runtime"][key]
    for key in ["proposals", "batch_size", "seed"]:
        assert grnade["config"]["budget"][key] == struct["config"]["budget"][key]
    for key in ["source_revision", "source_sha256", "worker_sha256", "runtime"]:
        assert grnade["runtime"][key] == struct["runtime"][key], key
    for key in ["rnet", "rnet_ss"]:
        assert grnade["runtime"]["assets"][key] == struct["runtime"]["assets"][key]
    write_json(
        PROJECT / "evidence/struct2seq-results.json",
        {
            "verified_at": now(),
            "runs": summaries,
            "matched_twort_evaluator_audit": "passed",
            "matched_fields": [
                "construct",
                "target",
                "reference_fold",
                "evaluation settings",
                "scorer code",
                "scorer checkpoints",
                "scorer package versions",
                "device",
                "threads",
                "returned quota",
                "batch size",
                "master seed",
            ],
            "limits": "Small feasibility probes, not a model leaderboard. Struct2SeQ has RNet training coupling and distinct native sampling controls. TW-019 remains deferred.",
        },
    )
    print("All Struct2SeQ candidate/provenance audits and matched Twort evaluator checks passed.")


if __name__ == "__main__":
    main()
