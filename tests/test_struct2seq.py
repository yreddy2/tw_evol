import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rna_design import struct2seq_runtime
from rna_design.cli import main
from rna_design.config import Experiment, InputConfig, ModelConfig, Struct2SeqConfig
from rna_design.errors import CapabilityError, DesignError
from rna_design.pipeline import validate


@pytest.fixture
def config(tmp_path):
    sequence = tmp_path / "input.fasta"
    sequence.write_text("ACGUACGU")
    return Experiment(input=InputConfig(sequence=sequence), model=Struct2SeqConfig())


@pytest.mark.parametrize(
    "extra",
    [
        {"sampling_temperature": 0.8},
        {"sampling_probability": -0.1},
        {"sampling_probability": 1.1},
        {"sampling_probability": float("nan")},
        {"reference_bias": 1},
        {"shape_profile": "shape.txt"},
        {"checkpoint_variant": "unknown"},
        {"sampler": "rescue"},
    ],
)
def test_reject_unsupported_or_misleading_controls(config, extra):
    value = config.model_dump()
    value["model"].update(extra)
    with pytest.raises(ValidationError):
        Experiment.model_validate(value)


def test_struct2seq_config_round_trip_and_model_switch(config, tmp_path, capsys):
    path = tmp_path / "struct2seq.yaml"
    assert (
        main(
            [
                "init",
                "--sequence",
                str(config.input.sequence),
                "--model",
                "struct2seq",
                "--output",
                str(path),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["run", "--config", str(path), "--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["config"]["model"] == config.model.model_dump()
    assert data["inference_performed"] is False
    assert (
        main(
            [
                "validate",
                "--config",
                str(path),
                "--model",
                "grnade",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["config"]["model"] == ModelConfig().model_dump()


def test_model_specific_alphabetic_targets(config, tmp_path):
    target = tmp_path / "target.dbn"
    target.write_text("A([.a.)]")
    config.input = InputConfig(sequence=config.input.sequence, target=target)
    assert validate(config)["provided_target"] == "A([.a.)]"
    config.model = ModelConfig()
    with pytest.raises(DesignError, match="alphabetic"):
        validate(config)


@pytest.mark.parametrize("length", [7, 501])
def test_struct2seq_bounded_length_is_not_cropped(config, length):
    config.input.sequence.write_text("A" * length)
    with pytest.raises(CapabilityError, match="struct2seq"):
        validate(config)


def test_registry_lists_all_without_runtime(tmp_path, capsys):
    assert main(["models", "list", "--runtime-root", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {m["name"] for m in data["generators"]} == {"grnade", "struct2seq", "na_mpnn"}
    assert all(m["tested_lengths"] == [] for m in data["generators"])
    assert (
        main(
            [
                "doctor",
                "--model",
                "struct2seq",
                "--runtime-root",
                str(tmp_path),
                "--json",
            ]
        )
        == 3
    )
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["model"] == "struct2seq" and not doctor["ready"]
    assert any("Struct2SeQ checkpoint" in error for error in doctor["errors"])


def test_router_releases_generator_before_evaluation(monkeypatch):
    live = []
    calls = []

    class Generator:
        def __init__(self, *args):
            assert not live
            live.append(self)
            self.process = SimpleNamespace(poll=lambda: None)

        def close(self):
            live.remove(self)

        def request(self, action, **payload):
            calls.append((type(self), action))
            return action

    class Evaluator(Generator):
        pass

    monkeypatch.setattr(struct2seq_runtime, "Struct2SeqWorker", Generator)
    monkeypatch.setattr(struct2seq_runtime, "Worker", Evaluator)
    with struct2seq_runtime.PipelineWorker(None) as worker:
        assert worker.process.poll() is None  # A resumable fold may run before any child.
        worker.request("generate")
        worker.request("generate")
        worker.request("reactivity")
        worker.request("structure")
    assert not live
    assert calls == [(Generator, "generate")] * 2 + [
        (Evaluator, "reactivity"),
        (Evaluator, "structure"),
    ]


def test_router_startup_failure_is_retryable(monkeypatch):
    class Broken:
        def __init__(self, *args):
            raise OSError("TEST-ONLY failed process start")

    monkeypatch.setattr(struct2seq_runtime, "Worker", Broken)
    with struct2seq_runtime.PipelineWorker(None) as worker:
        with pytest.raises(OSError):
            worker.request("reactivity")
        assert worker.process.poll() is not None


def test_readiness_rejects_corrupt_weight_and_dirty_source(tmp_path, monkeypatch):
    entry = struct2seq_runtime.LOCK["checkpoint"]
    path = tmp_path / "checkpoints" / entry["file"]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt")
    monkeypatch.setattr(
        struct2seq_runtime.subprocess,
        "check_output",
        lambda cmd, **kwargs: (
            struct2seq_runtime.LOCK["source"]["revision"] if "rev-parse" in cmd else "dirty"
        ),
    )
    result = struct2seq_runtime.readiness(tmp_path)
    assert not result["ready"]
    assert any("modified" in error for error in result["errors"])
    assert any("corrupt" in error for error in result["errors"])


def test_light_struct2seq_commands_do_not_import_torch(config):
    import subprocess
    import sys

    code = f"""
import sys
from rna_design.cli import main
assert main(['validate', '--sequence', {str(config.input.sequence)!r}, '--model', 'struct2seq']) == 0
assert not any(m in sys.modules for m in ['torch', 'numpy', 'RNA', 'polars'])
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
