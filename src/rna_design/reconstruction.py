"""Explicit sequence reconstruction; absent 3D coordinates are never fabricated."""

import json

from .errors import DesignError
from .storage import identity


def validate_mapping(path, reference, design_sequence):
    try:
        mapping = json.loads(path.read_text())
    except (ValueError, OSError) as error:
        raise DesignError(f"Cannot read reconstruction mapping: {error}") from error
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
        raise DesignError("Reconstruction mapping must use schema_version 1")
    if (
        mapping.get("reference_sequence") != reference
        or mapping.get("design_sequence") != design_sequence
    ):
        raise DesignError(
            "Reconstruction mapping sequences must exactly match both supplied inputs"
        )
    positions = mapping.get("reference_positions", [])
    if (
        not isinstance(positions, list)
        or len(positions) != len(design_sequence)
        or any(type(i) is not int or not 1 <= i <= len(reference) for i in positions)
        or positions != sorted(set(positions))
    ):
        raise DesignError(
            "Mapping needs one strictly increasing, unique 1-based reference position per design base"
        )
    mismatches = [
        {
            "design_position": i,
            "reference_position": p,
            "design_base": base,
            "reference_base": reference[p - 1],
        }
        for i, (base, p) in enumerate(zip(design_sequence, positions), 1)
        if base != reference[p - 1]
    ]
    if mapping.get("acknowledged_mismatches", []) != mismatches:
        raise DesignError(
            "Mapping must explicitly acknowledge every reference/design base mismatch"
        )
    if not isinstance(mapping.get("strategy"), str) or not mapping["strategy"].strip():
        raise DesignError("Mapping must describe the explicit reconstruction strategy")
    fixed = [i for i in range(1, len(reference) + 1) if i not in set(positions)]
    value = {
        **mapping,
        "fixed_reference_positions": fixed,
        "designed_length": len(design_sequence),
        "full_length": len(reference),
        "policy": "mapped_segment_with_fixed_context",
        "coordinate_reconstruction": False,
    }
    return {**value, "id": identity(value)}


def reconstruct(sequence, mapping):
    if len(sequence) != mapping["designed_length"]:
        raise DesignError("Generated RNA has the wrong design-segment length")
    result = list(mapping["reference_sequence"])
    for base, position in zip(sequence, mapping["reference_positions"]):
        result[position - 1] = base
    return "".join(result)
