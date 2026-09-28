#!/usr/bin/env python3
"""Run under .runtime/venv/bin/python; compare local metrics with pinned primary source."""

import importlib.util
import json
import random
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from rna_design.metrics import SCORE_SOURCE, predicted_openknot  # noqa: E402

path = PROJECT / ".runtime/sources/openknotscore/src/openknotscore/pipeline/scoring.py"
spec = importlib.util.spec_from_file_location("upstream_scoring", path)
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
rng = random.Random(217)
cases = []
structures = ["........", "((....))", "[[....]]", "([..)]", "(([[..))..]]", "([....])"]
for structure in structures:
    for index in range(12):
        values = (
            ([0.125, 0.25, 0.5, 0, 1, -0.01] * 10)[: len(structure)]
            if index == 0
            else [rng.uniform(-0.1, 1.2) for _ in structure]
        )
        ecs = upstream.calculateEternaClassicScore(
            structure, values, 0, len(structure) - 1, filter_singlets=False
        )
        cpq = upstream.calculateCrossedPairQualityScore(
            structure, values, 0, len(structure) - 1, filter_singlets=False
        )[1]
        actual = predicted_openknot(structure, values)["value"]
        expected = (ecs + cpq) / 2
        assert abs(actual - expected) < 1e-12, (structure, actual, expected)
        cases.append(
            {"structure": structure, "reactivities": values, "expected": expected, "actual": actual}
        )
report = {
    "upstream_revision": SCORE_SOURCE,
    "tolerance": 1e-12,
    "cases": cases,
    "passed": len(cases),
}
out = PROJECT / "evidence/scoring-equivalence.json"
out.write_text(json.dumps(report, indent=2) + "\n")
print(f"{len(cases)} upstream-equivalence fixtures passed; {out}")
