"""Orchestration failure tests. Synthetic worker exists only in this test module."""

import json
from types import SimpleNamespace

import pytest

from rna_design import evaluation, pipeline, struct2seq_runtime
from rna_design.cli import main
from rna_design.config import (
    BudgetConfig,
    Experiment,
    InputConfig,
    SelectionConfig,
    Struct2SeqConfig,
)
from rna_design.errors import ExecutionError
from rna_design.metrics import levenshtein
from rna_design.reporting import collect
from rna_design.storage import committed, load_manifest, read_json, run_lock

IDENTITY = {
    "ready": True,
    "source_revision": "TEST-ONLY",
    "assets": {"grnade": {"sha256": "TEST-ONLY"}},
}
STRUCT2SEQ_IDENTITY = {
    **IDENTITY,
    "generator": {
        "source_revision": "TEST-STRUCT2SEQ",
        "assets": {"struct2seq": {"sha256": "TEST-STRUCT2SEQ"}},
    },
}


class TestOnlyWorker:
    __test__ = False
    calls = []
    generated = ["CGUACGUA", "CGUACGUA", "ACGUACGU", "AAAAAAAA", "INVALID"]
    interrupt_on = None
    fail_reactivity = None

    def __init__(self, *args):
        self.process = SimpleNamespace(poll=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, action, **payload):
        type(self).calls.append((action, payload))
        if action == self.interrupt_on:
            type(self).interrupt_on = None
            raise KeyboardInterrupt
        if action == "reactivity" and payload["sequence"] == self.fail_reactivity:
            raise ExecutionError("TEST-ONLY predictor failure")
        if action == "generate":
            result = {
                "target_consumed": payload["target"],
                "target_pairs_consumed": payload.get("target_pairs"),
                "internal_search_effort": {"sampled_sequences": payload["count"]},
                "proposals": [
                    {"sequence": sequence} for sequence in self.generated[: payload["count"]]
                ],
            }
        elif action == "reactivity":
            result = {"channels": [[0.2, 0.3] for _ in payload["sequence"]]}
        else:
            result = {"dot_bracket": "." * len(payload["sequence"])}
        return {"ok": True, "result": result, "seconds": 0.01, "peak_rss_bytes": 100}


@pytest.fixture(params=["grnade", "struct2seq"])
def setup(tmp_path, monkeypatch, request):
    monkeypatch.setattr(pipeline, "require_runtime", lambda config: IDENTITY)
    monkeypatch.setattr(struct2seq_runtime, "require_runtime", lambda config: STRUCT2SEQ_IDENTITY)
    TestOnlyWorker.calls = []
    TestOnlyWorker.generated = ["CGUACGUA", "CGUACGUA", "ACGUACGU", "AAAAAAAA", "INVALID"]
    TestOnlyWorker.interrupt_on = None
    TestOnlyWorker.fail_reactivity = None
    sequence = tmp_path / "input.fasta"
    sequence.write_text("ACGUACGU")
    config = Experiment(
        input=InputConfig(sequence=sequence), budget=BudgetConfig(proposals=5, attempt_cap=10)
    )
    if request.param == "struct2seq":
        config.model = Struct2SeqConfig()
    return config, tmp_path / "run"


def test_end_to_end_duplicates_invalid_reference_and_exports(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "completed"
    counts = result["counts"]
    assert (
        counts["returned"],
        counts["duplicate"],
        counts["invalid"],
        counts["reference_copies"],
    ) == (5, 1, 1, 1)
    assert counts["selected"] == 2
    assert len(committed(directory, "candidates")) == 3
    csv = (directory / "exports/selected.csv").read_text()
    assert "sequence_levenshtein_to_input" in csv and "mfe_structure_levenshtein_to_input" in csv
    details = [
        json.loads(line)
        for line in (directory / "exports/candidates.jsonl").read_text().splitlines()
    ]
    assert len(details) == 3
    events = (directory / "exports/generation.jsonl").read_text().splitlines()
    assert len(events) == 5
    proposal = json.loads(events[0])
    assert proposal["model"] == config.model.model_dump()
    generated_request = next(
        payload for action, payload in TestOnlyWorker.calls if action == "generate"
    )
    if config.model.name == "struct2seq":
        assert proposal["checkpoint_sha256"] == "TEST-STRUCT2SEQ"
        assert proposal["source_revision"] == "TEST-STRUCT2SEQ"
        assert proposal["internal_search_effort_scope"] == "batch"
        assert generated_request["sampling_probability"] == 1.0
        assert "temperature" not in generated_request
        assert "trained/guided using RNet" in (directory / "exports/report.md").read_text()
    else:
        assert proposal["checkpoint_sha256"] == "TEST-ONLY"
        assert generated_request["temperature"] == 0.8
    calls_before = len(TestOnlyWorker.calls)
    assert pipeline.execute(directory, TestOnlyWorker)["status"] == "completed"
    assert len(TestOnlyWorker.calls) == calls_before


def test_interrupt_resume_reuses_committed_batches_and_fold(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    TestOnlyWorker.interrupt_on = "structure"
    with pytest.raises(KeyboardInterrupt):
        pipeline.execute(directory, TestOnlyWorker)
    assert load_manifest(directory)["status"] == "interrupted"
    target = (directory / "target.json").read_bytes()
    pipeline.execute(directory, TestOnlyWorker)
    assert sum(action == "generate" for action, _ in TestOnlyWorker.calls) == 1
    reference = config.input.sequence.read_text()
    assert (
        sum(
            action == "reactivity" and p["sequence"] == reference
            for action, p in TestOnlyWorker.calls
        )
        == 1
    )
    assert (directory / "target.json").read_bytes() == target
    assert len(committed(directory, "batches")) == 1


def test_generation_interrupt_same_seed(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    TestOnlyWorker.interrupt_on = "generate"
    with pytest.raises(KeyboardInterrupt):
        pipeline.execute(directory, TestOnlyWorker)
    pipeline.execute(directory, TestOnlyWorker)
    calls = [p for action, p in TestOnlyWorker.calls if action == "generate"]
    assert len(calls) == 2
    assert calls[0]["seed"] == calls[1]["seed"]
    assert len(committed(directory, "attempts")) == 1
    assert load_manifest(directory)["counts"]["attempted"] == 10
    assert load_manifest(directory)["counts"]["generation_invocations"] == 2


def test_interrupted_replay_cannot_exceed_attempt_cap(setup):
    config, directory = setup
    config.budget.attempt_cap = 5
    pipeline.create_run(config, directory)
    TestOnlyWorker.interrupt_on = "generate"
    with pytest.raises(KeyboardInterrupt):
        pipeline.execute(directory, TestOnlyWorker)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "partial"
    assert result["counts"]["attempted"] == 5
    assert sum(action == "generate" for action, _ in TestOnlyWorker.calls) == 1


def test_wall_time_budget_stops_without_generation(setup):
    config, directory = setup
    config.budget.wall_time_seconds = 1e-9
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "partial"
    assert result["counts"]["returned"] == 0
    assert "wall-time budget" in result["error"]


def test_override_does_not_change_distance_reference(setup):
    config, directory = setup
    target = directory.parent / "target.dbn"
    target.write_text("(......)")
    config.input = InputConfig(sequence=config.input.sequence, target=target)
    pipeline.create_run(config, directory)
    pipeline.execute(directory, TestOnlyWorker)
    manifest, _, evaluations = collect(directory)
    context = manifest["immutable"]
    assert context["target"]["source"] == "provided"
    assert context["target"]["dot_bracket"] != context["reference_fold"]["dot_bracket"]
    for value in evaluations.values():
        metric = value["metrics"]["mfe_structure_levenshtein_to_input"]
        assert metric["provenance"]["comparator_string"] == context["reference_fold"]["dot_bracket"]
        assert metric["value"] == levenshtein(
            value["fold"]["output"]["dot_bracket"], context["reference_fold"]["dot_bracket"]
        )
    assert all(p["target"] == "(......)" for a, p in TestOnlyWorker.calls if a == "generate")


def test_fold_failure_keeps_sequence_distance_and_scores(setup, monkeypatch):
    config, directory = setup
    pipeline.create_run(config, directory)

    def fail(*args):
        raise ValueError("TEST-ONLY fold failure")

    monkeypatch.setattr(evaluation, "fold", fail)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "completed"
    for value in collect(directory)[2].values():
        metrics = value["metrics"]
        assert metrics["sequence_levenshtein_to_input"]["status"] == "ok"
        assert metrics["mfe_structure_levenshtein_to_input"]["value"] is None
        assert metrics["mfe_structure_levenshtein_to_input"]["status"] == "failed"
        assert metrics["predicted_openknot_score"]["status"] == "ok"


def test_one_predictor_failure_does_not_erase_rest(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    TestOnlyWorker.fail_reactivity = "AAAAAAAA"
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "partial"
    assert result["counts"]["selected"] == 1
    _, candidates, evaluations = collect(directory)
    failed = next(c for c in candidates if c["sequence"] == "AAAAAAAA")
    metrics = evaluations[failed["candidate_id"]]["metrics"]
    assert metrics["sequence_levenshtein_to_input"]["value"] > 0
    assert metrics["predicted_openknot_score"]["value"] is None
    assert metrics["pair_f1"]["status"] == "ok"


def test_composition_filter_precedes_neural_scoring_and_retains_distances(setup):
    config, directory = setup
    config.selection = SelectionConfig(max_c_percent=0)
    TestOnlyWorker.generated = ["CCCCCCCC"] * 5
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "completed_no_candidates"
    assert all(p["sequence"] != "CCCCCCCC" for a, p in TestOnlyWorker.calls if a != "generate")
    _, candidates, evaluations = collect(directory)
    cid = next(c["candidate_id"] for c in candidates if c["sequence"] == "CCCCCCCC")
    assert evaluations[cid]["metrics"]["mfe_structure_levenshtein_to_input"]["status"] == "ok"
    assert evaluations[cid]["metrics"]["predicted_openknot_score"]["status"] == "not_applicable"


def test_attempt_cap_shortfall(setup):
    config, directory = setup
    config.budget.attempt_cap = 2
    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, TestOnlyWorker)
    assert result["status"] == "partial"
    assert result["counts"]["attempted"] == 2
    assert result["counts"]["returned"] == 2


@pytest.mark.parametrize("violation", ["target", "count", "pairs"])
def test_generator_contract_violation_is_not_committed(setup, violation):
    config, directory = setup
    if violation == "pairs" and config.model.name == "grnade":
        pytest.skip("gRNAde consumes the target string directly")

    class BadWorker(TestOnlyWorker):
        def request(self, action, **payload):
            result = super().request(action, **payload)
            if action == "generate":
                if violation == "target":
                    result["result"]["target_consumed"] = "wrong"
                elif violation == "pairs":
                    result["result"]["target_pairs_consumed"] = [[1, 8]]
                else:
                    result["result"]["proposals"] *= 2
            return result

    pipeline.create_run(config, directory)
    result = pipeline.execute(directory, BadWorker)
    assert result["status"] == "partial"
    assert result["counts"]["returned"] == 0
    assert result["counts"]["attempted"] == 5
    assert not committed(directory, "batches")


def test_immutable_manifest_and_snapshots(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    (directory / "inputs/sequence.original").write_text("AAAA")
    with pytest.raises(ExecutionError, match="snapshot"):
        pipeline.execute(directory, TestOnlyWorker)


def test_runtime_identity_change_blocks_resume(setup, monkeypatch):
    config, directory = setup
    pipeline.create_run(config, directory)
    monkeypatch.setattr(
        pipeline, "require_runtime", lambda config: {**IDENTITY, "source_revision": "CHANGED"}
    )
    monkeypatch.setattr(
        struct2seq_runtime,
        "require_runtime",
        lambda config: {**STRUCT2SEQ_IDENTITY, "source_revision": "CHANGED"},
    )
    with pytest.raises(ExecutionError, match="Runtime identity"):
        pipeline.execute(directory, TestOnlyWorker)


def test_select_and_export_do_not_infer(setup, capsys):
    config, directory = setup
    pipeline.create_run(config, directory)
    pipeline.execute(directory, TestOnlyWorker)
    calls = len(TestOnlyWorker.calls)
    selection_before = (directory / "selection.json").read_bytes()
    out = directory.parent / "review"
    assert main(["select", str(directory), "--top-k", "1", "--out", str(out), "--json"]) == 0
    assert len(read_json(out / "selection.json")["selected_ids"]) == 1
    assert (directory / "selection.json").read_bytes() == selection_before
    assert len(TestOnlyWorker.calls) == calls


def test_lock_prevents_second_writer(setup):
    config, directory = setup
    pipeline.create_run(config, directory)
    with run_lock(directory):
        with pytest.raises(ExecutionError, match="Another process"):
            with run_lock(directory):
                pass
