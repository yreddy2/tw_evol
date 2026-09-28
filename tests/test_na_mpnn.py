"""Backbone coverage and explicit reconstruction contracts; production inference is opt-in."""

import csv
import json
from pathlib import Path

import pytest
from test_combined import CombinedWorker

from rna_design import na_mpnn_runtime, pipeline
from rna_design.backbone import inspect_backbone
from rna_design.config import (
    AllModelsConfig,
    BackboneConfig,
    Experiment,
    InputConfig,
    ReconstructionConfig,
)
from rna_design.errors import DesignError, ExecutionError
from rna_design.inputs import read_sequence
from rna_design.na_mpnn_worker import decode_tokens
from rna_design.reconstruction import reconstruct, validate_mapping
from rna_design.storage import committed, identity, load_manifest

ROOT = Path(__file__).resolve().parents[1]
PDB = ROOT / "examples/backbones/1Y0Q.pdb"
SHORT = read_sequence(ROOT / "examples/twort-1y0q-A.fasta")[1]


def test_crystal_coverage_and_explicit_atom_policy():
    strict = inspect_backbone(PDB, "A", SHORT)
    assert not strict["compatible"]
    masked = inspect_backbone(PDB, "A", SHORT, "mask_phosphate")
    assert masked["compatible"] and masked["length"] == 229
    assert [
        (r["residue_id"], r["missing_atoms"]) for r in masked["residues"] if r["missing_atoms"]
    ] == [("A:9", ["OP1", "OP2", "P"]), ("A:78", ["OP1", "OP2", "P"])]
    assert masked["numbering_gaps"] == [{"after": 62, "before": 78}]
    assert masked["mapping"][54] == {"reference_position": 55, "residue_id": "A:78"}
    assert masked["excluded_chains"] == ["B"]
    mismatch = inspect_backbone(
        PDB, "A", read_sequence(ROOT / "twort_seq.txt")[1], "mask_phosphate"
    )
    assert not mismatch["compatible"] and mismatch["mapping"] == []
    assert len(mismatch["unmapped_reference_positions"]) == 282
    mutation = inspect_backbone(PDB, "A", "A" + SHORT[1:], "mask_phosphate")
    assert mutation["mismatches"] == [{"position": 1, "reference": "A", "backbone": "G"}]
    assert not mutation["compatible"]
    assert inspect_backbone(PDB, "B", missing_atoms="mask_phosphate")["sequence"] == "GCUU"
    with pytest.raises(DesignError, match="available chains"):
        inspect_backbone(PDB, "Z")


def test_mmcif_preserves_mapping_and_coordinates(tmp_path):
    from Bio.PDB import MMCIFIO, PDBParser

    io = MMCIFIO()
    io.set_structure(PDBParser(QUIET=True).get_structure("test", str(PDB)))
    cif = tmp_path / "test.cif"
    io.save(str(cif))
    a = inspect_backbone(PDB, "A", SHORT, "mask_phosphate")
    b = inspect_backbone(cif, "A", SHORT, "mask_phosphate")
    assert b["compatible"] and b["residues"] == a["residues"] and b["mapping"] == a["mapping"]


@pytest.mark.parametrize(
    "change, expected",
    [
        ("missing", "Missing required atoms"),
        ("altloc", "Ambiguous alternate"),
        ("zero", "Invalid/zero occupancy"),
        ("modified", "Noncanonical"),
    ],
)
def test_invalid_backbone_rejected(tmp_path, change, expected):
    lines = PDB.read_text().splitlines()
    index = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("ATOM") and line[12:16].strip() == "C1'" and line[21] == "A"
    )
    line = lines[index]
    if change == "missing":
        lines.pop(index)
    elif change == "altloc":
        lines[index] = line[:16] + "A" + line[17:]
    elif change == "zero":
        lines[index] = line[:54] + "  0.00" + line[60:]
    else:
        # Change all atoms in the first residue, without leaving a duplicate residue identity.
        lines = [
            s[:17] + "PSU" + s[20:] if s.startswith("ATOM") and s[21:27] == line[21:27] else s
            for s in lines
        ]
    path = tmp_path / "bad.pdb"
    path.write_text("\n".join(lines) + "\n")
    report = inspect_backbone(path, "A", missing_atoms="mask_phosphate")
    assert not report["compatible"] and any(expected in e for e in report["errors"])


def test_explicit_twort_reconstruction_preserves_all_fixed_positions():
    reference = read_sequence(ROOT / "twort_seq.txt")[1]
    mapping = validate_mapping(ROOT / "examples/twort-reconstruction.json", reference, SHORT)
    assert len(mapping["fixed_reference_positions"]) == 53
    assembled = reconstruct("C" * 229, mapping)
    assert len(assembled) == 282
    assert all(assembled[p - 1] == reference[p - 1] for p in mapping["fixed_reference_positions"])
    assert all(assembled[p - 1] == "C" for p in mapping["reference_positions"])
    original_design_projection = "".join(reference[p - 1] for p in mapping["reference_positions"])
    assert reconstruct(original_design_projection, mapping) == reference
    assert len(mapping["acknowledged_mismatches"]) == 2
    with pytest.raises(DesignError, match="length"):
        reconstruct("A" * 228, mapping)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_positions", [1] * 229),
        ("reference_positions", [True] * 229),
        ("acknowledged_mismatches", []),
        ("reference_sequence", "A"),
        ("design_sequence", "A"),
        ("strategy", ""),
    ],
)
def test_bad_reconstruction_mapping_is_not_inferred(tmp_path, field, value):
    value_dict = json.loads((ROOT / "examples/twort-reconstruction.json").read_text())
    reference = value_dict["reference_sequence"]
    value_dict[field] = value
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value_dict))
    with pytest.raises(DesignError):
        validate_mapping(path, reference, SHORT)


def test_shared_rna_token_decoder_rejects_protein_dna_unknown_and_legacy():
    assert decode_tokens([21, 22, 23, 24]) == "ACGU"
    for token in [0, 20, 25, 26, 29, 30, 31, 32, -1]:
        with pytest.raises(ValueError, match="noncanonical"):
            decode_tokens([token])


class AllWorker(CombinedWorker):
    sequences = {**CombinedWorker.sequences, "na_mpnn": ["CGUACGUA", "CCCCAAAA", "GGGGUUUU"]}

    def request(self, action, **payload):
        result = super().request(action, **payload)
        if action == "generate" and payload["generator"] == "na_mpnn":
            result["result"]["target_consumed"] = None
            result["result"]["backbone_consumed_id"] = payload["backbone"]["id"]
        return result


@pytest.fixture
def all_models(tmp_path, monkeypatch):
    runtime = {
        "ready": True,
        "source_revision": "TEST-GRNADE",
        "assets": {"grnade": {"sha256": "TEST-GRNADE"}},
        "generator": {
            "source_revision": "TEST-STRUCT2SEQ",
            "assets": {"struct2seq": {"sha256": "TEST-STRUCT2SEQ"}},
        },
        "na_mpnn": {"source_revision": "TEST-NA", "assets": {"na_mpnn": {"sha256": "TEST-NA"}}},
    }
    monkeypatch.setattr(
        na_mpnn_runtime, "require_runtime", lambda config, include_struct2seq=False: runtime
    )
    from rna_design import backbone

    monkeypatch.setattr(
        backbone,
        "validate_backbone",
        lambda settings, seq: {
            "id": identity(seq),
            "length": len(seq),
            "chain": "TEST",
            "missing_atom_policy": "error",
            "sequence": seq,
            "compatible": True,
        },
    )
    sequence = tmp_path / "reference.fa"
    sequence.write_text("AAACGUACGUCC")
    short = tmp_path / "design.fa"
    short.write_text("ACGUACGU")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reference_sequence": "AAACGUACGUCC",
                "design_sequence": "ACGUACGU",
                "reference_positions": list(range(3, 11)),
                "strategy": "TEST ONLY positional mapping",
                "acknowledged_mismatches": [],
            }
        )
    )
    fake_backbone = tmp_path / "test-only.pdb"
    fake_backbone.write_text("TEST ONLY mocked backbone")
    config = Experiment(
        input=InputConfig(
            sequence=sequence,
            backbone=BackboneConfig(path=fake_backbone, chain="TEST", mapping="exact_sequence"),
            reconstruction=ReconstructionConfig(design_sequence=short, mapping=mapping),
        ),
        model=AllModelsConfig(),
        budget={"proposals": 3, "attempt_cap": 6, "batch_size": 3},
        selection={"top_k": 30},
    )
    AllWorker.calls = []
    AllWorker.closed = 0
    AllWorker.fail_model = AllWorker.interrupt_model = AllWorker.empty_model = None
    return config, tmp_path / "run"


def test_all_models_reconstruct_before_shared_scoring_and_recover(all_models):
    config, directory = all_models
    pipeline.create_run(config, directory)
    AllWorker.interrupt_model = "na_mpnn"
    with pytest.raises(KeyboardInterrupt):
        pipeline.execute(directory, AllWorker)
    assert [b["model"] for b in committed(directory, "batches")] == ["grnade", "struct2seq"]
    # Resume uses immutable inputs, not newly edited external files.
    config.input.reconstruction.design_sequence.write_text("INVALID")
    result = pipeline.execute(directory, AllWorker)
    assert result["status"] == "completed"
    assert result["counts"]["requested"] == result["counts"]["returned"] == 9
    assert result["counts"]["attempted"] == 12
    assert result["counts"]["cross_model_overlap"] == 1  # One sequence shared by all three.
    assert set(result["counts"]["per_model"]) == {"grnade", "struct2seq", "na_mpnn"}
    calls = [p for a, p in AllWorker.calls if a == "generate"]
    assert [p["generator"] for p in calls] == ["grnade", "struct2seq", "na_mpnn", "na_mpnn"]
    assert all(len(p["sequence"]) == 8 and len(p["target"]) == 8 for p in calls)
    assert calls[-1]["seed"] == calls[-2]["seed"]
    evaluation_calls = [p for a, p in AllWorker.calls if a != "generate"]
    assert all(
        len(p["sequence"]) == 12 and p["sequence"].startswith("AA") and p["sequence"].endswith("CC")
        for p in evaluation_calls
    )
    rows = list(csv.DictReader((directory / "exports/ranked.csv").open()))
    assert all(
        r["length"] == "12" and r["designed_length"] == "8" and r["fixed_context_length"] == "4"
        for r in rows
    )
    assert any(r["generator"] == "grnade;na_mpnn;struct2seq" for r in rows)
    events = [p for b in committed(directory, "batches") for p in b["events"]]
    assert all(
        e["generation_target_id"] is None and e["backbone_id"]
        for e in events
        if e["model"]["name"] == "na_mpnn"
    )
    assert all(e["reconstruction_id"] for e in events)
    before = len(AllWorker.calls)
    pipeline.execute(directory, AllWorker)
    assert len(AllWorker.calls) == before


@pytest.mark.parametrize(
    "artifact",
    [
        "backbone.json",
        "generation-target.json",
        "reconstruction.json",
        "inputs/backbone.original",
        "inputs/reconstruction-map.original",
    ],
)
def test_sealed_mapping_and_backbone_cannot_change(all_models, artifact):
    config, directory = all_models
    pipeline.create_run(config, directory)
    (directory / artifact).write_text("{}")
    with pytest.raises(ExecutionError, match="modified|differs"):
        load_manifest(directory)


def test_na_failure_has_per_model_partial_status_and_resumes(all_models):
    config, directory = all_models
    pipeline.create_run(config, directory)
    AllWorker.fail_model = "na_mpnn"
    result = pipeline.execute(directory, AllWorker)
    assert result["status"] == "partial" and result["model_errors"]["na_mpnn"]
    assert result["counts"]["per_model"]["na_mpnn"]["returned"] == 0
    AllWorker.fail_model = None
    assert pipeline.execute(directory, AllWorker)["status"] == "completed"


def test_na_mpnn_needs_explicit_backbone(tmp_path):
    sequence = tmp_path / "input.fa"
    sequence.write_text("ACGUACGU")
    config = Experiment(input=InputConfig(sequence=sequence), model={"name": "na_mpnn"})
    with pytest.raises(DesignError, match="requires input.backbone"):
        pipeline.validate(config)


def test_wrong_backbone_receipt_is_a_model_failure(all_models):
    config, directory = all_models
    pipeline.create_run(config, directory)

    class WrongBackbone(AllWorker):
        def request(self, action, **payload):
            result = super().request(action, **payload)
            if action == "generate" and payload["generator"] == "na_mpnn":
                result["result"]["backbone_consumed_id"] = "WRONG"
            return result

    result = pipeline.execute(directory, WrongBackbone)
    assert result["status"] == "partial"
    assert "incompatible backbone" in result["model_errors"]["na_mpnn"]
    assert result["counts"]["per_model"]["na_mpnn"]["returned"] == 0


@pytest.mark.integration
def test_real_native_features_and_sampling(tmp_path):
    import os
    import subprocess

    if os.environ.get("RNA_DESIGN_INTEGRATION") != "1":
        pytest.skip("Requires the pinned NA-MPNN runtime")
    output = tmp_path / "native-verification.json"
    subprocess.run(
        [
            str(ROOT / ".runtime/na-mpnn-venv/bin/python"),
            str(ROOT / "scripts/verify_na_mpnn.py"),
            "--out",
            str(output),
        ],
        check=True,
        timeout=120,
    )
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "passed" and all(evidence["feature_equality"].values())


def test_full_comparison_target_does_not_replace_short_generation_target(all_models):
    config, directory = all_models
    target = directory.parent / "full-target.dbn"
    target.write_text("AA((..aa..))")
    config.input.target = target
    config.input.target_source = "provided"
    manifest = pipeline.create_run(config, directory)
    context = manifest["immutable"]
    assert context["target"]["dot_bracket"] == target.read_text()
    assert len(context["generation_target"]["dot_bracket"]) == 8
    result = pipeline.execute(directory, AllWorker)
    assert result["status"] == "completed"
    assert all(len(p["target"]) == 8 for a, p in AllWorker.calls if a == "generate")
