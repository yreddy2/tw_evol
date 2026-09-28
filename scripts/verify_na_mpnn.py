#!/usr/bin/env python3
"""Run with .runtime/na-mpnn-venv/bin/python; verify native features and CPU sampling."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src/rna_design"))
from na_mpnn_worker import Models, backbone_features  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"Refusing to overwrite {args.out}")
    backbone = json.loads((PROJECT / "evidence/twort-1y0q-comparison/backbone.json").read_text())
    model = Models(PROJECT / ".runtime", "cpu", 2)
    import torch
    from data_utils import featurize, parse_PDB

    native, *_ = parse_PDB(
        str(PROJECT / "examples/backbones/1Y0Q.pdb"),
        device="cpu",
        chains=["A"],
        parse_na_only=True,
        model_type="na_mpnn",
        na_shared_tokens=True,
        load_residues_with_missing_atoms=1,
    )
    native["chain_mask"] = torch.ones_like(native["mask"])
    native = featurize(native)
    ours = backbone_features(backbone, "cpu")
    equality = {key: torch.equal(native[key], ours[key]) for key in native}
    assert all(equality.values()), equality
    assert torch.all(ours["mask"] == 1) and torch.all(ours["chain_mask"] == 1)
    request = dict(
        backbone=backbone, sequence=backbone["sequence"], count=2, seed=42, temperature=0.1
    )
    start = time.monotonic()
    first = model.generate(request)
    repeat = model.generate(request)
    assert first == repeat
    assert first["target_consumed"] is None and first["backbone_consumed_id"] == backbone["id"]
    assert len(first["proposals"]) == 2
    assert all(
        len(p["sequence"]) == 229 and not set(p["sequence"]) - set("ACGU")
        for p in first["proposals"]
    )
    changed = model.generate({**request, "seed": 43})
    assert [p["sequence"] for p in first["proposals"]] != [
        p["sequence"] for p in changed["proposals"]
    ]
    weight = PROJECT / ".runtime/checkpoints/na_mpnn/s_19137.pt"
    report = {
        "status": "passed",
        "device": "cpu",
        "feature_equality": equality,
        "canonical_rna": True,
        "same_seed_repeat_identical": True,
        "different_seed_changes_output": True,
        "design_length": 229,
        "missing_atom_policy": backbone["missing_atom_policy"],
        "full_redesign_mask": True,
        "checkpoint_sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
        "worker_sha256": hashlib.sha256(
            (PROJECT / "src/rna_design/na_mpnn_worker.py").read_bytes()
        ).hexdigest(),
        "runtime": model.check(),
        "seconds": time.monotonic() - start,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
