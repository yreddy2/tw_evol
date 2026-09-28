"""Dependency-free scientific measurements, with explicit missing-value semantics."""

import math

from .inputs import crossing_pairs, parse_structure

SCORE_VERSION = "oksp-target-ecs-cpq-v1"
SCORE_SOURCE = "23e24fe21a3ebe50046b0cce29bcb1c4a1a571b8"


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        previous, row = row, [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
    return row[-1]


def measurement(value, version: str, *, error: str | None = None, **provenance) -> dict:
    return {
        "value": value,
        "status": "failed" if error else "not_applicable" if value is None else "ok",
        "version": version,
        "error": error,
        "provenance": provenance,
    }


def sequence_metrics(sequence: str, reference: str) -> dict:
    if len(sequence) != len(reference):
        raise ValueError("Hamming distance requires equal-length RNA")
    hamming = sum(a != b for a, b in zip(sequence, reference))
    return {
        "sequence_levenshtein_to_input": measurement(
            levenshtein(sequence, reference),
            "levenshtein-unit-v1",
            candidate_string=sequence,
            comparator_string=reference,
            comparator="original_input",
        ),
        "hamming_to_input": measurement(hamming, "hamming-v1"),
        "identity_to_input": measurement(1 - hamming / len(reference), "identity-fraction-v1"),
        **{
            name: measurement(
                100 * sum(sequence.count(b) for b in bases) / len(sequence),
                "composition-percent-v1",
                scope="full_construct",
                denominator=len(sequence),
            )
            for name, bases in (("c_percent", "C"), ("u_percent", "U"), ("gc_percent", "GC"))
        },
    }


def pair_metrics(predicted: set[tuple[int, int]], target: set[tuple[int, int]]) -> dict:
    tp = len(predicted & target)
    crossed = crossing_pairs(target)
    values = {
        "pair_precision": tp / len(predicted) if predicted else None,
        "pair_recall": tp / len(target) if target else None,
        "pair_f1": 2 * tp / (len(predicted) + len(target)) if predicted or target else 1.0,
        "base_pair_distance": len(predicted ^ target),
        "crossed_pair_recall": len(predicted & crossed) / len(crossed) if crossed else None,
    }
    return {
        key: measurement(value, "pair-set-v1", coordinates="1-based inclusive")
        for key, value in values.items()
    }


def predicted_openknot(target: str, reactivities: list[float]) -> dict:
    """Equivalent to pinned OKSP with one supplied target and filter_singlets=False.

    Full construct mask, raw RNet channel 0 (2A3), no clipping/normalization.
    This is target-specific predicted OKS, not the experimental ensemble pipeline.
    """
    _, pairs = parse_structure(target)
    if len(reactivities) != len(target) or not all(math.isfinite(v) for v in reactivities):
        raise ValueError("RNet output must be finite and match the full target length")
    hits = sum(v > 0.125 if symbol == "." else v < 0.5 for symbol, v in zip(target, reactivities))
    ecs = 100 * hits / len(target)
    positions = {i for pair in crossing_pairs(pairs) for i in pair}
    cpq = (
        100 * sum(reactivities[i - 1] < 0.25 for i in positions) / len(positions)
        if positions
        else 0.0
    )
    return measurement(
        (ecs + cpq) / 2,
        SCORE_VERSION,
        source_revision=SCORE_SOURCE,
        eterna_classic_score=ecs,
        crossed_pair_quality_score=cpq,
        channel="2A3",
        channel_index=0,
        evaluated_positions=[1, len(target)],
        filter_singlets=False,
        normalization="none",
        clipping="none",
        target=target,
        attainable_maximum=100 if positions else 50,
    )
