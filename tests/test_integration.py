import os
from pathlib import Path

import pytest

from rna_design.config import RuntimeConfig
from rna_design.folding import fold
from rna_design.inputs import parse_structure
from rna_design.metrics import predicted_openknot
from rna_design.runtime import Worker, require_runtime


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RNA_DESIGN_INTEGRATION") != "1", reason="explicit real inference only"
)
def test_real_models_and_repeatable_sampling():
    root = Path(__file__).parents[1] / ".runtime"
    config = RuntimeConfig(root=root)
    require_runtime(config)
    sequence = "GGGAAACCCGGGAAACCCGGGAAACCCGGGAAACCC"
    target = fold(sequence, 37)["dot_bracket"]
    payload = dict(sequence=sequence, target=target, count=2, temperature=0.8, seed=42)
    with Worker(config) as worker:
        first = worker.request("generate", **payload)["result"]
        second = worker.request("generate", **payload)["result"]
        assert first == second
        assert first["target_consumed"] == target
        for proposal in first["proposals"]:
            candidate = proposal["sequence"]
            assert len(candidate) == len(sequence)
            values = worker.request("reactivity", sequence=candidate)["result"]["channels"]
            score = predicted_openknot(target, [row[0] for row in values])
            assert score["status"] == "ok"
            ss = worker.request("structure", sequence=candidate)["result"]["dot_bracket"]
            parse_structure(ss, len(candidate))


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RNA_DESIGN_INTEGRATION") != "1", reason="explicit real inference only"
)
def test_real_struct2seq_cpu_repeatability_pairs_and_shared_evaluators():
    from rna_design.struct2seq_runtime import PipelineWorker, Struct2SeqWorker
    from rna_design.struct2seq_runtime import require_runtime as require_struct2seq

    config = RuntimeConfig(root=Path(__file__).parents[1] / ".runtime")
    identity = require_struct2seq(config)
    assert identity["runtime"]["packages"]["torch"].startswith("2.1.2")
    assert identity["generator"]["runtime"]["packages"]["torch"].startswith("2.9.1")
    # Crossing alphabetic and punctuation pairs exercise all coordinate conversions.
    target = "AA((..aa..))"
    _, pairs = parse_structure(target)
    payload = dict(
        sequence="A" * len(target),
        target=target,
        target_pairs=sorted(pairs),
        count=2,
        sampling_probability=1.0,
        seed=42,
    )
    with Struct2SeqWorker(config) as worker:
        first = worker.request("generate", **payload)["result"]
        second = worker.request("generate", **{**payload, "sequence": "C" * len(target)})["result"]
        assert first == second  # Same seed; no hidden reference bias.
    assert first["target_consumed"] == target
    assert first["target_pairs_consumed"] == [list(pair) for pair in sorted(pairs)]
    assert first["internal_search_effort"]["sampled_sequences"] == 2
    assert first["internal_search_effort"]["generation_scorer_calls"] == 0
    with PipelineWorker(config) as worker:
        for proposal in first["proposals"]:
            sequence = proposal["sequence"]
            assert len(sequence) == len(target) and set(sequence) <= set("ACGU")
            assert all(
                sequence[i - 1] + sequence[j - 1] in {"AU", "UA", "CG", "GC", "GU"}
                for i, j in pairs
            )
            values = worker.request("reactivity", sequence=sequence)["result"]["channels"]
            assert predicted_openknot(target, [row[0] for row in values])["status"] == "ok"
            ss = worker.request("structure", sequence=sequence)["result"]["dot_bracket"]
            parse_structure(ss, len(sequence))
