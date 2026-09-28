"""Combined generation: cross-model identity, accounting, partial failures and recovery."""

import csv
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rna_design import pipeline, struct2seq_runtime
from rna_design.cli import main
from rna_design.config import (
    BothModelsConfig,
    BudgetConfig,
    Experiment,
    InputConfig,
    SelectionConfig,
)
from rna_design.errors import DesignError, ExecutionError
from rna_design.reporting import collect
from rna_design.storage import committed, load_manifest


class CombinedWorker:
    calls = []
    fail_model = None
    interrupt_model = None
    empty_model = None
    closed = 0
    sequences = {
        "grnade": ["CGUACGUA", "GGGGAAAA", "CGUACGUA"],
        "struct2seq": ["CGUACGUA", "UUUUAAAA", "ACGUACGU"],
    }

    def __init__(self, *args):
        self.process = SimpleNamespace(poll=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        type(self).closed += 1

    def request(self, action, **payload):
        cls = type(self)
        cls.calls.append((action, payload))
        if action == "generate":
            name = payload["generator"]
            if name == cls.interrupt_model:
                cls.interrupt_model = None
                raise KeyboardInterrupt
            if name == cls.fail_model:
                raise ExecutionError(f"TEST-ONLY {name} failed")
            seqs = [] if name == cls.empty_model else cls.sequences[name][: payload["count"]]
            result = {
                "target_consumed": payload["target"],
                "target_pairs_consumed": payload.get("target_pairs"),
                "proposals": [{"sequence": sequence} for sequence in seqs],
            }
        elif action == "reactivity":
            value = 0.1 if payload["sequence"].startswith("G") else 0.5
            result = {"channels": [[value, 0.3] for _ in payload["sequence"]]}
        else:
            result = {"dot_bracket": "." * len(payload["sequence"])}
        return {"ok": True, "result": result, "seconds": 0.01, "peak_rss_bytes": 100}


@pytest.fixture
def combined(tmp_path, monkeypatch):
    identity = {
        "ready": True,
        "source_revision": "TEST-GRNADE",
        "assets": {"grnade": {"sha256": "TEST-GRNADE"}},
        "generator": {
            "source_revision": "TEST-STRUCT2SEQ",
            "assets": {"struct2seq": {"sha256": "TEST-STRUCT2SEQ"}},
        },
    }
    monkeypatch.setattr(struct2seq_runtime, "require_runtime", lambda config: identity)
    CombinedWorker.calls = []
    CombinedWorker.closed = 0
    CombinedWorker.fail_model = CombinedWorker.interrupt_model = CombinedWorker.empty_model = None
    sequence = tmp_path / "input.fasta"
    sequence.write_text("ACGUACGU")
    target = tmp_path / "target.dbn"
    target.write_text("(......)")
    config = Experiment(
        input=InputConfig(sequence=sequence, target=target),
        model=BothModelsConfig(),
        budget=BudgetConfig(proposals=3, attempt_cap=6, batch_size=3),
        selection=SelectionConfig(top_k=2),
    )
    return config, tmp_path / "run"


def rows(directory, name="ranked"):
    with (directory / f"exports/{name}.csv").open() as stream:
        return list(csv.DictReader(stream))


def test_combined_unique_pool_one_evaluation_and_one_ranking(combined):
    config, directory = combined
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "completed"
    counts = result["counts"]
    assert counts["requested"] == counts["returned"] == counts["attempted"] == 6
    assert counts["unique_generated"] == 4 and counts["duplicate"] == 2
    assert counts["reference_copies"] == 1 and counts["cross_model_overlap"] == 1
    assert counts["eligible"] == 3 and counts["selected"] == 2
    assert counts["per_model"]["grnade"]["unique_generated"] == 2
    assert counts["per_model"]["struct2seq"]["unique_generated"] == 3
    for action in ["reactivity", "structure"]:
        sequences = [p["sequence"] for a, p in CombinedWorker.calls if a == action]
        assert len(sequences) == len(set(sequences)) == 4
    generation = [p for a, p in CombinedWorker.calls if a == "generate"]
    assert [p["generator"] for p in generation] == ["grnade", "struct2seq"]
    assert generation[0]["seed"] == generation[1]["seed"]
    assert generation[0]["target"] == generation[1]["target"] == "(......)"
    assert "temperature" in generation[0] and "sampling_probability" not in generation[0]
    assert "sampling_probability" in generation[1] and "temperature" not in generation[1]
    ranked = rows(directory)
    assert len(ranked) == 3 and len(rows(directory, "selected")) == 2
    assert [int(row["rank"]) for row in ranked] == [1, 2, 3]
    assert all(row["run_status"] == "completed" for row in ranked)
    assert ranked == sorted(
        ranked,
        key=lambda row: (
            -float(row["predicted_openknot_score"]),
            -float(row["pair_f1"]),
            row["candidate_id"],
        ),
    )
    shared = next(row for row in ranked if row["sequence"] == "CGUACGUA")
    assert shared["generator"] == "grnade;struct2seq" and shared["generation_count"] == "3"
    assert rows(directory, "reference")[0]["generator"] == ""
    detailed = [
        json.loads(line)
        for line in (directory / "exports/candidates.jsonl").read_text().splitlines()
    ]
    assert next(row for row in detailed if row["sequence"] == "CGUACGUA")["generators"] == [
        "grnade",
        "struct2seq",
    ]
    events = [p for b in committed(directory, "batches") for p in b["events"]]
    assert len(events) == 6
    for proposal in events:
        assert proposal["checkpoint_sha256"] == f"TEST-{proposal['model']['name'].upper()}"
    calls = len(CombinedWorker.calls)
    pipeline.execute(directory, CombinedWorker)
    assert len(CombinedWorker.calls) == calls


@pytest.mark.parametrize("name", ["grnade", "struct2seq"])
def test_one_generator_failure_scores_other_and_resume_recovers(combined, name):
    config, directory = combined
    pipeline.create_run(config, directory)
    CombinedWorker.fail_model = name
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "partial"
    assert name in result["model_errors"]
    assert result["counts"]["per_model"][name]["returned"] == 0
    assert result["counts"]["returned"] == 3
    assert rows(directory) and all(row["run_status"] == "partial" for row in rows(directory))
    before = {str(p): p.read_bytes() for p in directory.glob("evaluations/*/stages/*/*.json")}
    calls = len(CombinedWorker.calls)
    CombinedWorker.fail_model = None
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "completed" and result["model_errors"] == {}
    assert result["counts"]["attempted"] == 9
    assert result["counts"]["per_model"][name]["attempted"] == 6
    assert {
        str(p): p.read_bytes()
        for p in directory.glob("evaluations/*/stages/*/*.json")
        if str(p) in before
    } == before
    new_generations = [p for a, p in CombinedWorker.calls[calls:] if a == "generate"]
    assert len(new_generations) == 1 and new_generations[0]["generator"] == name
    ref = [p for a, p in CombinedWorker.calls if a == "reactivity" and p["sequence"] == "ACGUACGU"]
    assert len(ref) == 1


def test_stop_policy_does_not_start_second_generator(combined):
    config, directory = combined
    config.model.failure_policy = "stop"
    CombinedWorker.fail_model = "grnade"
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "partial"
    assert [p["generator"] for a, p in CombinedWorker.calls if a == "generate"] == ["grnade"]


def test_interrupt_second_model_preserves_first_and_replays_only_second(combined):
    config, directory = combined
    pipeline.create_run(config, directory)
    CombinedWorker.interrupt_model = "struct2seq"
    with pytest.raises(KeyboardInterrupt):
        pipeline.execute(directory, CombinedWorker)
    assert load_manifest(directory)["status"] == "interrupted"
    first_batch = (directory / "batches/000000.json").read_bytes()
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "completed"
    calls = [p for a, p in CombinedWorker.calls if a == "generate"]
    assert [p["generator"] for p in calls] == ["grnade", "struct2seq", "struct2seq"]
    assert calls[1]["seed"] == calls[2]["seed"]
    assert (directory / "batches/000000.json").read_bytes() == first_batch
    assert result["counts"]["per_model"]["struct2seq"]["attempted"] == 6


def test_attempt_caps_and_shortfalls_apply_separately(combined):
    config, directory = combined
    CombinedWorker.empty_model = "grnade"
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "partial"
    counts = result["counts"]["per_model"]
    assert counts["grnade"]["attempted"] == 6 and counts["grnade"]["returned"] == 0
    assert counts["struct2seq"]["attempted"] == counts["struct2seq"]["returned"] == 3
    assert rows(directory)


def test_model_specific_controls_and_batch_seeds(combined):
    config, directory = combined
    config.budget.proposals = 5
    config.budget.attempt_cap = 10
    config.model.grnade.sampling_temperature = 0.6
    config.model.struct2seq.sampling_probability = 0.5
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, CombinedWorker)
    calls = [p for a, p in CombinedWorker.calls if a == "generate"]
    assert result["status"] == "completed"
    assert [p["count"] for p in calls] == [3, 2, 3, 2]
    assert calls[0]["seed"] == calls[2]["seed"]
    assert calls[1]["seed"] == calls[3]["seed"]
    assert calls[0]["seed"] != calls[1]["seed"]
    assert calls[0]["temperature"] == calls[1]["temperature"] == 0.6
    assert calls[2]["sampling_probability"] == calls[3]["sampling_probability"] == 0.5
    assert result["counts"]["requested"] == result["counts"]["returned"] == 10


def test_combined_global_filters_and_empty_selection(combined):
    config, directory = combined
    config.selection = SelectionConfig(top_k=0, max_c_percent=0)
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, CombinedWorker)
    assert result["status"] == "completed_no_candidates"
    assert result["counts"]["eligible"] == 2 and result["counts"]["selected"] == 0
    assert len(rows(directory)) == 2 and rows(directory, "selected") == []
    assert not any(
        p["sequence"] == "CGUACGUA" for a, p in CombinedWorker.calls if a == "reactivity"
    )


def test_combined_cli_config_validation_and_export_without_inference(combined, capsys):
    config, directory = combined
    path = directory.parent / "both.yaml"
    assert (
        main(
            [
                "init",
                "--sequence",
                str(config.input.sequence),
                "--model",
                "both",
                "--smoke",
                "--output",
                str(path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["run", "--config", str(path), "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["requested_proposals"] == 20 and plan["attempt_cap"] == 40
    assert plan["proposals_per_model"] == {"grnade": 10, "struct2seq": 10}
    pipeline.create_run(config, directory)
    pipeline.execute(directory, CombinedWorker)
    calls = len(CombinedWorker.calls)
    out = directory.parent / "exported"
    assert main(["export", str(directory), "--formats", "csv", "--out", str(out)]) == 0
    assert (out / "ranked.csv").read_bytes() == (directory / "exports/ranked.csv").read_bytes()
    assert (
        main(
            ["select", str(directory), "--top-k", "1", "--out", str(directory.parent / "selected")]
        )
        == 0
    )
    assert len(CombinedWorker.calls) == calls
    assert len(collect(directory)[1]) == 4


def test_combined_rejects_controls_and_targets_not_supported_by_both(combined):
    config, _ = combined
    with pytest.raises(ValidationError):
        BothModelsConfig(sampling_temperature=1)
    with pytest.raises(ValidationError):
        BothModelsConfig(failure_policy="ignore")
    config.input.target.write_text("A......a")
    with pytest.raises(DesignError, match="gRNAde.*alphabetic"):
        pipeline.validate(config)
